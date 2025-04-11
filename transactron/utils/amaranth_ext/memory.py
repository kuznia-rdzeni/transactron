from amaranth import *
from amaranth.utils import *
import amaranth.lib.memory as memory
from amaranth.hdl import AlreadyElaborated

from typing import Optional, Any, final
from collections.abc import Iterable

from transactron.utils.amaranth_ext.elaboratables import OneHotMux
from transactron.utils.amaranth_ext.coding import Encoder
from transactron.lib import logging
from transactron.core import TModule

from .. import get_src_loc
from amaranth_types.types import ShapeLike, ValueLike

__all__ = ["MultiReadMemory", "MultiportXORMemory", "MultiportILVTMemory", "MultiportOneHotILVTMemory"]

log = logging.HardwareLogger("memory")


@final
class MultipleWritePorts(Exception):
    """Exception raised when a single write memory is being requested multiple write ports."""


class ReadPort:

    def __init__(
        self,
        memory: "BaseMultiportMemory",
        transparent_for: Iterable[Any] = (),
        src_loc=0,
    ):
        self.src_loc = get_src_loc(src_loc)
        self.transparent_for = transparent_for
        self.en = Signal()
        self.addr = Signal(range(memory.depth))
        self.data = Signal(memory.shape)
        self._memory = memory
        memory.read_ports.append(self)


