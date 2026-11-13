from typing import Protocol

from amaranth import *

from transactron.utils.transactron_helpers import from_method_layout
from ..core import *
from ..utils import SrcLoc, get_src_loc, MethodLayout
from .connectors import Forwarder
from transactron.lib import BasicFifo
from amaranth.utils import *

__all__ = [
    "ArgumentsToResultsZipper",
    "PureRequestResponseInterface",
    "RequestResponseInterface",
    "Serializer",
]


class ArgumentsToResultsZipper(Elaboratable):
    r"""Zips arguments used to call method with results, cutting critical path.

    This module provides possibility to pass arguments from caller and connect it with results
    from callee. Arguments are stored in 2-FIFO and results in Forwarder. Because of this asymmetry,
    the callee should provide results as long as they aren't correctly received.

    FIFO is used as rate-limiter, so when FIFO reaches full capacity there should be no new requests issued.

    Example topology:

    .. mermaid::

        graph LR
            Caller -- write_arguments --> 2-FIFO;
            Caller -- invoke --> Callee["Callee \n (1+ cycle delay)"];
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

        self.peek_arg.provide(fifo.peek)

        return m


class RequestResponseInterface(Protocol):
    """Paired request and response methods."""

    request: Provided[Method]
    """Issues a request."""

    response: Provided[Method]
    """Accepts the response to the oldest pending request."""


class PureRequestResponseInterface(RequestResponseInterface):
    """`RequestResponseInterface` with no behavior of its own."""

    def __init__(self, req_layout: MethodLayout, resp_layout: MethodLayout, *, src_loc: int | SrcLoc = 0):
        """
        Parameters
        ----------
        req_layout: method layout
            Input layout of `request`.
        resp_layout: method layout
            Output layout of `response`.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        src_loc = get_src_loc(src_loc)
        self.request = Method(i=req_layout, src_loc=src_loc)
        self.response = Method(o=resp_layout, src_loc=src_loc)

    @staticmethod
    def create(request: Method, response: Method, *, src_loc: int | SrcLoc = 0) -> "PureRequestResponseInterface":
        """Creates a `PureRequestResponseInterface` connected to the given methods.

        Parameters
        ----------
        request: Method
            Method used as `request`. It must not return values.
        response: Method
            Method used as `response`. It must not accept arguments.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        if request.layout_out.size != 0:
            raise ValueError("request must not return values")
        if response.layout_in.size != 0:
            raise ValueError("response must not accept arguments")

        iface = PureRequestResponseInterface(request.layout_in, response.layout_out, src_loc=get_src_loc(src_loc))
        iface.request.provide(request)
        iface.response.provide(response)
        return iface


class Serializer(Elaboratable):
    r"""Module to serialize request-response methods.

    Provides a transactional interface to connect many clients (which request something using a method call)
    with a server, which provides a method to request operation and a method to get a response.

    Requests are being serialized from many clients and forwarded to a server which can process only one request
    at the time. Responses from the server are deserialized and passed to the proper client. `Serializer` assumes
    that responses from the server are in-order, so the order of responses is the same as order of requests.
    """

    ports: Provided[list[RequestResponseInterface]]
    """Client ports."""

    server: Required[RequestResponseInterface]
    """Request and response methods provided by server."""

    def __init__(
        self,
        *,
        port_count: int,
        req_layout: MethodLayout,
        resp_layout: MethodLayout,
        depth: int = 4,
        src_loc: int | SrcLoc = 0,
    ):
        """
        Parameters
        ----------
        port_count: int
            Number of ports which should be generated. `len(ports)=port_count`
        req_layout: method layout
            Input layout of the `request` methods.
        resp_layout: method layout
            Output layout of the `response` methods.
        depth: int
            Number of requests which can be forwarded to server, before server provides first response. Describe
            the resistance of `Serializer` to latency of server in case when server is fully pipelined.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        self.src_loc = get_src_loc(src_loc)
        self.port_count = port_count
        self.server = PureRequestResponseInterface(req_layout, resp_layout, src_loc=self.src_loc)

        self.depth = depth

        self.id_layout = [("id", range(self.port_count))]

        self.clear = Method()
        self.ports = [
            PureRequestResponseInterface(req_layout, resp_layout, src_loc=self.src_loc) for _ in range(port_count)
        ]

    @staticmethod
    def create(
        *,
        port_count: int,
        server: RequestResponseInterface,
        depth: int = 4,
        src_loc: int | SrcLoc = 0,
    ):
        """Creates a `Serializer` connected to the given server.

        Parameters
        ----------
        port_count: int
            Number of ports which should be generated. `len(ports)=port_count`
        server: RequestResponseInterface
            Request and response methods provided by server.
        depth: int
            Number of requests which can be forwarded to server, before server provides first response. Describe
            the resistance of `Serializer` to latency of server in case when server is fully pipelined.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        serializer = Serializer(
            port_count=port_count,
            req_layout=server.request.layout_in,
            resp_layout=server.response.layout_out,
            depth=depth,
            src_loc=get_src_loc(src_loc),
        )
        provide[RequestResponseInterface](serializer.server, server)
        return serializer

    def elaborate(self, platform) -> TModule:
        m = TModule()

        pending_requests = BasicFifo(self.id_layout, self.depth, src_loc=self.src_loc)
        m.submodules.pending_requests = pending_requests

        @def_methods(m, [port.request for port in self.ports])
        def _(i: int, arg):
            pending_requests.write(m, {"id": i})
            self.server.request(m, arg)

        @def_methods(m, [port.response for port in self.ports], ready=lambda i: pending_requests.head.id == i)
        def _(i: int):
            pending_requests.read(m)
            return self.server.response(m)

        self.clear.provide(pending_requests.clear)

        return m
