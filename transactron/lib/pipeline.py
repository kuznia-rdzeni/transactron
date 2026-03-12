from collections.abc import Callable
from dataclasses import dataclass
from inspect import Parameter, signature
from typing import Optional, Protocol, final

from amaranth import *
from amaranth.lib.data import StructLayout
from amaranth_types import ShapeLike, SrcLoc, ValueLike
from amaranth_types.types import HasElaborate

from transactron.core import Method, TModule, def_method
from transactron.lib.connectors import FIFO, ConnectTrans, Forwarder, Pipe
from transactron.utils import MethodLayout, from_method_layout
from transactron.utils.assign import AssignType, assign
from transactron.utils.transactron_helpers import get_src_loc

__all__ = ["PipelineBuilder"]


# ---------------------------------------------------------------------------
# Node descriptor types
# ---------------------------------------------------------------------------


class _PipelineNodeProtocol(Protocol):
    def get_required_fields(self) -> StructLayout: ...

    def get_generated_fields(self) -> StructLayout: ...

    def finalize(self, m: TModule, method: Method) -> None: ...


class _ForwarderLike(HasElaborate, Protocol):
    read: Method
    write: Method


@final
class _ProvidedMethodNode(_PipelineNodeProtocol):
    """Descriptor for a node where the pipeline provides (defines the body of) a Method."""

    def __init__(self, method: Method):
        self.method = method

    def get_required_fields(self):
        return self.method.layout_out

    def get_generated_fields(self):
        return self.method.layout_in

    def finalize(self, m: TModule, method: Method) -> None:
        self.method.provide(method)


@final
class _CalledMethodNode(_PipelineNodeProtocol):
    """Descriptor for a node where the pipeline calls an existing Method."""

    def __init__(self, method: Method):
        self.method = method

    def get_required_fields(self):
        return self.method.layout_in

    def get_generated_fields(self):
        return self.method.layout_out

    def finalize(self, m: TModule, method: Method) -> None:
        m.submodules += ConnectTrans.create(self.method, method)


# ---------------------------------------------------------------------------
# PipelineBuilder
# ---------------------------------------------------------------------------


