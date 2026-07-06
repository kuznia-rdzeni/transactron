from collections.abc import Callable, Sequence
from typing import cast, overload
from amaranth import Cat, Value
from amaranth.lib import data
from amaranth_types import ShapeLike


__all__ = ["layout_keys", "transpose_layout", "transpose_layout_with_keys", "transpose"]


@overload
def layout_keys(layout: data.ArrayLayout) -> Sequence[int]: ...


@overload
def layout_keys(layout: data.StructLayout | data.UnionLayout) -> Sequence[str]: ...


@overload
def layout_keys(layout: data.Layout) -> Sequence[str | int]: ...


def layout_keys(layout: data.Layout) -> Sequence[str | int]:
    """Get the field keys of a layout, in order.

    Parameters
    ----------
    layout : data.Layout
        The layout to get the keys of. If it is an `ArrayLayout`, the keys are
        the field indices (`int`); if it is a `StructLayout` or `UnionLayout`,
        the keys are the field names (`str`).

    Returns
    -------
    Sequence[str | int]
        The keys of `layout`, in the order in which they are defined.
    """
    return [k for k, _ in layout]


def transpose_layout(layout: data.Layout) -> data.Layout:
    """Transpose a two-level layout.

    `layout` is expected to be an `ArrayLayout` or `StructLayout` whose every field
    is itself an `ArrayLayout` or `StructLayout`, with all of the fields sharing the
    same set of inner keys. The transposition swaps the outer and inner levels, so
    that a value which was addressed as ``value[o_key][i_key]`` in the original
    layout is addressed as ``transposed[i_key][o_key]`` in the resulting layout.

    Parameters
    ----------
    layout : data.Layout
        The layout to transpose. Must be an `ArrayLayout` or `StructLayout` whose
        fields are all `ArrayLayout`\\s or `StructLayout`\\s sharing identical keys.

    Returns
    -------
    data.Layout
        The transposed layout.

    Raises
    ------
    ValueError
        If `layout` is not an `ArrayLayout` or `StructLayout`; if it has no fields;
        if its fields are not all `ArrayLayout`\\s or `StructLayout`\\s; if its
        fields have no keys; or if its fields do not all share the same keys.
    """
    return transpose_layout_with_keys(layout)[0]


def transpose_layout_with_keys(layout: data.Layout) -> tuple[data.Layout, Sequence[str | int], Sequence[str | int]]:
    """Extended version of `transpose_layout` that also returns the keys of both levels.

    Behaves exactly like `transpose_layout`, but additionally returns the outer and
    inner keys of `layout` that were used to perform the transposition, so that
    callers can address the original fields without recomputing them.

    Parameters
    ----------
    layout : data.Layout
        The layout to transpose. See `transpose_layout` for the requirements it
        must satisfy.

    Returns
    -------
    tuple[data.Layout, Sequence[str | int], Sequence[str | int]]
        A tuple ``(ret_layout, o_keys, i_keys)``, where `ret_layout` is the layout
        that would be returned by `transpose_layout`, `o_keys` are the outer
        (top-level) keys of `layout`, and `i_keys` are the inner keys shared by
        all of its fields.

    Raises
    ------
    ValueError
        See `transpose_layout`.
    """
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


@overload
def transpose(view: data.Const) -> data.Const: ...


@overload
def transpose(view: data.View) -> data.View: ...


def transpose(view: data.View | data.Const) -> data.View | data.Const:
    """Transpose a view or constant over a two-level layout.

    Builds a new `View` or `Const` (matching the type of `view`), over the layout
    returned by `transpose_layout`, whose contents are the same as `view` but with
    the outer and inner levels swapped, so that ``transpose(view)[i_key][o_key]``
    is equal to ``view[o_key][i_key]`` for every valid pair of keys.

    Parameters
    ----------
    view : data.View | data.Const
        The view or constant to transpose. Its shape must satisfy the requirements
        of `transpose_layout`.

    Returns
    -------
    data.View | data.Const
        Transposed `Const`, if `view` is a `data.Const`, otherwise transposed
        `View`; over transposed layout.

    Raises
    ------
    ValueError
        See `transpose_layout`.
    """
    ret_layout, o_keys, i_keys = transpose_layout_with_keys(view.shape())
    if isinstance(view, data.Const):
        return ret_layout.const({i_key: {o_key: view[o_key][i_key] for o_key in o_keys} for i_key in i_keys})
    ret_target = Cat(Value.cast(view[o_key][i_key]) for i_key in i_keys for o_key in o_keys)
    return data.View(ret_layout, ret_target)
