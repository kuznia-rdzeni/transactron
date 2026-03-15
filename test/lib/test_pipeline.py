import pytest
from amaranth import *

from transactron import Method, TModule, def_method
from transactron.core.transaction import Transaction
from transactron.lib.pipeline import PipelineBuilder
from transactron.testing import SimpleTestCircuit, TestbenchContext, TestCaseWithSimulator

# ---------------------------------------------------------------------------
# Simple pipeline: write → (+1) → read
# ---------------------------------------------------------------------------


class SimplePipeline(Elaboratable):
    """A one-stage pipeline that adds 1 to its input."""

    def __init__(self):
        self.write = Method(i=[("data", unsigned(8))])
        self.read = Method(o=[("data", unsigned(8))])

    def elaborate(self, platform):
        m = TModule()

        m.submodules.pipeline = p = PipelineBuilder()
        p.add_external(self.write)

        @p.stage(m, o=[("data", unsigned(8))])
        def _(data):
            return {"data": data + 1}

        p.add_external(self.read)

        return m


class TestSimplePipeline(TestCaseWithSimulator):
    def test_pipeline(self):
        m = SimpleTestCircuit(SimplePipeline())

        async def writer(sim: TestbenchContext):
            for i in range(16):
                await m.write.call(sim, data=i)

        async def reader(sim: TestbenchContext):
            for i in range(16):
                result = await m.read.call(sim)
                assert result.data == (i + 1) % 256

        with self.run_simulation(m) as sim:
            sim.add_testbench(writer)
            sim.add_testbench(reader)


# ---------------------------------------------------------------------------
# Multi-stage pipeline: write → (+1) → (+2) → read
# ---------------------------------------------------------------------------


class MultiStagePipeline(Elaboratable):
    """A two-stage pipeline; first adds 1, then adds 2."""

    def __init__(self):
        self.write = Method(i=[("data", unsigned(8))])
        self.read = Method(o=[("data", unsigned(8))])

    def elaborate(self, platform):
        m = TModule()

        m.submodules.pipeline = p = PipelineBuilder()
        p.add_external(self.write)

        @p.stage(m, o=[("data", unsigned(8))])
        def _(data):
            return {"data": data + 1}

        @p.stage(m, o=[("data", unsigned(8))])
        def _(data):
            return {"data": data + 2}

        p.add_external(self.read)

        return m


class TestMultiStagePipeline(TestCaseWithSimulator):
    def test_pipeline(self):
        m = SimpleTestCircuit(MultiStagePipeline())

        async def writer(sim: TestbenchContext):
            for i in range(16):
                await m.write.call(sim, data=i)

        async def reader(sim: TestbenchContext):
            for i in range(16):
                result = await m.read.call(sim)
                assert result.data == (i + 3) % 256

        with self.run_simulation(m) as sim:
            sim.add_testbench(writer)
            sim.add_testbench(reader)


# ---------------------------------------------------------------------------
# Pass-through: pipeline preserves unused signals
# ---------------------------------------------------------------------------


class PassThroughPipeline(Elaboratable):
    """A pipeline where stage 1 adds a field and stage 2 passes it through."""

    def __init__(self):
        self.write = Method(i=[("a", unsigned(8)), ("b", unsigned(8))])
        self.read = Method(o=[("a", unsigned(8)), ("b", unsigned(8)), ("c", unsigned(8))])

    def elaborate(self, platform):
        m = TModule()

        m.submodules.pipeline = p = PipelineBuilder()
        p.add_external(self.write)

        # Stage 1: produce c = a + b; a and b are passed through automatically
        @p.stage(m, o=[("c", unsigned(8))])
        def _(a, b):
            return {"c": a + b}

        stall_counter = Signal(range(100))
        m.d.sync += stall_counter.eq(Mux(stall_counter == 99, 0, stall_counter + 1))

        # Stage 2: no new outputs; a, b, c all pass through, introduces stalling
        @p.stage(m, ready=stall_counter == 0)
        def _():
            pass

        p.add_external(self.read)

        return m


class TestPassThroughPipeline(TestCaseWithSimulator):
    def test_pipeline(self):
        m = SimpleTestCircuit(PassThroughPipeline())

        async def writer(sim: TestbenchContext):
            for i in range(8):
                await m.write.call(sim, a=i, b=i * 2)

        async def reader(sim: TestbenchContext):
            for i in range(8):
                result = await m.read.call(sim)
                assert result.a == i
                assert result.b == (i * 2) % 256
                assert result.c == (i + i * 2) % 256

        with self.run_simulation(m) as sim:
            sim.add_testbench(writer)
            sim.add_testbench(reader)


# ---------------------------------------------------------------------------
# FIFO pipeline: FIFO inserted before a stage
# ---------------------------------------------------------------------------


