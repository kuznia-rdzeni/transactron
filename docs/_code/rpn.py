from amaranth import *
import amaranth.lib.data as data

from transactron import TModule, Transaction
from transactron.lib.basicio import InputSampler, OutputBuffer
from transactron.lib.connectors import ConnectTrans

from rpnstack import RpnStack


class Rpn(Elaboratable):
    def elaborate(self, platform):
        m = TModule()

        width = 4

        switch = Cat(platform.request("switch", i).i for i in range(width))
        led = Cat(platform.request("led", i).o for i in range(width))
        btn_push = platform.request("button", 0)
        btn_add = platform.request("button", 1)
        btn_mul = platform.request("button", 2)

        layout = data.StructLayout({"val": width})

        m.submodules.push_sampler = push_sampler = InputSampler(layout, synchronize=True, polarity=True, edge=True)
        m.d.comb += push_sampler.data.val.eq(switch)
        m.d.comb += push_sampler.trigger.eq(btn_push.i)

        m.submodules.add_sampler = add_sampler = InputSampler([], synchronize=True, polarity=True, edge=True)
        m.d.comb += add_sampler.trigger.eq(btn_add.i)

        m.submodules.mul_sampler = mul_sampler = InputSampler([], synchronize=True, polarity=True, edge=True)
        m.d.comb += mul_sampler.trigger.eq(btn_mul.i)

        m.submodules.led_buffer = led_buffer = OutputBuffer(layout, synchronize=True)
        m.d.comb += led.eq(led_buffer.data.val)

        m.submodules.rpnstack = rpnstack = RpnStack(width)

        m.submodules += ConnectTrans.create(rpnstack.push, push_sampler.get)
        m.submodules += ConnectTrans.create(led_buffer.put, rpnstack.peek)

        with Transaction().body(m):
            add_sampler.get(m)
            val1 = rpnstack.peek(m).val
            val2 = rpnstack.peek2(m).val
            rpnstack.pop_set_top(m, val=val1 + val2)

        with Transaction().body(m):
            mul_sampler.get(m)
            val1 = rpnstack.peek(m).val
            val2 = rpnstack.peek2(m).val
            rpnstack.pop_set_top(m, val=val1 * val2)

        return m
