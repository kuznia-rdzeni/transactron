from amaranth import *
from amaranth.utils import *
import amaranth.lib.memory as memory
from amaranth.hdl import AlreadyElaborated

from typing import Optional, Any, final
from collections.abc import Iterable

from .. import get_src_loc
from amaranth_types.types import ShapeLike, ValueLike

__all__ = ["MultiReadMemory", "MultiportXORMemory", "MultiportILVTMemory"]


@final
class MultipleWritePorts(Exception):
    """Exception raised when a single write memory is being requested multiple write ports."""


class ReadPort:

    def __init__(
        self,
        memory,
        width: ShapeLike,
        depth: int,
        init: Iterable[ValueLike] = (),
        transparent_for: Iterable[Any] = (),
        src_loc=0,
    ):
        self.src_loc = get_src_loc(src_loc)
        self.depth = depth
        self.width = width
        self.init = init
        self.transparent_for = transparent_for
        self.addr_width = bits_for(self.depth - 1)
        self.en = Signal(1)
        self.addr = Signal(self.addr_width)
        self.data = Signal(width)
        self._memory = memory
        memory.read_ports.append(self)


class WritePort:

    def __init__(
        self,
        memory,
        width: ShapeLike,
        depth: int,
        init: Iterable[ValueLike] = (),
        granularity: Optional[int] = None,
        src_loc=0,
    ):
        self.src_loc = get_src_loc(src_loc)
        self.depth = depth
        self.width = width
        self.init = init
        self.addr_width = bits_for(self.depth - 1)
        self.en = Signal(1)
        self.addr = Signal(self.addr_width)
        self.data = Signal(width)
        self.granularity = granularity
        self._memory = memory
        memory.write_ports.append(self)


class BaseMultiportMemory(Elaboratable):
    def __init__(
        self,
        *,
        shape: ShapeLike,
        depth: int,
        init: Iterable[ValueLike],
        attrs: Optional[dict[str, str]] = None,
        src_loc_at: int = 0,
    ):
        """
        Parameters
        ----------
        shape: ShapeLike
            Shape of each memory row.
        depth : int
            Number of memory rows.
        init : iterable of initial values
            Initial values for memory rows.
        src_loc: int
            How many stack frames deep the source location is taken from.
        """

        self.shape = shape
        self.depth = depth
        self.init = init
        self.attrs = attrs
        self.src_loc = src_loc_at

        self.read_ports: "list[ReadPort]" = []
        self.write_ports: "list[WritePort]" = []
        self._frozen = False

    def read_port(self, *, domain: str = "sync", transparent_for: Iterable[Any] = (), src_loc_at: int = 0):
        if self._frozen:
            raise AlreadyElaborated("Cannot add a memory port to a memory that has already been elaborated")
        if domain != "sync":
            raise ValueError("Invalid port domain: Only synchronous memory ports supported.")
        return ReadPort(
            memory=self,
            width=self.shape,
            depth=self.depth,
            init=self.init,
            transparent_for=transparent_for,
            src_loc=1 + src_loc_at,
        )

    def write_port(self, *, domain: str = "sync", granularity: Optional[int] = None, src_loc_at: int = 0):
        if self._frozen:
            raise AlreadyElaborated("Cannot add a memory port to a memory that has already been elaborated")
        if domain != "sync":
            raise ValueError("Invalid port domain: Only synchronous memory ports supported.")
        return WritePort(
            memory=self,
            width=self.shape,
            depth=self.depth,
            init=self.init,
            granularity=granularity,
            src_loc=1 + src_loc_at,
        )


