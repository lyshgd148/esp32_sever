from machine import I2S, Pin
import math
import struct
import time
import gc


SAMPLE_RATE = 16000


# =========================
# 初始化I2S
# =========================

audio_i2s = None


def init_audio():

    global audio_i2s

    # 如果之前存在，先释放
    if audio_i2s is not None:
        try:
            audio_i2s.deinit()
        except:
            pass

    gc.collect()

    audio_i2s = I2S(
        1,  # ESP32-S3 使用 I2S1
        sck=Pin(4),  # BCLK
        ws=Pin(5),  # LRCLK
        sd=Pin(6),  # DATA
        mode=I2S.TX,
        bits=16,
        format=I2S.MONO,
        rate=SAMPLE_RATE,
        ibuf=20000,
    )

    print("I2S init OK")


# =========================
# 生成测试声音
# =========================


def create_tone(freq=1000, volume=0.5):

    samples = SAMPLE_RATE

    buf = bytearray(samples * 2)

    for i in range(samples):
        value = int(32767 * volume * math.sin(2 * math.pi * freq * i / SAMPLE_RATE))

        struct.pack_into("<h", buf, i * 2, value)

    return buf


# =========================
# 主程序
# =========================

try:
    init_audio()

    # 音量 0.0 ~ 1.0
    data = create_tone(freq=1000, volume=0.3)

    print("playing...")

    while True:
        audio_i2s.write(data)


except KeyboardInterrupt:
    print("stop")


finally:
    if audio_i2s is not None:
        try:
            audio_i2s.deinit()
            print("I2S released")

        except:
            pass

    gc.collect()
