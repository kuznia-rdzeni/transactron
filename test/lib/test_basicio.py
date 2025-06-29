import pytest
import random
from amaranth import *
from collections import deque
from transactron.testing import SimpleTestCircuit, TestCaseWithSimulator, ProcessContext, TestbenchContext, TestbenchIO
from transactron.lib.basicio import InputSampler, OutputBuffer
from transactron.utils.data_repr import data_layout


@pytest.mark.parametrize("edge", [False, True])
@pytest.mark.parametrize("polarity", [False, True])
@pytest.mark.parametrize("synchronize", [False, True])
class TestBasicIO(TestCaseWithSimulator):
    n_bits = 8
    n_tests = 100

    def mk_gen(self, q: deque[int], sig: Value, m: int):
        async def gen(sim: ProcessContext):
            while True:
                val = random.randrange(m)
                sim.set(sig, val)
                q.appendleft(val)
                q.pop()
                await sim.tick()

        return gen

    def mk_trigger_tb(self, method: TestbenchIO, edge: bool, polarity: bool, synchronize: bool):
        async def tb(sim: TestbenchContext):
            async for _, _, en in sim.tick().sample(method.adapter.iface.ready):
                last_triggers = list(self.last_triggers)[1 + synchronize :]
                if edge:
                    if polarity:  # rising edge
                        assert en == (last_triggers[0] and not last_triggers[1])
                    else:  # falling edge
                        assert en == (not last_triggers[0] and last_triggers[1])
                else:
                    if polarity:  # high level
                        assert en == last_triggers[0]
                    else:  # low level
                        assert en == (not last_triggers[0])

        return tb

    def test_inputsampler(self, edge: bool, polarity: bool, synchronize: bool):
        self.m = SimpleTestCircuit(
            InputSampler(data_layout(self.n_bits), edge=edge, polarity=polarity, synchronize=synchronize)
        )
        self.last_triggers = deque([0, 0, 0, 0])
        self.last_data = deque([0, 0, 0])

        async def tb(sim: TestbenchContext):
            for _ in range(self.n_tests):
                res = await self.m.get.call(sim)
                assert res.data == self.last_data[1 + synchronize]

        with self.run_simulation(self.m) as sim:
            sim.add_process(self.mk_gen(self.last_triggers, self.m._dut.trigger, 2))
            sim.add_process(self.mk_gen(self.last_data, self.m._dut.data.data, 2**self.n_bits))
            sim.add_testbench(self.mk_trigger_tb(self.m.get, edge, polarity, synchronize), background=True)
            sim.add_testbench(tb)

    def test_outputbuffer(self, edge: bool, polarity: bool, synchronize: bool):
        self.m = SimpleTestCircuit(
            OutputBuffer(data_layout(self.n_bits), edge=edge, polarity=polarity, synchronize=synchronize)
        )
        self.last_triggers = deque([0, 0, 0, 0])

        async def tb(sim: TestbenchContext):
            for _ in range(self.n_tests):
                data = random.randrange(2**self.n_bits)
                await self.m.put.call(sim, data=data)
                assert sim.get(self.m._dut.data).data == data

        with self.run_simulation(self.m) as sim:
            sim.add_process(self.mk_gen(self.last_triggers, self.m._dut.trigger, 2))
            sim.add_testbench(self.mk_trigger_tb(self.m.put, edge, polarity, synchronize), background=True)
            sim.add_testbench(tb)
