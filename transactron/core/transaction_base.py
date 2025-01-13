from enum import Enum, auto
from dataclasses import KW_ONLY, dataclass
from typing import (
    Generic,
    Protocol,
    TypeVar,
    runtime_checkable,
)
from amaranth import *

from transactron.graph import Owned
from transactron.utils import *


__all__ = ["TransactionBase", "Priority"]


_T = TypeVar("_T", bound="TransactionBase")


class Priority(Enum):
    #: Conflicting transactions/methods don't have a priority order.
    UNDEFINED = auto()
    #: Left transaction/method is prioritized over the right one.
    LEFT = auto()
    #: Right transaction/method is prioritized over the left one.
    RIGHT = auto()


@dataclass
class RelationBase(Generic[_T]):
    _: KW_ONLY
    end: _T
    priority: Priority = Priority.UNDEFINED
    conflict: bool = False
    silence_warning: bool = False


@dataclass
class Relation(RelationBase[_T], Generic[_T]):
    _: KW_ONLY
    start: _T


@runtime_checkable
class TransactionBase(Owned, Protocol, Generic[_T]):
    src_loc: SrcLoc
    relations: list[RelationBase[_T]]
    simultaneous_list: list[_T]
    independent_list: list[_T]

    def __init__(self, *, src_loc: SrcLoc):
        self.src_loc = src_loc
        self.relations = []
        self.simultaneous_list = []
        self.independent_list = []

    def add_conflict(self, end: _T, priority: Priority = Priority.UNDEFINED) -> None:
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

    def schedule_before(self, end: _T) -> None:
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

    def simultaneous(self, *others: _T) -> None:
        """Adds simultaneity relations.

        The given `Transaction`\\s or `Method``\\s will execute simultaneously
        (in the same clock cycle) with this `Transaction` or `Method`.

        Parameters
        ----------
        *others: Transaction or Method
            The `Transaction`\\s or `Method`\\s to be executed simultaneously.
        """
        self.simultaneous_list += others

    def simultaneous_alternatives(self, *others: _T) -> None:
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

    def _independent(self, *others: _T) -> None:
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
