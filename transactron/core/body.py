from collections import defaultdict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from itertools import count

from amaranth.lib.data import StructLayout
from transactron.core.tmodule import CtrlPath, TModule
from transactron.core.transaction_base import TransactionBase

from transactron.utils import *
from amaranth import *
from typing import TYPE_CHECKING, ClassVar, NewType, NotRequired, Optional, Callable, TypedDict, Unpack, final
from transactron.utils.assign import AssignArg

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
    ctrl_path: CtrlPath = CtrlPath(-1, [])
    method_uses: dict["Method", tuple[MethodStruct, Signal]]
    method_calls: defaultdict["Method", list[tuple[CtrlPath, MethodStruct, ValueLike]]]

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

        def default_combiner(m: Module, args: Sequence[MethodStruct], runs: Value) -> AssignArg:
            ret = Signal(from_method_layout(i))
            for k in OneHotSwitchDynamic(m, runs):
                m.d.comb += ret.eq(args[k])
            return ret

        self.def_order = next(Body.def_counter)
        self.name = name
        self.owner = owner
        self.ready = Signal(name=self.owned_name + "_ready")
        self.runnable = Signal(name=self.owned_name + "_runnable")
        self.run = Signal(name=self.owned_name + "_run")
        self.data_in: MethodStruct = Signal(from_method_layout(i), name=self.owned_name + "_data_in")
        self.data_out: MethodStruct = Signal(from_method_layout(o), name=self.owned_name + "_data_out")
        self.combiner: Callable[[Module, Sequence[MethodStruct], Value], AssignArg] = (
            kwargs["combiner"] if "combiner" in kwargs else default_combiner
        )
        self.nonexclusive = kwargs["nonexclusive"] if "nonexclusive" in kwargs else False
        self.single_caller = kwargs["single_caller"] if "single_caller" in kwargs else False
        self.validate_arguments: Optional[Callable[..., ValueLike]] = (
            kwargs["validate_arguments"] if "validate_arguments" in kwargs else None
        )
        self.method_uses = {}
        self.method_calls = defaultdict(list)

        if self.nonexclusive:
            assert len(self.data_in.as_value()) == 0 or self.combiner is not None

    def _validate_arguments(self, arg_rec: MethodStruct) -> ValueLike:
        if self.validate_arguments is not None:
            return self.ready & method_def_helper(self, self.validate_arguments, arg_rec)
        return self.ready

    @contextmanager
    def context(self, m: TModule) -> Iterator["Body"]:
        self.ctrl_path = m.ctrl_path

        parent = Body.peek()
        if parent is not None:
            parent.schedule_before(self)

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

    def _set_method_uses(self, m: ModuleLike):
        for method, calls in self.method_calls.items():
            arg_rec, enable_sig = self.method_uses[method]
            if len(calls) == 1:
                m.d.comb += arg_rec.eq(calls[0][1])
                m.d.comb += enable_sig.eq(calls[0][2])
            else:
                call_ens = Cat([en for _, _, en in calls])

                for i in OneHotSwitchDynamic(m, call_ens):
                    m.d.comb += arg_rec.eq(calls[i][1])
                    m.d.comb += enable_sig.eq(1)


TBody = NewType("TBody", Body)
MBody = NewType("MBody", Body)
