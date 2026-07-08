_CRC16_TABLE = []
for _i in range(256):
    _crc = _i << 8
    for _ in range(8):
        if _crc & 0x8000:
            _crc = ((_crc << 1) ^ 0x1021) & 0xFFFF
        else:
            _crc = (_crc << 1) & 0xFFFF
    _CRC16_TABLE.append(_crc)


def crc16(data):
    crc = 0
    for b in data:
        crc = ((crc << 8) ^ _CRC16_TABLE[((crc >> 8) ^ b) & 0xFF]) & 0xFFFF
    return crc


_CRC32_TABLE = []
for _i in range(256):
    _crc = _i
    for _ in range(8):
        if _crc & 1:
            _crc = (_crc >> 1) ^ 0xEDB88320
        else:
            _crc = _crc >> 1
    _CRC32_TABLE.append(_crc)


def crc32_init():
    return 0xFFFFFFFF


def crc32_update(crc, data):
    for b in data:
        crc = _CRC32_TABLE[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc


def crc32_final(crc):
    return crc ^ 0xFFFFFFFF
