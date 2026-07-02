from dataclasses import dataclass, field, asdict
from dataclasses_json import dataclass_json
from typing import Iterable, Optional, TypeAlias

from amaranth import *
from amaranth.back import verilog
from amaranth.hdl import Fragment, ValueCastable
from amaranth_types import AbstractInterface

from transactron.core import TransactionManager
from transactron.core.keys import TransactionManagerKey
from transactron.core.manager import MethodMap
from transactron.evlog import EmittedEvent, EventSiteLocation, GeneratedEvLog, get_emitted_events
from transactron.evlog.schema import schema_from_records
from transactron.lib.metrics import HardwareMetricsManager
from transactron.utils import logging
from transactron.utils.dependencies import DependencyContext
from transactron.utils.gen_hacks import fixup_vivado_transparent_memories
from transactron.utils.idgen import IdGenerator
from transactron.profiler import ProfileData

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from amaranth.hdl._ast import SignalDict


__all__ = [
    "MetricLocation",
    "GeneratedLog",
    "GenerationInfo",
    "generate_verilog",
]

SignalHandle: TypeAlias = list[str]
"""The location of a signal is a list of Verilog identifiers that denote a path
consisting of module names (and the signal name at the end) leading
to the signal wire."""


@dataclass_json
@dataclass
class MetricLocation:
    """Information about the location of a metric in the generated Verilog code.

    Attributes
    ----------
    regs : dict[str, SignalHandle]
        The location of each register of that metric.
    """

    regs: dict[str, SignalHandle] = field(default_factory=dict)


@dataclass_json
@dataclass
class TransactionSignalsLocation:
    """Information about transaction control signals in the generated Verilog code.

    Attributes
    ----------
    ready: list[str]
        The location of the ``ready`` signal.
    runnable: list[str]
        The location of the ``runnable`` signal.
    run: list[str]
        The location of the ``run`` signal.
    """

    ready: list[str]
    runnable: list[str]
    run: list[str]


@dataclass_json
@dataclass
class MethodSignalsLocation:
    """Information about method control signals in the generated Verilog code.

    Attributes
    ----------
    run: list[str]
        The location of the ``run`` signal.
    """

    run: list[str]


@dataclass_json
@dataclass
class GeneratedLog(logging.LogRecordInfo):
    """Information about a log record in the generated Verilog code.

    Attributes
    ----------
    trigger_location : SignalHandle
        The location of the trigger signal.
    fields_location : list[SignalHandle]
        Locations of the log fields.
    """

    trigger_location: SignalHandle
    fields_location: list[SignalHandle]


@dataclass_json
@dataclass
class GenerationInfo:
    """Various information about the generated circuit.

    Attributes
    ----------
    metrics_location : dict[str, MetricInfo]
        Mapping from a metric name to an object storing Verilog locations
        of its registers.
    logs : list[GeneratedLog]
        Locations and metadata for all log records.
    evlog : GeneratedEvLog
        Event log schema and signal locations for all event emission sites.
    """

    metrics_location: dict[str, MetricLocation]
    transaction_signals_location: dict[int, TransactionSignalsLocation]
    method_signals_location: dict[int, MethodSignalsLocation]
    profile_data: ProfileData
    logs: list[GeneratedLog]
    evlog: GeneratedEvLog

    def encode(self, file_name: str):
        """
        Encodes the generation information as JSON and saves it to a file.
        """
        with open(file_name, "w") as fp:
            fp.write(self.to_json())  # type: ignore

    @staticmethod
    def decode(file_name: str) -> "GenerationInfo":
        """
        Loads the generation information from a JSON file.
        """
        with open(file_name, "r") as fp:
            return GenerationInfo.from_json(fp.read())  # type: ignore


def escape_verilog_identifier(identifier: str) -> str:
    """
    Escapes a Verilog identifier according to the language standard.

    From IEEE Std 1364-2001 (IEEE Standard Verilog® Hardware Description Language)

    "2.7.1 Escaped identifiers

    Escaped identifiers shall start with the backslash character and end with white
    space (space, tab, newline). They provide a means of including any of the printable ASCII
    characters in an identifier (the decimal values 33 through 126, or 21 through 7E in hexadecimal)."
    """

    # The standard says how to escape a identifier, but not when. So this is
    # a non-exhaustive list of characters that Yosys escapes (it is used
    # by Amaranth when generating Verilog code).
    characters_to_escape = [".", "$", "-"]

    for char in characters_to_escape:
        if char in identifier:
            return f"\\{identifier} "

    return identifier


def get_signal_location(signal: Signal, name_map: "SignalDict") -> SignalHandle:
    raw_location = name_map[signal]
    return raw_location


def collect_metric_locations(name_map: "SignalDict") -> dict[str, MetricLocation]:
    metrics_location: dict[str, MetricLocation] = {}

    # Collect information about the location of metric registers in the generated code.
    metrics_manager = HardwareMetricsManager()
    for metric_name, metric in metrics_manager.get_metrics().items():
        metric_loc = MetricLocation()
        for reg_name in metric.regs:
            metric_loc.regs[reg_name] = get_signal_location(
                metrics_manager.get_register_value(metric_name, reg_name), name_map
            )

        metrics_location[metric_name] = metric_loc

    return metrics_location


