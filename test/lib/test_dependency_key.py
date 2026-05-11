from dataclasses import dataclass
from typing import Required

import pytest
from transactron.core import *
from transactron.lib.dependencies import ListKey, UnifierKey
from transactron.lib.transformers import MethodProduct
from transactron.testing.infrastructure import TestCaseWithSimulator

from amaranth import *
from transactron.testing.test_circuit import SimpleTestCircuit
from transactron.utils.dependencies import DependencyContext, DependencyKey, SimpleKey


@dataclass(frozen=True)
class ListKeyInstance(ListKey[str]):
    pass


@dataclass(frozen=True)
class ListKeyInstanceNoLock(ListKey[str]):
    lock_on_get = False


@dataclass(frozen=True)
class SimpleKeyEmptyValid(SimpleKey[str]):
    empty_valid = True
    default_value = "default"


combine_calls = 0


class CacheTestKey(DependencyKey[None, int]):
    def combine(self, data):
        global combine_calls
        combine_calls += 1
        return combine_calls


@dataclass(frozen=True)
class KeyNoCache(CacheTestKey):
    cache = False
    pass


@dataclass(frozen=True)
class KeyCache(CacheTestKey):
    pass


@dataclass(frozen=True)
class UnifierKeyProduct(UnifierKey, unifier=MethodProduct.create):
    pass


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


class TestDependencyKey(TestCaseWithSimulator):

    def test_unifier_key(self):
        m = SimpleTestCircuit(CircuitTestModule())

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

    @pytest.mark.parametrize("key", [ListKeyInstance(), ListKeyInstanceNoLock()])
    def test_list_key(self, key):
        dm = DependencyContext.get()

        if isinstance(key, ListKeyInstanceNoLock):
            assert dm.get_dependency(key) == []

        dm.add_dependency(key, "a")
        dm.add_dependency(key, "b")
        assert dm.get_dependency(key) == ["a", "b"]
        try:
            dm.add_dependency(key, "c")
            assert dm.get_dependency(key) == ["a", "b", "c"]
            assert isinstance(key, ListKeyInstanceNoLock)
        except KeyError:
            assert isinstance(key, ListKeyInstance)

    def test_simple_key_defualt(self):
        dm = DependencyContext.get()
        assert dm.get_dependency(SimpleKeyEmptyValid()) == "default"

    def test_simple_key(self):
        dm = DependencyContext.get()
        dm.add_dependency(SimpleKeyEmptyValid(), "dep")
        assert dm.get_dependency(SimpleKeyEmptyValid()) == "dep"
        assert dm.get_dependency(SimpleKeyEmptyValid()) == "dep"

    def test_simple_key_multiple(self):
        dm = DependencyContext.get()
        try:
            dm.add_dependency(SimpleKeyEmptyValid(), "dep")
            dm.add_dependency(SimpleKeyEmptyValid(), "dep")
            dm.get_dependency(SimpleKeyEmptyValid())
            assert False
        except RuntimeError:
            pass

    def test_key_empty(self):
        dm = DependencyContext.get()
        try:
            dm.get_dependency(KeyNoCache())
            assert False
        except KeyError:
            pass

    def test_key_cache(self):
        global combine_calls
        dm = DependencyContext.get()
        dm.add_dependency(KeyCache(), None)
        dm.add_dependency(KeyNoCache(), None)
        dm.get_dependency(KeyCache())
        assert combine_calls == 1
        dm.get_dependency(KeyCache())
        assert combine_calls == 1
        dm.get_dependency(KeyNoCache())
        assert combine_calls == 2
        dm.get_dependency(KeyNoCache())
        assert combine_calls == 3
