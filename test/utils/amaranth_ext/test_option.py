import random
import pytest
from amaranth import *
from amaranth.lib import data
from transactron.utils.amaranth_ext import Option
from transactron.testing import TestCaseWithSimulator, TestbenchContext


struct = data.StructLayout({"a": 3, "b": 5})


class TestOption(TestCaseWithSimulator):
    def test_shape(self):
        opt = Option(8)
        assert opt == Option(8)
        assert opt != Option(4)
        assert opt == opt.const(None).shape()

    def test_const(self):
        opt = Option(8)
        for value in [None, 0, 5]:
            assert opt.from_bits(Const.cast(opt.const(value)).value) == value
        assert Const.cast(opt(None)).value == 0
        value = struct.const({"a": 2, "b": 7})
        assert Option(struct).from_bits(Const.cast(Option(struct).const(value)).value) == value

    @pytest.mark.parametrize("data_shape, value", [(8, 5), (struct, struct.const({"a": 2, "b": 7}))])
    def test_assign_const(self, data_shape, value):
        opt = Option(data_shape)
        m = Module()
        eq_out = opt.signal()
        data_eq_out = opt.signal()
        empty_out = opt.signal()
        m.d.comb += eq_out.eq(opt.const(value))
        m.d.comb += data_eq_out.data_eq(value)
        m.d.comb += empty_out.eq(opt.const(None))

        async def testbench(ctx: TestbenchContext):
            expected = Const.cast(opt.const(value)).value
            assert ctx.get(Value.cast(eq_out)) == expected
            assert ctx.get(Value.cast(data_eq_out)) == expected
            assert ctx.get(empty_out) is None

        with self.run_simulation(m) as sim:
            sim.add_testbench(testbench)

    def test_with_data(self):
        opt = Option(8)
        m = Module()
        src = opt.signal()
        out = opt.signal()
        with src.with_data(m) as d:
            m.d.comb += out.data_eq(d + 1)
        with m.Else():
            m.d.comb += out.data_eq(None)

        async def testbench(ctx: TestbenchContext):
            for _ in range(50):
                value = random.choice([None, random.randrange(2**8)])
                ctx.set(src, value)
                assert ctx.get(out) == (None if value is None else (value + 1) % 2**8)

        with self.run_simulation(m) as sim:
            sim.add_testbench(testbench)

    def test_compare(self):
        opt = Option(2)
        m = Module()
        a = opt.signal()
        b = opt.signal()
        eq = Signal()
        ne = Signal()
        m.d.comb += [eq.eq(a == b), ne.eq(a != b)]

        async def testbench(ctx: TestbenchContext):
            for x in range(2**3):
                for y in range(2**3):
                    ctx.set(Value.cast(a), x)
                    ctx.set(Value.cast(b), y)
                    expected = opt.from_bits(x) == opt.from_bits(y)
                    assert ctx.get(eq) == expected
                    assert ctx.get(ne) != expected

        with self.run_simulation(m) as sim:
            sim.add_testbench(testbench)
