import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Optional, Protocol, TextIO, runtime_checkable

from .event import Event, get_event_class
from .schema import EvLogSchema, EventSiteSchema


__all__ = [
    "RawEvent",
    "DecodedEvent",
    "RawEventSink",
    "EventDecoder",
    "EventLog",
    "EventLogWriter",
    "EventLogReader",
]


type RawEvent = tuple[int, int, list[int]]
"""A raw event record: (cycle, site index, dynamic field values in schema order)."""


@dataclass
class DecodedEvent:
    """A single decoded event from an event log.

    Attributes
    ----------
    cycle: int
        The clock cycle in which the event was recorded.
    site: EventSiteSchema
        Schema of the emission site which produced the event.
    event: Event
        The typed event instance, with dynamic and static fields filled in.
    """

    cycle: int
    site: EventSiteSchema
    event: Event

    @property
    def source_name(self) -> str:
        return self.site.source_name


@runtime_checkable
class RawEventSink(Protocol):
    """Protocol for consumers of raw event records produced by samplers."""

    def emit_raw(self, cycle: int, site: int, values: Sequence[int]) -> None: ...


class EventDecoder:
    """Decodes raw event records into typed `Event` instances using a schema.

    The modules defining the used event types must be imported beforehand,
    as event types are looked up in the global registry by name.
    """

    def __init__(self, schema: EvLogSchema):
        self.schema = schema
        self._site_classes = [get_event_class(site.event_name) for site in schema.sites]

    def decode(self, cycle: int, site_idx: int, values: Sequence[int]) -> DecodedEvent:
        site = self.schema.sites[site_idx]
        if len(values) != len(site.fields):
            raise ValueError(
                f"Site {site_idx} ('{site.event_name}') expects {len(site.fields)} field values, got {len(values)}"
            )
        dynamics = {field.name: value for field, value in zip(site.fields, values)}
        event = self._site_classes[site_idx].from_raw(dynamics, site.statics)
        return DecodedEvent(cycle=cycle, site=site, event=event)


def _write_header(fp: TextIO, schema: EvLogSchema):
    fp.write(json.dumps(schema.to_dict()) + "\n")  # type: ignore


def _read_header(fp: TextIO) -> EvLogSchema:
    return EvLogSchema.from_dict(json.loads(fp.readline()))  # type: ignore


class EventLog:
    """An in-memory event log: raw event records paired with their schema.

    Events are stored raw (as integers) and decoded on demand. The log is
    serialized in the JSON-lines format: a header line containing the schema,
    followed by one line per event.

    Attributes
    ----------
    schema: EvLogSchema
        Schema used to decode the raw records.
    raw: list[RawEvent]
        The raw event records, in capture order.
    """

    def __init__(self, schema: EvLogSchema):
        self.schema = schema
        self.raw: list[RawEvent] = []

    def emit_raw(self, cycle: int, site: int, values: Sequence[int]) -> None:
        self.raw.append((cycle, site, list(values)))

    def decoded(self) -> list[DecodedEvent]:
        """Decodes all events, in capture order."""
        decoder = EventDecoder(self.schema)
        return [decoder.decode(cycle, site, values) for cycle, site, values in self.raw]

    def save(self, filename: str):
        """Saves the event log to a JSON-lines file."""
        with open(filename, "w") as fp:
            _write_header(fp, self.schema)
            for cycle, site, values in self.raw:
                fp.write(json.dumps([cycle, site, values]) + "\n")

    @staticmethod
    def load(filename: str) -> "EventLog":
        """Loads an event log from a JSON-lines file."""
        with open(filename, "r") as fp:
            log = EventLog(_read_header(fp))
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                cycle, site, values = json.loads(line)
                log.raw.append((cycle, site, values))
        return log


class EventLogWriter:
    """Streams raw event records directly to a JSON-lines file.

    Unlike `EventLog.save`, records are not kept in memory, so arbitrarily
    long simulations can be captured. Can be used as a context manager.
    """

    def __init__(self, filename: str, schema: EvLogSchema):
        self.schema = schema
        self._fp: Optional[TextIO] = open(filename, "w")
        _write_header(self._fp, schema)

    def emit_raw(self, cycle: int, site: int, values: Sequence[int]) -> None:
        assert self._fp is not None, "Event log writer is closed"
        self._fp.write(json.dumps([cycle, site, list(values)]) + "\n")

    def close(self):
        if self._fp is not None:
            self._fp.close()
            self._fp = None

    def __enter__(self) -> "EventLogWriter":
        return self

    def __exit__(self, exc_type, exc_value, exc_tb):
        self.close()


class EventLogReader:
    """Streaming reader of JSON-lines event log files.

    Decodes events lazily while iterating, so arbitrarily long event logs can
    be processed with constant memory usage.

    Attributes
    ----------
    schema: EvLogSchema
        The schema read from the event log header.
    """

    def __init__(self, filename: str):
        self._filename = filename
        with open(filename, "r") as fp:
            self.schema = _read_header(fp)

    def __iter__(self) -> Iterator[DecodedEvent]:
        decoder = EventDecoder(self.schema)
        with open(self._filename, "r") as fp:
            fp.readline()  # skip the header
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                cycle, site, values = json.loads(line)
                yield decoder.decode(cycle, site, values)