class MultiReadMemory(BaseMultiportMemory):
    """Memory with one write and multiple read ports.

    One can request multiple read ports and not more than 1 write port. Module internally
    uses multiple (number of read ports) instances of amaranth.lib.memory.Memory with one
    read and one write port.

    """

    def write_port(self, *, domain: str = "sync", granularity: Optional[int] = None, src_loc_at: int = 0):
        if self.write_ports:
            raise MultipleWritePorts("Cannot add multiple write ports to a single write memory")
        return super().write_port(domain=domain, granularity=granularity, src_loc_at=src_loc_at)

    def elaborate(self, platform):
        m = Module()

        self._frozen = True

        write_port = self.write_ports[0] if self.write_ports else None
        for port in self.read_ports:
            if port is None:
                raise ValueError("Found None in read ports")
            # for each read port a new single port memory block is generated
            mem = memory.Memory(
                shape=port.width, depth=self.depth, init=self.init, attrs=self.attrs, src_loc_at=self.src_loc
            )
            m.submodules += mem
            physical_write_port = mem.write_port(granularity=write_port.granularity) if write_port else None
            physical_read_port = mem.read_port(
                transparent_for=(
                    [physical_write_port] if physical_write_port and write_port in port.transparent_for else []
                )
            )
            m.d.comb += [
                physical_read_port.addr.eq(port.addr),
                port.data.eq(physical_read_port.data),
                physical_read_port.en.eq(port.en),
            ]

            if physical_write_port and write_port:
                m.d.comb += [
                    physical_write_port.addr.eq(write_port.addr),
                    physical_write_port.data.eq(write_port.data),
                    physical_write_port.en.eq(write_port.en),
                ]

        return m


class MultiportXORMemory(BaseMultiportMemory):
    """Multiport memory based on xor.

    Multiple read and write ports can be requested. Memory is built of
    (number of write ports) * (number of write ports - 1 + number of read ports) single port
    memory blocks. XOR is used to enable writing multiple values in one cycle and reading correct values.
    Writing two different values to the same memory address in one cycle has undefined behavior.

    """

    def elaborate(self, platform):
        m = Module()

        self._frozen = True

        addr_width = bits_for(self.depth - 1)

        write_xors: "list[Value]" = [Signal(self.shape) for _ in self.write_ports]
        read_xors: "list[Value]" = [Signal(self.shape) for _ in self.read_ports]

        write_regs_addr = [Signal(addr_width) for _ in self.write_ports]
        write_regs_data = [Signal(self.shape) for _ in self.write_ports]
        read_en_bypass = [Signal() for _ in self.read_ports]

        for index, write_port in enumerate(self.write_ports):
            if write_port is None:
                raise ValueError("Found None in write ports")

            m.d.sync += [write_regs_data[index].eq(write_port.data), write_regs_addr[index].eq(write_port.addr)]
            write_xors[index] ^= write_regs_data[index]
            for i in range(len(self.write_ports) - 1):
                mem = memory.Memory(
                    shape=self.shape, depth=self.depth, init=[], attrs=self.attrs, src_loc_at=self.src_loc
                )
                mem_name = f"memory_{index}_{i}"
                m.submodules[mem_name] = mem
                physical_write_port = mem.write_port()
                physical_read_port = mem.read_port(transparent_for=[physical_write_port])

                idx = i + 1 if i >= index else i
                write_xors[idx] ^= physical_read_port.data

                m.d.comb += [physical_read_port.en.eq(1), physical_read_port.addr.eq(self.write_ports[idx].addr)]

                m.d.sync += [
                    physical_write_port.en.eq(write_port.en),
                    physical_write_port.addr.eq(write_port.addr),
                ]

        for index, write_port in enumerate(self.write_ports):
            write_xor = write_xors[index]
            for i in range(len(self.write_ports) - 1):
                mem_name = f"memory_{index}_{i}"
                mem = m.submodules[mem_name]
                physical_write_port = mem.write_ports[0]

                m.d.comb += [physical_write_port.data.eq(write_xor)]

            init = self.init if index == 0 else []
            read_block = MultiReadMemory(
                shape=self.shape, depth=self.depth, init=init, attrs=self.attrs, src_loc_at=self.src_loc
            )
            mem_name = f"read_block_{index}"
            m.submodules[mem_name] = read_block
            r_write_port = read_block.write_port()
            r_read_ports = [read_block.read_port() for _ in self.read_ports]
            m.d.comb += [r_write_port.data.eq(write_xor)]

            m.d.sync += [r_write_port.addr.eq(write_port.addr), r_write_port.en.eq(write_port.en)]

            write_addr_bypass = Signal(addr_width)
            write_data_bypass = Signal(self.shape)
            write_en_bypass = Signal()
            m.d.sync += [
                write_addr_bypass.eq(write_regs_addr[index]),
                write_data_bypass.eq(write_xor),
                write_en_bypass.eq(r_write_port.en),
            ]

            for idx, port in enumerate(r_read_ports):
                read_addr_bypass = Signal(addr_width)

                m.d.sync += [
                    read_addr_bypass.eq(self.read_ports[idx].addr),
                    read_en_bypass[idx].eq(self.read_ports[idx].en),
                ]

                single_stage_bypass = Mux(
                    (read_addr_bypass == write_addr_bypass) & read_en_bypass[idx] & write_en_bypass,
                    write_data_bypass,
                    port.data,
                )

                if write_port in self.read_ports[idx].transparent_for:
                    read_xors[idx] ^= Mux(
                        (read_addr_bypass == write_regs_addr[index]) & r_write_port.en,
                        write_xor,
                        single_stage_bypass,
                    )
                else:
                    read_xors[idx] ^= single_stage_bypass

                m.d.comb += [port.addr.eq(self.read_ports[idx].addr), port.en.eq(self.read_ports[idx].en)]

        for index, port in enumerate(self.read_ports):
            m.d.comb += [port.data.eq(Mux(read_en_bypass[index], read_xors[index], port.data))]

        return m