def collect_transaction_method_signals(
    transaction_manager: TransactionManager, name_map: "SignalDict"
) -> tuple[dict[int, TransactionSignalsLocation], dict[int, MethodSignalsLocation]]:
    transaction_signals_location: dict[int, TransactionSignalsLocation] = {}
    method_signals_location: dict[int, MethodSignalsLocation] = {}

    method_map = MethodMap(transaction_manager.transactions, transaction_manager.methods)
    get_id = IdGenerator()

    for transaction in method_map.transactions:
        ready_loc = get_signal_location(transaction.ready, name_map)
        runnable_loc = get_signal_location(transaction.runnable, name_map)
        run_loc = get_signal_location(transaction.run, name_map)
        transaction_signals_location[get_id(transaction)] = TransactionSignalsLocation(ready_loc, runnable_loc, run_loc)

    for method in method_map.methods:
        run_loc = get_signal_location(method.run, name_map)
        method_signals_location[get_id(method)] = MethodSignalsLocation(run_loc)

    return (transaction_signals_location, method_signals_location)


@dataclass
class SignalLogRecord(logging.LogRecordInfo):
    """A SignalLogRecord instance represents an event being logged."""

    trigger: Signal
    """Amaranth signal triggering the log."""

    fields: tuple[Signal, ...] = tuple()
    """Amaranth signals that will be used to format the message."""


class VerilogDebugWrapper(Elaboratable):
    """Wraps an elaboratable in order to expose debug information (log records
    and event emission sites) as named signals locatable in the generated
    Verilog code."""

    def __init__(self, elaboratable: Elaboratable):
        self.elaboratable = elaboratable
        self.records: list[SignalLogRecord] = []
        self.evlog_records: list[tuple[EmittedEvent, Signal, list[Signal]]] = []
        self.evlog_triggers: Optional[Signal] = None

    def elaborate(self, platform):
        m = Module()

        elaboratable = Fragment.get(self.elaboratable, platform)
        m.submodules.elaboratable = elaboratable

        def to_signal(val: Value | ValueCastable) -> Signal:
            val = Value.cast(val)
            if isinstance(val, Signal):
                return val
            else:
                sig = Signal.like(val)
                m.d.comb += sig.eq(val)
                return sig

        for record in logging.get_log_records(0):
            record_dict = asdict(record)
            record_dict["trigger"] = to_signal(record.trigger)
            record_dict["fields"] = tuple(to_signal(val) for val in record.fields)
            self.records.append(SignalLogRecord(**record_dict))

        for emitted in get_emitted_events():
            trigger = to_signal(emitted.trigger)
            fields = [to_signal(val) for val in emitted.fields.values()]
            self.evlog_records.append((emitted, trigger, fields))

        if self.evlog_records:
            # A packed vector of all event triggers, so that simulators can
            # check all emission sites with a single signal read per cycle.
            self.evlog_triggers = Signal(len(self.evlog_records), name="evlog_triggers")
            m.d.comb += self.evlog_triggers.eq(Cat(trigger for _, trigger, _ in self.evlog_records))

        return m

    def collect_logs(self, name_map: "SignalDict") -> list[GeneratedLog]:
        logs: list[GeneratedLog] = []

        for record in self.records:
            trigger_loc = get_signal_location(record.trigger, name_map)
            fields_loc = [get_signal_location(field, name_map) for field in record.fields]
            log = GeneratedLog(
                logger_name=record.logger_name,
                level=record.level,
                format_str=record.format_str,
                location=record.location,
                trigger_location=trigger_loc,
                fields_location=fields_loc,
            )
            logs.append(log)

        return logs

    def collect_evlog(self, name_map: "SignalDict") -> GeneratedEvLog:
        schema = schema_from_records(emitted for emitted, _, _ in self.evlog_records)
        site_locations = [
            EventSiteLocation(
                trigger=list(get_signal_location(trigger, name_map)),
                fields=[list(get_signal_location(field, name_map)) for field in fields],
            )
            for _, trigger, fields in self.evlog_records
        ]
        triggers_location = (
            list(get_signal_location(self.evlog_triggers, name_map)) if self.evlog_triggers is not None else None
        )
        return GeneratedEvLog(schema=schema, site_locations=site_locations, triggers_location=triggers_location)


def generate_verilog(
    elaboratable: Elaboratable,
    ports: Optional[list[Value]] = None,
    top_name: str = "top",
    *,
    enable_hacks: Iterable[str] = {},
) -> tuple[str, GenerationInfo]:
    # The ports logic is copied (and simplified) from amaranth.back.verilog.convert.
    # Unfortunately, the convert function doesn't return the name map.
    if ports is None and isinstance(elaboratable, AbstractInterface):
        ports = []
        for _, _, value in elaboratable.signature.flatten(elaboratable):
            ports.append(Value.cast(value))
    elif ports is None:
        raise TypeError("The `generate_verilog()` function requires a `ports=` argument")

    wrapped_elaboratable = VerilogDebugWrapper(elaboratable)
    design = Fragment.get(wrapped_elaboratable, platform=None).prepare(ports=ports)

    if "fixup_vivado_transparent_memories" in enable_hacks:
        fixup_vivado_transparent_memories(design)

    verilog_text, name_map = verilog.convert_fragment(design, name=top_name, emit_src=True, strip_internal_attrs=True)

    transaction_manager = DependencyContext.get().get_dependency(TransactionManagerKey())
    transaction_signals, method_signals = collect_transaction_method_signals(
        transaction_manager, name_map  # type: ignore
    )
    profile_data, _ = ProfileData.make(transaction_manager)
    gen_info = GenerationInfo(
        metrics_location=collect_metric_locations(name_map),  # type: ignore
        transaction_signals_location=transaction_signals,
        method_signals_location=method_signals,
        profile_data=profile_data,
        logs=wrapped_elaboratable.collect_logs(name_map),
        evlog=wrapped_elaboratable.collect_evlog(name_map),  # type: ignore
    )

    return verilog_text, gen_info
