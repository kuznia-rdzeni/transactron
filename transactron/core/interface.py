import inspect
from collections.abc import Callable, Iterable, Sequence, Mapping
from typing import Any, final, overload
from dataclasses import dataclass
from .method import Method, Methods, MethodDir


__all__ = ["Interface", "InterfaceMember", "InterfaceMembers", "InterfacePart", "interface_members", "provide"]


@final
@dataclass(frozen=True)
class InterfaceMember:
    mdir: MethodDir
    iface: "InterfacePart"


# TODO: when frozendicts are introduced in Python, use here
@final
class InterfaceMembers(Mapping[str, "InterfaceMember"]):
    def __init__(self, members: Iterable[tuple[str, "InterfaceMember"]]):
        self._dict = dict(members)

    def __len__(self):
        return len(self._dict)

    def __getitem__(self, name: str):
        return self._dict[name]

    def __iter__(self):
        return iter(self._dict)


type InterfacePart = Method | InterfaceMembers | Sequence["InterfacePart"] | Mapping[str, "InterfacePart"]


def interface_members(obj, cls: type | None = None) -> InterfaceMembers:
    """Collect the interface members of an object.

    If `cls` is given, only the members annotated in `cls` and its base
    classes are collected. Otherwise, annotations from the object's class
    are used, and unannotated `Method` and `Methods` attributes are
    collected as provided members.
    """
    if cls is not None and not isinstance(obj, cls):
        raise TypeError(f"Object of type {type(obj).__name__} is not an instance of {cls.__name__}")

    hints: dict[str, Any] = {}
    for base in reversed((cls or type(obj)).__mro__):
        hints.update(inspect.get_annotations(base, eval_str=True))

    hint_dirs: dict[str, MethodDir] = {}
    for name, hint in hints.items():
        dirs = [dir for dir in getattr(hint, "__metadata__", ()) if isinstance(dir, MethodDir)]
        if len(dirs) > 1:
            raise TypeError(f'Interface member "{name}" is annotated with multiple directions')
        elif dirs:
            hint_dirs[name] = dirs[0]

    attrs = vars(obj)
    for name in hint_dirs:
        if name not in attrs:
            raise AttributeError(f'Interface member "{name}" is annotated, but not set')

    ret: dict[str, InterfaceMember] = {}

    for name, attr in attrs.items():
        if name in hint_dirs:
            ret[name] = InterfaceMember(hint_dirs[name], interface_part(attr))
        elif cls is None and isinstance(attr, (Method, Methods)):
            ret[name] = InterfaceMember(MethodDir.PROVIDED, interface_part(attr))

    return InterfaceMembers(ret.items())


@overload
def interface_part(obj: Method) -> Method: ...


@overload
def interface_part(obj: Sequence) -> Sequence[InterfacePart]: ...


@overload
def interface_part(obj: Mapping) -> Mapping[str, InterfacePart]: ...


@overload
def interface_part(obj: object) -> InterfacePart: ...


def interface_part(obj) -> InterfacePart:
    if isinstance(obj, InterfaceMembers):
        raise TypeError("interface_part called on InterfaceMembers")
    elif isinstance(obj, Method):
        return obj
    elif isinstance(obj, Sequence):
        return tuple(interface_part(elem) for elem in obj)
    elif isinstance(obj, Mapping):
        if not all(isinstance(key, str) for key in obj.keys()):
            raise TypeError("Interface mapping keys must be strings")
        return {key: interface_part(elem) for (key, elem) in obj.items()}
    else:
        return interface_members(obj)


def part_provide(lhs: InterfacePart, rhs: InterfacePart):
    if isinstance(lhs, Method) and isinstance(rhs, Method):
        lhs.provide(rhs)
    elif isinstance(lhs, InterfaceMembers) and isinstance(rhs, InterfaceMembers):
        if lhs.keys() != rhs.keys():
            raise ValueError(f"Interface members have different names: {list(lhs.keys())} and {list(rhs.keys())}")
        for key in lhs.keys():
            lhs_member = lhs[key]
            rhs_member = rhs[key]
            if lhs_member.mdir != rhs_member.mdir:
                raise ValueError(f'Interface members "{key}" have different directions')
            elif lhs_member.mdir == MethodDir.PROVIDED:
                part_provide(lhs_member.iface, rhs_member.iface)
            else:
                part_provide(rhs_member.iface, lhs_member.iface)
    elif isinstance(lhs, Sequence) and isinstance(rhs, Sequence):
        if len(lhs) != len(rhs):
            raise ValueError(f"Interface sequences have different lengths: {len(lhs)} and {len(rhs)}")
        for lhs_item, rhs_item in zip(lhs, rhs, strict=True):
            part_provide(lhs_item, rhs_item)
    elif (
        isinstance(lhs, Mapping)
        and isinstance(rhs, Mapping)
        and not isinstance(lhs, InterfaceMembers)
        and not isinstance(rhs, InterfaceMembers)
    ):
        if lhs.keys() != rhs.keys():
            raise ValueError(f"Interface mappings have different keys: {list(lhs.keys())} and {list(rhs.keys())}")
        for key in lhs.keys():
            part_provide(lhs[key], rhs[key])
    else:
        raise TypeError(f"Incompatible interface parts: {type(lhs).__name__} and {type(rhs).__name__}")


def provide(lhs, rhs):
    part_provide(interface_part(lhs), interface_part(rhs))


def _scoped_provide(cls: type, lhs, rhs):
    part_provide(interface_members(lhs, cls), interface_members(rhs, cls))


class _MembersDescriptor:
    @overload
    def __get__[T](self, obj: None, owner: type[T]) -> Callable[[T], InterfaceMembers]: ...

    @overload
    def __get__(self, obj: object, owner: Any = None) -> Callable[[], InterfaceMembers]: ...

    def __get__(self, obj: Any, owner: Any = None) -> Any:
        if obj is None:
            return lambda obj: interface_members(obj, owner)
        else:
            return lambda: interface_members(obj, type(obj))


class _ProvideDescriptor:
    @overload
    def __get__[T](self, obj: None, owner: type[T]) -> Callable[[T, T], None]: ...

    @overload
    def __get__[T](self, obj: T, owner: Any = None) -> Callable[[T], None]: ...

    def __get__(self, obj: Any, owner: Any = None) -> Any:
        if obj is None:
            return lambda lhs, rhs: _scoped_provide(owner, lhs, rhs)
        else:
            return lambda rhs: _scoped_provide(type(obj), obj, rhs)


class Interface:
    """Base class for interfaces.

    The members of an interface are its attributes annotated with
    `Provided` or `Required`. When `members` or `provide` is accessed
    through a class, e.g. `A.provide(lhs, rhs)`, only the members declared
    in that class and its base classes are used. This allows a class
    implementing multiple interfaces to provide each of them separately.
    When accessed through an object, the object's class is used.
    """

    members = _MembersDescriptor()
    provide = _ProvideDescriptor()
