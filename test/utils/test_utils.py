import unittest
import random
import pytest

from amaranth import *
from transactron.testing import *
from transactron.utils import (
    align_to_power_of_two,
    align_down_to_power_of_two,
    popcount,
    count_leading_zeros,
    count_trailing_zeros,
    cyclic_mask,
)
from amaranth.utils import ceil_log2


class TestAlignToPowerOfTwo(unittest.TestCase):
    def test_align_to_power_of_two(self):
        test_cases = [
            (2, 2, 4),
            (2, 1, 2),
            (3, 1, 4),
            (7, 3, 8),
            (8, 3, 8),
            (14, 3, 16),
            (17, 3, 24),
            (33, 3, 40),
            (33, 1, 34),
            (33, 0, 33),
            (33, 4, 48),
            (33, 5, 64),
            (33, 6, 64),
        ]

        for num, power, expected in test_cases:
            out = align_to_power_of_two(num, power)
            assert expected == out

    def test_align_down_to_power_of_two(self):
        test_cases = [
            (3, 1, 2),
            (3, 0, 3),
            (3, 3, 0),
            (8, 3, 8),
            (8, 2, 8),
            (33, 5, 32),
            (29, 5, 0),
            (29, 1, 28),
            (29, 3, 24),
        ]

        for num, power, expected in test_cases:
            out = align_down_to_power_of_two(num, power)
            assert expected == out


class PopcountTestCircuit(Elaboratable):
    def __init__(self, size: int):
        self.sig_in = Signal(size)
        self.sig_out = Signal(size)

    def elaborate(self, platform):
        m = Module()

        m.d.comb += self.sig_out.eq(popcount(self.sig_in))

        return m


@pytest.mark.parametrize("size", [2, 3, 4, 5, 6, 8, 10, 16, 21, 32, 33, 64, 1025])
class TestPopcount(TestCaseWithSimulator):
    @pytest.fixture(scope="function", autouse=True)
    def setup_fixture(self, size):
        self.size = size
        random.seed(14)
        self.test_number = 40
        self.m = PopcountTestCircuit(self.size)

    def check(self, sim: TestbenchContext, n):
        sim.set(self.m.sig_in, n)
        out_popcount = sim.get(self.m.sig_out)
        assert out_popcount == n.bit_count(), f"{n:x}"

    async def process(self, sim: TestbenchContext):
        for i in range(self.test_number):
            n = random.randrange(2**self.size)
            self.check(sim, n)
            sim.delay(1e-6)
        self.check(sim, 2**self.size - 1)

    def test_popcount(self, size):
        with self.run_simulation(self.m) as sim:
            sim.add_testbench(self.process)


class CLZTestCircuit(Elaboratable):
    def __init__(self, xlen: int):
        self.sig_in = Signal(xlen)
        self.sig_out = Signal(ceil_log2(xlen) + 1)
        self.xlen = xlen

    def elaborate(self, platform):
        m = Module()

        m.d.comb += self.sig_out.eq(count_leading_zeros(self.sig_in))
        # dummy signal
        s = Signal()
        m.d.sync += s.eq(1)

        return m


@pytest.mark.parametrize("size", [1, 2, 3, 4, 5, 6, 7, 8, 9, 15, 16, 17, 31, 32, 33, 63, 64, 65, 97, 98, 127, 128])
class TestCountLeadingZeros(TestCaseWithSimulator):
    @pytest.fixture(scope="function", autouse=True)
    def setup_fixture(self, size):
        self.size = size
        random.seed(14)
        self.test_number = 40
        self.m = CLZTestCircuit(self.size)

    def check(self, sim: TestbenchContext, n):
        sim.set(self.m.sig_in, n)
        out_clz = sim.get(self.m.sig_out)
        expected = (self.size) - n.bit_length()
        assert out_clz == expected, f"Incorrect result: got {out_clz}\t expected: {expected}"

    async def process(self, sim: TestbenchContext):
        for i in range(self.test_number):
            n = random.randrange(self.size)
            self.check(sim, n)
            sim.delay(1e-6)
        self.check(sim, 2**self.size - 1)
        await sim.delay(1e-6)
        self.check(sim, 0)

    def test_count_leading_zeros(self, size):
        with self.run_simulation(self.m) as sim:
            sim.add_testbench(self.process)


class CTZTestCircuit(Elaboratable):
    def __init__(self, xlen: int):
        self.sig_in = Signal(xlen)
        self.sig_out = Signal(ceil_log2(xlen) + 1)
        self.xlen = xlen

    def elaborate(self, platform):
        m = Module()

        m.d.comb += self.sig_out.eq(count_trailing_zeros(self.sig_in))
        # dummy signal
        s = Signal()
        m.d.sync += s.eq(1)

        return m


@pytest.mark.parametrize("size", [1, 2, 3, 4, 5, 6, 7, 8, 9, 15, 16, 17, 31, 32, 33, 63, 64, 65, 97, 98, 127, 128])
class TestCountTrailingZeros(TestCaseWithSimulator):
    @pytest.fixture(scope="function", autouse=True)
    def setup_fixture(self, size):
        self.size = size
        random.seed(14)
        self.test_number = 40
        self.m = CTZTestCircuit(self.size)

    def check(self, sim: TestbenchContext, n):
        sim.set(self.m.sig_in, n)
        out_ctz = sim.get(self.m.sig_out)

        expected = 0
        if n == 0:
            expected = self.size
        else:
            while (n & 1) == 0:
                expected += 1
                n >>= 1

        assert out_ctz == expected, f"{n:x}"

    async def process(self, sim: TestbenchContext):
        for i in range(self.test_number):
            n = random.randrange(self.size)
            self.check(sim, n)
            await sim.delay(1e-6)
        self.check(sim, self.size - 1)
        await sim.delay(1e-6)
        self.check(sim, 0)

    def test_count_trailing_zeros(self, size):
        with self.run_simulation(self.m) as sim:
            sim.add_testbench(self.process)


class GenCyclicMaskTestCircuit(Elaboratable):
    def __init__(self, xlen: int):
        self.start = Signal(range(xlen))
        self.end = Signal(range(xlen))
        self.sig_out = Signal(xlen)
        self.xlen = xlen

    def elaborate(self, platform):
        m = Module()

        m.d.comb += self.sig_out.eq(cyclic_mask(self.xlen, self.start, self.end))

        return m


@pytest.mark.parametrize("size", [1, 2, 3, 5, 8])
class TestGenCyclicMask(TestCaseWithSimulator):
    @pytest.fixture(scope="function", autouse=True)
    def setup_fixture(self, size):
        self.size = size
        random.seed(14)
        self.test_number = 40
        self.m = GenCyclicMaskTestCircuit(self.size)

    async def check(self, sim: TestbenchContext, start, end):
        sim.set(self.m.start, start)
        sim.set(self.m.end, end)
        await sim.delay(1e-6)
        out = sim.get(self.m.sig_out)

        expected = 0
        for i in range(min(start, end), max(start, end) + 1):
            expected |= 1 << i

        if end < start:
            expected ^= (1 << self.size) - 1
            expected |= 1 << start
            expected |= 1 << end

        assert out == expected

    async def process(self, sim: TestbenchContext):
        for _ in range(self.test_number):
            start = random.randrange(self.size)
            end = random.randrange(self.size)
            await self.check(sim, start, end)
            await sim.delay(1e-6)

    def test_count_trailing_zeros(self, size):
        with self.run_simulation(self.m) as sim:
            sim.add_testbench(self.process)
