import pytest
import random
from typing import TypeAlias
from collections import defaultdict

from amaranth import *
from transactron import *
from transactron.utils.typing import MethodLayout
from transactron.lib.connectors import *
from transactron.testing.testbenchio import CallTrigger
from transactron.testing import (
    SimpleTestCircuit,
    TestCaseWithSimulator,
    data_layout,
    TestbenchContext,
)


class RevConnect(Elaboratable):
    def __init__(self, layout: MethodLayout):
        self.connect = Connect(rev_layout=layout)
        self.read = self.connect.write
        self.write = self.connect.read

    def elaborate(self, platform):
        return self.connect


FIFO_Like: TypeAlias = FIFO | Forwarder | Connect | RevConnect | Pipe


class TestFifoBase(TestCaseWithSimulator):
    def do_test_fifo(
        self, fifo_class: type[FIFO_Like], writer_rand: int = 0, reader_rand: int = 0, fifo_kwargs: dict = {}
    ):
        iosize = 8

        m = SimpleTestCircuit(fifo_class(data_layout(iosize), **fifo_kwargs))

        random.seed(1337)

        async def writer(sim: TestbenchContext):
            for i in range(2**iosize):
                await m.write.call(sim, data=i)
                await self.random_wait(sim, writer_rand)

        async def reader(sim: TestbenchContext):
            for i in range(2**iosize):
                assert (await m.read.call(sim)).data == i
                await self.random_wait(sim, reader_rand)

        with self.run_simulation(m) as sim:
            sim.add_testbench(reader)
            sim.add_testbench(writer)


class TestFIFO(TestFifoBase):
    @pytest.mark.parametrize("writer_rand, reader_rand", [(0, 0), (2, 0), (0, 2), (1, 1)])
    def test_fifo(self, writer_rand, reader_rand):
        self.do_test_fifo(FIFO, writer_rand=writer_rand, reader_rand=reader_rand, fifo_kwargs=dict(depth=4))


class TestConnect(TestFifoBase):
    @pytest.mark.parametrize("writer_rand, reader_rand", [(0, 0), (2, 0), (0, 2), (1, 1)])
    def test_fifo(self, writer_rand, reader_rand):
        self.do_test_fifo(Connect, writer_rand=writer_rand, reader_rand=reader_rand)

    @pytest.mark.parametrize("writer_rand, reader_rand", [(0, 0), (2, 0), (0, 2), (1, 1)])
    def test_rev_fifo(self, writer_rand, reader_rand):
        self.do_test_fifo(RevConnect, writer_rand=writer_rand, reader_rand=reader_rand)


class TestForwarder(TestFifoBase):
    @pytest.mark.parametrize("writer_rand, reader_rand", [(0, 0), (2, 0), (0, 2), (1, 1)])
    def test_fifo(self, writer_rand, reader_rand):
        self.do_test_fifo(Forwarder, writer_rand=writer_rand, reader_rand=reader_rand)

    def test_forwarding(self):
        iosize = 8

        m = SimpleTestCircuit(Forwarder(data_layout(iosize)))

        async def forward_check(sim: TestbenchContext, x: int):
            read_res, write_res = await CallTrigger(sim).call(m.read).call(m.write, data=x)
            assert read_res is not None and read_res.data == x
            assert write_res is not None

        async def process(sim: TestbenchContext):
            # test forwarding behavior
            for x in range(4):
                await forward_check(sim, x)

            # load the overflow buffer
            res = await m.write.call_try(sim, data=42)
            assert res is not None

            # writes are not possible now
            res = await m.write.call_try(sim, data=42)
            assert res is None

            # read from the overflow buffer, writes still blocked
            read_res, write_res = await CallTrigger(sim).call(m.read).call(m.write, data=111)
            assert read_res is not None and read_res.data == 42
            assert write_res is None

            # forwarding now works again
            for x in range(4):
                await forward_check(sim, x)

        with self.run_simulation(m) as sim:
            sim.add_testbench(process)


class TestPipe(TestFifoBase):
    @pytest.mark.parametrize("writer_rand, reader_rand", [(0, 0), (2, 0), (0, 2), (1, 1)])
    def test_fifo(self, writer_rand, reader_rand):
        self.do_test_fifo(Pipe, writer_rand=writer_rand, reader_rand=reader_rand)

    def test_pipelining(self):
        self.do_test_fifo(Pipe, writer_rand=0, reader_rand=0)


class TestCrossbarConnectTrans(TestCaseWithSimulator):
    def initialize(self):
        f1_size = 14
        f2_size = 3
        self.lay = [("field1", f1_size), ("field2", f2_size)]

        self.m = SimpleTestCircuit(CrossbarConnectTrans(self.count, 1, [], self.lay))
        random.seed(14)

        self.inputs = []
        # Create list with info if we processed all data from inputs
        self.producer_end = [False for i in range(self.count)]
        self.expected_output = defaultdict(int)
        self.max_wait = 4

        # Prepare random results for inputs
        for i in range(self.count):
            data = []
            input_size = random.randint(20, 30)
            for j in range(input_size):
                t = (
                    random.randrange(0, 2**f1_size),
                    random.randrange(0, 2**f2_size),
                )
                data.append(t)
                self.expected_output[t] += 1
            self.inputs.append(data)

    def generate_producer(self, i: int):
        async def producer(sim: TestbenchContext):
            inputs = self.inputs[i]
            for field1, field2 in inputs:
                await self.m.methods1[i].call(sim, field1=field1, field2=field2)
                await self.random_wait(sim, self.max_wait)
            self.producer_end[i] = True

        return producer

    async def consumer(self, sim: TestbenchContext):
        while not all(self.producer_end):
            result = await self.m.methods2[0].call(sim)

            t = (result.field1, result.field2)
            assert self.expected_output[t]
            self.expected_output[t] -= 1
            await self.random_wait(sim, self.max_wait)

    @pytest.mark.parametrize("count", [1, 4])
    def test(self, count: int):
        self.count = count
        self.initialize()
        with self.run_simulation(self.m) as sim:
            sim.add_testbench(self.consumer)
            for i in range(self.count):
                sim.add_testbench(self.generate_producer(i))
