from collections.abc import Iterable, Sequence
import enum
import textwrap

from transactron.utils import *
from amaranth import *
from amaranth import tracer
from amaranth.hdl import ShapeCastable
from amaranth.lib.data import Layout, StructLayout, View
from amaranth_types import ShapeLike, ValueLike
from typing import TYPE_CHECKING, Annotated, Any, Optional, Self, TypeAlias, TypeVar, Unpack, overload
from collections.abc import Iterator
from .transaction_base import *
from contextlib import AbstractContextManager, contextmanager
from warnings import deprecated
from transactron.utils.assign import AssignArg
from transactron.utils.typing import type_self_add_1pos_kwargs_as

from .body import Body, BodyParams, MBody
from .keys import DefinedMethodsKey, ProvidedMethodsKey
from .tmodule import TModule
from .transaction_base import TransactionBase


if TYPE_CHECKING:
    from .transaction import Transaction  # noqa: F401


__all__ = ["Method", "MethodDir", "Methods", "Provided", "Required"]


class MethodDir(enum.Enum):
    PROVIDED = enum.auto()
    REQUIRED = enum.auto()


_T = TypeVar("_T")
Provided: TypeAlias = Annotated[_T, MethodDir.PROVIDED]  # noqa: UP040
Required: TypeAlias = Annotated[_T, MethodDir.REQUIRED]  # noqa: UP040


type _FlatShape = int | range | type[enum.Enum]


def _method_shape(shape: LayoutIterable | ShapeLike) -> Shape | ShapeCastable:
    if isinstance(shape, ShapeCastable | Shape):
        return shape
    elif isinstance(shape, int | range | type):
        return Shape.cast(shape)
    else:
        return from_method_layout(shape)


# The data types of `Method`'s `data_in` and `data_out` are computed from
# the shape type parameters by the overloads of `__get__`.
class _DataIn:
    @overload
    def __get__[L: Layout](self, obj: "Method[L, Any]", owner: Any) -> View[L]: ...

    @overload
    def __get__[T: MethodData](self, obj: "Method[ShapeCastable[T], Any]", owner: Any) -> T: ...

    @overload
    def __get__(self, obj: "Method[Shape, Any]", owner: Any) -> Signal: ...

    @overload
    def __get__(self, obj: "Method[Any, Any]", owner: Any) -> MethodData: ...

    @overload
    def __get__(self, obj: None, owner: Any) -> Self: ...

    def __get__(self, obj: "Method[Any, Any] | None", owner: Any) -> Any:
        return self if obj is None else obj._data_in


class _DataOut:
    @overload
    def __get__[L: Layout](self, obj: "Method[Any, L]", owner: Any) -> View[L]: ...

    @overload
    def __get__[T: MethodData](self, obj: "Method[Any, ShapeCastable[T]]", owner: Any) -> T: ...

    @overload
    def __get__(self, obj: "Method[Any, Shape]", owner: Any) -> Signal: ...

    @overload
    def __get__(self, obj: "Method[Any, Any]", owner: Any) -> MethodData: ...

    @overload
    def __get__(self, obj: None, owner: Any) -> Self: ...

    def __get__(self, obj: "Method[Any, Any] | None", owner: Any) -> Any:
        return self if obj is None else obj._data_out


