import time
import gc
import _thread
from esp32sever.config import PING_INTERVAL, PONG_TIMEOUT
from esp32sever.connection import NetworkManager
from esp32sever.protocol import parse_binary_frame, parse_audio_frame
from esp32sever.ota import OTAHandler
from esp32sever.hardware import Hardware
from esp32sever.ir_handler import do_ir_learn, do_ir_send
from esp32sever.audio import AudioPlayer


hw = Hardware()
net = NetworkManager()
ota = OTAHandler(net)
audio = AudioPlayer()
audio_active = False


def receiver_thread():
    global audio_active
    recv_buf = b""
    prev_binary = False

    while True:
        try:
            data = net.sock.recv(1024)
            if not data:
                print("TCP lost -> will reconnect")
                net.need_reconnect = True
                return
            recv_buf += data

            while True:
                if ota.state > 0:
                    while len(recv_buf) >= 9:
                        result = parse_binary_frame(recv_buf)
                        if result == -2:
                            break
                        if result == -1:
                            recv_buf = recv_buf[1:]
                            continue
                        cmd, seq, payload, consumed = result
                        ota.handle_frame(cmd, seq, payload)
                        recv_buf = recv_buf[consumed:]
                        if ota.state == 0:
                            break
                    if ota.state > 0:
                        break

                elif audio_active:
                    while len(recv_buf) >= 7:
                        result = parse_audio_frame(recv_buf)
                        if result == -2:
                            break
                        if result == -1:
                            recv_buf = recv_buf[1:]
                            continue
                        cmd, seq, payload, consumed = result
                        if cmd == 0x30:
                            audio.write(payload)
                            recv_buf = recv_buf[consumed:]
                        elif cmd == 0x31:
                            audio.stop()
                            audio_active = False
                            prev_binary = False
                            recv_buf = recv_buf[consumed:]
                            break
                    if audio_active:
                        break

                else:
                    if prev_binary:
                        recv_buf = b""
                    while b"\n" in recv_buf:
                        idx = recv_buf.find(b"\n")
                        line = recv_buf[:idx].decode().strip()
                        recv_buf = recv_buf[idx + 1 :]
                        if not line:
                            continue

                        if line == "PONG":
                            net.last_pong = time.ticks_ms()

                        elif line == "OTA_START":
                            ota.start_ready()

                        elif line == "FLASH_BIN":
                            ota.do_flash()

                        elif line == "AUDIO_START":
                            audio.start()
                            audio_active = True

                        elif line == "LEARN_IR":
                            do_ir_learn(net)

                        elif line.startswith("SEND_IR="):
                            data_str = line.split("=", 1)[1]
                            do_ir_send(net, data_str)

                        elif line == "GET_STATE":
                            hw.send_state(net)

                        elif line == "START_DATA":
                            net.send_enable = True

                        elif line == "STOP_DATA":
                            net.send_enable = False

                        elif line == "MOTOR_START":
                            hw.motor_start()
                            net.safe_send("MOTOR=START\n")

                        elif line == "MOTOR_STOP":
                            hw.motor_stop()
                            net.safe_send("MOTOR=STOP\n")

                        elif line.startswith("COLOR="):
                            _, rgb = line.split("=")
                            r, g, b = rgb.split(",")
                            hw.set_color(r, g, b)

                        if audio_active or ota.state > 0:
                            break

                    prev_binary = ota.state > 0 or audio_active
                    if audio_active or ota.state > 0:
                        continue
                    break

        except OSError:
            pass
        except Exception as e:
            print("receiver error:", e)
            net.need_reconnect = True
            return


def main():
    hw.init_led()
    net.wifi_connect()
    net.tcp_connect()

    _thread.start_new_thread(receiver_thread, ())

    last_ping = time.ticks_ms()
    was_paused = False

    while True:
        try:
            now = time.ticks_ms()

            if net.need_reconnect:
                print("Reconnecting...")
                ota.reset()
                net.tcp_connect()
                time.sleep(0.5)
                net.need_reconnect = False
                _thread.start_new_thread(receiver_thread, ())
                continue

            if ota.state == 0 and not ota.flash_active and not audio_active:
                if was_paused:
                    net.last_pong = now
                was_paused = False

                if time.ticks_diff(now, last_ping) > PING_INTERVAL:
                    net.safe_send("PING\n")
                    last_ping = now

                if time.ticks_diff(now, net.last_pong) > PONG_TIMEOUT:
                    print("No PONG -> reconnect")
                    net.need_reconnect = True
                    continue
            else:
                was_paused = True

            if (
                net.send_enable
                and ota.state == 0
                and not ota.flash_active
                and not audio_active
            ):
                hw.send_data(net)

            gc.collect()
            time.sleep(0.1)

        except Exception as e:
            print("main error:", e)
            net.wifi_connect()
            net.tcp_connect()
            time.sleep(0.5)
            ota.reset()
            _thread.start_new_thread(receiver_thread, ())


main()
