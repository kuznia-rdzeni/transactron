from collections.abc import Mapping, Sequence
from amaranth import ValueCastable
import pytest
import random
import enum as pyenum
from typing import Callable
from amaranth import *
from amaranth.lib import data
from amaranth.lib import enum
from amaranth.hdl._ast import ArrayProxy, SwitchValue, Slice
from amaranth_types import ShapeLike

from transactron.utils.typing import MethodLayout
from transactron.utils import AssignType, assign
from transactron.utils.assign import AssignArg, AssignFields


class ExampleEnum(enum.Enum, shape=1):
    ZERO = 0
    ONE = 1


class ExamplePyEnum(enum.Enum):
    ZERO = 0
    ONE = 1


class ExampleIntEnum(enum.IntEnum, shape=1):
    ZERO = 0
    ONE = 1


def with_reversed(pairs: list[tuple[str, str]]):
    return pairs + [(b, a) for (a, b) in pairs]


layout_a = [("a", 1)]
layout_ab = [("a", 1), ("b", 2)]
layout_ac = [("a", 1), ("c", 3)]
layout_a_alt = [("a", 2)]
layout_a_enum = [("a", ExampleEnum)]
layout_a_pyenum = [("a", ExamplePyEnum)]
layout_a_intenum = [("a", ExampleIntEnum)]

# Defines functions build, wrap, extr used in TestAssign
params_funs = {
    "normal": (lambda mk, lay: mk(lay), lambda x: x, lambda r: r),
    "rec": (lambda mk, lay: mk([("x", lay)]), lambda x: {"x": x}, lambda r: r.x),
    "dict": (lambda mk, lay: {"x": mk(lay)}, lambda x: {"x": x}, lambda r: r["x"]),
    "list": (lambda mk, lay: [mk(lay)], lambda x: {0: x}, lambda r: r[0]),
    "union": (
        lambda mk, lay: Signal(data.UnionLayout({"x": reclayout2datalayout(lay)})),
        lambda x: {"x": x},
        lambda r: r.x,
    ),
    "array": (lambda mk, lay: Signal(data.ArrayLayout(reclayout2datalayout(lay), 1)), lambda x: {0: x}, lambda r: r[0]),
}


params_pairs = [(k, k) for k in params_funs if k != "union"] + with_reversed(
    [("rec", "dict"), ("list", "array"), ("union", "dict")]
)


def mkproxy(layout):
    arr = Array([Signal(reclayout2datalayout(layout)) for _ in range(4)])
    sig = Signal(2)
    return arr[sig]


def reclayout2datalayout(layout):
    if not isinstance(layout, list):
        return layout
    return data.StructLayout({k: reclayout2datalayout(lay) for k, lay in layout})


def mkstruct(layout):
    return Signal(reclayout2datalayout(layout))


type ConstType = int | pyenum.Enum | data.Const | Mapping[str, ConstType] | Sequence[ConstType]


def const_from_shape(shape: ShapeLike) -> ConstType:
    if isinstance(shape, int):
        return random.randrange(2**shape)
    elif isinstance(shape, Shape):
        if shape.signed:
            return random.randrange(-(2 ** (shape.width - 1)) - 1, 2 ** (shape.width - 1))
        else:
            return random.randrange(2**shape.width)
    elif isinstance(shape, pyenum.EnumType):
        if random.randrange(2) or issubclass(shape, enum.Enum):  # enums are strict
            return random.choice(list(shape))
        else:
            return random.choice(list(shape)).value
    elif isinstance(shape, data.StructLayout):
        d = {k: const_from_shape(l) for k, l in shape.members.items()}
        if random.randrange(2):
            return shape.const(d)  # type: ignore # dict invariance
        else:
            return d
    elif isinstance(shape, data.UnionLayout):
        k = random.choice(list(shape.members.keys()))
        d = {k: const_from_shape(shape.members[k])}
        if random.randrange(2):
            return shape.const(d)  # type: ignore # dict invariance
        else:
            return d
    elif isinstance(shape, data.ArrayLayout):
        s = tuple(const_from_shape(shape.elem_shape) for _ in range(shape.length))
        if random.randrange(2):
            return shape.const(s)
        else:
            return s
    else:
        raise ValueError("shape unsupported: %s" % shape)


def const_from_assignarg(arg: AssignArg) -> AssignArg:
    if isinstance(arg, Value) or isinstance(arg, ValueCastable):
        return const_from_shape(arg.shape())
    elif isinstance(arg, Sequence):
        return tuple(const_from_assignarg(v) for v in arg)
    elif isinstance(arg, Mapping):
        return {k: const_from_assignarg(v) for k, v in arg.items()}  # type: ignore
    elif isinstance(arg, (int, pyenum.Enum)):
        raise ValueError("const in lhs")


def mkconst(layout):
    return const_from_assignarg(mkstruct(layout))


params_mk = [
    ("proxy", mkproxy),
    ("struct", mkstruct),
]


