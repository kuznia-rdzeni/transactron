from typing import (
    Callable,
    Concatenate,
    Protocol,
    TypeAlias,
    cast,
    runtime_checkable,
    Union,
    Any,
)
from collections.abc import Iterable, Mapping
from amaranth import *
from amaranth.lib.data import StructLayout, View
from amaranth_types import ShapeLike, ValueLike

__all__ = [
    "MethodLayout",
    "MethodStruct",
    "ValueBundle",
    "LayoutListField",
    "LayoutList",
    "LayoutIterable",
    "RecordIntDict",
    "RecordIntDictRet",
    "RecordValueDict",
    "RecordDict",
    "ROGraph",
    "Graph",
    "GraphCC",
    "HasDebugSignals",
]

# Internal Transactron types
type ValueBundle = Value | Record | View | Iterable["ValueBundle"] | Mapping[str, "ValueBundle"]
type LayoutListField = tuple[str, "ShapeLike | LayoutList"]
type LayoutList = list["LayoutListField"]
type LayoutIterable = Iterable["LayoutListField"]
type MethodLayout = StructLayout | LayoutIterable
MethodStruct: TypeAlias = "View[StructLayout]"  # defined as TypeAlias because of def_method logic

type RecordIntDict = Mapping[str, Union[int, "RecordIntDict"]]
type RecordIntDictRet = Mapping[str, Any]  # full typing hard to work with
type RecordValueDict = Mapping[str, Union[ValueLike, "RecordValueDict"]]
type RecordDict = ValueLike | Mapping[str, "RecordDict"]

type ROGraph[T] = Mapping[T, Iterable[T]]
type Graph[T] = dict[T, set[T]]
type GraphCC[T] = set[T]


@runtime_checkable
class HasDebugSignals(Protocol):
    def debug_signals(self) -> ValueBundle: ...


def type_self_kwargs_as[**P](as_func: Callable[Concatenate[Any, P], Any]):
    """
    Decorator used to annotate `**kwargs` type to be the same as named arguments from `as_func` method.

    Works only with methods with (self, **kwargs) signature. `self` parameter is also required in `as_func`.
    """

    def return_func[T](func: Callable[Concatenate[Any, ...], T]) -> Callable[Concatenate[Any, P], T]:
        return cast(Callable[Concatenate[Any, P], T], func)

    return return_func


def type_self_add_1pos_kwargs_as[
    **P, T, U
](as_func: Callable[Concatenate[Any, P], Any]) -> Callable[
    [Callable[Concatenate[Any, T, ...], U]], Callable[Concatenate[Any, T, P], U]
]:
    """
    Decorator used to annotate `**kwargs` type to be the same as named arguments from `as_func` method.

    Works only with methods with (self, **kwargs) signature. `self` parameter is also required in `as_func`.
    """

    def return_func(func: Callable[Concatenate[Any, T, ...], U]) -> Callable[Concatenate[Any, T, P], U]:
        return cast(Callable[Concatenate[Any, T, P], U], func)

    return return_func
