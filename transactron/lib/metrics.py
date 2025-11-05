from dataclasses import dataclass, field
from amaranth.lib.data import ArrayLayout, StructLayout
from dataclasses_json import dataclass_json
from typing import Optional, Type, TypeVar
from abc import ABC
from enum import Enum

from amaranth import *
from amaranth.utils import bits_for, ceil_log2, exact_log2

from transactron.utils import OneHotSwitchDynamic, ValueBundle
from transactron import Method, Methods, def_methods, TModule
from transactron.lib import WideFifo, AsyncMemoryBank, logging
from transactron.utils.amaranth_ext.functions import and_value, max_value, min_value, or_value, sum_value, popcount
from transactron.utils.dependencies import ListKey, DependencyContext, SimpleKey

__all__ = [
    "MetricRegisterModel",
    "MetricModel",
    "HwMetric",
    "HwCounter",
    "TaggedCounter",
    "HwExpHistogram",
    "WideFIFOLatencyMeasurer",
    "FIFOLatencyMeasurer",
    "TaggedLatencyMeasurer",
    "HardwareMetricsManager",
    "HwMetricsEnabledKey",
]


_T_Method = TypeVar("_T_Method", Method, Methods)


@dataclass_json
@dataclass(frozen=True)
class MetricRegisterModel:
    """
    Represents a single register of a metric, serving as a fundamental
    building block that holds a singular value.

    Attributes
    ----------
    name: str
        The unique identifier for the register (among remaning
        registers of a specific metric).
    description: str
        A brief description of the metric's purpose.
    width: int
        The bit-width of the register.
    """

    name: str
    description: str
    width: int


@dataclass_json
@dataclass
class MetricModel:
    """
    Provides information about a metric exposed by the circuit. Each metric
    comprises multiple registers, each dedicated to storing specific values.

    The configuration of registers is internally determined by a
    specific metric type and is not user-configurable.

    Attributes
    ----------
    fully_qualified_name: str
        The fully qualified name of the metric, with name components joined by dots ('.'),
        e.g., 'foo.bar.requests'.
    description: str
        A human-readable description of the metric's functionality.
    regs: list[MetricRegisterModel]
        A list of registers associated with the metric.
    """

    fully_qualified_name: str
    description: str
    regs: dict[str, MetricRegisterModel] = field(default_factory=dict)


class HwMetricRegister(MetricRegisterModel):
    """
    A concrete implementation of a metric register that holds its value as Amaranth signal.

    Attributes
    ----------
    value: Signal
        Amaranth signal representing the value of the register.
    """

    def __init__(self, name: str, width_bits: int, description: str = "", init: int = 0):
        """
        Parameters
        ----------
        name: str
            The unique identifier for the register (among remaning
            registers of a specific metric).
        width: int
            The bit-width of the register.
        description: str
            A brief description of the metric's purpose.
        init: int
            The reset value of the register.
        """
        super().__init__(name, description, width_bits)

        self.value = Signal(width_bits, init=init, name=name)


@dataclass(frozen=True)
class HwMetricsListKey(ListKey["HwMetric"]):
    """DependencyManager key collecting hardware metrics globally as a list."""

    pass


@dataclass(frozen=True)
class HwMetricsEnabledKey(SimpleKey[bool]):
    """
    DependencyManager key for enabling hardware metrics. If metrics are disabled,
    none of theirs signals will be synthesized.
    """

    empty_valid = True
    default_value = False


# TODO: find a cleaner way to make metric methods disappear when disabled.
class DummyMethod(Method):
    """
    Hacky way to make a method ignore calls, allowing it not to be defined and
    making it not appear in the final call graph.
    """

    def __call__(self, *args, **kwargs):
        return Signal(StructLayout({}))


