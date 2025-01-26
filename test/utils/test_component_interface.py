from unittest import TestCase

from amaranth import Signal, unsigned
from amaranth.lib.data import ArrayLayout, View
from amaranth.lib.wiring import Component, Flow, In
from transactron.utils.amaranth_ext.component_interface import CIn, COut, ComponentInterface


class SubSubInterface(ComponentInterface):
    def __init__(self):
        self.i = CIn()


class SubInterface(ComponentInterface):
    def __init__(self):
        self.i = CIn()
        self.f = SubSubInterface().flipped()


class TBInterface(ComponentInterface):
    def __init__(self):
        self.i = CIn(2)
        self.o = COut(2)
        self.s = SubInterface()
        self.f = SubInterface().flipped()
        self.uf = SubInterface().flipped().flipped()
        self.a = COut(ArrayLayout(2, 3))


class TestComponentInterface(TestCase):
    def test_a(self):
        class TestComponent(Component):
            iface: TBInterface

            def __init__(self):
                super().__init__({"iface": In(TBInterface().signature)})

        t = TestComponent()

        assert isinstance(t.iface.i, Signal)
        assert isinstance(t.iface.s.i, Signal)
        assert isinstance(t.iface.f.i, Signal)

        ci = TBInterface()
        sig = ci.signature

        assert sig.members["s"].signature.members["i"].flow is Flow.In
        assert sig.members["f"].signature.members["i"].flow is Flow.Out
        assert sig.members["uf"].signature.members["i"].flow is Flow.In

        assert sig.members["s"].signature.members["f"].signature.members["i"].flow is Flow.Out
        assert sig.members["f"].signature.members["f"].signature.members["i"].flow is Flow.In
        assert sig.members["uf"].signature.members["f"].signature.members["i"].flow is Flow.Out

        assert t.iface.f.f.i.shape() == unsigned(1)
        assert t.iface.i.shape() == unsigned(2)
        assert t.iface.a.shape() == ArrayLayout(2, 3)
        assert isinstance(t.iface.a, View)
