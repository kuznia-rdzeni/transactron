from collections.abc import Iterator
from contextlib import contextmanager
from amaranth import Cat, Const, Format, Shape, ShapeCastable, Value, ValueCastable
from amaranth.hdl._ast import Assign
from amaranth_types import ModuleLike, ShapeLike, ValueLike
from amaranth.lib import data


__all__ = ["OptionView", "Option"]


class OptionView[T: ShapeLike = ShapeLike](ValueCastable):
    def __init__(self, data_shape: T, target: ValueLike):
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

    @property
    def valid(self) -> Value:
        return self._target.valid

    @property
    def data(self) -> T:
        return self._target.data

    @contextmanager
    def with_data(self, m: ModuleLike) -> Iterator[T]:
        with m.If(self.valid):
            yield self.data

    def eq(self, other: ValueLike) -> Assign:
        if isinstance(other, ValueCastable):
            if not self.shape() == other.shape():
                raise TypeError(
                    f"Cannot assign value with shape {other.shape()} to an option view with shape {self.shape()}"
                )
        return self.as_value().eq(other)

    def __eq__(self, other) -> Value:  # type: ignore
        if isinstance(other, OptionView) and self._shape == other._shape:
            return ~(self.valid | other.valid) | (self.valid & other.valid & (self._target.data == other._target.data))
        else:
            raise TypeError(
                f"Option view with layout {self._shape} can only be compared to another option view with same layout"
            )

    def __ne__(self, other) -> Value:  # type: ignore
        return ~(self == other)


class Option[T: ShapeLike = ShapeLike](ShapeCastable[OptionView[T]]):
    def __init__(self, data_shape: T):
        try:
            Shape.cast(data_shape)
        except TypeError as e:
            raise TypeError(f"Option data shape must be a shape-castable object, not {data_shape}") from e
        self._data_shape = data_shape
        self._internal_shape = data.StructLayout({"valid": 1, "data": data_shape})

    @property
    def data_shape(self) -> T:
        return self._data_shape

    @property
    def empty(self):
        return self(Const(0, Shape.cast(self).width))

    def wrap(self, data: ValueLike):
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
        return Format("Option({}, {})", obj.valid, obj.data)
