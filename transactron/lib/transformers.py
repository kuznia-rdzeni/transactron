from amaranth import *
from amaranth_types import ValueLike, ModuleLike, HasElaborate

from transactron.utils.transactron_helpers import get_src_loc
from ..core import *
from ..utils import SrcLoc
from typing import Iterable, Optional, Protocol
from collections.abc import Callable, Sequence
from transactron.utils import (
    assign,
    AssignType,
    MethodStruct,
    MethodLayout,
    RecordDict,
)
from .connectors import Forwarder, CrossbarConnectTrans
from .simultaneous import condition

__all__ = [
    "Transformer",
    "Unifier",
    "MethodMap",
    "MethodFilter",
    "MethodProduct",
    "MethodTryProduct",
    "Collector",
    "NonexclusiveWrapper",
]


class Transformer(HasElaborate, Protocol):
    """Method transformer abstract class.

    Method transformers construct a new method which utilizes other methods.
    """

    method: Provided[Method]
    """Method created by the transformer."""

    def use(self, m: ModuleLike):
        """Returns the method and adds the transformer to a module.

        Parameters
        ----------
        m: Module or TModule
            The module to which this transformer is added as a submodule.
        """
        m.submodules += self
        return self.method


class TransformerOneTarget(Transformer, Protocol):
    target: Required[Method]
    """Method called by the transformer."""


class TransformerMultiTarget(Transformer, Protocol):
    targets: Required[Sequence[Method]]
    """Methods called by the transformer."""


class Unifier(TransformerMultiTarget, Protocol):
    def __init__(self, targets: list[Method]): ...


class MethodMap(Elaboratable, TransformerOneTarget):
    """Bidirectional map for methods.

    Takes a target method and creates a transformed method which calls the
    original target method, mapping the input and output values with
    functions. The mapping functions take two parameters, a `Module` and the
    structure being transformed. Alternatively, a `Method` can be
    passed.
    """

    def __init__(
        self,
        i_layout: MethodLayout = (),
        o_layout: MethodLayout = (),
        *,
        i_transform: Optional[tuple[MethodLayout, Callable[[TModule, MethodStruct], RecordDict]]] = None,
        o_transform: Optional[tuple[MethodLayout, Callable[[TModule, MethodStruct], RecordDict]]] = None,
        src_loc: int | SrcLoc = 0,
    ):
        """
        Parameters
        ----------
        i_layout: MethodLayout
            Input layout of the `target` method.
        o_layout: MethodLayout
            Output layout of the `target` method.
        i_transform: (method layout, function or Method), optional
            Input mapping function. If specified, it should be a pair of a
            function and a input layout for the transformed method.
            If not present, input is passed unmodified.
        o_transform: (method layout, function or Method), optional
            Output mapping function. If specified, it should be a pair of a
            function and a output layout for the transformed method.
            If not present, output is passed unmodified.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        if i_transform is None:
            i_transform = (i_layout, lambda _, x: x)
        if o_transform is None:
            o_transform = (o_layout, lambda _, x: x)

        self.target = Method(i=i_layout, o=o_layout)
        src_loc = get_src_loc(src_loc)
        self.method = Method(i=i_transform[0], o=o_transform[0], src_loc=src_loc)
        self.i_fun = i_transform[1]
        self.o_fun = o_transform[1]

    @staticmethod
    def create(
        target: Method,
        *,
        i_transform: Optional[tuple[MethodLayout, Callable[[TModule, MethodStruct], RecordDict]]] = None,
        o_transform: Optional[tuple[MethodLayout, Callable[[TModule, MethodStruct], RecordDict]]] = None,
        src_loc: int | SrcLoc = 0,
    ):
        """
        Parameters
        ----------
        target: Method
            The target method.
        i_transform: (method layout, function or Method), optional
            See constructor.
        o_transform: (method layout, function or Method), optional
            See constructor.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        src_loc = get_src_loc(src_loc)
        tr = MethodMap(
            target.layout_in, target.layout_out, i_transform=i_transform, o_transform=o_transform, src_loc=src_loc
        )
        tr.target.provide(target)
        return tr

    def elaborate(self, platform):
        m = TModule()

        @def_method(m, self.method)
        def _(arg):
            return self.o_fun(m, self.target(m, self.i_fun(m, arg)))

        return m


