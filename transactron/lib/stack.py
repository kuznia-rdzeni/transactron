from amaranth import *
import amaranth.lib.memory as memory
from amaranth_types import ValueLike, SrcLoc
from transactron import Method, def_method, TModule
from transactron.utils._typing import MethodLayout, MethodStruct
from transactron.utils.transactron_helpers import from_method_layout, get_src_loc


__all__ = ["Stack"]


class Stack(Elaboratable):
    """Transactional stack"""

    read: Method
    """Reads the top of the stack and pops it.

    Returns data at the top of the stack, as specified by the data layout
    `layout`, and removes it. Ready only if the stack is not empty.

    If the stack is written in the same cycle, the result is as if
    `read` and `write` were called in sequence.

    Parameters
    ----------
    m: TModule
        Transactron module.

    Returns
    -------
    MethodStruct
        Data with layout `layout`.
    """

    peek: Method
    """Returns the element at the top of the stack.

    Ready only if the stack is not empty. The method is nonexclusive.
    The stack is not modified.

    Parameters
    ----------
    m: TModule
        Transactron module.

    Returns
    -------
    MethodStruct
        Data with layout `layout`.
    """

    write: Method
    """Pushes data to the stack.

    Accepts arguments as specified by the data layout `layout`. Ready only if
    the stack is not full.

    If called in the same cycle as `read`, the result of `read` is not
    influenced.

    Parameters
    ----------
    m: TModule
        Transactron module.
    **kwargs: ValueLike
        Arguments as specified by the data layout.
    """

    clear: Method
    """Clears the stack.

    The stack is empty in the next cycle after this method runs, even when
    `write` is called simultaneously with `clear`.

    Parameters
    ----------
    m: TModule
        Transactron module.
    """

    def __init__(self, layout: MethodLayout, depth: int, *, src_loc: int | SrcLoc = 0) -> None:
        """
        Parameters
        ----------
        layout: method layout
            Layout of data stored in the stack.
        depth: int
            Size of the stack.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        self.layout = from_method_layout(layout)
        self.depth = depth

        src_loc = get_src_loc(src_loc)
        self.read = Method(o=self.layout, src_loc=src_loc)
        self.peek = Method(o=self.layout, src_loc=src_loc)
        self.write = Method(i=self.layout, src_loc=src_loc)
        self.clear = Method(src_loc=src_loc)
        self.head = Signal(from_method_layout(layout))

        self.data = memory.Memory(shape=self.layout, depth=self.depth, init=[])

        self.level = Signal(range(self.depth + 1))

    def elaborate(self, platform):
        m = TModule()

        m.submodules.data = self.data
        data_wrport = self.data.write_port()
        data_rdport = self.data.read_port(domain="sync", transparent_for=[data_wrport])

        read_ready = Signal()
        write_ready = Signal()

        m.d.comb += read_ready.eq(self.level != 0)
        m.d.comb += write_ready.eq(self.level != self.depth)

        next_level = Signal.like(self.level)
        m.d.comb += next_level.eq(self.level)
        with m.If(self.read.run & ~self.write.run):
            m.d.comb += next_level.eq(self.level - 1)
        with m.If(self.write.run & ~self.read.run):
            m.d.comb += next_level.eq(self.level + 1)
        with m.If(self.clear.run):
            m.d.comb += next_level.eq(0)
        m.d.sync += self.level.eq(next_level)

        m.d.comb += data_rdport.addr.eq(next_level - 1)
        m.d.comb += self.head.eq(data_rdport.data)

        @def_method(m, self.write, ready=write_ready)
        def _(arg: MethodStruct) -> None:
            m.d.top_comb += data_wrport.addr.eq(data_rdport.addr)
            m.d.top_comb += data_wrport.data.eq(arg)
            m.d.comb += data_wrport.en.eq(1)

        @def_method(m, self.read, read_ready)
        def _() -> ValueLike:
            # size change handled in earlier code
            return self.head

        @def_method(m, self.peek, read_ready, nonexclusive=True)
        def _() -> ValueLike:
            return self.head

        @def_method(m, self.clear)
        def _() -> None:
            pass  # size change handled in earlier code

        return m
