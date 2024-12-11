from amaranth_types.types import ShapeLike
import pytest
from amaranth import *
from amaranth.lib import data

from transactron.lib import AdapterTrans, BasicFifo
from transactron.lib.fifo import WideFifo
from transactron.utils.amaranth_ext import const_of

from transactron.testing import TestCaseWithSimulator, TestbenchIO, data_layout, TestbenchContext
from collections import deque
import random

from transactron.testing.infrastructure import SimpleTestCircuit


class BasicFifoTestCircuit(Elaboratable):
    def __init__(self, depth):
        self.depth = depth

    def elaborate(self, platform):
        m = Module()

        m.submodules.fifo = self.fifo = BasicFifo(layout=data_layout(8), depth=self.depth)

        m.submodules.fifo_read = self.fifo_read = TestbenchIO(AdapterTrans(self.fifo.read))
        m.submodules.fifo_write = self.fifo_write = TestbenchIO(AdapterTrans(self.fifo.write))
        m.submodules.fifo_clear = self.fifo_clear = TestbenchIO(AdapterTrans(self.fifo.clear))

        return m


class TestBasicFifo(TestCaseWithSimulator):
    @pytest.mark.parametrize("depth", [5, 4])
    def test_randomized(self, depth):
        fifoc = BasicFifoTestCircuit(depth=depth)
        expq = deque()

        cycles = 256
        random.seed(42)

        self.done = False

        async def source(sim: TestbenchContext):
            for _ in range(cycles):
                await self.random_wait_geom(sim, 0.5)

                v = random.randint(0, (2**fifoc.fifo.width) - 1)
                expq.appendleft(v)
                await fifoc.fifo_write.call(sim, data=v)

                if random.random() < 0.005:
                    await fifoc.fifo_clear.call(sim)
                    await sim.delay(1e-9)
                    expq.clear()

            self.done = True

        async def target(sim: TestbenchContext):
            while not self.done or expq:
                await self.random_wait_geom(sim, 0.5)

                v = await fifoc.fifo_read.call_try(sim)

                if v is not None:
                    assert v.data == expq.pop()

        with self.run_simulation(fifoc) as sim:
            sim.add_testbench(source)
            sim.add_testbench(target)


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
        self.circ = SimpleTestCircuit(WideFifo(shape, depth, read_width, write_width))
        self.read_width = read_width
        self.write_width = write_width

        self.expq = deque()
        self.done = False

        with self.run_simulation(self.circ) as sim:
            sim.add_testbench(self.source)
            sim.add_testbench(self.target)
            sim.add_testbench(self.peek_verifier)
