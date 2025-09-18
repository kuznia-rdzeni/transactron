import pytest
import random

from amaranth import *
from amaranth_types import ModuleLike
from transactron import *
from transactron.lib.adapters import Adapter, AdapterTrans
from transactron.lib.transformers import *
from transactron.testing.testbenchio import CallTrigger
from transactron.utils.typing import MethodStruct, RecordDict
from transactron.utils import ModuleConnector
from transactron.testing import (
    SimpleTestCircuit,
    TestCaseWithSimulator,
    data_layout,
    def_method_mock,
    TestbenchIO,
    TestbenchContext,
)


class TestMethodMap(TestCaseWithSimulator):
    @pytest.fixture(autouse=True)
    def initialize(self):
        self.iosize = 4
        self.layout = data_layout(self.iosize)

    async def source(self, sim: TestbenchContext):
        for i in range(2**self.iosize):
            v = await self.m.method.call(sim, data=i)
            i1 = (i + 1) & ((1 << self.iosize) - 1)
            assert v.data == (((i1 << 1) | (i1 >> (self.iosize - 1))) - 1) & ((1 << self.iosize) - 1)

    @def_method_mock(lambda self: self.m.target)
    def target(self, data):
        return {"data": (data << 1) | (data >> (self.iosize - 1))}

    def test_method_transformer(self):
        def itransform(m: ModuleLike, v: MethodStruct) -> MethodStruct:
            s = Signal.like(v)
            m.d.comb += s.data.eq(v.data + 1)
            return s

        def otransform(m: ModuleLike, v: MethodStruct) -> MethodStruct:
            s = Signal.like(v)
            m.d.comb += s.data.eq(v.data - 1)
            return s

        tr = MethodMap(
            self.layout, self.layout, i_transform=(self.layout, itransform), o_transform=(self.layout, otransform)
        )
        self.m = SimpleTestCircuit(tr)

        with self.run_simulation(self.m) as sim:
            sim.add_testbench(self.source)

    def test_method_transformer_dicts(self):
        def itransform(_, v: MethodStruct) -> RecordDict:
            return {"data": v.data + 1}

        def otransform(_, v: MethodStruct) -> RecordDict:
            return {"data": v.data - 1}

        tr = MethodMap(
            self.layout, self.layout, i_transform=(self.layout, itransform), o_transform=(self.layout, otransform)
        )
        self.m = SimpleTestCircuit(tr)

        with self.run_simulation(self.m) as sim:
            sim.add_testbench(self.source)

    def test_method_transformer_methods(self):
        imeth = TestbenchIO(Adapter(i=self.layout, o=self.layout))
        ometh = TestbenchIO(Adapter(i=self.layout, o=self.layout))

        @def_method_mock(lambda: imeth)
        def imeth_mock(data):
            return {"data": data + 1}

        @def_method_mock(lambda: ometh)
        def ometh_mock(data):
            return {"data": data - 1}

        itransform = imeth.adapter.iface
        otransform = ometh.adapter.iface

        tr = MethodMap(
            self.layout, self.layout, i_transform=(self.layout, itransform), o_transform=(self.layout, otransform)
        )
        self.m = SimpleTestCircuit(tr)

        with self.run_simulation(ModuleConnector(self.m, imeth, ometh)) as sim:
            sim.add_testbench(self.source)


class TestMethodFilter(TestCaseWithSimulator):
    @pytest.fixture(autouse=True)
    def initialize(self):
        self.iosize = 4
        self.layout = data_layout(self.iosize)

    async def source(self, sim: TestbenchContext):
        for i in range(2**self.iosize):
            v = await self.tc.method.call(sim, data=i)
            if i & 1:
                assert v.data == (i + 1) & ((1 << self.iosize) - 1)
            else:
                assert v.data == 0

    @def_method_mock(lambda self: self.tc.target)
    def target_mock(self, data):
        return {"data": data + 1}

    def test_method_filter_with_methods(self):
        cmeth = TestbenchIO(Adapter(i=self.layout, o=data_layout(1)))

        @def_method_mock(lambda: cmeth)
        def cmeth_mock(data):
            return {"data": data % 2}

        self.tc = SimpleTestCircuit(MethodFilter(self.layout, self.layout, cmeth.adapter.iface))

        m = ModuleConnector(test_circuit=self.tc, cmeth=cmeth)
        with self.run_simulation(m) as sim:
            sim.add_testbench(self.source)

    @pytest.mark.parametrize("use_condition", [True, False])
    def test_method_filter_plain(self, use_condition: bool):
        def condition(_, v):
            return v.data[0]

        self.tc = SimpleTestCircuit(MethodFilter(self.layout, self.layout, condition, use_condition=use_condition))
        with self.run_simulation(self.tc) as sim:
            sim.add_testbench(self.source)


