from collections.abc import Callable
from dataclasses import dataclass
from inspect import Parameter, signature
from typing import Optional, Protocol, final

from amaranth import *
from amaranth.lib.data import StructLayout
from amaranth_types import ShapeLike, ValueLike

from transactron.core import Method, TModule, def_method
from transactron.lib.connectors import FIFO, ConnectTrans, Forwarder, Pipe
from transactron.utils import AssignType, MethodLayout, assign, from_method_layout

__all__ = ["PipelineBuilder"]


# ---------------------------------------------------------------------------
# Node descriptor types
# ---------------------------------------------------------------------------


class _PipelineNodeProtocol(Protocol):
    def get_required_fields(self) -> dict[str, Optional[ShapeLike]]: ...

    def get_generated_fields(self) -> dict[str, ShapeLike]: ...

    def get_final_required_fields(
        self, inputs: list[tuple[str, ShapeLike]]
    ) -> MethodLayout: ...

    def get_final_generated_fields(
        self, inputs: list[tuple[str, ShapeLike]]
    ) -> MethodLayout: ...

    def finalize(self, m: TModule, method: Method) -> None: ...


@final
class _FuncNode(_PipelineNodeProtocol):
    """Descriptor for a function-based pipeline stage."""

    def __init__(
        self,
        m: TModule,
        func: Callable,
        o: MethodLayout,
        i: Optional[MethodLayout] = None,
    ):
        # check if there is no 'arg' parameter or **kwargs
        params = signature(func).parameters
        for p in params.values():
            if p.name == "arg":
                raise TypeError(
                    f"Pipeline stage function {func} cannot have a parameter named 'arg'"
                )
            if p.kind == Parameter.VAR_KEYWORD:
                raise TypeError(f"Pipeline stage function {func} cannot have **kwargs")

        self.m = m
        self.func = func
        self.o_layout = from_method_layout(o)
        self.i_layout = from_method_layout(i) if i is not None else None

        if self.i_layout is not None:
            # check if we have all the required by the function signature inputs are present
            for p in params.values():
                if p.kind in (Parameter.POSITIONAL_OR_KEYWORD, Parameter.KEYWORD_ONLY):
                    if p.name not in self.i_layout.members:
                        raise TypeError(
                            f"Pipeline stage function {func} has parameter '{p.name}' "
                            f"which is not present in the input layout {self.i_layout}"
                        )

    def get_required_fields(self) -> dict[str, Optional[ShapeLike]]:
        if self.i_layout is not None:
            return {k: v for k, v in self.i_layout.members.items()}

        params = signature(self.func).parameters

        return {
            k: None
            for k, p in params.items()
            if p.kind in (Parameter.POSITIONAL_OR_KEYWORD, Parameter.KEYWORD_ONLY)
        }

    def get_generated_fields(self) -> dict[str, ShapeLike]:
        return {k: v for k, v in self.o_layout.members.items()}

    def get_final_required_fields(self, inputs) -> MethodLayout:
        if self.i_layout is not None:
            return self.i_layout

        # extract only the required fields from the input layout
        required_fields = self.get_required_fields().keys()
        return [(k, v) for k, v in inputs if k in required_fields]

    def get_final_generated_fields(self, inputs) -> MethodLayout:
        return self.o_layout

    def finalize(self, m: TModule, method: Method) -> None:
        fun_method = Method(i=method.layout_out, o=method.layout_in)
        def_method(self.m, fun_method)(self.func)
        m.submodules += ConnectTrans.create(fun_method, method)


@final
class _ProvidedMethodNode(_PipelineNodeProtocol):
    """Descriptor for a node where the pipeline provides (defines the body of) a Method."""

    def __init__(self, method: Method, no_dependency: bool = False):
        self.method = method
        self.no_dependency = no_dependency

    def get_required_fields(self) -> dict[str, Optional[ShapeLike]]:
        method_out = from_method_layout(self.method.layout_out)
        return {k: v for k, v in method_out.members.items()}

    def get_generated_fields(self) -> dict[str, ShapeLike]:
        method_in = from_method_layout(self.method.layout_in)
        return method_in.members

    def get_final_required_fields(self, inputs) -> MethodLayout:
        return self.method.layout_out

    def get_final_generated_fields(self, inputs) -> MethodLayout:
        return self.method.layout_in

    def finalize(self, m: TModule, method: Method) -> None:
        if self.no_dependency:
            fwd = Forwarder(self.method.layout_in)
            m.submodules += fwd
            self.method.provide(fwd.write)
            m.submodules += ConnectTrans.create(fwd.read, method)
        else:
            self.method.provide(method)


