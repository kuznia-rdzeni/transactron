from amaranth import *
from collections.abc import Callable
from amaranth_types import ShapeLike
import pytest
import random
from collections import deque
from datetime import timedelta
from dataclasses import dataclass, field
from hypothesis import example, given, settings, Phase
from hypothesis import strategies as st
import amaranth.lib.memory as memory
import amaranth_types.memory as amemory
from transactron.testing import *
from transactron.testing.input_generation import amaranth_structs, generate_input
from transactron.lib.storage import *
from transactron.utils.amaranth_ext.memory import MultiportXORMemory, MultiportXORILVTMemory, MultiportOneHotILVTMemory
from transactron.utils.transactron_helpers import make_layout
from transactron.utils.data_repr import data_layout


class TestContentAddressableMemory(TestCaseWithSimulator):
    addr_width = 4
    content_width = 5
    test_number = 30
    max_nop = 3
    addr_layout = data_layout(addr_width)
    content_layout = data_layout(content_width)

    def set_up(self):
        self.entries_count = 8

        self.circ = SimpleTestCircuit(
            ContentAddressableMemory(self.addr_layout, self.content_layout, self.entries_count)
        )

        self.memory = {}

    def generic_process(
        self,
        method,
        input_lst,
        behaviour_check=None,
        state_change=None,
        input_verification=None,
        settle_count=0,
        name="",
    ):
        async def f(sim: TestbenchContext):
            while input_lst:
                # wait till all processes will end the previous cycle
                await sim.delay(1e-9)
                elem = input_lst.pop()
                if elem is None:
                    await sim.tick()
                    continue
                if input_verification is not None and not input_verification(elem):
                    await sim.tick()
                    continue
                response = await method.call(sim, **elem)
                await sim.delay(settle_count * 1e-9)
                if behaviour_check is not None:
                    behaviour_check(elem, response)
                if state_change is not None:
                    state_change(elem, response)
                await sim.tick()

        return f

    def push_process(self, in_push):
        def verify_in(elem):
            return frozenset(elem["addr"].items()) not in self.memory

        def modify_state(elem, response):
            self.memory[frozenset(elem["addr"].items())] = elem["data"]

        return self.generic_process(
            self.circ.push,
            in_push,
            state_change=modify_state,
            input_verification=verify_in,
            settle_count=3,
            name="push",
        )

    def read_process(self, in_read):
        def check(elem, response):
            addr = elem["addr"]
            frozen_addr = frozenset(addr.items())
            if frozen_addr in self.memory:
                assert response.not_found == 0
                assert data_const_to_dict(response.data) == self.memory[frozen_addr]
            else:
                assert response.not_found == 1

        return self.generic_process(self.circ.read, in_read, behaviour_check=check, settle_count=0, name="read")

    def remove_process(self, in_remove):
        def modify_state(elem, response):
            if frozenset(elem["addr"].items()) in self.memory:
                del self.memory[frozenset(elem["addr"].items())]

        p = self.generic_process(self.circ.remove, in_remove, state_change=modify_state, settle_count=2, name="remv")

        async def process(sim: TestbenchContext):
            await p(sim)

            # The input generator doesn't guarantee that every data gets freed, which leads to lockups.
            # This hack cleans the memory, allowing tests to complete.
            while True:
                await sim.delay(5e-9)
                while not self.memory:
                    await sim.tick()
                    await sim.delay(5e-9)
                addr = next(iter(self.memory.keys()))
                await self.circ.remove.call(sim, addr=dict(addr))
                # Update the model only after the removal takes effect, like in generic_process,
                # so that reads and writes in the same cycle still see the entry.
                await sim.delay(10e-9)
                del self.memory[addr]

        return process

    def write_process(self, in_write):
        def verify_in(elem):
            ret = frozenset(elem["addr"].items()) in self.memory
            return ret

        def check(elem, response):
            assert response.not_found == int(frozenset(elem["addr"].items()) not in self.memory)

        def modify_state(elem, response):
            if frozenset(elem["addr"].items()) in self.memory:
                self.memory[frozenset(elem["addr"].items())] = elem["data"]

        return self.generic_process(
            self.circ.write,
            in_write,
            behaviour_check=check,
            state_change=modify_state,
            input_verification=None,
            settle_count=1,
            name="writ",
        )

    @settings(
        max_examples=10,
        phases=(Phase.explicit, Phase.reuse, Phase.generate, Phase.shrink),
        deadline=timedelta(milliseconds=2000),
    )
    @given(
        generate_input(test_number, amaranth_structs({"addr": addr_layout, "data": content_layout}), max_nones=max_nop),
        generate_input(test_number, amaranth_structs({"addr": addr_layout, "data": content_layout}), max_nones=max_nop),
        generate_input(test_number, amaranth_structs({"addr": addr_layout}), max_nones=max_nop),
        generate_input(test_number, amaranth_structs({"addr": addr_layout}), max_nones=max_nop),
    )
    def test_random(self, in_push, in_write, in_read, in_remove):
        self.set_up()
        with self.run_simulation(self.circ, max_cycles=500) as sim:
            sim.add_testbench(self.push_process(in_push))
            sim.add_testbench(self.read_process(in_read))
            sim.add_testbench(self.write_process(in_write))
            sim.add_testbench(self.remove_process(in_remove), background=True)


