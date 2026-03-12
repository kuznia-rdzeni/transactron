from amaranth import *

from transactron.core import Method, Methods, TModule, def_method, def_methods
from transactron.utils.amaranth_ext.elaboratables import MultiPriorityEncoder
from amaranth.lib.data import ArrayLayout


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


class PreservedOrderAllocator(Elaboratable):
    """Allocator with allocation order information.

    This module allows to allocate and deallocate identifiers from a
    continuous range. The order of allocations is preserved in the form of
    a permutation of identifiers. Smaller positions correspond to earlier
    (older) allocations.

    Attributes
    ----------
    alloc : Method
        Allocates a fresh identifier.
    free : Method
        Frees a previously allocated identifier.
    free_idx : Method
        Frees a previously allocated identifier at the given index of the
        allocation order.
    order : Method
        Returns the allocation order as a permutation of identifiers
        and the number of allocated identifiers.
    """

    def __init__(self, entries: int):
        self.entries = entries

        self.alloc = Method(o=[("ident", range(entries))])
        self.free = Method(i=[("ident", range(entries))])
        self.free_idx = Method(i=[("idx", range(entries))])
        self.order = Method(
            o=[("used", range(entries + 1)), ("order", ArrayLayout(range(self.entries), self.entries))],
        )

    def elaborate(self, platform) -> TModule:
        m = TModule()

        order = Signal(ArrayLayout(range(self.entries), self.entries), init=list(range(self.entries)))
        used = Signal(range(self.entries + 1))
        incr_used = Signal(range(self.entries + 1))

        m.d.comb += incr_used.eq(used + self.alloc.run)
        m.d.sync += used.eq(incr_used - self.free_idx.run)

        @def_method(m, self.alloc, ready=used != self.entries)
        def _():
            return {"ident": order[used]}

        @def_method(m, self.free_idx)
        def _(idx):
            for i in range(self.entries - 1):
                with m.If(i >= idx):
                    m.d.sync += order[i].eq(order[i + 1])
            m.d.sync += order[self.entries - 1].eq(order[idx])

        @def_method(m, self.free)
        def _(ident):
            idx = Signal(range(self.entries))
            for i in range(self.entries):
                with m.If(order[i] == ident):
                    m.d.comb += idx.eq(i)
            self.free_idx(m, idx=idx)

        @def_method(m, self.order, nonexclusive=True)
        def _():
            return {"used": used, "order": order}

        return m