@final
class _CalledMethodNode(_PipelineNodeProtocol):
    """Descriptor for a node where the pipeline calls an existing Method."""

    def __init__(self, method: Method, no_dependency: bool = False):
        self.method = method
        self.no_dependency = no_dependency

    def get_required_fields(self) -> dict[str, Optional[ShapeLike]]:
        method_in = from_method_layout(self.method.layout_in)
        return {k: v for k, v in method_in.members.items()}

    def get_generated_fields(self) -> dict[str, ShapeLike]:
        method_out = from_method_layout(self.method.layout_out)
        return method_out.members

    def get_final_required_fields(self, inputs) -> MethodLayout:
        return self.method.layout_in

    def get_final_generated_fields(self, inputs) -> MethodLayout:
        return self.method.layout_out

    def finalize(self, m: TModule, method: Method) -> None:
        if self.no_dependency:
            # add result Forwarder right after the method
            fwd = Forwarder(self.method.layout_out)
            m.submodules += fwd
            m.submodules += ConnectTrans.create(self.method, fwd.write)
            m.submodules += ConnectTrans.create(fwd.read, method)
        else:
            m.submodules += ConnectTrans.create(self.method, method)


# ---------------------------------------------------------------------------
# PipelineBuilder
# ---------------------------------------------------------------------------