class HwMetric(ABC, MetricModel):
    """
    A base for all metric implementations. It should be only used for declaring
    new types of metrics.

    It takes care of registering the metric in the dependency manager.

    Attributes
    ----------
    signals: dict[str, Signal]
        A mapping from a register name to a Signal containing the value of that register.
    """

    def __init__(self, fully_qualified_name: str, description: str):
        """
        Parameters
        ----------
        fully_qualified_name: str
            The fully qualified name of the metric.
        description: str
            A human-readable description of the metric's functionality.
        """
        super().__init__(fully_qualified_name, description)

        self.signals: dict[str, Signal] = {}

        # add the metric to the global list of all metrics
        DependencyContext.get().add_dependency(HwMetricsListKey(), self)

        # So Amaranth doesn't report that the module is unused when metrics are disabled
        self._MustUse__silence = True  # type: ignore

    def add_registers(self, regs: list[HwMetricRegister]):
        """
        Adds registers to a metric. Should be only called by inheriting classes
        during initialization.

        Parameters
        ----------
        regs: list[HwMetricRegister]
            A list of registers to be registered.
        """
        for reg in regs:
            if reg.name in self.regs:
                raise RuntimeError(f"Register {reg.name}' is already added to the metric {self.fully_qualified_name}")

            self.regs[reg.name] = reg
            self.signals[reg.name] = reg.value

    @staticmethod
    def metrics_enabled() -> bool:
        return DependencyContext.get().get_dependency(HwMetricsEnabledKey())

    @staticmethod
    def wrap_method(method: _T_Method) -> _T_Method:
        if not HwMetric.metrics_enabled():

            if isinstance(method, Method):
                method.__class__ = DummyMethod
            else:
                for m in method:
                    m.__class__ = DummyMethod
        return method

    # To restore hashability lost by dataclass subclassing
    def __hash__(self):
        return object.__hash__(self)


class HwCounter(Elaboratable, HwMetric):
    """Hardware Counter

    The most basic hardware metric that can just increase its value.
    """

    def __init__(self, fully_qualified_name: str, description: str = "", *, width_bits: int = 32, ways: int = 1):
        """
        Parameters
        ----------
        fully_qualified_name: str
            The fully qualified name of the metric.
        description: str
            A human-readable description of the metric's functionality.
        width_bits: int
            The bit-width of the register. Defaults to 32 bits.
        ways: int
            The number of users of this counter.
        """

        super().__init__(fully_qualified_name, description)

        self.count = HwMetricRegister("count", width_bits, "the value of the counter")

        self.add_registers([self.count])

        self.incr = self.wrap_method(Methods(ways))

    def elaborate(self, platform):
        if not self.metrics_enabled():
            return TModule()

        m = TModule()

        @def_methods(m, self.incr)
        def _(k: int):
            pass  # The actual counter logic is below

        m.d.sync += self.count.value.eq(self.count.value + popcount(Cat(method.run for method in self.incr)))

        return m

    incr: Methods
    """
    Increases the value of the counter by 1.

    Should be called in the body of either a transaction or a method.

    Parameters
    ----------
    m: TModule
        Transactron module
    """