@dataclass(frozen=True)
class BankShape:
    shape: ShapeLike
    to_shape: Callable = field(repr=False)
    from_shape: Callable = field(repr=False)


bank_shapes = [
    BankShape(6, lambda x: x, lambda x: x),
    BankShape(make_layout(("data_field", 6)), lambda x: {"data_field": x}, lambda x: x["data_field"]),
]


@dataclass(frozen=True)
class MemoryBankConf:
    max_addr: int
    writer_rand: int
    reader_req_rand: int
    reader_resp_rand: int
    seed: int
    transparent: bool
    read_on_resp: bool
    read_ports: int
    write_ports: int
    shape: BankShape


@st.composite
def memory_bank_confs(draw: st.DrawFn) -> MemoryBankConf:
    write_ports = draw(st.integers(1, 4))
    return MemoryBankConf(
        # writers lock addresses, so there must be at least as many addresses as write ports
        max_addr=draw(st.integers(write_ports, 20)),
        writer_rand=draw(st.integers(0, 3)),
        reader_req_rand=draw(st.integers(0, 3)),
        reader_resp_rand=draw(st.integers(0, 3)),
        seed=draw(st.integers(0, 2**32 - 1)),
        transparent=draw(st.booleans()),
        read_on_resp=draw(st.booleans()),
        read_ports=draw(st.integers(1, 4)),
        write_ports=write_ports,
        shape=draw(st.sampled_from(bank_shapes)),
    )


class TestMemoryBank(TestCaseWithSimulator):
    @pytest.mark.parametrize(
        "memory_type",
        [memory.Memory, MultiportXORMemory, MultiportXORILVTMemory, MultiportOneHotILVTMemory],
    )
    @settings(max_examples=20, deadline=None)
    @given(memory_bank_confs())
    # pipelined operation, checked against a strict cycle bound
    @example(MemoryBankConf(9, 0, 0, 0, 18, False, False, 1, 1, bank_shapes[0]))
    @example(MemoryBankConf(9, 0, 0, 0, 18, True, True, 4, 4, bank_shapes[1]))
    def test_mem(
        self,
        memory_type: amemory.AbstractMemoryConstructor[ShapeLike, Value],
        conf: MemoryBankConf,
    ):
        test_count = 200

        max_addr = conf.max_addr
        transparent = conf.transparent
        read_on_resp = conf.read_on_resp
        read_ports = conf.read_ports
        write_ports = conf.write_ports
        shape = conf.shape.shape
        to_shape = conf.shape.to_shape
        from_shape = conf.shape.from_shape
        writer_rand = conf.writer_rand
        reader_req_rand = conf.reader_req_rand
        reader_resp_rand = conf.reader_resp_rand

        m = SimpleTestCircuit(
            MemoryBank(
                shape=shape,
                depth=max_addr,
                transparent=transparent,
                read_on_resp=read_on_resp,
                read_ports=read_ports,
                write_ports=write_ports,
                memory_type=memory_type,
            ),
        )

        data: list[int] = [0 for _ in range(max_addr)]
        read_req_queues = [deque() for _ in range(read_ports)]
        address_lock = [False] * max_addr

        random.seed(conf.seed)

        def writer(i):
            async def process(sim: TestbenchContext):
                for cycle in range(test_count):
                    d = random.randrange(2 ** Shape.cast(shape).width)
                    a = random.randrange(max_addr)

                    # one address shouldn't be written by multiple ports at the same time
                    while address_lock[a]:
                        a = random.randrange(max_addr)
                    address_lock[a] = True

                    await m.write[i].call(sim, data=to_shape(d), addr=a)

                    await sim.delay(1e-9 * (i + 2 if not transparent else i))
                    data[a] = d

                    address_lock[a] = False

                    await self.random_wait(sim, writer_rand)

            return process

        def reader_req(i):
            async def process(sim: TestbenchContext):
                for cycle in range(test_count):
                    a = random.randrange(max_addr)
                    await m.read[i].request.call(sim, addr=a)
                    await sim.delay(1e-9 * (1 if not transparent else write_ports + 2))
                    if read_on_resp:
                        read_req_queues[i].append(a)
                    else:
                        read_req_queues[i].append(data[a])
                    await self.random_wait(sim, reader_req_rand)

            return process

        def reader_resp(i):
            async def process(sim: TestbenchContext):
                for cycle in range(test_count):
                    await sim.delay(1e-9 * (write_ports + 3))
                    while not read_req_queues[i]:
                        await self.random_wait(sim, reader_resp_rand or 1, min_cycle_cnt=1)
                        await sim.delay(1e-9 * (write_ports + 3))
                    if read_on_resp:
                        if not transparent:
                            d = data[read_req_queues[i].popleft()]
                    else:
                        d = read_req_queues[i].popleft()
                    rd = from_shape((await m.read[i].response.call(sim)).data)
                    await sim.delay(1e-9 * (write_ports + 3))
                    if read_on_resp and transparent:
                        d = data[read_req_queues[i].popleft()]
                    assert rd == d  # type: ignore
                    await self.random_wait(sim, reader_resp_rand)

            return process

        pipeline_test = writer_rand == 0 and reader_req_rand == 0 and reader_resp_rand == 0
        max_cycles = test_count + 2 if pipeline_test else 100000

        with self.run_simulation(m, max_cycles=max_cycles) as sim:
            for i in range(read_ports):
                sim.add_testbench(reader_req(i))
                sim.add_testbench(reader_resp(i))
            for i in range(write_ports):
                sim.add_testbench(writer(i))


