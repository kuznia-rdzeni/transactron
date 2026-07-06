from transactron.testing import *
from transactron.utils.amaranth_ext.data import *
from amaranth import *
from amaranth.lib import data
import pytest
import random


class TestLayoutKeys:
    def test_layout_keys(self):
        assert list(layout_keys(data.ArrayLayout(4, 3))) == [0, 1, 2]
        assert list(layout_keys(data.StructLayout({"a": 1, "b": 2, "c": 3}))) == ["a", "b", "c"]
        assert list(layout_keys(data.UnionLayout({"a": 1, "b": 2, "c": 3}))) == ["a", "b", "c"]
        assert list(layout_keys(data.FlexibleLayout(10, {0: data.Field(2, 0), "a": data.Field(8, 2)}))) == [0, "a"]


class TestTranspose(TestCaseWithSimulator):
    @pytest.mark.parametrize(
        "layout",
        [
            data.ArrayLayout(data.ArrayLayout(4, 3), 3),
            data.ArrayLayout(data.StructLayout({"a": 2, "b": 3, "c": 4}), 3),
            data.StructLayout({"a": data.ArrayLayout(2, 3), "b": data.ArrayLayout(3, 3), "c": data.ArrayLayout(4, 3)}),
            data.StructLayout(
                {
                    "a": data.StructLayout({"d": 2, "e": 3}),
                    "b": data.StructLayout({"d": 4, "e": 5}),
                    "c": data.StructLayout({"d": 6, "e": 7}),
                }
            ),
        ],
    )
    def test_transpose(self, layout: data.Layout):
        dut = Module()
        sig = Signal(layout)
        tr_layout, o_keys, i_keys = transpose_layout_with_keys(layout)
        tr_sig = Signal(tr_layout)
        dut.d.comb += tr_sig.eq(transpose(sig))

        assert layout_keys(layout) == o_keys
        assert layout_keys(layout[o_keys[0]].shape) == i_keys  # type: ignore
        assert layout_keys(tr_layout) == i_keys
        assert layout_keys(tr_layout[i_keys[0]].shape) == o_keys  # type: ignore

        iterations = 20

        async def testbench(ctx: TestbenchContext):
            for i in range(iterations):
                for o_key in o_keys:
                    for i_key in i_keys:
                        ctx.set(sig[o_key][i_key], random.randrange(2 ** sig[o_key][i_key].shape().width))
                        assert ctx.get(sig[o_key][i_key]) == ctx.get(tr_sig[i_key][o_key])

        with self.run_simulation(dut) as sim:
            sim.add_testbench(testbench)
