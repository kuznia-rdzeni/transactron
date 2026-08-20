from typing import Any, Optional, overload
from amaranth import *
from amaranth.hdl import ShapeCastable, ValueCastable
from amaranth.hdl._ast import SwitchValue
from amaranth.utils import bits_for, ceil_log2
from amaranth.lib import data, enum
from collections.abc import Callable, Iterable, Mapping, Sequence
import operator

from amaranth_types import FlatValueLike, SrcLoc, SwitchKey
from amaranth_types.types import ValueLike, ShapeLike
from transactron.utils.transactron_helpers import get_src_loc
from transactron.utils.typing import ValueBundle
from transactron.utils.logging import top_assertion

__all__ = [
    "mod_incr",
    "mod_add",
    "popcount",
    "count_leading_zeros",
    "count_trailing_zeros",
    "cyclic_mask",
    "flatten_signals",
    "shape_of",
    "const_of",
    "binary_tree_reduce",
    "sum_value",
    "or_value",
    "and_value",
    "generic_min_value",
    "min_value",
    "max_value",
    "switch_value",
    "mux",
    "one_hot_mux",
    "extract_lowest_set_bit",
    "clear_lowest_set_bit",
    "mask_from_first_set_bit",
    "mask_after_first_set_bit",
    "mask_until_first_set_bit",
    "mask_before_first_set_bit",
]


def mod_incr(sig: ValueLike, mod: int) -> Value:
    """
    Perform `(sig+1) % mod` operation.
    """
    assert mod > 0
    sig = Value.cast(sig)
    if not (mod & (mod - 1)):
        return (sig + 1) & (mod - 1)
    return Mux(sig == mod - 1, 0, sig + 1)


def mod_add(sig: ValueLike, mod: int, incr: ValueLike, max_incr: int):
    """
    Perform `(sig+incr) % mod` operation, for `0 < incr <= max_incr`.
    """
    assert mod > 0
    assert max_incr >= 0
    sig = Value.cast(sig)
    incr = Value.cast(incr)
    if not (mod & (mod - 1)):
        return (sig + incr) & (mod - 1)
    return SwitchValue(sig + incr, [(mod + i, i) for i in range(0, max_incr)] + [(None, sig + incr)])


def popcount(s: Value):
    sum_layers = [s[i] for i in range(len(s))]

    while len(sum_layers) > 1:
        if len(sum_layers) % 2:
            sum_layers.append(C(0))
        sum_layers = [a + b for a, b in zip(sum_layers[::2], sum_layers[1::2])]

    return sum_layers[0][0 : bits_for(len(s))]


def count_leading_zeros(s: Value) -> Value:
    def iter(s: Value, step: int) -> Value:
        # if no bits left - return empty value
        if step == 0:
            return C(0)

        # boudaries of upper and lower halfs of the value
        partition = 2 ** (step - 1)
        current_bit = 1 << (step - 1)

        # recursive call
        upper_value = iter(s[partition:], step - 1)
        lower_value = iter(s[:partition], step - 1)

        # if there are lit bits in upperhalf - take result directly from recursive value
        # otherwise add 1 << (step - 1) to lower value and return
        result = Mux(s[partition:].any(), upper_value, lower_value | current_bit)

        return result

    slen = len(s)
    slen_log = ceil_log2(slen)
    closest_pow_2_of_s = 2**slen_log
    zeros_prepend_count = closest_pow_2_of_s - slen
    value = iter(Cat(C(0, shape=zeros_prepend_count), s), slen_log)

    # 0 number edge case
    # if s == 0 then iter() returns value off by 1
    # this switch negates this effect
    result = Mux(s.any(), value, slen)
    return result


def count_trailing_zeros(s: Value) -> Value:
    return count_leading_zeros(s[::-1])


def cyclic_mask(bits: int, start: Value, end: Value):
    """
    Generate `bits` bit-wide mask with ones from `start` to `end` position, including both ends.
    If `end` value is < than `start` the mask wraps around.
    """
    start = start.as_unsigned()
    end = end.as_unsigned()

    # start <= end
    length = (end - start + 1).as_unsigned()
    mask_se = ((1 << length) - 1) << start

    # start > end
    left = (1 << (end + 1)) - 1
    right = (1 << ((bits - start).as_unsigned())) - 1
    mask_es = left | (right << start)

    return Mux(start <= end, mask_se, mask_es)


def flatten_signals(signals: ValueBundle) -> Iterable[Value]:
    """
    Flattens input data, which can be either a signal, a record, a list (or a dict) of SignalBundle items.

    """
    if isinstance(signals, Mapping):
        for x in signals.values():
            yield from flatten_signals(x)
    elif isinstance(signals, Iterable):
        for x in signals:
            yield from flatten_signals(x)
    elif isinstance(signals, data.View):
        for x, _ in signals.shape():
            yield from flatten_signals(signals[x])
    else:
        yield signals


