from amaranth import *
from amaranth.hdl import ValueCastable
from collections.abc import Sequence
from typing import Optional, TypeVar, cast, overload
from amaranth_types.types import ValueLike
from .functions import shape_of, const_of


__all__ = [
    "generic_shift_right",
    "generic_shift_left",
    "shift_right",
    "shift_left",
    "rotate_right",
    "rotate_left",
    "generic_shift_vec_right",
    "generic_shift_vec_left",
    "shift_vec_right",
    "shift_vec_left",
    "rotate_vec_right",
    "rotate_vec_left",
]


_T_ValueCastable = TypeVar("_T_ValueCastable", bound=ValueCastable)


def generic_shift_right(value1: ValueLike, value2: ValueLike, offset: ValueLike) -> Value:
    value1 = Value.cast(value1)
    value2 = Value.cast(value2)
    assert len(value1) == len(value2)
    return Cat(value1, value2).bit_select(offset, len(value1))


def generic_shift_left(value1: ValueLike, value2: ValueLike, offset: ValueLike) -> Value:
    value1 = Value.cast(value1)
    value2 = Value.cast(value2)
    return Cat(*reversed(generic_shift_right(Cat(*reversed(value1)), Cat(*reversed(value2)), offset)))


def shift_right(value: ValueLike, offset: ValueLike, placeholder: ValueLike = 0) -> Value:
    value = Value.cast(value)
    placeholder = Value.cast(placeholder)
    assert len(placeholder) == 1
    return generic_shift_right(value, placeholder.replicate(len(value)), offset)


def shift_left(value: ValueLike, offset: ValueLike, placeholder: ValueLike = 0) -> Value:
    value = Value.cast(value)
    placeholder = Value.cast(placeholder)
    assert len(placeholder) == 1
    return generic_shift_left(value, placeholder.replicate(len(value)), offset)


def rotate_right(value: ValueLike, offset: ValueLike) -> Value:
    return generic_shift_right(value, value, offset)


def rotate_left(value: ValueLike, offset: ValueLike) -> Value:
    return generic_shift_left(value, value, offset)


@overload
def generic_shift_vec_right(
    data1: Sequence[_T_ValueCastable], data2: Sequence[_T_ValueCastable], offset: ValueLike
) -> Sequence[_T_ValueCastable]: ...


@overload
def generic_shift_vec_right(
    data1: Sequence[ValueLike], data2: Sequence[ValueLike], offset: ValueLike
) -> Sequence[Value]: ...


def generic_shift_vec_right(
    data1: Sequence[ValueLike | ValueCastable], data2: Sequence[ValueLike | ValueCastable], offset: ValueLike
) -> Sequence[Value | ValueCastable]:
    shape = shape_of(data1[0])

    data1_values = [Value.cast(entry) for entry in data1]
    data2_values = [Value.cast(entry) for entry in data2]
    width = Shape.cast(shape).width

    assert len(data1_values) == len(data2_values)
    assert all(val.shape().width == width for val in data1_values)
    assert all(val.shape().width == width for val in data2_values)

    bits1 = [Cat(val[i] for val in data1_values) for i in range(width)]
    bits2 = [Cat(val[i] for val in data2_values) for i in range(width)]

    shifted_bits = [generic_shift_right(b1, b2, offset) for b1, b2 in zip(bits1, bits2)]

    shifted_values = [Cat(bits[i] for bits in shifted_bits) for i in range(len(data1))]

    if isinstance(shape, Shape):
        return shifted_values
    else:
        return [shape(val) for val in shifted_values]


@overload
def generic_shift_vec_left(
    data1: Sequence[_T_ValueCastable], data2: Sequence[_T_ValueCastable], offset: ValueLike
) -> Sequence[_T_ValueCastable]: ...


@overload
def generic_shift_vec_left(
    data1: Sequence[ValueLike], data2: Sequence[ValueLike], offset: ValueLike
) -> Sequence[Value]: ...


def generic_shift_vec_left(
    data1: Sequence[ValueLike | ValueCastable], data2: Sequence[ValueLike | ValueCastable], offset: ValueLike
) -> Sequence[Value | ValueCastable]:
    return list(reversed(generic_shift_vec_right(list(reversed(data1)), list(reversed(data2)), offset)))


@overload
def shift_vec_right(
    data: Sequence[_T_ValueCastable], offset: ValueLike, placeholder: Optional[_T_ValueCastable]
) -> Sequence[_T_ValueCastable]: ...


@overload
def shift_vec_right(
    data: Sequence[ValueLike], offset: ValueLike, placeholder: Optional[ValueLike]
) -> Sequence[Value]: ...


def shift_vec_right(
    data: Sequence[ValueLike | ValueCastable],
    offset: ValueLike,
    placeholder: Optional[ValueLike | ValueCastable] = None,
) -> Sequence[Value | ValueCastable]:
    if placeholder is None:
        shape = shape_of(data[0])
        if isinstance(shape, Shape):
            placeholder = C(0, shape)
        else:
            placeholder = cast(ValueLike, shape.from_bits(0))
    return generic_shift_vec_right(data, [placeholder] * len(data), offset)


@overload
def shift_vec_left(
    data: Sequence[_T_ValueCastable], offset: ValueLike, placeholder: Optional[_T_ValueCastable]
) -> Sequence[_T_ValueCastable]: ...


@overload
def shift_vec_left(
    data: Sequence[ValueLike], offset: ValueLike, placeholder: Optional[ValueLike]
) -> Sequence[Value]: ...


def shift_vec_left(
    data: Sequence[ValueLike | ValueCastable],
    offset: ValueLike,
    placeholder: Optional[ValueLike | ValueCastable] = None,
) -> Sequence[Value | ValueCastable]:
    if placeholder is None:
        placeholder = cast(ValueLike, const_of(0, shape_of(data[0])))
    return generic_shift_vec_left(data, [placeholder] * len(data), offset)


@overload
def rotate_vec_right(data: Sequence[_T_ValueCastable], offset: ValueLike) -> Sequence[_T_ValueCastable]: ...


@overload
def rotate_vec_right(data: Sequence[ValueLike], offset: ValueLike) -> Sequence[Value]: ...


def rotate_vec_right(data: Sequence[ValueLike | ValueCastable], offset: ValueLike) -> Sequence[Value | ValueCastable]:
    return generic_shift_vec_right(data, data, offset)


@overload
def rotate_vec_left(data: Sequence[_T_ValueCastable], offset: ValueLike) -> Sequence[_T_ValueCastable]: ...


@overload
def rotate_vec_left(data: Sequence[ValueLike], offset: ValueLike) -> Sequence[Value]: ...


def rotate_vec_left(data: Sequence[ValueLike | ValueCastable], offset: ValueLike) -> Sequence[Value | ValueCastable]:
    return generic_shift_vec_left(data, data, offset)
