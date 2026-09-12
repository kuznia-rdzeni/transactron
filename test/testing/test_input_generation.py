from typing import Any, cast
from amaranth import Shape
from amaranth_types import ShapeLike
from amaranth.lib import data
from transactron.testing.input_generation import *
from hypothesis import given, settings, strategies as st
import pytest
import itertools
import math
import enum as py_enum


@pytest.mark.parametrize("prob", [0.2, 0.5, 0.8])
def test_geometric(prob: float):
    values: list[int] = []

    expected_avg = (1 - prob) / prob

    # Max value is given because Hypothesis generates outliers more often than distribution implies

    @settings(max_examples=500)
    @given(geometric(prob, math.ceil(expected_avg * 20)))
    def f(val):
        values.append(val)
        assert isinstance(val, int)
        assert val >= 0

    f()

    # average is in the right ballpark
    average = sum(values) / len(values)
    assert expected_avg / 3 <= average <= expected_avg * 3


@pytest.mark.parametrize("value", [10, 1000])
def test_shrinkable_constants(value: int):
    values: list[int] = []

    @settings(max_examples=200)
    @given(shrinkable_constants(value))
    def f(val):
        values.append(val)
        assert isinstance(val, int)
        assert val <= value

    f()

    # mostly generates the constant value
    assert values.count(value) / len(values) >= 0.8


@pytest.mark.parametrize("size", [5, 20])
def test_shrinkable_lists(size: int):
    sizes: list[int] = []

    @settings(max_examples=200)
    @given(shrinkable_lists(size, st.integers()))
    def f(elems: list[int]):
        sizes.append(len(elems))
        assert len(elems) <= size
        assert all(isinstance(i, int) for i in elems)

    f()

    # mostly generates the full size
    assert sizes.count(size) / len(sizes) >= 0.7


@pytest.mark.parametrize("size_range", [(0, 5), (0, 20), (5, 20), (3, 3)])
def test_sized_lists_range(size_range: tuple[int, int]):
    lo, hi = size_range
    sizes: list[int] = []
    strategy = st.integers(min_value=lo, max_value=hi)

    @settings(
        max_examples=500,
    )
    @given(sized_lists(strategy, st.integers()))
    def f(elems: list[int]):
        sizes.append(len(elems))
        assert lo <= len(elems) and len(elems) <= hi
        assert all(isinstance(i, int) for i in elems)

    f()

    assert lo in sizes
    expected_avg = (lo + hi) / 2
    average = sum(sizes) / len(sizes)
    assert expected_avg * 0.5 <= average <= expected_avg * 1.5


def validate_const(shape: ShapeLike, v: Any):
    if isinstance(shape, int):
        assert isinstance(v, int)
        assert v >= 0 and v < 2**shape
    elif isinstance(shape, Shape):
        assert isinstance(v, int)
        if shape.signed:
            assert v >= -(2 ** (shape.width - 1)) and v < 2 ** (shape.width - 1)
        else:
            assert v >= -0 and v < 2**shape.width
    elif isinstance(shape, range):
        assert isinstance(v, int)
        assert v >= shape.start and v < 2**shape.stop
    elif isinstance(shape, py_enum.EnumType):
        assert isinstance(v, shape)
        assert v in shape
    elif isinstance(shape, data.ArrayLayout):
        assert isinstance(v, list)
        assert len(v) == shape.length
        for e in v:
            validate_const(shape.elem_shape, e)
    elif isinstance(shape, data.StructLayout):
        assert isinstance(v, dict)
        for key in v:
            assert key in shape.members
        for key, fld in shape:
            assert key in v
            validate_const(fld.shape, v[key])
    elif isinstance(shape, data.UnionLayout):
        assert isinstance(v, dict)
        assert len(v) == 1
        (key,) = tuple(v)
        assert key in shape.members
        validate_const(shape[key].shape, v[key])
    else:
        raise ValueError("Unsupported ShapeLike")


class FooEnum(py_enum.Enum):
    FOO = 0
    BAR = 1
    BAZ = 2


class BarEnum(py_enum.Enum):
    FOO = 2
    BAR = 7
    BAZ = 42


@pytest.mark.parametrize(
    "shape",
    [0, 1, 8, 32]
    + [range(1), range(2), range(8), range(-12, 35), range(-1234, 56)]
    + [FooEnum, BarEnum]
    + [data.StructLayout({}), data.StructLayout({"a": 5, "b": FooEnum})]
    + [data.ArrayLayout(5, 0), data.ArrayLayout(BarEnum, 5)]
    + [data.UnionLayout({"a": 5, "b": FooEnum})],
)
def test_amaranth_consts(shape: ShapeLike):
    @given(amaranth_consts(shape))
    def f(v):
        validate_const(shape, v)

    f()


def test_amaranth_structs():
    @given(amaranth_structs(data.StructLayout({"a": 5, "b": FooEnum}), a=st.just(1)))
    def f(v):
        assert isinstance(v, dict)
        assert v["a"] == 1
        validate_const(FooEnum, v["b"])

    f()


@given(st.lists(st.integers(), max_size=10), st.lists(st.integers(), max_size=3), st.data())
def test_intersperse(seq_list: list[int], sep_list: list[int], data: st.DataObject):
    result = data.draw(intersperse(st.just(seq_list), st.just(sep_list)))
    expected = list(itertools.chain(sep_list, *(itertools.chain([elem], sep_list) for elem in seq_list)))
    assert result == expected


@given(st.lists(st.integers(), max_size=10), st.integers(), st.integers(min_value=0, max_value=4), st.data())
def test_intersperse_many(seq_list: list[int], sep: int, k: int, data: st.DataObject):
    result = data.draw(intersperse_many(st.just(seq_list), st.just(sep), st.just(k)))
    sep_list = [sep] * k
    expected = list(itertools.chain(sep_list, *(itertools.chain([elem], sep_list) for elem in seq_list)))
    assert result == expected


@given(st.integers(0, 5), st.lists(st.integers(), max_size=10), st.data())
def test_intersperse_range(lo: int, seq_list: list[int], data: st.DataObject):
    hi = data.draw(st.one_of(st.integers(lo, 5), st.just(None)))
    result = data.draw(intersperse_range(st.just(seq_list), st.just("sep"), min_count=lo, max_count=hi))

    groups = [list(group) for is_str, group in itertools.groupby(result, lambda x: isinstance(x, str)) if is_str]
    groups = cast(list[list[str]], groups)

    assert len(groups) <= len(seq_list) + 1
    if lo:
        assert len(groups) == len(seq_list) + 1
    assert all(lo <= len(gr) for gr in groups)
    if hi is not None:
        assert all(hi >= len(gr) for gr in groups)
    assert [x for x in result if isinstance(x, int)] == seq_list
