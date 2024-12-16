from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from amaranth.lib.data import StructLayout
from transactron.core.tmodule import TModule
from transactron.graph import Owned

from transactron.utils import *
from amaranth import *
from typing import ClassVar, Optional, Callable, Protocol, final
from .transaction_base import *
from transactron.utils.assign import AssignArg


__all__ = ["Body"]


@final
class Body(TransactionBase):
    stack: ClassVar[list["Body"]] = []

    def __init__(
        self,
        *,
        name: str,
        owner: Optional[Elaboratable],
        i: StructLayout,
        o: StructLayout,
        combiner: Optional[Callable[[Module, Sequence[MethodStruct], Value], AssignArg]],
        validate_arguments: Optional[Callable[..., ValueLike]],
        nonexclusive: bool,
        single_caller: bool,
        src_loc: SrcLoc
    ):
        super().__init__(src_loc=src_loc)
        
        def default_combiner(m: Module, args: Sequence[MethodStruct], runs: Value) -> AssignArg:
            ret = Signal(from_method_layout(i))
            for k in OneHotSwitchDynamic(m, runs):
                m.d.comb += ret.eq(args[k])
            return ret

        self.def_order = next(TransactionBase.def_counter)
        self.name = name
        self.owner = owner
        self.ready = Signal(name=self.owned_name + "_ready")
        self.run = Signal(name=self.owned_name + "_run")
        self.data_in: MethodStruct = Signal(from_method_layout(i), name=self.owned_name + "_data_in")
        self.data_out: MethodStruct = Signal(from_method_layout(o), name=self.owned_name + "_data_out")
        self.combiner: Callable[[Module, Sequence[MethodStruct], Value], AssignArg] = combiner or default_combiner
        self.nonexclusive = nonexclusive
        self.single_caller = single_caller
        self.validate_arguments: Optional[Callable[..., ValueLike]] = validate_arguments
        
        if nonexclusive:
            assert len(self.data_in.as_value()) == 0 or combiner is not None
    
    def _validate_arguments(self, arg_rec: MethodStruct) -> ValueLike:
        if self.validate_arguments is not None:
            return self.ready & method_def_helper(self, self.validate_arguments, arg_rec)
        return self.ready
