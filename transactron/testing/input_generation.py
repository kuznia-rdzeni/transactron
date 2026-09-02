from collections.abc import Iterable, Mapping
from typing import Any
from amaranth import *
from amaranth import ShapeCastable
from amaranth.sim._async import SimulatorContext
from amaranth_types import ShapeLike
import hypothesis.strategies as st
import enum as py_enum
import math
from amaranth.lib import data
from hypothesis.strategies import DataObject, DrawFn, SearchStrategy
from .simulator import tick


__all__ = [
    "amaranth_consts",
    "amaranth_structs",
    "draw_wait_geom",
    "exponential",
    "generate_input",
    "geometric",
    "intersperse",
    "intersperse_many",
    "intersperse_range",
    "shrinkable_constants",
    "shrinkable_lists",
    "sized_lists",
]


def exponential(rate: float, max_value: float = math.inf) -> SearchStrategy[float]:
    """Returns a strategy which generates exponentially distributed floats.

    The distribution is not perfect, but the values shrink to 0.

    Parameters
    ----------
    rate : float
        Rate parameter.
    max_value : float, optional
        Maximum generated value. If omitted, no upper bound.

    Returns
    -------
    SearchStrategy[float]
        The constructed strategy.
    """
    assert rate > 0
    return st.floats(min_value=0, max_value=1, exclude_max=True).map(lambda u: min(max_value, -math.log1p(-u) / rate))


def geometric(prob: float, max_value: int | None = None) -> SearchStrategy[int]:
    """Returns a strategy which generates geometrically distributed integers.

    The distribution is not perfect, but the values shrink to 0.

    Parameters
    ----------
    prob : float
        Success probability.
    max_value : int, optional
        Maximum generated value. If omitted, no upper bound.

    Returns
    -------
    SearchStrategy[int]
        The constructed strategy.
    """

    def f(val: float):
        if max_value is None:
            return math.floor(val)
        else:
            return min(max_value, math.floor(val))

    return exponential(-math.log1p(-prob)).map(f)


async def draw_wait_geom(ctx: SimulatorContext, data: DataObject, prob: float = 0.5, max_cycle_cnt: int = 32):
    """Awaits a random, geometrically distributed number of cycles.

    This is a Hypothesis compatible equivalent of ``random_wait_geom``.
    """
    await tick(ctx, data.draw(geometric(prob, max_cycle_cnt), label="wait"))


def shrinkable_constants(n: int) -> SearchStrategy[int]:
    """Returns a strategy which generates an almost constant integer, but shrinkable to 0.

    This allows to write tests which usually do someting a constant amount of times,
    which can be reduced when searching for a counterexample.

    Parameters
    ----------
    n : int
        The value of the constant.

    Returns
    -------
    SearchStrategy[int]
        The constructed strategy.
    """
    return st.integers(min_value=0, max_value=100 * n).map(lambda x: min(x, n))


@st.composite
def shrinkable_lists[T](draw: DrawFn, size: int, elements: SearchStrategy[T]) -> list[T]:
    """Returns a strategy which generates lists of given size, but shrinkable.

    This differs from Hypothesis ``lists`` strategy in that it will generate
    lists of constant size for the main test run, but reduce them in size
    when shrinking. This is useful for generating inputs for long test runs,
    but with the ability to produce short counterexamples.

    Parameters
    ----------
    size : int
        Size of the generated lists.
    elements : SearchStrategy[T]
        The strategy for list elements.

    Returns
    -------
    SearchStrategy[list[T]]
        The constructed strategy.

    Notes
    -----
    Trick based on https://github.com/HypothesisWorks/hypothesis/blob/
    6867da71beae0e4ed004b54b92ef7c74d0722815/hypothesis-python/src/hypothesis/stateful.py#L143
    """
    hp_data = draw(st.data())
    lst = []
    while True:
        force_val = True if len(lst) >= size else None
        b = hp_data.conjecture_data.draw_boolean(p=2**-16, forced=force_val)
        if b:
            break
        lst.append(draw(elements))
    return lst


@st.composite
def sized_lists[T](draw: DrawFn, size: SearchStrategy[int], elements: SearchStrategy[T]) -> list[T]:
    """Returns a strategy which generates lists of size given by another strategy.

    Parameters
    ----------
    size : SearchStrategy[int]
        Size of the generated lists.
    elements : SearchStrategy[T]
        The strategy for list elements.

    Returns
    -------
    SearchStrategy[list[T]]
        The constructed strategy.
    """
    size_val = draw(size)
    return draw(st.lists(elements, min_size=size_val, max_size=size_val))


