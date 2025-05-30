from amaranth import *

from transactron.utils.transactron_helpers import from_method_layout
from ..core import *
from ..utils import SrcLoc, get_src_loc, MethodLayout
from .connectors import Forwarder
from transactron.lib import BasicFifo
from amaranth.utils import *

__all__ = [
    "ArgumentsToResultsZipper",
    "Serializer",
]


class ArgumentsToResultsZipper(Elaboratable):
    """Zips arguments used to call method with results, cutting critical path.

    This module provides possibility to pass arguments from caller and connect it with results
    from callee. Arguments are stored in 2-FIFO and results in Forwarder. Because of this asymmetry,
    the callee should provide results as long as they aren't correctly received.

    FIFO is used as rate-limiter, so when FIFO reaches full capacity there should be no new requests issued.

    Example topology:

    .. mermaid::

        graph LR
            Caller -- write_arguments --> 2-FIFO;
            Caller -- invoke --> Callee["Callee \\n (1+ cycle delay)"];
            Callee -- write_results --> Forwarder;
            Forwarder -- read --> Zip;
            2-FIFO -- read --> Zip;
            Zip -- read --> User;
            subgraph ArgumentsToResultsZipper
                Forwarder;
                2-FIFO;
                Zip;
            end

    Attributes
    ----------
    peek_arg: Method
        A nonexclusive method to read (but not delete) the head of the arg queue.
    write_args: Method
        Method to write arguments with `args_layout` format to 2-FIFO.
    write_results: Method
        Method to save results with `results_layout` in the Forwarder.
    read: Method
        Reads latest entries from the fifo and the forwarder and return them as
        a structure with two fields: 'args' and 'results'.
    """

    def __init__(self, args_layout: MethodLayout, results_layout: MethodLayout, src_loc: int | SrcLoc = 0):
        """
        Parameters
        ----------
        args_layout: method layout
            The format of arguments.
        results_layout: method layout
            The format of results.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        self.src_loc = get_src_loc(src_loc)
        self.results_layout = from_method_layout(results_layout)
        self.args_layout = from_method_layout(args_layout)
        self.output_layout = [("args", self.args_layout), ("results", results_layout)]

        self.peek_arg = Method(o=self.args_layout, src_loc=self.src_loc)
        self.write_args = Method(i=self.args_layout, src_loc=self.src_loc)
        self.write_results = Method(i=self.results_layout, src_loc=self.src_loc)
        self.read = Method(o=self.output_layout, src_loc=self.src_loc)

    def elaborate(self, platform):
        m = TModule()

        fifo = BasicFifo(self.args_layout, depth=2, src_loc=self.src_loc)
        forwarder = Forwarder(self.results_layout, src_loc=self.src_loc)

        m.submodules.fifo = fifo
        m.submodules.forwarder = forwarder

        @def_method(m, self.write_args)
        def _(arg):
            fifo.write(m, arg)

        @def_method(m, self.write_results)
        def _(arg):
            forwarder.write(m, arg)

        @def_method(m, self.read)
        def _():
            args = fifo.read(m)
            results = forwarder.read(m)
            return {"args": args, "results": results}

        self.peek_arg.proxy(m, fifo.peek)

        return m


class Serializer(Elaboratable):
    """Module to serialize request-response methods.

    Provides a transactional interface to connect many client `Module`\\s (which request somethig using method call)
    with a server `Module` which provides method to request operation and method to get response.

    Requests are being serialized from many clients and forwarded to a server which can process only one request
    at the time. Responses from server are deserialized and passed to proper client. `Serializer` assumes, that
    responses from the server are in-order, so the order of responses is the same as order of requests.


    Attributes
    ----------
    serialize_in: Methods
        Request methods. Data layouts are the same as for `serialized_req_method`.
    serialize_out: Methods
        Response methods. Data layouts are the same as for `serialized_resp_method`.
        `i`-th response method provides responses for requests from `i`-th `serialize_in` method.
    """

    def __init__(
        self,
        *,
        port_count: int,
        serialized_req_method: Method,
        serialized_resp_method: Method,
        depth: int = 4,
        src_loc: int | SrcLoc = 0,
    ):
        """
        Parameters
        ----------
        port_count: int
            Number of ports, which should be generated. `len(serialize_in)=len(serialize_out)=port_count`
        serialized_req_method: Method
            Request method provided by server's `Module`.
        serialized_resp_method: Method
            Response method provided by server's `Module`.
        depth: int
            Number of requests which can be forwarded to server, before server provides first response. Describe
            the resistance of `Serializer` to latency of server in case when server is fully pipelined.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        if serialized_req_method.layout_out.size != 0:
            raise ValueError("serialized_req_method must not return values")
        if serialized_resp_method.layout_in.size != 0:
            raise ValueError("serialized_resp_method must not accept arguments")

        self.src_loc = get_src_loc(src_loc)
        self.port_count = port_count
        self.serialized_req_method = serialized_req_method
        self.serialized_resp_method = serialized_resp_method

        self.depth = depth

        self.id_layout = [("id", range(self.port_count))]

        self.clear = Method()
        self.serialize_in = Methods(port_count, i=serialized_req_method.layout_in, src_loc=self.src_loc)
        self.serialize_out = Methods(port_count, o=serialized_resp_method.layout_out, src_loc=self.src_loc)

    def elaborate(self, platform) -> TModule:
        m = TModule()

        pending_requests = BasicFifo(self.id_layout, self.depth, src_loc=self.src_loc)
        m.submodules.pending_requests = pending_requests

        for i in range(self.port_count):

            @def_method(m, self.serialize_in[i])
            def _(arg):
                pending_requests.write(m, {"id": i})
                self.serialized_req_method(m, arg)

            @def_method(m, self.serialize_out[i], ready=(pending_requests.head.id == i))
            def _():
                pending_requests.read(m)
                return self.serialized_resp_method(m)

        self.clear.proxy(m, pending_requests.clear)

        return m
