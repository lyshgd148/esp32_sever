import network
import socket
import time
import machine
import gc
import _thread
from .config import WIFI_SSID, WIFI_PASSWORD, PC_IP, PC_PORT, MAGIC
from .crc import crc16


class NetworkManager:
    def __init__(self):
        self.sock = None
        self.send_enable = False
        self.last_pong = 0
        self.need_reconnect = False
        self.send_lock = _thread.allocate_lock()

    def wifi_connect(self):
        while True:
            wlan = network.WLAN(network.STA_IF)
            wlan.active(False)
            time.sleep(1)
            wlan.active(True)
            wlan.disconnect()
            time.sleep(1)
            print("Connecting WiFi...")
            wlan.connect(WIFI_SSID, WIFI_PASSWORD)
            timeout = 0
            while not wlan.isconnected():
                time.sleep(0.5)
                timeout += 1
                if timeout > 40:
                    print("WiFi failed, retry...")
                    break
            if wlan.isconnected():
                print("WiFi OK:", wlan.ifconfig())
                return wlan

    def tcp_connect(self):
        try:
            if self.sock:
                self.sock.close()
        except:
            pass
        self.sock = None

        fail_count = 0

        while True:
            try:
                wlan = network.WLAN(network.STA_IF)
                if not wlan.isconnected():
                    print("WiFi lost -> reconnecting")
                    self.wifi_connect()

                print("Connecting TCP...")
                s = socket.socket()
                try:
                    s.settimeout(5)
                    s.connect((PC_IP, PC_PORT))
                    s.settimeout(0.1)
                    self.sock = s
                    self.send_enable = False
                    self.last_pong = time.ticks_ms()
                    print("TCP connected")
                    return
                except:
                    try:
                        s.close()
                    except:
                        pass
                    raise

            except Exception as e:
                fail_count += 1
                print("TCP fail #%d: %s" % (fail_count, e))

                if fail_count % 10 == 0:
                    gc.collect()

                if fail_count > 100:
                    print("Too many failures -> machine reset")
                    machine.reset()

                time.sleep(3)

    def safe_send(self, msg):
        try:
            if self.sock:
                self.send_lock.acquire()
                try:
                    self.sock.send(msg.encode())
                finally:
                    self.send_lock.release()
                return True
        except:
            print("send failed -> will reconnect")
            self.need_reconnect = True
        return False

    def send_binary(self, cmd, seq, payload):
        plen = len(payload)
        head = bytes([cmd, (seq >> 8) & 0xFF, seq & 0xFF,
                      (plen >> 8) & 0xFF, plen & 0xFF])
        body = head + payload
        crc = crc16(body)
        frame = MAGIC + body + bytes([(crc >> 8) & 0xFF, crc & 0xFF])
        try:
            self.send_lock.acquire()
            try:
                if self.sock:
                    self.sock.send(frame)
            finally:
                self.send_lock.release()
        except:
            print("send binary frame failed")
