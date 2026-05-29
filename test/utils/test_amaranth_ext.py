from transactron.testing import *
import random
import pytest
from amaranth import *
from amaranth.lib.data import ArrayLayout
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


def one_hot_mux_reference(select_values, input_values, default_value, is_priority):
    selected_index = None
    if is_priority:
        for index, select in enumerate(select_values):
            if select:
                selected_index = index
                break
    else:
        assert sum(1 for select in select_values if select) <= 1
        for index, select in enumerate(select_values):
            if select:
                selected_index = index
                break

    if selected_index is None:
        if default_value is not None:
            return default_value
        if len(input_values) == 1:
            return input_values[0]
        return 0

    return input_values[selected_index]


class OneHotMuxDUT(Elaboratable):
    def __init__(self, shape, input_count, is_priority, has_default):
        self.select = [Signal() for _ in range(input_count)]
        self.inputs = [Signal(shape) for _ in range(input_count)]  # type: ignore
        self.output = Signal(shape)  # type: ignore
        self.default = Signal(shape) if has_default else None  # type: ignore
        self.is_priority = is_priority
        self.has_default = has_default

    def elaborate(self, platform):
        m = Module()
        m.d.comb += Value.cast(self.output).eq(
            OneHotMux.create(
                m,
                list(zip(self.select, self.inputs)),
                self.default if self.has_default else None,
                priority=self.is_priority,
            )
        )
        return m


class TestOneHotMux(TestCaseWithSimulator):
    @pytest.mark.parametrize("is_valuecastable", [False, True])
    @pytest.mark.parametrize("has_default", [False, True])
    @pytest.mark.parametrize("is_priority", [False, True])
    @pytest.mark.parametrize("input_count", [1, 3, 7])
    @pytest.mark.parametrize("width", [1, 6, 15])
    def test_randomized(self, is_valuecastable, has_default, is_priority, input_count, width):
        random.seed(f"{int(is_valuecastable)}-{int(has_default)}-{int(is_priority)}-{input_count}-{width}")

        shape = ArrayLayout(1, width) if is_valuecastable else width
        dut = OneHotMuxDUT(shape=shape, input_count=input_count, is_priority=is_priority, has_default=has_default)

        def from_repr(x):
            return shape.from_bits(x) if is_valuecastable else x

        async def proc(sim: TestbenchContext):
            for _ in range(100):
                if is_priority:
                    if has_default:
                        select = random.randrange(0, 2**input_count)
                    else:
                        select = random.randrange(1, 2**input_count)
                else:
                    if has_default:
                        select = random.randrange(0, input_count + 1)
                        select = 0 if select == input_count else 1 << select
                    else:
                        select = 1 << random.randrange(0, input_count)

                values = [random.randrange(0, 2**width) for _ in range(input_count)]
                default_value = random.randrange(0, 2**width) if has_default else None

                for i in range(input_count):
                    sim.set(dut.select[i], (select >> i) & 1)
                    sim.set(dut.inputs[i], from_repr(values[i]))  # type: ignore

                if has_default:
                    sim.set(dut.default, from_repr(default_value))  # type: ignore

                await sim.delay(1e-7)

                got = sim.get(dut.output)

                if is_valuecastable:
                    got = Const.cast(got).value

                expected = one_hot_mux_reference(
                    select_values=[(select >> i) & 1 for i in range(input_count)],
                    input_values=values,
                    default_value=default_value,
                    is_priority=is_priority,
                )

                assert got == expected

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
