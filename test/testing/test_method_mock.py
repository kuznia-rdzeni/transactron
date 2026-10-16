import random
from amaranth import *
from amaranth.sim import *
from amaranth.lib.data import StructLayout
import pytest

from transactron import *
from transactron.testing import TestCaseWithSimulator, TestbenchContext, SimpleTestCircuit
from transactron.testing.method_mock import MethodMock, def_method_mock, def_method_mocks
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


class DerivedMethodMockTestCircuit(SimpleMethodMockTestCircuit):
    pass


class MroTestBase:
    method: int
    wrapper: int


class MroMethodMockTestCircuit(SimpleMethodMockTestCircuit, MroTestBase):  # type: ignore
    pass


@pytest.mark.parametrize(
    "test_circuit", [SimpleMethodMockTestCircuit, DerivedMethodMockTestCircuit, MroMethodMockTestCircuit]
)
class TestMethodMock(TestCaseWithSimulator):
    async def process(self, sim: TestbenchContext):
        for _ in range(20):
            val = random.randrange(2**self.width)
            ret = await self.dut.wrapper.call(sim, input=val)
            assert ret.output == (val + 2) % 2**self.width

    @def_method_mock(lambda self: self.dut.method, enable=lambda _: random.randint(0, 1))
    def method_mock(self, input):
        return {"output": input + 1}

    def test_method_mock_simple(self, test_circuit):
        random.seed(42)
        self.width = 4
        self.dut = SimpleTestCircuit(test_circuit(self.width))

        with self.run_simulation(self.dut) as sim:
            sim.add_testbench(self.process)

    def test_method_mock_exclude(self, test_circuit):
        self.dut = SimpleTestCircuit(test_circuit(1), exclude={"wrapper"})

        assert not hasattr(self.dut, "wrapper")

        with self.run_simulation(self.dut):
            pass


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


class SimpleMethodMocksTestCircuit(Elaboratable):
    method: Required[Methods]
    wrapper: Provided[Methods]

    def __init__(self, count: int, width: int):
        self.method = Methods(count, i=StructLayout({"input": width}), o=StructLayout({"output": width}))
        self.wrapper = Methods(count, i=StructLayout({"input": width}), o=StructLayout({"output": width}))

    def elaborate(self, platform):
        m = TModule()

        @def_methods(m, self.wrapper)
        def _(k, input):
            return {"output": self.method[k](m, input).output + k}

        return m


class TestMethodMocks(TestCaseWithSimulator):
    def make_process(self, k: int):
        async def process(sim: TestbenchContext):
            for _ in range(20):
                val = random.randrange(2**self.width)
                ret = await self.dut.wrapper[k].call(sim, input=val)
                assert ret.output == (val + 2 * k) % 2**self.width

        return process

    @def_method_mocks(lambda self: self.dut.method, enable=lambda _: random.randint(0, 1))
    def method_mock(self, k, input):
        return {"output": input + k}

    def test_method_mocks(self):
        random.seed(42)
        self.count = 3
        self.width = 10
        self.dut = SimpleTestCircuit(SimpleMethodMocksTestCircuit(self.count, self.width))

        with self.run_simulation(self.dut) as sim:
            for k in range(self.count):
                sim.add_testbench(self.make_process(k))