class WritePort:

    def __init__(
        self,
        memory: "BaseMultiportMemory",
        granularity: Optional[int] = None,
        src_loc=0,
    ):
        self.src_loc = get_src_loc(src_loc)

        shape = memory.shape
        if granularity is None:
            en_width = 1
        elif not isinstance(granularity, int) or granularity <= 0:
            raise TypeError(f"Granularity must be a positive integer or None, " f"not {granularity!r}")
        elif shape.signed:
            raise ValueError("Granularity cannot be specified for a memory with a signed shape")
        elif shape.width % granularity != 0:
            raise ValueError("Granularity must evenly divide data width")
        else:
            en_width = shape.width // granularity

        self.en = Signal(en_width)
        self.addr = Signal(range(memory.depth))
        self.data = Signal(shape)
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

        self.shape = Shape.cast(shape)
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
            # for each read port a new single port memory block is generated
            mem = memory.Memory(
                shape=self.shape, depth=self.depth, init=self.init, attrs=self.attrs, src_loc_at=self.src_loc
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
    Write port granularity is not yet supported.

    """

    def write_port(self, *, domain: str = "sync", granularity: Optional[int] = None, src_loc_at: int = 0):
        if granularity is not None:
            raise ValueError("Granularity is not supported.")
        return super().write_port(domain=domain, granularity=granularity, src_loc_at=src_loc_at)

    def elaborate(self, platform):
        m = TModule()

        self._frozen = True

        write_xors = [Value.cast(0) for _ in self.write_ports]
        read_xors = [Value.cast(0) for _ in self.read_ports]

        write_regs_addr = [Signal(range(self.depth)) for _ in self.write_ports]
        write_regs_data = [Signal(self.shape) for _ in self.write_ports]
        read_en_bypass = [Signal() for _ in self.read_ports]

        # feedback ports
        for index, write_port in enumerate(self.write_ports):
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

        # real read ports
        for index, write_port in enumerate(self.write_ports):
            write_xor = Signal(self.shape)
            m.d.comb += [write_xor.eq(write_xors[index])]

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

            write_addr_bypass = Signal(range(self.depth))
            write_data_bypass = Signal(self.shape)
            write_en_bypass = Signal()
            m.d.sync += [
                write_addr_bypass.eq(write_regs_addr[index]),
                write_data_bypass.eq(write_xor),
                write_en_bypass.eq(r_write_port.en),
            ]

            for idx, port in enumerate(r_read_ports):
                read_addr_bypass = Signal(range(self.depth))

                m.d.sync += [
                    read_addr_bypass.eq(self.read_ports[idx].addr),
                    read_en_bypass[idx].eq(self.read_ports[idx].en),
                ]

                double_stage_bypass = Mux(
                    (read_addr_bypass == write_addr_bypass) & read_en_bypass[idx] & write_en_bypass,
                    write_data_bypass,
                    port.data,
                )

                if write_port in self.read_ports[idx].transparent_for:
                    read_xors[idx] ^= Mux(
                        (read_addr_bypass == write_regs_addr[index]) & r_write_port.en,
                        write_xor,
                        double_stage_bypass,
                    )
                else:
                    read_xors[idx] ^= double_stage_bypass

                m.d.comb += [port.addr.eq(self.read_ports[idx].addr), port.en.eq(self.read_ports[idx].en)]

        for index, port in enumerate(self.read_ports):
            sync_data = Signal.like(port.data)
            m.d.sync += sync_data.eq(port.data)
            m.d.comb += [port.data.eq(Mux(read_en_bypass[index], read_xors[index], sync_data))]

        return m


class MultiportILVTMemory(BaseMultiportMemory):
    """Multiport memory based on Invalidation Live Value Table.

    Multiple read and write ports can be requested. Memory is built of
    number of write ports memory blocks with multiple read and multi-ported Invalidation Live Value Table.
    ILVT is a XOR based memory that returns the number of the memory bank in which the current value is stored.
    Width of data stored in ILVT is the binary logarithm of the number of write ports.
    Writing two different values to the same memory address in one cycle has undefined behavior.

    """

    def elaborate(self, platform):
        m = TModule()

        self._frozen = True

        m.submodules.ilvt = ilvt = MultiportXORMemory(
            shape=bits_for(len(self.write_ports) - 1),
            depth=self.depth,
            init=self.init,
            src_loc_at=self.src_loc + 1,  # ten init jest źle
        )

        ilvt_write_ports = [ilvt.write_port() for _ in self.write_ports]
        ilvt_read_ports = [ilvt.read_port() for _ in self.read_ports]

        write_addr_bypass = [Signal(port.addr.shape()) for port in self.write_ports]
        write_data_bypass = [Signal(self.shape) for _ in self.write_ports]
        write_en_bypass = [Signal(port.en.shape()) for port in self.write_ports]

        m.d.sync += [write_addr_bypass[index].eq(port.addr) for index, port in enumerate(self.write_ports)]
        m.d.sync += [write_data_bypass[index].eq(port.data) for index, port in enumerate(self.write_ports)]
        m.d.sync += [write_en_bypass[index].eq(port.en) for index, port in enumerate(self.write_ports)]

        for index, write_port in enumerate(ilvt_write_ports):
            # address a is marked as last changed in bank k (k gets stored at a in ILVT)
            # when any part of value at address a gets overwritten by port k
            m.d.comb += [
                write_port.addr.eq(self.write_ports[index].addr),
                write_port.en.eq(self.write_ports[index].en.any()),
                write_port.data.eq(index),
            ]

            mem = MultiReadMemory(
                shape=self.shape, depth=self.depth, init=self.init, attrs=self.attrs, src_loc_at=self.src_loc
            )
            mem_name = f"bank_{index}"
            m.submodules[mem_name] = mem
            bank_write_port = mem.write_port(granularity=self.write_ports[index].granularity)
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

            read_en_bypass = Signal()
            read_addr_bypass = Signal(self.shape)

            m.d.sync += [read_en_bypass.eq(read_port.en), read_addr_bypass.eq(read_port.addr)]

            bank_data = Signal(self.shape)
            with m.Switch(ilvt_read_ports[index].data):
                for value in range(len(self.write_ports)):
                    with m.Case(value):
                        m.d.comb += [bank_data.eq(m.submodules[f"bank_{value}"].read_ports[index].data)]

            mux_inputs = [
                ((write_addr_bypass[idx] == read_addr_bypass) & write_en_bypass[idx], write_data_bypass[idx])
                for idx, write_port in enumerate(self.write_ports)
                if write_port in read_port.transparent_for
            ]
            new_data = OneHotMux.create(m, mux_inputs, bank_data)

            sync_data = Signal.like(read_port.data)
            m.d.sync += sync_data.eq(read_port.data)
            m.d.comb += [read_port.data.eq(Mux(read_en_bypass, new_data, sync_data))]

        return m


class OneHotCodedILVT(BaseMultiportMemory):
    """One-hot-coded Invalidation Live Value Table.

    ILVT returns one-hot-coded ID of the memory bank which stores the current value.
    No external data is written to ILVT, it stores only feedback data from
    other banks. Correct data is recovered by checking the mutual exclusion condition.

    """

    # ona nie do końca udostępnia interfejs pamięci, ale czy aż tak bardzo nie?
    # nie pozwala na zapisywanie danych zewnętrznych, ale porty odczytu normalnie produkują dane,
    # a porty zapisu pozwalają na wpięcie addr i enable czyli prawie wszystko, hmmm
    # kwestia też, żeby nikt nie pomyślał że to zwykła pamięć, do której można coś zapisywać
    def elaborate(self, platform):
        m = TModule()

        self._frozen = True

        write_addr_sync = [Signal(port.addr.shape()) for port in self.write_ports]  # ta szerokość musi być ustalona
        write_en_sync = [
            Signal() for _ in self.write_ports
        ]  # nie no, bez przesady, en musi być jednobitowy, rzucę cośtam jeśli nie jest
        write_data_sync = [
            Signal(self.shape) for _ in self.write_ports
        ]  # to się nazwya sync, ale będzie zapisywane kombinacyjnie

        write_addr_bypass = [Signal(port.addr.shape()) for port in self.write_ports]  # ta szerokość musi być ustalona
        write_en_bypass = [Signal() for _ in self.write_ports]
        write_data_bypass = [Signal(self.shape) for _ in self.write_ports]

        read_addr_bypass = [Signal(port.addr.shape()) for port in self.read_ports]
        read_en_bypass = [Signal() for _ in self.read_ports]

        bypassed_data = [[Signal(self.shape) for _ in self.read_ports] for _ in self.write_ports]

        for index, write_port in enumerate(self.write_ports):
            mem = MultiReadMemory(
                # pytanie, czy chcę jej podawać ilość write portów, czy wezmę sobie pewną jak już będzie,
                # czy rzucać jakiś wyjątek jeśli te dwie rzeczy się nie zgadzają (brzmi nie najgorzej, ale nadmiarowo)
                # a tak wgl to one są o bit krótsze niż wyjście
                shape=len(self.write_ports) - 1,
                depth=self.depth,
                init=self.init,
                attrs=self.attrs,
                src_loc_at=self.src_loc,  # tu też trzeba przemyśleć init
            )
            mem_name = f"bank_{index}"
            m.submodules[mem_name] = mem
            bank_write_port = mem.write_port()
            bank_read_ports = [
                mem.read_port(transparent_for=[bank_write_port])
                for _ in range(len(self.read_ports) + len(self.write_ports) - 1)
            ]

            m.d.sync += [
                write_addr_sync[index].eq(write_port.addr),
                bank_write_port.addr.eq(
                    write_port.addr
                ),  # (write_addr_sync[index]),  # tutaj jakaś niepewność czy dodatkowy rejestr czy nie
                write_addr_bypass[index].eq(write_addr_sync[index]),
                write_en_sync[index].eq(write_port.en),
                bank_write_port.en.eq(write_port.en),  # (write_en_sync[index]),
                write_en_bypass[index].eq(write_en_sync[index]),
            ]

            log.debug(
                m,
                True,
                "port: {}, w-enable: {}, w-enable_sync: {}, w-enable_bypass: {}",
                index,
                write_port.en,
                write_en_sync[index],
                write_en_bypass[index],
            )
            log.debug(
                m,
                True,
                "port: {}, w-addr: {:x}, w-addr_sync: {:x}, w-addr_bypass: {:x}",
                index,
                write_port.addr,
                write_addr_sync[index],
                write_addr_bypass[index],
            )

            first_feedback_port = len(self.read_ports)
            for idx in range(first_feedback_port):
                m.d.sync += [
                    read_addr_bypass[idx].eq(self.read_ports[idx].addr),
                    read_en_bypass[idx].eq(self.read_ports[idx].en),
                ]
                m.d.comb += [
                    bank_read_ports[idx].en.eq(self.read_ports[idx].en),
                    bank_read_ports[idx].addr.eq(self.read_ports[idx].addr),
                ]
            for idx in range(first_feedback_port, len(bank_read_ports)):
                # mogę tutaj podpiąć odpowiednie adresy i enable
                # ja potrzebuję tylko 1 BITU
                # może mogę jakoś tak zorganizować tę pamięć, żeby faktycznie czytać tylko 1 bit
                # ale wydaje mi się, że nie mogę, granularność jest tylko przy zapisie, a nie przy odczycie
                # tak czy siak to tylko kwestia już odczytanych danych, na razie tylko podpinam wejścia portu
                i = idx - first_feedback_port
                k = i + 1 if index < i + 1 else i
                m.d.comb += [
                    bank_read_ports[idx].en.eq(1),  # być może może być stale 1 / self.write_ports[k].en
                    bank_read_ports[idx].addr.eq(self.write_ports[k].addr),
                ]
                log.debug(m, True, "bank: {}, r-port: {}, r-addr: {:x}", index, idx, bank_read_ports[idx].addr)

        # tutaj będę podpinać data, wczesniej nie mogłam, bo nie istniały pozostałe banki i ich porty
        for index in range(len(self.write_ports)):
            mem_name = f"bank_{index}"
            mem = m.submodules[mem_name]

            first_feedback_port = len(self.read_ports)
            idx = index + first_feedback_port
            # tu chyba nie potrezba dodatkowego opóźnienia
            bit_selection = [
                (
                    ~(m.submodules[f"bank_{i}"].read_ports[idx - 1].data[index - 1])
                    if i < index
                    else m.submodules[f"bank_{i+1}"].read_ports[idx].data[index]
                )
                for i in range(len(self.write_ports) - 1)
            ]
            m.d.comb += [
                write_data_sync[index].eq(Cat(*bit_selection)),
                mem.write_ports[0].data.eq(write_data_sync[index]),
            ]
            m.d.sync += write_data_bypass[index].eq(write_data_sync[index])

            log.debug(m, True, "w-port: {}, write_data_sync: {:x}", index, write_data_sync[index])
            log.debug(m, True, "w-port: {}, write_data_bypass: {:x}", index, write_data_bypass[index])

            for idx in range(len(self.read_ports)):
                double_stage_bypass = Mux(
                    (read_addr_bypass[idx] == write_addr_bypass[index]) & read_en_bypass[idx] & write_en_bypass[index],
                    write_data_bypass[index],
                    mem.read_ports[idx].data,
                )
                log.debug(
                    m,
                    True,
                    "bank: {}, r-port: {}, double_stage cond: {}",
                    index,
                    idx,
                    (read_addr_bypass[idx] == write_addr_bypass[index]) & read_en_bypass[idx] & write_en_bypass[index],
                )
                log.debug(m, True, "bank: {}, r-port: {}, double_stage_data: {:x}", index, idx, double_stage_bypass)

                m.d.comb += bypassed_data[index][idx].eq(
                    Mux(
                        (read_addr_bypass[idx] == write_addr_sync[index]) & write_en_sync[index],
                        write_data_sync[index],
                        double_stage_bypass,
                    )
                )
                log.debug(
                    m,
                    True,
                    "bank: {}, r-port: {}, bypassed final cond: {}",
                    index,
                    idx,
                    (read_addr_bypass[idx] == write_addr_sync[index]) & write_en_sync[index],
                )
                log.debug(m, True, "bank: {}, r-port: {}, final_data(?): {:x}", index, idx, bypassed_data[index][idx])

        for index, read_port in enumerate(self.read_ports):
            # może potem zrobię sama te xory, na razie wystarczą komparatory
            # xors = [Signal() for _ in range(len(self.write_ports))] # chyba (bo ilość wp to długość słowa)

            exclusive_bits = [
                [
                    (~(bypassed_data[i][index][idx - 1]) if i < idx else bypassed_data[i + 1][index][idx])
                    for i in range(len(self.write_ports) - 1)
                ]
                for idx in range(len(self.write_ports))
            ]
            one_hot = [Cat(*exclusive_bits[idx]) == bypassed_data[idx][index] for idx in range(len(self.write_ports))]

            log.debug(
                m, True, "r-port: {}, r-addr: {:x}, r-addr_bypass: {:x}", index, read_port.addr, read_addr_bypass[index]
            )
            log.debug(m, True, "r-port: {:x}, final_data: {}", index, Cat(*one_hot))

            m.d.comb += read_port.data.eq(Cat(*one_hot))

        return m


class MultiportOneHotILVTMemory(BaseMultiportMemory):
    """Multiport memory based on Invalidation Live Value Table."""

    def elaborate(self, platform):
        m = Module()

        self._frozen = True

        m.submodules.ilvt = ilvt = OneHotCodedILVT(
            shape=len(self.write_ports),
            depth=self.depth,
            init=self.init,
            src_loc_at=self.src_loc + 1,  # ten init jest źle
        )

        ilvt_write_ports = [ilvt.write_port() for _ in self.write_ports]
        ilvt_read_ports = [ilvt.read_port() for _ in self.read_ports]

        # dummy = Signal()
        # m.d.comb += dummy.eq(1)
        # log.debug(m, True, "dummy: {}", dummy)

        write_addr_bypass = [Signal(port.addr.shape()) for port in self.write_ports]
        write_data_bypass = [Signal(self.shape) for _ in self.write_ports]
        write_en_bypass = [Signal(port.en.shape()) for port in self.write_ports]

        m.d.sync += [write_addr_bypass[index].eq(port.addr) for index, port in enumerate(self.write_ports)]
        m.d.sync += [write_data_bypass[index].eq(port.data) for index, port in enumerate(self.write_ports)]
        m.d.sync += [write_en_bypass[index].eq(port.en) for index, port in enumerate(self.write_ports)]

        for index, write_port in enumerate(ilvt_write_ports):
            # address a is marked as last changed in bank k (k gets stored at a in ILVT)
            # when any part of value at address a gets overwritten by port k
            m.d.comb += [
                write_port.addr.eq(self.write_ports[index].addr),
                write_port.en.eq(self.write_ports[index].en.any()),
                # write_port.data.eq(index),
            ]
            # log.debug(m, True, "external enable: {}", write_port.en)
            # log.debug(m, True, "write address: {:x}", write_port.addr)

            mem = MultiReadMemory(
                shape=self.shape, depth=self.depth, init=self.init, attrs=self.attrs, src_loc_at=self.src_loc
            )
            mem_name = f"bank_{index}"
            m.submodules[mem_name] = mem
            bank_write_port = mem.write_port(granularity=self.write_ports[index].granularity)
            bank_read_ports = [mem.read_port() for _ in self.read_ports]

            m.d.comb += [
                bank_write_port.addr.eq(self.write_ports[index].addr),
                bank_write_port.en.eq(self.write_ports[index].en),
                bank_write_port.data.eq(self.write_ports[index].data),
            ]
            # log.debug(m, True, "bank write address: {:x}", bank_write_port.addr)
            for idx, port in enumerate(bank_read_ports):
                m.d.comb += [
                    port.en.eq(self.read_ports[idx].en),
                    port.addr.eq(self.read_ports[idx].addr),
                ]

        for index, read_port in enumerate(self.read_ports):
            m.d.comb += [ilvt_read_ports[index].addr.eq(read_port.addr), ilvt_read_ports[index].en.eq(read_port.en)]

            read_en_bypass = Signal()
            read_addr_bypass = Signal(self.shape)

            m.d.sync += [read_en_bypass.eq(read_port.en), read_addr_bypass.eq(read_port.addr)]

            bank_data = Signal(self.shape)
            encoder_name = f"encoder_{index}"
            m.submodules[encoder_name] = encoder = Encoder(width=len(self.write_ports))
            m.d.comb += encoder.i.eq(ilvt_read_ports[index].data)
            with m.Switch(encoder.o):
                for value in range(len(self.write_ports)):
                    with m.Case(value):
                        m.d.comb += [bank_data.eq(m.submodules[f"bank_{value}"].read_ports[index].data)]

            mux_inputs = [
                ((write_addr_bypass[idx] == read_addr_bypass) & write_en_bypass[idx], write_data_bypass[idx])
                for idx, write_port in enumerate(self.write_ports)
                if write_port in read_port.transparent_for
            ]
            new_data = OneHotMux.create(m, mux_inputs, bank_data)

            sync_data = Signal.like(read_port.data)
            m.d.sync += sync_data.eq(read_port.data)
            m.d.comb += [read_port.data.eq(Mux(read_en_bypass, new_data, sync_data))]

        return m