class TaggedCounter(Elaboratable, HwMetric):
    """Hardware Tagged Counter

    Like HwCounter, but contains multiple counters, each with its own tag.
    At a time a single counter can be increased and the value of the tag
    can be provided dynamically. The type of the tag can be either an int
    enum, a range or a list of integers (negative numbers are ok).

    Internally, it detects if tag values can be one-hot encoded and if so,
    it generates more optimized circuit.

    Attributes
    ----------
    tag_width: int
        The length of the signal holding a tag value.
    one_hot: bool
        Whether tag values can be one-hot encoded.
    counters: dict[int, HwMetricRegisters]
        Mapping from a tag value to a register holding a counter for that tag.
    """

    def __init__(
        self,
        fully_qualified_name: str,
        description: str = "",
        *,
        tags: range | Type[Enum] | list[int],
        registers_width: int = 32,
        ways: int = 1,
    ):
        """
        Parameters
        ----------
        fully_qualified_name: str
            The fully qualified name of the metric.
        description: str
            A human-readable description of the metric's functionality.
        tags: range | Type[Enum] | list[int]
            Tag values.
        registers_width: int
            Width of the underlying registers. Defaults to 32 bits.
        ways: int
            The number of users of this counter.
        """

        super().__init__(fully_qualified_name, description)

        if isinstance(tags, range) or isinstance(tags, list):
            counters_meta = [(i, f"{i}") for i in tags]
        else:
            counters_meta = [(i.value, i.name) for i in tags]

        values = [value for value, _ in counters_meta]
        self.tag_width = max(bits_for(max(values)), bits_for(min(values)))

        self.one_hot = True
        negative_values = False
        for value in values:
            if value < 0:
                self.one_hot = False
                negative_values = True
                break

            log = ceil_log2(value)
            if 2**log != value:
                self.one_hot = False

        self.incr = self.wrap_method(Methods(ways, i=[("tag", Shape(self.tag_width, signed=negative_values))]))

        self.counters: dict[int, HwMetricRegister] = {}
        for tag_value, name in counters_meta:
            value_str = ("1<<" + str(exact_log2(tag_value))) if self.one_hot else str(tag_value)
            description = f"the counter for tag {name} (value={value_str})"

            self.counters[tag_value] = HwMetricRegister(
                name,
                registers_width,
                description,
            )

        self.add_registers(list(self.counters.values()))

    def elaborate(self, platform):
        if not self.metrics_enabled():
            return TModule()

        m = TModule()

        runs: dict[int, Signal] = {tag_value: Signal(len(self.incr)) for tag_value in self.counters.keys()}

        @def_methods(m, self.incr)
        def _(k: int, tag):
            if self.one_hot:
                sorted_tags = sorted(list(self.counters.keys()))
                for i in OneHotSwitchDynamic(m, tag):
                    m.d.comb += runs[sorted_tags[i]][k].eq(1)
            else:
                for tag_value in self.counters.keys():
                    with m.If(tag == tag_value):
                        m.d.comb += runs[tag_value][k].eq(1)

        for tag_value, counter in self.counters.items():
            m.d.sync += counter.value.eq(counter.value + popcount(runs[tag_value]))

        return m

    incr: Methods
    """
    Increases the counter of a given tag by 1.

    Should be called in the body of either a transaction or a method.

    Parameters
    ----------
    m: TModule
        Transactron module
    tag: ValueLike
        The tag of the counter.
    """


