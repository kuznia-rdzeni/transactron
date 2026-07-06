from itertools import chain
from typing import Any, Optional

from amaranth.sim._async import ProcessContext

from transactron.utils.dependencies import DependencyContext
from transactron.evlog import EmittedEvent, EventLog, RawEventSink, get_emitted_events, schema_from_records
from .tick_count import TicksKey


__all__ = ["capture_evlog"]


def _make_evlog_process(sink: RawEventSink, records: Optional[list[EmittedEvent]]):
    """Creates a simulation process which samples all registered event
    emission sites every clock cycle and reports fired events to the sink.
    """

    async def evlog_process(sim: ProcessContext):
        if not records:
            return

        ticks = DependencyContext.get().get_dependency(TicksKey())

        to_sample = chain.from_iterable((rec.trigger, *rec.fields.values()) for rec in records)
        tick_trigger = sim.tick().sample(ticks).sample(*to_sample)

        async for _, _, ticks_val, *values in tick_trigger:
            it = iter(values)
            for site, rec in enumerate(records):
                trigger = next(it)
                field_values = [next(it) for _ in rec.fields]
                if trigger:
                    sink.emit_raw(ticks_val, site, field_values)

    return evlog_process


def capture_evlog(metadata: Optional[dict[str, Any]] = None):
    """Creates an in-memory event log capturing all registered event emission
    sites, together with the simulation process filling it.

    Must be called after the design is elaborated. Usage::

        with self.run_simulation(m) as sim:
            log, process = capture_evlog()
            sim.add_process(process)
            sim.add_testbench(...)

        events = log.decoded()

    Parameters
    ----------
    metadata: dict[str, Any], optional
        Metadata stored in the event log schema.
    """
    records = get_emitted_events()
    log = EventLog(schema_from_records(records, metadata))
    return log, _make_evlog_process(log, records)
