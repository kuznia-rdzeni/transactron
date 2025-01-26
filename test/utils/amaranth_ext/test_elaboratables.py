import pytest
import random

from transactron.utils import StableSelectingNetwork
from transactron.testing import TestCaseWithSimulator, TestbenchContext


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
