from machine import I2S, Pin
import gc

SAMPLE_RATE = 16000


class AudioPlayer:
    def __init__(self):
        self.i2s = None
        self.playing = False

    def _init(self):
        if self.i2s is not None:
            return
        gc.collect()
        self.i2s = I2S(
            1,
            sck=Pin(4),
            ws=Pin(5),
            sd=Pin(6),
            mode=I2S.TX,
            bits=16,
            format=I2S.MONO,
            rate=SAMPLE_RATE,
            ibuf=20000,
        )

    def start(self):
        self._init()
        self.playing = True

    def stop(self):
        self.playing = False

    def write(self, data):
        if self.i2s and self.playing:
            self.i2s.write(data)
