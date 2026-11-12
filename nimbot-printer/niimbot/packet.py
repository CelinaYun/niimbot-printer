"""NIIMBOT 封包格式与命令号。

封包布局（NiimbotPacket）::

    0x55 0x55 | CMD | LEN | DATA... | XOR | 0xAA 0xAA

``XOR`` = CMD ^ LEN ^ 每个 DATA 字节。Connect 命令整包额外前置 ``0x03``。
"""

from __future__ import annotations

from dataclasses import dataclass, field

HEAD = b"\x55\x55"
TAIL = b"\xaa\xaa"

# ---------------------------------------------------------------- 命令号（发→打印机）
CMD_PRINT_START = 0x01
CMD_PAGE_START = 0x03
CMD_PRINTER_INFO = 0x40
CMD_PRINT_QUANTITY = 0x15
CMD_SET_PAGE_SIZE = 0x13
CMD_PRINT_CLEAR = 0x20
CMD_SET_DENSITY = 0x21
CMD_SET_PRINT_SPEED = 0x22
CMD_SET_LABEL_TYPE = 0x23
CMD_PRINT_EMPTY_ROW = 0x84
CMD_PRINT_BITMAP_ROW = 0x85
CMD_PRINT_BITMAP_ROW_INDEXED = 0x83
CMD_PRINT_STATUS = 0xA3
CMD_PRINTER_STATUS_DATA = 0xA5
CMD_GET_PAPER_INFO = 0x59
CMD_CONNECT = 0xC1
CMD_HEARTBEAT = 0xDC
CMD_PAGE_END = 0xE3
CMD_PRINT_END = 0xF3

# ---------------------------------------------------------------- 命令号（打印机→收）
RX_NOT_SUPPORTED = 0x00
RX_PRINT_START = 0x02
RX_PAGE_START = 0x04
RX_PRINTER_INFO_DENSITY = 0x41
RX_PRINTER_INFO_SPEED = 0x42
RX_PRINTER_INFO_LABEL_TYPE = 0x43
RX_PRINTER_INFO_AUTO_SHUTDOWN = 0x47
RX_PRINTER_INFO_SERIAL = 0x4B
RX_PRINTER_INFO_MODEL_CODE = 0x48
RX_PRINTER_INFO_SOFT_VERSION = 0x49
RX_PRINTER_INFO_CHARGE = 0x4A
RX_PRINTER_INFO_HARD_VERSION = 0x4C
RX_PRINTER_INFO_BT_ADDR = 0x4D
RX_PRINT_QUANTITY = 0x16
RX_SET_PAGE_SIZE = 0x14
RX_PRINT_CLEAR = 0x30
RX_SET_DENSITY = 0x31
RX_SET_PRINT_SPEED = 0x32
RX_SET_LABEL_TYPE = 0x33
RX_PRINTER_STATUS_DATA = 0xB5
RX_PRINT_STATUS = 0xB3
RX_CONNECT = 0xC2
RX_HEARTBEAT_ADV1 = 0xDD
RX_HEARTBEAT_ADV2 = 0xD9
RX_HEARTBEAT_PRINTER_INFO = 0xDF
RX_PRINT_ERROR = 0xDB
RX_PRINTER_PAGE_INDEX = 0xE0
RX_PAGE_END = 0xE4
RX_PRINT_END = 0xF4
RX_GET_PAPER_INFO = 0x69

# PrinterInfo 查询类型（发给 CMD_PRINTER_INFO 的 data[0]）
INFO_DENSITY = 1
INFO_SPEED = 2
INFO_LABEL_TYPE = 3
INFO_LANGUAGE = 6
INFO_AUTO_SHUTDOWN = 7
INFO_MODEL_ID = 8
INFO_SOFT_VERSION = 9
INFO_BATTERY = 10
INFO_SERIAL = 11
INFO_HARD_VERSION = 12
INFO_BT_ADDR = 13

# Connect 应答码
CONNECT_DISCONNECT = 0
CONNECT_CONNECTED = 1
CONNECT_CONNECTED_NEW = 2
CONNECT_CONNECTED_V3 = 3
CONNECT_FIRMWARE_ERRORS = 90

# 心跳类型（发给 CMD_HEARTBEAT 的 data[0]）
HEARTBEAT_ADV1 = 1
HEARTBEAT_BASIC = 2
HEARTBEAT_PRINTER_INFO = 3
HEARTBEAT_ADV2 = 4

# 标签类型
LABEL_WITH_GAPS = 1
LABEL_BLACK = 2
LABEL_CONTINUOUS = 3
LABEL_PERFORATED = 4
LABEL_TRANSPARENT = 5
LABEL_PVC_TAG = 6

# 打印错误码
PRINT_ERRORS = {
    0x01: "上盖未合上",
    0x02: "缺纸",
    0x03: "电量不足",
    0x04: "电池异常",
    0x05: "用户取消",
    0x06: "数据错误",
    0x07: "打印头过热",
    0x08: "打印机忙",
    0x20: "纸张类型不匹配",
    0x34: "接收数据超时",
}

