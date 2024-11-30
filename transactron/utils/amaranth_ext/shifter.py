from amaranth import *
from amaranth.hdl import ValueCastable, ShapeCastable
from collections.abc import Sequence
from typing import TypeVar, overload
from amaranth_types.types import ValueLike


__all__ = ["rotate_left", "rotate_right", "rotate_vec_left", "rotate_vec_right"]


_T_ValueCastable = TypeVar("_T_ValueCastable", bound=ValueCastable)


def rotate_left(value: ValueLike, offset: ValueLike) -> Value:
    value = Value.cast(value)
    return value.replicate(2).bit_select(offset, len(value))


def rotate_right(value: ValueLike, offset: ValueLike) -> Value:
    value = Value.cast(value)
    return Cat(*reversed(rotate_left(Cat(*reversed(value)), offset)))


@overload
def rotate_vec_left(data: Sequence[_T_ValueCastable], offset: ValueLike) -> Sequence[_T_ValueCastable]: ...


@overload
def rotate_vec_left(data: Sequence[ValueLike], offset: ValueLike) -> Sequence[Value]: ...


def rotate_vec_left(data: Sequence[ValueLike | ValueCastable], offset: ValueLike) -> Sequence[Value | ValueCastable]:
    if isinstance(data[0], ValueCastable):
        shape = data[0].shape()
    else:
        shape = Value.cast(data[0]).shape()
    assert isinstance(shape, (Shape | ShapeCastable))

    data_values = [Value.cast(entry) for entry in data]
    width = Shape.cast(shape).width
    assert all(val.shape().width == width for val in data_values)

    shifted_bits = [rotate_left(Cat(val[i] for val in data_values), offset) for i in range(width)]

    shifted_values = [Cat(bits[i] for bits in shifted_bits) for i in range(len(data))]

    if isinstance(shape, Shape):
        return shifted_values
    else:
        return [shape(val) for val in shifted_values]


@overload
def rotate_vec_right(data: Sequence[_T_ValueCastable], offset: ValueLike) -> Sequence[_T_ValueCastable]: ...


@overload
def rotate_vec_right(data: Sequence[ValueLike], offset: ValueLike) -> Sequence[Value]: ...


def rotate_vec_right(data: Sequence[ValueLike | ValueCastable], offset: ValueLike) -> Sequence[Value | ValueCastable]:
    return list(reversed(rotate_vec_left(list(reversed(data)), offset)))
