import re
import operator
import logging
from functools import reduce
from dataclasses import dataclass
from amaranth.hdl import ValueCastable
from dataclasses_json import dataclass_json
from typing import TypeAlias

from amaranth import *
from amaranth_types import ModuleLike, ValueLike

from transactron.utils import SrcLoc, get_src_loc, local_src_loc
from transactron.utils.dependencies import DependencyContext, ListKey


__all__ = [
    "LogLevel",
    "LogRecordInfo",
    "LogRecord",
    "LogKey",
    "HardwareLogger",
    "top_assertion",
    "assertion",
    "get_log_records",
    "get_trigger_bit",
]


LogLevel: TypeAlias = int


@dataclass_json
@dataclass
class LogRecordInfo:
    """
    Simulator-backend-agnostic information about a log record that can
    be serialized and used outside the Amaranth context.
    """

    logger_name: str
    """Name of the logger which produced the record."""

    level: LogLevel
    """The severity level of the log."""

    format_str: str
    """The template of the message. Should follow PEP 3101 standard."""

    location: SrcLoc
    """Source location of the log."""

    def format(self, *args) -> str:
        """Format the log message with a set of concrete arguments."""

        return self.format_str.format(*args)


@dataclass
class LogRecord(LogRecordInfo):
    """A LogRecord instance represents an event being logged."""

    trigger: Value
    """Amaranth signal triggering the log."""

    fields: tuple[Value | ValueCastable, ...] = tuple()
    """Amaranth signals that will be used to format the message."""


@dataclass(frozen=True)
class LogKey(ListKey[LogRecord]):
    pass


class HardwareLogger:
    """A class for creating log messages in the hardware.

    Intuitively, the hardware logger works similarly to a normal software
    logger. You can log a message anywhere in the circuit, but due to the
    parallel nature of the hardware you must specify a special trigger signal
    which will indicate if a message shall be reported in that cycle.

    Hardware logs are evaluated and printed during simulation, so both
    the trigger and the format fields are Amaranth values, i.e.
    signals or arbitrary Amaranth expressions.

    Instances of the HardwareLogger class represent a logger for a single
    submodule of the circuit. Exactly how a "submodule" is defined is up
    to the developer. Submodule are identified by a unique string and
    the names can be nested. Names are organized into a namespace hierarchy
    where levels are separated by periods, much like the Python package
    namespace. So in the instance, submodules names might be "frontend"
    for the upper level, and "frontend.icache" and "frontend.bpu" for
    the sub-levels. There is no arbitrary limit to the depth of nesting.

    Attributes
    ----------
    name: str
        Name of this logger.
    """

    def __init__(self, name: str):
        """
        Parameters
        ----------
        name: str
            Name of this logger. Hierarchy levels are separated by periods,
            e.g. "backend.fu.jumpbranch".
        """
        self.name = name

    def top_log(
        self,
        level: LogLevel,
        trigger: ValueLike,
        format: str,
        *args: ValueLike,
        src_loc: int | SrcLoc = 0,
    ):
        """Registers a hardware log record with the given severity.

        The `top_*` logging functions ignore `m.If` etc. for triggering.
        They can be used in contexts where a module is not available.

        See `HardwareLogger.log` function for more details.
        """
        src_loc = local_src_loc(get_src_loc(src_loc))
        trigger = Value.cast(trigger)

        def convert(arg: ValueLike):
            if isinstance(arg, (Value, ValueCastable)):
                return arg
            return Value.cast(arg)

        args = tuple(convert(arg) for arg in args)

        record = LogRecord(
            logger_name=self.name, level=level, format_str=format, location=src_loc, trigger=trigger, fields=args
        )

        dependencies = DependencyContext.get()
        dependencies.add_dependency(LogKey(), record)

    def top_debug(self, trigger: ValueLike, format: str, *args: ValueLike, src_loc: int | SrcLoc = 0):
        """Log a message with severity 'DEBUG'.

        See `HardwareLogger.top_log` function for more details.
        """
        self.top_log(logging.DEBUG, trigger, format, *args, src_loc=src_loc)

    def top_info(self, trigger: ValueLike, format: str, *args: ValueLike, src_loc: int | SrcLoc = 0):
        """Log a message with severity 'INFO'.

        See `HardwareLogger.top_log` function for more details.
        """
        self.top_log(logging.INFO, trigger, format, *args, src_loc=src_loc)

    def top_warning(self, trigger: ValueLike, format: str, *args: ValueLike, src_loc: int | SrcLoc = 0):
        """Log a message with severity 'WARNING'.

        See `HardwareLogger.top_log` function for more details.
        """
        self.top_log(logging.WARNING, trigger, format, *args, src_loc=src_loc)

    def top_error(self, trigger: ValueLike, format: str, *args: ValueLike, src_loc: int | SrcLoc = 0):
        """Log a message with severity 'ERROR'.

        See `HardwareLogger.top_log` function for more details.
        """
        self.top_log(logging.ERROR, trigger, format, *args, src_loc=src_loc)

    def top_assertion(self, value: ValueLike, format: str, *args: ValueLike, src_loc: int | SrcLoc = 0):
        """Define an assertion.

        Unlike `HardwareLogger.assertion`, this function can be used in
        contexts where a module is not available.

        See `HardwareLogger.assertion` function for more details.
        """
        self.top_error(~Value.cast(value), format, *args, src_loc=get_src_loc(src_loc))

    def log(
        self,
        m: ModuleLike,
        level: LogLevel,
        trigger: ValueLike,
        format: str,
        *args: ValueLike,
        src_loc: int | SrcLoc = 0,
    ):
        """Registers a hardware log record with the given severity.

        Parameters
        ----------
        m: ModuleLike
            The module for which the log record is added.
        trigger: ValueLike
            If the value of this Amaranth expression is true, the log will reported.
        format: str
            The format of the message as defined in PEP 3101.
        *args: ValueLike
            Amaranth values that will be read during simulation and used to format
            the message.
        src_loc: int, optional
            How many stack frames below to look for the source location, used to
            identify the failing assertion.
        """
        trigger_signal = Signal()
        m.d.comb += trigger_signal.eq(trigger)
        self.top_log(level, trigger_signal, format, *args, src_loc=get_src_loc(src_loc))

    def debug(self, m: ModuleLike, trigger: ValueLike, format: str, *args: ValueLike, src_loc: int | SrcLoc = 0):
        """Log a message with severity 'DEBUG'.

        See `HardwareLogger.log` function for more details.
        """
        self.log(m, logging.DEBUG, trigger, format, *args, src_loc=get_src_loc(src_loc))

    def info(self, m: ModuleLike, trigger: ValueLike, format: str, *args: ValueLike, src_loc: int | SrcLoc = 0):
        """Log a message with severity 'INFO'.

        See `HardwareLogger.log` function for more details.
        """
        self.log(m, logging.INFO, trigger, format, *args, src_loc=get_src_loc(src_loc))

    def warning(self, m: ModuleLike, trigger: ValueLike, format: str, *args: ValueLike, src_loc: int | SrcLoc = 0):
        """Log a message with severity 'WARNING'.

        See `HardwareLogger.log` function for more details.
        """
        self.log(m, logging.WARNING, trigger, format, *args, src_loc=get_src_loc(src_loc))

    def error(self, m: ModuleLike, trigger: ValueLike, format: str, *args: ValueLike, src_loc: int | SrcLoc = 0):
        """Log a message with severity 'ERROR'.

        This severity level has special semantics. If a log with this serverity
        level is triggered, the simulation will be terminated.

        See `HardwareLogger.log` function for more details.
        """
        self.log(m, logging.ERROR, trigger, format, *args, src_loc=get_src_loc(src_loc))

    def assertion(self, m: ModuleLike, value: ValueLike, format: str = "", *args: ValueLike, src_loc: int | SrcLoc = 0):
        """Define an assertion.

        This function might help find some hardware bugs which might otherwise be
        hard to detect. If `value` is false, it will terminate the simulation or
        it can also be used to turn on a warning LED on a board.

        Internally, this is a convenience wrapper over log.error.

        See `HardwareLogger.log` function for more details.
        """
        self.error(m, ~Value.cast(value), format, *args, src_loc=get_src_loc(src_loc))


