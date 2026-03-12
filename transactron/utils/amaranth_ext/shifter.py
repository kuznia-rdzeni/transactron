from amaranth import *
from amaranth.hdl import ValueCastable
from collections.abc import Sequence
from typing import Optional, TypeVar, cast, overload
from amaranth_types import ValueLike, FlatValueLike
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
    """Generic right shift function.

    Shift `value1` right by `offset` bits, fill the empty space with bits
    from `value2`. The bit vectors `value1` and `value2` need to be of the
    same width. This function is used to implement `shift_right` and
    `rotate_right`.

    Parameters
    ----------
    value1 : ValueLike
        The bit vector to be shifted.
    value2 : ValueLike
        The bit vector used to fill space after shifting.
    offset : ValueLike
        The number of bits to shift.

    Returns
    -------
    Value
        The shifted value, the same width as `value1`.
    """
    value1 = Value.cast(value1)
    value2 = Value.cast(value2)
    assert len(value1) == len(value2)
    return Cat(value1, value2).bit_select(offset, len(value1))


def generic_shift_left(value1: ValueLike, value2: ValueLike, offset: ValueLike) -> Value:
    """Generic left shift function.

    Shift `value1` left by `offset` bits, fill the empty space with bits
    from `value2`. The bit vectors `value1` and `value2` need to be of the
    same width. This function is used to implement `shift_left` and
    `rotate_left`.

    Parameters
    ----------
    value1 : ValueLike
        The bit vector to be shifted.
    value2 : ValueLike
        The bit vector used to fill space after shifting.
    offset : ValueLike
        The number of bits to shift.

    Returns
    -------
    Value
        The shifted value, the same width as `value1`.
    """
    value1 = Value.cast(value1)
    value2 = Value.cast(value2)
    return Cat(*reversed(generic_shift_right(Cat(*reversed(value1)), Cat(*reversed(value2)), offset)))


def shift_right(value: ValueLike, offset: ValueLike, placeholder: ValueLike = 0) -> Value:
    """Right shift function.

    Shift `value` right by `offset` bits, fill the empty space with the
    `placeholder` bit (0 by default).

    Differs from `value.shift_right(offset)` in that the shift amount is
    variable. Differs from `value >> offset` in that the placeholder bit
    can be customized.

    Parameters
    ----------
    value : ValueLike
        The bit vector to be shifted.
    offset : ValueLike
        The number of bits to shift.
    placeholder : ValueLike, optional
        The bit used to fill space after shifting.

    Returns
    -------
    Value
        The shifted value, the same width as `value`.
    """
    value = Value.cast(value)
    placeholder = Value.cast(placeholder)
    assert len(placeholder) == 1
    return generic_shift_right(value, placeholder.replicate(len(value)), offset)


def shift_left(value: ValueLike, offset: ValueLike, placeholder: ValueLike = 0) -> Value:
    """Left shift function.

    Shift `value` left by `offset` bits, fill the empty space with the
    `placeholder` bit (0 by default).

    Differs from `value.shift_left(offset)` in that the shift amount is
    variable. Differs from `value << offset` in that the placeholder bit
    can be customized. Differs from both in that the result is of the
    same width as `value`.

    Parameters
    ----------
    value : ValueLike
        The bit vector to be shifted.
    offset : ValueLike
        The number of bits to shift.
    placeholder : ValueLike, optional
        The bit used to fill space after shifting.

    Returns
    -------
    Value
        The shifted value, the same width as `value`.
    """
    value = Value.cast(value)
    placeholder = Value.cast(placeholder)
    assert len(placeholder) == 1
    return generic_shift_left(value, placeholder.replicate(len(value)), offset)


def rotate_right(value: ValueLike, offset: ValueLike) -> Value:
    """Right rotate function.

    Rotate `value` right by `offset` bits.

    Differs from `value.rotate_right(offset)` in that the shift amount is
    variable.

    Parameters
    ----------
    value : ValueLike
        The bit vector to be rotated.
    offset : ValueLike
        The number of bits to rotate.

    Returns
    -------
    Value
        The rotated value, the same width as `value`.
    """
    return generic_shift_right(value, value, offset)


