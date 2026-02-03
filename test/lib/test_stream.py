import random
from amaranth import *
from amaranth.lib import stream, wiring
from amaranth.lib.wiring import In, Out
from amaranth.lib.data import StructLayout

from transactron import *
from transactron.lib.stream import StreamMethodProducer, StreamMethodConsumer
from transactron.testing import (
    SimpleTestCircuit,
    TestCaseWithSimulator,
    data_layout,
    TestbenchContext,
)


class TestStreamMethodProducer(TestCaseWithSimulator):
    def setup_method(self):
        self.data_width = 8
        random.seed(42)

    def test_simple_read(self):
        producer = StreamMethodProducer(self.data_width)
        m = SimpleTestCircuit(producer)

        async def testbench(sim: TestbenchContext):
            # Test 1: Stream has no data initially
            result = await m.read.call_try(sim)
            assert result is None, "Method should not be ready when stream is invalid"

            # Test 2: Provide data on the stream
            test_value = 42
            sim.set(producer.i.valid, 1)
            sim.set(producer.i.payload, test_value)
            await sim.tick()

            # Now the method should be able to read
            result = await m.read.call(sim)
            assert result.data == test_value, f"Expected {test_value}, got {result.data}"

            # Test 3: Multiple reads
            for i in range(10):
                test_value = i * 7 % (2**self.data_width)
                sim.set(producer.i.valid, 1)
                sim.set(producer.i.payload, test_value)
                await sim.tick()

                result = await m.read.call(sim)
                assert result.data == test_value

        with self.run_simulation(m) as sim:
            sim.add_testbench(testbench)

    def test_struct_layout(self):
        """Test with a structured payload"""
        struct_layout = StructLayout({"field1": 8, "field2": 4, "field3": 16})
        producer = StreamMethodProducer(struct_layout)
        m = SimpleTestCircuit(producer)

        async def testbench(sim: TestbenchContext):
            # Set structured data
            sim.set(producer.i.valid, 1)
            sim.set(producer.i.payload.field1, 0xAB)
            sim.set(producer.i.payload.field2, 0x5)
            sim.set(producer.i.payload.field3, 0x1234)
            await sim.tick()

            result = await m.read.call(sim)
            assert result.data.field1 == 0xAB
            assert result.data.field2 == 0x5
            assert result.data.field3 == 0x1234

        with self.run_simulation(m) as sim:
            sim.add_testbench(testbench)


class TestStreamMethodConsumer(TestCaseWithSimulator):
    def setup_method(self):
        self.data_width = 8
        random.seed(42)

    def test_simple_write(self):
        consumer = StreamMethodConsumer(self.data_width)
        m = SimpleTestCircuit(consumer)

        async def testbench(sim: TestbenchContext):
            # Initially, stream should not be valid
            assert sim.get(consumer.o.valid) == 0

            # Write data through the method
            test_value = 42
            await m.write.call(sim, data=test_value)

            # After the write, stream should be valid
            assert sim.get(consumer.o.valid) == 1
            assert sim.get(consumer.o.payload) == test_value

            # Consumer accepts the data
            sim.set(consumer.o.ready, 1)
            await sim.tick()

            assert sim.get(consumer.o.valid) == 0

        with self.run_simulation(m) as sim:
            sim.add_testbench(testbench)

    def test_buffering(self):
        """Test that buffering works correctly"""
        consumer = StreamMethodConsumer(self.data_width)
        m = SimpleTestCircuit(consumer)

        async def testbench(sim: TestbenchContext):
            await m.write.call(sim, data=10)

            # Stream should be valid with the data
            assert sim.get(consumer.o.valid) == 1
            assert sim.get(consumer.o.payload) == 10

            # Try to write again without consumer ready - should not be possible
            # because buffer is full
            result = await m.write.call_try(sim, data=20)
            assert result is None, "Write should not be ready when buffer is full"

            # Consumer accepts the data
            sim.set(consumer.o.ready, 1)
            await sim.tick()

            # Now stream should be invalid and we can write again
            assert sim.get(consumer.o.valid) == 0
            sim.set(consumer.o.ready, 0)

            await m.write.call(sim, data=20)

            assert sim.get(consumer.o.valid) == 1
            assert sim.get(consumer.o.payload) == 20

        with self.run_simulation(m) as sim:
            sim.add_testbench(testbench)

    def test_simultaneous_write_and_ready(self):
        """Test writing when consumer is ready in the same cycle"""
        consumer = StreamMethodConsumer(self.data_width)
        m = SimpleTestCircuit(consumer)

        async def testbench(sim: TestbenchContext):
            # Write a value and have buffer full
            await m.write.call(sim, data=10)

            # Stream should be valid with first value
            assert sim.get(consumer.o.valid) == 1
            assert sim.get(consumer.o.payload) == 10

            # Consumer becomes ready, and we write at the same time
            # This should work because buffer is emptied in the same cycle
            sim.set(consumer.o.ready, 1)
            result = await m.write.call_try(sim, data=20)
            assert result is not None, "Write should succeed when buffer is being emptied"

            # After the tick, the second write should have completed
            # Stream should still be valid but ready should be deasserted (next value is here)
            sim.set(consumer.o.ready, 0)

            assert sim.get(consumer.o.valid) == 1
            assert sim.get(consumer.o.payload) == 20

        with self.run_simulation(m) as sim:
            sim.add_testbench(testbench)

    def test_struct_layout(self):
        """Test with a structured layout"""
        struct_layout = StructLayout({"field1": 8, "field2": 4, "field3": 16})
        consumer = StreamMethodConsumer(struct_layout)
        m = SimpleTestCircuit(consumer)

        async def testbench(sim: TestbenchContext):
            # Write structured data
            await m.write.call(sim, data={"field1": 0xAB, "field2": 0x5, "field3": 0x1234})
            await sim.tick()

            assert sim.get(consumer.o.valid) == 1
            assert sim.get(consumer.o.payload.field1) == 0xAB
            assert sim.get(consumer.o.payload.field2) == 0x5
            assert sim.get(consumer.o.payload.field3) == 0x1234

            sim.set(consumer.o.ready, 1)
            await sim.tick()

            assert sim.get(consumer.o.valid) == 0

        with self.run_simulation(m) as sim:
            sim.add_testbench(testbench)


