from collections.abc import Callable, Iterable

from .. import Method
from .transformers import Unifier
from ..utils.dependencies import *


__all__ = ["DependencyManager", "DependencyKey", "SimpleKey", "ListKey", "UnifierKey"]


class UnifierKey(DependencyKey["Method", tuple["Method", Iterable["Unifier"]]]):
    """Base class for method unifier dependency keys.

    Method unifier dependency keys are used to collect methods to be called by
    some part of the core. As multiple modules may wish to be called, a method
    unifier is used to present a single method interface to the caller, which
    allows to customize the calling behavior.
    `Unifier` module needs to be added as submodule when calling `combine`.
    """

    unifier: Callable[[list["Method"]], "Unifier"]

    cache = False

    def __init_subclass__(cls, unifier: Callable[[list["Method"]], "Unifier"], **kwargs) -> None:
        cls.unifier = staticmethod(unifier)
        super().__init_subclass__(**kwargs)

    def combine(self, data: list["Method"]) -> tuple["Method", Iterable["Unifier"]]:
        if len(data) == 1:
            return data[0], tuple()
        else:
            unifier = self.unifier(data)
            return unifier.method, (unifier,)
