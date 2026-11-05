from transactron.utils import *
from typing import TYPE_CHECKING, Any  # noqa: F401 used in a string type argument
from dataclasses import dataclass

if TYPE_CHECKING:
    from .manager import TransactionManager  # noqa: F401 because of https://github.com/PyCQA/pyflakes/issues/571
    from .method import Method  # noqa: F401 because of https://github.com/PyCQA/pyflakes/issues/571
    from .transaction import Transaction  # noqa: F401 because of https://github.com/PyCQA/pyflakes/issues/571

__all__ = ["DefinedMethodsKey", "ProvidedMethodsKey", "TransactionManagerKey", "TransactionsKey"]


@dataclass(frozen=True)
class TransactionManagerKey(SimpleKey["TransactionManager"]):
    pass


@dataclass(frozen=True)
class TransactionsKey(ListKey["Transaction"]):
    pass


@dataclass(frozen=True)
class DefinedMethodsKey(ListKey["Method[Any, Any]"]):
    pass


@dataclass(frozen=True)
class ProvidedMethodsKey(ListKey["Method[Any, Any]"]):
    pass
