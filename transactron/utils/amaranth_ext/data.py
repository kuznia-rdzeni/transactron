from collections.abc import Callable, Sequence
from typing import cast, overload
from amaranth import Cat, Value
from amaranth.lib import data
from amaranth_types import ShapeLike


__all__ = ["layout_keys", "transpose_layout_with_keys", "transpose_layout", "transpose"]


@overload
def layout_keys(layout: data.ArrayLayout) -> Sequence[int]: ...


@overload
def layout_keys(layout: data.StructLayout | data.UnionLayout) -> Sequence[str]: ...


@overload
def layout_keys(layout: data.Layout) -> Sequence[str | int]: ...


def layout_keys(layout: data.Layout) -> Sequence[str | int]:
    if isinstance(layout, data.ArrayLayout):
        return list(range(layout.length))
    elif isinstance(layout, (data.StructLayout, data.UnionLayout)):
        return list(layout.members.keys())
    elif isinstance(layout, data.FlexibleLayout):
        return list(layout.fields.keys())
    else:
        raise ValueError("Argument is not a layout")


def transpose_layout_with_keys(layout: data.Layout):
    if not isinstance(cast(object, layout), (data.ArrayLayout, data.StructLayout)):
        raise ValueError("Argument layout is not ArrayLayout nor StructLayout")
    o_keys = layout_keys(layout)
    if not o_keys:
        raise ValueError("Argument has no fields")
    i_layouts = {key: layout[key].shape for key in o_keys}
    if not all(isinstance(i_layouts[key], (data.ArrayLayout, data.StructLayout)) for key in o_keys):
        raise ValueError("Field layouts are not all ArrayLayouts nor StructLayouts")
    i_layouts = cast(dict[str | int, data.Layout], i_layouts)
    i_keys = layout_keys(i_layouts[o_keys[0]])
    if not i_keys:
        raise ValueError("Fields have no fields")
    if not all(layout_keys(i_layouts[key]) == i_keys for key in o_keys[1:]):
        raise ValueError("Fields have different keys")

    def mk_layout(keys: Sequence[str | int], cont: Callable[[str | int], ShapeLike]) -> data.Layout:
        if isinstance(keys[0], str):
            nkeys = cast(list[str], keys)
            return data.StructLayout({k: cont(k) for k in nkeys})
        else:
            return data.ArrayLayout(cont(0), len(keys))

    ret_layout = mk_layout(i_keys, lambda i_key: mk_layout(o_keys, lambda o_key: i_layouts[o_key][i_key].shape))
    return ret_layout, o_keys, i_keys


def transpose_layout(layout: data.Layout) -> data.Layout:
    return transpose_layout_with_keys(layout)[0]


def transpose(view: data.View) -> data.View:
    ret_layout, o_keys, i_keys = transpose_layout_with_keys(view.shape())
    ret_target = Cat(Value.cast(view[o_key][i_key]) for i_key in i_keys for o_key in o_keys)
    return data.View(ret_layout, ret_target)
