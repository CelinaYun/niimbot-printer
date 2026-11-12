"""集成测试：用假传输层跑完整握手 + 打印流程，校验命令序列。

不需要真实打印机，用来确认协议时序与参考实现一致。
"""

import asyncio

from niimbot import packet as pkt
from niimbot.client import NiimbotClient, PrintOptions
from niimbot.encoder import encode_image, prepare_source_image
from niimbot.transport import Transport


class FakeTransport(Transport):
    """记录上行封包，并按脚本回放下行应答。"""

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[pkt.NiimbotPacket] = []
        self.raw_sent: list[bytes] = []
        self.auto_reply = True
        self._printed_pages = 0
        self._print_started = False
        self._quantity = 1
        # 可配置的握手结果：型号 ID 与协议版本
        self.model_id = 0x0900  # 2304 = D110
        self.connect_result = pkt.CONNECT_CONNECTED_NEW  # → 协议 v1
        self.protocol_n = 210  # 用于 PrinterStatusData 的版本字段
        self.printhead_pixels = 96  # 打印头宽度（点）

    @property
    def kind(self) -> str:
        return "fake"

    async def open(self, target: str) -> str:
        return "FAKE-D110"

    async def close(self) -> None:
        pass

    async def write(self, data: bytes) -> None:
        self.raw_sent.append(data)
        packets, _ = pkt.parse_packet_bundle(data)
        for p in packets:
            self.sent.append(p)
            if self.auto_reply:
                self._auto_reply(p)

    def _reply(self, command: int, data: bytes = b"\x01") -> None:
        p = pkt.NiimbotPacket(command, data)
        self._deliver(p.to_bytes())

    def _auto_reply(self, req: pkt.NiimbotPacket) -> None:
        c = req.command

        if c == pkt.CMD_CONNECT:
            self._reply(pkt.RX_CONNECT, bytes([self.connect_result]))
        elif c == pkt.CMD_PRINTER_STATUS_DATA:
            body = bytearray(13)
            body[10] = 0
            body[11] = self.protocol_n // 100
            body[12] = self.protocol_n % 100
            self._reply(pkt.RX_PRINTER_STATUS_DATA, bytes(body))
        elif c == pkt.CMD_GET_PAPER_INFO:
            # 30 mm 长 × 15 mm 宽、间隙 2 mm 的标签，203 dpi
            d = bytearray(18)
            d[0:2] = pkt.u16be(16)        # gapHeightPixel
            d[2:4] = pkt.u16be(240)       # totalHeightPixel (30 mm)
            d[4] = 1                       # paperType
            d[5:7] = pkt.u16be(20)        # gapHeight 2.0 mm
            d[7:9] = pkt.u16be(300)       # totalHeight 30.0 mm
            d[9:11] = pkt.u16be(120)      # paperWidthPixel (15 mm)
            d[11:13] = pkt.u16be(150)     # paperWidth 15.0 mm
            d[13] = 0
            d[14:16] = pkt.u16be(0)
            d[16:18] = pkt.u16be(0)
            self._reply(pkt.RX_GET_PAPER_INFO, bytes(d))
        elif c == pkt.CMD_HEARTBEAT and req.data[:1] == bytes([pkt.HEARTBEAT_PRINTER_INFO]):
            d = bytearray(10)
            d[0:2] = pkt.u16be(0x0100)   # hw
            d[2:4] = pkt.u16be(0x0A55)   # fw
            d[4:6] = pkt.u16be(self.printhead_pixels)  # printheadWidth
            d[6] = 1                      # resolutionClass
            d[7] = 0                      # printheadAlignment
            d[8] = 1                      # supportsRFID
            d[9] = 0                      # supportsWriteRFID
            self._reply(pkt.RX_HEARTBEAT_PRINTER_INFO, bytes(d))
        elif c == pkt.CMD_PRINTER_INFO:
            info_type = req.data[0] if req.data else 0
            mid = self.model_id
            table = {
                pkt.INFO_MODEL_ID: (pkt.RX_PRINTER_INFO_MODEL_CODE,
                                    bytes([(mid >> 8) & 0xFF, mid & 0xFF])),
                pkt.INFO_BATTERY: (pkt.RX_PRINTER_INFO_CHARGE, bytes([3])),
                pkt.INFO_SOFT_VERSION: (pkt.RX_PRINTER_INFO_SOFT_VERSION, bytes([0x01, 0x2C])),
            }
            resp_cmd, resp_data = table.get(info_type, (pkt.RX_PRINTER_INFO_MODEL_CODE, b"\x01"))
            self._reply(resp_cmd, resp_data)
        elif c == pkt.CMD_PRINT_START:
            self._print_started = True
            self._reply(pkt.RX_PRINT_START)
        elif c == pkt.CMD_PRINT_CLEAR:
            self._reply(pkt.RX_PRINT_CLEAR)
        elif c == pkt.CMD_PAGE_START:
            self._reply(pkt.RX_PAGE_START)
        elif c == pkt.CMD_SET_PAGE_SIZE:
            self._reply(pkt.RX_SET_PAGE_SIZE)
        elif c == pkt.CMD_PRINT_QUANTITY:
            self._quantity = pkt.u16be_read(req.data) if len(req.data) >= 2 else 1
            self._reply(pkt.RX_PRINT_QUANTITY)
        elif c == pkt.CMD_SET_DENSITY:
            self._reply(pkt.RX_SET_DENSITY)
        elif c == pkt.CMD_SET_LABEL_TYPE:
            self._reply(pkt.RX_SET_LABEL_TYPE)
        elif c == pkt.CMD_PAGE_END:
            self._printed_pages += 1
            self._reply(pkt.RX_PAGE_END)
        elif c == pkt.CMD_PRINT_STATUS:
            # 打印已全部完成，上报总页数
            self._reply(pkt.RX_PRINT_STATUS, pkt.u16be(self._quantity) + bytes([100, 100, 0, 0, 0, 0, 0]))
        elif c == pkt.CMD_PRINT_END:
            self._reply(pkt.RX_PRINT_END)
        elif c in (pkt.CMD_PRINT_BITMAP_ROW, pkt.CMD_PRINT_BITMAP_ROW_INDEXED, pkt.CMD_PRINT_EMPTY_ROW):
            pass  # 单向


