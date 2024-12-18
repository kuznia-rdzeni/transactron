import random
from collections import deque

from amaranth import *
from transactron import *
from transactron.lib.adapters import Adapter
from transactron.lib.reqres import *
from transactron.testing.method_mock import MethodMock
from transactron.utils import ModuleConnector
from transactron.testing import (
    SimpleTestCircuit,
    TestCaseWithSimulator,
    def_method_mock,
    TestbenchIO,
    TestbenchContext,
)


class TestSerializer(TestCaseWithSimulator):
    def setup_method(self):
        self.test_count = 100

        self.port_count = 2
        self.data_width = 5

        self.requestor_rand = 4

        layout = [("field", self.data_width)]

        self.req_method = TestbenchIO(Adapter.create(i=layout))
        self.resp_method = TestbenchIO(Adapter.create(o=layout))

        self.test_circuit = SimpleTestCircuit(
            Serializer(
                port_count=self.port_count,
                serialized_req_method=self.req_method.adapter.iface,
                serialized_resp_method=self.resp_method.adapter.iface,
            ),
        )
        self.m = ModuleConnector(
            test_circuit=self.test_circuit, req_method=self.req_method, resp_method=self.resp_method
        )

        random.seed(14)

        self.serialized_data = deque()
        self.port_data = [deque() for _ in range(self.port_count)]

        self.got_request = False

    @def_method_mock(lambda self: self.req_method, enable=lambda self: not self.got_request)
    def serial_req_mock(self, field):
        @MethodMock.effect
        def eff():
            self.serialized_data.append(field)
            self.got_request = True

    @def_method_mock(lambda self: self.resp_method, enable=lambda self: self.got_request)
    def serial_resp_mock(self):
        @MethodMock.effect
        def eff():
            self.got_request = False

        if self.serialized_data:
            return {"field": self.serialized_data[-1]}

    def requestor(self, i: int):
        async def f(sim: TestbenchContext):
            for _ in range(self.test_count):
                d = random.randrange(2**self.data_width)
                await self.test_circuit.serialize_in[i].call(sim, field=d)
                self.port_data[i].append(d)
                await self.random_wait(sim, self.requestor_rand, min_cycle_cnt=1)

        return f

    def responder(self, i: int):
        async def f(sim: TestbenchContext):
            for _ in range(self.test_count):
                data_out = await self.test_circuit.serialize_out[i].call(sim)
                assert self.port_data[i].popleft() == data_out.field
                await self.random_wait(sim, self.requestor_rand, min_cycle_cnt=1)

        return f

    def test_serial(self):
        with self.run_simulation(self.m) as sim:
            for i in range(self.port_count):
                sim.add_testbench(self.requestor(i))
                sim.add_testbench(self.responder(i))
