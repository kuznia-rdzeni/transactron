from amaranth import *
from transactron import *

from transactron.lib.pipeline import PipelineBuilder


class PipelinedMult(Elaboratable):
    read: Provided[Method]
    write: Provided[Method]

    def __init__(self):
        self.write = Method(i=[("a", unsigned(32)), ("b", unsigned(32))])
        self.read = Method(o=[("data", unsigned(64))])

    def elaborate(self, platform):
        m = TModule()

        m.submodules.pipeline = p = PipelineBuilder()

        p.add_external(self.write)

        @p.stage(m, o=[("data", unsigned(32))])
        def _(a, b):
            return {"data": a[16:] * b[16:]}

        # the shape of specific signal can change between stages
        @p.stage(m, o=[("data", unsigned(64))])
        def _(a, b, data):
            return {"data": data + ((a[16:] * b[:16]) << 16)}

        @p.stage(m, o=[("data", unsigned(64))])
        def _(a, b, data):
            return {"data": data + ((a[:16] * b[16:]) << 16)}

        @p.stage(m, o=[("data", unsigned(64))])
        def _(a, b, data):
            return {"data": data + ((a[:16] * b[:16]) << 32)}

        p.add_external(self.read)

        return m
