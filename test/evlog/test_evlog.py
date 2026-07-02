from enum import IntEnum
from io import StringIO

import pytest
from amaranth import *
from amaranth.lib.wiring import Component, In, Out

from transactron import TModule
from transactron.evlog import (
    Event,
    EventConsumer,
    EventFieldSchema,
    EventLog,
    EventLogReader,
    EventSiteLocation,
    EventSiteSchema,
    EventSource,
    EvLogEnabledKey,
    EvLogSchema,
    GeneratedEvLog,
    GeneratedEvLogSampler,
    Static,
    event,
    get_emitted_events,
    get_event_class,
    handles,
)
from transactron.evlog.log import DecodedEvent
from transactron.testing import TestCaseWithSimulator, TestbenchContext
from transactron.testing.evlog import capture_evlog
from transactron.utils.dependencies import DependencyContext


class UnitKind(IntEnum):
    ALU = 0
    MUL = 1


@event("test.insn_start")
class InsnStart(Event):
    tag: int
    pc: int
    kind: UnitKind
    lane: Static[int]


@event("test.insn_done")
class InsnDone(Event):
    tag: int
    note: Static[str] = "done"


class TestEventDefinition:
    def test_field_split(self):
        assert InsnStart.event_name == "test.insn_start"
        assert InsnStart._dynamic_fields == ("tag", "pc", "kind")
        assert InsnStart._static_fields == ("lane",)
        assert get_event_class("test.insn_start") is InsnStart

    def test_from_raw(self):
        ev = InsnStart.from_raw({"tag": 3, "pc": 100, "kind": 1}, {"lane": 2})
        assert ev == InsnStart(tag=3, pc=100, kind=UnitKind.MUL, lane=2)
        assert isinstance(ev.kind, UnitKind)

    def test_duplicate_name(self):
        with pytest.raises(ValueError):

            @event("test.insn_start")
            class Duplicate(Event):
                x: int

    def test_unknown_event(self):
        with pytest.raises(KeyError):
            get_event_class("test.does_not_exist")


class EvLogTestCircuit(Elaboratable):
    def __init__(self):
        self.tag = Signal(4)
        self.pc = Signal(8)
        self.kind = Signal(UnitKind)
        self.start = Signal()
        self.done = Signal()
        self.evlog = EventSource("test.circuit")

    def elaborate(self, platform):
        m = TModule()

        with m.If(self.start):
            self.evlog.emit(m, InsnStart.hw(tag=self.tag, pc=self.pc, kind=self.kind, lane=0))

        self.evlog.emit(m, InsnDone.hw(tag=self.tag), when=self.done)

        return m