class HwExpHistogram(Elaboratable, HwMetric):
    """Hardware Exponential Histogram

    Represents the distribution of sampled data through a histogram. A histogram
    samples observations (usually things like request durations or queue sizes) and counts
    them in a configurable number of buckets. The buckets are of exponential size. For example,
    a histogram with 5 buckets would have the following value ranges:
    [0, 1); [1, 2); [2, 4); [4, 8); [8, +inf).

    Additionally, the histogram tracks the number of observations, the sum
    of observed values, and the minimum and maximum values.
    """

    def __init__(
        self,
        fully_qualified_name: str,
        description: str = "",
        *,
        bucket_count: int,
        sample_width: int = 32,
        registers_width: int = 32,
        ways: int = 1,
    ):
        """
        Parameters
        ----------
        fully_qualified_name: str
            The fully qualified name of the metric.
        description: str
            A human-readable description of the metric's functionality.
        max_value: int
            The maximum value that the histogram would be able to count. This
            value is used to calculate the number of buckets.
        ways: int
            The number of users of this metric.
        """

        super().__init__(fully_qualified_name, description)
        self.bucket_count = bucket_count
        self.sample_width = sample_width

        self.add = self.wrap_method(Methods(ways, i=[("sample", self.sample_width)]))

        self.count = HwMetricRegister("count", registers_width, "the count of events that have been observed")
        self.sum = HwMetricRegister("sum", registers_width, "the total sum of all observed values")
        self.min = HwMetricRegister(
            "min",
            self.sample_width,
            "the minimum of all observed values",
            init=(1 << self.sample_width) - 1,
        )
        self.max = HwMetricRegister("max", self.sample_width, "the maximum of all observed values")

        self.buckets: list[HwMetricRegister] = []
        for i in range(self.bucket_count):
            bucket_start = 0 if i == 0 else 2 ** (i - 1)
            bucket_end = "inf" if i == self.bucket_count - 1 else 2**i

            self.buckets.append(
                HwMetricRegister(
                    f"bucket-{bucket_end}",
                    registers_width,
                    f"the cumulative counter for the observation bucket [{bucket_start}, {bucket_end})",
                )
            )

        self.add_registers([self.count, self.sum, self.max, self.min] + self.buckets)

    def elaborate(self, platform):
        if not self.metrics_enabled():
            return TModule()

        m = TModule()

        bucket_incrs: list[Signal] = [Signal(len(self.add)) for _ in self.buckets]

        @def_methods(m, self.add)
        def _(k: int, sample):
            # todo: perhaps replace with a recursive implementation of the priority encoder
            bucket_idx = Signal(range(self.sample_width))
            for i in range(self.sample_width):
                with m.If(sample[i]):
                    m.d.av_comb += bucket_idx.eq(i)

            for i in range(len(self.buckets)):
                if i == 0:
                    # The first bucket has a range [0, 1).
                    should_incr = sample == 0
                elif i == self.bucket_count - 1:
                    # The last bucket should count values bigger or equal to 2**(self.bucket_count-1)
                    should_incr = (bucket_idx >= i - 1) & (sample != 0)
                else:
                    should_incr = (bucket_idx == i - 1) & (sample != 0)

                m.d.comb += bucket_incrs[i][k].eq(should_incr)

        def sample_or_default(method: Method, default: Value) -> Value:
            return Mux(method.run, method.data_in.sample, default)

        method_min_samples = list(sample_or_default(m, C((1 << self.sample_width)) - 1) for m in self.add)
        method_max_samples = list(sample_or_default(m, C(0)) for m in self.add)

        min_sample = min_value(self.min.value, method_min_samples)
        max_sample = max_value(self.max.value, method_max_samples)
        sample_sum = sum_value(self.sum.value, method_max_samples)

        m.d.sync += self.min.value.eq(min_sample)
        m.d.sync += self.max.value.eq(max_sample)
        m.d.sync += self.count.value.eq(self.count.value + popcount(Cat(m.run for m in self.add)))
        m.d.sync += self.sum.value.eq(sample_sum)

        for i, bucket in enumerate(self.buckets):
            m.d.sync += bucket.value.eq(bucket.value + popcount(bucket_incrs[i]))

        return m

    add: Methods
    """
    Adds a new sample to the histogram.

    Should be called in the body of either a transaction or a method.

    Parameters
    ----------
    m: TModule
        Transactron module
    sample: ValueLike
        The value that will be added to the histogram
    """