class PipelineBuilder(Elaboratable):
    """Helper class for building transactional pipelines.

    Each node in the pipeline can be a function stage, a provided-method node,
    or a called-method node.  Call :meth:`finalize` after adding all nodes to
    build the hardware.

    Parameters
    ----------
    m : TModule
        The module in which the pipeline is elaborated.

    Examples
    --------
    See the module-level docstring for a complete example.
    """

    @dataclass
    class _NodeInfo:
        node: _PipelineNodeProtocol
        ready: ValueLike

        """
        Forwarder factory to connect this node with the previous node.
        """
        forwarder: Callable[[MethodLayout], _ForwarderLike] = Pipe

        no_dependency: bool = False

    def __init__(self, allow_unused: bool = False, allow_empty: bool = False):
        self._nodes: list[PipelineBuilder._NodeInfo] = []
        self.allow_unused: bool = allow_unused
        self.allow_empty: bool = allow_empty
        self._live_signal_shapes: dict[str, ShapeLike] = dict()

    def add_external(
        self,
        method: Method,
        *,
        ready: ValueLike = C(1),
        fifo_depth: Optional[int] = None,
        no_dependency: bool = False,
    ) -> None:
        """Add a node where the pipeline provides (defines the body of) a ``Method``.

        This can be used to define initial source signals, output sink signals,
        or interact with submodules in the middle of the pipeline (e.g. send
        a request to a submodule and later receive the response back into the pipeline).

        Parameters
        ----------
        method : Method
            The ``Method`` whose body the pipeline will define.
        ready : ValueLike
            Additional combinational ready condition (default: always ready).

        Returns
        -------
        None
        """
        node = _ProvidedMethodNode(method)
        info = self._NodeInfo(node, ready, no_dependency=no_dependency)
        if fifo_depth is not None:
            info.forwarder = lambda layout: FIFO(layout, fifo_depth)
        self._add_node(info)

    def create_external(
        self,
        *,
        i: MethodLayout,
        o: MethodLayout,
        ready: ValueLike = C(1),
        fifo_depth: Optional[int] = None,
        no_dependency: bool = False,
        name: Optional[str] = None,
        src_loc: int | SrcLoc = 0,
    ) -> Method:
        """Create a new Method and add a node where the pipeline provides (defines the body of) that Method.

        This is a convenience wrapper around :meth:`add_external` that creates a new Method for you.

        Parameters
        ----------
        i : MethodLayout
            The input layout of the created Method.
        o : MethodLayout
            The output layout of the created Method.
        ready : ValueLike
            Additional combinational ready condition (default: always ready).

        Returns
        -------
        Method
            The created Method.
        """
        src_loc = get_src_loc(src_loc)
        method = Method(name=name, i=i, o=o, src_loc=src_loc)
        self.add_external(method, ready=ready, fifo_depth=fifo_depth, no_dependency=no_dependency)
        return method

    def call_method(
        self,
        method: Method,
        *,
        ready: ValueLike = C(1),
        fifo_depth: Optional[int] = None,
        no_dependency: bool = False,
    ) -> None:
        """Add a node where the pipeline calls an existing ``Method``.

        The method's input fields are taken by name from the current pipeline
        layout. The method's output fields are merged into the pipeline layout.

        Similar to :meth:`add_external`, but of reversed polarity: the pipeline
        is a caller of the method rather than its provider.

        Parameters
        ----------
        method : Method
            The ``Method`` to call.  All of its input fields must be present in
            the pipeline layout at the point where :meth:`finalize` is called.
        ready : ValueLike
            Additional combinational ready condition (default: always ready).
        fifo_depth : int, optional
            If given, inserts a FIFO of this depth *before* this node.

        Returns
        -------
        None
        """
        node = _CalledMethodNode(method)
        info = self._NodeInfo(node, ready, no_dependency=no_dependency)
        if fifo_depth is not None:
            info.forwarder = lambda layout: FIFO(layout, fifo_depth)

        self._add_node(info)

    def stage(
        self,
        m: TModule,
        o: MethodLayout = (),
        *,
        i: Optional[MethodLayout] = None,
        ready: ValueLike = C(1),
        fifo_depth: Optional[int] = None,
        no_dependency: bool = False,
    ) -> Callable:
        """Decorator that register a function as a pipeline

        Decorator that registers a function-based pipeline stage.

        The decorated function is called inside a transaction body.  It receives
        pipeline signals as keyword arguments (or as a single ``arg`` struct) and
        must return a ``dict`` mapping output field names to Amaranth values, or
        ``None`` when it produces no new fields.

        Parameters
        ----------
        o : MethodLayout
            Layout of the fields *produced* (or overwritten) by this stage.
        ready : ValueLike
            Optional combinational ready signal for the stage's transaction.

        Returns
        -------
        Callable
            A decorator that wraps the stage function, returning a
            :class:`_FuncNode` that can be further modified by :meth:`fifo`.
        """

        def decorator(func: Callable) -> None:
            params = signature(func).parameters
            i_layout_from_pipeline: dict[str, ShapeLike] = dict()
            for p in params.values():
                if p.name == "arg":
                    raise TypeError(f"Pipeline stage function {func} cannot have a parameter named 'arg'")
                if p.kind == Parameter.VAR_KEYWORD:
                    raise TypeError(f"Pipeline stage function {func} cannot have **kwargs")

                if p.name not in self._live_signal_shapes:
                    raise TypeError(
                        f"Pipeline stage function {func} has parameter {p.name} "
                        f"that is not a live signal in the pipeline"
                    )

                i_layout_from_pipeline[p.name] = self._live_signal_shapes[p.name]

            if i is not None:
                i_layout = from_method_layout(i)
                # check if the input layout matches the i_layout_from_pipeline
                if i_layout.members != i_layout_from_pipeline:
                    raise TypeError(
                        f"Pipeline stage function {func} has an input layout that does not match "
                        f"the live signal shapes in the pipeline"
                    )
            else:
                i_layout = from_method_layout(i_layout_from_pipeline.items())

            o_layout = from_method_layout(o)

            method = Method(name=f"pipeline_stage_{len(self._nodes)}", i=i_layout, o=o_layout)
            def_method(m, method)(func)

            self.call_method(method, ready=ready, fifo_depth=fifo_depth, no_dependency=no_dependency)

        return decorator

    def get_live_signals(self) -> list[dict[str, ShapeLike]]:
        """Get the live signals at each point in the pipeline, with their types.

        This can be used to inspect the pipeline layout before finalization.

        Returns
        -------
        list[dict[str, ShapeLike]]
            A list of live variable dicts, one per node.  Each dict maps field names to their types.
        """

        live: dict[str, ShapeLike] = dict()
        live_per_node: list[dict[str, ShapeLike]] = []

        for i in reversed(range(len(self._nodes))):
            node = self._nodes[i]
            live_per_node.append(live.copy())

            gen = node.node.get_generated_fields().members
            req = node.node.get_required_fields().members

            if not self.allow_unused:
                # check if we are generating a field that is never used
                unused = gen.keys() - live.keys()
                if unused:
                    raise ValueError(
                        f"Pipeline node {i} generates fields {unused} " f"which are not used by any later node"
                    )

            for k in gen.keys():
                live.pop(k, None)

            live.update(req)

        live_per_node.reverse()

        if not self.allow_empty and not all(live_per_node[:-1]):
            # check if there are any moments in the pipeline where there are no live variables
            # (besides the end)

            # This could be useful to allow only 'done' signals to some later part of the pipeline
            raise ValueError(
                "There are points in the pipeline where there are no live variables. "
                "If this is intentional, set allow_empty=True."
            )

        assert not live_per_node[-1], "There should be no live variables after the end of the pipeline"
        assert len(live_per_node) == len(self._nodes), "There should be one live variable dict per node"

        return live_per_node

    def elaborate(self, platform) -> TModule:
        """Build all transactions and connectors for the pipeline.

        Must be called after all nodes have been added.
        """

        m = TModule()

        live_types = self.get_live_signals()

        live_items_in: MethodLayout = []
        prev_write: Optional[Method] = None

        for i in range(len(self._nodes)):
            node = self._nodes[i]

            prev_read: Optional[Method] = None

            if prev_write is not None:
                m.submodules[f"{i}_forwarder"] = fifo = node.forwarder(live_items_in)
                prev_write.provide(fifo.write)
                prev_read = fifo.read

            live_items_out: MethodLayout = list(live_types[i].items())
            write_output: Optional[Method] = None

            if i != len(self._nodes) - 1:
                write_output = Method(name=f"{i}_write", i=live_items_out)

            in_layout = node.node.get_generated_fields()
            out_layout = node.node.get_required_fields()

            stage_method = Method(name=f"{i}_combiner", i=in_layout, o=out_layout)

            @def_method(m, stage_method, ready=node.ready)
            def _(arg):
                in_data = prev_read(m) if prev_read is not None else dict()
                out_data = Signal(out_layout)
                if out_layout.members:
                    m.d.top_comb += assign(out_data, in_data, fields=AssignType.LHS)

                if write_output is not None:
                    collected = Signal(from_method_layout(live_items_out))

                    for k, _ in live_items_out:
                        if k in in_layout.members.keys():
                            m.d.top_comb += collected[k].eq(arg[k])
                        else:
                            m.d.top_comb += collected[k].eq(in_data[k])

                    _ = write_output(m, collected)

                return out_data

            if node.no_dependency:
                m.submodules[f"{i}_forwarder"] = fwd = Forwarder(node.node.get_generated_fields())
                m.submodules += ConnectTrans.create(fwd.read, stage_method)
                node.node.finalize(m, fwd.write)
            else:
                node.node.finalize(m, stage_method)

            live_items_in = live_items_out
            prev_write = write_output

        return m

    def _add_node(self, node: _NodeInfo) -> None:
        for k, v in node.node.get_required_fields().members.items():
            if k not in self._live_signal_shapes:
                raise ValueError(f"Signal {k} is required but not provided")

            if self._live_signal_shapes[k] != v:
                raise ValueError(f"Signal {k} has incompatible shape: expected {v}, got {self._live_signal_shapes[k]}")

        if node.no_dependency and node.node.get_required_fields().members:
            raise ValueError("No-dependency nodes cannot have required signals")

        self._nodes.append(node)
        self._live_signal_shapes.update(node.node.get_generated_fields().members)