class TestStreamMethodIntegration(TestCaseWithSimulator):
    """Test producer and consumer working together"""

    def setup_method(self):
        self.data_width = 8
        random.seed(42)

    def test_producer_consumer_integration(self):
        """Test amaranth stream -> transactron -> amaranth stream roundtrip"""

        class TestCircuit(wiring.Component):
            i: stream.Interface
            o: stream.Interface

            def __init__(self, shape):
                self.shape = shape

                super().__init__(
                    {
                        "i": In(stream.Signature(shape)),
                        "o": Out(stream.Signature(shape)),
                    }
                )

            def elaborate(self, platform):
                m = TModule()
                m.submodules.producer = producer = StreamMethodProducer(self.shape)
                m.submodules.consumer = consumer = StreamMethodConsumer(self.shape)

                wiring.connect(m.main_module, wiring.flipped(self.i), producer.i)
                wiring.connect(m.main_module, wiring.flipped(self.o), consumer.o)

                # Connect the streams through a transaction
                with Transaction().body(m):
                    data = producer.read(m)
                    consumer.write(m, data=data.data)

                return m

        m = TestCircuit(self.data_width)
        circuit = SimpleTestCircuit(m)
        test_data = [random.randrange(2**self.data_width) for _ in range(20)]

        async def stream_writer(sim: TestbenchContext):
            """Simulates a stream producer"""
            for value in test_data:
                sim.set(m.i.valid, 1)
                sim.set(m.i.payload, value)
                await sim.tick().until(m.i.ready)
            sim.set(m.i.valid, 0)

        async def stream_reader(sim: TestbenchContext):
            """Simulates a stream consumer"""
            for expected in test_data:
                # Wait until valid
                if not sim.get(m.o.valid):
                    await sim.tick().until(~m.o.valid)
                actual = sim.get(m.o.payload)
                assert actual == expected, f"Expected {expected}, got {actual}"
                sim.set(m.o.ready, 1)
                await sim.tick()
                sim.set(m.o.ready, 0)

        with self.run_simulation(circuit) as sim:
            sim.add_testbench(stream_writer)
            sim.add_testbench(stream_reader)

    def test_backpressure(self):
        """Test that backpressure works correctly through the chain"""

        class TestCircuit(wiring.Component):
            i: stream.Interface
            o: stream.Interface

            def __init__(self, shape):
                self.shape = shape

                super().__init__(
                    {
                        "i": In(stream.Signature(shape)),
                        "o": Out(stream.Signature(shape)),
                    }
                )

            def elaborate(self, platform):
                m = TModule()
                m.submodules.producer = producer = StreamMethodProducer(self.shape)
                m.submodules.consumer = consumer = StreamMethodConsumer(self.shape)

                wiring.connect(m.main_module, wiring.flipped(self.i), producer.i)
                wiring.connect(m.main_module, wiring.flipped(self.o), consumer.o)

                with Transaction().body(m):
                    data = producer.read(m)
                    consumer.write(m, data=data.data)

                return m

        m = TestCircuit(self.data_width)
        circuit = SimpleTestCircuit(m)

        async def testbench(sim: TestbenchContext):
            # Provide data on input stream
            sim.set(m.i.valid, 1)
            sim.set(m.i.payload, 42)

            # Don't make consumer ready yet
            sim.set(m.o.ready, 0)

            # Wait a few cycles - data should flow to consumer buffer
            await sim.tick().repeat(5)

            # Consumer buffer should be full, blocking further transfers
            assert sim.get(m.o.valid) == 1
            assert sim.get(m.o.payload) == 42
            # Input ready should be low because consumer is blocked
            assert sim.get(m.i.ready) == 0

            # Make consumer ready
            sim.set(m.o.ready, 1)
            await sim.tick()

            # Now the transfer should complete
            assert sim.get(m.i.ready) == 1

        with self.run_simulation(circuit) as sim:
            sim.add_testbench(testbench)

    def test_stream_passthrough(self):
        """Test transactron -> amaranth stream -> transactron roundtrip"""

        class TestCircuit(Elaboratable):
            def __init__(self, shape):
                self.shape = shape
                method_layout = data_layout(shape)
                self.write = Method(i=method_layout)
                self.read = Method(o=method_layout)

            def elaborate(self, platform):
                m = TModule()

                m.submodules.producer = producer = StreamMethodProducer(self.shape)
                m.submodules.consumer = consumer = StreamMethodConsumer(self.shape)

                wiring.connect(m.main_module, producer.i, consumer.o)

                @def_method(m, self.write)
                def _(data):
                    return consumer.write(m, data=data)

                @def_method(m, self.read)
                def _():
                    return producer.read(m)

                return m

        circuit = SimpleTestCircuit(TestCircuit(self.data_width))
        test_data = [random.randrange(2**self.data_width) for _ in range(20)]

        async def stream_writer(sim: TestbenchContext):
            for value in test_data:
                await circuit.write.call(sim, data=value)

        async def stream_reader(sim: TestbenchContext):
            for expected in test_data:
                result = await circuit.read.call(sim)
                assert result.data == expected, f"Expected {expected}, got {result.data}"

        with self.run_simulation(circuit) as sim:
            sim.add_testbench(stream_writer)
            sim.add_testbench(stream_reader)
