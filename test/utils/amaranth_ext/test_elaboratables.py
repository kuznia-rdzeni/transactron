import pytest
import random

from transactron.utils import OneHotRoundRobin, StableSelectingNetwork
from transactron.testing import TestCaseWithSimulator, TestbenchContext


class TestScheduler(TestCaseWithSimulator):
    def count_test(self, sched, cnt):
        assert sched.count == cnt
        assert len(sched.requests) == cnt
        assert len(sched.grant) == cnt
        assert len(sched.valid) == 1

    async def sim_step(self, sim, sched: OneHotRoundRobin, request: int, expected_grant: int):
        sim.set(sched.requests, request)
        _, _, valid, grant = await sim.tick().sample(sched.valid, sched.grant)

        if request == 0:
            assert not valid
        else:
            assert grant == expected_grant
            assert valid

    def test_single(self):
        sched = OneHotRoundRobin(1)
        self.count_test(sched, 1)

        async def process(sim):
            await self.sim_step(sim, sched, 0, 0)
            await self.sim_step(sim, sched, 1, 1)
            await self.sim_step(sim, sched, 1, 1)
            await self.sim_step(sim, sched, 0, 0)

        with self.run_simulation(sched) as sim:
            sim.add_testbench(process)

    def test_multi(self):
        sched = OneHotRoundRobin(4)
        self.count_test(sched, 4)

        async def process(sim):
            await self.sim_step(sim, sched, 0b0000, 0b0000)
            await self.sim_step(sim, sched, 0b1010, 0b0010)
            await self.sim_step(sim, sched, 0b1010, 0b1000)
            await self.sim_step(sim, sched, 0b1010, 0b0010)
            await self.sim_step(sim, sched, 0b1001, 0b1000)
            await self.sim_step(sim, sched, 0b1001, 0b0001)

            await self.sim_step(sim, sched, 0b1111, 0b0010)
            await self.sim_step(sim, sched, 0b1111, 0b0100)
            await self.sim_step(sim, sched, 0b1111, 0b1000)
            await self.sim_step(sim, sched, 0b1111, 0b0001)

            await self.sim_step(sim, sched, 0b0000, 0b0000)
            await self.sim_step(sim, sched, 0b0010, 0b0010)
            await self.sim_step(sim, sched, 0b0010, 0b0010)

        with self.run_simulation(sched) as sim:
            sim.add_testbench(process)


class TestStableSelectingNetwork(TestCaseWithSimulator):

    @pytest.mark.parametrize("n", [2, 3, 7, 8])
    def test(self, n: int):
        m = StableSelectingNetwork(n, 8)

        random.seed(42)

        async def process(sim: TestbenchContext):
            for _ in range(100):
                inputs = [random.randrange(2**8) for _ in range(n)]
                valids = [random.randrange(2) for _ in range(n)]
                total = sum(valids)

                expected_output_prefix = []
                for i in range(n):
                    sim.set(m.valids[i], valids[i])
                    sim.set(m.inputs[i], inputs[i])

                    if valids[i]:
                        expected_output_prefix.append(inputs[i])

                for i in range(total):
                    out = sim.get(m.outputs[i])
                    assert out == expected_output_prefix[i]

                assert sim.get(m.output_cnt) == total
                await sim.tick()

        with self.run_simulation(m) as sim:
            sim.add_testbench(process)