class MethodFilter(Elaboratable, TransformerOneTarget):
    """Method filter.

    Takes a target method and creates a method which calls the target method
    only when some condition is true. The condition function takes two
    parameters, a module and the input structure of the method. Non-zero
    return value is interpreted as true. Alternatively to using a function,
    a `Method` can be passed as a condition.

    By default, the target method is locked for use even if it is not called.
    If this is not the desired effect, set `use_condition` to True, but this will
    cause that the provided method will be `single_caller` and all other `condition`
    drawbacks will be in place (e.g. risk of exponential complexity).
    """

    def __init__(
        self,
        i_layout: MethodLayout,
        o_layout: MethodLayout,
        condition: Callable[[TModule, MethodStruct], ValueLike],
        default: Optional[RecordDict] = None,
        *,
        use_condition: bool = False,
        src_loc: int | SrcLoc = 0,
    ):
        """
        Parameters
        ----------
        i_layout: MethodLayout
            Input layout of the `target` method.
        o_layout: MethodLayout
            Output layout of the `target` method.
        condition: function or Method
            The condition which, when true, allows the call to `target`. When
            false, `default` is returned.
        default: Value or dict, optional
            The default value returned from the filtered method when the condition
            is false. If omitted, zero is returned.
        use_condition : bool
            Instead of `m.If` use simultaneus `condition` which allow to execute
            this filter if the condition is False and target is not ready.
            When `use_condition` is true, `condition` must not be a `Method`.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        self.target = Method(i=i_layout, o=o_layout)
        self.use_condition = use_condition
        src_loc = get_src_loc(src_loc)
        self.method = Method(i=i_layout, o=o_layout, src_loc=src_loc)
        self.condition = condition
        self.default = default if default is not None else Signal(self.target.layout_out)

        assert not (use_condition and isinstance(condition, Method))

    @staticmethod
    def create(
        target: Method,
        condition: Callable[[TModule, MethodStruct], ValueLike],
        default: Optional[RecordDict] = None,
        *,
        use_condition: bool = False,
        src_loc: int | SrcLoc = 0,
    ):
        """
        Parameters
        ----------
        target: Method
            The target method.
        condition: function or Method
            See constructor.
        default: Value or dict, optional
            See constructor.
        use_condition : bool
            See constructor.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        src_loc = get_src_loc(src_loc)
        tr = MethodFilter(
            target.layout_in, target.layout_out, condition, default, use_condition=use_condition, src_loc=src_loc
        )
        tr.target.provide(target)
        return tr

    def elaborate(self, platform):
        m = TModule()

        ret = Signal.like(self.target.data_out)
        m.d.comb += assign(ret, self.default, fields=AssignType.ALL)

        @def_method(m, self.method, single_caller=self.use_condition)
        def _(arg):
            if self.use_condition:
                cond = Signal()
                m.d.top_comb += cond.eq(self.condition(m, arg))
                with condition(m, nonblocking=True) as branch:
                    with branch(cond):
                        m.d.comb += ret.eq(self.target(m, arg))
            else:
                with m.If(self.condition(m, arg)):
                    m.d.comb += ret.eq(self.target(m, arg))
            return ret

        return m


class MethodProduct(Elaboratable, Unifier):
    """Method product.

    Takes arbitrary, non-zero number of target methods, and constructs
    a method which calls all of the target methods using the same
    argument. The return value of the resulting method is, by default,
    the return value of the first of the target methods. A combiner
    function can be passed, which can compute the return value from
    the results of every target method.
    """

    def __init__(
        self,
        i_layout: MethodLayout = (),
        o_layouts: Iterable[MethodLayout] = (),
        combiner: Optional[tuple[MethodLayout, Callable[[TModule, list[MethodStruct]], RecordDict]]] = None,
        *,
        src_loc: int | SrcLoc = 0,
    ):
        """
        Parameters
        ----------
        i_layout: MethodLayout
            Input layout of the `targets` methods.
        o_layouts: Iterable[MethodLayout]
            Output layouts of each of the `targets` methods.
        combiner: (int or method layout, function), optional
            A pair of the output layout and the combiner function. The
            combiner function takes two parameters: a `Module` and
            a list of outputs of the target methods.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        o_layouts = tuple(o_layouts)
        if combiner is None:
            combiner = (o_layouts[0], lambda _, x: x[0])
        self.targets = [Method(i=i_layout, o=o_layout) for o_layout in o_layouts]
        self.combiner = combiner
        src_loc = get_src_loc(src_loc)
        self.method = Method(i=i_layout, o=combiner[0], src_loc=src_loc)

    @staticmethod
    def create(
        targets: Iterable[Method],
        combiner: Optional[tuple[MethodLayout, Callable[[TModule, list[MethodStruct]], RecordDict]]] = None,
        *,
        src_loc: int | SrcLoc = 0,
    ):
        """
        Parameters
        ----------
        targets: Iterable[Method]
            The target methods.
        combiner: (int or method layout, function), optional
            See constructor.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        targets = list(targets)
        src_loc = get_src_loc(src_loc)
        tr = MethodProduct(
            i_layout=targets[0].layout_in,
            o_layouts=[target.layout_out for target in targets],
            combiner=combiner,
            src_loc=src_loc,
        )
        for m1, m2 in zip(tr.targets, targets):
            m1.provide(m2)
        return tr

    def elaborate(self, platform):
        m = TModule()

        @def_method(m, self.method)
        def _(arg):
            results = []
            for target in self.targets:
                results.append(target(m, arg))
            return self.combiner[1](m, results)

        return m


