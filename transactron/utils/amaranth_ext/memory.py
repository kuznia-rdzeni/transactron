from amaranth import *
from amaranth.utils import *
import amaranth.lib.wiring as wiring
import amaranth.lib.memory as memory
import amaranth_types.memory as amemory
from amaranth.hdl import AlreadyElaborated

from typing import Optional, Any, final
from collections.abc import Iterable

from .. import get_src_loc
from amaranth_types.types import ShapeLike, ValueLike

__all__ = ["MultiReadMemory"]

@final
class MultipleWritePorts(Exception):
    """Exception raised when a single write memory is being requested multiple write ports."""

class MultiReadMemory(Elaboratable):

    def __init__(
        self,
        *,
        shape: ShapeLike,
        depth: int,
        init: Iterable[ValueLike],
        attrs: Optional[dict[str, str]] = None,
        src_loc_at: int = 0
    ):
        self.shape = shape
        self.depth = depth
        self.init = init

        self._read_ports: "list[ReadPort]" = []
        self._write_ports: "list[WritePort]" = []
        self._frozen = False
    
    def read_port(
        self,
        *,
        domain: str = "sync",
        transparent_for: Iterable[Any] = (),
        src_loc_at: int = 0
    ) :
        if self._frozen:
            raise AlreadyElaborated("Cannot add a memory port to a memory that has already been elaborated")
        return ReadPort(memory=self, width=self.shape, depth=self.depth, init=self.init, transparent_for=transparent_for, src_loc=1 + src_loc_at)

    def write_port(
        self,
        *,
        domain: str = "sync",
        granularity: Optional[int] = None,
        src_loc_at: int = 0
    ) :
        if self._frozen:
            raise AlreadyElaborated("Cannot add a memory port to a memory that has already been elaborated")
        if self._write_ports:
            raise MultipleWritePorts("Cannot add multiple write ports to a single write memory")
        return WritePort(memory=self, width=self.shape, depth=self.depth, init=self.init, granularity=granularity, src_loc=1+src_loc_at)

    def elaborate(self, platform) :
        m = Module()

        self._frozen = True
        print(self._read_ports)
        # dla każdego read portu wyelaboruje się blok pamięci
        # write port będzie miał tylko 3 sygnały, które chcemy podczepić pod wszystkie przewody write bloków z read_ports
        # nie wiem czy nie muszę dodać read_ports jako podmoduły
        if self._write_ports:
            write_port = self._write_ports[0]
            for port in self._read_ports:
                m.d.comb += [port.write_en.eq(write_port.en),
                             port.write_data.eq(write_port.data),
                             port.write_addr.eq(write_port.addr)]


class ReadPort(Elaboratable):

    #póki co ignoruję domenę, nie wiem co potem
    def __init__(self, memory, width, depth, init, transparent_for, src_loc = 0):
        # to się dzieje w elaborate
        # self._mem_block = memory.Memory(shape=width, depth=depth, init=init)
        # self._read_port = self._mem_block.read_port(transparent_for=transparent_for)
        # self._write_port = self._mem_block.write_port()
        self.src_loc = get_src_loc(src_loc)
        self.depth = depth
        self.width = width
        self.init = init
        self.transparent_for = transparent_for
        self.addr_width = bits_for(self.depth - 1)
        self.en = Signal(1)
        self.addr = Signal(self.addr_width)
        self.data = Signal(width)
        self.write_en = Signal(1)
        self.write_addr = Signal(self.addr_width)
        self.write_data = Signal(width)
        self._memory = memory
        memory._read_ports.append(self)

    def elaborate(self, platform):
        m = Module()

        # read port instantiates memory block - increases memory blocks used 
        m.submodules.mem = self.mem = mem = memory.Memory(shape=self.width, depth=self.depth, init=self.init)
        read_port = mem.read_port(transparent_for=self.transparent_for)
        write_port = mem.write_port()

        # write and read ports of the memory block are binded with external ReadPort signals
        m.d.comb += [self.en.eq(read_port.en),
                     self.addr.eq(read_port.addr),
                     self.data.eq(read_port.data)]
        m.d.comb += [self.write_en.eq(write_port.en),
                     self.write_addr.eq(write_port.addr),
                     self.write_data.eq(write_port.data)]
        
class WritePort:

    #póki co ignoruję domenęi granularity, nie wiem co potem
    def __init__(self, memory, width, depth, init, granularity=None, src_loc = 0):
        self.src_loc = get_src_loc(src_loc)
        self.depth = depth
        self.width = width
        self.init = init
        self.addr_width = bits_for(self.depth - 1)
        self.en = Signal(1)
        self.addr = Signal(self.addr_width)
        self.data = Signal(width)
        self._memory = memory
        memory._write_ports.append(self)