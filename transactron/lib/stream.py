from amaranth import *
from amaranth.lib import stream, wiring
from amaranth.lib.wiring import In, Out
from amaranth_types import ShapeLike, HasElaborate

from ..core import *
from ..utils import SrcLoc, get_src_loc
from ..utils.data_repr import data_layout


__all__ = [
    "StreamSink",
    "StreamSource",
]


class StreamSink(wiring.Component):
    """Adapter from stream sink to method.

    Stream sink that provides data via method calls.

    Attributes
    ----------
    i: stream.Interface, in
        The input stream interface. The adapter reads from this stream.
    read: Method
        The read method. Returns the data from the stream's payload.
    peek: Method
        The peek method. Returns the data from the stream's payload without
        consuming it.
    """

    i: stream.Interface
    read: Method
    peek: Method

    def __init__(self, shape: ShapeLike, *, src_loc: int | SrcLoc = 0):
        """
        Parameters
        ----------
        shape: ShapeLike
            The shape of the data in the stream.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        super().__init__(
            {
                "i": In(stream.Signature(shape)),
            }
        )

        method_layout = data_layout(shape)

        src_loc = get_src_loc(src_loc)
        self.read = Method(o=method_layout, src_loc=src_loc)
        self.peek = Method(o=method_layout, src_loc=src_loc)

    def elaborate(self, platform):
        m = TModule()

        @def_method(m, self.peek, ready=self.i.valid, nonexclusive=True)
        def _():
            return {"data": self.i.payload}

        @def_method(m, self.read, ready=self.i.valid)
        def _():
            m.d.comb += self.i.ready.eq(1)
            return {"data": self.i.payload}

        return m


class StreamSource(wiring.Component):
    """Adapter from method to stream source.

    Stream source that accepts data via method calls.

    The buffering ensures that:
    - Data from the method call is captured and held stable
    - `valid` remains high until the consumer accepts the data
    - The method can accept new data only when the buffer is empty or being emptied

    Attributes
    ----------
    o: stream.Interface, out
        The output stream interface. The adapter writes to this stream.
    write: Method
        The write method. Accepts data and sends it to the stream.
    """

    o: stream.Interface
    write: Method

    def __init__(self, shape: ShapeLike, *, src_loc: int | SrcLoc = 0):
        """
        Parameters
        ----------
        shape: ShapeLike
            The shape of the data in the stream.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        super().__init__(
            {
                "o": Out(stream.Signature(shape)),
            }
        )

        method_layout = data_layout(shape)

        src_loc = get_src_loc(src_loc)
        self.write = Method(i=method_layout, src_loc=src_loc)

    def elaborate(self, platform):
        m = TModule()

        # Method is ready when buffer is empty or being emptied this cycle
        @def_method(m, self.write, ready=(~self.o.valid | self.o.ready))
        def _(data):
            m.d.sync += self.o.payload.eq(data)
            m.d.sync += self.o.valid.eq(1)

        with m.If(self.o.ready & ~self.write.run):
            # Clear valid when data is accepted and we don't have new data this cycle
            m.d.sync += self.o.valid.eq(0)

        return m


class StreamHasElaborate(HasElaborate):
    """Marker interface for modules wrapped by `StreamModuleWrapper`."""

    i: stream.Interface
    o: stream.Interface


class StreamModuleWrapper(Elaboratable):
    """Wraps a module with stream interfaces to provide method interfaces.

    Attributes
    ----------
    module: StreamHasElaborate
        Underlying Amaranth module with stream interfaces.
    write: Method
        Method to write data to the module's input stream.
    read: Method
        Method to read data from the module's output stream.
    """

    module: StreamHasElaborate
    write: Method
    read: Method

    def __init__(self, module: StreamHasElaborate, *, src_loc: int | SrcLoc = 0):
        """
        Parameters
        ----------
        module: StreamHasElaborate
            The Amaranth module with `i` and `o` stream interfaces.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """

        self.module = module

        src_loc = get_src_loc(src_loc)
        self.write = Method(i=data_layout(module.i.payload.shape()), src_loc=src_loc)
        self.read = Method(o=data_layout(module.o.payload.shape()), src_loc=src_loc)

    def elaborate(self, platform):
        m = TModule()

        m.submodules.module = module = self.module

        m.submodules.source = source = StreamSource(module.i.payload.shape())
        m.submodules.sink = sink = StreamSink(module.o.payload.shape())

        wiring.connect(m.main_module, source.o, module.i)
        wiring.connect(m.main_module, module.o, sink.i)

        @def_method(m, self.write)
        def _(data):
            return source.write(m, data)

        @def_method(m, self.read)
        def _():
            return sink.read(m)

        return m