class MethodTryProduct(Elaboratable, Unifier):
    """Method product with optional calling.

    Takes arbitrary, non-zero number of target methods, and constructs
    a method which tries to call all of the target methods using the same
    argument. The methods which are not ready are not called. The return
    value of the resulting method is, by default, empty. A combiner
    function can be passed, which can compute the return value from the
    results of every target method.
    """

    def __init__(
        self,
        i_layout: MethodLayout = (),
        o_layouts: Iterable[MethodLayout] = (),
        combiner: Optional[
            tuple[MethodLayout, Callable[[TModule, list[tuple[Value, MethodStruct]]], RecordDict]]
        ] = None,
        *,
        src_loc: int | SrcLoc = 0,
    ):
        """
        Parameters
        ----------
        i_layout: MethodLayout
            Input layout of the `targets` methods.
        o_layouts: Iterable[MethodLayout]
            Output layouts of each of the `targets` methods.
        combiner: (int or method layout, function), optional
            A pair of the output layout and the combiner function. The
            combiner function takes two parameters: a `TModule` and
            a list of pairs. Each pair contains a bit which signals
            that a given call succeeded, and the result of the call.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        if combiner is None:
            combiner = ([], lambda _, __: {})
        self.targets = [Method(i=i_layout, o=o_layout) for o_layout in o_layouts]
        self.combiner = combiner
        self.src_loc = get_src_loc(src_loc)
        self.method = Method(i=i_layout, o=combiner[0], src_loc=self.src_loc)

    @staticmethod
    def create(
        targets: Iterable[Method],
        combiner: Optional[
            tuple[MethodLayout, Callable[[TModule, list[tuple[Value, MethodStruct]]], RecordDict]]
        ] = None,
        *,
        src_loc: int | SrcLoc = 0,
    ):
        """
        Parameters
        ----------
        targets: Iterable[Method]
            The target methods.
        combiner: (int or method layout, function), optional
            See constructor.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        targets = list(targets)
        src_loc = get_src_loc(src_loc)
        tr = MethodTryProduct(
            targets[0].layout_in, [target.layout_out for target in targets], combiner, src_loc=src_loc
        )
        for m1, m2 in zip(tr.targets, targets):
            m1.provide(m2)
        return tr

    def elaborate(self, platform):
        m = TModule()

        @def_method(m, self.method)
        def _(arg):
            results: list[tuple[Value, MethodStruct]] = []
            for target in self.targets:
                success = Signal()
                with Transaction(src_loc=self.src_loc).body(m):
                    m.d.comb += success.eq(1)
                    results.append((success, target(m, arg)))
            return self.combiner[1](m, results)

        return m


class Collector(Elaboratable, Unifier):
    """Single result collector.

    Creates method that collects results of many methods with identical
    layouts. Each call of this method will return a single result of one
    of the provided methods.
    """

    def __init__(self, count: int = 1, o_layout: MethodLayout = (), *, src_loc: int | SrcLoc = 0):
        """
        Parameters
        ----------
        count: int
            The number of target methods.
        o_layout: MethodLayout
            Output layout of the `targets` methods.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        self.src_loc = get_src_loc(src_loc)
        self.targets = Methods(count, o=o_layout, src_loc=self.src_loc)
        self.method = Method(o=o_layout, src_loc=self.src_loc)

    @staticmethod
    def create(targets: Iterable[Method], *, src_loc: int | SrcLoc = 0):
        """
        Parameters
        ----------
        targets: Iterable[Method]
            Methods from which results will be collected.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        targets = list(targets)
        src_loc = get_src_loc(src_loc)
        tr = Collector(len(targets), targets[0].layout_out)
        for m1, m2 in zip(tr.targets, targets):
            m1.provide(m2)
        return tr

    def elaborate(self, platform):
        m = TModule()

        m.submodules.forwarder = forwarder = Forwarder(self.method.layout_out, src_loc=self.src_loc)
        m.submodules.connect = CrossbarConnectTrans.create(self.targets, forwarder.write, src_loc=self.src_loc)

        self.method.provide(forwarder.read)

        return m


class NonexclusiveWrapper(Elaboratable, TransformerOneTarget):
    """Nonexclusive wrapper around a method.

    Useful when you can assume, for external reasons, that a given method will
    never be called more than once in a given clock cycle - even when the
    call graph indicates it could.

    Possible use case is unifying parallel pipelines with the same latency.
    """

    def __init__(self, i_layout: MethodLayout = (), o_layout: MethodLayout = (), *, src_loc: int | SrcLoc = 0):
        """
        Parameters
        ----------
        i_layout: MethodLayout
            Input layout of the `target` method.
        o_layout: MethodLayout
            Output layout of the `target` method.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        src_loc = get_src_loc(src_loc)
        self.target = Method(i=i_layout, o=o_layout, src_loc=src_loc)
        self.method = Method(i=i_layout, o=o_layout, src_loc=src_loc)

    @staticmethod
    def create(target: Method, *, src_loc: int | SrcLoc = 0):
        """
        Parameters
        ----------
        target: Method
            The target method.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        src_loc = get_src_loc(src_loc)
        tr = NonexclusiveWrapper(target.layout_in, target.layout_out)
        tr.target.provide(target)
        return tr

    def elaborate(self, platform):
        m = TModule()

        @def_method(m, self.method, nonexclusive=True)
        def _(arg):
            return self.target(m, arg)

        return m
