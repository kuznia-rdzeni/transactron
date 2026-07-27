import random
from amaranth import *
from amaranth import ShapeCastable
from amaranth import ValueCastable
from amaranth.lib import data, enum
from amaranth_types import ValueLike
from transactron.utils.amaranth_ext import mux
from transactron.testing import TestCaseWithSimulator, TestbenchContext


class FooEnum(enum.Enum, shape=1):
    FOO = 0
    BAR = 1


class InvertedView(ValueCastable):
    def __init__(self, val: ValueLike):
        self.val = Value.cast(val)

    def as_value(self):
        return self.val

    def shape(self):
        return Inverted(self.val.shape().width)

    def eq(self, other: ValueLike):
        return Value.cast(self).eq(other)


class Inverted(ShapeCastable):
    def __init__(self, width: int):
        self.width = width

    def as_shape(self):
        return Shape(self.width, signed=True)

    def const(self, val: int | None):
        if val is None:
            val = 0
        return InvertedView(C(~val, signed(self.width)))

    def __call__(self, arg: ValueLike):
        return InvertedView(arg)

    def from_bits(self, raw: int):
        return ~raw


class TestMux(TestCaseWithSimulator):
    def test_mux_signal(self):
        m = Module()
        sel = Signal()
        in1 = Signal(4)
        in2 = Signal(5)
        out = Signal(5)
        m.d.comb += out.eq(mux(sel, in1, in2))

        async def tb(ctx: TestbenchContext):
            for i in range(100):
                sel_val = random.randrange(2)
                in1_val = random.randrange(2 ** in1.shape().width)
                in2_val = random.randrange(2 ** in2.shape().width)
                ctx.set(sel, sel_val)
                ctx.set(in1, in1_val)
                ctx.set(in2, in2_val)
                out_val = ctx.get(out)
                assert out_val == (in1_val if sel_val else in2_val)

        with self.run_simulation(m) as sim:
            sim.add_testbench(tb)

    def test_mux_inverted(self):
        m = Module()
        sel = Signal()
        in1 = Signal(Inverted(4))
        out = Signal(Inverted(4))
        m.d.comb += out.eq(mux(sel, in1, 5))

        async def tb(ctx: TestbenchContext):
            for i in range(100):
                sel_val = random.randrange(2)
                in1_val = random.randrange(-(2 ** (in1.shape().width - 1)), 2 ** (in1.shape().width - 1))
                ctx.set(sel, sel_val)
                ctx.set(in1, in1_val)
                out_val = ctx.get(out)
                print(sel_val, in1_val, out_val)
                assert out_val == (in1_val if sel_val else 5)

        with self.run_simulation(m) as sim:
            sim.add_testbench(tb)

    def test_mux_enum(self):
        m = Module()
        sel = Signal()
        in1 = Signal(FooEnum)
        out = Signal(FooEnum)
        ret = mux(sel, in1, FooEnum.FOO)
        assert ret.shape() == FooEnum
        m.d.comb += out.eq(ret)

        async def tb(ctx: TestbenchContext):
            for i in range(100):
                sel_val = random.randrange(2)
                in1_val = random.choice(list(FooEnum))
                ctx.set(sel, sel_val)
                ctx.set(in1, in1_val)
                out_val = ctx.get(out)
                assert out_val == (in1_val if sel_val else FooEnum.FOO)

        with self.run_simulation(m) as sim:
            sim.add_testbench(tb)

    def test_mux_struct(self):
        shape = data.StructLayout({"x": 5})
        m = Module()
        sel = Signal()
        in1 = Signal(shape)
        in2 = Signal(shape)
        out = Signal(shape)
        ret = mux(sel, in1, in2)
        assert ret.shape() == shape
        m.d.comb += out.eq(ret)

        async def tb(ctx: TestbenchContext):
            for i in range(100):
                sel_val = random.randrange(2)
                in1_val = random.randrange(2 ** in1.x.shape().width)
                in2_val = random.randrange(2 ** in2.x.shape().width)
                ctx.set(sel, sel_val)
                ctx.set(in1, {"x": in1_val})
                ctx.set(in2, {"x": in2_val})
                out_val = ctx.get(out).x
                assert out_val == (in1_val if sel_val else in2_val)

        with self.run_simulation(m) as sim:
            sim.add_testbench(tb)
