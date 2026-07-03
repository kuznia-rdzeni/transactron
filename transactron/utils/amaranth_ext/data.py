from collections.abc import Callable
from typing import cast
from amaranth import Cat, Value
from amaranth.lib import data
from amaranth_types import ShapeLike


__all__ = ["layout_keys", "transpose"]


def layout_keys(layout: data.Layout) -> list[str | int]:
    if isinstance(layout, data.ArrayLayout):
        return list(range(layout.length))
    elif isinstance(layout, (data.StructLayout, data.UnionLayout)):
        return list(layout.members.keys())
    elif isinstance(layout, data.FlexibleLayout):
        return list(layout.fields.keys())
    else:
        raise ValueError("Argument is not a layout")


def transpose(view: data.View) -> data.View:
    if not isinstance(view.shape(), (data.ArrayLayout, data.StructLayout)):
        raise ValueError("Argument layout is not ArrayLayout nor StructLayout")
    o_keys = layout_keys(view.shape())
    if not o_keys:
        raise ValueError("Argument has no fields")
    if not all(isinstance(view[key], data.View) for key in o_keys):
        raise ValueError("Fields are not all amaranth.data.Views")
    if not all(isinstance(view[key].shape(), (data.ArrayLayout, data.StructLayout)) for key in o_keys):
        raise ValueError("Field layouts are not all ArrayLayouts nor StructLayouts")
    i_keys = layout_keys(view[o_keys[0]])
    if not i_keys:
        raise ValueError("Fields have no fields")
    if not all(layout_keys(view[key]) == i_keys for key in o_keys[1:]):
        raise ValueError("Fields have different keys")

    def mk_layout(keys: list[str | int], cont: Callable[[str | int], ShapeLike]) -> data.Layout:
        if isinstance(keys[0], str):
            nkeys = cast(list[str], keys)
            return data.StructLayout({k: cont(k) for k in nkeys})
        else:
            return data.ArrayLayout(cont(0), len(keys))

    ret_layout = mk_layout(i_keys, lambda i_key: mk_layout(o_keys, lambda o_key: view[o_key][i_key].shape()))
    ret_target = Cat(Value.cast(view[o_key][i_key]) for i_key in i_keys for o_key in o_keys)
    return data.View(ret_layout, ret_target)
