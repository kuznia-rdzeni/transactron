from amaranth import ShapeCastable, ShapeLike, Signal, unsigned
from amaranth.lib.wiring import Signature, Flow, Member
from amaranth_types import AbstractInterface, AbstractSignature

from abc import ABCMeta
from typing import TYPE_CHECKING, Generic, Mapping, Self, TypeVar, final, overload
from dataclasses import dataclass

__all__ = [
    "CIn",
    "COut",
    "AbstractComponentInterface",
    "ComponentInterface",
    "FlippedComponentInterface",
]

T = TypeVar("T")


class _ShapeTypingMeta(ABCMeta):
    """
    Internal metaclass for adding type information to `_ComponentSignal`.

    HACK: When type-checking it specifies return type, as targeted `Signal(shape)` type, which is not correct.
    Implementation forwards all calls unaltered, so the object is really an `_ComponentSignal` storing required
    information.
    At the stage of `Component` creation all fields are instatiated in-place and have the correct target type
    (given initially to typchecker) by `Component.__init__`.
    """

    if TYPE_CHECKING:
        # Amaranth ShapeCastable Signal creation rules

        @overload
        def __call__(cls, shape: ShapeCastable[T]) -> T:
            raise NotImplementedError

        @overload
        def __call__(cls, shape: ShapeLike = unsigned(1)) -> Signal:
            raise NotImplementedError

        def __call__(cls, shape: ShapeLike = unsigned(1)):
            raise NotImplementedError

    else:
        # bypass metaclass - create _ComponentSignal
        def __call__(cls, shape: ShapeLike = unsigned(1)):
            return super().__call__(shape)


@dataclass
class _ComponentSignal:
    """Component Signal
    Element of `ComponentInterface`. Do not use directly - use `CIn` and `COut` wrappers that add correct typing
    information.

    Created signals should never be referenced directly in HDL code and exist only on type checking
    and `ComponentInterface` definition level.

    Real `Signal` is created in place of `ComponentSignal` when initialising `Component`.

    Parameters
    ----------

    flow: Flow
        Direction of signal flow. `Flow.In` or `Flow.Out`.

    shape: ShapeLike
        Shape of Signal. `unsigned(1)` by default.
    """

    flow: Flow
    shape: ShapeLike

    def as_member(self):
        return self.flow(self.shape)


@final
class CIn(_ComponentSignal, metaclass=_ShapeTypingMeta):
    """`_ComponentSignal` with `Flow.In` direction."""

    def __init__(self, shape: ShapeLike):
        super().__init__(Flow.In, shape)


@final
class COut(_ComponentSignal, metaclass=_ShapeTypingMeta):
    """`_ComponentSignal` with `Flow.Out` direction."""

    def __init__(self, shape: ShapeLike):
        super().__init__(Flow.Out, shape)


class AbstractComponentInterface(AbstractInterface[AbstractSignature]):
    def flipped(self) -> "AbstractComponentInterface": ...

    # Remove after pyright update
    @property
    def signature(self) -> AbstractSignature: ...


class ComponentInterface(AbstractComponentInterface):
    """Component Interface
    Syntactic sugar for using typed lib.wiring `Signature`s in `Component`.

    It allows to avoid defining desired Amaranth `Signature` and separetly `AbstractInterface` of `Signals` to get
    `Component` attribute-level typing of interface.

    Interface should be constructed in `__init__` of class that inherits `ComponentInterface`, by defining
    instance attributes.
    Only allowed attributes in `ComponentInterface` are of `ComponentSignal` (see `CIn` and `COut`)
    and `ComponentInterface` (nested interface) types.

    Resulting class can be used directly as typing hint for class-level interface attribute, that will be later
    constructed  by `Component`. Use `signature` property to get amaranth `Signature` in `Component` constructor.

    Examples
    --------
    .. highlight:: python
    .. code-block:: python

        class ExampleInterface(ComponentInterface):
            def __init__(self, data_width: int):
                self.data_in = CIn(data_width)
                self.data_out = COut(data_width)
                self.valid = CIn()
                self.x = SubInterface().flipped()

        class Examp,le(Component):
            bus: ExampleInterface
            def __init__(self):
                super().__init__({bus: In(ExampleInterface(2).signature)})
    """

    @property
    def signature(self) -> AbstractSignature:
        """Amaranth lib.wiring `Signature` constructed from defined `ComponentInterface` attributes."""
        return Signature(self._to_members_list())

    def flipped(self) -> "FlippedComponentInterface[Self]":
        """`ComponentInterface` with flipped `Flow` direction of members."""
        return FlippedComponentInterface(self)

    def _to_members_list(self, *, name_prefix: str = "") -> Mapping[str, Member]:
        res = {}
        for m_name in dir(self):
            if m_name.startswith("_") or m_name == "signature" or m_name == "flipped":
                continue

            m_val = getattr(self, m_name)
            if isinstance(m_val, _ComponentSignal):
                res[m_name] = m_val.as_member()
            elif isinstance(m_val, ComponentInterface) or isinstance(m_val, FlippedComponentInterface):
                res[m_name] = Flow.Out(m_val.signature)
            else:
                raise AttributeError(
                    f"Illegal attribute `{name_prefix+m_name}`: `{m_val}`.  "
                    "Expected `CIn`, `COut`, `ComponentInterface` or `FlippedComponentInterface`"
                )
        return res


_T_ComponentInterface = TypeVar("_T_ComponentInterface", bound=ComponentInterface)


@final
class FlippedComponentInterface(AbstractComponentInterface, Generic[_T_ComponentInterface]):
    """
    Represents `ComponentInterface` with flipped `Flow` directions of its members.
    Flip is applied only in resulting `signature` property.
    """

    def __init__(self, base: _T_ComponentInterface):
        self._base = base

    def __getattr__(self, name: str):
        return getattr(self._base, name)

    @property
    def signature(self) -> AbstractSignature:
        """Amaranth lib.wiring `Signature` constructed from defined `ComponentInterface` attributes."""
        return self._base.signature.flip()

    def flipped(self) -> _T_ComponentInterface:
        """`ComponentInterface` with flipped `Flow` direction of members."""
        return self._base