class TestEvLogCapture(TestCaseWithSimulator):
    def run_capture(self):
        DependencyContext.get().add_dependency(EvLogEnabledKey(), True)
        m = EvLogTestCircuit()

        async def proc(sim: TestbenchContext):
            sim.set(m.tag, 1)
            sim.set(m.pc, 100)
            sim.set(m.kind, UnitKind.MUL)
            sim.set(m.start, 1)
            await sim.tick()
            sim.set(m.start, 0)
            sim.set(m.done, 1)
            await sim.tick()
            sim.set(m.done, 0)
            await sim.tick()

        with self.run_simulation(m) as sim:
            log, process = capture_evlog(metadata={"config": "test_config"})
            sim.add_process(process)
            sim.add_testbench(proc)

        return log

    def test_capture(self):
        log = self.run_capture()

        assert log.schema.metadata == {"config": "test_config"}
        assert [site.event_name for site in log.schema.sites] == ["test.insn_start", "test.insn_done"]
        assert log.schema.sites[0].source_name == "test.circuit"
        assert log.schema.sites[0].statics == {"lane": 0}
        assert log.schema.sites[0].fields == [
            EventFieldSchema(name="tag", width=4),
            EventFieldSchema(name="pc", width=8),
            EventFieldSchema(name="kind", width=1),
        ]
        assert log.schema.sites[1].statics == {"note": "done"}

        events = log.decoded()
        assert len(events) == 2

        start, done = events
        assert start.source_name == "test.circuit"
        assert start.event == InsnStart(tag=1, pc=100, kind=UnitKind.MUL, lane=0)
        assert done.event == InsnDone(tag=1, note="done")
        assert done.cycle == start.cycle + 1

    def test_save_load_roundtrip(self, tmp_path):
        log = self.run_capture()

        path = str(tmp_path / "events.jsonl")
        log.save(path)

        loaded = EventLog.load(path)
        assert loaded.schema == log.schema
        assert loaded.raw == log.raw
        assert loaded.decoded() == log.decoded()

        reader = EventLogReader(path)
        assert reader.schema == log.schema
        assert list(reader) == log.decoded()

    def test_disabled(self):
        # Event log is disabled by default: no sites registered, empty log.
        m = EvLogTestCircuit()

        async def proc(sim: TestbenchContext):
            sim.set(m.start, 1)
            await sim.tick()
            await sim.tick()

        with self.run_simulation(m) as sim:
            log, process = capture_evlog()
            sim.add_process(process)
            sim.add_testbench(proc)

        assert get_emitted_events() == []
        assert log.schema.sites == []
        assert log.decoded() == []


def make_synthetic_log() -> EventLog:
    schema = EvLogSchema(
        sites=[
            EventSiteSchema(
                source_name="test.src",
                event_name="test.insn_start",
                location=("file.py", 1),
                fields=[
                    EventFieldSchema(name="tag", width=4),
                    EventFieldSchema(name="pc", width=8),
                    EventFieldSchema(name="kind", width=1),
                ],
                statics={"lane": 1},
            ),
            EventSiteSchema(
                source_name="test.src",
                event_name="test.insn_done",
                location=("file.py", 2),
                fields=[EventFieldSchema(name="tag", width=4)],
                statics={"note": "bye"},
            ),
        ]
    )
    log = EventLog(schema)
    log.emit_raw(7, 0, [2, 50, 0])
    log.emit_raw(3, 1, [2])
    return log


class TestEventConsumer:
    def test_dispatch_sorted(self):
        class Collector(EventConsumer):
            def __init__(self):
                self.seen: list[tuple[int, Event]] = []
                self.unhandled: list[DecodedEvent] = []

            @handles(InsnStart)
            def on_start(self, rec: DecodedEvent):
                self.seen.append((rec.cycle, rec.event))

            def on_unhandled(self, rec: DecodedEvent):
                self.unhandled.append(rec)

        collector = Collector()
        collector.run(make_synthetic_log().decoded())

        assert collector.seen == [(7, InsnStart(tag=2, pc=50, kind=UnitKind.ALU, lane=1))]
        assert [rec.cycle for rec in collector.unhandled] == [3]

    def test_handler_inheritance(self):
        class Base(EventConsumer):
            @handles(InsnStart)
            def on_start(self, rec: DecodedEvent):
                pass

        class Derived(Base):
            @handles(InsnDone)
            def on_done(self, rec: DecodedEvent):
                pass

        assert Base._handlers == {"test.insn_start": "on_start"}
        assert Derived._handlers == {"test.insn_start": "on_start", "test.insn_done": "on_done"}


