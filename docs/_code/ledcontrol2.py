from amaranth import *
import amaranth.lib.data as data

from transactron import TModule, Transaction
from transactron.lib.basicio import InputSampler, OutputBuffer


class LedControl(Elaboratable):
    def elaborate(self, platform):
        m = TModule()

        switch = platform.request("switch")
        led = platform.request("led")
        btn_switch = platform.request("button", 0)
        btn_led = platform.request("button", 1)

        layout = data.StructLayout({"val": 1})

        m.submodules.switch_sampler = switch_sampler = InputSampler(layout, synchronize=True, polarity=True)
        m.d.comb += switch_sampler.data.val.eq(switch.i)
        m.d.comb += switch_sampler.trigger.eq(btn_switch.i)

        m.submodules.led_buffer = led_buffer = OutputBuffer(layout, synchronize=True, polarity=True)
        m.d.comb += led.o.eq(led_buffer.data.val)
        m.d.comb += led_buffer.trigger.eq(btn_led.i)

        with Transaction().body(m):
            led_buffer.put(m, val=switch_sampler.get(m).val)

        return m
