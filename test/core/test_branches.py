import pytest

from amaranth import *
from itertools import product
from transactron.core import (
    TModule,
    Method,
    Transaction,
    TransactionManager,
    TransactronContextElaboratable,
    def_method,
)
from transactron.core.tmodule import CtrlPath
from transactron.core.manager import MethodMap
from unittest import TestCase
from transactron.testing import SimpleTestCircuit, TestCaseWithSimulator
from transactron.utils.dependencies import DependencyContext


class TestExclusivePath(TestCase):
    def test_exclusive_path(self):
        m = TModule()
        m._MustUse__silence = True  # type: ignore

        with m.If(0):
            cp0 = m.ctrl_path
            with m.Switch(3):
                with m.Case(0):
                    cp0a0 = m.ctrl_path
                with m.Case(1):
                    cp0a1 = m.ctrl_path
                with m.Default():
                    cp0a2 = m.ctrl_path
            with m.If(1):
                cp0b0 = m.ctrl_path
            with m.Else():
                cp0b1 = m.ctrl_path
        with m.Elif(1):
            cp1 = m.ctrl_path
            with m.FSM():
                with m.State("start"):
                    cp10 = m.ctrl_path
                with m.State("next"):
                    cp11 = m.ctrl_path
        with m.Else():
            cp2 = m.ctrl_path

        def mutually_exclusive(*cps: CtrlPath):
            return all(cpa.exclusive_with(cpb) for i, cpa in enumerate(cps) for cpb in cps[i + 1 :])

        def pairwise_exclusive(cps1: list[CtrlPath], cps2: list[CtrlPath]):
            return all(cpa.exclusive_with(cpb) for cpa, cpb in product(cps1, cps2))

        def pairwise_not_exclusive(cps1: list[CtrlPath], cps2: list[CtrlPath]):
            return all(not cpa.exclusive_with(cpb) for cpa, cpb in product(cps1, cps2))

        assert mutually_exclusive(cp0, cp1, cp2)
        assert mutually_exclusive(cp0a0, cp0a1, cp0a2)
        assert mutually_exclusive(cp0b0, cp0b1)
        assert mutually_exclusive(cp10, cp11)
        assert pairwise_exclusive([cp0, cp0a0, cp0a1, cp0a2, cp0b0, cp0b1], [cp1, cp10, cp11])
        assert pairwise_not_exclusive([cp0, cp0a0, cp0a1, cp0a2], [cp0, cp0b0, cp0b1])


class ExclusiveConflictRemovalCircuit(Elaboratable):
    def __init__(self):
        self.sel = Signal()

    def elaborate(self, platform):
        m = TModule()

        called_method = Method(i=[], o=[])

        @def_method(m, called_method)
        def _():
            pass

        with m.If(self.sel):
            with Transaction().body(m):
                called_method(m)
        with m.Else():
            with Transaction().body(m):
                called_method(m)

        return m


class TestExclusiveConflictRemoval(TestCaseWithSimulator):
    def test_conflict_removal(self):
        circ = ExclusiveConflictRemovalCircuit()

        tm = TransactionManager()
        dut = TransactronContextElaboratable(circ, DependencyContext.get(), tm)

        with self.run_simulation(dut, add_transaction_module=False):
            pass

        cgr, _ = tm._conflict_graph(MethodMap(tm.transactions))

        for s in cgr.values():
            assert not s


class ExclusiveDiamondCallCircuit(Elaboratable):
    def __init__(self):
        self.sel = Signal()
        self.method_left = Method()
        self.method_right = Method()
        self.method_inner = Method()

    def elaborate(self, platform):
        m = TModule()

        @def_method(m, self.method_inner)
        def _():
            pass

        @def_method(m, self.method_left)
        def _():
            self.method_inner(m)

        @def_method(m, self.method_right)
        def _():
            self.method_inner(m)

        with Transaction().body(m):
            with m.If(self.sel):
                self.method_left(m)
            with m.Else():
                self.method_right(m)

        return m


class NonExclusiveDiamondCallCircuit(Elaboratable):
    def __init__(self):
        self.sel_left = Signal()
        self.sel_right = Signal()
        self.method_left = Method()
        self.method_right = Method()
        self.method_inner = Method()

    def elaborate(self, platform):
        m = TModule()

        @def_method(m, self.method_inner)
        def _():
            pass

        @def_method(m, self.method_left)
        def _():
            self.method_inner(m)

        @def_method(m, self.method_right)
        def _():
            self.method_inner(m)

        with Transaction().body(m):
            with m.If(self.sel_left):
                self.method_left(m)
            with m.If(self.sel_right):
                self.method_right(m)

        return m


class TestExclusiveDiamondCall(TestCaseWithSimulator):
    def test_exclusive_diamond_call(self):
        dut = ExclusiveDiamondCallCircuit()
        circ = SimpleTestCircuit(dut)

        async def run(sim):
            assert sim.get(dut.method_inner.run)

        with self.run_simulation(circ) as sim:
            sim.add_testbench(run)


class TestNonExclusiveDiamondCall(TestCaseWithSimulator):
    def test_nonexclusive_diamond_call(self):
        circ = SimpleTestCircuit(NonExclusiveDiamondCallCircuit())

        with pytest.raises(RuntimeError):
            with self.run_simulation(circ):
                pass