def shape_of(value: ValueLike) -> Shape | ShapeCastable:
    value_type = type(value)
    if isinstance(value, ValueCastable):
        shape = value.shape()
        assert isinstance(shape, (Shape, ShapeCastable))
        return shape
    elif isinstance(value_type, enum.EnumType):  # hack for enums
        return value_type
    else:
        return Value.cast(value).shape()


def const_of(value: int, shape: ShapeLike) -> Any:
    if isinstance(shape, ShapeCastable):
        return shape.from_bits(value)
    else:
        return C(value, Shape.cast(shape))


@overload
def _uniformize_values(
    values: Iterable[FlatValueLike],
) -> tuple[Callable[[Value], Value], list[Value]]: ...


@overload
def _uniformize_values[
    T: ValueCastable
](values: Iterable[T],) -> tuple[Callable[[Value], T], list[Value]]: ...


@overload
def _uniformize_values(
    values: Iterable[ValueLike],
) -> tuple[Callable[[Value], Value | ValueCastable], list[Value]]: ...


def _uniformize_values(
    values: Iterable[ValueLike],
) -> tuple[Callable[[Value], Value | ValueCastable], list[Value]]:
    values = list(values)
    shapes = [shape_of(v) for v in values]
    shapecastable_shapes = [shape for shape in shapes if isinstance(shape, ShapeCastable)]
    if not shapecastable_shapes:
        return (lambda v: v), [Value.cast(v) for v in values]

    shape = shapecastable_shapes[0]
    if any(case_shape != shape for case_shape in shapecastable_shapes):
        raise ValueError("Different ShapeCastables for different shapes")

    def unify(v):
        return Value.cast(v) if isinstance(v, (Value, ValueCastable)) else Value.cast(shape.const(v))

    return (lambda v: shape(v)), [unify(v) for v in values]


def binary_tree_reduce(*values: ValueBundle, neutral: Value, operator: Callable[[Value, Value], Value]) -> Value:
    min_layers = list(flatten_signals(values))
    if not min_layers:
        min_layers.append(neutral)

    while len(min_layers) > 1:
        tail = [min_layers[-1]] if len(min_layers) % 2 else []
        min_layers = [operator(a, b) for a, b in zip(min_layers[::2], min_layers[1::2])] + tail

    return min_layers[0]


def sum_value(*values: ValueBundle):
    return binary_tree_reduce(*values, neutral=C(0, 0), operator=operator.add)


def or_value(*values: ValueBundle):
    return binary_tree_reduce(*values, neutral=C(0, 0), operator=operator.or_)


def and_value(*values: ValueBundle):
    return binary_tree_reduce(*values, neutral=C(-1), operator=operator.and_)


def generic_min_value(*values: ValueBundle, operator: Callable[[Value, Value], Value]) -> Value:
    def binary_min(v1: Value, v2: Value):
        return Mux(operator(v1, v2), v1, v2)

    return binary_tree_reduce(*values, neutral=C(0), operator=binary_min)


def min_value(*values: ValueBundle) -> Value:
    return generic_min_value(*values, operator=operator.lt)


def max_value(*values: ValueBundle) -> Value:
    return generic_min_value(*values, operator=operator.gt)


@overload
def switch_value(
    test: ValueLike,
    cases: Iterable[tuple[SwitchKey | tuple[SwitchKey, ...] | None, FlatValueLike]],
    *,
    src_loc: int | SrcLoc = 0,
) -> Value: ...


@overload
def switch_value[
    T: ValueCastable
](
    test: ValueLike, cases: Iterable[tuple[SwitchKey | tuple[SwitchKey, ...] | None, T]], *, src_loc: int | SrcLoc = 0
) -> T: ...


@overload
def switch_value(
    test: ValueLike,
    cases: Iterable[tuple[SwitchKey | tuple[SwitchKey, ...] | None, ValueLike]],
    *,
    src_loc: int | SrcLoc = 0,
) -> ValueLike: ...


def switch_value(
    test: ValueLike,
    cases: Iterable[tuple[SwitchKey | tuple[SwitchKey, ...] | None, ValueLike]],
    *,
    src_loc: int | SrcLoc = 0,
) -> ValueLike:
    src_loc = get_src_loc(src_loc)
    cases = list(cases)
    shape_cast, values = _uniformize_values(val for _, val in cases)
    ret_val = SwitchValue(test, [(key, val) for (key, _), val in zip(cases, values)], src_loc=src_loc)
    return shape_cast(ret_val)


@overload
def mux(sel: ValueLike, val1: FlatValueLike, val0: FlatValueLike) -> Value: ...


@overload
def mux[T: ValueCastable](sel: ValueLike, val1: T, val0: FlatValueLike) -> T: ...


@overload
def mux[T: ValueCastable](sel: ValueLike, val1: FlatValueLike, val0: T) -> T: ...


@overload
def mux[T: ValueCastable](sel: ValueLike, val1: T, val0: T) -> T: ...


