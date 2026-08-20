import pytest
from transactron.lib.stack import Stack
from transactron.testing import TestCaseWithSimulator, data_layout, TestbenchContext, SimpleTestCircuit
from transactron.testing.input_generation import draw_wait_geom, shrinkable_constants
from hypothesis import given, settings
import hypothesis.strategies as st


class TestStack(TestCaseWithSimulator):
    @pytest.mark.parametrize("depth", [5, 4])
    @settings(max_examples=10)
    @given(st.data())
    def test_randomized(self, depth, data: st.DataObject):
        width = 8
        layout = data_layout(width)
        circ = SimpleTestCircuit(Stack(layout=layout, depth=depth))
        stk: list[int] = []

        cycles = data.draw(shrinkable_constants(100), label="cycles")

        self.done = False

        async def source(sim: TestbenchContext):
            for _ in range(cycles):
                await draw_wait_geom(sim, data, 0.5)

                v = data.draw(st.integers(0, 2**width - 1), label="val")
                await circ.write.call(sim, data=v)
                await sim.delay(2e-9)
                stk.append(v)

            self.done = True

        async def target(sim: TestbenchContext):
            while not self.done or stk:
                await draw_wait_geom(sim, data, 0.5)

                v = await circ.read.call_try(sim)
                await sim.delay(1e-9)

                if v is not None:
                    assert v.data == stk.pop()
                else:
                    assert not stk

        async def peek(sim: TestbenchContext):
            while not self.done or stk:
                v = await circ.peek.call_try(sim)

                if v is not None:
                    assert v.data == stk[-1]
                else:
                    assert not stk

        async def clear(sim: TestbenchContext):
            while not self.done:
                await draw_wait_geom(sim, data, 0.03)

                await circ.clear.call(sim)
                await sim.delay(3e-9)
                stk.clear()

        with self.run_simulation(circ) as sim:
            sim.add_testbench(source)
            sim.add_testbench(target)
            sim.add_testbench(peek)
            sim.add_testbench(clear)
