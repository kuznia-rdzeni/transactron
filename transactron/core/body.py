from collections import defaultdict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from itertools import count
from functools import cached_property

from amaranth.lib.data import StructLayout
from transactron.core.tmodule import CtrlPath, TModule
from transactron.core.transaction_base import TransactionBase

from amaranth import *
from amaranth_types import ShapeLike, ValueLike, SrcLoc
from typing import TYPE_CHECKING, ClassVar, NewType, NotRequired, Optional, Callable, TypedDict, Unpack, final
from transactron.utils.amaranth_ext.functions import one_hot_mux
from transactron.utils.assign import AssignArg
from transactron.utils.transactron_helpers import from_method_layout, method_def_helper
from transactron.utils.typing import MethodStruct

if TYPE_CHECKING:
    from .method import Method


__all__ = ["AdapterBodyParams", "BodyParams", "Body", "TBody", "MBody"]


class AdapterBodyParams(TypedDict):
    combiner: NotRequired[Callable[[Module, Sequence[MethodStruct], Value], AssignArg]]
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
    method_calls: defaultdict["Method", list[tuple[CtrlPath, MethodStruct, Value]]]

    def __init__(
        self,
        *,
        name: str,
        owner: Optional[Elaboratable],
        i: StructLayout,
        o: StructLayout,
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
        self.data_in: MethodStruct = Signal(from_method_layout(i), name=self.owned_name + "_data_in")
        self.data_out: MethodStruct = Signal(from_method_layout(o), name=self.owned_name + "_data_out")
        self.combiner: Callable[[Module, Sequence[MethodStruct], Value], AssignArg] = (
            kwargs["combiner"] if "combiner" in kwargs else Body._default_combiner(from_method_layout(i))
        )
        self.nonexclusive = kwargs["nonexclusive"] if "nonexclusive" in kwargs else False
        self.single_caller = kwargs["single_caller"] if "single_caller" in kwargs else False
        self.validate_arguments: Optional[Callable[..., ValueLike]] = (
            kwargs["validate_arguments"] if "validate_arguments" in kwargs else None
        )
        self.method_calls = defaultdict(list)

        if self.nonexclusive:
            assert len(self.data_in.as_value()) == 0 or "combiner" in kwargs

    @cached_property
    def conditional_calls(self) -> set["Method"]:
        return {
            method
            for method, calls in self.method_calls.items()
            if any(len(ctrl_path.path) > len(self.ctrl_path.path) + 1 for ctrl_path, _, _ in calls)
        }

    def _validate_arguments(self, en: Value, arg_rec: MethodStruct) -> ValueLike:
        if self.validate_arguments is not None:
            return self.ready & (~en | method_def_helper(self, self.validate_arguments, arg_rec))
        return self.ready

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
        def impl(m: Module, args: Sequence[MethodStruct], runs: Value) -> AssignArg:
            arg = Signal(shape)
            m.d.comb += arg.eq(one_hot_mux(runs, [Value.cast(arg) for arg in args], assert_one_hot=False))
            return arg

        return impl


TBody = NewType("TBody", Body)
MBody = NewType("MBody", Body)