class TestAssign:
    @pytest.fixture(
        scope="function",
        autouse=True,
        params=[
            tuple(map(staticmethod, params_funs[nl] + params_funs[nr] + (m,)))
            for nl, nr in params_pairs
            for c, m in params_mk
        ],
        ids=[f"{nl}_{nr}_{c}" for nl, nr in params_pairs for c, _ in params_mk],
    )
    def setup_fixture(self, request):
        self.buildl, self.wrapl, self.extrl, self.buildr, self.wrapr, self.extrr, self.mk = request.param

    # constructs `assign` arguments (views, proxies, dicts) which have an "inner" and "outer" part
    # parameterized with a constructor and a layout of the inner part
    buildl: Callable[[Callable[[MethodLayout], AssignArg], MethodLayout], AssignArg]
    buildr: Callable[[Callable[[MethodLayout], AssignArg], MethodLayout], AssignArg]
    # constructs field specifications for `assign`, takes field specifications for the inner part
    wrapl: Callable[[AssignFields], AssignFields]
    wrapr: Callable[[AssignFields], AssignFields]
    # extracts the inner part of the structure
    extrl: Callable[[AssignArg], ArrayProxy]
    extrr: Callable[[AssignArg], ArrayProxy]
    # constructor, takes a layout
    mk: Callable[[MethodLayout], AssignArg]

    def test_wraps_eq(self):
        assert self.wrapl({}) == self.wrapr({})

    def test_rhs_exception(self):
        with pytest.raises(KeyError):
            list(assign(self.buildl(self.mk, layout_a), self.buildr(self.mk, layout_ab), fields=AssignType.RHS))
        with pytest.raises(KeyError):
            list(assign(self.buildl(self.mk, layout_ab), self.buildr(self.mk, layout_ac), fields=AssignType.RHS))

    def test_all_exception(self):
        with pytest.raises(KeyError):
            list(assign(self.buildl(self.mk, layout_a), self.buildr(self.mk, layout_ab), fields=AssignType.ALL))
        with pytest.raises(KeyError):
            list(assign(self.buildl(self.mk, layout_ab), self.buildr(self.mk, layout_a), fields=AssignType.ALL))
        with pytest.raises(KeyError):
            list(assign(self.buildl(self.mk, layout_ab), self.buildr(self.mk, layout_ac), fields=AssignType.ALL))

    def test_missing_exception(self):
        with pytest.raises(KeyError):
            list(assign(self.buildl(self.mk, layout_a), self.buildr(self.mk, layout_ab), fields=self.wrapl({"b"})))
        with pytest.raises(KeyError):
            list(assign(self.buildl(self.mk, layout_ab), self.buildr(self.mk, layout_a), fields=self.wrapl({"b"})))
        with pytest.raises(KeyError):
            list(assign(self.buildl(self.mk, layout_a), self.buildr(self.mk, layout_a), fields=self.wrapl({"b"})))

    def test_wrong_bits(self):
        with pytest.raises(ValueError):
            list(assign(self.buildl(self.mk, layout_a), self.buildr(self.mk, layout_a_alt)))
        if self.mk.__wrapped__ != mkproxy:  # type: ignore # Arrays are troublesome and defeat some checks
            with pytest.raises(ValueError):
                list(assign(self.buildl(self.mk, layout_a), self.buildr(self.mk, layout_a_enum)))

    @pytest.mark.parametrize(
        "name, layout1, layout2, atype",
        [
            ("lhs", layout_a, layout_ab, AssignType.LHS),
            ("rhs", layout_ab, layout_a, AssignType.RHS),
            ("all", layout_a, layout_a, AssignType.ALL),
            ("common", layout_ab, layout_ac, AssignType.COMMON),
            ("set", layout_ab, layout_ab, {"a"}),
            ("list", layout_ab, layout_ab, ["a", "a"]),
        ],
    )
    def test_assign_a(self, name, layout1: MethodLayout, layout2: MethodLayout, atype: AssignType):
        lhs = self.buildl(self.mk, layout1)
        rhs = self.buildr(self.mk, layout2)
        alist = list(assign(lhs, rhs, fields=self.wrapl(atype)))
        assert len(alist) == 1
        self.assertIs_AP(alist[0].lhs, self.extrl(lhs).a)
        self.assertIs_AP(alist[0].rhs, self.extrr(rhs).a)

    @pytest.mark.parametrize(
        "name, layout",
        [
            ("a", layout_a),
            ("ab", layout_ab),
            ("a_enum", layout_a_enum),
            ("a_pyenum", layout_a_pyenum),
            ("a_intenum", layout_a_intenum),
        ],
    )
    def test_assign_const(self, name, layout):
        if self.mk.__wrapped__ == mkproxy:  # type: ignore # Arrays are troublesome
            return
        lhs = self.buildl(self.mk, layout)
        for _ in range(100):
            rhs = self.buildr(mkconst, layout)
            alist = list(assign(lhs, rhs))
            assert len(alist) == len(layout)

    def assertIs_AP(self, expr1, expr2):  # noqa: N802
        expr1 = Value.cast(expr1)
        expr2 = Value.cast(expr2)
        if isinstance(expr1, SwitchValue) and isinstance(expr2, SwitchValue):
            # new proxies are created on each index, structural equality is needed
            assert expr1.test is expr2.test
            assert len(expr1.cases) == len(expr2.cases)
            for (px, x), (py, y) in zip(expr1.cases, expr2.cases):
                assert px == py
                self.assertIs_AP(x, y)
        elif isinstance(expr1, Slice) and isinstance(expr2, Slice):
            self.assertIs_AP(expr1.value, expr2.value)
            assert expr1.start == expr2.start
            assert expr1.stop == expr2.stop
        else:
            assert expr1 is expr2
