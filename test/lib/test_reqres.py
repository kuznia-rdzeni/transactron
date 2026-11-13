import random
from collections import deque

from amaranth import *
import pytest
from transactron import *
from transactron.lib.reqres import *
from transactron.testing import (
    MethodMock,
    SimpleTestCircuit,
    TestCaseWithSimulator,
    TestbenchContext,
    call_request_response,
    data_layout,
    def_request_response_mock,
)


@pytest.mark.parametrize("port_count", [2, 3])
class TestSerializer(TestCaseWithSimulator):
    @pytest.fixture(scope="function", autouse=True)
    def setup_method(self, port_count: int):
        self.test_count = 100

        self.port_count = port_count
        self.data_width = 5

        self.requestor_rand = 4

        layout = data_layout(self.data_width)

        self.test_circuit = SimpleTestCircuit(
            Serializer(
                port_count=self.port_count,
                req_layout=layout,
                resp_layout=layout,
            ),
        )

        random.seed(14)

        self.port_data = [deque() for _ in range(self.port_count)]

    def requestor(self, i: int):
        async def f(sim: TestbenchContext):
            for _ in range(self.test_count):
                d = random.randrange(2**self.data_width)
                await self.test_circuit.ports[i].request.call(sim, data=d)
                self.port_data[i].append(d)
                await self.random_wait(sim, self.requestor_rand, min_cycle_cnt=1)

        return f

    def responder(self, i: int):
        async def f(sim: TestbenchContext):
            for _ in range(self.test_count):
                data_out = await self.test_circuit.ports[i].response.call(sim)
                assert self.port_data[i].popleft() == data_out.data
                await self.random_wait(sim, self.requestor_rand, min_cycle_cnt=1)

        return f

    @pytest.mark.parametrize("max_pending", [1, None])
    def test_serial(self, port_count: int, max_pending: int | None):
        @def_request_response_mock(self.test_circuit.server, max_pending=max_pending)
        def server(data):
            return {"data": data}

        with self.run_simulation(self.test_circuit) as sim:
            for i in range(port_count):
                sim.add_testbench(self.requestor(i))
                sim.add_testbench(self.responder(i))

    def test_call(self, port_count: int):
        @def_request_response_mock(self.test_circuit.server)
        def server(data):
            return {"data": data}

        async def process(sim: TestbenchContext):
            for _ in range(self.test_count):
                d = random.randrange(2**self.data_width)
                port = self.test_circuit.ports[random.randrange(port_count)]
                assert (await call_request_response(sim, port, data=d)).data == d

        with self.run_simulation(self.test_circuit) as sim:
            sim.add_testbench(process)

    @pytest.mark.parametrize("max_pending", [1, 2, None])
    def test_max_pending(self, port_count: int, max_pending: int | None):
        accepted = 0
        respond = False

        @def_request_response_mock(self.test_circuit.server, max_pending=max_pending, response_enable=lambda: respond)
        def server(data):
            @MethodMock.effect
            def _():
                nonlocal accepted
                accepted += 1

            return {"data": data}

        async def process(sim: TestbenchContext):
            nonlocal respond
            requested = []
            for i in range(port_count):
                if await self.test_circuit.ports[i].request.call_try(sim, data=i) is not None:
                    requested.append(i)
            await self.tick(sim, 3)
            assert accepted == len(requested) == min(port_count, max_pending or port_count)

            respond = True
            for i in requested:
                assert (await self.test_circuit.ports[i].response.call(sim)).data == i

        with self.run_simulation(self.test_circuit) as sim:
            sim.add_testbench(process)

    @pytest.mark.parametrize("compute_on_response", [False, True])
    def test_compute_on_response(self, port_count: int, compute_on_response: bool):
        counter = 0

        @def_request_response_mock(self.test_circuit.server, compute_on_response=compute_on_response)
        def server(data):
            return {"data": counter}

        async def process(sim: TestbenchContext):
            nonlocal counter
            for _ in range(self.test_count):
                port = self.test_circuit.ports[random.randrange(port_count)]
                await port.request.call(sim, data=0)
                before = counter
                counter = (counter + 1) % 2**self.data_width
                await self.random_wait(sim, self.requestor_rand)
                assert (await port.response.call(sim)).data == (counter if compute_on_response else before)

        with self.run_simulation(self.test_circuit) as sim:
            sim.add_testbench(process)
