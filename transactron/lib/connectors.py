from collections.abc import Iterable
from amaranth import *
from amaranth.lib.data import View
import amaranth.lib.fifo

from transactron.utils.transactron_helpers import from_method_layout
from ..core import *
from ..utils import SrcLoc, get_src_loc, MethodLayout

__all__ = [
    "FIFO",
    "Forwarder",
    "Connect",
    "ConnectTrans",
    "CrossbarConnectTrans",
    "Pipe",
]


class FIFO(Elaboratable):
    """FIFO module.

    Provides a transactional interface to Amaranth FIFOs. Exposes two methods:
    `read`, and `write`. Both methods are ready only when they can
    be executed -- i.e. the queue is respectively not empty / not full.
    It is possible to simultaneously read and write in a single clock cycle,
    but only if both readiness conditions are fulfilled.

    Attributes
    ----------
    read: Method
        The read method. Accepts an empty argument, returns a structure.
    write: Method
        The write method. Accepts a structure, returns empty result.
    """

    def __init__(
        self, layout: MethodLayout, depth: int, fifo_type=amaranth.lib.fifo.SyncFIFO, *, src_loc: int | SrcLoc = 0
    ):
        """
        Parameters
        ----------
        layout: method layout
            The format of structures stored in the FIFO.
        depth: int
            Size of the FIFO.
        fifoType: Elaboratable
            FIFO module conforming to Amaranth library FIFO interface. Defaults
            to SyncFIFO.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        self.layout = from_method_layout(layout)
        self.width = self.layout.size
        self.depth = depth
        self.fifoType = fifo_type

        src_loc = get_src_loc(src_loc)
        self.read = Method(o=layout, src_loc=src_loc)
        self.write = Method(i=layout, src_loc=src_loc)

    def elaborate(self, platform):
        m = TModule()

        m.submodules.fifo = fifo = self.fifoType(width=self.width, depth=self.depth)

        @def_method(m, self.write, ready=fifo.w_rdy)
        def _(arg):
            m.d.comb += fifo.w_en.eq(1)
            m.d.top_comb += fifo.w_data.eq(arg)

        @def_method(m, self.read, ready=fifo.r_rdy)
        def _():
            m.d.comb += fifo.r_en.eq(1)
            return View(self.layout, fifo.r_data)  # remove View after Amaranth upgrade

        return m


# Forwarding with overflow buffering


class Forwarder(Elaboratable):
    """Forwarding with overflow buffering

    Provides a means to connect two transactions with forwarding. Exposes
    two methods: `read`, and `write`. When both of these methods are
    executed simultaneously, data is forwarded between them. If `write`
    is executed, but `read` is not, the value cannot be forwarded,
    but is stored into an overflow buffer. No further `write`\\s are
    possible until the overflow buffer is cleared by `read`.

    The `write` method is scheduled before `read`.

    Attributes
    ----------
    read: Method
        The read method. Accepts an empty argument, returns a structure.
    write: Method
        The write method. Accepts a structure, returns empty result.
    """

    def __init__(self, layout: MethodLayout, *, src_loc: int | SrcLoc = 0):
        """
        Parameters
        ----------
        layout: method layout
            The format of structures forwarded.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        src_loc = get_src_loc(src_loc)
        self.read = Method(o=layout, src_loc=src_loc)
        self.write = Method(i=layout, src_loc=src_loc)
        self.clear = Method(src_loc=src_loc)
        self.head = Signal.like(self.read.data_out)

        self.clear.add_conflict(self.read, Priority.LEFT)
        self.clear.add_conflict(self.write, Priority.LEFT)

    def elaborate(self, platform):
        m = TModule()

        reg = Signal.like(self.read.data_out)
        reg_valid = Signal()
        read_value = Signal.like(self.read.data_out)
        m.d.comb += self.head.eq(read_value)

        self.write.schedule_before(self.read)  # to avoid combinational loops

        @def_method(m, self.write, ready=~reg_valid)
        def _(arg):
            m.d.av_comb += read_value.eq(arg)  # for forwarding
            m.d.sync += reg.eq(arg)
            m.d.sync += reg_valid.eq(1)

        with m.If(reg_valid):
            m.d.av_comb += read_value.eq(reg)  # write method is not ready

        @def_method(m, self.read, ready=reg_valid | self.write.run)
        def _():
            m.d.sync += reg_valid.eq(0)
            return read_value

        @def_method(m, self.clear)
        def _():
            m.d.sync += reg_valid.eq(0)

        return m


