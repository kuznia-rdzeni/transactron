from abc import abstractmethod
from typing import TYPE_CHECKING, Any, Self, Unpack, overload
from amaranth import *
from amaranth.hdl import ShapeCastable
from amaranth.lib.wiring import Component, In, Out
from amaranth.lib.data import Layout, StructLayout, View
from amaranth_types import ShapeLike

from ..utils import SrcLoc, get_src_loc, DebugBundle, LayoutIterable, MethodData
from ..core import *
from ..core.method import _FlatShape

__all__ = [
    "Adapter",
    "AdapterBase",
    "AdapterTrans",
]


# Typed access to the ports of adapters, like `_DataIn` and `_DataOut`
# in `transactron.core.method`. Only used for type checking, as the
# ports are created by `Component`.
class _PortIn:
    @overload
    def __get__[L: Layout](self, obj: "AdapterBase[L, Any]", owner: Any) -> View[L]: ...

    @overload
    def __get__[T: MethodData](self, obj: "AdapterBase[ShapeCastable[T], Any]", owner: Any) -> T: ...

    @overload
    def __get__(self, obj: "AdapterBase[Shape, Any]", owner: Any) -> Signal: ...

    @overload
    def __get__(self, obj: "AdapterBase[Any, Any]", owner: Any) -> MethodData: ...

    @overload
    def __get__(self, obj: None, owner: Any) -> Self: ...

    def __get__(self, obj: Any, owner: Any) -> Any: ...


class _PortOut:
    @overload
    def __get__[L: Layout](self, obj: "AdapterBase[Any, L]", owner: Any) -> View[L]: ...

    @overload
    def __get__[T: MethodData](self, obj: "AdapterBase[Any, ShapeCastable[T]]", owner: Any) -> T: ...

    @overload
    def __get__(self, obj: "AdapterBase[Any, Shape]", owner: Any) -> Signal: ...

    @overload
    def __get__(self, obj: "AdapterBase[Any, Any]", owner: Any) -> MethodData: ...

    @overload
    def __get__(self, obj: None, owner: Any) -> Self: ...

    def __get__(self, obj: Any, owner: Any) -> Any: ...


class AdapterBase[PI: Shape | ShapeCastable = StructLayout, PO: Shape | ShapeCastable = StructLayout](Component):
    """Base class for adapters.

    The type parameters `PI` and `PO` are the shapes of the ports
    `data_in` and `data_out`.
    """

    iface: Method[Any, Any]
    if TYPE_CHECKING:
        data_in: _PortIn = _PortIn()
        data_out: _PortOut = _PortOut()
    en: Signal
    done: Signal

    def __init__(self, iface: Method[Any, Any], shape_in: PI, shape_out: PO):
        super().__init__({"data_in": In(shape_in), "data_out": Out(shape_out), "en": In(1), "done": Out(1)})
        self.iface = iface

    def debug_signals(self) -> DebugBundle:
        return [self.en, self.done, self.data_in, self.data_out]

    @abstractmethod
    def elaborate(self, platform) -> TModule:
        raise NotImplementedError


