import inspect
from collections.abc import Iterable, Sequence, Mapping
from typing import Any, final, overload
from dataclasses import dataclass
from .method import Method, Methods, MethodDir


__all__ = ["Interface", "InterfacePart", "InterfaceMember", "interface"]


@final
@dataclass
class InterfaceMember:
    mdir: MethodDir
    iface: "InterfacePart"


# TODO: when frozendicts are introduced, use here
@final
class Interface(Mapping[str, "InterfaceMember"]):
    def __init__(self, members: Iterable[tuple[str, "InterfaceMember"]]):
        self._dict = dict(members)

    def __len__(self):
        return len(self._dict)

    def __getitem__(self, name: str):
        return self._dict[name]

    def __iter__(self):
        return iter(self._dict)


type InterfacePart = Method | Interface | Sequence["InterfacePart"] | Mapping[str, "InterfacePart"]


def interface(obj) -> Interface:
    hints: dict[str, Any] = {}
    for cls in reversed(obj.__class__.__mro__):
        hints.update(inspect.get_annotations(cls, eval_str=True))

    ret: dict[str, InterfaceMember] = {}

    for name, attr in vars(obj).items():
        if name in hints and hasattr(hints[name], "__metadata__"):
            dirs = [dir for dir in hints[name].__metadata__ if isinstance(dir, MethodDir)]
            if len(dirs) != 1:
                continue
            ret[name] = InterfaceMember(dirs[0], interface_part(attr))
        elif isinstance(attr, (Method, Methods)):
            ret[name] = InterfaceMember(MethodDir.PROVIDED, interface_part(attr))

    return Interface(ret.items())


@overload
def interface_part(obj: Method) -> Method: ...


@overload
def interface_part(obj: Sequence) -> Sequence[InterfacePart]: ...


@overload
def interface_part(obj: Mapping) -> Mapping[str, InterfacePart]: ...


def interface_part(obj) -> InterfacePart:
    if isinstance(obj, Method):
        return obj
    elif isinstance(obj, Sequence):
        return tuple(interface_part(elem) for elem in obj)
    elif isinstance(obj, Mapping):
        assert all(isinstance(key, str) for key in obj.keys())
        return {key: interface_part(elem) for (key, elem) in obj.items()}
    else:
        return interface(obj)
