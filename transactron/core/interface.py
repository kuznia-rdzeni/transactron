import inspect
from types import UnionType
from collections.abc import Callable, Iterable, Sequence, Mapping
from typing import Any, final, get_args, get_origin, overload, is_protocol
from dataclasses import dataclass
from .method import Method, Methods, MethodDir


__all__ = ["InterfaceMember", "InterfaceMembers", "InterfacePart", "interface_members", "provide"]


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

    Interface members are attributes annotated with `Required` and
    `Provided`. Accepted types are `Method` and `Methods` instances,
    arbitrary sequences and mappings of accepted types, and arbitrary
    objects containing interface members.

    If `cls` is given, only the members annotated in `cls` and its base
    classes are collected. `cls` can be a `Protocol`, which the object
    doesn't need to explicitly inherit from. Otherwise, annotations from
    the object's class are used, and unannotated `Method` and `Methods`
    attributes are collected as provided members.

    Members are collected recursively according to their declared types,
    e.g. a member annotated `Provided[list[P]]` is collected as a list of
    objects implementing `P`, using only the members declared in `P`.
    If the declared type is `Any`, the actual types of the values are
    used, as if `cls` was not given.
    """
    if cls is not None and not is_protocol(cls) and not isinstance(obj, cls):
        raise TypeError(f"Object of type {type(obj).__name__} is not an instance of {cls.__name__}")

    hints: dict[str, Any] = {}
    for base in reversed((cls or type(obj)).__mro__):
        hints.update(inspect.get_annotations(base, eval_str=True))

    hint_dirs: dict[str, tuple[MethodDir, object]] = {}
    for name, hint in hints.items():
        dirs = [dir for dir in getattr(hint, "__metadata__", ()) if isinstance(dir, MethodDir)]
        if len(dirs) > 1:
            raise TypeError(f'Interface member "{name}" is annotated with multiple directions')
        elif dirs:
            hint_dirs[name] = (dirs[0], hint.__origin__)

    attrs = vars(obj)
    for name in hint_dirs:
        if name not in attrs:
            raise AttributeError(f'Interface member "{name}" is annotated, but not set')

    ret: dict[str, InterfaceMember] = {}

    for name, attr in attrs.items():
        if name in hint_dirs:
            mdir, hint = hint_dirs[name]
            ret[name] = InterfaceMember(mdir, _interface_part(attr, hint))
        elif cls is None and isinstance(attr, (Method, Methods)):
            ret[name] = InterfaceMember(MethodDir.PROVIDED, _interface_part(attr))

    return InterfaceMembers(ret.items())


@overload
def _interface_part(obj: Method, hint: object | None = None) -> Method: ...


@overload
def _interface_part(obj: Sequence, hint: object | None = None) -> Sequence[InterfacePart]: ...


@overload
def _interface_part(obj: Mapping, hint: object | None = None) -> Mapping[str, InterfacePart]: ...


@overload
def _interface_part(obj: object, hint: object | None = None) -> InterfacePart: ...


def _interface_part(obj, hint: object | None = None) -> InterfacePart:
    if isinstance(obj, InterfaceMembers):
        raise TypeError("interface_part called on InterfaceMembers")

    # Without a declared type, the actual type of `obj` is used.
    if hint is Any:
        hint = None
    origin = type(obj) if hint is None else get_origin(hint) or hint
    args = get_args(hint)
    if origin is UnionType or not isinstance(origin, type):
        raise TypeError(f"Unsupported interface member type: {hint}")
    if not is_protocol(origin) and not isinstance(obj, origin):
        raise TypeError(f"Object of type {type(obj).__name__} is not an instance of {hint}")

    if issubclass(origin, Method):
        return obj
    elif issubclass(origin, tuple) and args and args[-1] is not Ellipsis:
        if len(obj) != len(args):
            raise ValueError(f"Tuple of length {len(obj)} does not match {hint}")
        return tuple(_interface_part(elem, elem_hint) for elem, elem_hint in zip(obj, args))
    elif issubclass(origin, Sequence):
        elem_hint = args[0] if args else None
        return tuple(_interface_part(elem, elem_hint) for elem in obj)
    elif issubclass(origin, Mapping):
        if args and args[0] not in (str, Any) or not all(isinstance(key, str) for key in obj.keys()):
            raise TypeError("Interface mapping keys must be strings")
        elem_hint = args[1] if args else None
        return {key: _interface_part(elem, elem_hint) for (key, elem) in obj.items()}
    else:
        return interface_members(obj, None if hint is None else origin)


def _part_provide(lhs: InterfacePart, rhs: InterfacePart):
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
                _part_provide(lhs_member.iface, rhs_member.iface)
            else:
                _part_provide(rhs_member.iface, lhs_member.iface)
    elif isinstance(lhs, Sequence) and isinstance(rhs, Sequence):
        if len(lhs) != len(rhs):
            raise ValueError(f"Interface sequences have different lengths: {len(lhs)} and {len(rhs)}")
        for lhs_item, rhs_item in zip(lhs, rhs, strict=True):
            _part_provide(lhs_item, rhs_item)
    elif (
        isinstance(lhs, Mapping)
        and isinstance(rhs, Mapping)
        and not isinstance(lhs, InterfaceMembers)
        and not isinstance(rhs, InterfaceMembers)
    ):
        if lhs.keys() != rhs.keys():
            raise ValueError(f"Interface mappings have different keys: {list(lhs.keys())} and {list(rhs.keys())}")
        for key in lhs.keys():
            _part_provide(lhs[key], rhs[key])
    else:
        raise TypeError(f"Incompatible interface parts: {type(lhs).__name__} and {type(rhs).__name__}")


@final
class _Provide:
    """Implementation of `provide`."""

    def __call__(self, lhs: object, rhs: object) -> None:
        """Connect all interface members of `lhs` and `rhs`."""
        _part_provide(_interface_part(lhs), _interface_part(rhs))

    def __getitem__[T](self, cls: type[T]) -> Callable[[T, T], None]:
        """Connect only the interface members declared in `cls`."""
        return lambda lhs, rhs: _part_provide(interface_members(lhs, cls), interface_members(rhs, cls))


provide = _Provide()
"""Connect the interfaces of two objects.

There are two distinct ways to use `provide`.

* `provide(lhs, rhs)`: provided methods of `lhs` are provided by the
  corresponding methods of `rhs`, and required methods of `rhs` are
  provided by the corresponding methods of `lhs`.

  Used this way, `lhs` and `rhs` can be anything `interface_members`
  accepts as the value of an annotated attribute, including `Method`
  instances, sequences (which includes `Methods`), mappings and arbitrary
  objects. When objects are given, every `Method` and `Methods` attribute
  is considered for connection, in addition to all `Provided` and
  `Required` attributes.

* `provide[cls](lhs, rhs)`: only the members declared in `cls` (e.g. a
  `Protocol`) and its base classes are connected, and the type checker
  checks that both objects conform to `cls`. This allows an object
  implementing multiple interfaces to provide each of them separately.

  When used this way, `lhs` and `rhs` must both implement `cls`, if
  `cls` is a `Protocol`, or be instances of `cls`, if `cls` is an
  ordinary class. Only `Provided` and `Required` annotated attributes
  are considered for connection, and nested members are connected
  according to their declared types.

:meta hide-value:

See Also
--------
interface_members : Used internally to identify which methods to provide.
"""
