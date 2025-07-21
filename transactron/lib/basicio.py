from amaranth import *
from amaranth.lib.data import StructLayout, View
from amaranth.lib.wiring import Component, In, Out

from transactron.utils.transactron_helpers import from_method_layout
from ..core import *
from ..utils import SrcLoc, get_src_loc, MethodLayout

__all__ = ["InputSampler", "OutputBuffer"]


class BasicIOBase(Component):
    trigger: Signal
    data: View

    def __init__(self, layout: StructLayout, direction: bool, edge: bool, polarity: bool, synchronize: bool):
        if direction:
            super().__init__({"trigger": In(1), "data": Out(layout)})
        else:
            super().__init__({"trigger": In(1), "data": In(layout)})
        self._edge = edge
        self._polarity = polarity
        self._synchronize = synchronize

    def _trigger(self, m: TModule) -> Value:
        if self._synchronize:
            trigger = Signal()
            m.d.sync += trigger.eq(self.trigger)
        else:
            trigger = self.trigger

        if not self._polarity:
            trigger = ~trigger

        if self._edge:
            old_trigger = Signal(init=not self._polarity)
            new_trigger = trigger
            m.d.sync += old_trigger.eq(new_trigger)
            trigger = Signal()
            m.d.comb += trigger.eq(new_trigger & ~old_trigger)

        return trigger


class InputSampler(BasicIOBase):
    """Input sampler.

    The `get` method samples input data `data`, which is possible only
    when activated by the input signal `trigger`.

    The trigger logic is configurable using constructor parameters.
    If the `trigger` signal is not supplied, the default triggering logic
    (low level) implies that the trigger is always active.

    Inputs are optionally synchronized, which can be used for sampling
    asynchronous signals from e.g. FPGA inputs.

    Attributes
    ----------
    trigger: Signal, in
        The button input.
    data: MethodStruct, in
        The data input.
    """

    get: Provided[Method]
    """Samples and returns `data`.

    Enabled only if trigger is active. Takes no arguments, returns
    results as specified by the data layout `layout` passed to `__init__`.

    Parameters
    ----------
    m: TModule
        Transactron module.

    Returns
    -------
    MethodStruct
        Arguments as specified by the data layout.
    """

    def __init__(
        self,
        layout: MethodLayout,
        *,
        edge: bool = False,
        polarity: bool = False,
        synchronize: bool = False,
        src_loc: int | SrcLoc = 0,
    ):
        """
        Parameters
        ----------
        layout: MethodLayout
            The data format for the input.
        edge: bool, optional
            Trigger type. If true, edge triggering, otherwise level triggering.
            Level triggering is the default.
        polarity: bool, optional
            Trigger polarity. If true, positive trigger (rising edge or high
            level), otherwise negative trigger (falling edge or low level).
            Negative trigger is the default.
        synchronize: bool, optional
            If true, `trigger` and `data` inputs are synchronized using
            a register. Otherwise, inputs are assumed to be synchronous. No
            synchronizer by default.
        src_loc: int | SrcLoc, optional
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        src_loc = get_src_loc(src_loc)
        super().__init__(from_method_layout(layout), False, edge, polarity, synchronize)
        self.get = Method(o=layout, src_loc=src_loc)

    def elaborate(self, platform):
        m = TModule()

        if self._synchronize:
            data = Signal.like(self.data)
            m.d.sync += data.eq(self.data)
        else:
            data = self.data

        @def_method(m, self.get, ready=self._trigger(m))
        def _():
            return data

        return m


class OutputBuffer(BasicIOBase):
    """Output buffer.

    The `put` method modifies the output `data`, which is possible only
    when activated by the input signal `trigger`.

    The trigger logic is configurable using constructor parameters.
    If the `trigger` signal is not supplied, the default triggering logic
    (low level) implies that the trigger is always active.

    Inputs are optionally synchronized, which can be used for sampling
    asynchronous signals from e.g. FPGA inputs.

    Attributes
    ----------
    trigger: Signal, in
        The button input.
    data: MethodStruct, out
        The data output.
    """

    put: Provided[Method]
    """Updates the output buffer.

    Enabled only if trigger is active. Accepts arguments as specified by the
    data layout `layout` passed to `__init__`. Returns an empty structure.

    Parameters
    ----------
    m: TModule
        Transactron module.
    **kwargs: ValueLike
        Arguments as specified by the data layout.
    """

    def __init__(
        self,
        layout: MethodLayout,
        *,
        edge: bool = True,
        polarity: bool = True,
        synchronize: bool = False,
        src_loc: int | SrcLoc = 0,
    ):
        """
        Parameters
        ----------
        layout: method layout
            The data format for the `dat` output and `put` method arguments.
        edge: bool, optional
            Trigger type. If true, edge triggering, otherwise level triggering.
            Level triggering is the default.
        polarity: bool, optional
            Trigger polarity. If true, positive trigger (rising edge or high
            level), otherwise negative trigger (falling edge or low level).
            Negative trigger is the default.
        synchronize: bool, optional
            If true, `trigger` input is synchronized using a register.
            Otherwise, inputs are assumed to be synchronous. No synchronizer
            by default.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        src_loc = get_src_loc(src_loc)
        super().__init__(from_method_layout(layout), False, edge, polarity, synchronize)
        self.put = Method(i=layout, src_loc=src_loc)

    def elaborate(self, platform):
        m = TModule()

        @def_method(m, self.put, ready=self._trigger(m))
        def _(arg):
            m.d.sync += self.data.eq(arg)

        return m