class MultiportILVTMemory(BaseMultiportMemory):
    def elaborate(self, platform):
        m = Module()

        self._frozen = True

        m.submodules.ilvt = ilvt = MultiportXORMemory(
            shape=bits_for(len(self.write_ports) - 1), depth=self.depth, init=self.init, src_loc_at=self.src_loc + 1
        )

        ilvt_write_ports = [ilvt.write_port(granularity=port.granularity) for port in self.write_ports]
        ilvt_read_ports = [ilvt.read_port() for _ in self.read_ports]

        for index, write_port in enumerate(ilvt_write_ports):
            m.d.comb += [
                write_port.addr.eq(self.write_ports[index].addr),
                write_port.en.eq(self.write_ports[index].en),
                write_port.data.eq(index),
            ]

            mem = MultiReadMemory(
                shape=self.shape, depth=self.depth, init=self.init, attrs=self.attrs, src_loc_at=self.src_loc
            )
            mem_name = f"bank_{index}"
            m.submodules[mem_name] = mem
            bank_write_port = mem.write_port(granularity=write_port.granularity)
            bank_read_ports = [mem.read_port() for _ in self.read_ports]

            m.d.comb += [
                bank_write_port.addr.eq(self.write_ports[index].addr),
                bank_write_port.en.eq(self.write_ports[index].en),
                bank_write_port.data.eq(self.write_ports[index].data),
            ]
            for idx, port in enumerate(bank_read_ports):
                m.d.comb += [
                    port.en.eq(self.read_ports[idx].en),
                    port.addr.eq(self.read_ports[idx].addr),
                ]

        for index, read_port in enumerate(self.read_ports):
            m.d.comb += [ilvt_read_ports[index].addr.eq(read_port.addr), ilvt_read_ports[index].en.eq(read_port.en)]
            with m.Switch(ilvt_read_ports[index].data):
                for value in range(len(self.write_ports)):
                    with m.Case(value):
                        m.d.comb += [read_port.data.eq(m.submodules[f"bank_{value}"].read_ports[index].data)]
        return m
