import functools
import json
import random
import pytest
from typing import Type, Optional
from enum import IntFlag, IntEnum, auto, Enum
from collections import deque

from amaranth import *
from amaranth.lib.data import ArrayLayout

from transactron.lib.metrics import *
from transactron import *
from transactron.testing import TestCaseWithSimulator, data_layout, SimpleTestCircuit, TestbenchContext
from transactron.testing.tick_count import TicksKey
from transactron.utils.dependencies import DependencyContext


class CounterInMethodCircuit(Elaboratable):
    def __init__(self):
        self.method = Method()
        self.counter = HwCounter("in_method")

    def elaborate(self, platform):
        m = TModule()

        m.submodules.counter = self.counter

        @def_method(m, self.method)
        def _():
            self.counter.incr[0](m)

        return m


class CounterWithConditionInMethodCircuit(Elaboratable):
    def __init__(self):
        self.method = Method(i=[("cond", 1)])
        self.counter = HwCounter("with_condition_in_method")

    def elaborate(self, platform):
        m = TModule()

        m.submodules.counter = self.counter

        @def_method(m, self.method)
        def _(cond):
            self.counter.incr[0](m, enable_call=cond)

        return m


class CounterWithoutMethodCircuit(Elaboratable):
    def __init__(self, ways: int):
        self.cond = Signal(ways)
        self.counter = HwCounter("with_condition_without_method", ways=ways)

    def elaborate(self, platform):
        m = TModule()

        m.submodules.counter = self.counter

        with Transaction().body(m):
            for k in range(len(self.cond)):
                self.counter.incr[k](m, enable_call=self.cond[k])

        return m


class TestHwCounter(TestCaseWithSimulator):
    def setup_method(self) -> None:
        random.seed(42)

    def test_counter_in_method(self):
        DependencyContext.get().add_dependency(HwMetricsEnabledKey(), True)
        m = SimpleTestCircuit(CounterInMethodCircuit())

        async def test_process(sim):
            called_cnt = 0
            for _ in range(200):
                call_now = random.randint(0, 1) == 0

                if call_now:
                    await m.method.call(sim)
                    called_cnt += 1
                else:
                    await sim.tick()

                assert called_cnt == sim.get(m._dut.counter.count.value)

        with self.run_simulation(m) as sim:
            sim.add_testbench(test_process)

    def test_counter_with_condition_in_method(self):
        DependencyContext.get().add_dependency(HwMetricsEnabledKey(), True)
        m = SimpleTestCircuit(CounterWithConditionInMethodCircuit())

        async def test_process(sim):
            called_cnt = 0
            for _ in range(200):
                call_now = random.randint(0, 1) == 0
                condition = random.randint(0, 1)

                if call_now:
                    await m.method.call(sim, cond=condition)
                    called_cnt += condition
                else:
                    await sim.tick()

                assert called_cnt == sim.get(m._dut.counter.count.value)

        with self.run_simulation(m) as sim:
            sim.add_testbench(test_process)

    @pytest.mark.parametrize("ways", [1, 4])
    def test_counter_with_condition_without_method(self, ways):
        DependencyContext.get().add_dependency(HwMetricsEnabledKey(), True)
        m = CounterWithoutMethodCircuit(ways)

        async def test_process(sim):
            called_cnt = 0
            for _ in range(200):
                condition = random.randrange(2**ways)

                sim.set(m.cond, condition)
                await sim.tick()

                called_cnt += condition.bit_count()

                assert called_cnt == sim.get(m.counter.count.value)

        with self.run_simulation(m) as sim:
            sim.add_testbench(test_process)


class OneHotEnum(IntFlag):
    ADD = auto()
    XOR = auto()
    OR = auto()


class PlainIntEnum(IntEnum):
    TEST_1 = auto()
    TEST_2 = auto()
    TEST_3 = auto()


class TaggedCounterCircuit(Elaboratable):
    def __init__(self, tags: range | Type[Enum] | list[int], ways: int):
        self.counter = TaggedCounter("counter", "", tags=tags, ways=ways)

        self.cond = Signal(ways)
        self.tag = Signal(ArrayLayout(self.counter.tag_width, ways))

    def elaborate(self, platform):
        m = TModule()

        m.submodules.counter = self.counter

        with Transaction().body(m):
            for k in range(len(self.cond)):
                self.counter.incr[k](m, self.tag[k], enable_call=self.cond[k])

        return m


