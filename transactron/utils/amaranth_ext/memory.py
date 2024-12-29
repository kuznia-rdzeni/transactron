from amaranth import *
from amaranth.utils import *
import amaranth.lib.memory as memory
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
    """Memory with one write and multiple read ports.

    One can request multiple read ports and not more than 1 read port. Module internally
    uses multiple (number of read ports) instances of amaranth.lib.memory.Memory with one 
    read and one write port.

    Attributes
    ----------
    shape: ShapeLike
        Shape of each memory row.
    depth: int
        Number of memory rows.
    """

    def __init__(
        self,
        *,
        shape: ShapeLike,
        depth: int,
        init: Iterable[ValueLike],
        attrs: Optional[dict[str, str]] = None,
        src_loc_at: int = 0
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

        self._shape = shape
        self._depth = depth
        self._init = init
        self._attrs = attrs
        self.src_loc = src_loc_at

        self._read_ports: "list[ReadPort]" = []
        self._write_ports: "list[WritePort]" = []
        self._frozen = False

    @property
    def shape(self):
        return self._shape

    @property
    def depth(self):
        return self._depth

    @property
    def init(self):
        return self._init

    @init.setter
    def init(self, init):
        self._init = init

    @property
    def attrs(self):
        return self._attrs
    
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
                mem = memory.Memory(shape=port.width, depth=self.depth, init=self.init, attrs=self.attrs, src_loc_at=self.src_loc)
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