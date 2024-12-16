from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from enum import Enum, auto
from itertools import count
from typing import (
    ClassVar,
    TypeAlias,
    TypedDict,
    Union,
    TypeVar,
    Protocol,
    Self,
    runtime_checkable,
    TYPE_CHECKING,
    Optional,
)
from amaranth import *

from .tmodule import TModule, CtrlPath
from transactron.graph import Owned
from transactron.utils import *

if TYPE_CHECKING:
    from .method import Body, Method
    from .transaction import Transaction

__all__ = ["TransactionBase", "Priority"]

TransactionOrMethodImpl: TypeAlias = Union["Transaction", "Body"]
TransactionOrMethodImplBound = TypeVar("TransactionOrMethodImplBound", "Transaction", "MethodImpl")


class Priority(Enum):
    #: Conflicting transactions/methods don't have a priority order.
    UNDEFINED = auto()
    #: Left transaction/method is prioritized over the right one.
    LEFT = auto()
    #: Right transaction/method is prioritized over the left one.
    RIGHT = auto()


class RelationBase(TypedDict):
    end: TransactionOrMethodImpl
    priority: Priority
    conflict: bool
    silence_warning: bool


class Relation(RelationBase):
    start: TransactionOrMethodImpl


class OwnedAndNamed(Owned, Protocol):
    name: str

    @property
    def owned_name(self):
        if self.owner is not None and self.owner.__class__.__name__ != self.name:
            return f"{self.owner.__class__.__name__}_{self.name}"
        else:
            return self.name


@runtime_checkable
class TransactionBase(OwnedAndNamed, Protocol):
    def_counter: ClassVar[count] = count()
    def_order: int
    defined: bool = False
    src_loc: SrcLoc
    relations: list[RelationBase]
    simultaneous_list: list[TransactionOrMethodImpl]
    independent_list: list[TransactionOrMethodImpl]

    def __init__(self, *, src_loc: int | SrcLoc):
        self.src_loc = get_src_loc(src_loc)
        self.relations = []
        self.simultaneous_list = []
        self.independent_list = []

    def add_conflict(self, end: TransactionOrMethodImpl, priority: Priority = Priority.UNDEFINED) -> None:
        """Registers a conflict.

        Record that that the given `Transaction` or `Method` cannot execute
        simultaneously with this `Method` or `Transaction`. Typical reason
        is using a common resource (register write or memory port).

        Parameters
        ----------
        end: Transaction or Method
            The conflicting `Transaction` or `Method`
        priority: Priority, optional
            Is one of conflicting `Transaction`\\s or `Method`\\s prioritized?
            Defaults to undefined priority relation.
        """
        self.relations.append(
            RelationBase(end=end, priority=priority, conflict=True, silence_warning=self.owner != end.owner)
        )

    def schedule_before(self, end: TransactionOrMethodImpl) -> None:
        """Adds a priority relation.

        Record that that the given `Transaction` or `Method` needs to be
        scheduled before this `Method` or `Transaction`, without adding
        a conflict. Typical reason is data forwarding.

        Parameters
        ----------
        end: Transaction or Method
            The other `Transaction` or `Method`
        """
        self.relations.append(
            RelationBase(end=end, priority=Priority.LEFT, conflict=False, silence_warning=self.owner != end.owner)
        )

    def simultaneous(self, *others: TransactionOrMethodImpl) -> None:
        """Adds simultaneity relations.

        The given `Transaction`\\s or `Method``\\s will execute simultaneously
        (in the same clock cycle) with this `Transaction` or `Method`.

        Parameters
        ----------
        *others: Transaction or Method
            The `Transaction`\\s or `Method`\\s to be executed simultaneously.
        """
        self.simultaneous_list += others

    def simultaneous_alternatives(self, *others: TransactionOrMethodImpl) -> None:
        """Adds exclusive simultaneity relations.

        Each of the given `Transaction`\\s or `Method``\\s will execute
        simultaneously (in the same clock cycle) with this `Transaction` or
        `Method`. However, each of the given `Transaction`\\s or `Method`\\s
        will be separately considered for execution.

        Parameters
        ----------
        *others: Transaction or Method
            The `Transaction`\\s or `Method`\\s to be executed simultaneously,
            but mutually exclusive, with this `Transaction` or `Method`.
        """
        self.simultaneous(*others)
        others[0]._independent(*others[1:])

    def _independent(self, *others: TransactionOrMethodImpl) -> None:
        """Adds independence relations.

        This `Transaction` or `Method`, together with all the given
        `Transaction`\\s or `Method`\\s, will never be considered (pairwise)
        for simultaneous execution.

        Warning: this function is an implementation detail, do not use in
        user code.

        Parameters
        ----------
        *others: Transaction or Method
            The `Transaction`\\s or `Method`\\s which, together with this
            `Transaction` or `Method`, need to be independently considered
            for execution.
        """
        self.independent_list += others
