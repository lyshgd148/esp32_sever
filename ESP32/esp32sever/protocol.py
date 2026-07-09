from .crc import crc16


def _pack_u32(val):
    return bytes([val & 0xFF, (val >> 8) & 0xFF,
                  (val >> 16) & 0xFF, (val >> 24) & 0xFF])


def _pack_u16(val):
    return bytes([val & 0xFF, (val >> 8) & 0xFF])


def parse_binary_frame(buf):
    if len(buf) < 9:
        return -2
    if buf[0] != 0xAA or buf[1] != 0x55:
        return -1
    cmd = buf[2]
    seq = (buf[3] << 8) | buf[4]
    plen = (buf[5] << 8) | buf[6]
    frame_len = 9 + plen
    if len(buf) < frame_len:
        return -2
    crc_area = buf[2:7 + plen]
    exp_crc = crc16(crc_area)
    recv_crc = (buf[7 + plen] << 8) | buf[7 + plen + 1]
    if exp_crc != recv_crc:
        return -1
    payload = buf[7:7 + plen]
    return cmd, seq, payload, frame_len


def parse_audio_frame(buf):
    if len(buf) < 7:
        return -2
    if buf[0] != 0xAA or buf[1] != 0x55:
        return -1
    cmd = buf[2]
    plen = (buf[5] << 8) | buf[6]
    frame_len = 7 + plen
    if len(buf) < frame_len:
        return -2
    if cmd not in (0x30, 0x31):
        return -1
    if plen > 4096:
        return -1
    payload = buf[7:7 + plen]
    seq = (buf[3] << 8) | buf[4]
    return cmd, seq, payload, frame_len