class WideFIFOLatencyMeasurer(Elaboratable):
    """
    Measures duration between two events, e.g. request processing latency.
    It can track multiple events at the same time, i.e. the second event can
    be registered as started, before the first finishes. However, they must be
    processed in the FIFO order.

    The module exposes an exponential histogram of the measured latencies.
    """

    def __init__(
        self,
        fully_qualified_name: str,
        description: str = "",
        *,
        slots_number: int,
        max_latency: int,
        max_start_count: int = 1,
        max_stop_count: Optional[int] = None,
        ways: int = 1,
    ):
        """
        Parameters
        ----------
        fully_qualified_name: str
            The fully qualified name of the metric.
        description: str
            A human-readable description of the metric's functionality.
        slots_number: int
            A number of events that the module can track simultaneously
            per module user.
        max_latency: int
            The maximum latency of an event. Used to set signal widths and
            number of buckets in the histogram. If a latency turns to be
            bigger than the maximum, it will overflow and result in a false
            measurement.
        max_start_count: int
            The maximum number of event starts that can be registered from
            a single user in a single clock cycle.
        max_stop_count: int, optional
            The maximum number of event stops that can be registered from
            a single user in a single clock cycle. If omitted, assumed to
            be equal to `max_start_count`.
        ways: int, optional
            The number of users of this metric. If omitted, assumed to be
            equal to 1.
        """
        if max_stop_count is None:
            max_stop_count = max_start_count

        self.fully_qualified_name = fully_qualified_name
        self.description = description
        self.max_latency = max_latency
        self.max_start_count = max_start_count
        self.max_stop_count = max_stop_count
        max_count = max(max_start_count, max_stop_count)
        # slots_number is rounded up to a multiple of max_count because of WideFifo requirements
        self.slots_number = (slots_number + (max_count - 1)) // max_count * max_count

        self.start = HwMetric.wrap_method(Methods(ways, i=[("count", range(max_start_count + 1))]))
        self.stop = HwMetric.wrap_method(Methods(ways, i=[("count", range(max_stop_count + 1))]))

        # This bucket count gives us the best possible granularity.
        bucket_count = bits_for(self.max_latency) + 1
        self.histogram = HwExpHistogram(
            self.fully_qualified_name,
            self.description,
            bucket_count=bucket_count,
            sample_width=bits_for(self.max_latency),
            ways=ways * max_stop_count,
        )

    def elaborate(self, platform):
        if not HwMetric.metrics_enabled():
            return TModule()

        m = TModule()

        epoch_width = bits_for(self.max_latency)

        self.fifos = [
            WideFifo(epoch_width, self.slots_number, self.max_stop_count, self.max_start_count)
            for _ in range(len(self.start))
        ]
        for k in range(len(self.start)):
            m.submodules[f"fifo{k}"] = self.fifos[k]

        m.submodules.histogram = self.histogram

        epoch = Signal(epoch_width)

        m.d.sync += epoch.eq(epoch + 1)

        @def_methods(m, self.start)
        def _(k: int, count: Value):
            self.fifos[k].write(m, count=count, data=[epoch] * self.max_start_count)

        @def_methods(m, self.stop)
        def _(k: int, count: Value):
            ret = self.fifos[k].read(m, count=count)
            for i in range(self.max_stop_count):
                with m.If(i < ret.count):
                    # The result of subtracting two unsigned n-bit is a signed (n+1)-bit value,
                    # so we need to cast the result and discard the most significant bit.
                    duration = (epoch - ret.data[i]).as_unsigned()[:-1]
                    self.histogram.add[k * self.max_stop_count + i](m, duration)

        return m

    start: Methods
    """
    Registers the start of an event. Can be called before the previous events
    finish. If there are no slots available, the method will be blocked.

    Should be called in the body of either a transaction or a method.

    Parameters
    ----------
    m: TModule
        Transactron module
    count: ValueLike
        Number of registered events.
    """

    stop: Methods
    """
    Registers the end of the oldest event (the FIFO order). If there are no
    started events in the queue, the method will block.

    Should be called in the body of either a transaction or a method.

    Parameters
    ----------
    m: TModule
        Transactron module
    count: ValueLike
        Number of registered events.
    """


