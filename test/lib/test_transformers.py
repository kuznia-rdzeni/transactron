import pytest
import random

from amaranth import *
from transactron import *
from transactron.lib.adapters import Adapter, AdapterTrans
from transactron.lib.transformers import *
from transactron.utils._typing import ModuleLike, MethodStruct, RecordDict
from transactron.utils import ModuleConnector
from transactron.testing import (
    SimpleTestCircuit,
    TestCaseWithSimulator,
    data_layout,
    def_method_mock,
    TestbenchIO,
    TestbenchContext,
)


class MethodMapTestCircuit(Elaboratable):
    def __init__(self, iosize: int, use_methods: bool, use_dicts: bool):
        self.iosize = iosize
        self.use_methods = use_methods
        self.use_dicts = use_dicts

    def elaborate(self, platform):
        m = TModule()

        layout = data_layout(self.iosize)

        def itransform_rec(m: ModuleLike, v: MethodStruct) -> MethodStruct:
            s = Signal.like(v)
            m.d.comb += s.data.eq(v.data + 1)
            return s

        def otransform_rec(m: ModuleLike, v: MethodStruct) -> MethodStruct:
            s = Signal.like(v)
            m.d.comb += s.data.eq(v.data - 1)
            return s

        def itransform_dict(_, v: MethodStruct) -> RecordDict:
            return {"data": v.data + 1}

        def otransform_dict(_, v: MethodStruct) -> RecordDict:
            return {"data": v.data - 1}

        if self.use_dicts:
            itransform = itransform_dict
            otransform = otransform_dict
        else:
            itransform = itransform_rec
            otransform = otransform_rec

        m.submodules.target = self.target = TestbenchIO(Adapter.create(i=layout, o=layout))

        if self.use_methods:
            imeth = Method(i=layout, o=layout)
            ometh = Method(i=layout, o=layout)

            @def_method(m, imeth)
            def _(arg: MethodStruct):
                return itransform(m, arg)

            @def_method(m, ometh)
            def _(arg: MethodStruct):
                return otransform(m, arg)

            trans = MethodMap(self.target.adapter.iface, i_transform=(layout, imeth), o_transform=(layout, ometh))
        else:
            trans = MethodMap(
                self.target.adapter.iface,
                i_transform=(layout, itransform),
                o_transform=(layout, otransform),
            )

        m.submodules.source = self.source = TestbenchIO(AdapterTrans(trans.use(m)))

        return m


class TestMethodMap(TestCaseWithSimulator):
    m: MethodMapTestCircuit

    async def source(self, sim: TestbenchContext):
        for i in range(2**self.m.iosize):
            v = await self.m.source.call(sim, data=i)
            i1 = (i + 1) & ((1 << self.m.iosize) - 1)
            assert v.data == (((i1 << 1) | (i1 >> (self.m.iosize - 1))) - 1) & ((1 << self.m.iosize) - 1)

    @def_method_mock(lambda self: self.m.target)
    def target(self, data):
        return {"data": (data << 1) | (data >> (self.m.iosize - 1))}

    def test_method_transformer(self):
        self.m = MethodMapTestCircuit(4, False, False)
        with self.run_simulation(self.m) as sim:
            sim.add_testbench(self.source)

    def test_method_transformer_dicts(self):
        self.m = MethodMapTestCircuit(4, False, True)
        with self.run_simulation(self.m) as sim:
            sim.add_testbench(self.source)

    def test_method_transformer_with_methods(self):
        self.m = MethodMapTestCircuit(4, True, True)
        with self.run_simulation(self.m) as sim:
            sim.add_testbench(self.source)