def rotate_left(value: ValueLike, offset: ValueLike) -> Value:
    """Left rotate function.

    Rotate `value` left by `offset` bits.

    Differs from `value.rotate_left(offset)` in that the shift amount is
    variable.

    Parameters
    ----------
    value : ValueLike
        The bit vector to be rotated.
    offset : ValueLike
        The number of bits to rotate.

    Returns
    -------
    Value
        The rotated value, the same width as `value`.
    """
    return generic_shift_left(value, value, offset)


@overload
def generic_shift_vec_right(
    data1: Sequence[_T_ValueCastable], data2: Sequence[_T_ValueCastable], offset: ValueLike
) -> Sequence[_T_ValueCastable]: ...


@overload
def generic_shift_vec_right(
    data1: Sequence[FlatValueLike], data2: Sequence[FlatValueLike], offset: ValueLike
) -> Sequence[Value]: ...


def generic_shift_vec_right(
    data1: Sequence[ValueLike], data2: Sequence[ValueLike], offset: ValueLike
) -> Sequence[Value | ValueCastable]:
    """Generic right shift function for bit vectors and complex data.

    Given `data1` and `data2` which are sequences of `ValueLike` or
    `ValueCastable`, shift `data1` right by `offset` bits, fill the empty
    space with entries from `data2`. The sequences `data1` and `value2` need
    to be of the same length, and their entries must be of the same width.
    This function is used to implement `shift_vec_right` and
    `rotate_vec_right`.

    Parameters
    ----------
    data1 : Sequence[ValueLike]
        The sequence of data to be shifted.
    data2 : Sequence[ValueLike]
        The sequence of data used to fill space after shifting.
    offset : ValueLike
        The number of entries to shift.

    Returns
    -------
    Sequence[Value | ValueCastable]
        The shifted sequence, the same length as `data1`.
    """
    assert len(data1) > 0
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
    data1: Sequence[FlatValueLike], data2: Sequence[FlatValueLike], offset: ValueLike
) -> Sequence[Value]: ...


def generic_shift_vec_left(
    data1: Sequence[ValueLike], data2: Sequence[ValueLike], offset: ValueLike
) -> Sequence[Value | ValueCastable]:
    """Generic left shift function for bit vectors and complex data.

    Given `data1` and `data2` which are sequences of `ValueLike` or
    `ValueCastable`, shift `data1` left by `offset` bits, fill the empty
    space with entries from `data2`. The sequences `data1` and `value2` need
    to be of the same length, and their entries must be of the same width.
    This function is used to implement `shift_vec_left` and
    `rotate_vec_left`.

    Parameters
    ----------
    data1 : Sequence[ValueLike]
        The sequence of data to be shifted.
    data2 : Sequence[ValueLike]
        The sequence of data used to fill space after shifting.
    offset : ValueLike
        The number of entries to shift.

    Returns
    -------
    Sequence[Value | ValueCastable]
        The shifted sequence, the same length as `data1`.
    """
    return list(reversed(generic_shift_vec_right(list(reversed(data1)), list(reversed(data2)), offset)))  # type: ignore


@overload
def shift_vec_right(
    data: Sequence[_T_ValueCastable], offset: ValueLike, placeholder: Optional[_T_ValueCastable]
) -> Sequence[_T_ValueCastable]: ...


@overload
def shift_vec_right(
    data: Sequence[FlatValueLike], offset: ValueLike, placeholder: Optional[ValueLike]
) -> Sequence[Value]: ...


