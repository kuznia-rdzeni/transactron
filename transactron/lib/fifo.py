from typing import Optional
from amaranth import *
import amaranth.lib.memory as memory
import amaranth.lib.data as data
from amaranth_types import ShapeLike, ValueLike, SrcLoc
from transactron import Method, def_method, Priority, TModule
from transactron.utils.typing import MethodLayout, MethodStruct
from transactron.utils.amaranth_ext import mod_incr, rotate_vec_right, rotate_vec_left
from transactron.utils.amaranth_ext.functions import const_of
from transactron.utils.amaranth_ext.shifter import rotate_left
from transactron.utils.transactron_helpers import from_method_layout, get_src_loc


__all__ = ["BasicFifo", "WideFifo", "Semaphore"]


class BasicFifo(Elaboratable):
    """Transactional FIFO queue"""

    read: Method
    """Reads from the FIFO.

    Returns data at the front of the FIFO, as specified by the data layout
    `layout`. Ready only if the FIFO is not empty.

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
    """Returns the element at the front.

    Ready only if the FIFO is not empty. The method is nonexclusive.

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
    """Writes to the FIFO.

    Accepts arguments as specified by the data layout `layout`. Ready only if
    the FIFO is not full.

    Parameters
    ----------
    m: TModule
        Transactron module.
    **kwargs: ValueLike
        Arguments as specified by the data layout.
    """

    clear: Method
    """Clears the FIFO entries.

    The FIFO is empty in the next cycle after this method runs, even when
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
            Layout of data stored in the FIFO.
        depth: int
            Size of the FIFO.
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

        self.read_idx = Signal(range(self.depth))
        self.write_idx = Signal(range(self.depth))
        # current fifo depth
        self.level = Signal(range(self.depth + 1))

    def elaborate(self, platform):
        m = TModule()

        next_read_idx = Signal.like(self.read_idx)
        m.d.comb += next_read_idx.eq(mod_incr(self.read_idx, self.depth))

        m.submodules.data = self.data
        data_wrport = self.data.write_port()
        data_rdport = self.data.read_port(domain="sync", transparent_for=[data_wrport])

        read_ready = Signal()
        write_ready = Signal()

        m.d.comb += read_ready.eq(self.level != 0)
        m.d.comb += write_ready.eq(self.level != self.depth)

        with m.If(self.read.run & ~self.write.run):
            m.d.sync += self.level.eq(self.level - 1)
        with m.If(self.write.run & ~self.read.run):
            m.d.sync += self.level.eq(self.level + 1)
        with m.If(self.clear.run):
            m.d.sync += self.level.eq(0)

        m.d.comb += data_rdport.addr.eq(Mux(self.read.run, next_read_idx, self.read_idx))
        m.d.comb += self.head.eq(data_rdport.data)

        @def_method(m, self.write, ready=write_ready)
        def _(arg: MethodStruct) -> None:
            m.d.top_comb += data_wrport.addr.eq(self.write_idx)
            m.d.top_comb += data_wrport.data.eq(arg)
            m.d.comb += data_wrport.en.eq(1)

            m.d.sync += self.write_idx.eq(mod_incr(self.write_idx, self.depth))

        @def_method(m, self.read, read_ready)
        def _() -> ValueLike:
            m.d.sync += self.read_idx.eq(next_read_idx)
            return self.head

        @def_method(m, self.peek, read_ready, nonexclusive=True)
        def _() -> ValueLike:
            return self.head

        @def_method(m, self.clear)
        def _() -> None:
            m.d.sync += self.read_idx.eq(0)
            m.d.sync += self.write_idx.eq(0)

        return m


class WideFifo(Elaboratable):
    """Transactional FIFO queue which allows reading or writing multiple elements per cycle.

    The `read`, `write` and `peek` methods use the following layout, where `k` denotes the
    maximum number of elements which can be inserted or removed:

    .. highlight:: python
    .. code-block:: python

        StructLayout({"count": range(k+1), "data": ArrayLayout(shape, k)})

    Attributes
    ----------
    read: Method
        Reads from the FIFO. Accepts a single argument `count`, denoting the number of elements
        to be read. Returns `count` elements, or less, if the queue has less than `count` elements.
        Ready only if the FIFO is not empty.
    peek: Method
        Returns the elements at the front (without removing them). Ready only if the FIFO
        is not empty. The method is nonexclusive.
    write: Method
        Writes to the FIFO. Ready only if the FIFO has enough free slots to accept the write.
    clear: Method
        Clears the FIFO entries. Has priority over `read` and `write` methods.
    """

    class Data:
        """Allows to access internal data in simulation."""

        def __init__(self, fifo: "WideFifo"):
            self._fifo = fifo

        def __getitem__(self, index: int):
            return self._fifo._storage[index % self._fifo.col_count].data[index // self._fifo.col_count]

    def __init__(
        self,
        shape: ShapeLike,
        depth: int,
        read_width: int,
        write_width: Optional[int] = None,
        *,
        src_loc: int | SrcLoc = 0,
    ) -> None:
        """
        Parameters
        ----------
        shape: ShapeLike
            Shape of the data stored in the queue.
        depth: int
            Depth of the FIFO. Must be a multiple of `max(read_width, write_width)`.
        read_width: int
            Number of elements which can be simultaneously read from the queue.
        write_width: int, optional
            Number of elements which can be simultaneously written to the queue.
            If omitted, it is assumed to be equal to `read_width`.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        if write_width is None:
            write_width = read_width

        self.read_width = read_width
        self.write_width = write_width
        self.col_count = max(read_width, write_width)
        self.depth = depth

        if depth % self.col_count != 0:
            raise ValueError(f"WideFifo depth {depth} not a multiple of {self.col_count}")

        self.shape = shape
        self.read_layout = data.StructLayout(
            {"count": range(read_width + 1), "data": data.ArrayLayout(shape, read_width)}
        )
        self.write_layout = data.StructLayout(
            {"count": range(write_width + 1), "data": data.ArrayLayout(shape, write_width)}
        )
        self.idx_layout = data.StructLayout(
            {"col": range(self.col_count), "row": range(self.row_count)}  # col less significant for monotonicity
        )
        self.read = Method(i=[("count", range(read_width + 1))], o=self.read_layout, src_loc=src_loc)
        self.peek = Method(o=self.read_layout, src_loc=src_loc)
        self.write = Method(i=self.write_layout, src_loc=src_loc)
        self.clear = Method(src_loc=src_loc)

        self.read_idx = Signal(self.idx_layout)
        self.write_idx = Signal(self.idx_layout)

        self.data = WideFifo.Data(self)

    @property
    def row_count(self):
        return self.depth // self.col_count

    def elaborate(self, platform):
        m = TModule()

        col_count = self.col_count
        row_count = self.row_count

        self._storage = [memory.Memory(shape=self.shape, depth=row_count, init=[]) for _ in range(col_count)]

        for i, mem in enumerate(self._storage):
            m.submodules[f"storage{i}"] = mem

        write_ports = [mem.write_port() for mem in self._storage]
        read_ports = [
            mem.read_port(domain="sync", transparent_for=[port]) for mem, port in zip(self._storage, write_ports)
        ]

        write_idx = self.write_idx
        read_idx = self.read_idx

        next_read_idx = Signal(self.idx_layout)

        incr_read_row = Signal(range(row_count))
        incr_next_read_row = Signal(range(row_count))
        incr_write_row = Signal(range(row_count))

        level = Signal(range(col_count * row_count + 1))
        remaining = Signal(range(col_count * row_count + 1))

        read_available = Signal(range(self.read_width + 1))
        write_available = Signal(range(self.write_width + 1))

        read_count = Signal(range(self.read_width + 1))
        write_count = Signal(range(self.write_width + 1))

        m.d.comb += incr_read_row.eq(mod_incr(read_idx.row, row_count))
        m.d.comb += incr_next_read_row.eq(mod_incr(next_read_idx.row, row_count))
        m.d.comb += incr_write_row.eq(mod_incr(write_idx.row, row_count))

        m.d.sync += level.eq(level - read_count + write_count)
        m.d.comb += remaining.eq(col_count * row_count - level)

        m.d.comb += read_available.eq(Mux(level > self.read_width, self.read_width, level))
        m.d.comb += write_available.eq(Mux(remaining > self.write_width, self.write_width, remaining))

        for i, port in enumerate(read_ports):
            m.d.comb += port.addr.eq(Mux(i >= next_read_idx.col, next_read_idx.row, incr_next_read_row))

        for i, port in enumerate(write_ports):
            m.d.comb += port.addr.eq(Mux(i >= write_idx.col, write_idx.row, incr_write_row))

        read_data = [port.data for port in read_ports]
        head = rotate_vec_right(read_data, read_idx.col)[: self.read_width]

        head_sig = [Signal.like(item) for item in head]
        for item_sig, item in zip(head_sig, head):
            m.d.comb += item_sig.eq(item)

        def incr_row_col(idx: data.View, incr_row: Value, count: Value):
            chg_idx = Signal(self.idx_layout)
            with m.If(idx.col + count >= col_count):
                m.d.comb += chg_idx.row.eq(incr_row)
                m.d.comb += chg_idx.col.eq(idx.col + count - col_count)
            with m.Else():
                m.d.comb += chg_idx.row.eq(idx.row)
                m.d.comb += chg_idx.col.eq(idx.col + count)
            return chg_idx

        @def_method(m, self.write, remaining != 0, validate_arguments=lambda count, data: count <= remaining)
        def _(count, data):
            ext_data = list(data) + [const_of(0, self.shape)] * (col_count - self.write_width)
            shifted_data = rotate_vec_left(ext_data, write_idx.col)
            ens = Signal(col_count)
            m.d.comb += ens.eq(Cat(i < count for i in range(col_count)))
            m.d.comb += Cat(port.en for port in write_ports).eq(rotate_left(ens, write_idx.col))
            m.d.av_comb += [write_ports[i].data.eq(shifted_data[i]) for i in range(col_count)]
            m.d.comb += write_count.eq(count)
            m.d.sync += write_idx.eq(incr_row_col(write_idx, incr_write_row, count))

        # The next_read_idx signals contain the value written to read_idx registers in the next cycle.
        # They following assignments are the defaults, which are overridden in the read method.
        m.d.comb += next_read_idx.eq(read_idx)
        m.d.sync += read_idx.eq(next_read_idx)

        @def_method(m, self.read, level != 0)
        def _(count):
            m.d.comb += read_count.eq(Mux(count > read_available, read_available, count))
            m.d.comb += next_read_idx.eq(incr_row_col(read_idx, incr_read_row, read_count))
            return {"count": read_count, "data": head}

        @def_method(m, self.peek, level != 0, nonexclusive=True)
        def _():
            return {"count": read_available, "data": head}

        @def_method(m, self.clear)
        def _() -> None:
            m.d.sync += write_idx.eq(0)
            m.d.sync += read_idx.eq(0)
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

        self.count = Signal(range(self.max_count + 1))
        self.count_next = Signal(range(self.max_count + 1))

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
