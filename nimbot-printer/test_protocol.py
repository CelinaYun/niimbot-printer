"""协议层自检：封包编解码、校验和、图像编码。"""

from niimbot import packet as pkt
from niimbot.encoder import count_pixels, encode_image, index_pixels, rows_to_packets


def test_fragmentation_never_loses_packets():
    """任意切分方式下都不能丢包 —— 特别是包头被切成 1 个字节时。

    BLE 通知会按 MTU 切分，包头 ``55 55`` 很可能跨分片；解析器必须把
    残留的 ``0x55`` 留到下次，否则整包丢失，表现就是莫名其妙的应答超时。
    """
    # 三个不同长度的包，制造出各种边界
    p1 = pkt.NiimbotPacket(pkt.CMD_CONNECT, b"\x01").to_bytes()
    p2 = pkt.NiimbotPacket(pkt.CMD_SET_PAGE_SIZE, bytes(range(13))).to_bytes()
    p3 = pkt.NiimbotPacket(pkt.RX_PRINT_STATUS, bytes(8)).to_bytes()
    stream = p1 + p2 + p3

    def feed(chunks):
        buf = b""
        got = []
        for c in chunks:
            buf += c
            pkts, buf = pkt.parse_packet_bundle(buf)
            got.extend(pkts)
        return got, buf

    # 1) 一次性给完
    got, left = feed([stream])
    assert len(got) == 3 and left == b""

    # 2) 逐字节喂（每一分片只有 1 字节）—— 最容易踩的场景
    got, left = feed([bytes([b]) for b in stream])
    assert len(got) == 3, f"逐字节丢失了包：{len(got)}"
    assert left == b""

    # 3) 专门在包头中间切断（前一分片以 0x55 结尾）
    for cut in range(1, len(stream)):
        got, left = feed([stream[:cut], stream[cut:]])
        assert len(got) == 3, f"在第 {cut} 字节切断时丢了包：{len(got)} / 剩 {left.hex()}"
        assert left == b""

    # 4) 随机切分
    import random

    random.seed(7)
    for _ in range(200):
        cuts = sorted(random.sample(range(1, len(stream)), k=random.randint(0, 6)))
        chunks, prev = [], 0
        for c in cuts + [len(stream)]:
            chunks.append(stream[prev:c])
            prev = c
        got, left = feed(chunks)
        assert len(got) == 3, f"随机切分丢包：{len(got)} 剩 {left.hex()}"


def test_packet_roundtrip():
    p = pkt.NiimbotPacket(0x4A, b"\x04")
    raw = p.to_bytes()
    assert raw == bytes.fromhex("5555 4a 01 04 4f aaaa".replace(" ", "")), raw.hex()
    (p2,), rest = pkt.parse_packet_bundle(raw)
    assert rest == b""
    assert p2.command == 0x4A and p2.data == b"\x04"

    p = pkt.NiimbotPacket(0xF6, b"\x01")
    raw = p.to_bytes()
    assert raw == bytes.fromhex("5555 f6 01 01 f6 aaaa".replace(" ", "")), raw.hex()


def test_bundle_split():
    raw = bytes.fromhex("55554a01044faaaa5555f60101f6aaaa")
    packets, rest = pkt.parse_packet_bundle(raw)
    assert len(packets) == 2 and rest == b""
    assert packets[0].command == 0x4A and packets[1].command == 0xF6

    # 半包：第二个包只到一半，应留到剩余字节里
    half = raw[: len(raw) - 3]
    packets, rest = pkt.parse_packet_bundle(half)
    assert len(packets) == 1
    assert rest == raw[8 : len(raw) - 3]

    # 噪声 + 两个包
    noisy = b"\x00\x11" + raw
    packets, rest = pkt.parse_packet_bundle(noisy)
    assert len(packets) == 2 and rest == b""


def test_connect_prefix():
    raw = pkt.NiimbotPacket(pkt.CMD_CONNECT, b"\x01").to_bytes()
    assert raw[0] == 0x03 and raw[1:3] == b"\x55\x55"


def test_pixel_count_split():
    # 96 点 = 12 字节，chunk_size = 4，拆 3 段
    data = bytes([0xFF] * 4 + [0x00] * 8)
    parts, total = count_pixels(data)
    assert total == 32
    assert parts == [32, 0, 0], parts

    data = bytes([0x00] * 4 + [0xFF] * 4 + [0x00] * 4)
    parts, total = count_pixels(data)
    assert total == 32 and parts == [0, 32, 0], parts


def test_index_pixels():
    # 0x80 0x00 → 只有第 0 个像素是黑的
    assert index_pixels(bytes([0x80, 0x00])) == bytes([0x00, 0x00])


def test_encode_identity_mapping():
    """自然竖排标签（宽=打印头，高=长度）逆时针转 90° 后编码，行/列应与原图一致。"""
    from PIL import Image

    W, H = 16, 4  # 16 点宽（打印头方向），4 点长（走纸方向）
    img = Image.new("1", (W, H), 1)  # 全白
    px = img.load()
    px[3, 2] = 0  # 原图 (x=3, y=2) 打黑点

    src = img.rotate(90, expand=True)  # 逆时针 90° → (4, 16)
    enc = encode_image(src, print_direction="left")
    assert enc.cols == 16, enc.cols  # 打印头方向 = 原图宽
    assert enc.rows == 4, enc.rows  # 走纸方向 = 原图高

    # 原图 (3,2) 应落在 encoded(row=2, x=3)
    row2 = next(r for r in enc.rows_data if r.row_number == 2)
    assert row2.row_data is not None
    # 第 3 号点 = 字节 0 的 bit (7-3) = 0x10
    assert row2.row_data[0] == 0x10, row2.row_data.hex()
    assert row2.black_pixels_count == 1


def test_rows_to_packets_sparse():
    """黑像素 ≤ 6 的行走稀疏索引包，空白行走 0x84 空行包。"""
    from PIL import Image

    img = Image.new("1", (8, 2), 1)
    px = img.load()
    px[0, 0] = 0  # 第 0 行单个黑点
    enc = encode_image(img, print_direction="top")
    pk = rows_to_packets(enc)
    assert len(pk) == 2, pk  # 一行位图 + 一行空白
    assert pk[0][0] == pkt.CMD_PRINT_BITMAP_ROW_INDEXED
    assert pk[1][0] == pkt.CMD_PRINT_EMPTY_ROW


if __name__ == "__main__":
    import sys

    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok  {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL  {name}: {e}")
    sys.exit(1 if failures else 0)