class TestMethodProduct(TestCaseWithSimulator):
    @pytest.mark.parametrize("targets, add_combiner", [(1, False), (2, False), (5, True)])
    def test_method_product(self, targets: int, add_combiner: bool):
        random.seed(14)

        iosize = 8
        layout = data_layout(iosize)
        combiner = None
        if add_combiner:
            combiner = (layout, lambda _, vs: {"data": sum(x.data for x in vs)})

        m = SimpleTestCircuit(MethodProduct(layout, (layout,) * targets, combiner))

        method_en = [False] * targets

        def target_process(k: int):
            @def_method_mock(lambda: m.targets[k], enable=lambda: method_en[k])
            def mock(data):
                return {"data": data + k}

            return mock()

        async def method_process(sim: TestbenchContext):
            # if any of the target methods is not enabled, call does not succeed
            for i in range(2**targets - 1):
                for k in range(targets):
                    method_en[k] = bool(i & (1 << k))

                await sim.tick()
                assert (await m.method.call_try(sim, data=0)) is None

            # otherwise, the call succeeds
            for k in range(targets):
                method_en[k] = True
            await sim.tick()

            data = random.randint(0, (1 << iosize) - 1)
            val = (await m.method.call(sim, data=data)).data
            if add_combiner:
                assert val == (targets * data + (targets - 1) * targets // 2) & ((1 << iosize) - 1)
            else:
                assert val == data

        with self.run_simulation(m) as sim:
            sim.add_testbench(method_process)
            for k in range(targets):
                self.add_mock(sim, target_process(k))


class TestMethodTryProduct(TestCaseWithSimulator):
    @pytest.mark.parametrize("targets, add_combiner", [(1, False), (2, False), (5, True)])
    def test_method_try_product(self, targets: int, add_combiner: bool):
        random.seed(14)

        iosize = 8
        layout = data_layout(iosize)
        combiner = None
        if add_combiner:
            combiner = (layout, lambda _, vs: {"data": sum(Mux(s, r, 0) for (s, r) in vs)})

        m = SimpleTestCircuit(MethodTryProduct(layout, (layout,) * targets, combiner))

        method_en = [False] * targets

        def target_process(k: int):
            @def_method_mock(lambda: m.targets[k], enable=lambda: method_en[k])
            def mock(data):
                return {"data": data + k}

            return mock()

        async def method_process(sim: TestbenchContext):
            for i in range(2**targets):
                for k in range(targets):
                    method_en[k] = bool(i & (1 << k))

                active_targets = sum(method_en)

                await sim.tick()

                data = random.randint(0, (1 << iosize) - 1)
                val = await m.method.call(sim, data=data)
                if add_combiner:
                    adds = sum(k * method_en[k] for k in range(targets))
                    assert val.data == (active_targets * data + adds) & ((1 << iosize) - 1)
                else:
                    assert val.shape().size == 0

        with self.run_simulation(m) as sim:
            sim.add_testbench(method_process)
            for k in range(targets):
                self.add_mock(sim, target_process(k))


class NonexclusiveWrapperTestCircuit(Elaboratable):
    def __init__(self, iosize: int, wrappers: int, callers: int):
        self.iosize = iosize
        self.wrappers = wrappers
        self.callers = callers
        self.sources: list[list[TestbenchIO]] = []

    def elaborate(self, platform):
        m = TModule()

        layout = data_layout(self.iosize)

        m.submodules.target = self.target = TestbenchIO(Adapter(i=layout, o=layout))

        for i in range(self.wrappers):
            nonex = NonexclusiveWrapper.create(self.target.adapter.iface).use(m)
            sources = []
            self.sources.append(sources)

            for j in range(self.callers):
                m.submodules[f"source_{i}_{j}"] = source = TestbenchIO(AdapterTrans.create(nonex))
                sources.append(source)

        return m


class TestNonexclusiveWrapper(TestCaseWithSimulator):
    def test_nonexclusive_wrapper(self):
        iosize = 4
        wrappers = 2
        callers = 2
        iterations = 100
        m = NonexclusiveWrapperTestCircuit(iosize, wrappers, callers)

        def caller_process(i: int):
            async def process(sim: TestbenchContext):
                for _ in range(iterations):
                    j = random.randrange(callers)
                    data = random.randrange(2**iosize)
                    ret = await m.sources[i][j].call(sim, data=data)
                    assert ret.data == (data + 1) % (2**iosize)
                    await self.random_wait_geom(sim, 0.5)

            return process

        @def_method_mock(lambda: m.target)
        def target(data):
            return {"data": data + 1}

        with self.run_simulation(m) as sim:
            self.add_mock(sim, target())
            for i in range(wrappers):
                sim.add_testbench(caller_process(i))

    def test_no_conflict(self):
        m = NonexclusiveWrapperTestCircuit(1, 1, 2)

        async def process(sim: TestbenchContext):
            res1, res2 = await CallTrigger(sim).call(m.sources[0][0], data=1).call(m.sources[0][1], data=2).until_done()
            assert res1 is not None and res2 is not None  # there was no conflict, however the result is undefined

        @def_method_mock(lambda: m.target)
        def target(data):
            return {"data": data + 1}

        with self.run_simulation(m) as sim:
            sim.add_testbench(process)
