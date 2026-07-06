from typing import Any, overload
from amaranth_types import ShapeLike
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


@overload
def mk_const(shape: data.StructLayout) -> dict[str, Any]: ...


@overload
def mk_const(shape: data.ArrayLayout) -> list[Any]: ...


@overload
def mk_const(shape: int) -> int: ...


@overload
def mk_const(shape: ShapeLike) -> Any: ...


def mk_const(shape: ShapeLike):
    if isinstance(shape, data.StructLayout):
        return {k: mk_const(shape[k].shape) for k in layout_keys(shape)}
    elif isinstance(shape, data.ArrayLayout):
        return [mk_const(shape[k].shape) for k in range(shape.length)]
    elif isinstance(shape, int):
        return random.randrange(2**shape)
    else:
        raise ValueError("incompatible shape")


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
class TestTranspose(TestCaseWithSimulator):
    iterations = 20

    def test_transpose_const(self, layout: data.Layout):
        tr_layout, o_keys, i_keys = transpose_layout_with_keys(layout)
        for i in range(self.iterations):
            const = mk_const(layout)
            val = layout.const(const)
            tr_val = transpose(val)

            assert tr_val.shape() == tr_layout
            for o_key in o_keys:
                for i_key in i_keys:
                    assert val[o_key][i_key] == tr_val[i_key][o_key]

    def test_transpose_view(self, layout: data.Layout):
        dut = Module()
        sig = Signal(layout)
        tr_layout, o_keys, i_keys = transpose_layout_with_keys(layout)
        tr_sig = Signal(tr_layout)
        dut.d.comb += tr_sig.eq(transpose(sig))

        assert layout_keys(layout) == o_keys
        assert layout_keys(layout[o_keys[0]].shape) == i_keys  # type: ignore
        assert layout_keys(tr_layout) == i_keys
        assert layout_keys(tr_layout[i_keys[0]].shape) == o_keys  # type: ignore

        async def testbench(ctx: TestbenchContext):
            for i in range(self.iterations):
                const = mk_const(layout)
                ctx.set(sig, const)
                tr_const = ctx.get(tr_sig)
                for o_key in o_keys:
                    for i_key in i_keys:
                        assert const[o_key][i_key] == tr_const[i_key][o_key]

        with self.run_simulation(dut) as sim:
            sim.add_testbench(testbench)
