from collections.abc import Callable
from dataclasses import dataclass
from inspect import Parameter, signature
from typing import Optional, Protocol, final

from amaranth import *
from amaranth.lib.data import StructLayout
from amaranth_types import ShapeLike, SrcLoc, ValueLike
from amaranth_types.types import HasElaborate

from transactron.core import Method, TModule, def_method
from transactron.lib.connectors import ConnectTrans, Pipe
from transactron.lib.fifo import BasicFifo
from transactron.utils import MethodLayout, from_method_layout
from transactron.utils.assign import AssignArg, AssignType, assign
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
    clear: Method


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
    or a called-method node.

    By default all stages have strict happens-before relationships (if stage A is
    before stage B, then A must complete before B can start). Please note that this
    means that if stage A cannot complete if B is not ready (happens when e.g A and B
    are provided and the external module calls both in the same transaction), than A
    and B are in a deadlock. To break the strict happens-before and allow B to be called
    before the data from earlier stages is provided, you can set no_dependency=True for stage B.
    This allows B to be called once before data arrives and prevents the deadlock. However, it
    also means that B cannot consume any signals from earlier stages (since they may not be ready
    when B is called), so use this option with care.

    The happens-before relationships also apply across data instances: for any two elements
    entering the pipeline in order, that order is preserved at every stage and at the output.

    Attributes
    ----------
    allow_unused : bool
        If ``True``, allows pipeline stages to generate output fields that are not
        consumed by any later stage. Default: ``False``.
    allow_empty : bool
        If ``True``, allows points in the pipeline where no signals are "live"
        (all previous outputs have been consumed). Useful when there are stages
        that fully consume the state and handle it externally and later restore
        the state back into the pipeline. Default: ``False``.
    """

    @dataclass
    class _NodeInfo:
        node: _PipelineNodeProtocol
        src_loc: SrcLoc
        ready: ValueLike
        no_dependency: bool
        forwarder: Callable[[MethodLayout], _ForwarderLike]

    def __init__(self, allow_unused: bool = False, allow_empty: bool = False):
        """Initialize a new pipeline builder.

        Parameters
        ----------
        allow_unused : bool
            If ``True``, allows pipeline stages to generate output fields that are not
            consumed by any later stage. Default: ``False``.
        allow_empty : bool
            If ``True``, allows points in the pipeline where no signals are "live"
            (all previous outputs have been consumed). Useful when there are stages
            that fully consume the state and handle it externally and later restore
            the state back into the pipeline. Default: ``False``.
        """
        self._nodes: list[PipelineBuilder._NodeInfo] = []
        self.allow_unused: bool = allow_unused
        self.allow_empty: bool = allow_empty
        self._live_signal_shapes: dict[str, ShapeLike] = dict()
        self._next_forwarder = None

    def add_external(
        self,
        method: Method,
        *,
        ready: ValueLike = C(1),
        no_dependency: bool = False,
        src_loc: int | SrcLoc = 0,
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
        no_dependency : bool
            If ``True``, this node has no dependencies on prior pipeline state.
            The node will be decoupled from the pipeline. Such nodes cannot have required input signals.
            For more information, see :class:`~transactron.lib.pipeline.PipelineBuilder`.
            Default: ``False``.
        src_loc : int | SrcLoc
            Source location for debugging. Default: ``0``.

        Returns
        -------
        None
        """
        node = _ProvidedMethodNode(method)
        self._add_node(node, ready, no_dependency=no_dependency, src_loc=get_src_loc(src_loc))

    def create_external(
        self,
        *,
        i: MethodLayout,
        o: MethodLayout,
        name: Optional[str] = None,
        src_loc: int | SrcLoc = 0,
        **kwargs,
    ) -> Method:
        """Create a new Method and add a node where the pipeline provides (defines the body of) that Method.

        This is a convenience wrapper around :meth:`add_external` that creates a new Method for you.

        Parameters
        ----------
        i : MethodLayout
            The input layout of the created Method.
        o : MethodLayout
            The output layout of the created Method.
        name : Optional[str]
            Optional name for the created Method. Default: ``None``.
        src_loc : int | SrcLoc
            Source location for debugging. Default: ``0``.
        **kwargs
            Additional keyword arguments are passed to the :ref:add_external node.

        Returns
        -------
        Method
            The created Method.
        """
        src_loc = get_src_loc(src_loc)
        method = Method(name=name, i=i, o=o, src_loc=src_loc)
        self.add_external(method, src_loc=src_loc, **kwargs)
        return method

    def call_method(
        self,
        method: Method,
        *,
        ready: ValueLike = C(1),
        no_dependency: bool = False,
        src_loc: int | SrcLoc = 0,
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
        no_dependency : bool
            If ``True``, this node has no dependencies on prior pipeline state.
            The node will be decoupled from the pipeline. Such nodes cannot have required input signals.
            For more information, see :class:`~transactron.lib.pipeline.PipelineBuilder`.
            Default: ``False``.
        src_loc : int | SrcLoc
            Source location for debugging. Default: ``0``.

        Returns
        -------
        None
        """
        node = _CalledMethodNode(method)
        self._add_node(node, ready, no_dependency=no_dependency, src_loc=get_src_loc(src_loc))

    def stage(
        self,
        m: TModule,
        o: MethodLayout = (),
        *,
        i: Optional[MethodLayout] = None,
        name: Optional[str] = None,
        src_loc: int | SrcLoc = 0,
        **kwargs,
    ) -> Callable:
        """Decorator that registers a function-based pipeline stage.

        This decorator transforms a function into a transactional stage that can be
        added to the pipeline. The decorated function is called inside a transaction
        body and receives pipeline signals as keyword arguments (or as a single ``arg``
        struct) and must return a ``dict`` mapping output field names to Amaranth values,
        or ``None`` when it produces no new fields.

        Parameters
        ----------
        m : TModule
            The module to which the stage method will be added.
        o : MethodLayout
            Layout of the fields *produced* (or overwritten) by this stage.
            Default: no fields produced.
        i : Optional[MethodLayout]
            Layout of the fields *consumed* by this stage. If ``None`` (default),
            the input layout is automatically inferred from the decorated function's
            parameters by matching them to live pipeline signals.
        name : Optional[str]
            Optional name for the stage method. Default: ``None``.
        src_loc : int | SrcLoc
            Source location for debugging. Default: ``0``.
        **kwargs
            Additional keyword arguments are passed to :meth:`call_method` when the stage is registered.

        Returns
        -------
        Callable
            A decorator that wraps the stage function and registers it with the pipeline.
            The decorator can be further modified by :meth:`fifo` before registration.
        """

        src_loc = get_src_loc(src_loc)

        def decorator(func: Callable[..., Optional[AssignArg]]) -> None:
            params = signature(func).parameters
            i_layout_from_pipeline: dict[str, ShapeLike] = dict()
            va_args_type = None
            for p in params.values():
                if p.name == "arg":
                    va_args_type = "named 'arg'"
                    break
                elif p.kind == Parameter.VAR_KEYWORD:
                    va_args_type = "**kwargs"
                    break

                if p.name not in self._live_signal_shapes:
                    raise TypeError(
                        f"Pipeline stage function has parameter {p.name} that is not a live signal in the pipeline"
                    )

                i_layout_from_pipeline[p.name] = self._live_signal_shapes[p.name]

            if va_args_type is not None:
                if i is None:
                    raise TypeError(
                        f"Pipeline stage function cannot have a parameter {va_args_type} "
                        + "when input layout is inferred (i=None)"
                    )

                if len(params) != 1:
                    raise TypeError(
                        f"Pipeline stage function cannot have a parameter {va_args_type} "
                        + "when it is not the only parameter"
                    )

                assert not i_layout_from_pipeline
                i_layout = from_method_layout(i)
                for var in i_layout.members.keys():
                    if var not in self._live_signal_shapes:
                        raise TypeError(
                            f"Pipeline stage function has input layout field {var} that "
                            + "is not a live signal in the pipeline"
                        )
                    i_layout_from_pipeline[var] = self._live_signal_shapes[var]

            if i is not None:
                i_layout = from_method_layout(i)
                # check if the input layout matches the i_layout_from_pipeline
                if i_layout.members != i_layout_from_pipeline:
                    raise TypeError(
                        "Pipeline stage function has an input layout that does not match "
                        "the live signal shapes in the pipeline"
                    )
            else:
                i_layout = from_method_layout(i_layout_from_pipeline.items())

            o_layout = from_method_layout(o)

            method = Method(
                name=name or f"pipeline_stage_{len(self._nodes)}",
                i=i_layout,
                o=o_layout,
                src_loc=src_loc,
            )
            def_method(m, method)(func)

            self.call_method(method, src_loc=src_loc, **kwargs)

        return decorator

    def fifo(self, depth: int, *, src_loc: int | SrcLoc = 0):
        """Insert a FIFO buffer after the current pipeline stage.

        Parameters
        ----------
        depth : int
            The number of elements the FIFO can buffer.
        src_loc : int | SrcLoc
            Source location for debugging. Default: ``0``.

        Returns
        -------
        None

        Raises
        ------
        RuntimeError
            If FIFO is added twice for the same stage.
        """
        src_loc = get_src_loc(src_loc)
        if self._next_forwarder is not None:
            raise RuntimeError("Fifo was added twice for the same stage")
        self._next_forwarder = lambda layout: BasicFifo(layout, depth, src_loc=src_loc)

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
                        f"Pipeline node {i} ({node.src_loc}) generates fields {unused} "
                        "which are not used by any later node"
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
        m = TModule()

        live_types = self.get_live_signals()

        read_methods: list[Optional[Method]] = [None] * len(self._nodes)
        write_methods: list[Optional[Method]] = [None] * len(self._nodes)

        for i in range(len(self._nodes) - 1):
            read_methods[i + 1] = Method(name=f"{i}_pipeline_read", o=live_types[i].items())
            write_methods[i] = Method(name=f"{i}_pipeline_write", i=live_types[i].items())

        for i in range(len(self._nodes)):
            node = self._nodes[i]
            read_method = read_methods[i]
            write_method = write_methods[i]

            in_layout = node.node.get_generated_fields()
            out_layout = node.node.get_required_fields()

            stage_method = Method(name=f"{i}_pipeline_combiner", i=in_layout, o=out_layout)

            @def_method(m, stage_method, ready=node.ready)
            def _(arg):
                in_data = read_method(m) if read_method is not None else dict()
                out_data = Signal(out_layout)
                if out_layout.members:
                    m.d.top_comb += assign(out_data, in_data, fields=AssignType.LHS)

                if write_method is not None:
                    collected = Signal(write_method.layout_in)

                    for k in write_method.layout_in.members.keys():
                        if k in in_layout.members.keys():
                            m.d.top_comb += collected[k].eq(arg[k])
                        else:
                            m.d.top_comb += collected[k].eq(in_data[k])

                    _ = write_method(m, collected)

                return out_data

            if node.no_dependency:
                m.submodules[f"{i}_nodep"] = nodep = Pipe(stage_method.layout_in)
                node.node.finalize(m, nodep.write)
                m.submodules += ConnectTrans.create(nodep.read, stage_method)
            else:
                node.node.finalize(m, stage_method)

        for i in range(1, len(self._nodes)):
            node = self._nodes[i]
            prev_write = write_methods[i - 1]
            curr_read = read_methods[i]
            assert curr_read is not None
            assert prev_write is not None

            m.submodules[f"{i}_forwarder"] = fwd = node.forwarder(prev_write.layout_in)
            prev_write.provide(fwd.write)
            curr_read.provide(fwd.read)

        return m

    def _add_node(
        self,
        node: _PipelineNodeProtocol,
        ready: ValueLike,
        no_dependency: bool,
        src_loc: SrcLoc,
    ) -> None:
        gen = node.get_generated_fields().members
        req = node.get_required_fields().members

        if no_dependency and req:
            raise ValueError("No-dependency nodes cannot have required signals")

        for k, v in req.items():
            if k not in self._live_signal_shapes:
                raise ValueError(f"Signal {k} from {src_loc} is required but not provided")

            if self._live_signal_shapes[k] != v:
                raise ValueError(
                    f"Signal {k} from {src_loc} has incompatible shape: expected {v}, got {self._live_signal_shapes[k]}"
                )

        forwarder = Pipe
        if self._next_forwarder is not None:
            forwarder = self._next_forwarder
            self._next_forwarder = None

        self._nodes.append(
            self._NodeInfo(
                node=node,
                src_loc=src_loc,
                ready=ready,
                no_dependency=no_dependency,
                forwarder=forwarder,
            )
        )

        self._live_signal_shapes.update(gen)
