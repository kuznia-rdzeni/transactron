from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Optional

from dataclasses_json import dataclass_json

from transactron.utils.transactron_helpers import SrcLoc

from .emit import EmittedEvent


__all__ = [
    "SignalHandle",
    "EventFieldSchema",
    "EventSiteSchema",
    "EvLogSchema",
    "EventSiteLocation",
    "GeneratedEvLog",
    "schema_from_records",
]


type SignalHandle = list[str]
"""The location of a signal in generated Verilog code: a list of Verilog
identifiers denoting a path of module names with the signal name at the end."""


@dataclass_json
@dataclass
class EventFieldSchema:
    """Schema of a single dynamic event field.

    Attributes
    ----------
    name: str
        Name of the field.
    width: int
        Bit width of the backing hardware signal.
    signed: bool
        Whether the backing hardware signal is signed.
    """

    name: str
    width: int
    signed: bool = False


@dataclass_json
@dataclass
class EventSiteSchema:
    """Schema of a single event emission site.

    Attributes
    ----------
    source_name: str
        Name of the `EventSource` which registered this site.
    event_name: str
        Registered name of the emitted event type.
    location: SrcLoc
        Source location of the emission site.
    fields: list[EventFieldSchema]
        Dynamic fields, in declaration order. Raw event records store field
        values in this order.
    statics: dict[str, Any]
        Values of the static fields.
    """

    source_name: str
    event_name: str
    location: SrcLoc
    fields: list[EventFieldSchema]
    statics: dict[str, Any]


@dataclass_json
@dataclass
class EvLogSchema:
    """The decoding contract between an elaborated design and event log consumers.

    A schema lists all event emission sites of a single elaboration. Raw event
    records reference sites by their index in `sites`. The schema is stored
    together with captured event logs, so that they can be decoded offline.

    Attributes
    ----------
    sites: list[EventSiteSchema]
        All event emission sites, in registration order.
    metadata: dict[str, Any]
        Free-form, JSON-serializable metadata describing the elaborated design
        (e.g. the core configuration), for use by downstream consumers.
    """

    sites: list[EventSiteSchema]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass_json
@dataclass
class EventSiteLocation:
    """Locations of one emission site's signals in generated Verilog code.

    Attributes
    ----------
    trigger: SignalHandle
        Location of the trigger signal.
    fields: list[SignalHandle]
        Locations of the dynamic field signals, in schema field order.
    """

    trigger: SignalHandle
    fields: list[SignalHandle]


@dataclass_json
@dataclass
class GeneratedEvLog:
    """Event log information for a generated (Verilog) design.

    Attributes
    ----------
    schema: EvLogSchema
        The event log schema.
    site_locations: list[EventSiteLocation]
        Signal locations for each site, indexed like `schema.sites`.
    triggers_location: Optional[SignalHandle]
        Location of a packed vector of all site triggers (bit `i` is the
        trigger of site `i`). Allows a sampler to check all sites with a
        single signal read per cycle. `None` if there are no sites.
    """

    schema: EvLogSchema
    site_locations: list[EventSiteLocation]
    triggers_location: Optional[SignalHandle] = None


def schema_from_records(records: Iterable[EmittedEvent], metadata: Optional[dict[str, Any]] = None) -> EvLogSchema:
    """Builds an event log schema from the emission sites registered during
    elaboration (see `get_emitted_events`)."""
    sites: list[EventSiteSchema] = []
    for rec in records:
        fields_schema: list[EventFieldSchema] = []
        for name, value in rec.fields.items():
            shape = value.shape()
            fields_schema.append(EventFieldSchema(name=name, width=shape.width, signed=bool(shape.signed)))
        sites.append(
            EventSiteSchema(
                source_name=rec.source_name,
                event_name=rec.event_type.event_name,
                location=rec.location,
                fields=fields_schema,
                statics=dict(rec.statics),
            )
        )
    return EvLogSchema(sites=sites, metadata=dict(metadata or {}))
