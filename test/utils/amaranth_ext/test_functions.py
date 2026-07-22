import random
from amaranth import *
from amaranth.lib import data
from transactron.utils.amaranth_ext import mux
from transactron.testing import TestCaseWithSimulator, TestbenchContext


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
                in1_val = random.randrange(2**in1.shape().width)
                in2_val = random.randrange(2**in2.shape().width)
                ctx.set(sel, sel_val)
                ctx.set(in1, in1_val)
                ctx.set(in2, in2_val)
                out_val = ctx.get(out)
                assert out_val == (in1_val if sel_val else in2_val)

        with self.run_simulation(m) as sim:
            sim.add_testbench(tb)

    def test_mux_struct(self):
        shape = data.StructLayout({'x': 5})
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
                in1_val = random.randrange(2**in1.x.shape().width)
                in2_val = random.randrange(2**in2.x.shape().width)
                ctx.set(sel, sel_val)
                ctx.set(in1, {'x': in1_val})
                ctx.set(in2, {'x': in2_val})
                out_val = ctx.get(out).x
                assert out_val == (in1_val if sel_val else in2_val)

        with self.run_simulation(m) as sim:
            sim.add_testbench(tb)
