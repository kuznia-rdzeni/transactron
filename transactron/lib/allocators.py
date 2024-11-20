from amaranth import *

from transactron.core import Methods, TModule, def_methods
from transactron.utils.amaranth_ext.elaboratables import MultiPriorityEncoder


__all__ = ["PriorityEncoderAllocator"]


class PriorityEncoderAllocator(Elaboratable):
    """Superscalar structure for identifier allocation.

    This module allows to allocate and deallocate identifiers from a continuous
    range. Multiple identifiers can be allocated or deallocated in a single
    clock cycle.

    Attributes
    ----------
    alloc : Methods
        Methods which allocate a fresh identifier. If there is too little free
        identifiers, some or all of the methods are disabled.
    free : Methods
        Methods which deallocate a single identifier in one cycle.
    """

    def __init__(self, entries: int, ways: int = 1, *, init: int = -1):
        """
        Parameters
        ----------
        entries : int
            The total number of identifiers available for allocation.
        ways : int
            The number of `alloc` and `free` methods.
        init : int
            Bit mask of identifiers which should be treated as free on reset.
            By default, every identifier is free on reset.
        """
        self.entries = entries
        self.ways = ways
        self.init = init

        self.alloc = Methods(ways, o=[("ident", range(entries))])
        self.free = Methods(ways, i=[("ident", range(entries))])

    def elaborate(self, platform) -> TModule:
        m = TModule()

        not_used = Signal(self.entries, init=self.init)

        m.submodules.priority_encoder = encoder = MultiPriorityEncoder(self.entries, self.ways)
        m.d.top_comb += encoder.input.eq(not_used)

        @def_methods(m, self.alloc, ready=lambda i: encoder.valids[i])
        def _(i):
            m.d.sync += not_used.bit_select(encoder.outputs[i], 1).eq(0)
            return {"ident": encoder.outputs[i]}

        @def_methods(m, self.free)
        def _(_, ident):
            m.d.sync += not_used.bit_select(ident, 1).eq(1)

        return m
