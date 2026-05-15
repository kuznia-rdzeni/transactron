import os
import random
import math
import functools
from contextlib import nullcontext
from collections.abc import Callable
from typing import Any, cast
from amaranth import *
from amaranth.sim import *
from amaranth.sim._async import SimulatorContext
from amaranth_types import HasElaborate

from transactron.utils.dependencies import DependencyContext
from .method_mock import MethodMock
from transactron.core.context import TransactronContextElaboratable
from transactron.utils import auto_debug_signals, HasDebugSignals


__all__ = ["PysimSimulator", "tick", "random_wait", "random_wait_geom"]


class _TestModule(Elaboratable):
    def __init__(self, tested_module: HasElaborate, add_transaction_module: bool):
        self.tested_module = (
            TransactronContextElaboratable(tested_module, dependency_manager=DependencyContext.get())
            if add_transaction_module
            else tested_module
        )
        self.add_transaction_module = add_transaction_module

    def elaborate(self, platform) -> HasElaborate:
        m = Module()

        # so that Amaranth allows us to use add_clock
        _dummy = Signal()
        m.d.sync += _dummy.eq(1)

        m.submodules.tested_module = self.tested_module

        m.domains.sync_neg = ClockDomain(clk_edge="neg", local=True)

        return m


class PysimSimulator(Simulator):
    def __init__(
        self,
        module: HasElaborate,
        max_cycles: float = 10e4,
        add_transaction_module=True,
        traces_file=None,
        clk_period=1e-6,
    ):
        test_module = _TestModule(module, add_transaction_module)
        self.tested_module = tested_module = test_module.tested_module
        super().__init__(test_module)

        self.add_clock(clk_period)
        self.add_clock(clk_period, domain="sync_neg")

        if isinstance(tested_module, HasDebugSignals):
            extra_signals = tested_module.debug_signals
        else:
            extra_signals = functools.partial(auto_debug_signals, tested_module)

        if traces_file:
            traces_dir = "test/__traces__"
            os.makedirs(traces_dir, exist_ok=True)
            # Signal handling is hacky and accesses Simulator internals.
            # TODO: try to merge with Amaranth.
            if isinstance(extra_signals, Callable):
                extra_signals = extra_signals()
            clocks = [d.clk for d in cast(Any, self)._design.fragment.domains.values()]

            self.ctx = self.write_vcd(
                f"{traces_dir}/{traces_file}.vcd",
                f"{traces_dir}/{traces_file}.gtkw",
                traces=[clocks, extra_signals],
            )
        else:
            self.ctx = nullcontext()

        async def timeout_testbench(sim: SimulatorContext):
            await sim.delay(clk_period * max_cycles)
            assert False, "simulation timed out"

        self.add_testbench(timeout_testbench, background=True)

    def run(self) -> None:
        with self.ctx:
            super().run()

    def add_mock(self, val: MethodMock):
        self.add_process(val.output_process)
        if val.validate_arguments is not None:
            self.add_process(val.validate_arguments_process)
        self.add_testbench(val.effect_process, background=True)


async def tick(ctx: SimulatorContext, cycle_cnt: int = 1):
    """
    Waits for the given number of cycles.
    """
    for _ in range(cycle_cnt):
        await ctx.tick()


async def random_wait(ctx: SimulatorContext, max_cycle_cnt: int, *, min_cycle_cnt: int = 0):
    """
    Wait for a random amount of cycles in range [min_cycle_cnt, max_cycle_cnt]
    """
    await tick(ctx, random.randrange(min_cycle_cnt, max_cycle_cnt + 1))


async def random_wait_geom(ctx: SimulatorContext, prob: float = 0.5, max_cycle_cnt: int = 2**16):
    """
    Wait till the first success, where there is `prob` probability for success in each cycle.
    """
    assert prob > 0 and prob <= 1
    if prob == 1:
        cycle_cnt = 0
    else:
        cycle_cnt = min(max_cycle_cnt, math.floor(math.log(1 - random.random()) / math.log(1 - prob)))
    await tick(ctx, cycle_cnt)
