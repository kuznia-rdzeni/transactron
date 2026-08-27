from collections.abc import Generator
from contextlib import contextmanager
from typing import overload
from amaranth import Cat, Const, Format, Shape, ShapeCastable, Value, ValueCastable
from amaranth.hdl._ast import Assign
from amaranth_types import FlatShapeLike, ModuleLike, ShapeLike, ValueLike
from amaranth.lib import data


__all__ = ["OptionView", "Option"]


class OptionView[T: ShapeLike](ValueCastable):
    """A view into a value of ``Option`` shape.
 
    Provides convenient accessors for the ``valid`` and ``data`` fields of an
    underlying value that has the layout produced by ``Option``, as well as
    helper methods for working with such a value in module context.
    """

    def __init__(self, data_shape: T, target: ValueLike):
        """
        Parameters
        ----------
        data_shape : T
            Shape of the data carried when the option is valid. Used to
            construct the corresponding ``Option`` shape that this view
            conforms to.
        target : ValueLike
            The value this view wraps. Must be exactly as wide as
            ``Option(data_shape)``.
        """
        try:
            cast_target = Value.cast(target)
        except TypeError as e:
            raise TypeError(f"Target of an option view must be a value-castable object, not {target}") from e

        self._shape = Option(data_shape)

        if len(cast_target) != Shape.cast(self._shape).width:
            raise ValueError(
                f"Target of an option view is {len(cast_target)} wide, should be {Shape.cast(self._shape).width}"
            )

        self._target = data.View(self._shape.as_shape(), cast_target)

    def as_value(self) -> "data.View[data.StructLayout]":
        return self._target

    def shape(self) -> "Option[T]":
        return self._shape

    def valid(self) -> Value:
        """Whether the option currently holds a value."""
        return self._target.valid

    @overload
    def data(self: "OptionView[FlatShapeLike]") -> Value:  # type: ignore
        ...

    @overload
    def data[U: ValueCastable](self: "OptionView[ShapeCastable[U]]") -> U:  # type: ignore
        ...

    @overload
    def data(self) -> Value | ValueCastable:  # type: ignore
        ...

    def data(self) -> Value | ValueCastable:
        """The contained data. Only meaningful when `valid` is asserted."""
        return self._target.data

    @overload
    @contextmanager
    def with_data(self: "OptionView[FlatShapeLike]", m: ModuleLike) -> Generator[Value]:
        ...

    @overload
    @contextmanager
    def with_data[U](self: "OptionView[ShapeCastable[U]]", m: ModuleLike) -> Generator[U]:
        ...

    @overload
    @contextmanager
    def with_data(self, m: ModuleLike) -> Generator[Value | ValueCastable]:
        ...

    @contextmanager
    def with_data(self, m: ModuleLike) -> Generator[T]:
        """Enter a conditional block active only while this option is valid.

        Parameters
        ----------
        m : ModuleLike
            The module to add the `m.If(self.valid)` conditional to.
        """
        with m.If(self.valid()):
            yield self.data  # type: ignore

    def eq(self, other: ValueLike) -> Assign:
        """Create an assignment of `other` to this option.

        Parameters
        ----------
        other : ValueLike
            The value to assign. If it is value-castable, its shape must
            match the shape of this option.

        Returns
        -------
        Assign
            The assignment statement.
        """
        if isinstance(other, ValueCastable):
            if not self.shape() == other.shape():
                raise TypeError(
                    f"Cannot assign value with shape {other.shape()} to an option view with shape {self.shape()}"
                )
        return self.as_value().eq(other)

    def __eq__(self, other) -> Value:  # type: ignore
        if isinstance(other, OptionView) and self._shape == other._shape:
            return ~(self.valid() | other.valid()) | (self.valid() & other.valid() & (self._target.data == other._target.data))
        else:
            raise TypeError(
                f"Option view with layout {self._shape} can only be compared to another option view with same layout"
            )

    def __ne__(self, other) -> Value:  # type: ignore
        return ~(self == other)


class Option[T: ShapeLike](ShapeCastable[OptionView[T]]):
    """A shape representing an optional ("maybe") value.

    An ``Option`` describes a value that either carries data of the given
    shape, or carries no data. It is internally represented as a
    ``amaranth.lib.data.StructLayout`` with a single-bit ``valid`` field and a
    ``data`` field of shape `data_shape`; ``data`` is meaningless whenever
    ``valid`` is deasserted.
    """
    def __init__(self, data_shape: T):
        """
        Parameters
        ----------
        data_shape : ShapeLike
            Shape of the data carried when the option is valid. Must be
            shape-castable.
        """
        try:
            Shape.cast(data_shape)
        except TypeError as e:
            raise TypeError(f"Option data shape must be a shape-castable object, not {data_shape}") from e
        self._data_shape = data_shape
        self._internal_shape = data.StructLayout({"valid": 1, "data": data_shape})

    @property
    def data_shape(self) -> T:
        """The shape of the data carried when the option is valid."""
        return self._data_shape

    @property
    def empty(self) -> OptionView[T]:
        """A view of an invalid ("empty") constant of this shape."""
        return self(Const(0, Shape.cast(self).width))

    def wrap(self, data: ValueLike) -> OptionView[T]:
        """Wrap a value as a valid option.

        Parameters
        ----------
        data : ValueLike
            The data to wrap. If ``data_shape`` is shape-castable, ``data`` is
            passed to it directly and interpreted as that shape's
            constructor would interpret it. Otherwise, ``data`` is cast to a
            ``Value`` and truncated or zero-extended to fit ``data_shape``.
        """
        if isinstance(self._data_shape, ShapeCastable):
            val = Value.cast(self._data_shape(data))
        else:
            shape = Shape.cast(self._data_shape)
            val = (Value.cast(data) | Const(0, shape.width))[: shape.width]
        return self(Cat(Const(1, 1), val))

    def __eq__(self, other) -> bool:
        while isinstance(other, ShapeCastable) and not isinstance(other, Option):
            new_other = other.as_shape()
            if new_other == other:
                break
            other = new_other
        return isinstance(other, Option) and self.data_shape == other.data_shape

    def __hash__(self) -> int:
        return hash(self._data_shape)

    def as_shape(self) -> data.StructLayout:
        return self._internal_shape

    def const(self, init):
        if init is None:
            return self._internal_shape.const({"valid": 0})
        else:
            return self._internal_shape.const({"valid": 1, "data": init})

    def __call__(self, target: ValueLike) -> OptionView[T]:
        return OptionView[T](self.data_shape, target)

    def from_bits(self, raw: int):
        if raw & 1:
            raw >>= 1
            if isinstance(self._data_shape, ShapeCastable):
                return self._data_shape.from_bits(raw)
            return Const(raw, self._data_shape).value
        else:
            return None

    def format(self, obj: ValueLike, spec: str):
        if spec != "":
            raise ValueError(f"Format specifier {spec!r} is not supported for options")
        if not isinstance(obj, OptionView):
            obj = self(obj)
        return Format("Option({}, {})", obj.valid(), obj.data())
