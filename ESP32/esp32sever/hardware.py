import machine
import neopixel
import time


class Hardware:
    def __init__(self):
        self._pin = machine.Pin(48, machine.Pin.OUT)
        self._np = neopixel.NeoPixel(self._pin, 1)
        self.motor_state = "STOP"
        self._encoder_i = 0.0
        self._encoder_j = 0.0

    def init_led(self, r=255, g=0, b=0):
        self._np[0] = (r, g, b)
        self._np.write()

    def set_color(self, r, g, b):
        self._np[0] = (int(r), int(g), int(b))
        self._np.write()

    def motor_start(self):
        self.set_color(0, 255, 0)
        self.motor_state = "START"

    def motor_stop(self):
        self.set_color(255, 0, 0)
        self.motor_state = "STOP"

    def read_encoder(self):
        self._encoder_i += 2.2
        self._encoder_j += 1.1
        if self._encoder_i > 360:
            self._encoder_i = 0.0
        if self._encoder_j > 380:
            self._encoder_j = 0.0
        return self._encoder_i, self._encoder_j

    def send_state(self, net):
        a, s = self.read_encoder()
        net.safe_send("STATE,motor=%s,angle=%s,speed=%s\n" %
                      (self.motor_state, a, s))

    def send_data(self, net):
        a, s = self.read_encoder()
        net.safe_send("DATA,angle=%s,speed=%s\n" % (a, s))
        time.sleep(0.1)