class FifoPipeline(Elaboratable):
    """Pipeline with a FIFO before the second stage."""

    def __init__(self):
        self.write = Method(i=[("data", unsigned(8))])
        self.read = Method(o=[("data", unsigned(8))])

    def elaborate(self, platform):
        m = TModule()

        m.submodules.pipeline = p = PipelineBuilder()
        p.add_external(self.write)

        @p.stage(m, o=[("data", unsigned(8))])
        def _(data):
            return {"data": data + 1}

        p.fifo(depth=4)

        @p.stage(m, o=[("data", unsigned(8))])
        def _(data):
            return {"data": data + 2}

        p.add_external(self.read)

        return m


class TestFifoPipeline(TestCaseWithSimulator):
    def test_pipeline(self):
        m = SimpleTestCircuit(FifoPipeline())

        async def writer(sim: TestbenchContext):
            for i in range(16):
                await m.write.call(sim, data=i)

        async def reader(sim: TestbenchContext):
            for i in range(16):
                result = await m.read.call(sim)
                assert result.data == (i + 3) % 256

        with self.run_simulation(m) as sim:
            sim.add_testbench(writer)
            sim.add_testbench(reader)


# ---------------------------------------------------------------------------
# call_method: pipeline calls an existing method as a node
# ---------------------------------------------------------------------------


class _Adder(Elaboratable):
    """Submodule that adds a constant to its input."""

    def __init__(self, delta: int):
        self._delta = delta
        self.compute = Method(i=[("data", unsigned(8))], o=[("data", unsigned(8))])

    def elaborate(self, platform):
        m = TModule()

        @def_method(m, self.compute)
        def _(data):
            return {"data": data + self._delta}

        return m


class CallMethodPipeline(Elaboratable):
    """Pipeline that calls sub.compute as a node instead of a manual stage."""

    def __init__(self):
        self.write = Method(i=[("data", unsigned(8))])
        self.read = Method(o=[("data", unsigned(8))])

    def elaborate(self, platform):
        m = TModule()

        m.submodules.adder = adder = _Adder(delta=5)

        m.submodules.pipeline = p = PipelineBuilder()
        p.add_external(self.write)

        @p.stage(m, o=[("data", unsigned(8))])
        def _(data):
            return {"data": data + 1}

        # Call adder.compute using current pipeline fields; its output
        # overwrites 'data' in the pipeline (same field name).
        p.call_method(adder.compute)

        p.add_external(self.read)

        return m


class TestCallMethodPipeline(TestCaseWithSimulator):
    def test_pipeline(self):
        m = SimpleTestCircuit(CallMethodPipeline())

        async def writer(sim: TestbenchContext):
            for i in range(16):
                await m.write.call(sim, data=i)

        async def reader(sim: TestbenchContext):
            for i in range(16):
                # +1 from stage, then +5 from adder.compute
                result = await m.read.call(sim)
                assert result.data == (i + 6) % 256

        with self.run_simulation(m) as sim:
            sim.add_testbench(writer)
            sim.add_testbench(reader)


# ---------------------------------------------------------------------------
# A stage that calls a potentially stalling method
# ---------------------------------------------------------------------------


class DelayNoop(Elaboratable):
    """Stage that does nothing."""

    def __init__(self, delay: int):
        self.write = Method(i=[("data", unsigned(8))])
        self.read = Method(o=[("data", unsigned(8))])
        self.delay = delay

    def elaborate(self, platform):
        m = TModule()

        counter = Signal(range(self.delay + 1))
        buf = Signal(unsigned(8))

        with m.If((counter > 0) & (counter < self.delay)):
            m.d.sync += counter.eq(counter + 1)

        @def_method(m, self.write, ready=counter == 0)
        def _(data):
            m.d.sync += buf.eq(data)
            m.d.sync += counter.eq(1)

        @def_method(m, self.read, ready=counter == self.delay)
        def _():
            m.d.sync += counter.eq(0)
            return {"data": buf}

        return m


class StallingPipeline(Elaboratable):
    """Pipeline with a stalling stage."""

    def __init__(self):
        self.write = Method(i=[("data", unsigned(8))])
        self.read = Method(o=[("data", unsigned(8))])

    def elaborate(self, platform):
        m = TModule()

        m.submodules.pipeline = p = PipelineBuilder()
        m.submodules.delay = delay = DelayNoop(5)
        p.add_external(self.write)

        @p.stage(m, o=[("data", unsigned(8))])
        def _(data):
            delay.write(m, data)
            return {"data": data + 1}

        @p.stage(m, o=[("data", unsigned(8))])
        def _(data):
            return {"data": delay.read(m).data + data}

        p.add_external(self.read)
        return m


class TestStallingPipeline(TestCaseWithSimulator):
    def test_pipeline(self):
        m = SimpleTestCircuit(StallingPipeline())

        async def writer(sim: TestbenchContext):
            for i in range(16):
                await m.write.call(sim, data=i)

        async def reader(sim: TestbenchContext):
            for i in range(16):
                result = await m.read.call(sim)
                assert result.data == (i + i + 1) % 256

        with self.run_simulation(m) as sim:
            sim.add_testbench(writer)
            sim.add_testbench(reader)


# ---------------------------------------------------------------------------
# providing two methods that must be available at the same time
# ---------------------------------------------------------------------------


