import enum
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from typing import Annotated, Any, ClassVar, Self, TypeVar, dataclass_transform, get_args, get_origin, get_type_hints


__all__ = ["Static", "Event", "event", "get_event_class"]


class _StaticMarker:
    """Marker used in `Annotated` metadata to denote static event fields."""


type Static[T] = Annotated[T, _StaticMarker]
"""Annotation for static event fields.

Static fields hold plain Python values which are fixed at emission
(elaboration) time. They are stored in the event log schema and don't
cost any hardware. Use them for properties of the emission site, e.g.
a lane index or a unit name::

    @event("exec.unit_start")
    class ExecUnitStart(Event):
        insn_tag: int
        lane: Static[int]
"""


def _split_hint(hint: Any) -> tuple[bool, Any]:
    """Splits a field type annotation into (is static, underlying type)."""
    if get_origin(hint) is Static:
        return True, get_args(hint)[0]
    if get_origin(hint) is Annotated and _StaticMarker in get_args(hint)[1:]:
        return True, get_args(hint)[0]
    return False, hint


def _convert_field(field_type: Any, raw):
    if isinstance(field_type, type):
        if issubclass(field_type, enum.Enum):
            return field_type(raw)
        if field_type is bool:
            return bool(raw)
    return raw


def static_to_raw(value: Any) -> Any:
    """Converts a static field value to its JSON-serializable representation."""
    if isinstance(value, enum.Enum):
        return value.value
    return value


class Event:
    """Base class for event types.

    Concrete event types subclass `Event` and are declared using the `event`
    decorator, which turns them into dataclasses. Fields annotated with
    `Static[...]` are static; all other fields are dynamic and are backed by
    hardware signals sampled during simulation.

    The same class is used in two contexts:

    - during elaboration, instances are constructed with Amaranth values for
      the dynamic fields and passed to `EventSource.emit`;
    - when reading a captured event log, decoded instances carry concrete
      Python values.

    Dynamic fields should be annotated with `int`, `bool` or an `enum.Enum`
    subclass; raw integers are converted according to the annotation when
    an event log is decoded.
    """

    event_name: ClassVar[str]
    """Unique dotted name of the event type, e.g. "fetch.block_started"."""

    _dynamic_fields: ClassVar[tuple[str, ...]]
    _static_fields: ClassVar[tuple[str, ...]]
    _field_types: ClassVar[Mapping[str, Any]]

    @classmethod
    def hw(cls, **fields: Any) -> Self:
        """Constructs an event instance for emission from hardware.

        Behaves exactly like the regular constructor, but accepts arbitrary
        values, as during elaboration the dynamic fields hold Amaranth values
        instead of the declared (decoded) field types. Field values are
        validated by `EventSource.emit`.
        """
        return cls(**fields)

    @classmethod
    def from_raw(cls, dynamics: Mapping[str, int], statics: Mapping[str, Any]) -> Self:
        """Reconstructs a typed event instance from raw field values.

        Parameters
        ----------
        dynamics: Mapping[str, int]
            Raw values of the dynamic fields, as sampled from hardware signals.
        statics: Mapping[str, Any]
            Raw values of the static fields, as stored in the event schema.
        """
        kwargs: dict[str, Any] = {}
        for name in cls._dynamic_fields:
            kwargs[name] = _convert_field(cls._field_types[name], dynamics[name])
        for name in cls._static_fields:
            kwargs[name] = _convert_field(cls._field_types[name], statics[name])
        return cls(**kwargs)


_event_registry: dict[str, type[Event]] = {}


def get_event_class(name: str) -> type[Event]:
    """Looks up an event type by its registered name.

    The module defining the event type must have been imported beforehand,
    as event types register themselves at class definition time.
    """
    if name not in _event_registry:
        raise KeyError(
            f"Unknown event type '{name}'. The module which defines this event must be imported before decoding."
        )
    return _event_registry[name]


_T = TypeVar("_T", bound=type[Event])


@dataclass_transform(eq_default=True)
def event(name: str) -> Callable[[_T], _T]:
    """Class decorator declaring a new event type.

    The decorated class must subclass `Event`. It is converted to a dataclass
    and registered globally under the given name, which must be unique.

    Parameters
    ----------
    name: str
        Unique dotted name of the event type, e.g. "fetch.block_started".
        Recorded in event log schemas and used to look up the event type
        when a log is decoded.
    """

    def decorator(cls: _T) -> _T:
        if not (isinstance(cls, type) and issubclass(cls, Event)):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TypeError(f"Class '{cls.__name__}' decorated with @event must subclass Event")
        if name in _event_registry:
            raise ValueError(f"Event name '{name}' is already registered by {_event_registry[name].__qualname__}")

        dcls = dataclass(cls)
        hints = get_type_hints(dcls, include_extras=True)

        dynamic: list[str] = []
        static: list[str] = []
        field_types: dict[str, Any] = {}
        for f in fields(dcls):  # type: ignore[arg-type]
            is_static, field_type = _split_hint(hints[f.name])
            field_types[f.name] = field_type
            if is_static:
                static.append(f.name)
            else:
                dynamic.append(f.name)

        dcls.event_name = name
        dcls._dynamic_fields = tuple(dynamic)
        dcls._static_fields = tuple(static)
        dcls._field_types = field_types
        _event_registry[name] = dcls
        return dcls

    return decorator
