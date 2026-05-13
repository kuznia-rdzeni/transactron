from collections.abc import Iterable, Mapping, Sequence
from typing import TypeGuard, Any
from amaranth import *
from amaranth.sim import *
from transactron.lib.adapters import Adapter, AdapterTrans

from .testbenchio import TestbenchIO
from transactron.core import interface, Interface, InterfacePart, MethodDir
from transactron.utils import ModuleConnector, auto_debug_signals
from amaranth_types import HasElaborate


__all__ = ["SimpleTestCircuit"]


type _T_nested_collection[T] = T | list["_T_nested_collection[T]"] | dict[str, "_T_nested_collection[T]"]


def guard_nested_collection[T](cont: Any, *t: type[T]) -> TypeGuard[_T_nested_collection[T]]:
    if isinstance(cont, (list, dict)):
        if isinstance(cont, dict):
            cont = cont.values()
        return all([guard_nested_collection(elem, *t) for elem in cont])
    elif isinstance(cont, t):
        return True
    else:
        return False


def mdir_adapter_type(mdir: MethodDir) -> type[AdapterTrans] | type[Adapter]:
    match mdir:
        case MethodDir.PROVIDED:
            return AdapterTrans
        case MethodDir.REQUIRED:
            return Adapter


class SimpleTestCircuit[T: HasElaborate](Elaboratable):
    def __init__(self, dut: T, *, exclude: Iterable[str] = ()):
        self._dut = dut
        self._io: dict[str, _T_nested_collection[TestbenchIO]] = {}
        self._exclude = set(exclude)

    def __getattr__(self, name: str) -> Any:
        try:
            return self._io[name]
        except KeyError:
            raise AttributeError(f"No mock for '{name}'")

    def elaborate(self, platform):
        def transform_methods_to_testbenchios(
            mdir: MethodDir,
            iface: InterfacePart,
        ) -> tuple[
            _T_nested_collection[TestbenchIO],
            ModuleConnector | TestbenchIO,
        ]:
            if isinstance(iface, Interface):
                ntb_dict: dict[str, _T_nested_collection[TestbenchIO]] = {}
                mc_dict: dict[str, HasElaborate] = {}
                for name, member in iface.items():
                    ntb, mod = transform_methods_to_testbenchios(mdir.under(member.mdir), member.iface)
                    ntb_dict[name] = ntb
                    mc_dict[name] = mod
                return ntb_dict, ModuleConnector(**mc_dict)
            elif isinstance(iface, Sequence):
                ntb_list: list[_T_nested_collection[TestbenchIO]] = []
                mod_list: list[HasElaborate] = []
                for elem in iface:
                    ntb, mod = transform_methods_to_testbenchios(mdir, elem)
                    ntb_list.append(ntb)
                    mod_list.append(mod)
                return ntb_list, ModuleConnector(*mod_list)
            elif isinstance(iface, Mapping):
                ntb_dict: dict[str, _T_nested_collection[TestbenchIO]] = {}
                mc_dict: dict[str, HasElaborate] = {}
                for name, elem in iface.items():
                    ntb, mod = transform_methods_to_testbenchios(mdir, elem)
                    ntb_dict[name] = ntb
                    mc_dict[name] = mod
                return ntb_dict, ModuleConnector(**mc_dict)
            else:
                tb = TestbenchIO(mdir_adapter_type(mdir).create(iface))
                return tb, tb

        m = Module()
        m.submodules.dut = self._dut

        iface = interface(self._dut)
        for name, member in iface.items():
            if name in self._exclude:
                continue
            tb_cont, mc = transform_methods_to_testbenchios(member.mdir, member.iface)
            self._io[name] = tb_cont
            m.submodules[name] = mc

        return m

    def debug_signals(self):
        sigs = {"_dut": auto_debug_signals(self._dut)}
        for name, io in self._io.items():
            sigs[name] = auto_debug_signals(io)
        return sigs
