from collections.abc import Callable
from amaranth_types import ShapeLike
import pytest
import random
from collections import deque
from datetime import timedelta
from hypothesis import given, settings, Phase
import amaranth.lib.memory as memory
import amaranth_types.memory as amemory
from transactron.testing import *
from transactron.lib.storage import *
from transactron.utils.amaranth_ext.memory import MultiportXORMemory, MultiportILVTMemory
from transactron.utils.transactron_helpers import make_layout


class TestContentAddressableMemory(TestCaseWithSimulator):
    addr_width = 4
    content_width = 5
    test_number = 30
    nop_number = 3
    addr_layout = data_layout(addr_width)
    content_layout = data_layout(content_width)

    def setUp(self):
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
                if isinstance(elem, OpNOP):
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
            return not (frozenset(elem["addr"].items()) in self.memory)

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

        return self.generic_process(self.circ.remove, in_remove, state_change=modify_state, settle_count=2, name="remv")

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
        derandomize=True,
        deadline=timedelta(milliseconds=500),
    )
    @given(
        generate_process_input(test_number, nop_number, [("addr", addr_layout), ("data", content_layout)]),
        generate_process_input(test_number, nop_number, [("addr", addr_layout), ("data", content_layout)]),
        generate_process_input(test_number, nop_number, [("addr", addr_layout)]),
        generate_process_input(test_number, nop_number, [("addr", addr_layout)]),
    )
    def test_random(self, in_push, in_write, in_read, in_remove):
        with self.reinitialize_fixtures():
            self.setUp()
            with self.run_simulation(self.circ, max_cycles=500) as sim:
                sim.add_testbench(self.push_process(in_push))
                sim.add_testbench(self.read_process(in_read))
                sim.add_testbench(self.write_process(in_write))
                sim.add_testbench(self.remove_process(in_remove))


bank_shapes = [
    (6, lambda x: x, lambda x: x),
    (make_layout(("data_field", 6)), lambda x: {"data_field": x}, lambda x: x["data_field"]),
]


class TestMemoryBank(TestCaseWithSimulator):
    test_conf = [(9, 3, 3, 3, 14), (16, 1, 1, 3, 15), (16, 1, 1, 1, 16), (12, 3, 1, 1, 17), (9, 0, 0, 0, 18)]

    @pytest.mark.parametrize("max_addr, writer_rand, reader_req_rand, reader_resp_rand, seed", test_conf)
    @pytest.mark.parametrize("transparent", [False, True])
    @pytest.mark.parametrize("read_ports", [1, 2])
    @pytest.mark.parametrize("write_ports", [1, 2])
    @pytest.mark.parametrize("memory_type", [memory.Memory, MultiportXORMemory, MultiportILVTMemory])
    @pytest.mark.parametrize("shape,to_shape,from_shape", bank_shapes)
    def test_mem(
        self,
        max_addr: int,
        writer_rand: int,
        reader_req_rand: int,
        reader_resp_rand: int,
        seed: int,
        transparent: bool,
        read_ports: int,
        write_ports: int,
        memory_type: amemory.AbstractMemoryConstructor[ShapeLike, Value],
        shape: ShapeLike,
        to_shape: Callable,
        from_shape: Callable,
    ):
        test_count = 200

        m = SimpleTestCircuit(
            MemoryBank(
                shape=shape,
                depth=max_addr,
                transparent=transparent,
                read_ports=read_ports,
                write_ports=write_ports,
                memory_type=memory_type,
            ),
        )

        data: list[int] = [0 for _ in range(max_addr)]
        read_req_queues = [deque() for _ in range(read_ports)]
        address_lock = [False] * max_addr

        random.seed(seed)

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
                    await m.read_req[i].call(sim, addr=a)
                    await sim.delay(1e-9 * (1 if not transparent else write_ports + 2))
                    d = data[a]
                    read_req_queues[i].append(d)
                    await self.random_wait(sim, reader_req_rand)

            return process

        def reader_resp(i):
            async def process(sim: TestbenchContext):
                for cycle in range(test_count):
                    await sim.delay(1e-9 * (write_ports + 3))
                    while not read_req_queues[i]:
                        await self.random_wait(sim, reader_resp_rand or 1, min_cycle_cnt=1)
                        await sim.delay(1e-9 * (write_ports + 3))
                    d = read_req_queues[i].popleft()
                    assert from_shape((await m.read_resp[i].call(sim)).data) == d
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


class TestAsyncMemoryBank(TestCaseWithSimulator):
    @pytest.mark.parametrize(
        "max_addr, writer_rand, reader_rand, seed", [(9, 3, 3, 14), (16, 1, 1, 15), (16, 1, 1, 16), (12, 3, 1, 17)]
    )
    @pytest.mark.parametrize("read_ports", [1, 2])
    @pytest.mark.parametrize("write_ports", [1, 2])
    @pytest.mark.parametrize("shape,to_shape,from_shape", bank_shapes)
    def test_mem(
        self,
        max_addr: int,
        writer_rand: int,
        reader_rand: int,
        seed: int,
        read_ports: int,
        write_ports: int,
        shape: ShapeLike,
        to_shape: Callable,
        from_shape: Callable,
    ):
        test_count = 200

        m = SimpleTestCircuit(
            AsyncMemoryBank(
                shape=shape,
                depth=max_addr,
                read_ports=read_ports,
                write_ports=write_ports,
            ),
        )

        data: list[int] = list(0 for i in range(max_addr))

        random.seed(seed)

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