@pytest.mark.parametrize("ways", [1, 4])
class TestTaggedCounter(TestCaseWithSimulator):
    def setup_method(self) -> None:
        random.seed(42)

    def do_test_enum(self, tags: range | Type[Enum] | list[int], tag_values: list[int], ways: int):
        DependencyContext.get().add_dependency(HwMetricsEnabledKey(), True)
        m = TaggedCounterCircuit(tags, ways)

        counts: dict[int, int] = {}
        for i in tag_values:
            counts[i] = 0

        async def test_process(sim):
            for _ in range(200):
                for i in tag_values:
                    assert counts[i] == sim.get(m.counter.counters[i].value)

                condition = random.randrange(2**ways)
                tags = [random.choice(list(tag_values)) for _ in range(ways)]

                sim.set(m.cond, condition)
                sim.set(m.tag, tags)
                await sim.tick()

                for k in range(ways):
                    if condition & (1 << k):
                        counts[tags[k]] += 1

        with self.run_simulation(m) as sim:
            sim.add_testbench(test_process)

    def test_one_hot_enum(self, ways: int):
        self.do_test_enum(OneHotEnum, [e.value for e in OneHotEnum], ways)

    def test_plain_int_enum(self, ways: int):
        self.do_test_enum(PlainIntEnum, [e.value for e in PlainIntEnum], ways)

    def test_negative_range(self, ways: int):
        r = range(-10, 15, 3)
        self.do_test_enum(r, list(r), ways)

    def test_positive_range(self, ways: int):
        r = range(0, 30, 2)
        self.do_test_enum(r, list(r), ways)

    def test_value_list(self, ways):
        values = [-2137, 2, 4, 8, 42]
        self.do_test_enum(values, values, ways)


class ExpHistogramCircuit(Elaboratable):
    def __init__(self, bucket_cnt: int, sample_width: int):
        self.sample_width = sample_width

        self.method = Method(i=data_layout(32))
        self.histogram = HwExpHistogram("histogram", bucket_count=bucket_cnt, sample_width=sample_width)

    def elaborate(self, platform):
        m = TModule()

        m.submodules.histogram = self.histogram

        @def_method(m, self.method)
        def _(data):
            self.histogram.add(m, data[0 : self.sample_width])

        return m


@pytest.mark.parametrize("ways", [1, 4])
@pytest.mark.parametrize(
    "bucket_count, sample_width",
    [
        (5, 5),  # last bucket is [8, inf), max sample=31
        (8, 5),  # last bucket is [64, inf), max sample=31
        (8, 6),  # last bucket is [64, inf), max sample=63
        (8, 20),  # last bucket is [64, inf), max sample=big
    ],
)
class TestHwHistogram(TestCaseWithSimulator):
    def test_histogram(self, bucket_count: int, sample_width: int, ways: int):
        random.seed(42)

        DependencyContext.get().add_dependency(HwMetricsEnabledKey(), True)
        m = SimpleTestCircuit(
            HwExpHistogram("histogram", bucket_count=bucket_count, sample_width=sample_width, ways=ways)
        )

        max_sample_value = 2**sample_width - 1
        iterations = 500
        min = max_sample_value
        max = 0
        sum = 0
        count = 0
        buckets = [0] * bucket_count

        async def test_process(way: int, sim: TestbenchContext):
            nonlocal min, max, sum, count

            for _ in range(iterations):
                if random.randrange(3) == 0:
                    value = random.randint(0, max_sample_value)
                    if value < min:
                        min = value
                    if value > max:
                        max = value
                    sum += value
                    count += 1
                    for i in range(bucket_count):
                        if value < 2**i or i == bucket_count - 1:
                            buckets[i] += 1
                            break
                    await m.add[way].call(sim, sample=value)
                else:
                    await sim.tick()
                await sim.delay(1e-12)  # so verify_process reads correct values

        async def verify_process(sim: TestbenchContext):
            nonlocal min, max, sum, count

            for _ in range(iterations):
                await sim.tick()

                histogram = m._dut

                assert min == sim.get(histogram.min.value)
                assert max == sim.get(histogram.max.value)
                assert sum == sim.get(histogram.sum.value)
                assert count == sim.get(histogram.count.value)

                total_count = 0
                for i in range(bucket_count):
                    bucket_value = sim.get(histogram.buckets[i].value)
                    total_count += bucket_value
                    assert buckets[i] == bucket_value

                # Sanity check if all buckets sum up to the total count value
                assert total_count == sim.get(histogram.count.value)

        with self.run_simulation(m) as sim:
            sim.add_testbench(verify_process)
            for k in range(ways):
                sim.add_testbench(functools.partial(test_process, k))


