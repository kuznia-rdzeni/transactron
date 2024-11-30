from typing import Optional
from amaranth import *
import amaranth.lib.memory as memory
import amaranth.lib.data as data
from amaranth_types.types import ShapeLike
from transactron import Method, def_method, Priority, TModule
from transactron.utils._typing import ValueLike, MethodLayout, SrcLoc, MethodStruct
from transactron.utils.amaranth_ext import mod_incr, rotate_vec_left, rotate_vec_right
from transactron.utils.amaranth_ext.shifter import rotate_right
from transactron.utils.transactron_helpers import from_method_layout, get_src_loc


__all__ = ["BasicFifo", "WideFifo", "Semaphore"]


class BasicFifo(Elaboratable):
    """Transactional FIFO queue

    Attributes
    ----------
    read: Method
        Reads from the FIFO. Accepts an empty argument, returns a structure.
        Ready only if the FIFO is not empty.
    peek: Method
        Returns the element at the front (but not delete). Ready only if the FIFO
        is not empty. The method is nonexclusive.
    write: Method
        Writes to the FIFO. Accepts a structure, returns empty result.
        Ready only if the FIFO is not full.
    clear: Method
        Clears the FIFO entries. Has priority over `read` and `write` methods.
        Note that, clearing the FIFO doesn't reinitialize it to values passed in `init` parameter.

    """

    def __init__(self, layout: MethodLayout, depth: int, *, src_loc: int | SrcLoc = 0) -> None:
        """
        Parameters
        ----------
        layout: method layout
            Layout of data stored in the FIFO.
        depth: int
            Size of the FIFO.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        self.layout = layout
        self.width = from_method_layout(self.layout).size
        self.depth = depth

        src_loc = get_src_loc(src_loc)
        self.read = Method(o=self.layout, src_loc=src_loc)
        self.peek = Method(o=self.layout, nonexclusive=True, src_loc=src_loc)
        self.write = Method(i=self.layout, src_loc=src_loc)
        self.clear = Method(src_loc=src_loc)
        self.head = Signal(from_method_layout(layout))

        self.buff = memory.Memory(shape=self.width, depth=self.depth, init=[])

        self.write_ready = Signal()
        self.read_ready = Signal()

        self.read_idx = Signal((self.depth - 1).bit_length())
        self.write_idx = Signal((self.depth - 1).bit_length())
        # current fifo depth
        self.level = Signal((self.depth).bit_length())

        # for interface compatibility with MultiportFifo
        self.read_methods = [self.read]
        self.write_methods = [self.write]

    def elaborate(self, platform):
        m = TModule()

        next_read_idx = Signal.like(self.read_idx)
        m.d.comb += next_read_idx.eq(mod_incr(self.read_idx, self.depth))

        m.submodules.buff = self.buff
        self.buff_wrport = self.buff.write_port()
        self.buff_rdport = self.buff.read_port(domain="sync", transparent_for=[self.buff_wrport])

        m.d.comb += self.read_ready.eq(self.level != 0)
        m.d.comb += self.write_ready.eq(self.level != self.depth)

        with m.If(self.read.run & ~self.write.run):
            m.d.sync += self.level.eq(self.level - 1)
        with m.If(self.write.run & ~self.read.run):
            m.d.sync += self.level.eq(self.level + 1)
        with m.If(self.clear.run):
            m.d.sync += self.level.eq(0)

        m.d.comb += self.buff_rdport.addr.eq(Mux(self.read.run, next_read_idx, self.read_idx))
        m.d.comb += self.head.eq(self.buff_rdport.data)

        @def_method(m, self.write, ready=self.write_ready)
        def _(arg: MethodStruct) -> None:
            m.d.top_comb += self.buff_wrport.addr.eq(self.write_idx)
            m.d.top_comb += self.buff_wrport.data.eq(arg)
            m.d.comb += self.buff_wrport.en.eq(1)

            m.d.sync += self.write_idx.eq(mod_incr(self.write_idx, self.depth))

        @def_method(m, self.read, self.read_ready)
        def _() -> ValueLike:
            m.d.sync += self.read_idx.eq(next_read_idx)
            return self.head

        @def_method(m, self.peek, self.read_ready)
        def _() -> ValueLike:
            return self.head

        @def_method(m, self.clear)
        def _() -> None:
            m.d.sync += self.read_idx.eq(0)
            m.d.sync += self.write_idx.eq(0)

        return m


class WideFifo(Elaboratable):
    def __init__(
        self, shape: ShapeLike, depth: int, read_width: int, write_width: Optional[int], *, src_loc: int | SrcLoc = 0
    ) -> None:
        if write_width is None:
            write_width = read_width

        self.shape = shape
        self.read_layout = data.StructLayout(
            {"count": range(read_width + 1), "data": data.ArrayLayout(shape, read_width)}
        )
        self.write_layout = data.StructLayout(
            {"count": range(write_width + 1), "data": data.ArrayLayout(shape, write_width)}
        )
        self.read_width = read_width
        self.write_width = write_width
        self.depth = depth

        self.read = Method(i=[("count", range(read_width + 1))], o=self.read_layout, src_loc=src_loc)
        self.peek = Method(o=self.read_layout, nonexclusive=True, src_loc=src_loc)
        self.write = Method(i=self.write_layout, src_loc=src_loc)
        self.clear = Method(src_loc=src_loc)

    def elaborate(self, platform):
        m = TModule()

        max_width = max(self.read_width, self.write_width)

        storage = [memory.Memory(shape=self.shape, depth=self.depth, init=[]) for _ in range(max_width)]

        for i, mem in enumerate(storage):
            m.submodules[f"storage{i}"] = mem

        write_ports = [mem.write_port() for mem in storage]
        read_ports = [mem.read_port(domain="sync", transparent_for=[port]) for mem, port in zip(storage, write_ports)]

        write_row = Signal(range(self.depth))
        write_col = Signal(range(max_width))
        read_row = Signal(range(self.depth))
        read_col = Signal(range(max_width))

        next_read_row = Signal(range(self.depth))
        next_read_col = Signal(range(max_width))

        incr_read_row = Signal(range(self.depth))
        incr_next_read_row = Signal(range(self.depth))
        incr_write_row = Signal(range(self.depth))

        level = Signal(range(max_width * self.depth + 1))
        remaining = Signal(range(max_width * self.depth + 1))

        read_available = Signal(range(self.read_width + 1))
        write_available = Signal(range(self.write_width + 1))

        read_count = Signal(range(self.read_width + 1))
        write_count = Signal(range(self.write_width + 1))

        m.d.comb += incr_read_row.eq(mod_incr(read_row, self.depth))
        m.d.comb += incr_next_read_row.eq(mod_incr(next_read_row, self.depth))
        m.d.comb += incr_write_row.eq(mod_incr(write_row, self.depth))

        m.d.sync += level.eq(level - read_count + write_count)
        m.d.comb += remaining.eq(max_width * self.depth - level)

        m.d.comb += read_available.eq(Mux(level > self.read_width, self.read_width, level))
        m.d.comb += write_available.eq(Mux(remaining > self.write_width, self.write_width, remaining))

        for i, port in enumerate(read_ports):
            m.d.comb += port.addr.eq(Mux(i >= next_read_col, next_read_row, incr_next_read_row))

        for i, port in enumerate(write_ports):
            m.d.comb += port.addr.eq(Mux(i >= write_col, write_row, incr_write_row))

        read_data = [port.data for port in read_ports]
        head = rotate_vec_left(read_data, read_col)[: self.read_width]

        head_sig = [Signal.like(item) for item in head]
        for item_sig, item in zip(head_sig, head):
            m.d.comb += item_sig.eq(item)

        def incr_row_col(new_row: Value, new_col: Value, row: Value, col: Value, incr_row: Value, count: Value):
            chg_row = Signal.like(new_row)
            chg_col = Signal.like(new_col)
            with m.If(col + count >= max_width):
                m.d.comb += chg_row.eq(incr_row)
                m.d.comb += chg_col.eq(col + count - max_width)
            with m.Else():
                m.d.comb += chg_row.eq(row)
                m.d.comb += chg_col.eq(col + count)
            return [new_row.eq(chg_row), new_col.eq(chg_col)]

        @def_method(m, self.write, remaining != 0, validate_arguments=lambda count, data: count <= remaining)
        def _(count, data):
            ext_data = list(data) + [C(0, self.shape)] * (max_width - self.write_width)
            shifted_data = rotate_vec_right(ext_data, write_col)
            ens = Signal(max_width)
            m.d.comb += ens.eq(Cat(i < count for i in range(max_width)))
            m.d.comb += Cat(port.en for port in write_ports).eq(rotate_right(ens, write_col))
            m.d.av_comb += [write_ports[i].data.eq(shifted_data[i]) for i in range(max_width)]
            m.d.comb += write_count.eq(count)
            m.d.sync += incr_row_col(write_row, write_col, write_row, write_col, incr_write_row, count)

        m.d.comb += next_read_row.eq(read_row)
        m.d.comb += next_read_col.eq(read_col)
        m.d.sync += read_row.eq(next_read_row)
        m.d.sync += read_col.eq(next_read_col)

        @def_method(m, self.read, level != 0)
        def _(count):
            m.d.comb += read_count.eq(Mux(count > read_available, read_available, count))
            m.d.comb += incr_row_col(next_read_row, next_read_col, read_row, read_col, incr_read_row, read_count)
            return {"count": read_count, "data": head}

        @def_method(m, self.peek, level != 0)
        def _():
            return {"count": read_available, "data": head}

        @def_method(m, self.clear)
        def _() -> None:
            m.d.sync += write_row.eq(0)
            m.d.sync += write_col.eq(0)
            m.d.sync += read_row.eq(0)
            m.d.sync += read_col.eq(0)
            m.d.sync += level.eq(0)

        return m


class Semaphore(Elaboratable):
    """Semaphore"""

    def __init__(self, max_count: int) -> None:
        """
        Parameters
        ----------
        size: int
            Size of the semaphore.

        """
        self.max_count = max_count

        self.acquire = Method()
        self.release = Method()
        self.clear = Method()

        self.acquire_ready = Signal()
        self.release_ready = Signal()

        self.count = Signal(self.max_count.bit_length())
        self.count_next = Signal(self.max_count.bit_length())

        self.clear.add_conflict(self.acquire, Priority.LEFT)
        self.clear.add_conflict(self.release, Priority.LEFT)

    def elaborate(self, platform) -> TModule:
        m = TModule()

        m.d.comb += self.release_ready.eq(self.count > 0)
        m.d.comb += self.acquire_ready.eq(self.count < self.max_count)

        with m.If(self.clear.run):
            m.d.comb += self.count_next.eq(0)
        with m.Else():
            m.d.comb += self.count_next.eq(self.count + self.acquire.run - self.release.run)

        m.d.sync += self.count.eq(self.count_next)

        @def_method(m, self.acquire, ready=self.acquire_ready)
        def _() -> None:
            pass

        @def_method(m, self.release, ready=self.release_ready)
        def _() -> None:
            pass

        @def_method(m, self.clear)
        def _() -> None:
            pass

        return m
