# This module was copied from Amaranth because it is deprecated there.
# Copyright (C) 2019-2024 Amaranth HDL contributors

from amaranth.hdl import *
from amaranth.sim import *
from transactron.utils.amaranth_ext.coding import *
from transactron.testing import *


class TestEncoder(TestCaseWithSimulator):
    def test_basic(self):
        enc = Encoder(4)

        async def process(sim: TestbenchContext):
            assert sim.get(enc.n) == 1
            assert sim.get(enc.o) == 0

            sim.set(enc.i, 0b0001)
            assert sim.get(enc.n) == 0
            assert sim.get(enc.o) == 0

            sim.set(enc.i, 0b0100)
            assert sim.get(enc.n) == 0
            assert sim.get(enc.o) == 2

            sim.set(enc.i, 0b0110)
            assert sim.get(enc.n) == 1
            assert sim.get(enc.o) == 0

        with self.run_simulation(enc) as sim:
            sim.add_testbench(process)


class TestPriorityEncoder(TestCaseWithSimulator):
    def test_basic(self):
        enc = PriorityEncoder(4)

        async def process(sim: TestbenchContext):
            assert sim.get(enc.n) == 1
            assert sim.get(enc.o) == 0

            sim.set(enc.i, 0b0001)
            assert sim.get(enc.n) == 0
            assert sim.get(enc.o) == 0

            sim.set(enc.i, 0b0100)
            assert sim.get(enc.n) == 0
            assert sim.get(enc.o) == 2

            sim.set(enc.i, 0b0110)
            assert sim.get(enc.n) == 0
            assert sim.get(enc.o) == 1

            sim.set(enc.i, 0b1110)
            assert sim.get(enc.n) == 0
            assert sim.get(enc.o) == 1

        with self.run_simulation(enc) as sim:
            sim.add_testbench(process)


class TestDecoder(TestCaseWithSimulator):
    def test_basic(self):
        dec = Decoder(4)

        async def process(sim: TestbenchContext):
            assert sim.get(dec.o) == 0b0001

            sim.set(dec.i, 1)
            assert sim.get(dec.o) == 0b0010

            sim.set(dec.i, 3)
            assert sim.get(dec.o) == 0b1000

            sim.set(dec.n, 1)
            assert sim.get(dec.o) == 0b0000

        with self.run_simulation(dec) as sim:
            sim.add_testbench(process)