@st.composite
def amaranth_consts(draw: DrawFn, shape: ShapeLike) -> Any:
    """Returns a strategy which generates valid constants for a given shape.

    Parameters
    ----------
    shape : ShapeLike
        Shape for which constants are generated.

    Returns
    -------
    SearchStrategy
        The constructed strategy.
    """
    if isinstance(shape, int):
        return draw(st.integers(min_value=0, max_value=2**shape - 1))
    elif isinstance(shape, Shape):
        if shape.signed:
            return draw(st.integers(min_value=-(2 ** (shape.width - 1)), max_value=2 ** (shape.width - 1) - 1))
        else:
            return draw(st.integers(min_value=0, max_value=2**shape.width - 1))
    elif isinstance(shape, range):
        return draw(st.integers(min_value=shape.start, max_value=shape.stop - 1))
    elif isinstance(shape, py_enum.EnumType):
        return draw(st.sampled_from(shape))
    elif isinstance(shape, data.ArrayLayout):
        return draw(st.lists(amaranth_consts(shape.elem_shape), min_size=shape.length, max_size=shape.length))
    elif isinstance(shape, data.StructLayout):
        return draw(st.fixed_dictionaries({key: amaranth_consts(fld.shape) for key, fld in shape}))
    elif isinstance(shape, data.UnionLayout):
        return draw(st.one_of(*(st.fixed_dictionaries({key: amaranth_consts(fld.shape)}) for key, fld in shape)))
    elif isinstance(shape, ShapeCastable):
        raise ValueError("Unsupported ShapeCastable")


@st.composite
def amaranth_structs(
    draw: DrawFn, layout: data.StructLayout | Mapping[str, ShapeLike], **override: SearchStrategy
) -> dict[str, Any]:
    """Returns a strategy which generates valid constants for structs.

    This differs from ``amaranth_consts`` in that the default strategy can
    be overridden for selected fields.

    Parameters
    ----------
    layout : data.StructLayout | Mapping[str, ShapeLike]
        Shape for which constants are generated.
    **override : SearchStrategy
        Overriding strategies for named layout fields.

    Returns
    -------
    SearchStrategy[dict[str, Any]]
        The constructed strategy.
    """
    if not isinstance(layout, data.StructLayout):
        layout = data.StructLayout(layout)

    for key in override:
        if key not in layout.members:
            raise ValueError(f"Overridden key {key} not present in layout {layout}")
    strategies = {key: amaranth_consts(sh.shape) for key, sh in layout if key not in override}
    strategies.update(override)
    return draw(st.fixed_dictionaries(strategies))


@st.composite
def intersperse[T, U](draw: DrawFn, seq: SearchStrategy[Iterable[T]], sep: SearchStrategy[Iterable[U]]) -> list[T | U]:
    """Returns a strategy which generates lists with separators between elements.

    Separators can consist of multiple elements, both lists and separators
    are specified by parameters.

    Parameters
    ----------
    seq : SearchStrategy[Iterable[T]]
        The strategy for lists.
    sep : SearchStrategy[Iterable[U]]
        The strategy for separators.

    Returns
    -------
    SearchStrategy[list[T | U]]
        The constructed strategy.
    """
    ret: list[T | U] = []
    for elem in draw(seq):
        ret.extend(draw(sep))
        ret.append(elem)
    ret.extend(draw(sep))
    return ret


@st.composite
def intersperse_many[T, U](
    draw: DrawFn, seq: SearchStrategy[Iterable[T]], sep: SearchStrategy[U], count: SearchStrategy[int]
) -> list[T | U]:
    """Returns a strategy which generates lists with separators between elements.

    Separators consist of multiple elements drawn from a separate strategy. The
    number of elements in a separator is also drawn from a strategy.

    Parameters
    ----------
    seq : SearchStrategy[Iterable[T]]
        The strategy for lists.
    sep : SearchStrategy[U]
        The strategy for separator elements.
    count : SearchStrategy[int]
        The strategy for the number of elements in a separator.

    Returns
    -------
    SearchStrategy[list[T | U]]
        The constructed strategy.
    """
    return draw(intersperse(seq, sized_lists(count, sep)))


def intersperse_range[T, U](
    seq: SearchStrategy[Iterable[T]], sep: SearchStrategy[U], *, min_count: int = 0, max_count: int | None = None
) -> SearchStrategy[list[T | U]]:
    """Returns a strategy which generates lists with separators between elements.

    Separators consist of multiple elements drawn from a separate strategy. The
    number of elements in a separator is given by numeric bounds.

    Parameters
    ----------
    seq : SearchStrategy[Iterable[T]]
        The strategy for lists.
    sep : SearchStrategy[U]
        The strategy for separator elements.
    min_count : int, optional
        Lower bound for the number of elements in a separator. If not given,
        defaults to 0.
    max_count : int, optional
        Upper bound for the number of elements in a separator. If not given,
        defaults to unbounded.

    Returns
    -------
    SearchStrategy[list[T | U]]
        The constructed strategy.
    """
    return intersperse(seq, st.lists(sep, min_size=min_count, max_size=max_count))


@st.composite
def generate_input[T](
    draw: DrawFn, count: int, strategy: SearchStrategy[T], prob: float = 0.5, max_nones: int = 32
) -> list[T | None]:
    """Useful shorthand for generating inputs for testing processes.

    Inputs are interspersed by ``None``, which means no input for a given cycle.
    The delays are geometrically distributed. For some test runs, ``None`` will
    not be included.

    The inputs are generated as shrinkable lists so that short counterexamples
    can be automatically constructed.

    Parameters
    ----------
    count : int
        Number of test inputs to generate.
    strategy : SearchStrategy
        Strategy for inputs.
    max_nones : int
        Maximum number of empty inputs in a row. If not given, defaults to
        some preset default.

    Returns
    -------
    SearchStrategy
        The constructed strategy.
    """
    do_intersperse = draw(st.booleans())
    sep_strategy = geometric(prob, max_nones) if do_intersperse else st.just(0)
    return draw(intersperse_many(shrinkable_lists(count, strategy), st.just(None), sep_strategy))
