import inspect
from collections.abc import Sequence, Mapping
from typing import Any, final, overload
from dataclasses import dataclass

from amaranth_types import HasElaborate
from .method import Method, Methods, MethodDir


__all__ = ["Interface", "InterfaceMember", "interface"]


type Interface = Method | Sequence["InterfaceMember"] | Mapping[str, "InterfaceMember"]

@final
@dataclass
class InterfaceMember:
    dir: MethodDir
    iface: Interface


@overload
def interface(obj: Method) -> Method:
    ...

@overload
def interface(obj: Sequence) -> Sequence[InterfaceMember]:
    ...

@overload
def interface(obj: Mapping) -> Mapping[str, InterfaceMember]:
    ...

@overload
def interface(obj: HasElaborate) -> Mapping[str, InterfaceMember]:
    ...

def interface(obj) -> Interface:
    if isinstance(obj, Method):
        return obj
    elif isinstance(obj, Sequence):
        return tuple(InterfaceMember(MethodDir.PROVIDED, interface(elem)) for elem in obj)
    elif isinstance(obj, Mapping):
        assert all(isinstance(key, str) for key in obj.keys())
        return {key: InterfaceMember(MethodDir.PROVIDED, interface(elem)) for (key, elem) in obj.items()}
    else:
        hints: dict[str, Any] = {}
        for cls in reversed(obj.__class__.__mro__):
            hints.update(inspect.get_annotations(cls, eval_str=True))

        ret: dict[str, InterfaceMember] = {}

        for name, attr in vars(obj).items():
            if name in hints and hasattr(hints[name], "__metadata__"):
                dirs = [dir for dir in hints[name].__metadata__ if isinstance(dir, MethodDir)]
                if len(dirs) != 1:
                    continue
                ret[name] = InterfaceMember(dirs[0], interface(attr))
            elif isinstance(attr, (Method, Methods)):
                ret[name] = InterfaceMember(MethodDir.PROVIDED, interface(attr))

        return ret