class PipelineBuilder:
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
        Fifo depth before this node
        """
        fifo_depth: Optional[int]

    def __init__(self, allow_unused: bool = False, allow_empty: bool = False):
        self._nodes: list[PipelineBuilder._NodeInfo] = []
        self.allow_unused: bool = allow_unused
        self.allow_empty: bool = allow_empty

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
        fifo_depth : int, optional
            If given, inserts a FIFO of this depth *before* this node.

        Returns
        -------
        None
        """
        node = _ProvidedMethodNode(method, no_dependency)
        self._nodes.append(self._NodeInfo(node, ready, fifo_depth))

    def create_external(
        self,
        *,
        i: MethodLayout,
        o: MethodLayout,
        ready: ValueLike = C(1),
        fifo_depth: Optional[int] = None,
        no_dependency: bool = False,
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
        method = Method(i=i, o=o)
        self.add_external(
            method, ready=ready, fifo_depth=fifo_depth, no_dependency=no_dependency
        )
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
        node = _CalledMethodNode(method, no_dependency)
        self._nodes.append(self._NodeInfo(node, ready, fifo_depth))

    def stage(
        self,
        m: TModule,
        o: MethodLayout = (),
        *,
        ready: ValueLike = C(1),
        fifo_depth: Optional[int] = None,
    ) -> Callable:
        """Decorator that register a function as a pipeline

        Decorator that registers a function-based pipeline stage.

        The decorated function is called inside a transaction body.  It receives
        pipeline signals as keyword arguments (or as a single ``arg`` struct) and
        must return a ``dict`` mapping output field names to Amaranth values, or
        ``None`` / ``{}`` when it produces no new fields.

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

        def decorator(func: Callable) -> _FuncNode:
            o_layout = from_method_layout(o)
            node = _FuncNode(m, func, o_layout)
            self._nodes.append(self._NodeInfo(node, ready, fifo_depth))
            return node

        return decorator

    def get_live_signals(self) -> list[dict[str, ShapeLike]]:
        """Get the live signals at each point in the pipeline, with their types.

        This can be used to inspect the pipeline layout before finalization, or to
        build custom connectors manually.

        Parameters
        ----------
        allow_unused : bool
            If True, allows fields to be generated without being used by any later node.
        allow_empty : bool
            If False, raises an error if there are any points in the pipeline (except the end) where there are no live variables.

        Returns
        -------
        list[dict[str, RecordDict]]
            A list of live variable dicts, one per node.  Each dict maps field names to their types.
        """

        live: set[str] = set()
        live_per_node = []

        gen = [node.node.get_generated_fields() for node in self._nodes]
        req = [node.node.get_required_fields() for node in self._nodes]

        for i in reversed(range(len(self._nodes))):
            live_per_node.append(live.copy())

            gen_set = set(gen[i].keys())
            req_set = set(req[i].keys())

            if not self.allow_unused:
                # check if we are generating a field that is never used
                unused = gen_set - live
                if unused:
                    raise ValueError(
                        f"Pipeline node {i} generates fields {unused} "
                        f"which are not used by any later node"
                    )

            live -= gen_set
            live |= req_set

        live_per_node.reverse()

        if not self.allow_empty and not all(live_per_node[:-1]):
            # check if there are any moments in the pipeline where there are no live variables
            # (besides the end)

            # This could be useful to allow only 'done' signals to some later part of the pipeline
            raise ValueError(
                "There are points in the pipeline where there are no live variables. "
                "If this is intentional, set allow_empty=True."
            )

        # now attach types to the used keys
        live_vars: list[dict[str, ShapeLike]] = []
        live_t: dict[str, ShapeLike] = dict()

        for i in range(len(self._nodes)):
            for k in req[i]:
                # check for missing input fields
                if k not in live_t.keys():
                    raise ValueError(
                        f"Pipeline node {i} requires field '{k}' "
                        f"which is not generated by any node"
                    )

                # check if the shape matches
                req_shape = req[i][k]
                if req_shape is not None and live_t[k] != req_shape:
                    raise ValueError(
                        f"Pipeline node {i} requires field '{k}' "
                        f"with shape {req_shape}, but it has shape {live_t[k]}"
                    )

            live_t.update(gen[i])
            # only preserve fields that are still live after this node
            live_t = {k: v for k, v in live_t.items() if k in live_per_node[i]}
            live_vars.append(live_t.copy())

        assert not live_vars[-1], (
            "There should be no live variables after the end of the pipeline"
        )
        assert len(live_vars) == len(self._nodes), (
            "There should be one live variable dict per node"
        )

        return live_vars

    def finalize(self) -> TModule:
        """Build all transactions and connectors for the pipeline.

        Must be called after all nodes have been added.
        """

        if not self._nodes:
            return TModule()

        m = TModule()

        live_types = self.get_live_signals()

        live_items_in: MethodLayout = []
        prev_read: Optional[Method] = None

        for i in range(len(self._nodes)):
            node = self._nodes[i]

            if prev_read is not None and node.fifo_depth:
                m.submodules[f"{i}_fifo"] = fifo = FIFO(
                    layout=live_items_in, depth=node.fifo_depth
                )
                m.submodules += ConnectTrans.create(prev_read, fifo.write)
                prev_read = fifo.read

            live_items_out: MethodLayout = list(live_types[i].items())
            output_pipe: Optional[Pipe] = None

            if i != len(self._nodes) - 1:
                m.submodules[f"{i}_pipe"] = output_pipe = Pipe(layout=live_items_out)

            in_layout = node.node.get_final_generated_fields(live_items_in)
            out_layout = node.node.get_final_required_fields(live_items_in)

            stage_method = Method(name=f"{i}_combiner", i=in_layout, o=out_layout)

            generated_names = [k for k, _ in in_layout]

            @def_method(m, stage_method, ready=node.ready)
            def _(arg):
                in_data = prev_read(m) if prev_read is not None else dict()
                out_data = {k: in_data[k] for k, _ in out_layout}

                if output_pipe is not None:
                    collected = Signal(from_method_layout(live_items_out))

                    for k, _ in live_items_out:
                        if k in generated_names:
                            m.d.top_comb += collected[k].eq(arg[k])
                        else:
                            m.d.top_comb += collected[k].eq(in_data[k])

                    output_pipe.write(m, collected)

                return out_data

            node.node.finalize(m, stage_method)

            live_items_in = live_items_out
            prev_read = output_pipe.read if output_pipe is not None else None

        return m
