from collections import defaultdict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from itertools import count
from functools import cached_property

from transactron.core.tmodule import CtrlPath, TModule
from transactron.core.transaction_base import TransactionBase

from amaranth import *
from amaranth_types import ShapeLike, ValueLike, SrcLoc
from typing import TYPE_CHECKING, Any, ClassVar, NewType, NotRequired, Optional, TypedDict, Unpack, final
from collections.abc import Callable
from transactron.utils.amaranth_ext.elaboratables import OneHotMux
from transactron.utils.assign import AssignArg
from transactron.utils.transactron_helpers import method_def_helper
from transactron.utils.typing import MethodData

if TYPE_CHECKING:
    from .method import Method


__all__ = ["AdapterBodyParams", "Body", "BodyParams", "MBody", "TBody"]


class AdapterBodyParams(TypedDict):
    combiner: NotRequired[Callable[[Module, Sequence[Any], Value], AssignArg]]
    nonexclusive: NotRequired[bool]
    single_caller: NotRequired[bool]


class BodyParams(AdapterBodyParams):
    validate_arguments: NotRequired[Callable[..., ValueLike]]


@final
class Body(TransactionBase["Body"]):
    def_counter: ClassVar[count] = count()
    def_order: int
    stack: ClassVar[list["Body"]] = []
    ctrl_path: CtrlPath = CtrlPath(-1, ())
    method_calls: defaultdict["Method[Any, Any]", list[tuple[CtrlPath, MethodData, Value]]]

    def __init__(
        self,
        *,
        name: str,
        owner: Elaboratable | None,
        i: ShapeLike,
        o: ShapeLike,
        src_loc: SrcLoc,
        **kwargs: Unpack[BodyParams],
    ):
        super().__init__(src_loc=src_loc)

        self.def_order = next(Body.def_counter)
        self.name = name
        self.owner = owner
        self.ready = Signal(name=self.owned_name + "_ready")
        self.runnable = Signal(name=self.owned_name + "_runnable")
        self.run = Signal(name=self.owned_name + "_run")
        self.data_in: MethodData = Signal(i, name=self.owned_name + "_data_in")
        self.data_out: MethodData = Signal(o, name=self.owned_name + "_data_out")
        self.combiner: Callable[[Module, Sequence[Any], Value], AssignArg] = (
            kwargs["combiner"] if "combiner" in kwargs else Body._default_combiner(i)
        )
        self.nonexclusive = kwargs["nonexclusive"] if "nonexclusive" in kwargs else False
        self.single_caller = kwargs["single_caller"] if "single_caller" in kwargs else False
        self.validate_arguments: Callable[..., ValueLike] | None = (
            kwargs["validate_arguments"] if "validate_arguments" in kwargs else None
        )
        self.method_calls = defaultdict(list)

        if self.nonexclusive:
            assert len(Value.cast(self.data_in)) == 0 or "combiner" in kwargs

    @cached_property
    def conditional_calls(self) -> set["Method[Any, Any]"]:
        return {
            method
            for method, calls in self.method_calls.items()
            if any(len(ctrl_path.path) > len(self.ctrl_path.path) + 1 for ctrl_path, _, _ in calls)
        }

    def _validate_arguments(self, en: Value, arg_rec: MethodData) -> Value:
        if self.validate_arguments is not None:
            return ~en | Value.cast(method_def_helper(self, self.validate_arguments, arg_rec)).bool()
        return C(1)

    @contextmanager
    def context(self, m: TModule) -> Iterator["Body"]:
        self.ctrl_path = m.ctrl_path

        parent = Body.peek()
        if parent is not None:
            parent.schedule_before(self, ready_dependent=True)

        Body.stack.append(self)

        try:
            yield self
        finally:
            Body.stack.pop()
            self.defined = True

    @staticmethod
    def get() -> "Body":
        ret = Body.peek()
        if ret is None:
            raise RuntimeError("No current body")
        return ret

    @staticmethod
    def peek() -> Optional["Body"]:
        if not Body.stack:
            return None
        return Body.stack[-1]

    @staticmethod
    def _default_combiner(shape: ShapeLike):
        def impl(m: Module, args: Sequence[Any], runs: Value) -> AssignArg:
            return OneHotMux.create(m, [(runs[i], args[i]) for i in range(len(args))])

        return impl


TBody = NewType("TBody", Body)
MBody = NewType("MBody", Body)