class AdapterTrans[I: Shape | ShapeCastable = StructLayout, O: Shape | ShapeCastable = StructLayout](AdapterBase[I, O]):
    """Adapter transaction.

    Creates a transaction controlled by plain Amaranth signals which calls
    a single method, `iface`. Allows to expose a method to plain Amaranth
    code, including testbenches.

    To expose an existing method, construct the `AdapterTrans` using `create`.

    Attributes
    ----------
    en: Signal, in
        Activates the transaction (sets the `ready` signal).
    done: Signal, out
        Signals that the transaction is performed (returns the `run` signal).
    data_in: Value or ValueCastable, in
        Data passed to the `iface` method.
    data_out: Value or ValueCastable, out
        Data returned from the `iface` method.
    """

    iface: Required[Method[I, O]]
    """The method called by the `AdapterTrans`."""

    @overload
    def __init__(
        self,
        name: str | None = None,
        i: LayoutIterable | I = (),
        o: LayoutIterable | O = (),
        src_loc: int | SrcLoc = 0,
    ) -> None: ...

    @overload
    def __init__(
        self: "AdapterTrans[Shape, StructLayout]",
        name: str | None = None,
        *,
        i: _FlatShape,
        o: LayoutIterable = (),
        src_loc: int | SrcLoc = 0,
    ) -> None: ...

    @overload
    def __init__[O2: Shape | ShapeCastable](
        self: "AdapterTrans[Shape, O2]",
        name: str | None = None,
        *,
        i: _FlatShape,
        o: O2,
        src_loc: int | SrcLoc = 0,
    ) -> None: ...

    @overload
    def __init__(
        self: "AdapterTrans[StructLayout, Shape]",
        name: str | None = None,
        *,
        i: LayoutIterable = (),
        o: _FlatShape,
        src_loc: int | SrcLoc = 0,
    ) -> None: ...

    @overload
    def __init__[I2: Shape | ShapeCastable](
        self: "AdapterTrans[I2, Shape]",
        name: str | None = None,
        *,
        i: I2,
        o: _FlatShape,
        src_loc: int | SrcLoc = 0,
    ) -> None: ...

    @overload
    def __init__(
        self: "AdapterTrans[Shape, Shape]",
        name: str | None = None,
        *,
        i: _FlatShape,
        o: _FlatShape,
        src_loc: int | SrcLoc = 0,
    ) -> None: ...

    def __init__(
        self,
        name: str | None = None,
        i: LayoutIterable | ShapeLike = (),
        o: LayoutIterable | ShapeLike = (),
        src_loc: int | SrcLoc = 0,
    ):
        """
        Parameters
        ----------
        name: str, optional
            Name for the created method.
        i: method layout or shape, optional
            Input layout of the created method.
        o: method layout or shape, optional
            Output layout of the created method.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        self.src_loc = get_src_loc(src_loc)
        method: Method[I, O] = Method(name=name, i=i, o=o, src_loc=self.src_loc)  # type: ignore
        super().__init__(method, method.shape_in, method.shape_out)

    @staticmethod
    def create[I2: Shape | ShapeCastable, O2: Shape | ShapeCastable](
        method: Method[I2, O2], *, src_loc: int | SrcLoc = 0
    ) -> "AdapterTrans[I2, O2]":
        """Creates an `AdapterTrans` which calls a given method.

        Parameters
        ----------
        method: Method
            The method to be called by the transaction.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        src_loc = get_src_loc(src_loc)
        adapter = AdapterTrans(name=method.name, i=method.shape_in, o=method.shape_out, src_loc=src_loc)
        adapter.iface.provide(method)
        return adapter

    def elaborate(self, platform):
        m = TModule()

        # this forces data_in signal to appear in VCD dumps
        data_in = Signal.like(self.data_in)
        m.d.comb += Value.cast(data_in).eq(self.data_in)

        with Transaction(name=f"AdapterTrans_{self.iface.name}", src_loc=self.src_loc).body(m, ready=self.en):
            data_out = self.iface(m, data_in)
            m.d.top_comb += Value.cast(self.data_out).eq(data_out)
            m.d.comb += self.done.eq(1)

        return m


