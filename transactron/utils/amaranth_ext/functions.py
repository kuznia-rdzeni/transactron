from typing import Any, Optional, cast, overload
from amaranth import *
from amaranth.hdl import ShapeCastable, ValueCastable
from amaranth.hdl._ast import SwitchValue
from amaranth.utils import bits_for, ceil_log2
from amaranth.lib import data
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
    if isinstance(value, ValueCastable):
        shape = value.shape()
        assert isinstance(shape, (Shape, ShapeCastable))
        return shape
    else:
        return Value.cast(value).shape()


def const_of(value: int, shape: ShapeLike) -> Any:
    if isinstance(shape, ShapeCastable):
        return shape.from_bits(value)
    else:
        return C(value, Shape.cast(shape))


def binary_tree_reduce(*values: ValueBundle, neutral: Value, operator: Callable[[Value, Value], Value]) -> Value:
    min_layers = list(flatten_signals(values))
    if not min_layers:
        min_layers.append(neutral)

    while len(min_layers) > 1:
        tail = [min_layers[-1]] if len(min_layers) % 2 else []
        min_layers = [operator(a, b) for a, b in zip(min_layers[::2], min_layers[1::2])] + tail

    return min_layers[0]


def sum_value(*values: ValueBundle):
    return binary_tree_reduce(*values, neutral=C(0), operator=operator.add)


def or_value(*values: ValueBundle):
    return binary_tree_reduce(*values, neutral=C(0), operator=operator.or_)


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
    case_shapes = [shape_of(val) for _, val in cases]
    if all(isinstance(shape, ShapeCastable) for shape in case_shapes):
        shape = cast(ShapeCastable, case_shapes[0])
        if any(case_shape != shape for case_shape in case_shapes):
            raise ValueError("Different ShapeCastables for different shapes")
        return shape(SwitchValue(test, [(key, Value.cast(val)) for key, val in cases], src_loc=src_loc))
    else:
        return SwitchValue(test, cases, src_loc=src_loc)


@overload
def mux(sel: ValueLike, val1: FlatValueLike, val0: FlatValueLike) -> Value: ...


@overload
def mux[T: ValueCastable](sel: ValueLike, val1: T, val0: T) -> T: ...


@overload
def mux(sel: ValueLike, val1: ValueLike, val0: ValueLike) -> ValueLike: ...


def mux(sel: ValueLike, val1: ValueLike, val0: ValueLike) -> ValueLike:
    return switch_value(sel, [(0, val0), (None, val1)], src_loc=1)


def one_hot_mux(
    select: ValueLike,
    inputs: Sequence[ValueLike],
    default: Optional[ValueLike] = None,
    priority: bool = False,
    assert_one_hot: bool = True,
) -> Value:
    """
    One-hot multiplexer.
    Takes n input values and a one-hot select signal of n bits and outputs the value corresponding
    to the bit set in the select signal.
    If priority is False and multiple bits are set, the output is undefined.
    If priority is True and multiple bits are set, the output corresponds to the lowest set bit in the select signal.
    If assert_one_hot is True and priority is False, an assertion is added that checks
    that the select signal is one-hot.
    If default is provided and select signal is 0, the output is default,
    otherwise select must have at least one bit set.
    """
    inputs = list(inputs)

    if len(inputs) == 0:
        return Value.cast(default) if default is not None else C(0)

    select = Value.cast(select).as_unsigned()

    if default is None and assert_one_hot:
        top_assertion(
            select.any(),
            "Select signal must be one-hot, but no bits are set",
            src_loc=1,
        )

    if default is None and len(inputs) == 1:
        return Value.cast(inputs[0])

    select_first = select & (~select + 1)
    select_one_hot = select_first if priority else select

    if not priority and assert_one_hot:
        top_assertion(
            select == select_first,
            "Select signal must be one-hot with priority=False, select: {:b}",
            select,
            src_loc=1,
        )

    value_combined = or_value([Mux(select_one_hot[i], Value.cast(inputs[i]), 0) for i in range(len(inputs))])

    if default is None:
        return value_combined

    return Mux(select.any(), value_combined, Value.cast(default))
