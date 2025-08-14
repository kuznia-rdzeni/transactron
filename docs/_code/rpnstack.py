from amaranth import *
from transactron import TModule, Method, def_method
from transactron.lib.stack import Stack


class RpnStack(Elaboratable):
    def __init__(self, shape):
        self.shape = shape
        self.layout = [("val", shape)]

        self.peek = Method(o=self.layout)
        self.peek2 = Method(o=self.layout)
        self.push = Method(i=self.layout)
        self.pop_set_top = Method(i=self.layout)

    def elaborate(self, platform):
        m = TModule()

        m.submodules.stack = stack = Stack(self.layout, 32)

        top = Signal(self.shape)
        nonempty = Signal()

        @def_method(m, self.peek, ready=nonempty, nonexclusive=True)
        def _():
            return {"val": top}

        @def_method(m, self.peek2, nonexclusive=True)
        def _():
            return stack.peek(m)

        @def_method(m, self.push)
        def _(val):
            m.d.sync += nonempty.eq(1)
            m.d.sync += top.eq(val)
            with m.If(nonempty):
                stack.write(m, val=top)

        @def_method(m, self.pop_set_top)
        def _(val):
            m.d.sync += top.eq(val)
            stack.read(m)

        self.push.add_conflict(self.pop_set_top)

        return m