class Pipe(Elaboratable):
    """
    This module implements a `Pipe`. It is a halfway between
    `Forwarder` and `2-FIFO`. In the `Pipe` data is always
    stored localy, so the critical path of the data is cut, but there is a
    combinational path between the control signals of the `read` and
    the `write` methods. For comparison:
    - in `Forwarder` there is both a data and a control combinational path
    - in `2-FIFO` there are no combinational paths

    The `read` method is scheduled before the `write`.

    Attributes
    ----------
    read: Method
        Reads from the pipe. Accepts an empty argument, returns a structure.
        Ready only if the pipe is not empty.
    write: Method
        Writes to the pipe. Accepts a structure, returns empty result.
        Ready only if the pipe is not full.
    clean: Method
        Cleans the pipe. Has priority over `read` and `write` methods.
    """

    def __init__(self, layout: MethodLayout):
        """
        Parameters
        ----------
        layout: record layout
            The format of records forwarded.
        """
        self.read = Method(o=layout)
        self.write = Method(i=layout)
        self.clean = Method()
        self.head = Signal.like(self.read.data_out)

        self.clean.add_conflict(self.read, Priority.LEFT)
        self.clean.add_conflict(self.write, Priority.LEFT)

    def elaborate(self, platform):
        m = TModule()

        reg = Signal.like(self.read.data_out)
        reg_valid = Signal()

        self.read.schedule_before(self.write)  # to avoid combinational loops

        @def_method(m, self.read, ready=reg_valid)
        def _():
            m.d.sync += reg_valid.eq(0)
            return reg

        @def_method(m, self.write, ready=~reg_valid | self.read.run)
        def _(arg):
            m.d.sync += reg.eq(arg)
            m.d.sync += reg_valid.eq(1)

        @def_method(m, self.clean)
        def _():
            m.d.sync += reg_valid.eq(0)

        return m


