from machine import Pin, PWM
import time
from array import array


class IRLearner:
    """红外学习 + 发射"""

    def __init__(
        self,
        pin_num=13,
        max_edges=1000,
        end_timeout_us=20000,
        led_pin=21,
        carrier_freq=38000,
        duty=21845,
    ):
        # 学习相关
        self.pin = Pin(pin_num, Pin.IN)
        self.max_edges = max_edges
        self.end_timeout_us = end_timeout_us

        self.raw = array("H", [0] * max_edges)
        self.index = 0
        self.last_tick = 0
        self.receiving = False

        # 发射相关
        self.ir_pwm = PWM(Pin(led_pin))
        self.ir_pwm.freq(carrier_freq)
        self.ir_pwm.duty_u16(0)
        self._duty = duty

    def _irq_handler(self, pin):
        """边沿中断: 记录两次跳变之间的微秒差值"""
        now = time.ticks_us()
        if not self.receiving:
            self.receiving = True
            self.last_tick = now
            return
        dt = time.ticks_diff(now, self.last_tick)
        if self.index < self.max_edges:
            self.raw[self.index] = dt
            self.index += 1
        self.last_tick = now

    def record(self, timeout_ms=5000):
        """录制红外时序, 返回微秒列表或超时返回 None"""
        try:
            self.index = 0
            self.last_tick = 0
            self.receiving = False

            self.pin.irq(
                trigger=Pin.IRQ_RISING | Pin.IRQ_FALLING,
                handler=self._irq_handler,
            )

            # 等待第一次跳变
            t0 = time.ticks_ms()
            while not self.receiving:
                if time.ticks_diff(time.ticks_ms(), t0) > timeout_ms:
                    self.pin.irq(handler=None)
                    return None
                time.sleep_ms(10)

            # 持续录制直到两次跳变间隔超过 end_timeout_us
            while True:
                if (
                    time.ticks_diff(time.ticks_us(), self.last_tick)
                    > self.end_timeout_us
                ):
                    break
                time.sleep_ms(1)

            self.pin.irq(handler=None)

            if self.index == 0:
                return None

            return [int(self.raw[i]) for i in range(self.index)]
        finally:
            self.pin.irq(handler=None)

    def _delay_us(self, us):
        """MicroPython 无 time.sleep_us, 手动忙等"""
        start = time.ticks_us()
        while time.ticks_diff(time.ticks_us(), start) < us:
            pass

    def send_raw(self, raw):
        """发射红外时序: 偶数位发载波, 奇数位关载波"""
        length = len(raw)
        for i in range(length):
            t = raw[i]
            if (i & 1) == 0:
                self.ir_pwm.duty_u16(self._duty)
            else:
                self.ir_pwm.duty_u16(0)
            self._delay_us(t)
        self.ir_pwm.duty_u16(0)

