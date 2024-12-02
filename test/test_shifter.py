import random
import pytest
from collections.abc import Callable, Iterable
from typing import Any
from amaranth import *
from amaranth_types.types import ValueLike
from transactron.utils.amaranth_ext.shifter import *
from transactron.testing import TestCaseWithSimulator, TestbenchContext


class ShifterCircuit(Elaboratable):
    def __init__(
        self, shift_fun: Callable[[ValueLike, ValueLike], Value], width, shift_kwargs: Iterable[tuple[str, Any]] = ()
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