def _cmds(t: FakeTransport) -> list[int]:
    return [p.command for p in t.sent]


def test_full_print_flow():
    async def run():
        t = FakeTransport()
        client = NiimbotClient(t)

        info = await client.connect("FAKE")
        assert info.model_id == 0x0900, info.model_id
        assert info.battery == 75, info.battery  # 3 → 3*25

        from PIL import Image

        img = Image.new("L", (96, 60), 255)
        for x in range(20, 70):
            img.putpixel((x, 30), 0)

        await client.print_image(img, PrintOptions(quantity=2, density=2), landscape=False)
        return t

    t = asyncio.run(run())
    commands = _cmds(t)

    # 1. 握手与取信息
    assert commands[0] == pkt.CMD_CONNECT
    assert pkt.CMD_PRINTER_INFO in commands

    # 2. D110 打印任务顺序（与 NiimPrintX 对齐：不发 PrintClear）
    start = commands.index(pkt.CMD_PAGE_START)
    print_slice = commands[start:]
    assert pkt.CMD_PRINT_CLEAR not in commands, "不应发送 PrintClear（会导致重复出纸）"
    expected_prefix = [
        pkt.CMD_PAGE_START,
        pkt.CMD_SET_PAGE_SIZE,
        pkt.CMD_PRINT_QUANTITY,
    ]
    assert print_slice[:3] == expected_prefix, print_slice[:6]

    # 3. 中间必须有位图行，最后 PageEnd → 状态轮询 → PrintEnd
    assert pkt.CMD_PRINT_BITMAP_ROW in print_slice
    assert pkt.CMD_PRINT_EMPTY_ROW in print_slice
    assert print_slice[-1] == pkt.CMD_PRINT_END
    assert pkt.CMD_PAGE_END in print_slice
    assert pkt.CMD_PRINT_STATUS in print_slice

    # 4. PrintStart / SetDensity / SetLabelType 必须在 PageStart 之前
    head = commands[:start]
    assert pkt.CMD_PRINT_START in head
    assert pkt.CMD_SET_DENSITY in head
    assert pkt.CMD_SET_LABEL_TYPE in head

    # 5. SetPageSize 数据：rows=60, cols=96
    page_size = next(p for p in t.sent if p.command == pkt.CMD_SET_PAGE_SIZE)
    assert pkt.u16be_read(page_size.data) == 60
    assert pkt.u16be_read(page_size.data, 2) == 96

    # 6. PrintQuantity = 2
    qty = next(p for p in t.sent if p.command == pkt.CMD_PRINT_QUANTITY)
    assert pkt.u16be_read(qty.data) == 2

    # 7. 每行单独成包、repeat=1（与 NiimPrintX 一致）
    row_packets = [
        p for p in t.sent
        if p.command in (pkt.CMD_PRINT_BITMAP_ROW, pkt.CMD_PRINT_BITMAP_ROW_INDEXED, pkt.CMD_PRINT_EMPTY_ROW)
    ]
    assert len(row_packets) == 60, f"应为逐行 60 包，实际 {len(row_packets)}"
    for p in row_packets:
        if p.command == pkt.CMD_PRINT_EMPTY_ROW:
            assert p.data[2] == 1, f"empty row repeat 应为 1: {p.data.hex()}"
        else:
            assert p.data[5] == 1, f"bitmap row repeat 应为 1: {p.data.hex()}"

    print("  ok  test_full_print_flow  (sent", len(t.sent), "packets)")