def shift_vec_right(
    data: Sequence[ValueLike],
    offset: ValueLike,
    placeholder: Optional[ValueLike] = None,
) -> Sequence[Value | ValueCastable]:
    """Right shift function for bit vectors and complex data.

    Given `data` which is a sequence of `ValueLike` or `ValueCastable`, shift
    `data` right by `offset` bits, fill the empty space with `placeholder`.
    The entries of `data` must be of the same width.

    Parameters
    ----------
    data : Sequence[ValueLike]
        The sequence of data to be shifted.
    offset : ValueLike
        The number of entries to shift.
    placeholder : ValueLike, optional
        The data used to fill space after shifting.

    Returns
    -------
    Sequence[Value | ValueCastable]
        The shifted sequence, the same length as `data`.
    """
    if placeholder is None:
        shape = shape_of(data[0])
        if isinstance(shape, Shape):
            placeholder = C(0, shape)
        else:
            placeholder = cast(ValueLike, shape.from_bits(0))
    return generic_shift_vec_right(data, [placeholder] * len(data), offset)  # type: ignore


@overload
def shift_vec_left(
    data: Sequence[_T_ValueCastable], offset: ValueLike, placeholder: Optional[_T_ValueCastable]
) -> Sequence[_T_ValueCastable]: ...


@overload
def shift_vec_left(
    data: Sequence[FlatValueLike], offset: ValueLike, placeholder: Optional[FlatValueLike]
) -> Sequence[Value]: ...


def shift_vec_left(
    data: Sequence[ValueLike],
    offset: ValueLike,
    placeholder: Optional[ValueLike] = None,
) -> Sequence[Value | ValueCastable]:
    """Left shift function for bit vectors and complex data.

    Given `data` which is a sequence of `ValueLike` or `ValueCastable`, shift
    `data` left by `offset` bits, fill the empty space with `placeholder`.
    The entries of `data` must be of the same width.

    Parameters
    ----------
    data : Sequence[ValueLike]
        The sequence of data to be shifted.
    offset : ValueLike
        The number of entries to shift.
    placeholder : ValueLike, optional
        The data used to fill space after shifting.

    Returns
    -------
    Sequence[Value | ValueCastable]
        The shifted sequence, the same length as `data`.
    """
    if placeholder is None:
        placeholder = cast(ValueLike, const_of(0, shape_of(data[0])))
    return generic_shift_vec_left(data, [placeholder] * len(data), offset)  # type: ignore


@overload
def rotate_vec_right(data: Sequence[_T_ValueCastable], offset: ValueLike) -> Sequence[_T_ValueCastable]: ...


@overload
def rotate_vec_right(data: Sequence[FlatValueLike], offset: ValueLike) -> Sequence[Value]: ...


def rotate_vec_right(data: Sequence[ValueLike], offset: ValueLike) -> Sequence[Value | ValueCastable]:
    """Right rotate function for bit vectors and complex data.

    Given `data` which is a sequence of `ValueLike` or `ValueCastable`, rotate
    `data` right by `offset` bits. The entries of `data` must be of the same
    width.

    Parameters
    ----------
    data : Sequence[ValueLike]
        The sequence of data to be rotated.
    offset : ValueLike
        The number of entries to rotate.

    Returns
    -------
    Sequence[Value | ValueCastable]
        The rotated sequence, the same length as `data`.
    """
    return generic_shift_vec_right(data, data, offset)  # type: ignore


@overload
def rotate_vec_left(data: Sequence[_T_ValueCastable], offset: ValueLike) -> Sequence[_T_ValueCastable]: ...


@overload
def rotate_vec_left(data: Sequence[FlatValueLike], offset: ValueLike) -> Sequence[Value]: ...


def rotate_vec_left(data: Sequence[ValueLike], offset: ValueLike) -> Sequence[Value | ValueCastable]:
    """Left rotate function for bit vectors and complex data.

    Given `data` which is a sequence of `ValueLike` or `ValueCastable`, rotate
    `data` left by `offset` bits. The entries of `data` must be of the same
    width.

    Parameters
    ----------
    data : Sequence[ValueLike]
        The sequence of data to be rotated.
    offset : ValueLike
        The number of entries to rotate.

    Returns
    -------
    Sequence[Value | ValueCastable]
        The rotated sequence, the same length as `data`.
    """
    return generic_shift_vec_left(data, data, offset)  # type: ignore
