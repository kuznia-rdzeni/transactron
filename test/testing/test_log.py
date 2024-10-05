import pytest
import re
from amaranth import *
from amaranth.sim import Tick

from transactron import *
from transactron.testing import TestCaseWithSimulator
from transactron.lib import logging

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

        def proc():
            for i in range(50):
                yield Tick()
                yield m.input.eq(i)

        with self.run_simulation(m) as sim:
            sim.add_process(proc)

        assert re.search(
            r"WARNING  test_logger:logging\.py:\d+ \[test/transactron/testing/test_log\.py:\d+\] "
            + r"Log triggered under Amaranth If value\+3=0x2d",
            caplog.text,
        )
        for i in range(0, 50, 2):
            assert re.search(
                r"WARNING  test_logger:logging\.py:\d+ \[test/transactron/testing/test_log\.py:\d+\] "
                + f"Input is even! input={i}, counter={i + 1}",
                caplog.text,
            )

    def test_error_log(self, caplog):
        m = ErrorLogTest()

        def proc():
            yield Tick()
            yield m.input.eq(1)

        with pytest.raises(AssertionError):
            with self.run_simulation(m) as sim:
                sim.add_process(proc)

        assert re.search(
            r"ERROR    test_logger:logging\.py:\d+ \[test/transactron/testing/test_log\.py:\d+\] "
            + "Input is different than output! input=0x1 output=0x0",
            caplog.text,
        )

    def test_assertion(self, caplog):
        m = AssertionTest()

        def proc():
            yield Tick()
            yield m.input.eq(1)

        with pytest.raises(AssertionError):
            with self.run_simulation(m) as sim:
                sim.add_process(proc)

        assert re.search(
            r"ERROR    test_logger:logging\.py:\d+ \[test/transactron/testing/test_log\.py:\d+\] Output differs",
            caplog.text,
        )