def test_print_error_propagates():
    async def run():
        t = FakeTransport()

        async def write_and_fail(data: bytes) -> None:
            t.raw_sent.append(data)
            packets, _ = pkt.parse_packet_bundle(data)
            for p in packets:
                t.sent.append(p)
                if p.command == pkt.CMD_PAGE_START:
                    # 回一个「缺纸」错误
                    t._deliver(pkt.NiimbotPacket(pkt.RX_PRINT_ERROR, bytes([0x02])).to_bytes())
                else:
                    t._auto_reply(p)

        t.write = write_and_fail  # type: ignore[method-assign]
        client = NiimbotClient(t)

        from PIL import Image

        img = Image.new("L", (96, 40), 255)
        try:
            await client.print_image(img, PrintOptions())
        except Exception as e:  # noqa: BLE001
            assert "缺纸" in str(e), e
            return
        raise AssertionError("应当抛出缺纸错误")

    asyncio.run(run())
    print("  ok  test_print_error_propagates")


def test_prepare_rotation_identity():
    """自然竖排标签经默认旋转后编码，行列与原图一一对应。"""
    from PIL import Image

    img = Image.new("L", (16, 8), 255)
    img.putpixel((5, 3), 0)
    src = prepare_source_image(img)  # 逆时针 90°
    enc = encode_image(src, "left")
    assert enc.cols == 16 and enc.rows == 8, (enc.cols, enc.rows)

    row3 = next(r for r in enc.rows_data if r.row_number == 3)
    assert row3.row_data is not None
    # col=5 → 字节 0 的 bit (7-5)=2 → 0x04
    assert row3.row_data[0] == 0x04, row3.row_data.hex()
    assert row3.black_pixels_count == 1
    print("  ok  test_prepare_rotation_identity")


