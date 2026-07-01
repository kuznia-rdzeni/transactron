from itertools import count
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
    @pytest.mark.parametrize("alloc_ways", [1, 3, 4])
    @pytest.mark.parametrize("free_ways", [1, 3, 4])
    @pytest.mark.parametrize("init", [-1, 0])
    def test_allocator(self, entries: int, alloc_ways: int, free_ways: int, init: int):
        dut = SimpleTestCircuit(PriorityEncoderAllocator(entries, alloc_ways, free_ways, init=init))

        iterations = 8 * entries

        init_allocated = tuple(i for i in range(entries) if not init & (1 << i))
        init_free = tuple(i for i in range(entries) if init & (1 << i))

        allocated = set(init_allocated)
        free = set(init_free)
        selected = set()

        clearing = False

        init_allocated_count = len(allocated)
        total = iterations * alloc_ways + init_allocated_count

        async def selected_reset(sim: TestbenchContext):
            while True:
                selected.clear()
                await sim.tick()

        def make_allocator(i: int):
            async def process(sim: TestbenchContext):
                nonlocal total
                for _ in range(iterations):
                    val = (await dut.alloc[i].call(sim)).ident
                    if not clearing:
                        assert val in free
                        free.remove(val)
                        allocated.add(val)
                    else:
                        total -= 1
                    await self.random_wait_geom(sim, 0.5)

            return process

        def make_deallocator(i: int):
            async def process(sim: TestbenchContext):
                nonlocal total
                while True:
                    await sim.delay(1e-12)  # to ensure that alloc/free were modified fine
                    if total <= 0:
                        return
                    while not (set(allocated) - selected):
                        if total <= 0:
                            return
                        await sim.tick()
                        await sim.delay(1e-12)
                    val = random.choice(list(allocated - selected))
                    selected.add(val)
                    await dut.free[i].call(sim, ident=val)
                    if not clearing:
                        free.add(val)
                        allocated.remove(val)
                        total -= 1
                    await self.random_wait_geom(sim, 0.3)

            return process

        async def clearer(sim: TestbenchContext):
            nonlocal clearing, total
            while True:
                await self.random_wait_geom(sim, 0.05)
                await dut.clear.call(sim)
                clearing = True
                total += len(init_allocated) - len(allocated)
                allocated.clear()
                allocated.update(init_allocated)
                free.clear()
                free.update(init_free)
                await sim.delay(1e-12)
                clearing = False

        with self.run_simulation(dut) as sim:
            sim.add_testbench(selected_reset, background=True)
            sim.add_testbench(clearer, background=True)
            for i in range(free_ways):
                sim.add_testbench(make_deallocator(i))
            for i in range(alloc_ways):
                sim.add_testbench(make_allocator(i))


class TestPreservedOrderAllocator(TestCaseWithSimulator):
    @pytest.mark.parametrize("entries", [5, 8])
    def test_allocator(self, entries: int):
        dut = SimpleTestCircuit(PreservedOrderAllocator(entries))

        iterations = 20 * entries
        total = iterations

        allocated: list[int] = []
        free: list[int] = list(range(entries))
        clearing = False

        async def allocator(sim: TestbenchContext):
            nonlocal total
            for _ in range(iterations):
                val = (await dut.alloc.call(sim)).ident
                if not clearing:
                    free.remove(val)
                    allocated.append(val)
                else:
                    total -= 1  # allocation was made but erased
                await self.random_wait_geom(sim, 0.5)

        async def deallocator(sim: TestbenchContext):
            nonlocal total
            for i in count():
                while not allocated:
                    assert i <= total
                    if i == total:
                        return
                    await sim.tick()
                idx = random.randrange(len(allocated))
                val = allocated[idx]
                if random.randint(0, 1):
                    await dut.free.call(sim, ident=val)
                else:
                    await dut.free_idx.call(sim, idx=idx)
                if not clearing:
                    free.append(val)
                    allocated.pop(idx)
                else:
                    total += 1  # deallocation was made but erased
                await self.random_wait_geom(sim, 0.4)

        async def clearer(sim: TestbenchContext):
            nonlocal clearing, total
            while True:
                await self.random_wait_geom(sim, 0.05)
                await dut.clear.call(sim)
                clearing = True
                total -= len(allocated)
                allocated.clear()
                free.clear()
                free.extend(range(entries))
                await sim.delay(1e-12)
                clearing = False

        async def order_verifier(sim: TestbenchContext):
            while True:
                val = await dut.order.call(sim)
                assert val.used == len(allocated)
                assert val.order == allocated + free

        with self.run_simulation(dut) as sim:
            sim.add_testbench(order_verifier, background=True)
            sim.add_testbench(clearer, background=True)
            sim.add_testbench(deallocator)
            sim.add_testbench(allocator)


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
