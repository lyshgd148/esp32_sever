from machine import UART, Pin
import time
import struct
import os


class STM32Loader:
    def __init__(self):
        self.uart = UART(
            1, baudrate=115200, bits=8, parity=1, stop=1, tx=17, rx=18, timeout=0
        )

        self.boot0 = Pin(3, Pin.OUT)
        self.nrst = Pin(15, Pin.OUT)

    # =========================
    # 基础IO
    # =========================
    def write(self, data):
        self.uart.write(data)

    def flush(self):
        self.uart.read()

    # =========================
    # 读ACK
    # =========================
    def read_proto(self, timeout_ms=200):
        t0 = time.ticks_ms()

        while time.ticks_diff(time.ticks_ms(), t0) < timeout_ms:
            r = self.uart.read(1)
            if not r:
                continue

            b = r[0]

            if b == 0x79:
                return True
            if b == 0x1F:
                return False

        return None

    # =========================
    # RESET进入Bootloader
    # =========================
    def reset_to_bootloader(self):
        self.boot0.value(1)
        time.sleep_ms(80)

        self.nrst.value(0)
        time.sleep_ms(120)
        self.nrst.value(1)

        time.sleep_ms(600)  # ⭐关键稳定窗口
        self.flush()

    # =========================
    # 强制重试进入（核心升级）
    # =========================
    def ensure_bootloader(self):
        print("[*] ENTER BOOTLOADER SAFE MODE")

        for i in range(5):
            self.reset_to_bootloader()

            if self.sync():
                print("[+] Bootloader READY")
                return True

            print("[-] retry reset:", i + 1)

        return False

    # =========================
    # SYNC（只负责通信）
    # =========================
    def sync(self):
        self.flush()

        for _ in range(60):
            self.write(b"\x7f")

            if self.read_proto(120) is True:
                return True

            time.sleep_ms(40)

        return False

    # =========================
    # ERASE
    # =========================
    def erase(self):
        print("[*] ERASE")

        self.write(b"\x43\xbc")
        if not self.read_proto(300):
            return False

        self.write(b"\xff\x00")
        return self.read_proto(1500)

    # =========================
    # 地址
    # =========================
    def send_addr(self, addr):
        data = struct.pack(">I", addr)

        xor = 0
        for b in data:
            xor ^= b

        self.write(data + bytes([xor]))
        return self.read_proto(300)

    # =========================
    # 写Flash
    # =========================
    def write_mem(self, addr, buf):
        self.write(b"\x31\xce")
        if not self.read_proto(300):
            return False

        if not self.send_addr(addr):
            return False

        length = len(buf) - 1
        xor = length

        for b in buf:
            xor ^= b

        self.write(bytes([length]) + buf + bytes([xor]))

        return self.read_proto(500)

    # =========================
    # 主流程（升级重点）
    # =========================
    def flash(self, path, callback=None):
        print("[*] FLASH START")

        addr = 0x08000000
        chunk = 128

        # 计算总块数
        try:
            fsize = os.stat(path)[6]
            total = (fsize + chunk - 1) // chunk
        except:
            total = 0
        if callback:
            callback("start", total)

        # ⭐关键升级：自动保证bootloader可用
        if not self.ensure_bootloader():
            print("[-] BOOTLOADER FAIL")
            if callback:
                callback("error", "BOOTLOADER FAIL")
            return

        if callback:
            callback("erase")

        if not self.erase():
            print("[-] ERASE FAIL")
            if callback:
                callback("error", "ERASE FAIL")
            return

        i = 0
        with open(path, "rb") as f:
            while True:
                data = f.read(chunk)
                if not data:
                    break

                if len(data) % 4:
                    data += b"\xff" * (4 - len(data) % 4)

                if not self.write_mem(addr, data):
                    print("[FAIL]", hex(addr))
                    if callback:
                        callback("error", "WRITE FAIL at %s" % hex(addr))
                    return

                addr += len(data)
                print("[+] ->", hex(addr))
                if callback:
                    callback("chunk", i, total)
                i += 1

        print("[*] DONE")
        if callback:
            callback("done")
        self.start_app()

    # =========================
    # 启动APP
    # =========================
    def start_app(self):
        print("[*] START APP")

        self.boot0.value(0)
        time.sleep_ms(80)

        self.nrst.value(0)
        time.sleep_ms(120)
        self.nrst.value(1)

        time.sleep_ms(500)
        self.flush()


# =========================
# RUN
# =========================
if __name__ == "__main__":
    loader = STM32Loader()
    loader.flash("2.bin")
