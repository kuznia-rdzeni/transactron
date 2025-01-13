import pytest
import random

from amaranth import *
from transactron import *
from transactron.lib.allocators import *
from transactron.lib.allocators import PreservedOrderAllocator
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


class TestPreservedOrderAllocator(TestCaseWithSimulator):
    @pytest.mark.parametrize("entries", [5, 8])
    def test_allocator(self, entries: int):
        dut = SimpleTestCircuit(PreservedOrderAllocator(entries))

        iterations = 5 * entries

        allocated: list[int] = []
        free: list[int] = list(range(entries))

        async def allocator(sim: TestbenchContext):
            for _ in range(iterations):
                val = (await dut.alloc.call(sim)).ident
                sim.delay(1e-9)  # Runs after deallocator
                free.remove(val)
                allocated.append(val)
                await self.random_wait_geom(sim, 0.5)

        async def deallocator(sim: TestbenchContext):
            for _ in range(iterations):
                while not allocated:
                    await sim.tick()
                idx = random.randrange(len(allocated))
                val = allocated[idx]
                if random.randint(0, 1):
                    await dut.free.call(sim, ident=val)
                else:
                    await dut.free_idx.call(sim, idx=idx)
                free.append(val)
                allocated.pop(idx)
                await self.random_wait_geom(sim, 0.4)

        async def order_verifier(sim: TestbenchContext):
            while True:
                val = await dut.order.call(sim)
                sim.delay(2e-9)  # Runs after allocator and deallocator
                assert val.used == len(allocated)
                assert val.order == allocated + free

        with self.run_simulation(dut) as sim:
            sim.add_testbench(order_verifier, background=True)
            sim.add_testbench(allocator)
            sim.add_testbench(deallocator)
