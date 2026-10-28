from collections.abc import Iterable, Mapping, Sequence
from typing import Any
from amaranth import *
from amaranth.sim import *
from transactron.lib.adapters import Adapter, AdapterTrans

from .testbenchio import TestbenchIO
from transactron.core import interface_members, InterfaceMembers, InterfacePart, MethodDir
from transactron.utils import ModuleConnector, auto_debug_signals, NestedCollection
from amaranth_types import HasElaborate


__all__ = ["SimpleTestCircuit"]


def mdir_adapter_type(mdir: MethodDir) -> type[AdapterTrans] | type[Adapter]:
    match mdir:
        case MethodDir.PROVIDED:
            return AdapterTrans
        case MethodDir.REQUIRED:
            return Adapter


def transform_methods_to_testbenchios(
    mdir: MethodDir,
    iface: InterfacePart,
) -> tuple[
    NestedCollection[TestbenchIO],
    ModuleConnector | TestbenchIO,
]:
    if isinstance(iface, InterfaceMembers):
        ntb_dict: dict[str, NestedCollection[TestbenchIO]] = {}
        mc_dict: dict[str, HasElaborate] = {}
        for name, member in iface.items():
            ntb, mod = transform_methods_to_testbenchios(mdir.under(member.mdir), member.iface)
            ntb_dict[name] = ntb
            mc_dict[name] = mod
        return ntb_dict, ModuleConnector(**mc_dict)
    elif isinstance(iface, Sequence):
        ntb_list: list[NestedCollection[TestbenchIO]] = []
        mod_list: list[HasElaborate] = []
        for elem in iface:
            ntb, mod = transform_methods_to_testbenchios(mdir, elem)
            ntb_list.append(ntb)
            mod_list.append(mod)
        return ntb_list, ModuleConnector(*mod_list)
    elif isinstance(iface, Mapping):
        ntb_dict: dict[str, NestedCollection[TestbenchIO]] = {}
        mc_dict: dict[str, HasElaborate] = {}
        for name, elem in iface.items():
            ntb, mod = transform_methods_to_testbenchios(mdir, elem)
            ntb_dict[name] = ntb
            mc_dict[name] = mod
        return ntb_dict, ModuleConnector(**mc_dict)
    else:
        tb = TestbenchIO(mdir_adapter_type(mdir).create(iface))
        return tb, tb


class SimpleTestCircuit[T: HasElaborate](Elaboratable):
    def __init__(self, dut: T, *, exclude: Iterable[str] = ()):
        self._dut = dut
        self._io: dict[str, NestedCollection[TestbenchIO]] = {}
        self._submodules: dict[str, HasElaborate] = {}
        self._exclude = set(exclude)

        iface = interface_members(self._dut)
        for name, member in iface.items():
            if name in self._exclude:
                continue
            tb_cont, mc = transform_methods_to_testbenchios(member.mdir, member.iface)
            self._io[name] = tb_cont
            self._submodules[name] = mc

    def __getattr__(self, name: str) -> Any:
        try:
            return self._io[name]
        except KeyError:
            raise AttributeError(f"No mock for '{name}'")

    def elaborate(self, platform):
        m = Module()

        m.submodules.dut = self._dut
        for name, mc in self._submodules.items():
            m.submodules[name] = mc

        return m

    def debug_signals(self):
        sigs = {"_dut": auto_debug_signals(self._dut)}
        for name, io in self._io.items():
            sigs[name] = auto_debug_signals(io)
        return sigs
