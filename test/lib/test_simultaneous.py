from typing import Optional
import pytest
from itertools import product

from amaranth import *
from transactron import *
from transactron.lib.adapters import Adapter
from transactron.lib.simultaneous import *
from transactron.testing.method_mock import MethodMock
from transactron.utils import ModuleConnector
from transactron.testing import (
    SimpleTestCircuit,
    TestCaseWithSimulator,
    def_method_mock,
    TestbenchIO,
    TestbenchContext,
)


class ConditionTestCircuit(Elaboratable):
    def __init__(self, target: Method, *, nonblocking: bool, priority: bool, catchall: bool):
        self.target = target
        self.source = Method(i=[("cond1", 1), ("cond2", 1), ("cond3", 1)])
        self.nonblocking = nonblocking
        self.priority = priority
        self.catchall = catchall

    def elaborate(self, platform):
        m = TModule()

        @def_method(m, self.source, single_caller=True)
        def _(cond1, cond2, cond3):
            with condition(m, nonblocking=self.nonblocking, priority=self.priority) as branch:
                with branch(cond1):
                    self.target(m, cond=1)
                with branch(cond2):
                    self.target(m, cond=2)
                with branch(cond3):
                    self.target(m, cond=3)
                if self.catchall:
                    with branch():
                        self.target(m, cond=0)

        return m


class TestCondition(TestCaseWithSimulator):
    @pytest.mark.parametrize("nonblocking", [False, True])
    @pytest.mark.parametrize("priority", [False, True])
    @pytest.mark.parametrize("catchall", [False, True])
    def test_condition(self, nonblocking: bool, priority: bool, catchall: bool):
        target = TestbenchIO(Adapter.create(i=[("cond", 2)]))

        circ = SimpleTestCircuit(
            ConditionTestCircuit(target.adapter.iface, nonblocking=nonblocking, priority=priority, catchall=catchall),
        )
        m = ModuleConnector(test_circuit=circ, target=target)

        selection: Optional[int]

        @def_method_mock(lambda: target)
        def target_process(cond):
            @MethodMock.effect
            def eff():
                nonlocal selection
                selection = cond

        async def process(sim: TestbenchContext):
            nonlocal selection
            await sim.tick()  # TODO workaround for mocks inactive in first cycle
            for c1, c2, c3 in product([0, 1], [0, 1], [0, 1]):
                selection = None
                res = await circ.source.call_try(sim, cond1=c1, cond2=c2, cond3=c3)

                if catchall or nonblocking:
                    assert res is not None

                if res is None:
                    assert selection is None
                    assert not catchall or nonblocking
                    assert (c1, c2, c3) == (0, 0, 0)
                elif selection is None:
                    assert nonblocking
                    assert (c1, c2, c3) == (0, 0, 0)
                elif priority:
                    assert selection == c1 + 2 * c2 * (1 - c1) + 3 * c3 * (1 - c2) * (1 - c1)
                else:
                    assert selection in [c1, 2 * c2, 3 * c3]

        with self.run_simulation(m) as sim:
            sim.add_testbench(process)
