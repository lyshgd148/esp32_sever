"""
OTA 固件传输协议实现
=====================

帧格式 (防 TCP 粘包):
  [0xAA] [0x55] [CMD:1B] [SEQ:2B BE] [LEN:2B BE] [PAYLOAD:LEN] [CRC16:2B BE]
  总开销: 2 + 1 + 2 + 2 + 2 = 9 字节/帧 (不含 payload)

命令字:
  S→E: 0x10 OTA_START, 0x11 OTA_DATA, 0x12 OTA_ABORT
  E→S: 0x20 OTA_ACK,   0x21 OTA_DONE,  0x22 OTA_ERROR

滑动窗口: 4 帧, 每帧 256B 数据, ACK 超时 3s, 最多重传 3 次

CRC16: 帧级校验, 多项式 0x1021 (CRC-16/XMODEM)
CRC32: 文件级校验, 多项式 0xEDB88320 (CRC-32/gzip, 反射式)
"""

import os
import queue
import struct
import time
import threading
from . import state as st
from .state import safe_send

# ===== 常量 =====
MAGIC_B0 = 0xAA  # 魔术头第 1 字节
MAGIC_B1 = 0x55  # 魔术头第 2 字节
MAGIC = b"\xaa\x55"

# 命令字
CMD_OTA_START = 0x10  # S→E: 开始传输 (附带 file_size + crc32)
CMD_OTA_DATA = 0x11  # S→E: 数据块 (附带 offset + chunk)
CMD_OTA_ABORT = 0x12  # S→E: 中止传输
CMD_OTA_ACK = 0x20  # E→S: 累积确认 (acked_offset)
CMD_OTA_DONE = 0x21  # E→S: 传输完成 (status + crc32)
CMD_OTA_ERROR = 0x22  # E→S: 出错 (error_code)

CHUNK_SIZE = 256  # 每个数据块的 payload 字节数
WINDOW_SIZE = 4  # 滑动窗口大小 (帧数)
ACK_TIMEOUT = 3.0  # ACK 超时秒数
MAX_RETRIES = 3  # 最大重试次数

HEADER_LEN = 7  # MAGIC(2) + CMD(1) + SEQ(2) + LEN(2)
FRAME_OVERHEAD = 9  # HEADER_LEN + CRC16(2)

# ===== CRC16 查表 (CRC-16/XMODEM) =====
_CRC16_TABLE = None


def _crc16_init():
    """预计算 CRC16 查表, 多项式 0x1021"""
    global _CRC16_TABLE
    if _CRC16_TABLE is not None:
        return
    _CRC16_TABLE = []
    for i in range(256):
        crc = i << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc = crc << 1
            crc &= 0xFFFF
        _CRC16_TABLE.append(crc)


def crc16(data):
    """计算字节串的 CRC16, 用于帧完整性校验"""
    _crc16_init()
    crc = 0
    for b in data:
        crc = ((crc << 8) ^ _CRC16_TABLE[((crc >> 8) ^ b) & 0xFF]) & 0xFFFF
    return crc


# ===== CRC32 查表 (CRC-32/gzip, 反射多项式 0xEDB88320) =====
_CRC32_TABLE = None


def _crc32_init():
    """预计算 CRC32 查表"""
    global _CRC32_TABLE
    if _CRC32_TABLE is not None:
        return
    _CRC32_TABLE = []
    for i in range(256):
        crc = i
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xEDB88320
            else:
                crc = crc >> 1
        _CRC32_TABLE.append(crc)


