"""
A pipeline builder helper class.

Every node in the pipeline can be:

- A **function stage** (``@p.stage``): a transaction that applies a transform function.
- A **provided-method node** (``p.add_external``): the pipeline defines the body of an
  external ``Method``.  The method can appear at any position:

  - *First node* — the method's input layout seeds the pipeline.  When called from
    outside, data is injected into the pipeline.
  - *Middle node* — reads data from the previous stage, optionally merges the method's
    inputs, optionally returns pipeline fields to the caller, then passes data forward.
  - *Last node* — reads data from the previous stage and returns pipeline fields to
    the caller.

- A **called-method node** (``p.call_method``): the pipeline calls an existing ``Method``
  using the current pipeline fields as inputs; the method's outputs are merged back into
  the pipeline.

Call ``p.finalize()`` after adding all nodes to build the hardware.

Signals can be overwritten by later stages by specifying the same name in the output
layout.  Unused signals are automatically passed through.

Example usage::

    class Submodule(Elaboratable):
        write: Method
        read: Method
        ...

    class SomeModule(Elaboratable):
        def __init__(self):
            in_layout  = [("val1", unsigned(8)), ("val2", unsigned(16))]
            out_layout = [("out1", unsigned(8)), ("out2", unsigned(16))]
            self.write = Method(i=in_layout)
            self.read  = Method(o=out_layout)

        def elaborate(self, platform):
            m = TModule()

            stage2_ready = Signal()
            m.d.sync += stage2_ready.eq(~stage2_ready)

            m.submodules.sub = sub = Submodule()

            p = PipelineBuilder(m)

            # Entry: callers inject data into the pipeline via self.write
            p.add_external(self.write)

            @p.stage(o=[("out1", unsigned(8))])
            def _(val1):
                return {"out1": val1 + 1}

            # Call sub.write with matching pipeline fields (no manual stage needed)
            p.call_method(sub.write)

            @p.stage(o=[("out2", unsigned(16))], ready=stage2_ready)
            def _(val2):
                return {"out2": val2 - 1}

            # FIFO before the next stage to allow >=4 concurrent sub.read calls
            @p.fifo(depth=4)
            @p.stage(o=[("out1", unsigned(8))])
            def _():
                r = sub.read(m)
                return {"out1": r.out1 + 1}

            # Middle exit: callers can read intermediate results; pipeline continues
            p.add_external(self.intermediate_out)

            @p.stage(o=[])
            def _():
                return {}

            # Final exit: callers pull results from the end of the pipeline
            p.add_external(self.read)

            p.finalize()
            return m
"""

from amaranth import *
from amaranth.lib.data import StructLayout
from collections.abc import Callable
from inspect import signature, Parameter
from typing import Optional, Union

from amaranth_types import ValueLike

from transactron.core import Method, TModule, def_method, Transaction
from transactron.utils import from_method_layout, MethodLayout, RecordDict, assign, AssignType
from transactron.lib.connectors import Pipe, FIFO

__all__ = ["PipelineBuilder"]


# ---------------------------------------------------------------------------
# Node descriptor types
# ---------------------------------------------------------------------------


class _FuncNode:
    """Descriptor for a function-based pipeline stage."""

    def __init__(self, func: Callable, o_layout: StructLayout, ready: ValueLike):
        self.func = func
        self.o_layout = o_layout
        self.ready = ready
        self.fifo_depth: Optional[int] = None


class _ProvidedMethodNode:
    """Descriptor for a node where the pipeline provides (defines the body of) a Method."""

    def __init__(self, method: Method, ready: ValueLike = C(1), fifo_depth: Optional[int] = None):
        self.method = method
        self.ready = ready
        self.fifo_depth = fifo_depth


class _CalledMethodNode:
    """Descriptor for a node where the pipeline calls an existing Method."""

    def __init__(self, method: Method, ready: ValueLike = C(1), fifo_depth: Optional[int] = None):
        self.method = method
        self.ready = ready
        self.fifo_depth = fifo_depth


