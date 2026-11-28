import unittest

from amaranth import *
from transactron.testing import *
from transactron.utils import (
    align_to_power_of_two,
    align_down_to_power_of_two,
    popcount,
    count_leading_zeros,
    count_trailing_zeros,
    extract_lowest_set_bit,
    clear_lowest_set_bit,
    mask_after_first_set_bit,
    mask_from_first_set_bit,
    mask_until_first_set_bit,
    mask_before_first_set_bit,
    rotate_left,
    rotate_right,
    cyclic_mask,
    cyclic_mask_count,
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


def reference_rotate_left(width, value, amount):
    value = bin(value)[2:].zfill(width)
    return int(value[amount:] + value[:amount], 2)


def reference_rotate_right(width, value, amount):
    value = bin(value)[2:].zfill(width)
    return int(value[-amount:] + value[:-amount], 2)


def reference_cyclic_mask_count(width, start, count):
    return reference_rotate_left(width, (2**count) - 1, start)


def reference_cyclic_mask(width, start, end):
    expected = 0
    for i in range(min(start, end), max(start, end) + 1):
        expected |= 1 << i

    if end < start:
        expected ^= (1 << width) - 1
        expected |= 1 << start
        expected |= 1 << end

    return expected


class TestFunctions(TestCaseWithSimulator):
    def do_test_generic(self, function, ref_function, data_gen):
        # TODO: remove simulator when https://codeberg.org/amaranth-lang/rfcs/pulls/88 gets approved and implemented
        async def process(sim: TestbenchContext):
            data = data_gen()
            result = sim.get(function(*data[0]))
            expected = ref_function(*data[1])
            assert result == expected, f"Failed for: {data[2]}; expected {expected}, got {result}"

        with self.run_simulation(Module()) as sim:
            sim.add_testbench(process)

    def do_test_value(self, function, ref_function, data):
        def data_gen():
            width = data.draw(st.integers(min_value=0, max_value=256))
            value = data.draw(st.integers(min_value=0, max_value=(1 << width) - 1))
            return (Const(value, width),), (value, width), f"{width=}, {value=}"

        self.do_test_generic(function, ref_function, data_gen)

    def do_test_rotate(self, function, ref_function, data):
        def data_gen():
            width = data.draw(st.integers(min_value=1, max_value=256))
            value = data.draw(st.integers(min_value=0, max_value=(1 << width) - 1))
            amount = data.draw(st.integers(min_value=0, max_value=width - 1))
            amount_type = data.draw(st.sampled_from([Const, int]))
            return (
                (Const(value, width), amount_type(amount)),
                (width, value, amount),
                f"{width=}, {value=}, {amount=} ({amount_type!r})",
            )

        self.do_test_generic(function, ref_function, data_gen)

    def do_test_cyclic_mask(self, function, ref_function, data):
        def data_gen():
            width = data.draw(st.integers(min_value=1, max_value=256))
            start = data.draw(st.integers(min_value=0, max_value=width - 1))
            start_type = data.draw(st.sampled_from([Const, int]))
            pos = data.draw(st.integers(min_value=0, max_value=width - 1))
            pos_type = data.draw(st.sampled_from([Const, int]))
            return (
                (width, start_type(start), pos_type(pos)),
                (width, start, pos),
                f"{width=}, {start=} ({start_type!r}), end/count={pos} ({pos_type!r})",
            )

        self.do_test_generic(function, ref_function, data_gen)

    @given(st.data())
    def test_popcount(self, data):
        self.do_test_value(popcount, reference_popcount, data)

    @given(st.data())
    def test_count_leading_zeros(self, data):
        self.do_test_value(count_leading_zeros, reference_clz, data)

    @given(st.data())
    def test_count_trailing_zeros(self, data):
        self.do_test_value(count_trailing_zeros, reference_ctz, data)

    @given(st.data())
    def test_extract_lowest_set_bit(self, data):
        self.do_test_value(extract_lowest_set_bit, reference_extract_lowest_set_bit, data)

    @given(st.data())
    def test_clear_lowest_set_bit(self, data):
        self.do_test_value(clear_lowest_set_bit, reference_clear_lowest_set_bit, data)

    @given(st.data())
    def test_mask_after_first_set_bit(self, data):
        self.do_test_value(mask_after_first_set_bit, reference_mask_after_first_set_bit, data)

    @given(st.data())
    def test_mask_from_first_set_bit(self, data):
        self.do_test_value(mask_from_first_set_bit, reference_mask_from_first_set_bit, data)

    @given(st.data())
    def test_mask_until_first_set_bit(self, data):
        self.do_test_value(mask_until_first_set_bit, reference_mask_until_first_set_bit, data)

    @given(st.data())
    def test_mask_before_first_set_bit(self, data):
        self.do_test_value(mask_before_first_set_bit, reference_mask_before_first_set_bit, data)

    @given(st.data())
    def test_rotate_left(self, data):
        self.do_test_rotate(rotate_left, reference_rotate_left, data)

    @given(st.data())
    def test_rotate_right(self, data):
        self.do_test_rotate(rotate_right, reference_rotate_right, data)

    @given(st.data())
    def test_cyclic_mask_count(self, data):
        self.do_test_cyclic_mask(cyclic_mask_count, reference_cyclic_mask_count, data)

    @given(st.data())
    def test_cyclic_mask(self, data):
        self.do_test_cyclic_mask(cyclic_mask, reference_cyclic_mask, data)
