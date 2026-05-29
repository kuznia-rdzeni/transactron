from amaranth import ValueCastable

from transactron.testing import *
import random
import pytest
from amaranth import *
from amaranth.lib.enum import Enum
from transactron.utils.amaranth_ext import MultiPriorityEncoder, OneHotMux, RingMultiPriorityEncoder


def get_expected_multi(input_width, output_count, input, *args):
    places = []
    for i in range(input_width):
        if input % 2:
            places.append(i)
        input //= 2
    places += [None] * output_count
    return places


def get_expected_ring(input_width, output_count, input, first, last):
    places = []
    input = (input << input_width) + input
    if last < first:
        last += input_width
    for i in range(2 * input_width):
        if i >= first and i < last and input % 2:
            places.append(i % input_width)
        input //= 2
    places += [None] * output_count
    return places


class OneHotMuxEnum(Enum, shape=1):
    ZERO = 0
    ONE = 1


class TestOneHotMux(TestCaseWithSimulator):
    def test_values(self):
        class DUT(Elaboratable):
            def __init__(self):
                self.select_0 = Signal()
                self.select_1 = Signal()
                self.input_0 = Signal(2)
                self.input_1 = Signal(2)
                self.default = Const(3, 2)
                self.output = Signal(2)

            def elaborate(self, platform):
                m = Module()
                m.d.comb += self.output.eq(
                    OneHotMux.create(
                        m,
                        [(self.select_0, self.input_0), (self.select_1, self.input_1)],
                        self.default,
                    )
                )
                return m

        dut = DUT()

        async def proc(sim: TestbenchContext):
            test_vectors = [
                (0, 0, 1, 2, 3),
                (1, 0, 1, 2, 1),
                (0, 1, 1, 2, 2),
            ]
            for select_0, select_1, input_0, input_1, expected in test_vectors:
                sim.set(dut.select_0, select_0)
                sim.set(dut.select_1, select_1)
                sim.set(dut.input_0, input_0)
                sim.set(dut.input_1, input_1)
                await sim.tick()
                assert sim.get(dut.output) == expected

        with self.run_simulation(dut) as sim:
            sim.add_testbench(proc)

    def test_valuecastables(self):
        class DUT(Elaboratable):
            select_0: Value
            select_1: Value
            input_0: ValueCastable
            input_1: ValueCastable
            default: ValueCastable
            output: ValueCastable

            def __init__(self):
                self.select_0 = Signal()
                self.select_1 = Signal()
                self.input_0 = Signal(OneHotMuxEnum)  # type: ignore
                self.input_1 = Signal(OneHotMuxEnum)  # type: ignore
                self.default = Signal(OneHotMuxEnum)  # type: ignore
                self.output = Signal(OneHotMuxEnum)  # type: ignore

            def elaborate(self, platform):
                m = Module()
                m.d.comb += Value.cast(self.output).eq(
                    OneHotMux.create(
                        m,
                        [(self.select_0, self.input_0), (self.select_1, self.input_1)],
                        self.default,
                    )
                )
                return m

        dut = DUT()

        async def proc(sim: TestbenchContext):
            test_vectors = [
                (0, 0, OneHotMuxEnum.ZERO, OneHotMuxEnum.ONE, OneHotMuxEnum.ZERO),
                (1, 0, OneHotMuxEnum.ZERO, OneHotMuxEnum.ONE, OneHotMuxEnum.ZERO),
                (0, 1, OneHotMuxEnum.ZERO, OneHotMuxEnum.ONE, OneHotMuxEnum.ONE),
            ]
            for select_0, select_1, input_0, input_1, expected in test_vectors:
                sim.set(dut.select_0, select_0)
                sim.set(dut.select_1, select_1)
                sim.set(dut.input_0, input_0)
                sim.set(dut.input_1, input_1)
                sim.set(dut.default, OneHotMuxEnum.ZERO)
                await sim.tick()
                assert sim.get(dut.output) == expected.value

        with self.run_simulation(dut) as sim:
            sim.add_testbench(proc)