class FIFOLatencyMeasurer(Elaboratable):
    """
    Measures duration between two events, e.g. request processing latency.
    It can track multiple events at the same time, i.e. the second event can
    be registered as started, before the first finishes. However, they must be
    processed in the FIFO order.

    The module exposes an exponential histogram of the measured latencies.
    """

    def __init__(
        self, fully_qualified_name: str, description: str = "", *, slots_number: int, max_latency: int, ways: int = 1
    ):
        """
        Parameters
        ----------
        fully_qualified_name: str
            The fully qualified name of the metric.
        description: str
            A human-readable description of the metric's functionality.
        slots_number: int
            A number of events that the module can track simultaneously
            per module user.
        max_latency: int
            The maximum latency of an event. Used to set signal widths and
            number of buckets in the histogram. If a latency turns to be
            bigger than the maximum, it will overflow and result in a false
            measurement.
        ways: int, optional
            The number of users of this metric. If omitted, assumed to be
            equal to 1.
        """
        self.fully_qualified_name = fully_qualified_name
        self.description = description
        self.slots_number = slots_number
        self.max_latency = max_latency
        self.ways = ways

        self.start = HwMetric.wrap_method(Methods(ways))
        self.stop = HwMetric.wrap_method(Methods(ways))

        self._impl = WideFIFOLatencyMeasurer(
            fully_qualified_name,
            description,
            slots_number=slots_number,
            max_latency=max_latency,
            ways=self.ways,
        )
        self.histogram = self._impl.histogram

    def elaborate(self, platform):
        m = TModule()

        m.submodules.impl = self._impl

        @def_methods(m, self.start)
        def _(k: int):
            self._impl.start[k](m, count=1)

        @def_methods(m, self.stop)
        def _(k: int):
            self._impl.stop[k](m, count=1)

        return m

    start: Methods
    """
    Registers the start of an event. Can be called before the previous events
    finish. If there are no slots available, the method will be blocked.

    Should be called in the body of either a transaction or a method.

    Parameters
    ----------
    m: TModule
        Transactron module
    """

    stop: Methods
    """
    Registers the end of the oldest event (the FIFO order). If there are no
    started events in the queue, the method will block.

    Should be called in the body of either a transaction or a method.

    Parameters
    ----------
    m: TModule
        Transactron module
    """


class TaggedLatencyMeasurer(Elaboratable):
    """
    Measures duration between two events, e.g. request processing latency.
    It can track multiple events at the same time, i.e. the second event can
    be registered as started, before the first finishes. However, each event
    needs to have an unique slot tag.

    The module exposes an exponential histogram of the measured latencies.
    """

    def __init__(
        self, fully_qualified_name: str, description: str = "", *, slots_number: int, max_latency: int, ways: int = 1
    ):
        """
        Parameters
        ----------
        fully_qualified_name: str
            The fully qualified name of the metric.
        description: str
            A human-readable description of the metric's functionality.
        slots_number: int
            A number of events that the module can track simultaneously.
        max_latency: int
            The maximum latency of an event. Used to set signal widths and
            number of buckets in the histogram. If a latency turns to be
            bigger than the maximum, it will overflow and result in a false
            measurement.
        ways: int
            The number of users of this metric.
        """
        self.fully_qualified_name = fully_qualified_name
        self.description = description
        self.slots_number = slots_number
        self.max_latency = max_latency

        self.start = HwMetric.wrap_method(Methods(ways, i=[("slot", range(0, slots_number))]))
        self.stop = HwMetric.wrap_method(Methods(ways, i=[("slot", range(0, slots_number))]))

        # This bucket count gives us the best possible granularity.
        bucket_count = bits_for(self.max_latency) + 1
        self.histogram = HwExpHistogram(
            self.fully_qualified_name,
            self.description,
            bucket_count=bucket_count,
            sample_width=bits_for(self.max_latency),
            ways=ways,
        )

        self.log = logging.HardwareLogger(fully_qualified_name)

    def elaborate(self, platform):
        if not HwMetric.metrics_enabled():
            return TModule()

        m = TModule()

        epoch_width = bits_for(self.max_latency)

        m.submodules.slots = self.slots = AsyncMemoryBank(
            shape=epoch_width,
            depth=self.slots_number,
            write_ports=len(self.start),
            read_ports=len(self.stop),
        )
        m.submodules.histogram = self.histogram

        slots_taken = Signal(self.slots_number)
        slots_taken_start = Signal(ArrayLayout(self.slots_number, len(self.start)))
        st_stop_init = [2**self.slots_number - 1 for _ in range(len(self.stop))]
        slots_taken_stop = Signal(ArrayLayout(self.slots_number, len(self.stop)), init=st_stop_init)

        m.d.sync += slots_taken.eq(or_value(slots_taken_start, and_value(slots_taken_stop, slots_taken)))

        epoch = Signal(epoch_width)

        m.d.sync += epoch.eq(epoch + 1)

        @def_methods(m, self.start)
        def _(k: int, slot: Value):
            m.d.comb += slots_taken_start[k].eq(1 << slot)
            self.log.error(m, (slots_taken & (1 << slot)).any(), "taken slot {} taken again", slot)
            self.slots.write[k](m, addr=slot, data=epoch)

        @def_methods(m, self.stop)
        def _(k: int, slot: Value):
            m.d.comb += slots_taken_stop[k].eq(~(C(1, self.slots_number) << slot))
            self.log.error(m, ~(slots_taken & (1 << slot)).any(), "free slot {} freed again", slot)
            ret = self.slots.read[k](m, addr=slot)
            # The result of substracting two unsigned n-bit is a signed (n+1)-bit value,
            # so we need to cast the result and discard the most significant bit.
            duration = (epoch - ret.data).as_unsigned()[:-1]
            self.histogram.add[k](m, duration)

        return m

    start: Methods
    """
    Registers the start of an event for a given slot tag.

    Should be called in the body of either a transaction or a method.

    Parameters
    ----------
    m: TModule
        Transactron module
    slot: ValueLike
        The slot tag of the event.
    """

    stop: Methods
    """
    Registers the end of the event for a given slot tag.

    Should be called in the body of either a transaction or a method.

    Parameters
    ----------
    m: TModule
        Transactron module
    slot: ValueLike
        The slot tag of the event.
    """