def test_landscape_identity_mapping():
    """横版画布经上下翻转后编码，row/x 与画布像素一一对应（预览即出纸）。"""
    from PIL import Image

    from niimbot.encoder import prepare_landscape_source

    canvas = Image.new("L", (20, 16), 255)  # 宽=长度 20，高=打印头 16
    canvas.putpixel((7, 3), 0)  # 横版坐标 (x=7 走纸, y=3 打印头)
    canvas.putpixel((12, 9), 0)

    src = prepare_landscape_source(canvas)
    enc = encode_image(src, "left")
    assert enc.cols == 16 and enc.rows == 20, (enc.cols, enc.rows)

    def bit_at(row, col):
        # 合并行以 row_number 起、repeat 跨度覆盖
        r = next(x for x in enc.rows_data
                 if x.row_number <= row < x.row_number + x.repeat)
        if r.row_data is None:
            return 0
        byte = r.row_data[col // 8]
        return 1 if byte & (1 << (7 - (col % 8))) else 0

    assert bit_at(7, 3) == 1, "canvas(7,3) 应映射到 encoded(row=7, x=3)"
    assert bit_at(12, 9) == 1, "canvas(12,9) 应映射到 encoded(row=12, x=9)"
    assert bit_at(7, 4) == 0 and bit_at(11, 9) == 0
    print("  ok  test_landscape_identity_mapping")


def test_landscape_render_and_print():
    """横版文字渲染 + 打印全流程：不再发 PrintClear，每行 repeat=1。"""
    from render import mm_to_px, render_landscape_label

    strip_dots, length_dots = mm_to_px(12), mm_to_px(40)
    res = render_landscape_label(
        text="资产编号 NB-2026-001\n负责人 张三",
        strip_dots=strip_dots,
        length_dots=length_dots,
        font_size=26,
        bold=True,
        align="left",
    )
    assert res.image.size == (length_dots, strip_dots), res.image.size

    async def run():
        t = FakeTransport()
        client = NiimbotClient(t)
        await client.connect("FAKE")
        await client.print_image(res.image, PrintOptions(quantity=1), landscape=True)
        return t

    t = asyncio.run(run())
    commands = _cmds(t)
    assert pkt.CMD_PRINT_CLEAR not in commands
    rows = [p for p in t.sent if p.command in (
        pkt.CMD_PRINT_BITMAP_ROW, pkt.CMD_PRINT_BITMAP_ROW_INDEXED, pkt.CMD_PRINT_EMPTY_ROW)]
    assert len(rows) == length_dots, f"应逐行 {length_dots} 包，实际 {len(rows)}"

    page_size = next(p for p in t.sent if p.command == pkt.CMD_SET_PAGE_SIZE)
    assert pkt.u16be_read(page_size.data) == length_dots, "rows 应等于标签长度"
    assert pkt.u16be_read(page_size.data, 2) == strip_dots, "cols 应等于打印头点数"
    print(f"  ok  test_landscape_render_and_print  ({length_dots} 行)")


class SilentTransport(FakeTransport):
    """不回任何应答的假传输，用来触发握手超时。"""

    def _auto_reply(self, req: pkt.NiimbotPacket) -> None:
        pass


class FakeSerialTransport(SilentTransport):
    """假串口：可预置 #10001 探测回包。"""

    def __init__(self, probe_reply: bytes = b"") -> None:
        super().__init__()
        self.probe_reply = probe_reply
        self.probe_sent: list[bytes] = []

    @property
    def kind(self) -> str:
        return "serial"

    async def raw_exchange(self, payload: bytes, wait: float = 1.5) -> bytes:
        self.probe_sent.append(payload)
        return self.probe_reply


def test_timeout_error_has_message():
    """握手超时时错误信息必须有内容，不能是空白。"""

    async def run():
        client = NiimbotClient(SilentTransport())
        try:
            await client.connect("X")
        except Exception as e:  # noqa: BLE001
            return e
        raise AssertionError("应当失败")

    e = asyncio.run(run())
    text = str(e).strip()
    assert text, "错误信息不能为空"
    assert "超时" in text or "握手失败" in text, text
    print("  ok  test_timeout_error_has_message")


def test_serial_probe_detects_charge_mode():
    """串口探测到 #10001 应识别为关机充电模式的 Niimbot。"""

    async def run():
        t = FakeSerialTransport(b"#10001:V13.10,D110-G326030306,1\r\n")
        client = NiimbotClient(t)
        try:
            await client.connect("COM9")
        except Exception as e:  # noqa: BLE001
            return t, str(e)
        raise AssertionError("应当失败")

    t, text = asyncio.run(run())
    assert t.probe_sent == [b"#10001\r\n"], t.probe_sent
    assert "D110-G326030306" in text, text
    assert "关机充电模式" in text, text
    assert text.strip(), "错误信息不能为空"
    print("  ok  test_serial_probe_detects_charge_mode")


def test_serial_probe_silent_gives_guidance():
    """串口无回音时应给出排查建议。"""

    async def run():
        t = FakeSerialTransport(b"")
        client = NiimbotClient(t)
        try:
            await client.connect("COM9")
        except Exception as e:  # noqa: BLE001
            return str(e)
        raise AssertionError("应当失败")

    text = asyncio.run(run())
    assert "串口完全没有回音" in text, text
    assert "蓝牙" in text, text
    print("  ok  test_serial_probe_silent_gives_guidance")


def _page_size_payload(p: pkt.NiimbotPacket) -> bytes:
    return p.data


def _run_print(model_id: int, connect_result: int, protocol_n: int, quantity: int = 1):
    """跑一次完整打印，返回 (client, fake)。"""
    from PIL import Image

    async def run():
        t = FakeTransport()
        t.model_id = model_id
        t.connect_result = connect_result
        t.protocol_n = protocol_n
        client = NiimbotClient(t)
        await client.connect("FAKE")
        img = Image.new("L", (200, 96), 255)  # 横版：宽=长度 200，高=96
        await client.print_image(img, PrintOptions(quantity=quantity), landscape=True)
        return client, t

    return asyncio.run(run())


def test_task_selection():
    """型号 + 协议版本 → 打印流程的映射。"""
    from niimbot.client import resolve_print_task

    assert resolve_print_task(2304, 1) == "D110"      # D110
    assert resolve_print_task(2305, 1) == "D110"      # D110
    assert resolve_print_task(2320, 1) == "B1"        # D110_M 老协议
    assert resolve_print_task(2320, 3) == "B1"        # D110_M v3
    assert resolve_print_task(2320, 4) == "D110M_V4"  # D110_M v4+
    assert resolve_print_task(2320, 5) == "D110M_V4"
    assert resolve_print_task(9999, 4) == "D110M_V4"  # 未知型号新协议
    assert resolve_print_task(9999, 1) == "D110"      # 未知型号老协议
    assert resolve_print_task(2320, 4, override="B1") == "B1"  # 手动指定优先
    print("  ok  test_task_selection")


def test_d110_m_uses_b1_flow():
    """D110_M（2320）必须走 B1 流程：printStart7b + setPageSize6b，不发 PrintQuantity。"""
    client, t = _run_print(2320, pkt.CONNECT_CONNECTED_V3, protocol_n=210)
    assert client.info.print_task == "B1", client.info.print_task

    start = next(p for p in t.sent if p.command == pkt.CMD_PRINT_START)
    assert len(start.data) == 7, f"printStart7b 应为 7 字节，实际 {len(start.data)}"
    assert pkt.u16be_read(start.data) == 1, "总页数应为 1"

    page_size = next(p for p in t.sent if p.command == pkt.CMD_SET_PAGE_SIZE)
    assert len(page_size.data) == 6, f"setPageSize6b 应为 6 字节，实际 {len(page_size.data)}"
    assert pkt.u16be_read(page_size.data) == 200      # rows
    assert pkt.u16be_read(page_size.data, 2) == 96    # cols
    assert pkt.u16be_read(page_size.data, 4) == 1     # copies

    assert pkt.CMD_PRINT_QUANTITY not in [p.command for p in t.sent], "B1 流程不发 PrintQuantity"
    assert pkt.CMD_PRINT_CLEAR not in [p.command for p in t.sent]
    print("  ok  test_d110_m_uses_b1_flow")


def test_d110_m_v4_flow():
    """D110_M + 协议 v4 → D110M_V4 流程：printStart9b + setPageSize13b。"""
    client, t = _run_print(2320, pkt.CONNECT_CONNECTED_V3, protocol_n=301)
    assert client.info.print_task == "D110M_V4", client.info.print_task
    assert client.info.protocol_version == 4, client.info.protocol_version

    start = next(p for p in t.sent if p.command == pkt.CMD_PRINT_START)
    assert len(start.data) == 9, f"printStart9b 应为 9 字节，实际 {len(start.data)}"

    page_size = next(p for p in t.sent if p.command == pkt.CMD_SET_PAGE_SIZE)
    assert len(page_size.data) == 13, f"setPageSize13b 应为 13 字节，实际 {len(page_size.data)}"
    assert pkt.u16be_read(page_size.data) == 200
    assert pkt.u16be_read(page_size.data, 2) == 96

    assert pkt.CMD_PRINT_QUANTITY not in [p.command for p in t.sent]
    print("  ok  test_d110_m_v4_flow")


def test_d110_keeps_old_flow():
    """真 D110（2304）仍走 D110 流程：setPageSize4b + PrintQuantity。"""
    client, t = _run_print(2304, pkt.CONNECT_CONNECTED_NEW, protocol_n=210)
    assert client.info.print_task == "D110", client.info.print_task

    start = next(p for p in t.sent if p.command == pkt.CMD_PRINT_START)
    assert len(start.data) == 1, f"printStart1b 应为 1 字节，实际 {len(start.data)}"

    page_size = next(p for p in t.sent if p.command == pkt.CMD_SET_PAGE_SIZE)
    assert len(page_size.data) == 4, f"setPageSize4b 应为 4 字节，实际 {len(page_size.data)}"

    assert pkt.CMD_PRINT_QUANTITY in [p.command for p in t.sent], "D110 流程要发 PrintQuantity"
    print("  ok  test_d110_keeps_old_flow")


def test_rendered_text_produces_black_pixels():
    """渲染出来的文字必须真的编码成黑像素，排除「整张空白」的可能。"""
    from PIL import Image

    from niimbot.encoder import prepare_landscape_source
    from render import mm_to_px, render_landscape_label

    strip_dots, length_dots = mm_to_px(12), mm_to_px(40)
    res = render_landscape_label(
        text="资产编号 NB-2026-001\n负责人 张三",
        strip_dots=strip_dots, length_dots=length_dots,
        font_size=26, bold=True, align="left",
    )
    # 原始画布上必须有黑点
    px = res.image.load()
    black = sum(1 for y in range(strip_dots) for x in range(length_dots) if px[x, y] < 128)
    assert black > 200, f"画布黑像素太少：{black}"

    enc = encode_image(prepare_landscape_source(res.image), "left")
    assert enc.rows == length_dots and enc.cols == strip_dots

    # 编码后的行数据里也必须有黑像素
    encoded_black = 0
    non_empty = 0
    for r in enc.rows_data:
        if r.row_data is not None:
            non_empty += 1
            encoded_black += sum(bin(b).count("1") for b in r.row_data)
    assert non_empty > 10, f"非空白行段太少：{non_empty}"
    assert encoded_black > 200, f"编码后黑像素太少：{encoded_black}"
    print(f"  ok  test_rendered_text_produces_black_pixels  "
          f"(画布 {black} 点 / 编码 {encoded_black} 点 / {non_empty} 段)")


def test_single_line_rows_have_incrementing_positions():
    """展开合并行时，每包的行号必须递增——这是空白打印的根因回归测试。

    连续相同的行会被合并成 repeat=N 的一段；如果拆开后每包都用段首行号，
    打印机就会在同一行反复加热、行号不前进，出纸空白或糊成一片。
    """
    from PIL import Image

    from niimbot.encoder import encode_image, rows_to_packets

    # 8 行全黑 + 4 行全白 + 2 行全黑，制造明显的合并段
    img = Image.new("L", (16, 14), 255)
    for y in range(8):
        for x in range(16):
            img.putpixel((x, y), 0)
    for y in range(12, 14):
        for x in range(16):
            img.putpixel((x, y), 0)

    enc = encode_image(img, print_direction="top")
    # 合并后应是 3 段：黑 8 行、白 4 行、黑 2 行
    assert len(enc.rows_data) == 3, [(r.row_number, r.repeat) for r in enc.rows_data]
    assert [r.repeat for r in enc.rows_data] == [8, 4, 2]

    packets = rows_to_packets(enc, single_line_rows=True)
    assert len(packets) == 14, f"应展开成 14 包，实际 {len(packets)}"

    positions = []
    for cmd, data in packets:
        if cmd == pkt.CMD_PRINT_EMPTY_ROW:
            positions.append(pkt.u16be_read(data))
        else:
            positions.append(pkt.u16be_read(data))
    assert positions == list(range(14)), f"行号必须逐包递增：{positions}"

    # 不展开时，段首行号 + repeat 才是合法的
    merged = rows_to_packets(enc, single_line_rows=False)
    merged_pos = [(pkt.u16be_read(d), d[5] if len(d) > 5 else d[2]) for _, d in merged]
    assert merged_pos == [(0, 8), (8, 4), (12, 2)], merged_pos
    print(f"  ok  test_single_line_rows_have_incrementing_positions  ({positions[:4]}…)")


def test_alignment_centers_the_ink():
    """居中必须对准字的实际墨迹中心，而不是字形原点——否则会整体偏右。"""
    from render import mm_to_px, render_landscape_label

    strip_dots, length_dots = mm_to_px(12), mm_to_px(40)

    def ink_span(text, align):
        res = render_landscape_label(text=text, strip_dots=strip_dots,
                                     length_dots=length_dots, font_size=28,
                                     align=align)
        px = res.image.load()
        cols = [x for y in range(strip_dots) for x in range(length_dots)
                if px[x, y] < 128]
        return (min(cols), max(cols)) if cols else None

    for text in ("中", "居中测试", "ABC", "资产编号 NB-2026-001"):
        left = ink_span(text, "left")
        center = ink_span(text, "center")
        right = ink_span(text, "right")
        assert left and center and right, text

        # 居中：墨迹中心应对准标签中心
        mid = (center[0] + center[1]) / 2
        assert abs(mid - length_dots / 2) <= 2, f"{text!r} 居中偏了：{mid} vs {length_dots / 2}"

        # 左对齐：墨迹左沿应贴着边距
        assert left[0] <= 8, f"{text!r} 左对齐离左边太远：{left[0]}"
        # 右对齐：墨迹右沿应贴着右边距
        assert right[1] >= length_dots - 9, f"{text!r} 右对齐离右边太远：{right[1]}"
    print(f"  ok  test_alignment_centers_the_ink  （标签中心 {length_dots / 2}）")


def test_preview_matches_print_transform():
    """预览和出纸必须是同一张图，否则就是「所见非所得」。"""
    from PIL import Image

    from niimbot.encoder import ORIENTATIONS, apply_orientation, encode_image, simulate_output

    canvas = Image.new("L", (20, 16), 255)
    canvas.putpixel((3, 5), 0)

    for name in ORIENTATIONS:
        src = apply_orientation(canvas, name)
        enc = encode_image(src, "left")
        assert enc.rows == 20 and enc.cols == 16, name
        # 链路末端整体转 180°，这就是纸上的样子；预览必须与之逐像素一致
        printed = src.rotate(180, expand=True)
        assert printed.tobytes() == simulate_output(canvas, name).tobytes(), name
    print(f"  ok  test_preview_matches_print_transform  （{len(ORIENTATIONS)} 种取向一致）")


def test_paper_detection():
    """连接时要能从机器读回打印头宽度和纸张规格（30×15mm 那种）。"""

    async def run():
        t = FakeTransport()
        t.printhead_pixels = 120          # 15 mm 宽的打印头
        t.model_id = 2320
        t.connect_result = pkt.CONNECT_CONNECTED_V3
        t.protocol_n = 210
        client = NiimbotClient(t)
        await client.connect("COM9")
        return client

    client = asyncio.run(run())

    assert client.info.printhead_pixels == 120, client.info.printhead_pixels
    p = client.info.paper
    assert p is not None and p.valid, "应读到 RFID 纸张信息"
    assert abs(p.paper_width_mm - 15.0) < 0.01, p.paper_width_mm
    assert abs(p.paper_height_mm - 30.0) < 0.01, p.paper_height_mm
    assert abs(p.gap_mm - 2.0) < 0.01, p.gap_mm
    assert p.width_pixels == 120 and p.height_pixels == 240, (p.width_pixels, p.height_pixels)

    # 打印头宽度必须真的用到打包里
    from niimbot.encoder import encode_image, rows_to_packets

    from PIL import Image

    img = Image.new("L", (120, 10), 255)
    enc = encode_image(img, "top")
    assert enc.cols == 120
    print(f"  ok  test_paper_detection  （{p.paper_width_mm:.0f}×{p.paper_height_mm:.0f} mm，"
          f"打印头 {client.info.printhead_pixels} 点）")


def test_paper_detection_fails_gracefully():
    """纸卷不带 RFID 时要退回默认值，不能崩。"""

    async def run():
        t = FakeTransport()

        async def no_paper(data):
            t.raw_sent.append(data)
            packets, _ = pkt.parse_packet_bundle(data)
            for p in packets:
                t.sent.append(p)
                if p.command == pkt.CMD_GET_PAPER_INFO:
                    continue  # 不回
                t._auto_reply(p)

        t.write = no_paper  # type: ignore[method-assign]
        client = NiimbotClient(t)
        await client.connect("COM9")
        return client

    client = asyncio.run(run())
    assert client.info.printhead_pixels == 96  # 退回型号默认
    assert client.info.paper is not None and not client.info.paper.valid
    print("  ok  test_paper_detection_fails_gracefully")


def test_settings_persistence():
    """设置存取要能往返，且损坏文件不导致崩溃。"""
    import os
    import tempfile

    import settings

    base = tempfile.mkdtemp()
    path = os.path.join(base, settings.FILENAME)
    assert not os.path.exists(path)

    # 缺文件 → 默认值
    data = settings.load(base)
    assert data["font_size"] == 28 and data["label_length_mm"] == 40.0

    # 存了要能读回来
    settings.save({"font_size": 33, "orientation": "正常", "text": "你好"}, base)
    data = settings.load(base)
    assert data["font_size"] == 33
    assert data["orientation"] == "正常"
    assert data["text"] == "你好"
    assert data["bold"] == settings.DEFAULTS["bold"]  # 未指定的走默认

    # 未知字段不会带进去
    settings.save({"font_size": 20, "evil": "x"}, base)
    data = settings.load(base)
    assert "evil" not in data

    # 损坏的文件 → 回退默认，不抛异常
    with open(path, "w", encoding="utf-8") as f:
        f.write("{ 这不是合法 json")
    data = settings.load(base)
    assert data["font_size"] == settings.DEFAULTS["font_size"]

    # 空文件 / 空对象
    open(path, "w").close()
    assert settings.load(base)["font_size"] == settings.DEFAULTS["font_size"]
    with open(path, "w", encoding="utf-8") as f:
        f.write("{}")
    assert settings.load(base)["text"] == settings.DEFAULTS["text"]
    print("  ok  test_settings_persistence")


def test_generation_failures_surface_as_warnings():
    """条码/二维码/图片生成失败必须报出来，不能静默消失。"""
    from render import mm_to_px, render_landscape_label

    strip_dots, length_dots = mm_to_px(12), mm_to_px(40)

    # EAN-13 填了字母 → 条码生成失败，必须有警示
    res = render_landscape_label(text="测试", strip_dots=strip_dots,
                                 length_dots=length_dots,
                                 barcode_value="NOT-DIGITS",
                                 barcode_symbology="ean13")
    assert any("条码生成失败" in w for w in res.warnings), res.warnings

    # 图片路径不存在 → 必须有警示
    res = render_landscape_label(text="测试", strip_dots=strip_dots,
                                 length_dots=length_dots,
                                 image_path="Z:/没有这个文件.png")
    assert any("图片" in w for w in res.warnings), res.warnings

    # 全空内容 → 提示会打出空白标签
    res = render_landscape_label(text="", strip_dots=strip_dots,
                                 length_dots=length_dots)
    assert any("空白标签" in w for w in res.warnings), res.warnings

    # 正常的二维码不该有警示
    res = render_landscape_label(text="测试", strip_dots=strip_dots,
                                 length_dots=length_dots, qr_value="https://a.b")
    assert not any("失败" in w for w in res.warnings), res.warnings
    print("  ok  test_generation_failures_surface_as_warnings")


def test_font_size_boundary_warnings():
    """字号太大 / 行数太多 / 单行过长都必须给出可操作的警示。"""
    from render import mm_to_px, render_landscape_label

    strip_dots, length_dots = mm_to_px(12), mm_to_px(40)

    # 1) 正常内容：不该有任何警示
    ok = render_landscape_label(
        text="资产编号 NB-2026-001", strip_dots=strip_dots,
        length_dots=length_dots, font_size=28)
    assert ok.warnings == [], ok.warnings
    assert ok.font_used == 28

    # 2) 字号超过标签宽度
    big = render_landscape_label(
        text="大", strip_dots=strip_dots, length_dots=length_dots, font_size=120)
    assert any("太大" in w for w in big.warnings), big.warnings
    assert big.font_used <= strip_dots, big.font_used

    # 3) 行数超出：12mm 纸在字号 28 时最多 2 行
    many = render_landscape_label(
        text="一\n二\n三\n四\n五", strip_dots=strip_dots,
        length_dots=length_dots, font_size=28)
    assert any("行放不下" in w for w in many.warnings), many.warnings
    assert many.font_used < 28, many.font_used  # 自动缩字号了

    # 4) 单行过长
    long_line = render_landscape_label(
        text="这一行文字真的非常非常长远远超出了四十毫米标签能放下的字数",
        strip_dots=strip_dots, length_dots=length_dots, font_size=28)
    # 超长行现在会先把字号缩到放得下；缩到底（8 号）仍超才提示「过长」
    assert long_line.font_used < 28, long_line.font_used
    assert not any("过长" in w for w in long_line.warnings), long_line.warnings

    # 5) 容量提示要有内容
    assert "每行约" in ok.capacity and "行" in ok.capacity, ok.capacity
    print(f"  ok  test_font_size_boundary_warnings  （正常时容量：{ok.capacity}）")


def test_orientation_transforms():
    """四种取向：模拟出的「纸上效果」必须符合选项名的字面意思。

    这台机器链路末端整体转 180°，所以预览 = 转 180°(源图)。
    这个测试锁住「用户看到什么」这一层，不再验证内部编码坐标。
    """
    from PIL import Image

    from niimbot.encoder import ORIENTATIONS, simulate_output

    L, S = 20, 16
    canvas = Image.new("L", (L, S), 255)
    canvas.putpixel((3, 5), 0)  # 左上角放一个黑点，四种翻转各落一处

    expect = {
        "正常": canvas,
        "左右翻转": canvas.transpose(Image.Transpose.FLIP_LEFT_RIGHT),
        "上下翻转": canvas.transpose(Image.Transpose.FLIP_TOP_BOTTOM),
        "旋转 180°": canvas.rotate(180, expand=True),
    }

    assert set(ORIENTATIONS) == set(expect), ORIENTATIONS
    for name in ORIENTATIONS:
        got = simulate_output(canvas, name)
        assert got.tobytes() == expect[name].tobytes(), f"{name} 的出纸效果不对"
    print("  ok  test_orientation_transforms")


def test_diagnostic_label_blocks():
    """诊断页必须包含全黑块、条纹、文字，才能用来定位问题。"""
    from render import mm_to_px, render_diagnostic_label

    strip_dots, length_dots = mm_to_px(12), mm_to_px(40)
    img = render_diagnostic_label(strip_dots, length_dots)
    assert img.size == (length_dots, strip_dots)

    px = img.load()

    def black_in(x0, x1):
        return sum(1 for y in range(strip_dots) for x in range(x0, x1) if px[x, y] < 128)

    seg = length_dots // 5
    # 第 1 段必须几乎全黑
    first = black_in(0, seg)
    assert first > strip_dots * seg * 0.95, f"全黑段太浅：{first}/{strip_dots * seg}"
    # 后面几段要有黑有白
    for i in range(1, 4):
        x0 = i * (seg + 2)
        n = black_in(x0, min(x0 + seg, length_dots))
        assert 0 < n < strip_dots * seg, f"第 {i+1} 段黑白不对：{n}"
    print(f"  ok  test_diagnostic_label_blocks  (全黑段 {first} 点)")


def test_trace_records_call_sequence():
    """调用记录必须包含握手、打印流程选择和关键命令。"""
    import os
    import tempfile

    from niimbot import trace as trace_mod
    from render import mm_to_px, render_landscape_label

    log_path = os.path.join(tempfile.gettempdir(), "niimbot_trace_test.log")
    t = trace_mod.enable(log_path, console=False)

    # 用带文字的真实标签，才能看到位图行
    res = render_landscape_label(
        text="资产编号 NB-2026-001",
        strip_dots=mm_to_px(12), length_dots=mm_to_px(30),
        font_size=26, bold=True, align="left",
    )

    async def run():
        fake = FakeTransport()
        fake.model_id = 2320
        fake.connect_result = pkt.CONNECT_CONNECTED_V3
        fake.protocol_n = 210
        client = NiimbotClient(fake)
        await client.connect("COM9")
        await client.print_image(res.image, PrintOptions(quantity=1), landscape=True)
        return client

    try:
        client = asyncio.run(run())
    finally:
        trace_mod.tracer = None

    assert client.info.print_task == "B1"

    with open(log_path, encoding="utf-8") as f:
        text = f.read()

    for needle in (
        "开始连接 COM9",
        "握手结果=3",
        "判定协议版本 = v3",
        "型号 ID=2320",
        "打印流程 B1",
        "开始打印：",
        "PrintStart 7字节",
        "SetPageSize 6字节",
        "PrintBitmapRow",
        "PrintEmptyRow",
        "PrintStatusResp",
        "打印完成",
    ):
        assert needle in text, f"调用记录里缺少：{needle}\n---\n{text[-2500:]}"

    # B1 流程不应发送 PrintQuantity
    assert "PrintQuantity 份数" not in text, "B1 流程不应发送 PrintQuantity"
    print(f"  ok  test_trace_records_call_sequence  ({len(text.splitlines())} 行记录)")


if __name__ == "__main__":
    test_full_print_flow()
    test_print_error_propagates()
    test_prepare_rotation_identity()
    test_landscape_identity_mapping()
    test_landscape_render_and_print()
    test_timeout_error_has_message()
    test_serial_probe_detects_charge_mode()
    test_serial_probe_silent_gives_guidance()
    test_task_selection()
    test_d110_m_uses_b1_flow()
    test_d110_m_v4_flow()
    test_d110_keeps_old_flow()
    test_rendered_text_produces_black_pixels()
    test_single_line_rows_have_incrementing_positions()
    test_alignment_centers_the_ink()
    test_preview_matches_print_transform()
    test_paper_detection()
    test_paper_detection_fails_gracefully()
    test_settings_persistence()
    test_generation_failures_surface_as_warnings()
    test_font_size_boundary_warnings()
    test_orientation_transforms()
    test_diagnostic_label_blocks()
    test_trace_records_call_sequence()
    print("all integration tests passed")
