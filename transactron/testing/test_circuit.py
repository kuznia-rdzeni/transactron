import inspect
from typing import TypeVar, Generic, TypeGuard, Any, TypeAlias
from amaranth import *
from amaranth.sim import *
from transactron.core.method import MethodDir
from transactron.lib.adapters import Adapter

from .testbenchio import TestbenchIO
from transactron import Method, Methods
from transactron.lib import AdapterTrans
from transactron.utils import ModuleConnector, HasElaborate, auto_debug_signals


__all__ = ["SimpleTestCircuit"]


T = TypeVar("T")
_T_nested_collection: TypeAlias = T | list["_T_nested_collection[T]"] | dict[str, "_T_nested_collection[T]"]


def guard_nested_collection(cont: Any, *t: type[T]) -> TypeGuard[_T_nested_collection[T]]:
    if isinstance(cont, (list, dict)):
        if isinstance(cont, dict):
            cont = cont.values()
        return all([guard_nested_collection(elem, *t) for elem in cont])
    elif isinstance(cont, t):
        return True
    else:
        return False


_T_HasElaborate = TypeVar("_T_HasElaborate", bound=HasElaborate)


class SimpleTestCircuit(Elaboratable, Generic[_T_HasElaborate]):
    def __init__(self, dut: _T_HasElaborate):
        self._dut = dut
        self._io: dict[str, _T_nested_collection[TestbenchIO]] = {}

    def __getattr__(self, name: str) -> Any:
        try:
            return self._io[name]
        except KeyError:
            raise AttributeError(f"No mock for '{name}'")

    def elaborate(self, platform):
        def transform_methods_to_testbenchios(
            adapter_type: type[Adapter] | type[AdapterTrans],
            container: _T_nested_collection[Method | Methods],
        ) -> tuple[
            _T_nested_collection["TestbenchIO"],
            "ModuleConnector | TestbenchIO",
        ]:
            if isinstance(container, list):
                tb_list = []
                mc_list = []
                for elem in container:
                    tb, mc = transform_methods_to_testbenchios(adapter_type, elem)
                    tb_list.append(tb)
                    mc_list.append(mc)
                return tb_list, ModuleConnector(*mc_list)
            elif isinstance(container, dict):
                tb_dict = {}
                mc_dict = {}
                for name, elem in container.items():
                    tb, mc = transform_methods_to_testbenchios(adapter_type, elem)
                    tb_dict[name] = tb
                    mc_dict[name] = mc
                return tb_dict, ModuleConnector(*mc_dict)
            elif isinstance(container, Methods):
                tb_list = [TestbenchIO(adapter_type(method)) for method in container]
                return list(tb_list), ModuleConnector(*tb_list)
            else:
                tb = TestbenchIO(adapter_type(container))
                return tb, tb

        m = Module()

        m.submodules.dut = self._dut
        hints: dict[str, Any] = {}
        for cls in reversed(self._dut.__class__.__mro__):
            hints.update(inspect.get_annotations(cls, eval_str=True))

        for name, attr in vars(self._dut).items():
            if guard_nested_collection(attr, Method, Methods) and attr:
                if (
                    name in hints
                    and hasattr(hints[name], "__metadata__")
                    and MethodDir.REQUIRED in hints[name].__metadata__
                ):
                    adapter_type = Adapter
                else:  # PROVIDED is the default
                    adapter_type = AdapterTrans
                tb_cont, mc = transform_methods_to_testbenchios(adapter_type, attr)
                self._io[name] = tb_cont
                m.submodules[name] = mc

        return m

    def debug_signals(self):
        sigs = {"_dut": auto_debug_signals(self._dut)}
        for name, io in self._io.items():
            sigs[name] = auto_debug_signals(io)
        return sigs