@overload
def mux(sel: ValueLike, val1: ValueLike, val0: ValueLike) -> ValueLike: ...


def mux(sel: ValueLike, val1: ValueLike, val0: ValueLike) -> ValueLike:
    return switch_value(sel, [(0, val0), (None, val1)], src_loc=1)


@overload
def one_hot_mux[
    T: ValueCastable
](
    inputs: Sequence[tuple[ValueLike, T]],
    default: Optional[T] = None,
    priority: bool = False,
    assert_one_hot: bool = True,
) -> T: ...


@overload
def one_hot_mux(
    inputs: Sequence[tuple[ValueLike, FlatValueLike]],
    default: Optional[FlatValueLike] = None,
    priority: bool = False,
    assert_one_hot: bool = True,
) -> Value: ...


@overload
def one_hot_mux(
    inputs: Sequence[tuple[ValueLike, ValueLike]],
    default: Optional[ValueLike] = None,
    priority: bool = False,
    assert_one_hot: bool = True,
) -> Value | ValueCastable: ...


def one_hot_mux(
    inputs: Sequence[tuple[ValueLike, ValueLike]],
    default: Optional[ValueLike] = None,
    priority: bool = False,
    assert_one_hot: bool = False,
) -> Value | ValueCastable:
    """
    One-hot multiplexer.
    Takes n input values and n one-hot select signals and outputs the value corresponding to the set select signal.

    Parameters
    ----------
    inputs : Sequence[tuple[ValueLike, ValueLike]]
        Sequence of tuples, where each tuple contains a select signal and a corresponding value.
    default: ValueLike, optional
        Default value to output if no select signal is set. If not provided, when no select signal is set, the output
        is undefined.
    priority : bool, default False
        If True, the output corresponds to the lowest entry with set select signal.
        If False, the output is undefined if multiple select signals are set.
    assert_one_hot : bool, default False
        If True, an assertion is added that checks if undefined output is produced.
    """
    inputs = list(inputs)
    select = Cat(Value.cast(sel).bool() for sel, _ in inputs)
    data = [val for _, val in inputs]

    if not inputs and default is None:
        raise ValueError("No inputs provided to one_hot_mux")

    if default is None and assert_one_hot:
        top_assertion(
            select.any(),
            "Select signal must be one-hot, but no bits are set",
            src_loc=1,
        )

    select_first = extract_lowest_set_bit(select)
    select_one_hot = select_first if priority else select

    if not priority and assert_one_hot:
        top_assertion(
            select == select_first,
            "Select signal must be one-hot with priority=False, select: {:b}",
            select,
            src_loc=1,
        )

    all_sel = select_one_hot if default is None else Cat(select_one_hot, ~select.any())
    shape_cast, all_data = _uniformize_values(data if default is None else data + [default])

    if len(all_data) == 1:
        return shape_cast(all_data[0])

    return shape_cast(or_value([Mux(all_sel[i], all_data[i], C(0, 0)) for i in range(len(all_data))]))


def extract_lowest_set_bit(value: Value) -> Value:
    """
    Extracts the least significant set bit from the input value.
    If no bits are set, returns ``0``.
    For example ``0b010100`` -> ``0b000100``.
    Same as: ``(1 << count_trailing_zeros(value))[: len(value)]``.
    """
    return (value & -value)[: len(value)]


def clear_lowest_set_bit(value: Value) -> Value:
    """
    Clears the least significant set bit from the input value.
    If no bits are set, returns ``0``.
    For example ``0b110100`` -> ``0b110000``.
    Same as: ``value & ~extract_lowest_set_bit(value)``
    """
    return (value & (value - 1))[: len(value)]


def mask_from_first_set_bit(value: Value) -> Value:
    """
    Generates a mask from the least significant set bit (inclusive) in the input value upto its length.
    For example ``0b010100`` -> ``0b111100``.
    Same as: ``(-1 << count_trailing_zeros(value))[: len(value)]``.
    """
    return (value | -value)[: len(value)]


def mask_after_first_set_bit(value: Value) -> Value:
    """
    Generates a mask from the least significant set bit (exclusive) in the input value upto its length.
    For example ``0b010100`` -> ``0b111000``.
    """
    return (mask_from_first_set_bit(value) << 1)[: len(value)]


def mask_until_first_set_bit(value: Value) -> Value:
    """
    Generates a mask from the 0-th bit upto the least significant set bit in the input (inclusive).
    For example ``0b010100`` -> ``0b000111``.
    """
    return ~mask_after_first_set_bit(value)


def mask_before_first_set_bit(value: Value) -> Value:
    """
    Generates a mask from the 0-th bit upto the least significant set bit in the input (exclusive).
    For example ``0b010100`` -> ``0b000011``.
    Same as: ``extract_lowest_set_bit(value) - 1``.
    """
    return ~mask_from_first_set_bit(value)
