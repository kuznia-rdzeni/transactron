from amaranth import *
from amaranth.utils import *
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

        if self._write_ports:
            write_port = self._write_ports[0]
            for port in self._read_ports:
                if port is None:
                    raise ValueError("Found None in read ports")
                # for each read port a new single port memory block is generated
                mem = memory.Memory(shape=port.width, depth=self.depth, init=self.init)
                m.submodules += mem
                physical_read_port = mem.read_port(transparent_for=port.transparent_for)
                physical_write_port = mem.write_port()
                print("joł\n")
                m.d.comb += [physical_read_port.addr.eq(port.addr),
                             port.data.eq(physical_read_port.data),
                             physical_read_port.en.eq(port.en),
                             physical_write_port.addr.eq(write_port.addr),
                             physical_write_port.data.eq(write_port.data),
                             physical_write_port.en.eq(write_port.en)]
        
        return m


class ReadPort:

    #póki co ignoruję domenę, nie wiem co potem
    def __init__(self, memory, width, depth, init=None, transparent_for=None, src_loc = 0):
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
        memory._read_ports.append(self)
        
class WritePort:

    #póki co ignoruję domenę i granularity, nie wiem co potem
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