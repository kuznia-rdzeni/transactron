from dataclasses import dataclass
from typing import Required

import pytest
from transactron.core import *
from transactron.lib.dependencies import ListKey, UnifierKey
from transactron.lib.transformers import MethodProduct
from transactron.testing.infrastructure import TestCaseWithSimulator

from amaranth import *
from transactron.testing.test_circuit import SimpleTestCircuit
from transactron.utils.dependencies import DependencyContext


@dataclass(frozen=True)
class ListKeyInstance(ListKey[str]):
    pass


@dataclass(frozen=True)
class ListKeyInstanceNoLockEmptyValid(ListKey[str]):
    lock_on_get = False
    empty_valid = True


@dataclass(frozen=True)
class UnifierKeyProduct(UnifierKey, unifier=MethodProduct.create):
    pass


class TestDependencies(TestCaseWithSimulator):
    class CircuitTestModule(Elaboratable):
        b: Required[Method]
        c: Required[Method]

        def __init__(self):
            self.a = Method()
            self.b = Method()
            self.c = Method()

            self.dm = DependencyContext.get()
            self.dm.add_dependency(UnifierKeyProduct(), self.b)
            self.dm.add_dependency(UnifierKeyProduct(), self.c)

        def elaborate(self, platform):
            m = TModule()

            method, unifier = DependencyContext.get().get_dependency(UnifierKeyProduct())

            m.submodules += unifier

            self.a.provide(method)

            return m

    def test_unifier_key(self):
        m = SimpleTestCircuit(self.CircuitTestModule())

        async def test_process(sim):
            m.b.enable(sim)
            m.c.enable(sim)
            m.a.call_init(sim)
            await sim.tick()
            assert m.a.get_done(sim)
            assert m.b.get_done(sim)
            assert m.c.get_done(sim)

        with self.run_simulation(m) as sim:
            sim.add_testbench(test_process)

    @pytest.mark.parametrize("key", [ListKeyInstance(), ListKeyInstanceNoLockEmptyValid()])
    def test_list_key(self, key):
        dm = DependencyContext.get()

        if isinstance(key, ListKeyInstanceNoLockEmptyValid):
            assert dm.get_dependency(key) == []

        dm.add_dependency(key, "a")
        dm.add_dependency(key, "b")
        assert dm.get_dependency(key) == ["a", "b"]
        try:
            dm.add_dependency(key, "c")
            assert dm.get_dependency(key) == ["a", "b", "c"]
        except KeyError:
            assert isinstance(key, ListKeyInstance)
