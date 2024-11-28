from amaranth import *
from amaranth.hdl import ValueCastable, ShapeCastable
from collections.abc import Sequence
from typing import TypeVar, overload
from amaranth_types.types import ValueLike


__all__ = ["barrel_shift_left", "barrel_shift_right"]


_T_ValueCastable = TypeVar("_T_ValueCastable", bound=ValueCastable)


@overload
def barrel_shift_left(data: Sequence[_T_ValueCastable], offset: ValueLike) -> Sequence[_T_ValueCastable]: ...


@overload
def barrel_shift_left(data: Sequence[ValueLike], offset: ValueLike) -> Sequence[Value]: ...


def barrel_shift_left(data: Sequence[ValueLike | ValueCastable], offset: ValueLike) -> Sequence[Value | ValueCastable]:
    if isinstance(data[0], ValueCastable):
        shape = data[0].shape()
    else:
        shape = Value.cast(data[0]).shape()
    assert isinstance(shape, (Shape | ShapeCastable))

    data_values = [Value.cast(entry) for entry in data]
    width = Shape.cast(shape).width
    assert all(val.shape().width == width for val in data_values)

    shifted_bits = [Cat(val[i] for val in data_values).replicate(2).bit_select(offset, len(data)) for i in range(width)]

    shifted_values = [Cat(bits[i] for bits in shifted_bits) for i in range(len(data))]

    if isinstance(shape, Shape):
        return shifted_values
    else:
        return [shape(val) for val in shifted_values]


@overload
def barrel_shift_right(data: Sequence[_T_ValueCastable], offset: ValueLike) -> Sequence[_T_ValueCastable]: ...


@overload
def barrel_shift_right(data: Sequence[ValueLike], offset: ValueLike) -> Sequence[Value]: ...


def barrel_shift_right(data: Sequence[ValueLike | ValueCastable], offset: ValueLike) -> Sequence[Value | ValueCastable]:
    return list(reversed(barrel_shift_left(list(reversed(data)), offset)))
