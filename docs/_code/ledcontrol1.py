from amaranth import *
import amaranth.lib.data as data

from transactron import TModule, Transaction
from transactron.lib.basicio import InputSampler, OutputBuffer


class LedControl(Elaboratable):
    def elaborate(self, platform):
        m = TModule()

        switch = platform.request("switch")
        led = platform.request("led")

        layout = data.StructLayout({"val": 1})

        m.submodules.switch_sampler = switch_sampler = InputSampler(layout, synchronize=True)
        m.d.comb += switch_sampler.data.val.eq(switch.i)

        m.submodules.led_buffer = led_buffer = OutputBuffer(layout, synchronize=True)
        m.d.comb += led.o.eq(led_buffer.data.val)

        with Transaction().body(m):
            led_buffer.put(m, val=switch_sampler.get(m).val)

        return m