# 请求命令 → 期望应答命令。None 表示单向无应答。
RESPONSE_MAP: dict[int, list[int] | None] = {
    CMD_PRINT_BITMAP_ROW: None,
    CMD_PRINT_BITMAP_ROW_INDEXED: None,
    CMD_PRINT_EMPTY_ROW: None,
    CMD_PRINT_START: [RX_PRINT_START],
    CMD_PAGE_START: [RX_PAGE_START],
    CMD_PAGE_END: [RX_PAGE_END],
    CMD_PRINT_END: [RX_PRINT_END],
    CMD_PRINT_CLEAR: [RX_PRINT_CLEAR],
    CMD_PRINT_QUANTITY: [RX_PRINT_QUANTITY],
    CMD_SET_PAGE_SIZE: [RX_SET_PAGE_SIZE],
    CMD_SET_DENSITY: [RX_SET_DENSITY],
    CMD_SET_LABEL_TYPE: [RX_SET_LABEL_TYPE],
    CMD_PRINT_STATUS: [RX_PRINT_STATUS],
    CMD_CONNECT: [RX_CONNECT],
    CMD_PRINTER_INFO: [
        RX_PRINTER_INFO_DENSITY,
        RX_PRINTER_INFO_SPEED,
        RX_PRINTER_INFO_LABEL_TYPE,
        RX_PRINTER_INFO_AUTO_SHUTDOWN,
        RX_PRINTER_INFO_MODEL_CODE,
        RX_PRINTER_INFO_SOFT_VERSION,
        RX_PRINTER_INFO_CHARGE,
        RX_PRINTER_INFO_SERIAL,
        RX_PRINTER_INFO_HARD_VERSION,
        RX_PRINTER_INFO_BT_ADDR,
    ],
    CMD_PRINTER_STATUS_DATA: [RX_PRINTER_STATUS_DATA],
    CMD_GET_PAPER_INFO: [RX_GET_PAPER_INFO],
    CMD_HEARTBEAT: [RX_HEARTBEAT_ADV1, RX_HEARTBEAT_ADV2, RX_HEARTBEAT_PRINTER_INFO],
}

# 收到这些应答视为错误并抛出
_ERROR_RESPONSES = {RX_PRINT_ERROR, RX_NOT_SUPPORTED}


class PacketError(Exception):
    """封包解析失败。"""


@dataclass
class NiimbotPacket:
    command: int
    data: bytes = b""
    valid_response_ids: list[int] = field(default_factory=list)

    @property
    def one_way(self) -> bool:
        return RESPONSE_MAP.get(self.command) is None

    @property
    def checksum(self) -> int:
        c = self.command ^ len(self.data)
        for b in self.data:
            c ^= b
        return c & 0xFF

    def to_bytes(self) -> bytes:
        if len(self.data) > 255:
            raise PacketError(f"数据长度超限: {len(self.data)} > 255")
        out = (
            HEAD
            + bytes([self.command & 0xFF, len(self.data)])
            + self.data
            + bytes([self.checksum])
            + TAIL
        )
        if self.command == CMD_CONNECT:
            out = b"\x03" + out
        return out

    @classmethod
    def from_bytes(cls, buf: bytes) -> "NiimbotPacket":
        min_len = len(HEAD) + 1 + 1 + 1 + len(TAIL)
        if len(buf) < min_len:
            raise PacketError(f"封包过短: {len(buf)}")
        if buf[:2] != HEAD:
            raise PacketError("封包头错误")
        if buf[-2:] != TAIL:
            raise PacketError("封包尾错误")
        command = buf[2]
        data_len = buf[3]
        if len(buf) != min_len + data_len:
            raise PacketError(f"封包长度不符: {len(buf)} != {min_len + data_len}")
        data = buf[4 : 4 + data_len]
        checksum = buf[4 + data_len]
        pkt = cls(command, data)
        if pkt.checksum != checksum:
            raise PacketError(f"校验和错误: {pkt.checksum:#04x} != {checksum:#04x}")
        return pkt


def parse_packet_bundle(buf: bytes) -> tuple[list[NiimbotPacket], bytes]:
    """从字节流里尽量多地解析出完整封包。

    返回 ``(封包列表, 剩余未解析字节)``。剩余字节留给下次调用（BLE 通知会分片）。
    """
    packets: list[NiimbotPacket] = []
    pos = 0
    n = len(buf)

    while pos < n:
        # 跳到下一个包头
        if buf[pos] != HEAD[0]:
            pos += 1
            continue

        # 只剩一个 0x55 时它可能是被 BLE 分包切开的包头 —— 必须留着，
        # 否则下一个分片的 0x55 xx … 会整包丢失。
        if pos + 2 > n:
            break

        if buf[pos + 1] != HEAD[1]:
            pos += 1
            continue

        if pos + 4 > n:
            break  # 头部未收齐

        data_len = buf[pos + 3]
        total = len(HEAD) + 1 + 1 + data_len + 1 + len(TAIL)
        if pos + total > n:
            break  # 数据未收齐

        chunk = buf[pos : pos + total]
        try:
            packets.append(NiimbotPacket.from_bytes(chunk))
        except PacketError:
            pos += 1
            continue
        pos += total

    return packets, buf[pos:]


def u16be(value: int) -> bytes:
    return bytes([(value >> 8) & 0xFF, value & 0xFF])


def u16be_read(data: bytes, offset: int = 0) -> int:
    return (data[offset] << 8) | data[offset + 1]
