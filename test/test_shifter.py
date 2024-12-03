import random
import pytest
from collections.abc import Callable, Iterable, Sequence
from typing import Any, cast
from amaranth import *
from amaranth.lib import data
from amaranth_types.types import ShapeCastable, ShapeLike, ValueLike
from transactron.utils import assign
from transactron.utils.amaranth_ext.shifter import *
from transactron.testing import TestCaseWithSimulator, TestbenchContext


class ShifterCircuit(Elaboratable):
    def __init__(
        self,
        shift_fun: Callable[[ValueLike, ValueLike], Value],
        width: int,
        shift_kwargs: Iterable[tuple[str, Any]] = (),
    ):
        self.input = Signal(width)
        self.output = Signal(width)
        self.offset = Signal(range(width + 1))
        self.shift_fun = shift_fun
        self.kwargs = dict(shift_kwargs)

    def elaborate(self, platform):
        m = Module()

        m.d.comb += self.output.eq(self.shift_fun(self.input, self.offset, **self.kwargs))

        return m


class TestShifter(TestCaseWithSimulator):
    @pytest.mark.parametrize(
        "shift_fun, shift_kwargs, test_fun",
        [
            (shift_left, [], lambda val, offset, width: (val << offset) % 2**width),
            (shift_right, [], lambda val, offset, width: (val >> offset)),
            (
                shift_left,
                [("placeholder", 1)],
                lambda val, offset, width: ((val << offset) | (2**width - 1 >> (width - offset))) % 2**width,
            ),
            (
                shift_right,
                [("placeholder", 1)],
                lambda val, offset, width: ((val >> offset) | (2**width - 1 << (width - offset))) % 2**width,
            ),
            (rotate_left, [], lambda val, offset, width: ((val << offset) | (val >> (width - offset))) % 2**width),
            (rotate_right, [], lambda val, offset, width: ((val >> offset) | (val << (width - offset))) % 2**width),
        ],
    )
    def test_shifter(self, shift_fun, shift_kwargs, test_fun):
        width = 8
        tests = 50
        dut = ShifterCircuit(shift_fun, width, shift_kwargs)

        async def test_process(sim: TestbenchContext):
            for _ in range(tests):
                val = random.randrange(2**width)
                offset = random.randrange(width + 1)
                sim.set(dut.input, val)
                sim.set(dut.offset, offset)
                _, result = await sim.delay(1e-9).sample(dut.output)
                assert result == test_fun(val, offset, width)

        with self.run_simulation(dut, add_transaction_module=False) as sim:
            sim.add_testbench(test_process)


class VecShifterCircuit(Elaboratable):
    def __init__(
        self,
        shift_fun: Callable[[Sequence, ValueLike], Sequence],
        shape: ShapeLike,
        width: int,
        shift_kwargs: Iterable[tuple[str, Any]] = (),
    ):
        self.input = Signal(data.ArrayLayout(shape, width))
        self.output = Signal(data.ArrayLayout(shape, width))
        self.offset = Signal(range(width + 1))
        self.shift_fun = shift_fun
        self.kwargs = dict(shift_kwargs)

    def elaborate(self, platform):
        m = Module()

        m.d.comb += assign(self.output, self.shift_fun(cast(Sequence, self.input), self.offset, **self.kwargs))

        return m


class TestVecShifter(TestCaseWithSimulator):
    @pytest.mark.parametrize(
        "shape",
        [
            4,
            data.ArrayLayout(2, 2),
        ],
    )
    @pytest.mark.parametrize(
        "shift_fun, shift_kwargs, test_fun",
        [
            (shift_vec_left, lambda mkc: [], lambda val, offset, mkc: [mkc(0)] * offset + val[: len(val) - offset]),
            (shift_vec_right, lambda mkc: [], lambda val, offset, mkc: val[offset:] + [mkc(0)] * offset),
            (
                shift_vec_left,
                lambda mkc: [("placeholder", mkc(0))],
                lambda val, offset, mkc: [mkc(0)] * offset + val[: len(val) - offset],
            ),
            (
                shift_vec_right,
                lambda mkc: [("placeholder", mkc(0))],
                lambda val, offset, mkc: val[offset:] + [mkc(0)] * offset,
            ),
            (
                rotate_vec_left,
                lambda mkc: [],
                lambda val, offset, mkc: val[len(val) - offset :] + val[: len(val) - offset],
            ),
            (rotate_vec_right, lambda mkc: [], lambda val, offset, mkc: val[offset:] + val[:offset]),
        ],
    )
    def test_vec_shifter(self, shape, shift_fun, shift_kwargs, test_fun):
        def mk_const(x):
            if isinstance(shape, ShapeCastable):
                return shape.from_bits(0)
            else:
                return C(x, shape)

        width = 8
        tests = 50
        dut = VecShifterCircuit(shift_fun, shape, width, shift_kwargs(mk_const))

        async def test_process(sim: TestbenchContext):
            for _ in range(tests):
                val = [mk_const(random.randrange(2 ** Shape.cast(shape).width)) for _ in range(width)]
                offset = random.randrange(width + 1)
                sim.set(dut.input, val)
                sim.set(dut.offset, offset)
                _, result = await sim.delay(1e-9).sample(dut.output)
                assert result == test_fun(val, offset, mk_const)

        with self.run_simulation(dut, add_transaction_module=False) as sim:
            sim.add_testbench(test_process)
