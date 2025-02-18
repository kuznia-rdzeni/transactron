from transactron.testing import *
from transactron.utils.amaranth_ext import min_value
import random


class MinValueCircuit(Elaboratable):
    def __init__(self, bits: int, num: int):
        self.inputs = [Signal(bits) for _ in range(num)]
        self.output = Signal(bits)

    def elaborate(self, platform):
        m = Module()

        m.d.comb += self.output.eq(min_value(m, self.inputs))

        return m


class TestMinValue(TestCaseWithSimulator):
    def test_min_value(self):
        bits = 4
        num = 3
        num_tests = 100

        circ = MinValueCircuit(bits, num)

        async def testbench(sim: TestbenchContext):
            for _ in range(num_tests):
                vals = [random.randrange(2**bits) for _ in circ.inputs]
                for sig, val in zip(circ.inputs, vals):
                    sim.set(sig, val)
                assert sim.get(circ.output) == min(vals)
                await sim.tick()

        with self.run_simulation(circ) as sim:
            sim.add_testbench(testbench)
