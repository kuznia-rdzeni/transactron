import pytest
import re
from io import StringIO
from contextlib import redirect_stdout
from textwrap import dedent
from amaranth import *
from amaranth.lib.enum import Enum, EnumView

from transactron import *
from transactron.testing import TestCaseWithSimulator, TestbenchContext
from transactron.utils import logging
from transactron.testing.logging import HDLLogWrapper

LOGGER_NAME = "test_logger"

log = logging.HardwareLogger(LOGGER_NAME)


class LogTest(Elaboratable):
    def __init__(self):
        self.input = Signal(range(100))
        self.counter = Signal(range(200))

    def elaborate(self, platform):
        m = TModule()

        with m.If(self.input == 42):
            log.warning(m, True, "Log triggered under Amaranth If value+3=0x{:x}", self.input + 3)

        log.warning(m, self.input[0] == 0, "Input is even! input={}, counter={}", self.input, self.counter)

        m.d.sync += self.counter.eq(self.counter + 1)

        return m


class FooEnum(Enum, shape=1):
    FOO = 0
    BAR = 1


class ValueCastableLogTest(Elaboratable):
    def __init__(self):
        self.input: EnumView = Signal(FooEnum)  # type: ignore

    def elaborate(self, platform):
        m = TModule()

        log.warning(m, True, "Input value is {}", self.input)

        return m


class ErrorLogTest(Elaboratable):
    def __init__(self):
        self.input = Signal()
        self.output = Signal()

    def elaborate(self, platform):
        m = TModule()

        m.d.comb += self.output.eq(self.input & ~self.input)

        log.error(
            m,
            self.input != self.output,
            "Input is different than output! input=0x{:x} output=0x{:x}",
            self.input,
            self.output,
        )

        return m


class AssertionTest(Elaboratable):
    def __init__(self):
        self.input = Signal()
        self.output = Signal()

    def elaborate(self, platform):
        m = TModule()

        m.d.comb += self.output.eq(self.input & ~self.input)

        log.assertion(m, self.input == self.output, "Output differs")

        return m


class TestLog(TestCaseWithSimulator):
    def test_log(self, caplog):
        m = LogTest()

        async def proc(sim: TestbenchContext):
            for i in range(50):
                await sim.tick()
                sim.set(m.input, i)

        with self.run_simulation(m) as sim:
            sim.add_testbench(proc)

        assert re.search(
            r"WARNING  test_logger:logging\.py:\d+ \[test/testing/test_log\.py:\d+\] "
            + r"Log triggered under Amaranth If value\+3=0x2d",
            caplog.text,
        )
        for i in range(0, 50, 2):
            assert re.search(
                r"WARNING  test_logger:logging\.py:\d+ \[test/testing/test_log\.py:\d+\] "
                + f"Input is even! input={i}, counter={i + 1}",
                caplog.text,
            )

    def test_valuecastable(self, caplog):
        m = ValueCastableLogTest()

        async def proc(sim: TestbenchContext):
            sim.set(m.input, FooEnum.FOO)
            await sim.tick()
            sim.set(m.input, FooEnum.BAR)
            await sim.tick()

        with self.run_simulation(m) as sim:
            sim.add_testbench(proc)

        for e in FooEnum:
            assert re.search(
                r"WARNING  test_logger:logging\.py:\d+ \[test/testing/test_log\.py:\d+\] " + f"Input value is {e}",
                caplog.text,
            )

    def test_error_log(self, caplog):
        m = ErrorLogTest()

        async def proc(sim: TestbenchContext):
            await sim.tick()
            sim.set(m.input, 1)
            await sim.tick()  # A log after the last tick is not handled

        with pytest.raises(AssertionError):
            with self.run_simulation(m) as sim:
                sim.add_testbench(proc)

        assert re.search(
            r"ERROR    test_logger:logging\.py:\d+ \[test/testing/test_log\.py:\d+\] "
            + "Input is different than output! input=0x1 output=0x0",
            caplog.text,
        )

    def test_assertion(self, caplog):
        m = AssertionTest()

        async def proc(sim: TestbenchContext):
            await sim.tick()
            sim.set(m.input, 1)
            await sim.tick()  # A log after the last tick is not handled

        with pytest.raises(AssertionError):
            with self.run_simulation(m) as sim:
                sim.add_testbench(proc)

        assert re.search(
            r"ERROR    test_logger:logging\.py:\d+ \[test/testing/test_log\.py:\d+\] Output differs",
            caplog.text,
        )


class TestLogWrapper(TestCaseWithSimulator):
    def test_log_wrapper(self):
        m = HDLLogWrapper(LogTest())

        async def proc(sim: TestbenchContext):
            await sim.tick()
            await sim.tick()

        output = StringIO()
        with redirect_stdout(output):
            with self.run_simulation(m) as sim:
                sim.add_testbench(proc)

        assert output.getvalue() == dedent(
            """\
            --- CYCLE 0 ---
            WARNING test_logger: Input is even! input=0, counter=0
            --- CYCLE 1 ---
            WARNING test_logger: Input is even! input=0, counter=1
            """
        )
