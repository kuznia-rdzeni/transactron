from collections.abc import Iterable
from typing import Any, Callable, ClassVar

from .event import Event
from .log import DecodedEvent


__all__ = ["handles", "EventConsumer"]


def handles(event_type: type[Event]):
    """Marks an `EventConsumer` method as the handler for an event type.

    The decorated method is called with a single `DecodedEvent` argument for
    every log record of the given event type.
    """

    def wrap(fn):
        fn._evlog_handles = event_type
        return fn

    return wrap


class EventConsumer:
    """Base class for event log consumers.

    Subclasses implement one `handles`-decorated method per consumed event
    type, e.g. a converter to a trace format::

        class KonataConverter(EventConsumer):
            @handles(InsnStageEnter)
            def on_stage_enter(self, rec: DecodedEvent): ...

    Handlers are dispatched by the registered event name, so the consumer
    works with logs decoded from any capture backend. Events without a
    handler are passed to `on_unhandled`, which does nothing by default.
    """

    _handlers: ClassVar[dict[str, str]] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        handlers = dict(cls._handlers)
        for name, fn in vars(cls).items():
            handled = getattr(fn, "_evlog_handles", None)
            if handled is not None:
                handlers[handled.event_name] = name
        cls._handlers = handlers

    def dispatch(self, rec: DecodedEvent):
        """Calls the handler registered for the record's event type."""
        name = self._handlers.get(rec.event.event_name)
        if name is None:
            self.on_unhandled(rec)
        else:
            handler: Callable[[DecodedEvent], Any] = getattr(self, name)
            handler(rec)

    def on_unhandled(self, rec: DecodedEvent):
        """Called for events without a registered handler."""

    def run(self, records: Iterable[DecodedEvent]):
        """Dispatches all records, sorted by cycle.

        Sorting makes the consumer independent of the capture order, which
        matters for consumers of formats requiring monotonic timestamps.
        """
        for rec in sorted(records, key=lambda rec: rec.cycle):
            self.dispatch(rec)