class HardwareMetricsManager:
    """
    Collects all metrics registered in the circuit and provides an easy
    access to them.
    """

    def __init__(self):
        self._metrics: Optional[dict[str, HwMetric]] = None

    def _collect_metrics(self) -> dict[str, HwMetric]:
        # We lazily collect all metrics so that the metrics manager can be
        # constructed at any time. Otherwise, if a metric object was created
        # after the manager object had been created, that metric wouldn't end up
        # being registered.
        metrics: dict[str, HwMetric] = {}
        for metric in DependencyContext.get().get_dependency(HwMetricsListKey()):
            if metric.fully_qualified_name in metrics:
                raise RuntimeError(f"Metric '{metric.fully_qualified_name}' is already registered")

            metrics[metric.fully_qualified_name] = metric

        return metrics

    def get_metrics(self) -> dict[str, HwMetric]:
        """
        Returns all metrics registered in the circuit.
        """
        if self._metrics is None:
            self._metrics = self._collect_metrics()
        return self._metrics

    def get_register_value(self, metric_name: str, reg_name: str) -> Signal:
        """
        Returns the signal holding the register value of the given metric.

        Parameters
        ----------
        metric_name: str
            The fully qualified name of the metric, for example 'frontend.icache.loads'.
        reg_name: str
            The name of the register from that metric, for example if
            the metric is a histogram, the 'reg_name' could be 'min'
            or 'bucket-32'.
        """

        metrics = self.get_metrics()
        if metric_name not in metrics:
            raise RuntimeError(f"Couldn't find metric '{metric_name}'")
        return metrics[metric_name].signals[reg_name]

    def debug_signals(self) -> ValueBundle:
        """
        Returns tree-like SignalBundle composed of all metric registers.
        """
        metrics = self.get_metrics()

        def rec(metric_names: list[str], depth: int = 1):
            bundle: list[ValueBundle] = []
            components: dict[str, list[str]] = {}

            for metric in metric_names:
                parts = metric.split(".")

                if len(parts) == depth:
                    signals = metrics[metric].signals
                    reg_values = [signals[reg_name] for reg_name in signals]

                    bundle.append({metric: reg_values})

                    continue

                component_prefix = ".".join(parts[:depth])

                if component_prefix not in components:
                    components[component_prefix] = []
                components[component_prefix].append(metric)

            for component_name, elements in components.items():
                bundle.append({component_name: rec(elements, depth + 1)})

            return bundle

        return {"metrics": rec(list(self.get_metrics().keys()))}