class Method[I: Shape | ShapeCastable = StructLayout, O: Shape | ShapeCastable = StructLayout](
    TransactionBase["Transaction | Method[Any, Any]"]
):
    """Transactional method.

    A `Method` serves to interface a module with external `Transaction`\\s
    or `Method`\\s. It can be called by at most once in a given clock cycle.
    When a given `Method` is called by multiple `Transaction`\\s
    (either directly, or indirectly via another `Method`) simultaneously,
    at most one of them is run by the `TransactionManager`, and the rest
    of them must wait. (Non-exclusive methods are an exception to this
    behavior.) Calling a `Method` always takes a single clock cycle.

    Data is combinationally transferred between to and from `Method`\\s
    using Amaranth values. Typically these are structures (`View` with
    a `StructLayout`), but any Amaranth shape can be used. The transfer
    can take place in both directions at the same time: from the called
    `Method` to the caller (`data_out`) and from the caller to the called
    `Method` (`data_in`).

    The type parameters `I` and `O` are the shapes of `data_in` and
    `data_out`; they are inferred from the layouts passed to the constructor.
    Shapes which are not shape-castable (e.g. `int` or `range`) are converted
    to `Shape`.

    A module which defines a `Method` should use `body` or `def_method`
    to describe the method's effect on the module state.

    Attributes
    ----------
    name: str
        Name of this `Method`.
    ready: Signal, in
        Signals that the method is ready to run in the current cycle.
        Typically defined by calling `body`.
    run: Signal, out
        Signals that the method is called in the current cycle by some
        `Transaction`. Defined by the `TransactionManager`.
    data_in: Value or ValueCastable, out
        Contains the data passed to the `Method` by the caller
        (a `Transaction` or another `Method`).
    data_out: Value or ValueCastable, in
        Contains the data passed from the `Method` to the caller
        (a `Transaction` or another `Method`). Typically defined by
        calling `body`.
    """

    _body_ptr: Optional["Body | Method[Any, Any]"] = None

    _data_in: MethodData
    _data_out: MethodData
    data_in = _DataIn()
    data_out = _DataOut()

    @overload
    def __init__(
        self,
        *,
        name: str | None = None,
        i: LayoutIterable | I = (),
        o: LayoutIterable | O = (),
        src_loc: int | SrcLoc = 0,
    ) -> None: ...

    @overload
    def __init__(
        self: "Method[Shape, StructLayout]",
        *,
        name: str | None = None,
        i: _FlatShape,
        o: LayoutIterable = (),
        src_loc: int | SrcLoc = 0,
    ) -> None: ...

    @overload
    def __init__[O2: Shape | ShapeCastable](
        self: "Method[Shape, O2]",
        *,
        name: str | None = None,
        i: _FlatShape,
        o: O2,
        src_loc: int | SrcLoc = 0,
    ) -> None: ...

    @overload
    def __init__(
        self: "Method[StructLayout, Shape]",
        *,
        name: str | None = None,
        i: LayoutIterable = (),
        o: _FlatShape,
        src_loc: int | SrcLoc = 0,
    ) -> None: ...

    @overload
    def __init__[I2: Shape | ShapeCastable](
        self: "Method[I2, Shape]",
        *,
        name: str | None = None,
        i: I2,
        o: _FlatShape,
        src_loc: int | SrcLoc = 0,
    ) -> None: ...

    @overload
    def __init__(
        self: "Method[Shape, Shape]",
        *,
        name: str | None = None,
        i: _FlatShape,
        o: _FlatShape,
        src_loc: int | SrcLoc = 0,
    ) -> None: ...

    def __init__(
        self,
        *,
        name: str | None = None,
        i: LayoutIterable | ShapeLike = (),
        o: LayoutIterable | ShapeLike = (),
        src_loc: int | SrcLoc = 0,
    ):
        """
        Parameters
        ----------
        name: str or None
            Name hint for this `Method`. If `None` (default) the name is
            inferred from the variable name this `Method` is assigned to.
        i: method layout or shape
            The format of `data_in`.
        o: method layout or shape
            The format of `data_out`.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        super().__init__(src_loc=get_src_loc(src_loc))
        self.owner, owner_name = get_caller_class_name(default="$method")
        self.name = name or tracer.get_var_name(depth=2, default=owner_name)
        self.ready = Signal(name=self.owned_name + "_ready")
        self.run = Signal(name=self.owned_name + "_run")
        self._shape_in: I = _method_shape(i)  # type: ignore  # checked by the overloads
        self._shape_out: O = _method_shape(o)  # type: ignore  # checked by the overloads
        self._data_in = Signal(self._shape_in, name=self.owned_name + "_data_in")
        self._data_out = Signal(self._shape_out, name=self.owned_name + "_data_out")

    @property
    def shape_in(self) -> I:
        return self._shape_in

    @property
    def shape_out(self) -> O:
        return self._shape_out

    @property
    @deprecated("Use `shape_in` instead.")
    def layout_in(self) -> I:
        return self._shape_in

    @property
    @deprecated("Use `shape_out` instead.")
    def layout_out(self) -> O:
        return self._shape_out

    @staticmethod
    def like[I2: Shape | ShapeCastable, O2: Shape | ShapeCastable](
        other: "Method[I2, O2]", *, name: str | None = None
    ) -> "Method[I2, O2]":
        """Constructs a new `Method` based on another.

        The returned `Method` has the same input/output data layouts as the
        `other` `Method`.

        Parameters
        ----------
        other : Method
            The `Method` which serves as a blueprint for the new `Method`.
        name : str, optional
            Name of the new `Method`.

        Returns
        -------
        Method
            The freshly constructed `Method`.
        """
        return Method(name=name, i=other.shape_in, o=other.shape_out)

    @property
    def _body(self) -> MBody:
        if isinstance(self._body_ptr, Body):
            return MBody(self._body_ptr)
        if isinstance(self._body_ptr, Method):
            self._body_ptr = self._body_ptr._body
            return self._body_ptr
        raise RuntimeError(f"Method '{self.name}' {self.src_loc} not defined")

    def _set_impl(self, value: "Body | Method[Any, Any]"):
        if self._body_ptr is not None:
            raise RuntimeError(f"Method '{self.name}' {self.src_loc} already defined")
        if value.data_in.shape() != self.shape_in or value.data_out.shape() != self.shape_out:
            raise ValueError(
                textwrap.dedent(
                    f"""\
                    Method {value.name} at {value.src_loc} has different interface than {self.name} at {self.src_loc}
                    {value.name} input: {value.data_in.shape()}
                    {value.name} output: {value.data_out.shape()}
                    {self.name} input: {self.shape_in}
                    {self.name} output: {self.shape_out}
                    """
                )
            )
        self._body_ptr = value

    def provide(self, method: "Method[Any, Any]"):
        """Provide method definition using another method.

        The calls to this method will be forwarded to `method`. Input and
        output data layouts of this method and `method` must match.

        Parameters
        ----------
        method : Method
            Definition of `method` will be provided to this method.
        """
        self._set_impl(method)
        DependencyContext.get().add_dependency(ProvidedMethodsKey(), self)

    @overload
    def body[L: Layout](
        self: "Method[L, Any]",
        m: TModule,
        *,
        ready: ValueLike = ...,
        out: ValueLike = ...,
        **kwargs: Unpack[BodyParams],
    ) -> AbstractContextManager[View[L]]: ...

    @overload
    def body[T: MethodData](
        self: "Method[ShapeCastable[T], Any]",
        m: TModule,
        *,
        ready: ValueLike = ...,
        out: ValueLike = ...,
        **kwargs: Unpack[BodyParams],
    ) -> AbstractContextManager[T]: ...

    @overload
    def body(
        self: "Method[Shape, Any]",
        m: TModule,
        *,
        ready: ValueLike = ...,
        out: ValueLike = ...,
        **kwargs: Unpack[BodyParams],
    ) -> AbstractContextManager[Signal]: ...

    @overload
    def body(
        self: "Method[Any, Any]",
        m: TModule,
        *,
        ready: ValueLike = ...,
        out: ValueLike = ...,
        **kwargs: Unpack[BodyParams],
    ) -> AbstractContextManager[MethodData]: ...

    @contextmanager
    def body(
        self, m: TModule, *, ready: ValueLike = C(1), out: ValueLike = C(0, 0), **kwargs: Unpack[BodyParams]
    ) -> Iterator[Any]:
        """Define method body

        The `body` context manager can be used to define the actions
        performed by a `Method` when it's run. Each assignment added to
        a domain under `body` is guarded by the `run` signal.
        Combinational assignments which do not need to be guarded by `run`
        can be added to `m.d.av_comb` or `m.d.top_comb` instead of `m.d.comb`.
        `Method` calls can be performed under `body`.

        Parameters
        ----------
        m : TModule
            Module in which operations on signals should be executed,
            `body` uses the combinational domain only.
        ready : Signal, in
            Signal to indicate if the method is ready to be run. By
            default it is `Const(1)`, so the method is always ready.
            Assigned combinationially to the `ready` attribute.
        out : Value, in
            Data generated by the `Method`, which will be passed to
            the caller (a `Transaction` or another `Method`). Assigned
            combinationally to the `data_out` attribute.
        validate_arguments: Optional[Callable[..., ValueLike]]
            Function that takes input arguments used to call the method
            and checks whether the method can be called with those arguments.
            It instantiates a combinational circuit for each
            method caller. By default, there is no function, so all arguments
            are accepted.
        combiner: (Module, Sequence[MethodStruct], Value) -> AssignArg
            If `nonexclusive` is true, the combiner function combines the
            arguments from multiple calls to this method into a single
            argument, which is passed to the method body. The third argument
            is a bit vector, whose n-th bit is 1 if the n-th call is active
            in a given cycle.
        nonexclusive: bool
            If true, the method is non-exclusive: it can be called by multiple
            transactions in the same clock cycle. If such a situation happens,
            the method still is executed only once, and each of the callers
            receive its output. Nonexclusive methods cannot have inputs.
        single_caller: bool
            If true, this method is intended to be called from a single
            transaction. An error will be thrown if called from multiple
            transactions.

        Returns
        -------
        data_in : Value or ValueCastable, out
            Data passed from the caller (a `Transaction` or another
            `Method`) to this `Method`.

        Examples
        --------
        .. highlight:: python
        .. code-block:: python

            m = Module()
            my_sum_method = Method(i = Layout([("arg1",8),("arg2",8)]))
            sum = Signal(16)
            with my_sum_method.body(m, out = sum) as data_in:
                m.d.comb += sum.eq(data_in.arg1 + data_in.arg2)
        """
        body = Body(
            name=self.name,
            owner=self.owner,
            i=self.shape_in,
            o=self.shape_out,
            src_loc=self.src_loc,
            **kwargs,
        )
        self._set_impl(body)

        m.d.av_comb += body.ready.eq(ready)
        m.d.top_comb += Value.cast(body.data_out).eq(out)
        with body.context(m):
            with m.AvoidedIf(body.run):
                yield body.data_in

        # Assigned here instead of the transaction manager because:
        # - The waveforms will be available in the module which defined the method.
        # - This simulates faster in pysim.
        m.d.top_comb += self.ready.eq(body.ready)
        m.d.top_comb += self.run.eq(body.run)
        m.d.top_comb += Value.cast(self.data_in).eq(body.data_in)
        m.d.top_comb += Value.cast(self.data_out).eq(body.data_out)

        DependencyContext.get().add_dependency(DefinedMethodsKey(), self)

    @overload
    def __call__[L: Layout](
        self: "Method[Any, L]",
        m: TModule,
        arg: AssignArg | None = None,
        /,
        enable_call: ValueLike = ...,
        **kwargs: AssignArg,
    ) -> View[L]: ...

    @overload
    def __call__[T: MethodData](
        self: "Method[Any, ShapeCastable[T]]",
        m: TModule,
        arg: AssignArg | None = None,
        /,
        enable_call: ValueLike = ...,
        **kwargs: AssignArg,
    ) -> T: ...

    @overload
    def __call__(
        self: "Method[Any, Shape]",
        m: TModule,
        arg: AssignArg | None = None,
        /,
        enable_call: ValueLike = ...,
        **kwargs: AssignArg,
    ) -> Signal: ...

    @overload
    def __call__(
        self: "Method[Any, Any]",
        m: TModule,
        arg: AssignArg | None = None,
        /,
        enable_call: ValueLike = ...,
        **kwargs: AssignArg,
    ) -> MethodData: ...

    def __call__(
        self, m: TModule, arg: AssignArg | None = None, /, enable_call: ValueLike = C(1), **kwargs: AssignArg
    ) -> Any:
        """Call a method.

        Methods can only be called from transaction and method bodies.
        Calling a `Method` marks, for the purpose of transaction scheduling,
        the dependency between the calling context and the called `Method`.
        It also connects the method's inputs to the parameters and the
        method's outputs to the return value.

        Parameters
        ----------
        m : TModule
            Module in which operations on signals should be executed,
        arg : Value or dict of Values
            Call argument. Can be passed as a `View` of the method's
            input layout or as a dictionary. Alternative syntax uses
            keyword arguments.
        enable_call : Value
            Configures the call as enabled in the current clock cycle.
            Disabled calls still lock the called method in transaction
            scheduling. Calls are by default enabled.
        **kwargs : Value or dict of Values
            Allows to pass method arguments using keyword argument
            syntax. Equivalent to passing a dict as the argument.

        Returns
        -------
        data_out : Value or ValueCastable
            The result of the method call.

        Examples
        --------
        .. highlight:: python
        .. code-block:: python

            m = Module()
            with Transaction().body(m):
                ret = my_sum_method(m, arg1=2, arg2=3)

        Alternative syntax:

        .. highlight:: python
        .. code-block:: python

            with Transaction().body(m):
                ret = my_sum_method(m, {"arg1": 2, "arg2": 3})
        """
        if arg is not None and kwargs:
            raise ValueError(f"Method '{self.name}' call with both keyword arguments and legacy record argument")

        if arg is None:
            arg = kwargs

        if not isinstance(enable_call, Const) or enable_call.value != 1:
            with m.If(enable_call):
                return self(m, arg)

        arg_rec = Signal.like(self.data_in)

        enable_sig = Signal(name=self.owned_name + "_enable")
        m.d.av_comb += enable_sig.eq(1)
        m.d.top_comb += assign(arg_rec, arg, fields=AssignType.ALL)

        caller = Body.get()
        caller.method_calls[self].append((m.ctrl_path, arg_rec, enable_sig))

        return self.data_out

    def __repr__(self) -> str:
        return f"(method {self.name})"

    def debug_signals(self) -> DebugBundle:
        return [self.ready, self.run, self.data_in, self.data_out]


class Methods(Sequence[Method]):
    @type_self_add_1pos_kwargs_as(Method.__init__)
    def __init__(self, count: int, **kwargs):
        if count < 0:
            raise ValueError("count should be at least 0")
        _, owner_name = get_caller_class_name(default="$method")
        self.name = kwargs["name"] if "name" in kwargs else tracer.get_var_name(depth=2, default=owner_name)
        if "src_loc" not in kwargs:
            kwargs["src_loc"] = 0
        if isinstance(kwargs["src_loc"], int):
            kwargs["src_loc"] += 1
        self._methods = [Method(**{**kwargs, "name": f"{self.name}{i}"}) for i in range(count)]
        self._layout_in = from_method_layout(kwargs["i"] if "i" in kwargs else ())
        self._layout_out = from_method_layout(kwargs["o"] if "o" in kwargs else ())

    @property
    def layout_in(self):
        return self._layout_in

    @property
    def layout_out(self):
        return self._layout_out

    def provide(self, methods: Iterable[Method]):
        """Provide methods' definition using another set of methods.

        The calls to `i`-th method will be forwarded to `i`-th method of
        `methods` using `Method.provide`.

        Parameters
        ----------
        methods : Iterable[Method]
            Definitions of `methods` will be provided to this method.
        """
        methods = list(methods)
        if len(methods) != len(self):
            raise ValueError(f"Invalid number of methods: {len(self)} expected, {len(methods)} provided")
        for self_method, method in zip(self, methods):
            self_method.provide(method)

    def __call__(
        self, m: TModule, arg: AssignArg | None = None, /, enable_call: ValueLike = C(1), **kwargs: AssignArg
    ) -> MethodStruct:
        if len(self._methods) != 1:
            raise RuntimeError("calling Methods only allowed when count=1")
        return self._methods[0](m, arg, enable_call, **kwargs)

    @overload
    def __getitem__(self, key: int) -> Method: ...

    @overload
    def __getitem__(self, key: slice) -> Sequence[Method]: ...

    def __getitem__(self, key: int | slice) -> Method | Sequence[Method]:
        return self._methods[key]

    def __len__(self) -> int:
        return len(self._methods)

    def debug_signals(self) -> DebugBundle:
        return {method.name: method.debug_signals() for method in self._methods}