class TestMethodFilter(TestCaseWithSimulator):
    def initialize(self):
        self.iosize = 4
        self.layout = data_layout(self.iosize)
        self.target = TestbenchIO(Adapter.create(i=self.layout, o=self.layout))
        self.cmeth = TestbenchIO(Adapter.create(i=self.layout, o=data_layout(1)))

    async def source(self, sim: TestbenchContext):
        for i in range(2**self.iosize):
            v = await self.tc.method.call(sim, data=i)
            if i & 1:
                assert v.data == (i + 1) & ((1 << self.iosize) - 1)
            else:
                assert v.data == 0

    @def_method_mock(lambda self: self.target)
    def target_mock(self, data):
        return {"data": data + 1}

    @def_method_mock(lambda self: self.cmeth)
    def cmeth_mock(self, data):
        return {"data": data % 2}

    def test_method_filter_with_methods(self):
        self.initialize()
        self.tc = SimpleTestCircuit(MethodFilter(self.target.adapter.iface, self.cmeth.adapter.iface))
        m = ModuleConnector(test_circuit=self.tc, target=self.target, cmeth=self.cmeth)
        with self.run_simulation(m) as sim:
            sim.add_testbench(self.source)

    @pytest.mark.parametrize("use_condition", [True, False])
    def test_method_filter_plain(self, use_condition):
        self.initialize()

        def condition(_, v):
            return v.data[0]

        self.tc = SimpleTestCircuit(MethodFilter(self.target.adapter.iface, condition, use_condition=use_condition))
        m = ModuleConnector(test_circuit=self.tc, target=self.target, cmeth=self.cmeth)
        with self.run_simulation(m) as sim:
            sim.add_testbench(self.source)


class MethodProductTestCircuit(Elaboratable):
    def __init__(self, iosize: int, targets: int, add_combiner: bool):
        self.iosize = iosize
        self.targets = targets
        self.add_combiner = add_combiner
        self.target: list[TestbenchIO] = []

    def elaborate(self, platform):
        m = TModule()

        layout = data_layout(self.iosize)

        methods = []

        for k in range(self.targets):
            tgt = TestbenchIO(Adapter.create(i=layout, o=layout))
            methods.append(tgt.adapter.iface)
            self.target.append(tgt)
            m.submodules += tgt

        combiner = None
        if self.add_combiner:
            combiner = (layout, lambda _, vs: {"data": sum(x.data for x in vs)})

        product = MethodProduct(methods, combiner)

        m.submodules.method = self.method = TestbenchIO(AdapterTrans(product.use(m)))

        return m


class TestMethodProduct(TestCaseWithSimulator):
    @pytest.mark.parametrize("targets, add_combiner", [(1, False), (2, False), (5, True)])
    def test_method_product(self, targets: int, add_combiner: bool):
        random.seed(14)

        iosize = 8
        m = MethodProductTestCircuit(iosize, targets, add_combiner)

        method_en = [False] * targets

        def target_process(k: int):
            @def_method_mock(lambda: m.target[k], enable=lambda: method_en[k])
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
        m = MethodTryProductTestCircuit(iosize, targets, add_combiner)

        method_en = [False] * targets

        def target_process(k: int):
            @def_method_mock(lambda: m.target[k], enable=lambda: method_en[k])
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


class MethodTryProductTestCircuit(Elaboratable):
    def __init__(self, iosize: int, targets: int, add_combiner: bool):
        self.iosize = iosize
        self.targets = targets
        self.add_combiner = add_combiner
        self.target: list[TestbenchIO] = []

    def elaborate(self, platform):
        m = TModule()

        layout = data_layout(self.iosize)

        methods = []

        for k in range(self.targets):
            tgt = TestbenchIO(Adapter.create(i=layout, o=layout))
            methods.append(tgt.adapter.iface)
            self.target.append(tgt)
            m.submodules += tgt

        combiner = None
        if self.add_combiner:
            combiner = (layout, lambda _, vs: {"data": sum(Mux(s, r, 0) for (s, r) in vs)})

        product = MethodTryProduct(methods, combiner)

        m.submodules.method = self.method = TestbenchIO(AdapterTrans(product.use(m)))

        return m