class Adapter[I: Shape | ShapeCastable = StructLayout, O: Shape | ShapeCastable = StructLayout](AdapterBase[O, I]):
    """Adapter method.

    Creates a method controlled by plain Amaranth signals. One of the
    possible uses is to mock a method in a testbench.

    To control an existing (but not yet defined) method, construct the
    `Adapter` using `create`.

    Attributes
    ----------
    en: Signal, in
        Activates the method (sets the `ready` signal).
    done: Signal, out
        Signals that the method is called (returns the `run` signal).
    data_in: Value or ValueCastable, in
        Data returned from the defined method.
    data_out: Value or ValueCastable, out
        Data passed as argument to the defined method.
    validators: list of tuples of Value or ValueCastable, out and Signal, in
        Hooks for `validate_arguments`.
    """

    iface: Provided[Method[I, O]]
    """The method defined and controlled by the `Adapter`."""

    @overload
    def __init__(
        self,
        name: str | None = None,
        i: LayoutIterable | I = (),
        o: LayoutIterable | O = (),
        src_loc: int | SrcLoc = 0,
        **kwargs: Unpack[AdapterBodyParams],
    ) -> None: ...

    @overload
    def __init__(
        self: "Adapter[Shape, StructLayout]",
        name: str | None = None,
        *,
        i: _FlatShape,
        o: LayoutIterable = (),
        src_loc: int | SrcLoc = 0,
        **kwargs: Unpack[AdapterBodyParams],
    ) -> None: ...

    @overload
    def __init__[O2: Shape | ShapeCastable](
        self: "Adapter[Shape, O2]",
        name: str | None = None,
        *,
        i: _FlatShape,
        o: O2,
        src_loc: int | SrcLoc = 0,
        **kwargs: Unpack[AdapterBodyParams],
    ) -> None: ...

    @overload
    def __init__(
        self: "Adapter[StructLayout, Shape]",
        name: str | None = None,
        *,
        i: LayoutIterable = (),
        o: _FlatShape,
        src_loc: int | SrcLoc = 0,
        **kwargs: Unpack[AdapterBodyParams],
    ) -> None: ...

    @overload
    def __init__[I2: Shape | ShapeCastable](
        self: "Adapter[I2, Shape]",
        name: str | None = None,
        *,
        i: I2,
        o: _FlatShape,
        src_loc: int | SrcLoc = 0,
        **kwargs: Unpack[AdapterBodyParams],
    ) -> None: ...

    @overload
    def __init__(
        self: "Adapter[Shape, Shape]",
        name: str | None = None,
        *,
        i: _FlatShape,
        o: _FlatShape,
        src_loc: int | SrcLoc = 0,
        **kwargs: Unpack[AdapterBodyParams],
    ) -> None: ...

    def __init__(
        self,
        name: str | None = None,
        i: LayoutIterable | ShapeLike = (),
        o: LayoutIterable | ShapeLike = (),
        src_loc: int | SrcLoc = 0,
        **kwargs: Unpack[AdapterBodyParams],
    ):
        """
        Parameters
        ----------
        name: str, optional
            Name for the created method.
        i: method layout or shape, optional
            Input layout of the created method.
        o: method layout or shape, optional
            Output layout of the created method.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        **kwargs
            Keyword arguments for `Method` body that will be created.
            See `transactron.core.Method.body` for parameters description.
        """
        method: Method[I, O] = Method(name=name, i=i, o=o, src_loc=get_src_loc(src_loc))  # type: ignore
        super().__init__(method, method.shape_out, method.shape_in)
        self.validators: list[tuple[MethodData, Signal]] = []
        self.with_validate_arguments: bool = False
        self.kwargs = kwargs

    @staticmethod
    def create[I2: Shape | ShapeCastable, O2: Shape | ShapeCastable](
        method: Method[I2, O2], /, *, src_loc: int | SrcLoc = 0, **kwargs: Unpack[AdapterBodyParams]
    ) -> "Adapter[I2, O2]":
        """Creates an `Adapter` which defines a given method.

        Parameters
        ----------
        method: Method
            `Method` to be controlled by the `Adapter`.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        **kwargs
            Keyword arguments for `Method` body that will be created.
            See `transactron.core.Method.body` for parameters description.
        """
        src_loc = get_src_loc(src_loc)
        adapter = Adapter(name=method.name, i=method.shape_in, o=method.shape_out, src_loc=src_loc, **kwargs)
        method.provide(adapter.iface)
        return adapter

    def update_args(self, **kwargs: Unpack[AdapterBodyParams]):
        self.kwargs.update(kwargs)
        return self

    def set(self, with_validate_arguments: bool | None):
        if with_validate_arguments is not None:
            self.with_validate_arguments = with_validate_arguments
        return self

    def elaborate(self, platform):
        m = TModule()

        # this forces data_in signal to appear in VCD dumps
        data_in = Signal.like(self.data_in)
        m.d.comb += Value.cast(data_in).eq(self.data_in)

        kwargs: BodyParams = self.kwargs  # type: ignore (pyright complains about optional attribute)

        if self.with_validate_arguments:
            def validate_arguments(arg):
                ret = Signal()
                self.validators.append((arg, ret))
                return ret

            kwargs["validate_arguments"] = validate_arguments

        @def_method(m, self.iface, ready=self.en, **kwargs)
        def _(arg):
            m.d.top_comb += Value.cast(self.data_out).eq(arg)
            m.d.comb += self.done.eq(1)
            return data_in

        return m
