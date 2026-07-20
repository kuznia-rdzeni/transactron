import functools
from typing import override
import pytest
import re
import logging as pylog
from io import StringIO
from contextlib import contextmanager, redirect_stdout
from textwrap import dedent
from amaranth import *
from amaranth.lib.enum import Enum, EnumView

from transactron import *
from transactron.testing import TestCaseWithSimulator, TestbenchContext
from transactron.utils import logging
from transactron.testing.logging import HDLLogWrapper, make_logging_process

LOGGER_NAME = "test_logger"

log = logging.HardwareLogger(LOGGER_NAME)


class LogTest(Elaboratable):
    def __init__(self, log_func):
        self.input = Signal(range(100))
        self.counter = Signal(range(200))
        self.log_func = log_func

    def elaborate(self, platform):
        m = TModule()

        with m.If(self.input == 42):
            self.log_func(m, True, "Log triggered under Amaranth If value+3=0x{:x}", self.input + 3)

        self.log_func(m, self.input[0] == 0, "Input is even! input={}, counter={}", self.input, self.counter)

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
    def __init__(self, log_func, is_assertion: bool):
        self.input = Signal()
        self.output = Signal()
        self.log_func = log_func
        self.is_assertion = is_assertion

    def elaborate(self, platform):
        m = TModule()

        m.d.comb += self.output.eq(self.input & ~self.input)

        self.log_func(
            m,
            (self.input != self.output) ^ self.is_assertion,
            "Input is different than output! input=0x{:x} output=0x{:x}",
            self.input,
            self.output,
        )

        return m


class LogTestCaseWithSimulator(TestCaseWithSimulator):
    @override
    @contextmanager
    def _configure_logging(self):
        def on_error():
            assert False

        self._transactron_sim_processes_to_add.append(lambda: make_logging_process(pylog.DEBUG, ".*", on_error))
        yield


def drop_first(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        return f(*args[1:], **kwargs)

    return wrapper


class TestLog(LogTestCaseWithSimulator):
    @pytest.mark.parametrize(
        "log_func, log_level, top_log",
        [
            (log.debug, "DEBUG", False),
            (log.info, "INFO", False),
            (log.warning, "WARNING", False),
            (drop_first(log.top_debug), "DEBUG", True),
            (drop_first(log.top_info), "INFO", True),
            (drop_first(log.top_warning), "WARNING", True),
        ],
    )
    def test_log(self, caplog, log_func, log_level: str, top_log: bool):
        caplog.set_level(pylog.DEBUG)
        m = LogTest(log_func)

        async def proc(sim: TestbenchContext):
            for i in range(50):
                await sim.tick()
                sim.set(m.input, i)
            await sim.tick()  # A log after the last tick is not handled

        with self.run_simulation(m) as sim:
            sim.add_testbench(proc)

        if top_log:
            for i in range(50):
                assert re.search(
                    f"{log_level:<7}"
                    + r"  test_logger:logging\.py:\d+ \[test/testing/test_log\.py:\d+\] "
                    + f"Log triggered under Amaranth If value\\+3=0x{i+3:x}",
                    caplog.text,
                )
        else:
            assert re.search(
                f"{log_level:<7}"
                + r"  test_logger:logging\.py:\d+ \[test/testing/test_log\.py:\d+\] "
                + "Log triggered under Amaranth If value\\+3=0x2d",
                caplog.text,
            )
        for i in range(0, 50, 2):
            assert re.search(
                f"{log_level:<7}"
                + r"  test_logger:logging\.py:\d+ \[test/testing/test_log\.py:\d+\] "
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

    @pytest.mark.parametrize(
        "log_func, is_assertion",
        [
            (log.error, False),
            (drop_first(log.top_error), False),
            (log.assertion, True),
            (drop_first(log.top_assertion), True),
            (functools.partial(logging.assertion, name=LOGGER_NAME), True),
            (functools.partial(drop_first(logging.top_assertion), name=LOGGER_NAME), True),
        ],
    )
    def test_error_log(self, caplog, log_func, is_assertion: bool):
        m = ErrorLogTest(log_func, is_assertion)

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


class TestLogWrapper(LogTestCaseWithSimulator):
    def test_log_wrapper(self):
        m = HDLLogWrapper(LogTest(log.warning))

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


class TestLogFormatInvalid(TestCaseWithSimulator):
    def test_bad_type(self):
        with pytest.raises(ValueError):
            a = Signal(2)
            log.top_assertion(1, "bad format {:q}", a)

    def test_too_few_args(self):
        with pytest.raises(IndexError):
            a = Signal(2)
            log.top_assertion(1, "bad format {}, {}", a)

    def test_too_many_args(self):
        with pytest.raises(ValueError):
            a = Signal(2)
            log.top_assertion(1, "bad format {}", a, a)

    def test_valid_amaranth_format_invalid_python_format(self):
        with pytest.raises(ValueError):
            a = Signal(8)
            log.top_assertion(1, "bad format {:s}", a)