def top_assertion(value: ValueLike, format: str, *args: ValueLike, name: str = "global", src_loc: int | SrcLoc = 0):
    """Define an assertion.

    This is a short form, for use in generic code. For general use,
    see `HardwareLogger.top_assertion`.
    """
    HardwareLogger(name).top_assertion(value, format, *args, src_loc=src_loc)


def assertion(
    m: ModuleLike, value: ValueLike, format: str, *args: ValueLike, name: str = "global", src_loc: int | SrcLoc = 0
):
    """Define an assertion.

    This is a short form, for use in generic code. For general use,
    see `HardwareLogger.assertion`.
    """
    HardwareLogger(name).assertion(m, value, format, *args, src_loc=src_loc)


def get_log_records(level: LogLevel, namespace_regexp: str = ".*") -> list[LogRecord]:
    """Get log records in for the given severity level and in the
    specified namespace.

    This function returns all log records with the severity bigger or equal
    to the specified level and belonging to the specified namespace.

    Parameters
    ----------
    level: LogLevel
        The minimum severity level.
    namespace: str, optional
        The regexp of the namespace. If not specified, logs from all namespaces
        will be processed.
    """

    dependencies = DependencyContext.get()
    all_logs = dependencies.get_dependency(LogKey())
    return [rec for rec in all_logs if rec.level >= level and re.search(namespace_regexp, rec.logger_name)]


def get_trigger_bit(level: LogLevel, namespace_regexp: str = ".*") -> Value:
    """Get a trigger bit for logs of the given severity level and
    in the specified namespace.

    The signal returned by this function is high whenever the trigger signal
    of any of the records with the severity bigger or equal to the specified
    level is high.

    Parameters
    ----------
    level: LogLevel
        The minimum severity level.
    namespace: str, optional
        The regexp of the namespace. If not specified, logs from all namespaces
        will be processed.
    """

    return reduce(operator.or_, [rec.trigger for rec in get_log_records(level, namespace_regexp)], C(0))
