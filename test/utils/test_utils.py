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
    extract_lowest_set_bit,
    clear_lowest_set_bit,
    mask_after_first_set_bit,
    mask_from_first_set_bit,
    mask_until_first_set_bit,
    mask_before_first_set_bit,
)
from hypothesis import given
import hypothesis.strategies as st


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


def reference_popcount(n, width):
    return n.bit_count()


def reference_clz(n, width):
    return width - n.bit_length()


def reference_ctz(n, width):
    if n == 0:
        return width
    return bin(n)[::-1].find("1")


def reference_extract_lowest_set_bit(n, width):
    if n == 0:
        return 0
    ctz = bin(n)[::-1].find("1")
    return 1 << ctz


def reference_clear_lowest_set_bit(n, width):
    return n & ~reference_extract_lowest_set_bit(n, width)


def reference_mask_after_first_set_bit(n, width):
    if n == 0:
        return 0
    ctz = bin(n)[::-1].find("1")
    return (-1 << (ctz + 1)) & ((1 << width) - 1)


def reference_mask_from_first_set_bit(n, width):
    if n == 0:
        return 0
    ctz = bin(n)[::-1].find("1")
    return (-1 << ctz) & ((1 << width) - 1)


def reference_mask_until_first_set_bit(n, width):
    if n == 0:
        return (1 << width) - 1
    ctz = bin(n)[::-1].find("1")
    return (1 << (ctz + 1)) - 1


def reference_mask_before_first_set_bit(n, width):
    if n == 0:
        return (1 << width) - 1
    ctz = bin(n)[::-1].find("1")
    return (1 << ctz) - 1


class TestBitManipulationFunctions(TestCaseWithSimulator):
    def do_test(self, function, ref_function, data):
        # TODO: remove simulator when https://codeberg.org/amaranth-lang/rfcs/pulls/88 gets approved and implemented
        async def process(sim: TestbenchContext):
            width = data.draw(st.integers(min_value=0, max_value=256))
            value = data.draw(st.integers(min_value=0, max_value=(1 << width) - 1))
            result = sim.get(function(Const(value, width)))
            expected = ref_function(value, width)
            assert result == expected, f"Failed for value {value} with width {width}"

        with self.run_simulation(Module()) as sim:
            sim.add_testbench(process)

    @given(st.data())
    def test_popcount(self, data):
        self.do_test(popcount, reference_popcount, data)

    @given(st.data())
    def test_count_leading_zeros(self, data):
        self.do_test(count_leading_zeros, reference_clz, data)

    @given(st.data())
    def test_count_trailing_zeros(self, data):
        self.do_test(count_trailing_zeros, reference_ctz, data)

    @given(st.data())
    def test_extract_lowest_set_bit(self, data):
        self.do_test(extract_lowest_set_bit, reference_extract_lowest_set_bit, data)

    @given(st.data())
    def test_clear_lowest_set_bit(self, data):
        self.do_test(clear_lowest_set_bit, reference_clear_lowest_set_bit, data)

    @given(st.data())
    def test_mask_after_first_set_bit(self, data):
        self.do_test(mask_after_first_set_bit, reference_mask_after_first_set_bit, data)

    @given(st.data())
    def test_mask_from_first_set_bit(self, data):
        self.do_test(mask_from_first_set_bit, reference_mask_from_first_set_bit, data)

    @given(st.data())
    def test_mask_until_first_set_bit(self, data):
        self.do_test(mask_until_first_set_bit, reference_mask_until_first_set_bit, data)

    @given(st.data())
    def test_mask_before_first_set_bit(self, data):
        self.do_test(mask_before_first_set_bit, reference_mask_before_first_set_bit, data)