def crc32_bytes(data):
    """一次性计算整个字节串的 CRC32"""
    _crc32_init()
    crc = 0xFFFFFFFF
    for b in data:
        crc = _CRC32_TABLE[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF


def crc32_file(filepath):
    """分块读取文件计算 CRC32, 避免大文件占用内存"""
    _crc32_init()
    crc = 0xFFFFFFFF
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(4096)
            if not chunk:
                break
            for b in chunk:
                crc = _CRC32_TABLE[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF


# ===== 帧的构建与解析 =====
def build_frame(cmd, seq, payload):
    """
    构建一帧 bytes:
      MAGIC(2) + CMD(1) + SEQ(2 BE) + LEN(2 BE) + PAYLOAD + CRC16(2 BE)
    """
    plen = len(payload)
    # 头部: CMD + SEQ + PLEN (不含 MAGIC, 是 CRC16 覆盖范围的开头)
    head = struct.pack(">BHH", cmd, seq, plen)
    # CRC16 覆盖 head + payload
    frame_crc = crc16(head + payload)
    return b"".join(
        [
            MAGIC,
            head,
            payload,
            struct.pack(">H", frame_crc),
        ]
    )

CMD_AUDIO_DATA = 0x30
CMD_AUDIO_STOP = 0x31


def build_audio_frame(cmd, payload):
    plen = len(payload)
    head = struct.pack(">BHH", cmd, 0, plen)
    return b"".join([MAGIC, head, payload])


def parse_frame(buf):
    """
    尝试从缓冲区开头解析一个二进制帧。
    返回 (cmd, seq, payload, consumed) 成功, None 失败(数据不足/CRC错)。
    consumed 是本次消费的字节数, 调用方据此截断 buf。
    """
    if len(buf) < FRAME_OVERHEAD:
        return None

    if buf[0] != MAGIC_B0 or buf[1] != MAGIC_B1:
        return None

    cmd = buf[2]
    seq = (buf[3] << 8) | buf[4]
    plen = (buf[5] << 8) | buf[6]
    frame_len = FRAME_OVERHEAD + plen

    if len(buf) < frame_len:
        return None

    # CRC16 覆盖字节 [2 : 7+plen] (CMD 到 PAYLOAD 末尾)
    crc_area = buf[2 : 7 + plen]
    exp_crc = crc16(crc_area)
    recv_crc = (buf[7 + plen] << 8) | buf[7 + plen + 1]

    if exp_crc != recv_crc:
        return None

    payload = buf[7 : 7 + plen]
    return cmd, seq, payload, frame_len


# ===== OTA 发送器 (服务端) =====
class OtaSender:
    """
    服务端 OTA 发送器, 运行在独立线程中。
    负责: 通知 ESP32 → 发送数据 → 滑动窗口控制 → 收 ACK → 完成/重试
    """

    def __init__(self, filepath):
        self.filepath = filepath
        self.total_chunks = 0
        self.filesize = 0
        self.file_crc32 = 0
        self.retry_count = 0

    def _recv_frame(self, timeout=None):
        """从 st.ota_queue 等待一帧"""
        try:
            return st.ota_queue.get(timeout=timeout)
        except queue.Empty:
            return None, None, None

    def _send_frame(self, cmd, seq, payload):
        """通过 TCP 发送一帧 (帧格式: MAGIC + HEAD + PAYLOAD + CRC16)"""
        frame = build_frame(cmd, seq, payload)
        safe_send(frame)

    def _emit(self, socketio, pct, msg):
        """推送进度到 Web 前端"""
        st.ota_progress = pct
        try:
            socketio.emit("ota_progress", {"percent": pct, "message": msg})
        except Exception:
            pass

    def run(self):
        """
        主入口。返回 True(成功) / False(失败)。
        流程:
          1. 读文件, 算 CRC32
          2. 发 "OTA_START\\n" 文本命令通知 ESP32 准备
          3. 等 ESP32 回复 "OTA_READY\\n" (tcp_recv_loop → handle_esp32 触发)
          4. 发 OTA_START 二进制帧 (filesize + crc32)
          5. 滑动窗口发送数据块, 收 OTA_ACK
          6. 全部发完 → 等 OTA_DONE/OTA_ERROR
          7. 清理 ota_active / ota_queue
        """
        from .extensions import socketio

        # ---- 0. 前置检查 ----
        if not os.path.exists(self.filepath):
            self._emit(socketio, -1, "文件不存在: " + self.filepath)
            self._cleanup()
            return False

        self.filesize = os.path.getsize(self.filepath)
        if self.filesize == 0:
            self._emit(socketio, -1, "文件为空")
            self._cleanup()
            return False

        # ---- 1. 计算文件 CRC32 ----
        self._emit(socketio, 0, "计算文件 CRC32 ...")
        self.file_crc32 = crc32_file(self.filepath)

        # ---- 2. 文本命令通知 ESP32 准备 OTA ----
        self._emit(socketio, 0, "通知 ESP32 准备 OTA ...")
        safe_send(b"OTA_START\n")

        # 等待 handle_esp32 收到 OTA_READY 后设置 ota_active 和 ota_queue
        wait_start = time.time()
        while not st.ota_active or st.ota_queue is None:
            if time.time() - wait_start > ACK_TIMEOUT:
                self._emit(socketio, -1, "ESP32 未响应 OTA_START (超时)")
                self._cleanup()
                return False
            time.sleep(0.1)

        # ---- 3. 发送 OTA_START 二进制帧 ----
        self._emit(
            socketio,
            0,
            "发送 OTA_START (size=%d, crc32=0x%08X)" % (self.filesize, self.file_crc32),
        )
        start_payload = struct.pack("<II", self.filesize, self.file_crc32)
        self._send_frame(CMD_OTA_START, 0, start_payload)

        # 等 ESP32 回复 OTA_ACK 确认准备就绪
        cmd, seq, payload = self._recv_frame(timeout=ACK_TIMEOUT)
        if cmd != CMD_OTA_ACK:
            self._emit(socketio, -1, "ESP32 未确认 OTA_START")
            self._cleanup()
            return False
        self._emit(socketio, 0, "ESP32 就绪, 开始传输 ...")

        # ---- 4. 读取全部数据块 ----
        chunks = []  # [(offset, data), ...]
        with open(self.filepath, "rb") as f:
            offset = 0
            while True:
                raw = f.read(CHUNK_SIZE)
                if not raw:
                    break
                chunks.append((offset, raw))
                offset += len(raw)
        self.total_chunks = len(chunks)

        # ---- 5. 滑动窗口发送 ----
        wnd_base = 0  # 窗口起始 chunk 索引 (Go-Back-N)
        next_seq = 0  # 下一个待发送的 chunk 索引
        # self.retry_count 在 __init__ 中已初始化为 0, 递归重试时保留累加值
        last_ack_time = time.time()

        while wnd_base < self.total_chunks:
            # 填满窗口: 发送 wnd_base 开始的 WINDOW_SIZE 个未发送块
            while next_seq < wnd_base + WINDOW_SIZE and next_seq < self.total_chunks:
                off, data = chunks[next_seq]
                p = struct.pack("<I", off) + data
                self._send_frame(CMD_OTA_DATA, next_seq, p)
                next_seq += 1

            # 等待 ACK
            cmd, seq, payload = self._recv_frame(timeout=0.3)

            if cmd == CMD_OTA_ACK:
                # acked_offset: ESP32 已连续接收的字节数 (即下一个期望的字节偏移)
                acked_off = struct.unpack("<I", payload)[0]
                new_base = acked_off // CHUNK_SIZE
                if new_base > wnd_base:
                    wnd_base = new_base
                    self.retry_count = 0
                    last_ack_time = time.time()
                    pct = min(acked_off * 100 // self.filesize, 100)
                    self._emit(socketio, pct, "传输中 %d%%" % pct)

            elif cmd == CMD_OTA_DONE:
                # ESP32 已完成校验并回复结果
                status = payload[0]
                rcv_crc = struct.unpack("<I", payload[1:5])[0]
                if status == 0 and rcv_crc == self.file_crc32:
                    self._emit(socketio, 100, "OTA 成功! 文件已写入 ESP32")
                    self._cleanup()
                    return True
                else:
                    self.retry_count += 1
                    if self.retry_count >= MAX_RETRIES:
                        self._emit(socketio, -1, "CRC32 校验失败, 已达最大重试")
                        self._cleanup()
                        return False
                    self._emit(
                        socketio,
                        wnd_base * CHUNK_SIZE * 100 // self.filesize,
                        "CRC32 不匹配, 重试 %d/%d" % (self.retry_count, MAX_RETRIES),
                    )
                    # 清理状态后从头重来
                    self._cleanup()
                    return self.run()

            elif cmd == CMD_OTA_ERROR:
                err = payload[0] if payload else 0
                self._emit(socketio, -1, "ESP32 报错 (code=%d)" % err)
                self._cleanup()
                return False

            # 窗口超时 → Go-Back-N 重传
            if time.time() - last_ack_time > ACK_TIMEOUT:
                self.retry_count += 1
                if self.retry_count >= MAX_RETRIES:
                    self._emit(socketio, -1, "ACK 超时, 已达最大重试")
                    self._send_frame(CMD_OTA_ABORT, 0, b"")
                    self._cleanup()
                    return False
                self._emit(
                    socketio,
                    wnd_base * CHUNK_SIZE * 100 // self.filesize,
                    "ACK 超时, Go-Back-N %d/%d" % (self.retry_count, MAX_RETRIES),
                )
                next_seq = wnd_base  # 回退发送指针, 窗口内全部重传
                last_ack_time = time.time()

        # ---- 6. 全部发送完毕, 等最终 OTA_DONE ----
        self._emit(socketio, 100, "数据发送完毕, 等待 ESP32 校验 ...")
        while True:
            cmd, seq, payload = self._recv_frame(timeout=ACK_TIMEOUT)
            if cmd == CMD_OTA_DONE:
                status = payload[0]
                rcv_crc = struct.unpack("<I", payload[1:5])[0]
                if status == 0 and rcv_crc == self.file_crc32:
                    self._emit(socketio, 100, "OTA 成功!")
                    self._cleanup()
                    return True
                else:
                    self.retry_count += 1
                    if self.retry_count >= MAX_RETRIES:
                        self._emit(socketio, -1, "CRC32 不匹配, 已达最大重试")
                        self._cleanup()
                        return False
                    self._emit(socketio, -1, "CRC32 不匹配, 重试中 ...")
                    self._cleanup()
                    return self.run()

            elif cmd == CMD_OTA_ERROR:
                err = payload[0] if payload else 0
                self._emit(socketio, -1, "ESP32 报错 (code=%d)" % err)
                self._cleanup()
                return False

            elif cmd is None:
                self.retry_count += 1
                if self.retry_count >= MAX_RETRIES:
                    self._emit(socketio, -1, "等待 OTA_DONE 超时")
                    self._cleanup()
                    return False
                self._emit(
                    socketio,
                    -1,
                    "等待 OTA_DONE 超时, 重试 %d/%d" % (self.retry_count, MAX_RETRIES),
                )
                self._emit(socketio, -1, "正在重试整包传输 ...")
                self._cleanup()
                return self.run()

    def _cleanup(self):
        """恢复文本模式"""
        st.end_ota()
