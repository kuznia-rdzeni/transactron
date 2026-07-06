from dataclasses import dataclass
from typing import Any

from amaranth import *
from amaranth.hdl import ValueCastable
from amaranth_types import ModuleLike, ValueLike

from transactron.utils.transactron_helpers import SrcLoc, get_src_loc, local_src_loc
from transactron.utils.dependencies import DependencyContext, ListKey, SimpleKey

from .event import Event, static_to_raw


__all__ = ["EmittedEvent", "EvLogKey", "EvLogEnabledKey", "EventSource", "evlog_enabled", "get_emitted_events"]


@dataclass
class EmittedEvent:
    """A single event emission site registered during elaboration.

    Attributes
    ----------
    source_name: str
        Name of the `EventSource` which registered this site.
    event_type: type[Event]
        The event type emitted at this site.
    location: SrcLoc
        Source location of the emission site.
    trigger: Value
        Single-bit Amaranth value. The event is recorded in every cycle in
        which the trigger is high.
    fields: dict[str, Value]
        Amaranth values backing the dynamic fields.
    statics: dict[str, Any]
        JSON-serializable values of the static fields.
    """

    source_name: str
    event_type: type[Event]
    location: SrcLoc
    trigger: Value
    fields: dict[str, Value]
    statics: dict[str, Any]


@dataclass(frozen=True)
class EvLogKey(ListKey[EmittedEvent]):
    """DependencyManager key collecting all event emission sites."""


@dataclass(frozen=True)
class EvLogEnabledKey(SimpleKey[bool]):
    """DependencyManager key for enabling the event log. If the event log
    is disabled (the default), `EventSource.emit` is a no-op and no signals
    are synthesized."""

    empty_valid = True
    default_value = False


def evlog_enabled() -> bool:
    """Checks if event logging is enabled in the current dependency context."""
    return DependencyContext.get().get_dependency(EvLogEnabledKey())


def get_emitted_events() -> list[EmittedEvent]:
    """Returns all event emission sites registered during elaboration,
    in registration order."""
    return DependencyContext.get().get_dependency(EvLogKey())


class EventSource:
    """Emission point for structured hardware events.

    An `EventSource` emits typed events (see `Event`) from the hardware
    description. Events are captured during simulation into an event log,
    which can be consumed by downstream tools (trace converters, statistics
    scripts) without touching the hardware description again.

    Like `HardwareLogger`, event sources are identified by a hierarchical
    dotted name, e.g. "frontend.ftq". Emitting the same event type multiple
    times (e.g. once per superscalar lane) is allowed; each `emit` call
    creates a separate emission site, typically distinguished by a static
    field. Usage::

        self.evlog = EventSource("exec.alu")
        ...
        self.evlog.emit(m, ExecUnitStart.hw(insn_tag=tag, lane=0), when=start)

    Attributes
    ----------
    name: str
        Name of this event source.
    """

    def __init__(self, name: str):
        """
        Parameters
        ----------
        name: str
            Name of this event source. Hierarchy levels are separated by
            periods, e.g. "frontend.ftq".
        """
        self.name = name

    def emit(self, m: ModuleLike, ev: Event, *, when: ValueLike = 1, src_loc: int | SrcLoc = 0):
        """Registers an event emission site.

        The event is recorded in every cycle in which `when` is true and the
        surrounding module context (`m.If` etc., transaction or method body)
        is active.

        Parameters
        ----------
        m: ModuleLike
            The module in which the event is emitted.
        ev: Event
            The emitted event. Dynamic fields must be Amaranth values;
            static fields must be plain Python values.
        when: ValueLike
            The event is recorded in cycles in which this Amaranth expression
            is true. Defaults to always (gated only by the module context).
        src_loc: int | SrcLoc, optional
            How many stack frames below to look for the source location.
        """
        if not evlog_enabled():
            return
        trigger = Signal()
        m.d.comb += trigger.eq(Value.cast(when).any())
        self.top_emit(ev, when=trigger, src_loc=get_src_loc(src_loc))

    def top_emit(self, ev: Event, *, when: ValueLike = 1, src_loc: int | SrcLoc = 0):
        """Registers an event emission site.

        Unlike `EventSource.emit`, this function ignores `m.If` etc. and can
        be used in contexts where a module is not available.

        See `EventSource.emit` for details.
        """
        if not evlog_enabled():
            return

        cls = type(ev)
        if not (isinstance(ev, Event) and hasattr(cls, "event_name")):
            raise TypeError(f"Emitted object must be an instance of an @event-decorated Event subclass, got: {ev!r}")

        fields: dict[str, Value] = {}
        for name in cls._dynamic_fields:
            value = getattr(ev, name)
            if not isinstance(value, (Value, ValueCastable, int, bool)):
                raise TypeError(f"Dynamic field '{name}' of event '{cls.event_name}' must be an Amaranth value")
            fields[name] = Value.cast(value)

        statics: dict[str, Any] = {}
        for name in cls._static_fields:
            value = getattr(ev, name)
            if isinstance(value, (Value, ValueCastable)):
                raise TypeError(f"Static field '{name}' of event '{cls.event_name}' must be a plain Python value")
            statics[name] = static_to_raw(value)

        record = EmittedEvent(
            source_name=self.name,
            event_type=cls,
            location=local_src_loc(get_src_loc(src_loc)),
            trigger=Value.cast(when).any(),
            fields=fields,
            statics=statics,
        )
        DependencyContext.get().add_dependency(EvLogKey(), record)
