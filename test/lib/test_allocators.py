import pytest
import random

from amaranth import *
from transactron import *
from transactron.lib.allocators import *
from transactron.testing import (
    SimpleTestCircuit,
    TestCaseWithSimulator,
    TestbenchContext,
)


class TestPriorityEncoderAllocator(TestCaseWithSimulator):
    @pytest.mark.parametrize("entries", [5, 8])
    @pytest.mark.parametrize("ways", [1, 3, 4])
    @pytest.mark.parametrize("init", [-1, 0])
    def test_allocator(self, entries: int, ways: int, init: int):
        dut = SimpleTestCircuit(PriorityEncoderAllocator(entries, ways, init=init))

        iterations = 5 * entries

        allocated = [i for i in range(entries) if not init & (1 << i)]
        free = [i for i in range(entries) if init & (1 << i)]

        init_allocated_count = len(allocated)

        def make_allocator(i: int):
            async def process(sim: TestbenchContext):
                for _ in range(iterations):
                    val = (await dut.alloc[i].call(sim)).ident
                    assert val in free
                    free.remove(val)
                    allocated.append(val)
                    await self.random_wait_geom(sim, 0.5)

            return process

        def make_deallocator(i: int):
            async def process(sim: TestbenchContext):
                for _ in range(iterations + (init_allocated_count + i) // ways):
                    while not allocated:
                        await sim.tick()
                    val = allocated.pop(random.randrange(len(allocated)))
                    await dut.free[i].call(sim, ident=val)
                    free.append(val)
                    await self.random_wait_geom(sim, 0.3)

            return process

        with self.run_simulation(dut) as sim:
            for i in range(ways):
                sim.add_testbench(make_allocator(i))
                sim.add_testbench(make_deallocator(i))