class DoAnOperation(Elaboratable):
    """Stage that requires both read and write to be available at the same time."""

    def __init__(self, data_in: Method, data_out: Method):
        self.data_in = data_in
        self.data_out = data_out

    def elaborate(self, platform):
        m = TModule()

        with Transaction().body(m):
            self.data_out(m, {"data": self.data_in(m).data + 1})

        return m


class TwoExternalsPipeline(Elaboratable):
    """Pipeline with two external methods that must be available at the same time."""

    def __init__(self):
        self.write = Method(i=[("data", unsigned(8))])
        self.read = Method(o=[("data", unsigned(8))])

    def elaborate(self, platform):
        m = TModule()

        m.submodules.pipeline = p = PipelineBuilder(allow_empty=True)
        p.add_external(self.write)

        op_in = p.create_external(o=[("data", unsigned(8))], i=[])
        op_out = p.create_external(i=[("data", unsigned(8))], o=[], no_dependency=True)
        m.submodules.op = DoAnOperation(op_in, op_out)

        p.add_external(self.read)

        return m


class TestTwoExternalsPipeline(TestCaseWithSimulator):
    def test_pipeline(self):
        m = SimpleTestCircuit(TwoExternalsPipeline())

        async def writer(sim: TestbenchContext):
            for i in range(8):
                await m.write.call(sim, data=i)

        async def reader(sim: TestbenchContext):
            for i in range(8):
                result = await m.read.call(sim)
                assert result.data == (i + 1) % 256

        with self.run_simulation(m) as sim:
            sim.add_testbench(writer)
            sim.add_testbench(reader)


# ---------------------------------------------------------------------------
# Middle add_external: exit method in the middle, pipeline continues
# ---------------------------------------------------------------------------


class MiddleExitPipeline(Elaboratable):
    """Pipeline with a provided method in the middle that returns intermediate results."""

    def __init__(self):
        self.write = Method(i=[("data", unsigned(8))])
        self.peek = Method(o=[("data", unsigned(8))])  # middle exit
        self.read = Method(o=[("data", unsigned(8))])  # final exit

    def elaborate(self, platform):
        m = TModule()

        m.submodules.pipeline = p = PipelineBuilder()
        p.add_external(self.write)

        @p.stage(m, o=[("data", unsigned(8))])
        def _(data):
            return {"data": data + 1}

        # Middle exit: callers can read 'data' here; pipeline also continues.
        p.add_external(self.peek)

        @p.stage(m, o=[("data", unsigned(8))])
        def _(data):
            return {"data": data + 2}

        p.add_external(self.read)

        return m


class TestMiddleExitPipeline(TestCaseWithSimulator):
    def test_pipeline(self):
        """peek observes intermediate values and forwards them; read gets the final result.

        peek is a mandatory synchronisation point: every element must be
        consumed by peek (which also writes it to the next stage) before read
        can return it.
        """
        m = SimpleTestCircuit(MiddleExitPipeline())

        async def writer(sim: TestbenchContext):
            for i in range(8):
                await m.write.call(sim, data=i)

        async def peeker(sim: TestbenchContext):
            for i in range(8):
                result = await m.peek.call(sim)
                # peek sees data after +1 from stage 1
                assert result.data == (i + 1) % 256

        async def reader(sim: TestbenchContext):
            for i in range(8):
                result = await m.read.call(sim)
                # read sees data after +1 (stage 1) and +2 (stage 2)
                assert result.data == (i + 3) % 256

        with self.run_simulation(m) as sim:
            sim.add_testbench(writer)
            sim.add_testbench(peeker)
            sim.add_testbench(reader)


# ---------------------------------------------------------------------------
# Type-validation: mismatched output shape raises ValueError
# ---------------------------------------------------------------------------


class TypeMismatchPipeline(Elaboratable):
    def __init__(self):
        self.write = Method(i=[("data", unsigned(8))])
        self.read = Method(o=[("data", unsigned(8))])

    def elaborate(self, platform):
        m = TModule()

        m.submodules.pipeline = p = PipelineBuilder()
        p.add_external(self.write)

        # Attempt to overwrite 'data' with a different shape
        @p.stage(m, o=[("data", unsigned(16))])
        def _(data):
            return {"data": data}

        p.add_external(self.read)
        return m


class TestTypeValidation(TestCaseWithSimulator):
    def test_shape_mismatch_raises(self):
        with pytest.raises(ValueError, match="not matching"):
            with self.run_simulation(SimpleTestCircuit(TypeMismatchPipeline())):
                pass

    def test_exit_field_missing_raises(self):
        class MissingFieldPipeline(Elaboratable):
            def __init__(self):
                self.read = Method(o=[("missing", unsigned(8))])

            def elaborate(self, platform):
                m = TModule()
                m.submodules.pipeline = p = PipelineBuilder()
                p.add_external(self.read)
                return m

        with pytest.raises(ValueError, match="required but not provided"):
            with self.run_simulation(SimpleTestCircuit(MissingFieldPipeline())):
                pass
