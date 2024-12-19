import random
from amaranth import *
from amaranth.sim import *
from amaranth.lib.data import StructLayout

from transactron import *
from transactron.testing import TestCaseWithSimulator, TestbenchContext
from transactron.testing.infrastructure import SimpleTestCircuit
from transactron.testing.method_mock import MethodMock, def_method_mock
from transactron.lib import *


class SimpleMethodMockTestCircuit(Elaboratable):
    method: Required[Method]
    wrapper: Provided[Method]

    def __init__(self, width: int):
        self.method = Method(i=StructLayout({"input": width}), o=StructLayout({"output": width}))
        self.wrapper = Method(i=StructLayout({"input": width}), o=StructLayout({"output": width}))

    def elaborate(self, platform):
        m = TModule()

        @def_method(m, self.wrapper)
        def _(input):
            return {"output": self.method(m, input).output + 1}

        return m


class TestMethodMock(TestCaseWithSimulator):
    async def process(self, sim: TestbenchContext):
        for _ in range(20):
            val = random.randrange(2**self.width)
            ret = await self.dut.wrapper.call(sim, input=val)
            assert ret.output == (val + 2) % 2**self.width

    @def_method_mock(lambda self: self.dut.method, enable=lambda _: random.randint(0, 1))
    def method_mock(self, input):
        return {"output": input + 1}

    def test_method_mock_simple(self):
        random.seed(42)
        self.width = 4
        self.dut = SimpleTestCircuit(SimpleMethodMockTestCircuit(self.width))

        with self.run_simulation(self.dut) as sim:
            sim.add_testbench(self.process)


class ReverseMethodMockTestCircuit(Elaboratable):
    def __init__(self, width):
        self.method = Method(i=StructLayout({"input": width}), o=StructLayout({"output": width}))

    def elaborate(self, platform):
        m = TModule()

        @def_method(m, self.method)
        def _(input):
            return input + 1

        return m


class TestReverseMethodMock(TestCaseWithSimulator):
    async def active(self, sim: TestbenchContext):
        for _ in range(10):
            await sim.tick()

    @def_method_mock(lambda self: self.m.method, enable=lambda _: random.randint(0, 1))
    def method_mock(self, output: int):
        input = random.randrange(0, 2**self.width)

        @MethodMock.effect
        def _():
            assert output == (input + 1) % 2**self.width

        return {"input": input}

    def test_reverse_method_mock(self):
        random.seed(42)
        self.width = 4
        self.m = SimpleTestCircuit(ReverseMethodMockTestCircuit(self.width))
        self.accepted_val = 0
        with self.run_simulation(self.m) as sim:
            sim.add_testbench(self.active)