class Connect(Elaboratable):
    """Forwarding by transaction simultaneity

    Provides a means to connect two transactions with forwarding
    by means of the transaction simultaneity mechanism. It provides
    two methods: `read`, and `write`, which always execute simultaneously.
    Typical use case is for moving data from `write` to `read`, but
    data flow in the reverse direction is also possible.

    Attributes
    ----------
    read: Method
        The read method. Accepts a (possibly empty) structure, returns
        a structure.
    write: Method
        The write method. Accepts a structure, returns a (possibly empty)
        structure.
    """

    def __init__(self, layout: MethodLayout = (), rev_layout: MethodLayout = (), *, src_loc: int | SrcLoc = 0):
        """
        Parameters
        ----------
        layout: method layout
            The format of structures forwarded.
        rev_layout: method layout
            The format of structures forwarded in the reverse direction.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        src_loc = get_src_loc(src_loc)
        self.read = Method(o=layout, i=rev_layout, src_loc=src_loc)
        self.write = Method(i=layout, o=rev_layout, src_loc=src_loc)

    def elaborate(self, platform):
        m = TModule()

        read_value = Signal.like(self.read.data_out)
        rev_read_value = Signal.like(self.write.data_out)

        self.write.simultaneous(self.read)

        @def_method(m, self.write)
        def _(arg):
            m.d.av_comb += read_value.eq(arg)
            return rev_read_value

        @def_method(m, self.read)
        def _(arg):
            m.d.av_comb += rev_read_value.eq(arg)
            return read_value

        return m


class ConnectTrans(Elaboratable):
    """Simple connecting transaction.

    Takes two methods and creates a transaction which calls both of them.
    Result of the first method is connected to the argument of the second,
    and vice versa. Allows easily connecting methods with compatible
    layouts.
    """

    method1: Required[Method]
    method2: Required[Method]

    def __init__(self, i_layout: MethodLayout = (), o_layout: MethodLayout = (), *, src_loc: int | SrcLoc = 0):
        """
        Parameters
        ----------
        i_layout: method layout, optional
            Input layout of `method1`, output layout of `method2`. If not
            provided, empty layout is used.
        o_layout: method layout, optional
            Output layout of `method1`, input layout of `method2`. If not
            provided, empty layout is used.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        self.method1 = Method(i=i_layout, o=o_layout)
        self.method2 = Method(i=o_layout, o=i_layout)
        self.src_loc = get_src_loc(src_loc)

    @staticmethod
    def create(method1: Method, method2: Method, *, src_loc: int | SrcLoc = 0):
        """Creates a `ConnectTrans` which connects two given methods.

        Parameters
        ----------
        method1: Method
            First method.
        method2: Method
            Second method.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        ct = ConnectTrans(method1.layout_in, method1.layout_out, src_loc=get_src_loc(src_loc))
        ct.method1.proxy(method1)
        ct.method2.proxy(method2)
        return ct

    def elaborate(self, platform):
        m = TModule()

        with Transaction(src_loc=self.src_loc).body(m):
            data1 = Signal.like(self.method1.data_out)
            data2 = Signal.like(self.method2.data_out)

            m.d.top_comb += data1.eq(self.method1(m, data2))
            m.d.top_comb += data2.eq(self.method2(m, data1))

        return m


class CrossbarConnectTrans(Elaboratable):
    """Crossbar method connection.

    Connects each of a set of methods to each one from another set using
    separate transactions. Equivalent to a set of `ConnectTrans`.
    """

    methods1: Required[Methods]
    methods2: Required[Methods]

    def __init__(
        self,
        count1: int = 1,
        count2: int = 1,
        i_layout: MethodLayout = (),
        o_layout: MethodLayout = (),
        *,
        src_loc: int | SrcLoc = 0,
    ):
        """
        Parameters
        ----------
        count2: int, optional
            Size of `methods1`. Defaults to 1 if not provided.
        count2: int, optional
            Size of `methods2`. Defaults to 1 if not provided.
        i_layout: method layout
            Input layout of `methods1`, output layout of `methods2`. If not
            provided, empty layout is used.
        o_layout: method layout
            Output layout of `methods1`, input layout of `methods2`. If not
            provided, empty layout is used.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        self.methods1 = Methods(count1, i=i_layout, o=o_layout)
        self.methods2 = Methods(count2, i=o_layout, o=i_layout)
        self.src_loc = get_src_loc(src_loc)

    @staticmethod
    def create(
        methods1: Method | Iterable[Method],
        methods2: Method | Iterable[Method],
        *,
        src_loc: int | SrcLoc = 0,
    ):
        """Creates a `CrossbarConnectTrans` which connects two sets of methods.

        Parameters
        ----------
        method1: Method | Iterable[Method]
            First method or set of methods.
        method2: Method | Iterable[Method]
            Second method or set of methods.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        if isinstance(methods1, Method):
            methods1 = [methods1]
        if isinstance(methods2, Method):
            methods2 = [methods2]

        methods1 = list(methods1)
        methods2 = list(methods2)

        cct = CrossbarConnectTrans(
            src_loc=get_src_loc(src_loc),
            count1=len(methods1),
            count2=len(methods2),
            i_layout=methods1[0].layout_in,
            o_layout=methods1[0].layout_out,
        )
        for cct_method1, method1 in zip(cct.methods1, methods1):
            cct_method1.proxy(method1)
        for cct_method2, method2 in zip(cct.methods2, methods2):
            cct_method2.proxy(method2)
        return cct

    def elaborate(self, platform):
        m = TModule()

        for i, method1 in enumerate(self.methods1):
            for j, method2 in enumerate(self.methods2):
                m.submodules[f"connect_{i}_{j}"] = ConnectTrans.create(method1, method2, src_loc=self.src_loc)

        return m