@pytest.mark.parametrize(
    "test_class, verif_f",
    [(MultiPriorityEncoder, get_expected_multi), (RingMultiPriorityEncoder, get_expected_ring)],
    ids=["MultiPriorityEncoder", "RingMultiPriorityEncoder"],
)
class TestPriorityEncoder(TestCaseWithSimulator):
    def process(self, get_expected):
        async def f(sim: TestbenchContext):
            for _ in range(self.test_number):
                input = random.randrange(2**self.input_width)
                first = random.randrange(self.input_width)
                last = random.randrange(self.input_width)
                sim.set(self.circ.input, input)
                try:
                    sim.set(self.circ.first, first)
                    sim.set(self.circ.last, last)
                except AttributeError:
                    pass
                expected_output = get_expected(self.input_width, self.output_count, input, first, last)
                for ex, real, valid in zip(expected_output, self.circ.outputs, self.circ.valids):
                    if ex is None:
                        assert sim.get(valid) == 0
                    else:
                        assert sim.get(valid) == 1
                        assert sim.get(real) == ex
                await sim.delay(1e-7)

        return f

    @pytest.mark.parametrize("input_width", [1, 5, 16, 23, 24])
    @pytest.mark.parametrize("output_count", [1, 3, 4])
    def test_random(self, test_class, verif_f, input_width, output_count):
        random.seed(input_width + output_count)
        self.test_number = 50
        self.input_width = input_width
        self.output_count = output_count
        self.circ = test_class(self.input_width, self.output_count)

        with self.run_simulation(self.circ) as sim:
            sim.add_testbench(self.process(verif_f))

    @pytest.mark.parametrize("name", ["prio_encoder", None])
    def test_static_create_simple(self, test_class, verif_f, name):
        random.seed(14)
        self.test_number = 50
        self.input_width = 7
        self.output_count = 1

        class DUT(Elaboratable):
            def __init__(self, input_width, output_count, name):
                self.input = Signal(input_width)
                self.first = Signal(range(input_width))
                self.last = Signal(range(input_width))
                self.output_count = output_count
                self.input_width = input_width
                self.name = name

            def elaborate(self, platform):
                m = Module()
                if test_class == MultiPriorityEncoder:
                    out, val = test_class.create_simple(m, self.input_width, self.input, name=self.name)
                else:
                    out, val = test_class.create_simple(
                        m, self.input_width, self.input, self.first, self.last, name=self.name
                    )
                # Save as a list to use common interface in testing
                self.outputs = [out]
                self.valids = [val]
                return m

        self.circ = DUT(self.input_width, self.output_count, name)

        with self.run_simulation(self.circ) as sim:
            sim.add_testbench(self.process(verif_f))

    @pytest.mark.parametrize("name", ["prio_encoder", None])
    def test_static_create(self, test_class, verif_f, name):
        random.seed(14)
        self.test_number = 50
        self.input_width = 7
        self.output_count = 2

        class DUT(Elaboratable):
            def __init__(self, input_width, output_count, name):
                self.input = Signal(input_width)
                self.first = Signal(range(input_width))
                self.last = Signal(range(input_width))
                self.output_count = output_count
                self.input_width = input_width
                self.name = name

            def elaborate(self, platform):
                m = Module()
                if test_class == MultiPriorityEncoder:
                    out = test_class.create(m, self.input_width, self.input, self.output_count, name=self.name)
                else:
                    out = test_class.create(
                        m, self.input_width, self.input, self.first, self.last, self.output_count, name=self.name
                    )
                self.outputs, self.valids = list(zip(*out))
                return m

        self.circ = DUT(self.input_width, self.output_count, name)

        with self.run_simulation(self.circ) as sim:
            sim.add_testbench(self.process(verif_f))