class TestLatencyMeasurerBase(TestCaseWithSimulator):
    def check_latencies(self, sim, m: SimpleTestCircuit, latencies: list[int]):
        assert min(latencies) == sim.get(m._dut.histogram.min.value)
        assert max(latencies) == sim.get(m._dut.histogram.max.value)
        assert sum(latencies) == sim.get(m._dut.histogram.sum.value)
        assert len(latencies) == sim.get(m._dut.histogram.count.value)

        for i in range(m._dut.histogram.bucket_count):
            bucket_start = 0 if i == 0 else 2 ** (i - 1)
            bucket_end = 1e10 if i == m._dut.histogram.bucket_count - 1 else 2**i

            count = sum(1 for x in latencies if bucket_start <= x < bucket_end)
            assert count == sim.get(m._dut.histogram.buckets[i].value)


@pytest.mark.parametrize("ways", [1, 4])
@pytest.mark.parametrize("max_count", [None, 1, 4])
@pytest.mark.parametrize(
    "slots_number, expected_consumer_wait",
    [
        (2, 5),
        (2, 10),
        (5, 10),
        (10, 1),
        (10, 10),
        (5, 5),
    ],
)
class TestFIFOLatencyMeasurer(TestLatencyMeasurerBase):
    def test_latency_measurer(
        self, slots_number: int, expected_consumer_wait: float, ways: int, max_count: Optional[int]
    ):
        random.seed(42)

        DependencyContext.get().add_dependency(HwMetricsEnabledKey(), True)
        if max_count is not None:
            dut = WideFIFOLatencyMeasurer(
                "latency", slots_number=slots_number, max_latency=300, ways=ways, max_start_count=max_count
            )
        else:
            dut = FIFOLatencyMeasurer("latency", slots_number=slots_number, max_latency=300, ways=ways)
        m = SimpleTestCircuit(dut)

        latencies: list[int] = []

        event_queue = [deque() for _ in range(ways)]

        finish = [False for _ in range(ways)]

        async def producer(way: int, sim: TestbenchContext):
            ticks = DependencyContext.get().get_dependency(TicksKey())

            for _ in range(200 // ways):
                if max_count is not None:
                    count = random.randrange(1, max_count + 1)
                    await m.start[way].call(sim, count=count)
                else:
                    count = 1
                    await m.start[way].call(sim)

                for _ in range(count):
                    event_queue[way].append(sim.get(ticks))
                await self.random_wait_geom(sim, 0.8)

            finish[way] = True

        async def consumer(way: int, sim: TestbenchContext):
            ticks = DependencyContext.get().get_dependency(TicksKey())

            while not finish[way]:
                if max_count is not None:
                    count = random.randrange(1, min(max_count, max(1, len(event_queue[way]))) + 1)
                    await m.stop[way].call(sim, count=count)
                else:
                    count = 1
                    await m.stop[way].call(sim)

                for _ in range(count):
                    latencies.append(sim.get(ticks) - event_queue[way].popleft())

                await self.random_wait_geom(sim, 1.0 / expected_consumer_wait)

        async def verifier(sim: TestbenchContext):
            while not all(finish):
                await sim.tick()

            await sim.delay(1e-12)  # so that consumer can update global state
            self.check_latencies(sim, m, latencies)

        with self.run_simulation(m) as sim:
            sim.add_testbench(verifier)
            for k in range(ways):
                sim.add_testbench(functools.partial(producer, k))
                sim.add_testbench(functools.partial(consumer, k))


@pytest.mark.parametrize(
    "ways, slots_number, expected_consumer_wait",
    [
        (1, 1, 10),
        (1, 2, 5),
        (1, 2, 10),
        (1, 5, 10),
        (1, 10, 1),
        (1, 10, 10),
        (4, 10, 1),
        (4, 10, 10),
        (1, 5, 5),
    ],
)
class TestTaggedLatencyMeasurer(TestLatencyMeasurerBase):
    def test_latency_measurer(self, slots_number: int, expected_consumer_wait: float, ways: int):
        random.seed(42)

        DependencyContext.get().add_dependency(HwMetricsEnabledKey(), True)
        m = SimpleTestCircuit(TaggedLatencyMeasurer("latency", slots_number=slots_number, max_latency=2000, ways=ways))

        latencies: list[int] = []

        events = list(0 for _ in range(slots_number))
        free_slots = list(k for k in range(slots_number))
        used_slots: list[int] = []

        finish = [False for _ in range(ways)]

        iterations = 200

        async def producer(way: int, sim: TestbenchContext):
            tick_count = DependencyContext.get().get_dependency(TicksKey())

            for _ in range(iterations // ways):
                while not free_slots:
                    await sim.tick()

                slot_id = random.choice(free_slots)
                free_slots.remove(slot_id)
                await m.start[way].call(sim, slot=slot_id)

                await sim.delay(1e-12)

                events[slot_id] = sim.get(tick_count)
                used_slots.append(slot_id)

                await self.random_wait_geom(sim, 0.8)

            finish[way] = True

        async def consumer(way: int, sim: TestbenchContext):
            tick_count = DependencyContext.get().get_dependency(TicksKey())

            while not finish[way]:
                while not used_slots:
                    await sim.tick()

                slot_id = random.choice(used_slots)
                used_slots.remove(slot_id)
                await m.stop[way].call(sim, slot=slot_id)

                await sim.delay(1e-12)

                latencies.append(sim.get(tick_count) - events[slot_id])
                free_slots.append(slot_id)

                await self.random_wait_geom(sim, 1.0 / expected_consumer_wait, max_cycle_cnt=500)

        async def verifier(sim: TestbenchContext):
            while not all(finish):
                await sim.tick()

            await sim.delay(3e-12)  # so that consumer can update global state
            self.check_latencies(sim, m, latencies)

        with self.run_simulation(m) as sim:
            sim.add_testbench(verifier)
            for k in range(ways):
                sim.add_testbench(functools.partial(producer, k))
                sim.add_testbench(functools.partial(consumer, k))


class MetricManagerTestCircuit(Elaboratable):
    def __init__(self):
        self.incr_counters = Method(i=[("counter1", 1), ("counter2", 1), ("counter3", 1)])

        self.counter1 = HwCounter("foo.counter1", "this is the description")
        self.counter2 = HwCounter("bar.baz.counter2")
        self.counter3 = HwCounter("bar.baz.counter3", "yet another description")

    def elaborate(self, platform):
        m = TModule()

        m.submodules += [self.counter1, self.counter2, self.counter3]

        @def_method(m, self.incr_counters)
        def _(counter1, counter2, counter3):
            self.counter1.incr[0](m, enable_call=counter1)
            self.counter2.incr[0](m, enable_call=counter2)
            self.counter3.incr[0](m, enable_call=counter3)

        return m


class TestMetricsManager(TestCaseWithSimulator):
    def test_metrics_metadata(self):
        # We need to initialize the circuit to make sure that metrics are registered
        # in the dependency manager.
        m = MetricManagerTestCircuit()
        metrics_manager = HardwareMetricsManager()

        # Run the simulation so Amaranth doesn't scream that we have unused elaboratables.
        with self.run_simulation(m):
            pass

        assert metrics_manager.get_metrics()["foo.counter1"].to_json() == json.dumps(  # type: ignore
            {
                "fully_qualified_name": "foo.counter1",
                "description": "this is the description",
                "regs": {"count": {"name": "count", "description": "the value of the counter", "width": 32}},
            }
        )

        assert metrics_manager.get_metrics()["bar.baz.counter2"].to_json() == json.dumps(  # type: ignore
            {
                "fully_qualified_name": "bar.baz.counter2",
                "description": "",
                "regs": {"count": {"name": "count", "description": "the value of the counter", "width": 32}},
            }
        )

        assert metrics_manager.get_metrics()["bar.baz.counter3"].to_json() == json.dumps(  # type: ignore
            {
                "fully_qualified_name": "bar.baz.counter3",
                "description": "yet another description",
                "regs": {"count": {"name": "count", "description": "the value of the counter", "width": 32}},
            }
        )

    def test_returned_reg_values(self):
        random.seed(42)

        DependencyContext.get().add_dependency(HwMetricsEnabledKey(), True)
        m = SimpleTestCircuit(MetricManagerTestCircuit())
        metrics_manager = HardwareMetricsManager()

        async def test_process(sim):
            counters = [0] * 3
            for _ in range(200):
                rand = [random.randint(0, 1) for _ in range(3)]

                await m.incr_counters.call(sim, counter1=rand[0], counter2=rand[1], counter3=rand[2])

                for i in range(3):
                    if rand[i] == 1:
                        counters[i] += 1

                assert counters[0] == sim.get(metrics_manager.get_register_value("foo.counter1", "count"))
                assert counters[1] == sim.get(metrics_manager.get_register_value("bar.baz.counter2", "count"))
                assert counters[2] == sim.get(metrics_manager.get_register_value("bar.baz.counter3", "count"))

        with self.run_simulation(m) as sim:
            sim.add_testbench(test_process)