class TestPrettyPrinter:
    def print_lines(self, **kwargs) -> list[str]:
        from transactron.cmd.evlog import print_events

        kwargs = {"mode": "auto", "name_filter": None, "cycles": None} | kwargs
        out = StringIO()
        print_events(make_synthetic_log(), out, **kwargs)
        return out.getvalue().splitlines()

    def test_print_events(self):
        # Sorted by cycle; enum fields printed as member names; fields wider
        # than 8 bits in hex; statics appended.
        assert self.print_lines() == [
            "3 test.src test.insn_done   tag=2 note=bye",
            "7 test.src test.insn_start  tag=2 pc=50 kind=ALU lane=1",
        ]

    def test_value_modes(self):
        assert self.print_lines(mode="hex")[1].endswith("tag=0x2 pc=0x32 kind=ALU lane=1")
        assert self.print_lines(mode="dec")[1].endswith("tag=2 pc=50 kind=ALU lane=1")

    def test_filter_and_cycles(self):
        assert len(self.print_lines(name_filter="insn_start")) == 1
        assert len(self.print_lines(cycles="0:5")) == 1
        assert len(self.print_lines(cycles="4:")) == 1

    def test_main(self, tmp_path, capsys):
        from transactron.cmd.evlog import main

        path = str(tmp_path / "events.jsonl")
        make_synthetic_log().save(path)

        main([path, "-f", "insn_done", "-d"])
        assert capsys.readouterr().out == "3 test.src test.insn_done  tag=2 note=bye\n"

        main([path, "--schema"])
        schema_out = capsys.readouterr().out
        assert "test.insn_start (tag[4], pc[8], kind[1]) lane=1" in schema_out


class TestGeneratedEvLogSampler:
    def test_sampler(self):
        log = make_synthetic_log()
        generated = GeneratedEvLog(
            schema=log.schema,
            site_locations=[
                EventSiteLocation(trigger=["top", "t0"], fields=[["top", "tag0"], ["top", "pc0"], ["top", "kind0"]]),
                EventSiteLocation(trigger=["top", "t1"], fields=[["top", "tag1"]]),
            ],
            triggers_location=["top", "triggers"],
        )

        values = {"top.triggers": 0, "top.t0": 0, "top.t1": 0, "top.tag0": 2, "top.pc0": 50, "top.kind0": 0}
        values["top.tag1"] = 3

        def resolve(location):
            key = ".".join(location)
            return lambda: values[key]

        sampler = GeneratedEvLogSampler(generated, resolve)
        sink = EventLog(log.schema)

        sampler.sample(0, sink)
        assert sink.raw == []

        values["top.triggers"] = 0b10
        sampler.sample(1, sink)
        assert sink.raw == [(1, 1, [3])]

        values["top.triggers"] = 0b01
        sampler.sample(2, sink)
        assert sink.raw == [(1, 1, [3]), (2, 0, [2, 50, 0])]

        # Without the packed trigger vector, per-site triggers are used.
        generated_unpacked = GeneratedEvLog(
            schema=generated.schema, site_locations=generated.site_locations, triggers_location=None
        )
        sampler = GeneratedEvLogSampler(generated_unpacked, resolve)
        sink = EventLog(log.schema)
        values["top.t1"] = 1
        sampler.sample(5, sink)
        assert sink.raw == [(5, 1, [3])]


@event("test.gen_event")
class GenEvent(Event):
    value: int


class GenTestCircuit(Component):
    din: Signal
    dout: Signal

    def __init__(self):
        super().__init__({"din": In(8), "dout": Out(8)})
        self.evlog = EventSource("test.gen")

    def elaborate(self, platform):
        m = Module()
        m.d.comb += self.dout.eq(self.din + 1)
        self.evlog.emit(m, GenEvent.hw(value=self.din), when=self.din[0])
        return m


class TestEmitValidation(TestCaseWithSimulator):
    def test_static_field_with_signal(self):
        DependencyContext.get().add_dependency(EvLogEnabledKey(), True)
        source = EventSource("test.validation")
        with pytest.raises(TypeError):
            source.top_emit(InsnStart.hw(tag=Signal(4), pc=Signal(8), kind=Signal(UnitKind), lane=Signal(2)))

    def test_non_event(self):
        DependencyContext.get().add_dependency(EvLogEnabledKey(), True)
        source = EventSource("test.validation")
        with pytest.raises(TypeError):
            source.top_emit(42)  # type: ignore
