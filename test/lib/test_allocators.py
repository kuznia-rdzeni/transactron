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


class TestCircularAllocator(TestCaseWithSimulator):
    @pytest.mark.parametrize("entries", [5, 8])
    @pytest.mark.parametrize("max_alloc", [1, 3])
    @pytest.mark.parametrize("max_free", [1, 3])
    @pytest.mark.parametrize("with_validate_arguments", [False, True])
    def test_allocator(self, entries: int, max_alloc: int, max_free: int, with_validate_arguments: bool):
        m = CircularAllocator(entries, max_alloc, max_free, with_validate_arguments=with_validate_arguments)
        dut = SimpleTestCircuit(m)

        iterations = 5 * entries

        start_idx = end_idx = allocated = 0

        async def allocator(sim: TestbenchContext):
            nonlocal allocated, end_idx
            while True:
                curr_max_alloc = max_alloc if with_validate_arguments else min(entries - allocated, max_alloc)
                count = random.randrange(curr_max_alloc + 1)
                ret = await dut.alloc.call_try(sim, count=count)
                if ret is None:
                    assert (with_validate_arguments and count > entries - allocated) or allocated == entries
                    count = 1
                    ret = await dut.alloc.call(sim, count=count)
                for i in range(max_alloc):
                    assert ret.idents[i] == (end_idx + i) % entries
                await sim.delay(1e-12)
                allocated = allocated + count
                end_idx = (end_idx + count) % entries
                assert ret.new_end_idx == end_idx
                await sim.delay(1e-12)
                await self.random_wait_geom(sim)

        async def deallocator(sim: TestbenchContext):
            nonlocal allocated, start_idx
            for _ in range(iterations):
                curr_max_free = max_free if with_validate_arguments else min(allocated, max_free)
                count = random.randrange(curr_max_free + 1)
                ret = await dut.free.call_try(sim, count=count)
                if ret is None:
                    assert (with_validate_arguments and count > allocated) or allocated == 0
                    count = 1
                    ret = await dut.free.call(sim, count=count)
                for i in range(max_free):
                    assert ret.idents[i] == (start_idx + i) % entries
                await sim.delay(1e-12)
                allocated = allocated - count
                start_idx = (start_idx + count) % entries
                assert ret.new_start_idx == start_idx
                await sim.delay(1e-12)
                await self.random_wait_geom(sim)

        async def verifier(sim: TestbenchContext):
            while True:
                await sim.delay(2e-12)
                assert allocated == sim.get(m.allocated)
                assert start_idx == sim.get(m.start_idx)
                assert end_idx == sim.get(m.end_idx)
                await sim.tick()

        with self.run_simulation(dut) as sim:
            sim.add_testbench(allocator, background=True)
            sim.add_testbench(deallocator)
            sim.add_testbench(verifier, background=True)
