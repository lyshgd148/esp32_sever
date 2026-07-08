from .config import CHUNK_SIZE, CMD_OTA_START, CMD_OTA_DATA, CMD_OTA_ABORT
from .config import CMD_OTA_ACK, CMD_OTA_DONE, CMD_OTA_ERROR, OTA_BIN_NAME
from .crc import crc32_init, crc32_update, crc32_final
from .protocol import _pack_u32


class OTAHandler:
    def __init__(self, net):
        self.net = net
        self.reset()

    def reset(self):
        self.state = 0
        self.crc32 = 0
        self.filesize = 0
        self.received = 0
        self.file = None
        self.last_seen = {}
        self.flash_active = False

    def start_ready(self):
        print("OTA: start ready")
        self.state = 1
        self.crc32 = crc32_init()
        self.filesize = 0
        self.received = 0
        self.last_seen = {}
        try:
            self.file = open(OTA_BIN_NAME, "wb")
        except Exception as e:
            print("OTA: open file failed", e)
            self.state = 0
            self.net.safe_send("OTA_ERROR\n")
            return
        self.net.safe_send("OTA_READY\n")

    def handle_frame(self, cmd, seq, payload):
        if cmd == CMD_OTA_START:
            if len(payload) < 8:
                self.net.send_binary(CMD_OTA_ERROR, 0, bytes([2]))
                self.state = 0
                return True
            fsize = (payload[0] | (payload[1] << 8) |
                     (payload[2] << 16) | (payload[3] << 24))
            expected_crc = (payload[4] | (payload[5] << 8) |
                            (payload[6] << 16) | (payload[7] << 24))
            self.filesize = fsize
            self.crc32 = crc32_init()
            self.received = 0
            self.last_seen = {}
            self.state = 2
            print("OTA: file_size=%d, crc32=0x%08X" % (fsize, expected_crc))
            self.net.send_binary(CMD_OTA_ACK, 0, bytes([0, 0, 0, 0]))
            return True

        elif cmd == CMD_OTA_DATA:
            if self.state != 2:
                return True
            if len(payload) < 4:
                return True
            offset = (payload[0] | (payload[1] << 8) |
                      (payload[2] << 16) | (payload[3] << 24))
            chunk_data = payload[4:]
            chunk_len = len(chunk_data)

            if seq in self.last_seen:
                self.net.send_binary(CMD_OTA_ACK, 0,
                                     _pack_u32(self.received))
                return True
            self.last_seen[seq] = True

            try:
                self.file.seek(offset)
                self.file.write(chunk_data)
            except Exception as e:
                print("OTA: write error", e)
                self.net.send_binary(CMD_OTA_ERROR, 0, bytes([3]))
                self.file.close()
                self.state = 0
                return True

            self.crc32 = crc32_update(self.crc32, chunk_data)

            if offset == self.received:
                self.received += chunk_len
                while True:
                    if self.received >= self.filesize:
                        break
                    nc = self.received // CHUNK_SIZE
                    if nc in self.last_seen:
                        self.received = min(self.received + CHUNK_SIZE,
                                            self.filesize)
                    else:
                        break

            self.net.send_binary(CMD_OTA_ACK, 0,
                                 _pack_u32(min(self.received, self.filesize)))

            if self.received >= self.filesize:
                self.state = 3
                print("OTA: all chunks received, verifying CRC32 ...")
                self._verify()
            return True

        elif cmd == CMD_OTA_ABORT:
            print("OTA: aborted by server")
            if self.state >= 1:
                self._cleanup()
            self.state = 0
            return True

        return False

    def _verify(self):
        final_crc = crc32_final(self.crc32)
        print("OTA: received_crc32=0x%08X" % final_crc)
        payload = bytes([0]) + _pack_u32(final_crc)
        self.net.send_binary(CMD_OTA_DONE, 0, payload)
        self._cleanup()
        self.state = 0
        print("OTA: done")

    def _cleanup(self):
        try:
            if self.file:
                self.file.close()
        except:
            pass
        self.file = None

    def do_flash(self):
        self.flash_active = True
        try:
            from STM32DOWN import STM32Loader

            def _cb(step, *args):
                if step == "start":
                    self.net.safe_send("FLASH_START=%d\n" % args[0])
                elif step == "erase":
                    self.net.safe_send("FLASH_ERASE\n")
                elif step == "chunk":
                    idx, total = args
                    pct = idx * 100 // total if total else 0
                    self.net.safe_send("FLASH_PROGRESS=%d\n" % pct)
                elif step == "done":
                    self.net.safe_send("FLASH_DONE\n")
                elif step == "error":
                    self.net.safe_send("FLASH_ERROR=%s\n" % args[0])

            loader = STM32Loader()
            loader.flash(OTA_BIN_NAME, callback=_cb)
        except Exception as e:
            self.net.safe_send("FLASH_ERROR=%s\n" % str(e))
        finally:
            self.flash_active = False
