from amaranth import *
import amaranth.lib.data as data

from transactron import TModule, Transaction
from transactron.lib.basicio import InputSampler, OutputBuffer


class LedControl(Elaboratable):
    def elaborate(self, platform):
        m = TModule()

        switch1 = platform.request("switch", 0)
        switch2 = platform.request("switch", 1)
        led = platform.request("led")
        btn_switch1 = platform.request("button", 0)
        btn_switch2 = platform.request("button", 1)

        layout = data.StructLayout({"val": 1})

        m.submodules.switch1_sampler = switch1_sampler = InputSampler(layout, synchronize=True, polarity=True)
        m.d.comb += switch1_sampler.data.val.eq(switch1.i)
        m.d.comb += switch1_sampler.trigger.eq(btn_switch1.i)

        m.submodules.switch2_sampler = switch2_sampler = InputSampler(layout, synchronize=True, polarity=True)
        m.d.comb += switch2_sampler.data.val.eq(switch2.i)
        m.d.comb += switch2_sampler.trigger.eq(btn_switch2.i)

        m.submodules.led_buffer = led_buffer = OutputBuffer(layout, synchronize=True)
        m.d.comb += led.o.eq(led_buffer.data.val)

        with Transaction().body(m):
            led_buffer.put(m, val=switch1_sampler.get(m).val)

        with Transaction().body(m):
            led_buffer.put(m, val=switch2_sampler.get(m).val)

        return m