@dataclass(frozen=True)
class AsyncMemoryBankConf:
    max_addr: int
    writer_rand: int
    reader_rand: int
    seed: int
    read_ports: int
    write_ports: int
    shape: BankShape


@st.composite
def async_memory_bank_confs(draw: st.DrawFn) -> AsyncMemoryBankConf:
    return AsyncMemoryBankConf(
        max_addr=draw(st.integers(1, 20)),
        writer_rand=draw(st.integers(1, 3)),
        reader_rand=draw(st.integers(1, 3)),
        seed=draw(st.integers(0, 2**32 - 1)),
        read_ports=draw(st.integers(1, 2)),
        write_ports=draw(st.integers(1, 2)),
        shape=draw(st.sampled_from(bank_shapes)),
    )


class TestAsyncMemoryBank(TestCaseWithSimulator):
    @settings(max_examples=20, deadline=None)
    @given(async_memory_bank_confs())
    def test_mem(self, conf: AsyncMemoryBankConf):
        max_addr = conf.max_addr
        read_ports = conf.read_ports
        write_ports = conf.write_ports
        shape = conf.shape.shape
        to_shape = conf.shape.to_shape
        from_shape = conf.shape.from_shape
        writer_rand = conf.writer_rand
        reader_rand = conf.reader_rand

        test_count = 200

        m = SimpleTestCircuit(
            AsyncMemoryBank(
                shape=shape,
                depth=max_addr,
                read_ports=read_ports,
                write_ports=write_ports,
            ),
        )

        data: list[int] = [0 for i in range(max_addr)]

        random.seed(conf.seed)

        def writer(i):
            async def process(sim: TestbenchContext):
                for cycle in range(test_count):
                    d = random.randrange(2 ** Shape.cast(shape).width)
                    a = random.randrange(max_addr)
                    await m.write[i].call(sim, data=to_shape(d), addr=a)
                    await sim.delay(1e-9 * (i + 2))
                    data[a] = d
                    await self.random_wait(sim, writer_rand, min_cycle_cnt=1)

            return process

        def reader(i):
            async def process(sim: TestbenchContext):
                for cycle in range(test_count):
                    a = random.randrange(max_addr)
                    d = await m.read[i].call(sim, addr=a)
                    await sim.delay(1e-9)
                    expected_d = data[a]
                    assert from_shape(d.data) == expected_d
                    await self.random_wait(sim, reader_rand, min_cycle_cnt=1)

            return process

        with self.run_simulation(m) as sim:
            for i in range(read_ports):
                sim.add_testbench(reader(i))
            for i in range(write_ports):
                sim.add_testbench(writer(i))
