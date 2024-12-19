from abc import abstractmethod
from typing import Optional, Unpack
from amaranth import *
from amaranth.lib.wiring import Component, In, Out
from amaranth.lib.data import StructLayout, View

from ..utils import SrcLoc, get_src_loc, MethodStruct
from ..core import *
from ..utils._typing import SignalBundle, MethodLayout

__all__ = [
    "AdapterBase",
    "AdapterTrans",
    "Adapter",
]


class AdapterBase(Component):
    data_in: MethodStruct
    data_out: MethodStruct
    en: Signal
    done: Signal

    def __init__(self, iface: Method, layout_in: StructLayout, layout_out: StructLayout):
        super().__init__({"data_in": In(layout_in), "data_out": Out(layout_out), "en": In(1), "done": Out(1)})
        self.iface = iface

    def debug_signals(self) -> SignalBundle:
        return [self.en, self.done, self.data_in, self.data_out]

    @abstractmethod
    def elaborate(self, platform) -> TModule:
        raise NotImplementedError()


class AdapterTrans(AdapterBase):
    """Adapter transaction.

    Creates a transaction controlled by plain Amaranth signals. Allows to
    expose a method to plain Amaranth code, including testbenches.

    Attributes
    ----------
    en: Signal, in
        Activates the transaction (sets the `request` signal).
    done: Signal, out
        Signals that the transaction is performed (returns the `grant`
        signal).
    data_in: View, in
        Data passed to the `iface` method.
    data_out: View, out
        Data returned from the `iface` method.
    """

    def __init__(self, iface: Method, *, src_loc: int | SrcLoc = 0):
        """
        Parameters
        ----------
        iface: Method
            The method to be called by the transaction.
        src_loc: int | SrcLoc
            How many stack frames deep the source location is taken from.
            Alternatively, the source location to use instead of the default.
        """
        super().__init__(iface, iface.layout_in, iface.layout_out)
        self.src_loc = get_src_loc(src_loc)

    def elaborate(self, platform):
        m = TModule()

        # this forces data_in signal to appear in VCD dumps
        data_in = Signal.like(self.data_in)
        m.d.comb += data_in.eq(self.data_in)

        with Transaction(name=f"AdapterTrans_{self.iface.name}", src_loc=self.src_loc).body(m, request=self.en):
            data_out = self.iface(m, data_in)
            m.d.top_comb += self.data_out.eq(data_out)
            m.d.comb += self.done.eq(1)

        return m


class Adapter(AdapterBase):
    """Adapter method.

    Creates a method controlled by plain Amaranth signals. One of the
    possible uses is to mock a method in a testbench.

    Attributes
    ----------
    en: Signal, in
        Activates the method (sets the `ready` signal).
    done: Signal, out
        Signals that the method is called (returns the `run` signal).
    data_in: View, in
        Data returned from the defined method.
    data_out: View, out
        Data passed as argument to the defined method.
    validators: list of tuples of View, out and Signal, in
        Hooks for `validate_arguments`.
    """

    def __init__(self, method: Method, /, **kwargs: Unpack[AdapterBodyParams]):
        """
        Parameters
        ----------
        **kwargs
            Keyword arguments for Method that will be created.
            See transactron.core.Method.__init__ for parameters description.
        """

        super().__init__(method, method.layout_out, method.layout_in)
        self.validators: list[tuple[View[StructLayout], Signal]] = []
        self.with_validate_arguments: bool = False
        self.kwargs = kwargs

    @staticmethod
    def create(
        name: Optional[str] = None,
        i: MethodLayout = [],
        o: MethodLayout = [],
        src_loc: int | SrcLoc = 0,
        **kwargs: Unpack[AdapterBodyParams],
    ):
        method = Method(name=name, i=i, o=o, src_loc=get_src_loc(src_loc))
        return Adapter(method, **kwargs)

    def update_args(self, **kwargs: Unpack[AdapterBodyParams]):
        self.kwargs.update(kwargs)
        return self

    def set(self, with_validate_arguments: Optional[bool]):
        if with_validate_arguments is not None:
            self.with_validate_arguments = with_validate_arguments
        return self

    def elaborate(self, platform):
        m = TModule()

        # this forces data_in signal to appear in VCD dumps
        data_in = Signal.like(self.data_in)
        m.d.comb += data_in.eq(self.data_in)

        kwargs: BodyParams = self.kwargs  # type: ignore (pyright complains about optional attribute)

        if self.with_validate_arguments:

            def validate_arguments(arg: "View[StructLayout]"):
                ret = Signal()
                self.validators.append((arg, ret))
                return ret

            kwargs["validate_arguments"] = validate_arguments

        @def_method(m, self.iface, ready=self.en, **kwargs)
        def _(arg):
            m.d.top_comb += self.data_out.eq(arg)
            m.d.comb += self.done.eq(1)
            return data_in

        return m
