import pytest
from typing import Annotated, Any, Protocol, TypeVar

from transactron import *
from transactron.core import MethodDir, interface_members
from transactron.testing import TestCaseWithSimulator


class Bag:
    def __init__(self, **kwargs):
        vars(self).update(kwargs)


class P(Protocol):
    p: Provided[Method]
    r: Required[Method]


class Q(Protocol):
    q: Provided[Method]


class SubP(Protocol):
    m: Provided[Method]


class Sub(Bag):
    m: Provided[Method]


class Sub2(Bag):
    m: Provided[Method]
    y: Required[Method]


class Obj:
    p: Provided[Method]
    r: Required[Method]
    q: Provided[Method]
    sub: Required[SubP]
    seq: Provided[tuple[dict[str, SubP], ...]]
    tup: Required[tuple[Method, SubP]]
    any: Provided[Any]
    extra: list[Method]

    def __init__(self, sub_cls: type[Bag] = Sub):
        self.subs: list[Any] = []

        def mk() -> Any:
            self.subs.append(sub_cls(m=Method(), x=Method(), y=Method()))
            return self.subs[-1]

        self.p, self.r, self.q, self.sub = Method(), Method(), Method(), mk()
        self.seq = ({"k": mk()},)
        self.tup = (Method(), mk())
        self.any = Bag(n=Method())
        self.ms = Methods(2)
        self.extra = [Method()]


class PA(Bag):
    a: Provided[Method]


class RA(Bag):
    a: Required[Method]


class Bad(Bag):
    a: Annotated[Method, MethodDir.PROVIDED, MethodDir.REQUIRED]


def typed(hint, value):
    return type("Typed", (Bag,), {"__annotations__": {"a": Provided[hint]}})(a=value)


class TestInterface(TestCaseWithSimulator):
    @pytest.mark.parametrize(
        "cls, expected",
        [
            (None, {"p", "r", "q", "sub", "seq", "tup", "any", "ms"}),
            (P, {"p", "r"}),
            (Q, {"q"}),
            (Obj, {"p", "r", "q", "sub", "seq", "tup", "any"}),
        ],
    )
    def test_provide(self, cls, expected):
        a, b = Obj(), Obj(Sub2)
        assert interface_members(a, cls).keys() == expected
        (provide if cls is None else provide[cls])(a, b)

        pairs = {
            "p": [(a.p, b.p)],
            "r": [(b.r, a.r)],
            "q": [(a.q, b.q)],
            "sub": [(b.sub.m, a.sub.m)],
            "seq": [(a.seq[0]["k"].m, b.seq[0]["k"].m)],
            "tup": [(b.tup[0], a.tup[0]), (b.tup[1].m, a.tup[1].m)],
            "any": [(a.any.n, b.any.n)],
            "ms": list(zip(a.ms, b.ms)),
        }
        for name, methods in pairs.items():
            for lhs, rhs in methods:
                assert (lhs._body_ptr is rhs) == (name in expected)
        for method in [a.extra[0]] + [m for sub in a.subs + b.subs for m in (sub.x, sub.y)]:
            assert method._body_ptr is None

    def test_provide_parts(self):
        lhs, rhs = Method(), Method()
        provide([{"k": lhs}], [{"k": rhs}])
        assert lhs._body_ptr is rhs

    @pytest.mark.parametrize(
        "f, exc",
        [
            (lambda: provide(Bag(a=Method()), Bag(b=Method())), ValueError),
            (lambda: provide(PA(a=Method()), RA(a=Method())), ValueError),
            (lambda: provide([Method()], [Method(), Method()]), ValueError),
            (lambda: provide({"a": Method()}, {"b": Method()}), ValueError),
            (lambda: provide(Method(), [Method()]), TypeError),
            (lambda: provide({1: Method()}, {1: Method()}), TypeError),
            (lambda: provide(interface_members(Bag()), interface_members(Bag())), TypeError),
            (lambda: interface_members(Bad(a=Method())), TypeError),
            (lambda: interface_members(Bag(), Q), AttributeError),
            (lambda: interface_members(Bag(), Obj), TypeError),
            (lambda: interface_members(typed(list[Method], {})), TypeError),
            (lambda: interface_members(typed(Method | None, Method())), TypeError),
            (lambda: interface_members(typed(TypeVar("T"), Method())), TypeError),
            (lambda: interface_members(typed(dict[int, Method], {})), TypeError),
            (lambda: interface_members(typed(tuple[Method, Method], (Method(),))), ValueError),
        ],
    )
    def test_errors(self, f, exc):
        with pytest.raises(exc):
            f()
