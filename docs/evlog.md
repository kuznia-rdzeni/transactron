# Event log

The `transactron.evlog` package instruments a hardware design with structured, typed events which are captured during simulation into an *event log*. Event logs are consumed by downstream tools - pipeline trace converters, statistics scripts, checkers - without touching the hardware description again.

The event log carries machine-readable payloads: named, typed fields with a stable schema. The same captured log can be decoded offline, long after the simulation finished, as long as the schema (saved together with the log) and the event definitions are available.

## Defining events

Events are dataclasses declared with the `event` decorator. The event name must be globally unique:

```python
from transactron.evlog import Event, Static, event

@event("exec.unit_start")
class ExecUnitStart(Event):
    insn_tag: int
    kind: UnitKind          # an enum.Enum subclass
    lane: Static[int]
```

There are two kinds of fields:

- **dynamic** fields are backed by hardware signals, sampled in every cycle in which the event fires. Annotate them with the *decoded* Python type: `int`, `bool` or an `enum.Enum` subclass (raw integers are converted back to the annotated type when a log is decoded). Signal widths are not declared - they are inferred from the actual Amaranth values at elaboration time and recorded in the schema, so parameterized designs need no special handling.
- **static** fields (annotated with `Static[...]`) hold plain Python values fixed at elaboration time and cost no hardware. Use them for properties of the emission site, such as a lane index or a unit name.

## Emitting events

`EventSource` mirrors the `HardwareLogger` API. Since during elaboration the dynamic fields hold Amaranth values rather than their declared types, instances are constructed with the `Event.hw` classmethod:

```python
from transactron.evlog import EventSource

self.evlog = EventSource("exec.alu")
...
def elaborate(self, platform):
    ...
    self.evlog.emit(m, ExecUnitStart.hw(insn_tag=tag, kind=kind, lane=0), when=start)
```

The event fires in every cycle in which `when` is true and the surrounding module context (`m.If`, transaction or method body) is active. `top_emit` is available for contexts without a module. Emitting the same event type multiple times (e.g. once per superscalar lane, distinguished by a static field) creates separate emission sites.

Event logging is disabled by default and `emit` is then a no-op, so instrumentation has no cost in synthesis flows. Enable it before elaboration with:

```python
DependencyContext.get().add_dependency(EvLogEnabledKey(), True)
```

## Capturing events

Every emission site registered during elaboration is described by an `EvLogSchema`: the event name, source name, dynamic field names and widths, and static field values. The schema is the only contract between the elaborated design and event log consumers.

With the Amaranth simulator, capture events with `transactron.testing.evlog`:

```python
from transactron.testing.evlog import capture_evlog

with self.run_simulation(m) as sim:
    log, process = capture_evlog(metadata={"config": "full"})
    sim.add_process(process)
    sim.add_testbench(testbench)

events = log.decoded()      # or log.save("events.jsonl")
```

Tests based on `TestCaseWithSimulator` capture event logs automatically when pytest is invoked with `--transactron-evlog`: every test which registered any emission sites (i.e. tests which enabled the event log themselves) saves its captured events to `test/__evlogs__/<test name>.jsonl`. The flag only controls capturing - enabling the event log stays a separate concern.

For generated Verilog, `generate_verilog` records the event log schema together with signal locations in `GenerationInfo.evlog`, including a packed vector of all event triggers so that a simulator only needs a single signal read per cycle to check all sites. `GeneratedEvLogSampler` samples events through any backend able to read signals by hierarchical location (e.g. cocotb on top of Verilator):

```python
from transactron.evlog import EventLogWriter, GeneratedEvLogSampler

sampler = GeneratedEvLogSampler(gen_info.evlog, resolve_handle)
with EventLogWriter("events.jsonl", gen_info.evlog.schema) as writer:
    # once per clock cycle, when signals are stable (e.g. falling edge):
    sampler.sample(cycle, writer)
```

## Event log files and consumers

Event logs are stored as JSON lines: a header with the schema and free-form metadata, followed by one record per fired event. `EventLog.load` reads a log into memory; `EventLogReader` decodes it lazily for logs too large to hold in memory.

The `transactron-evlog` command pretty-prints event log files. It works from the schema alone; passing `-m <module>` imports the event definitions so that enum fields are printed as member names:

```
$ transactron-evlog -m coreblocks.telemetry -f "ftq" -c 8:10 events.jsonl
 8 frontend.ftq frontend.ftq_alloc      ftq_ptr=4 pc=0x00000110
 8 frontend.ftq frontend.ftq_commit     ftq_ptr=0
 9 frontend.ftq frontend.ftq_rollback   ftq_ptr=2 cause=ifu_writeback
10 frontend.ftq frontend.ftq_alloc      ftq_ptr=2 pc=0x00000200
```

`--schema` prints a summary of the emission sites instead; `-x`/`-d` force hex/decimal field values.

Consumers subclass `EventConsumer` and declare one handler per event type; dispatch is keyed by the registered event name:

```python
from transactron.evlog import DecodedEvent, EventConsumer, EventLogReader, handles

class KonataConverter(EventConsumer):
    @handles(ExecUnitStart)
    def on_unit_start(self, rec: DecodedEvent):
        ...  # rec.cycle, rec.source_name, rec.event (typed ExecUnitStart)

KonataConverter().run(EventLogReader("events.jsonl"))
```

`EventConsumer.run` sorts records by cycle, so capture order does not matter - which is convenient for output formats requiring monotonic timestamps. Events without a handler go to `on_unhandled`; override it to detect drift between the schema and the consumer.
