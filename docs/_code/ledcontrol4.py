from amaranth import *
import amaranth.lib.data as data

from transactron import TModule
from transactron.lib.basicio import InputSampler, OutputBuffer
from transactron.lib.connectors import ConnectTrans
from transactron.lib.fifo import BasicFifo


class LedControl(Elaboratable):
    def elaborate(self, platform):
        m = TModule()

        switch = platform.request("switch")
        led = platform.request("led", 0)
        led_fifo_write = platform.request("led", 1)
        led_fifo_read = platform.request("led", 2)
        btn_switch = platform.request("button", 0)
        btn_led = platform.request("button", 1)

        layout = data.StructLayout({"val": 1})

        m.submodules.switch_sampler = switch_sampler = InputSampler(layout, synchronize=True, polarity=True, edge=True)
        m.d.comb += switch_sampler.data.val.eq(switch.i)
        m.d.comb += switch_sampler.trigger.eq(btn_switch.i)

        m.submodules.led_buffer = led_buffer = OutputBuffer(layout, synchronize=True, polarity=True, edge=True)
        m.d.comb += led.o.eq(led_buffer.data.val)
        m.d.comb += led_buffer.trigger.eq(btn_led.i)

        m.submodules.fifo = fifo = BasicFifo(layout, 4)
        m.d.comb += led_fifo_write.o.eq(fifo.write.ready)
        m.d.comb += led_fifo_read.o.eq(fifo.read.ready)

        m.submodules += ConnectTrans.create(fifo.write, switch_sampler.get)
        m.submodules += ConnectTrans.create(led_buffer.put, fifo.read)

        return m