_PipelineNode = Union[_FuncNode, _ProvidedMethodNode, _CalledMethodNode]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _call_stage_func(func: Callable, in_data) -> Optional[RecordDict]:
    """Call a stage function, passing only the fields it needs from ``in_data``.

    Supports three calling conventions:

    - ``def _(arg):`` - receives the full struct as a single positional argument.
    - ``def _(field1, field2):`` - receives only the named pipeline fields.
    - ``def _(**kwargs):`` - receives all pipeline fields as keyword arguments.
    """
    params = signature(func).parameters

    if not params:
        return func()

    first_param = next(iter(params.values()))
    if first_param.name == "arg" and first_param.kind in (
        Parameter.POSITIONAL_OR_KEYWORD,
        Parameter.POSITIONAL_ONLY,
    ):
        return func(in_data)

    available = {k: in_data[k] for k in in_data.shape().members}

    has_var_kw = any(p.kind == Parameter.VAR_KEYWORD for p in params.values())
    if has_var_kw:
        return func(**available)

    needed = {
        k for k, p in params.items() if p.kind in (Parameter.POSITIONAL_OR_KEYWORD, Parameter.KEYWORD_ONLY)
    }
    missing = needed - available.keys()
    if missing:
        raise TypeError(
            f"Stage function requires fields {missing} which are not present in the pipeline layout "
            f"{list(available.keys())}"
        )
    return func(**{k: available[k] for k in needed})


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

    def __init__(self, m: TModule):
        self._m = m
        self._nodes: list[_PipelineNode] = []

    def add_external(
        self,
        method: Method,
        *,
        ready: ValueLike = C(1),
        fifo_depth: Optional[int] = None,
    ) -> _ProvidedMethodNode:
        """Add a node where the pipeline provides (defines the body of) a ``Method``.

        The method can be placed at any position in the pipeline:

        - **First node**: the method's input layout defines the initial pipeline
          fields.  When the method is called from outside, data is injected into
          the pipeline.
        - **Middle node**: reads from the previous stage, optionally merges the
          method's inputs into the pipeline, optionally returns pipeline fields
          to the caller, then passes data forward to the next stage.
        - **Last node**: reads from the previous stage and returns the requested
          pipeline fields to the caller.

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
        _ProvidedMethodNode
            The node descriptor.
        """
        node = _ProvidedMethodNode(method, ready, fifo_depth)
        self._nodes.append(node)
        return node

    def call_method(
        self,
        method: Method,
        *,
        ready: ValueLike = C(1),
        fifo_depth: Optional[int] = None,
    ) -> _CalledMethodNode:
        """Add a node where the pipeline calls an existing ``Method``.

        The method's input fields are taken by name from the current pipeline
        layout.  The method's output fields are merged into the pipeline layout.

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
        _CalledMethodNode
            The node descriptor.
        """
        node = _CalledMethodNode(method, ready, fifo_depth)
        self._nodes.append(node)
        return node

    def stage(self, o: MethodLayout = (), *, ready: ValueLike = C(1)) -> Callable:
        """Decorator that registers a function-based pipeline stage.

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
            node = _FuncNode(func, o_layout, ready)
            self._nodes.append(node)
            return node

        return decorator

    def fifo(self, depth: int) -> Callable:
        """Decorator that inserts a FIFO before the decorated stage.

        Must be stacked *above* a :meth:`stage` decorator.

        Parameters
        ----------
        depth : int
            Depth of the FIFO to insert.
        """

        def decorator(node: _FuncNode) -> _FuncNode:
            if not isinstance(node, _FuncNode):
                raise TypeError(
                    "fifo() must be applied directly above a @stage() decorator; "
                    "use the fifo_depth= parameter of add_external()/call_method() instead"
                )
            node.fifo_depth = depth
            return node

        return decorator

    def finalize(self) -> None:
        """Build all transactions and connectors for the pipeline.

        Must be called after all nodes have been added.
        """
        self._finalize()

    # ------------------------------------------------------------------
    # Internal implementation
    # ------------------------------------------------------------------

    def _finalize(self) -> None:
        """Create all transactions and connectors for the pipeline."""
        m = self._m
        nodes = self._nodes

        if not nodes:
            return

        current_layout: StructLayout = StructLayout({})
        # ``prev_read`` is the read-Method of the most recent connector, or
        # ``None`` when no connector has been created yet (start of pipeline).
        prev_read: Optional[Method] = None

        for i, node in enumerate(nodes):
            is_last = i == len(nodes) - 1

            # -----------------------------------------------------------------
            # Optional FIFO between the previous connector and this node.
            # Skip for the very first node (no previous connector exists yet).
            # -----------------------------------------------------------------
            if node.fifo_depth is not None and prev_read is not None:
                fifo = FIFO(layout=current_layout, depth=node.fifo_depth)
                m.submodules[f"_pipeline_fifo_{i}"] = fifo
                with Transaction(name=f"_pipeline_fill_{i}").body(m):
                    data = prev_read(m)
                    fifo.write(m, data)
                prev_read = fifo.read

            # -----------------------------------------------------------------
            # Dispatch by node type
            # -----------------------------------------------------------------
            if isinstance(node, _ProvidedMethodNode):
                prev_read, current_layout = self._finalize_provided(
                    m, i, node, is_last, prev_read, current_layout
                )

            elif isinstance(node, _FuncNode):
                prev_read, current_layout = self._finalize_func(
                    m, i, node, is_last, prev_read, current_layout
                )

            elif isinstance(node, _CalledMethodNode):
                prev_read, current_layout = self._finalize_called(
                    m, i, node, is_last, prev_read, current_layout
                )

    # ------------------------------------------------------------------
    # Per-type finalization helpers
    # ------------------------------------------------------------------

    def _finalize_provided(
        self,
        m: TModule,
        i: int,
        node: _ProvidedMethodNode,
        is_last: bool,
        prev_read: Optional[Method],
        current_layout: StructLayout,
    ) -> tuple[Optional[Method], StructLayout]:
        """Handle a _ProvidedMethodNode."""
        method = node.method
        method_in = from_method_layout(method.layout_in)
        method_out = from_method_layout(method.layout_out)

        # Validate exit fields that come from the pipeline
        if prev_read is not None:
            for fname, fshape in method_out.members.items():
                if fname not in current_layout.members:
                    raise ValueError(
                        f"Pipeline node {i}: provided method requests output field '{fname}' "
                        f"which is not present in the pipeline layout "
                        f"{list(current_layout.members.keys())}"
                    )
                cur_shape = current_layout.members[fname]
                if cur_shape != fshape:
                    raise ValueError(
                        f"Pipeline node {i}: provided method output field '{fname}' has shape "
                        f"{fshape!r}, but the pipeline has that field with shape {cur_shape!r}"
                    )

        # New pipeline layout accumulates the method's input fields.
        new_layout = StructLayout(current_layout.members | method_in.members)

        if prev_read is None:
            # ---- Entry node ----
            if is_last:
                # Degenerate single-node pipeline: nothing to connect.
                return None, new_layout

            pipe = Pipe(layout=new_layout)
            m.submodules[f"_pipeline_fwd_{i}"] = pipe
            _fwd_write = pipe.write

            @def_method(m, method)
            def _(arg):
                pipe.write(m, arg)

            return pipe.read, new_layout

        else:
            # ---- Middle or last node ----
            _prev_read = prev_read
            _cur_layout = current_layout
            _new_layout = new_layout
            _exit_fields = list(method_out.members.keys())

            if not is_last:
                fwd = Forwarder(layout=new_layout)
                m.submodules[f"_pipeline_fwd_{i}"] = fwd
                _fwd_write = fwd.write

                @def_method(m, method)
                def _(arg):
                    data = _prev_read(m)
                    out_sig = Signal(_new_layout)
                    for f in _cur_layout.members:
                        m.d.top_comb += out_sig[f].eq(data[f])
                    if method_in.members:
                        m.d.top_comb += assign(out_sig, arg, fields=AssignType.RHS)
                    _fwd_write(m, out_sig)
                    if _exit_fields:
                        return {k: out_sig[k] for k in _exit_fields}

                return fwd.read, new_layout
            else:
                # Last node: read from prev and return fields to caller.
                @def_method(m, method)
                def _(arg):
                    data = _prev_read(m)
                    if method_in.members:
                        # Unusual exit-with-inputs: merge caller's args too.
                        out_sig = Signal(_new_layout)
                        for f in _cur_layout.members:
                            m.d.top_comb += out_sig[f].eq(data[f])
                        m.d.top_comb += assign(out_sig, arg, fields=AssignType.RHS)
                        return {k: out_sig[k] for k in _exit_fields}
                    return {k: data[k] for k in _exit_fields}

                return None, new_layout

    def _finalize_func(
        self,
        m: TModule,
        i: int,
        node: _FuncNode,
        is_last: bool,
        prev_read: Optional[Method],
        current_layout: StructLayout,
    ) -> tuple[Optional[Method], StructLayout]:
        """Handle a _FuncNode."""
        # Validate output field shapes.
        for fname, fshape in node.o_layout.members.items():
            if fname in current_layout.members:
                cur_shape = current_layout.members[fname]
                if cur_shape != fshape:
                    raise ValueError(
                        f"Pipeline node {i}: stage output field '{fname}' declares shape "
                        f"{fshape!r}, but the pipeline already has that field with shape "
                        f"{cur_shape!r}"
                    )

        new_layout = StructLayout(current_layout.members | node.o_layout.members)

        # Build an empty in_data signal when there is no previous connector
        # (a FuncNode that creates data from scratch, like a source).
        _empty_in = Signal(current_layout)
        _prev_read = prev_read
        _in_layout = current_layout
        _new_layout = new_layout
        _desc = node

        if not is_last:
            fwd = Forwarder(layout=new_layout)
            m.submodules[f"_pipeline_fwd_{i}"] = fwd
            _fwd = fwd

            with Transaction(name=f"_pipeline_trans_{i}").body(m, ready=_desc.ready):
                in_data = _prev_read(m) if _prev_read is not None else _empty_in
                out_sig = Signal(_new_layout)
                for fname in _in_layout.members:
                    m.d.top_comb += out_sig[fname].eq(in_data[fname])
                result = _call_stage_func(_desc.func, in_data)
                if result is not None:
                    m.d.top_comb += assign(out_sig, result, fields=AssignType.RHS)
                _fwd.write(m, out_sig)

            return fwd.read, new_layout
        else:
            # Last FuncNode: data is consumed and not forwarded.
            with Transaction(name=f"_pipeline_trans_{i}").body(m, ready=_desc.ready):
                in_data = _prev_read(m) if _prev_read is not None else _empty_in
                _call_stage_func(_desc.func, in_data)

            return None, new_layout

    def _finalize_called(
        self,
        m: TModule,
        i: int,
        node: _CalledMethodNode,
        is_last: bool,
        prev_read: Optional[Method],
        current_layout: StructLayout,
    ) -> tuple[Optional[Method], StructLayout]:
        """Handle a _CalledMethodNode."""
        method = node.method
        method_in = from_method_layout(method.layout_in)
        method_out = from_method_layout(method.layout_out)

        # Validate: all method input fields must exist in the pipeline.
        for fname in method_in.members:
            if fname not in current_layout.members:
                raise ValueError(
                    f"Pipeline node {i}: called method requires input field '{fname}' "
                    f"which is not present in the pipeline layout "
                    f"{list(current_layout.members.keys())}"
                )

        # Validate output type compatibility.
        for fname, fshape in method_out.members.items():
            if fname in current_layout.members and current_layout.members[fname] != fshape:
                raise ValueError(
                    f"Pipeline node {i}: called method output field '{fname}' has shape "
                    f"{fshape!r}, but the pipeline has that field with shape "
                    f"{current_layout.members[fname]!r}"
                )

        new_layout = StructLayout(current_layout.members | method_out.members)

        _prev_read = prev_read
        _cur_layout = current_layout
        _new_layout = new_layout
        _method = method
        _in_fields = list(method_in.members.keys())
        _out_fields = list(method_out.members.keys())

        if not is_last:
            fwd = Forwarder(layout=new_layout)
            m.submodules[f"_pipeline_fwd_{i}"] = fwd
            _fwd = fwd

            with Transaction(name=f"_pipeline_trans_{i}").body(m, ready=node.ready):
                in_data = _prev_read(m) if _prev_read is not None else Signal(_cur_layout)
                out_sig = Signal(_new_layout)
                for fname in _cur_layout.members:
                    m.d.top_comb += out_sig[fname].eq(in_data[fname])
                method_result = _method(m, {k: in_data[k] for k in _in_fields})
                for fname in _out_fields:
                    m.d.top_comb += out_sig[fname].eq(method_result[fname])
                _fwd.write(m, out_sig)

            return fwd.read, new_layout
        else:
            # Last CalledMethodNode: call the method but don't forward.
            with Transaction(name=f"_pipeline_trans_{i}").body(m, ready=node.ready):
                in_data = _prev_read(m) if _prev_read is not None else Signal(_cur_layout)
                _method(m, {k: in_data[k] for k in _in_fields})

            return None, new_layout
