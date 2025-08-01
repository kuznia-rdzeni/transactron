from amaranth_types.types import ShapeLike
import pytest
from amaranth import *
from amaranth.lib import data

from transactron.lib import BasicFifo
from transactron.lib.fifo import WideFifo
from transactron.utils.amaranth_ext import const_of

from transactron.testing import TestCaseWithSimulator, data_layout, TestbenchContext, SimpleTestCircuit
from collections import deque
import random


class TestBasicFifo(TestCaseWithSimulator):
    @pytest.mark.parametrize("depth", [5, 4])
    def test_randomized(self, depth):
        width = 8
        layout = data_layout(width)
        fifoc = SimpleTestCircuit(BasicFifo(layout=layout, depth=depth))
        expq = deque()

        cycles = 256
        random.seed(42)

        self.done = False

        async def source(sim: TestbenchContext):
            for _ in range(cycles):
                await self.random_wait_geom(sim, 0.5)

                v = random.randrange(0, 2**width)
                await fifoc.write.call(sim, data=v)
                await sim.delay(2e-9)
                expq.appendleft(v)

            self.done = True

        async def target(sim: TestbenchContext):
            while not self.done or expq:
                await self.random_wait_geom(sim, 0.5)

                v = await fifoc.read.call_try(sim)
                await sim.delay(1e-9)

                if v is not None:
                    assert v.data == expq.pop()

        async def peek(sim: TestbenchContext):
            while not self.done or expq:
                v = await fifoc.peek.call_try(sim)

                if v is not None:
                    assert v.data == expq[-1]
                else:
                    assert not expq

        async def clear(sim: TestbenchContext):
            while not self.done:
                await self.random_wait_geom(sim, 0.03)

                await fifoc.clear.call(sim)
                await sim.delay(3e-9)
                expq.clear()

        with self.run_simulation(fifoc) as sim:
            sim.add_testbench(source)
            sim.add_testbench(target)
            sim.add_testbench(peek)
            sim.add_testbench(clear)


class TestWideFifo(TestCaseWithSimulator):
    async def source(self, sim: TestbenchContext):
        cycles = 100

        for _ in range(cycles):
            await self.random_wait_geom(sim, 0.5)
            count = random.randint(1, self.write_width)
            data = [const_of(random.randrange(2**self.bits), self.shape) for _ in range(self.write_width)]
            await self.circ.write.call(sim, count=count, data=data)
            await sim.delay(2e-9)  # Ensures following code runs after peek_verifier and target
            self.expq.extend(data[:count])

        self.done = True

    async def target(self, sim: TestbenchContext):
        while not self.done or self.expq:
            await self.random_wait_geom(sim, 0.5)
            count = random.randint(1, self.read_width)
            v = await self.circ.read.call_try(sim, count=count)
            await sim.delay(1e-9)  # Ensures following code runs after peek_verifier
            if v is not None:
                assert v.count == min(count, len(self.expq))
                assert v.data[: v.count] == [self.expq.popleft() for _ in range(v.count)]

    async def peek_verifier(self, sim: TestbenchContext):
        while not self.done or self.expq:
            v = await self.circ.peek.call_try(sim)
            if v is not None:
                assert v.count == min(self.read_width, len(self.expq))
                assert v.data[: v.count] == [self.expq[i] for i in range(v.count)]
            else:
                assert not self.expq

    @pytest.mark.parametrize("shape", [4, data.ArrayLayout(2, 2)])
    @pytest.mark.parametrize("depth", [2, 5])
    @pytest.mark.parametrize("read_width, write_width", [(1, 1), (2, 2), (1, 3), (3, 1)])
    def test_randomized(self, shape: ShapeLike, depth: int, read_width: int, write_width: int):
        random.seed(42)

        self.shape = shape
        self.bits = Shape.cast(shape).width
        max_width = max(read_width, write_width)
        self.circ = SimpleTestCircuit(WideFifo(shape, depth * max_width, read_width, write_width))
        self.read_width = read_width
        self.write_width = write_width

        self.expq = deque()
        self.done = False

        with self.run_simulation(self.circ) as sim:
            sim.add_testbench(self.source)
            sim.add_testbench(self.target)
            sim.add_testbench(self.peek_verifier)
