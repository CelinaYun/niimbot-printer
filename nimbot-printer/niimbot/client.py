"""Niimbot 高层客户端：连接、握手、按 D110 流程打印标签。"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass

from . import packet as pkt
from . import trace as trace_mod
from .encoder import (
    D110_PRINTHEAD_PIXELS,
    EncodedImage,
    encode_image,
    rows_to_packets,
)
from .transport import Transport, TransportError

log = logging.getLogger("niimbot.client")


class PrinterError(Exception):
    """打印机返回的错误。"""


class PrintCancelled(PrinterError):
    """用户主动取消打印（不是故障，界面上不该当成错误刷红）。"""


# 打印流程名。不同型号走不同的命令序列，选错会导致空白/多份。
TASK_D110 = "D110"
TASK_B1 = "B1"
TASK_D110M_V4 = "D110M_V4"
TASK_AUTO = "自动"

# 型号 ID → 打印流程（来自 niimbluelib 的 modelPrintTasks 映射表）
_MODEL_TASK: dict[int, str] = {
    2304: TASK_D110,   # D110
    2305: TASK_D110,   # D110 / HI_D110
    2320: TASK_B1,     # D110_M
    512: TASK_D110,    # D11（v1/v2 用 D110 流程）
    514: TASK_D110,    # D11S
    528: TASK_D110M_V4,  # D11_H
    4352: TASK_D110M_V4,  # H1S
    3586: TASK_B1,     # N1
    1792: TASK_D110,   # B16
    3584: TASK_D110,   # B18
    3585: TASK_D110,   # B18S
}


def resolve_print_task(model_id: int | None, protocol_version: int = 0,
                       override: str | None = None) -> str:
    """按型号 + 协议版本挑打印流程；``override`` 可手动指定。"""
    if override and override != TASK_AUTO:
        return override

    # D110_M 在协议 v4 及以上要换 D110M_V4 流程
    if model_id == 2320 and protocol_version >= 4:
        return TASK_D110M_V4

    task = _MODEL_TASK.get(model_id or -1)
    if task:
        return task

    # 未知型号：新协议走 D110M_V4，老协议走 D110
    return TASK_D110M_V4 if protocol_version >= 4 else TASK_D110


def density_range_for(model_id: int | None) -> tuple[int, int, int]:
    """返回 (最小, 最大, 默认) 浓度档位。D110_M / D11_H 是 1-5。"""
    if model_id in (2320, 528, 513, 2305):
        return 1, 5, 3
    return 1, 3, 2


@dataclass
class PrintOptions:
    quantity: int = 1
    density: int = 2  # 1..3（D110_M 是 1..5）
    label_type: int = pkt.LABEL_WITH_GAPS
    page_timeout: float = 10.0
    status_timeout: float = 8.0
    status_poll_interval: float = 0.3
    speed: int = 1  # D110M_V4 的打印模式，0=清晰 1=速度（默认 1，与参考实现一致）
    printhead_pixels: int = 0  # 0 = 用打印机报的真实打印头宽度


@dataclass
class PaperInfo:
    """打印机从 RFID 纸卷标签里读到的纸张规格。"""

    valid: bool = False
    paper_width_mm: float = 0.0      # 纸宽
    paper_height_mm: float = 0.0     # 标签长度（走纸方向）
    gap_mm: float = 0.0              # 标签间隙
    paper_type: int = 0
    width_pixels: int = 0            # 纸宽对应点数
    height_pixels: int = 0           # 标签长度对应点数


@dataclass
class PrinterInfo:
    model_id: int | None = None
    battery: int | None = None
    soft_version: str | None = None
    serial: str | None = None
    connect_result: int | None = None
    protocol_version: int = 0
    print_task: str = TASK_D110
    printhead_pixels: int = D110_PRINTHEAD_PIXELS
    paper: PaperInfo | None = None


class NiimbotClient:
    def __init__(self, transport: Transport) -> None:
        self.transport = transport
        self.info = PrinterInfo()
        self._buffer = b""
        self._pending: list[tuple[list[int], asyncio.Future]] = []
        self._lock = asyncio.Lock()
        self._cancel = threading.Event()
        self.on_disconnect = None  # 由界面设置：连接意外断开时回调（在 I/O 线程里）
        self.transport.set_receive_callback(self._on_bytes)
        self.transport.set_disconnect_callback(self._on_transport_disconnect)

    # ------------------------------------------------------------- 收发

    def _on_bytes(self, data: bytes) -> None:
        if not data:
            return
        t = trace_mod.get()
        if t is not None:
            t.raw_in(data)
        self._buffer += data
        packets, self._buffer = pkt.parse_packet_bundle(self._buffer)
        if t is not None and not packets and self._buffer:
            t.orphan_bytes(self._buffer)
        for p in packets:
            self._dispatch(p)

    def _dispatch(self, p: pkt.NiimbotPacket) -> None:
        t = trace_mod.get()
        if t is not None:
            t.packet("in", p)
        if p.command == pkt.RX_PRINT_ERROR and len(p.data) >= 1:
            code = p.data[0]
            msg = f"打印机错误 {code}：{pkt.PRINT_ERRORS.get(code, '未知')}"
            if t is not None:
                t.event(f"!! {msg}")
            self._fail_pending(PrinterError(msg))
            return
        if p.command == pkt.RX_NOT_SUPPORTED:
            if t is not None:
                t.event("!! 打印机返回 NotSupported")
            self._fail_pending(PrinterError("打印机不支持该命令"))
            return

        for i, (ids, fut) in enumerate(self._pending):
            if not ids or p.command in ids:
                if not fut.done():
                    fut.set_result(p)
                del self._pending[i]
                return

    def _fail_pending(self, exc: Exception) -> None:
        for _, fut in self._pending:
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()

    # ------------------------------------------------------------- 取消 / 断开

    def cancel(self) -> None:
        """请求取消当前打印。任意线程可调，在下一次检查点生效。"""
        self._cancel.set()

    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            raise PrintCancelled("打印已取消")

    def _on_transport_disconnect(self) -> None:
        """通道断了（蓝牙掉线 / USB 被拔）。在 I/O 线程里执行。"""
        self._fail_pending(PrinterError("连接已断开"))
        cb = self.on_disconnect
        if cb is not None:
            try:
                cb()
            except Exception:  # noqa: BLE001
                pass

    async def _request(
        self,
        command: int,
        data: bytes = b"\x01",
        timeout: float = 2.0,
        expect: list[int] | None = None,
    ) -> pkt.NiimbotPacket | None:
        """发送一条命令并等待应答；单向命令立即返回 None。"""
        p = pkt.NiimbotPacket(command, data)
        t = trace_mod.get()
        if t is not None:
            t.packet("out", p)

        if p.one_way:
            async with self._lock:
                await self.transport.write(p.to_bytes())
            return None

        ids = expect if expect is not None else (pkt.RESPONSE_MAP.get(command) or [])
        fut: asyncio.Future = asyncio.get_running_loop().create_future()

        async with self._lock:
            # 先登记应答通道再发包，避免应答先到被丢弃
            self._pending.append((list(ids), fut))
            try:
                await self.transport.write(p.to_bytes())
                try:
                    return await asyncio.wait_for(fut, timeout=timeout)
                except asyncio.TimeoutError as e:
                    raise PrinterError(
                        f"等待命令 0x{command:02X} 的应答超时（{timeout:.0f} 秒）"
                    ) from e
            finally:
                self._pending = [(i, f) for i, f in self._pending if f is not fut]

    # ------------------------------------------------------------- 连接

    async def connect(self, target: str) -> PrinterInfo:
        t = trace_mod.get()
        if t is not None:
            t.event(f"开始连接 {target}（通道 {self.transport.kind}）")
        self._buffer = b""
        name = await self.transport.open(target)
        if t is not None:
            t.event(f"通道已打开：{name}")

        try:
            resp = await self._request(pkt.CMD_CONNECT, timeout=5.0)
        except Exception as e:  # noqa: BLE001
            raise PrinterError(await self._explain_handshake_failure(e)) from e

        if resp is None or len(resp.data) < 1:
            raise PrinterError(await self._explain_handshake_failure(
                PrinterError("握手失败：没有收到连接应答")))
        self.info.connect_result = resp.data[0]
        if self.info.connect_result == pkt.CONNECT_DISCONNECT:
            raise PrinterError("握手被拒绝，请确认打印机已开机、纸盖已合上")
        if t is not None:
            t.event(f"握手结果={self.info.connect_result}")

        # 协议版本决定用哪套打印命令（D110_M 在 v4 前后流程不同）
        if self.info.connect_result == pkt.CONNECT_CONNECTED_NEW:
            self.info.protocol_version = 1
        elif self.info.connect_result == pkt.CONNECT_CONNECTED_V3:
            self.info.protocol_version = await self._fetch_protocol_version()
        if t is not None:
            t.event(f"判定协议版本 = v{self.info.protocol_version}")

        try:
            r = await self._request(pkt.CMD_PRINTER_INFO, bytes([pkt.INFO_MODEL_ID]), timeout=3.0)
            if r is not None:
                if len(r.data) == 1:
                    self.info.model_id = r.data[0] << 8
                elif len(r.data) >= 2:
                    self.info.model_id = pkt.u16be_read(r.data)
        except Exception:  # noqa: BLE001
            self.info.model_id = None

        try:
            r = await self._request(pkt.CMD_PRINTER_INFO, bytes([pkt.INFO_BATTERY]), timeout=2.0)
            if r is not None and r.data:
                v = r.data[0]
                self.info.battery = v * 25 if v <= 4 else v
        except Exception:  # noqa: BLE001
            self.info.battery = None

        try:
            r = await self._request(pkt.CMD_PRINTER_INFO, bytes([pkt.INFO_SOFT_VERSION]), timeout=2.0)
            if r is not None and len(r.data) >= 2:
                self.info.soft_version = f"{(r.data[0] * 256 + r.data[1]) / 100:.2f}"
        except Exception:  # noqa: BLE001
            self.info.soft_version = None

        # 读真实打印头宽度和纸张规格，避免靠手填尺寸
        width = await self.get_printhead_width()
        if width > 0:
            self.info.printhead_pixels = width
        self.info.paper = await self.get_paper_info()

        self.info.print_task = resolve_print_task(self.info.model_id, self.info.protocol_version)
        if t is not None:
            t.event(f"型号 ID={self.info.model_id}  电量={self.info.battery}  "
                    f"固件={self.info.soft_version}  →  打印流程 {self.info.print_task}")
            t.event(f"打印头 {self.info.printhead_pixels} 点"
                    f"（约 {self.info.printhead_pixels / 203 * 25.4:.1f} mm）")
            p = self.info.paper
            if p and p.valid:
                t.event(f"检测到纸张：{p.paper_width_mm:.0f} mm 宽 × "
                        f"{p.paper_height_mm:.0f} mm 标签，间隙 {p.gap_mm:.1f} mm")
            else:
                t.event("未读到 RFID 纸张信息（纸卷可能不带 RFID），请手动确认标签尺寸")
        log.info("已连接 %s，型号 ID=%s，协议 v%s，打印流程 %s",
                 name, self.info.model_id, self.info.protocol_version, self.info.print_task)
        return self.info

    async def disconnect(self) -> None:
        try:
            await self.transport.close()
        except Exception:  # noqa: BLE001
            pass
        self._buffer = b""
        self._fail_pending(PrinterError("连接已断开"))

    async def _explain_handshake_failure(self, err: Exception) -> str:
        """握手失败时补一段诊断，说明最可能的原因。"""
        base = str(err).strip() or type(err).__name__
        lines = [f"握手失败：{base}"]

        if self.transport.kind == "serial":
            probe = b""
            try:
                probe = await self.transport.raw_exchange(b"#10001\r\n", wait=1.5)
            except Exception:  # noqa: BLE001
                probe = b""

            text = probe.decode("ascii", errors="replace").strip()
            if "#10001:" in text:
                ident = text.split("#10001:", 1)[1].splitlines()[0].strip()
                lines.append(f"探测到 Niimbot 打印机：{ident}")
                lines.append("它现在是关机充电模式，串口只认调试命令。请先开机，再重新连接。")
            elif text:
                lines.append(f"串口有回音但不是预期格式：{text[:80]}")
                lines.append("请确认这个 COM 口确实是打印机，或改用蓝牙连接。")
            else:
                lines.append("串口完全没有回音。请检查：")
                lines.append("  1) 这个 COM 口是不是打印机（设备管理器 → 端口）；")
                lines.append("  2) 打印机是否已开机（关机充电模式下不认打印协议）；")
                lines.append("  3) 部分 D110 的 USB 只供电不传数据，这种情况请改用蓝牙。")
        else:
            lines.append("请检查：")
            lines.append("  1) 打印机是否已开机、电量充足；")
            lines.append("  2) 手机上的 Niimbot App 是否已关闭（它会占住连接）；")
            lines.append("  3) 不要在 Windows 蓝牙设置里手动配对，程序走免配对直连。")

        return "\n".join(lines)

    # ------------------------------------------------------------- 状态

    async def get_paper_info(self) -> PaperInfo:
        """读打印机 RFID 纸卷标签里的纸张规格（纸宽 / 标签长度 / 间隙）。

        只有带 RFID 的纸卷才报得出来；报不出来时 ``valid`` 为 False。
        """
        info = PaperInfo()
        try:
            resp = await self._request(pkt.CMD_GET_PAPER_INFO, timeout=3.0)
        except Exception:  # noqa: BLE001
            return info
        if resp is None or len(resp.data) != 18:
            return info

        d = resp.data
        info.valid = True
        info.height_pixels = pkt.u16be_read(d, 2)       # totalHeightPixel
        info.paper_type = d[4]
        info.gap_mm = pkt.u16be_read(d, 5) / 10.0       # gapHeight
        info.paper_height_mm = pkt.u16be_read(d, 7) / 10.0  # totalHeight
        info.width_pixels = pkt.u16be_read(d, 9)        # paperWidthPixel
        info.paper_width_mm = pkt.u16be_read(d, 11) / 10.0  # paperWidth
        return info

    async def get_printhead_width(self) -> int:
        """从打印机读回真实打印头宽度（点数），读不到就用型号表里的默认值。"""
        try:
            resp = await self._request(
                pkt.CMD_HEARTBEAT, bytes([pkt.HEARTBEAT_PRINTER_INFO]), timeout=3.0)
        except Exception:  # noqa: BLE001
            return 0
        if resp is None or resp.command != pkt.RX_HEARTBEAT_PRINTER_INFO or len(resp.data) < 10:
            return 0
        return pkt.u16be_read(resp.data, 4)  # printheadWidth

    async def _fetch_protocol_version(self) -> int:
        """查询 PrinterStatusData，解析出协议版本号。取不到就返回 0。"""
        try:
            resp = await self._request(pkt.CMD_PRINTER_STATUS_DATA, timeout=3.0)
        except Exception:  # noqa: BLE001
            return 0
        if resp is None or len(resp.data) < 13:
            return 0
        n = resp.data[11] * 100 + resp.data[12]
        if n >= 302:
            return 5
        if n >= 300:
            return 4
        if n >= 204:
            return 3
        return 0

    async def get_print_status(self) -> tuple[int, int, int, int]:
        """返回 (page, print_progress, feed_progress, error)。"""
        resp = await self._request(pkt.CMD_PRINT_STATUS, timeout=3.0)
        if resp is None or len(resp.data) < 4:
            raise PrinterError("读取打印状态失败")
        page = pkt.u16be_read(resp.data)
        err = 0
        if len(resp.data) == 10:
            err = resp.data[8]
        return page, resp.data[2], resp.data[3], err

    def _page_size_payload(self, rows: int, cols: int, copies: int, task: str | None = None) -> bytes:
        """按打印流程拼 SetPageSize 数据。B1 系列必须带份数（6 字节）。"""
        name = task or self.info.print_task or TASK_D110
        if name == TASK_B1:
            return pkt.u16be(rows) + pkt.u16be(cols) + pkt.u16be(copies)
        if name == TASK_D110M_V4:
            return (
                pkt.u16be(rows) + pkt.u16be(cols) + pkt.u16be(copies)
                + pkt.u16be(0) + bytes([0, 0, 0]) + pkt.u16be(0)
            )
        return pkt.u16be(rows) + pkt.u16be(cols)

    async def feed_labels(self, count: int = 1) -> None:
        """走纸（吐出空白标签），用于对齐标签间隙。"""
        for _ in range(max(1, count)):
            await self._request(pkt.CMD_PAGE_START, timeout=2.0)
            await self._request(
                pkt.CMD_SET_PAGE_SIZE,
                self._page_size_payload(24, D110_PRINTHEAD_PIXELS, 1),
                timeout=2.0,
            )
            if self.info.print_task == TASK_D110:
                await self._request(pkt.CMD_PRINT_QUANTITY, pkt.u16be(1), timeout=2.0)
            await self._request(pkt.CMD_PRINT_EMPTY_ROW, pkt.u16be(0) + b"\x18", timeout=1.0)
            await self._page_end_with_retry()
        await self._request(pkt.CMD_PRINT_END, timeout=5.0)

    # ------------------------------------------------------------- 打印

    async def print_image(
        self,
        image: "object",
        options: PrintOptions | None = None,
        on_progress=None,
        landscape: bool = True,
        task: str | None = None,
        orientation: str = "正常",
    ) -> None:
        """打印一张 PIL Image。

        ``landscape=True``（默认）：画布宽=标签长度、高=打印头点数，文字沿长度方向书写。
        ``landscape=False``：竖排画布（宽=打印头点数、高=长度），会先旋转再编码。
        ``task``：手动指定打印流程，留空则按型号自动选择。
        ``orientation``：打印取向（正常/左右翻转/上下翻转/旋转 180°），
        用来抵消打印头点序方向相反导致的镜像。
        """
        opts = options or PrintOptions()
        if landscape:
            from .encoder import apply_orientation

            source = apply_orientation(image, orientation)
        else:
            from .encoder import prepare_source_image

            source = prepare_source_image(image)
        encoded = encode_image(source, print_direction="left")
        await self.print_encoded(encoded, opts, on_progress, task=task)

    async def print_encoded(
        self,
        encoded: EncodedImage,
        options: PrintOptions,
        on_progress=None,
        task: str | None = None,
    ) -> None:
        # 打印头宽度：优先用打印机报回来的，其次看选项，最后退回型号默认
        printhead = options.printhead_pixels or self.info.printhead_pixels or D110_PRINTHEAD_PIXELS
        self._cancel.clear()  # 每次新打印都重置取消标记
        if encoded.cols > printhead:
            raise PrinterError(
                f"图像宽度 {encoded.cols} 超过打印头 {printhead} 点"
                f"（约 {printhead / 203 * 25.4:.1f} mm）"
            )
        if options.quantity < 1:
            raise PrinterError("打印份数至少为 1")

        name = task or self.info.print_task or resolve_print_task(
            self.info.model_id, self.info.protocol_version
        )
        t = trace_mod.get()
        if t is not None:
            t.event(f"—— 开始打印：{encoded.cols}×{encoded.rows} 点，{options.quantity} 份，"
                    f"浓度 {options.density}，流程 {name} ——")
        log.info("打印流程 %s（型号 %s，协议 v%s）",
                 name, self.info.model_id, self.info.protocol_version)

        try:
            if name == TASK_B1:
                await self._print_b1(encoded, options, on_progress)
            elif name == TASK_D110M_V4:
                await self._print_d110m_v4(encoded, options, on_progress)
            else:
                await self._print_d110(encoded, options, on_progress)
        except Exception as e:  # noqa: BLE001
            if t is not None:
                t.event(f"!! 打印中断：{e}")
            raise
        if t is not None:
            t.event("—— 打印完成 ——")

    # --------------------------------------------------------- 三种打印流程

    async def _print_d110(self, encoded: EncodedImage, options: PrintOptions, on_progress=None) -> None:
        """D110 / D11：setPageSize4b + PrintQuantity。"""
        await self._request(pkt.CMD_SET_DENSITY, bytes([options.density & 0xFF]), timeout=2.0)
        await self._request(pkt.CMD_SET_LABEL_TYPE, bytes([options.label_type & 0xFF]), timeout=2.0)
        await self._request(pkt.CMD_PRINT_START, timeout=3.0)  # 数据 [1]
        try:
            await self._request(pkt.CMD_PAGE_START, timeout=2.0)
            await self._request(
                pkt.CMD_SET_PAGE_SIZE,
                self._page_size_payload(encoded.rows, encoded.cols, options.quantity, TASK_D110),
                timeout=2.0,
            )
            await self._request(pkt.CMD_PRINT_QUANTITY, pkt.u16be(options.quantity), timeout=2.0)
            await self._send_rows(encoded, options, on_progress)
            await self._page_end_with_retry()
        except Exception:
            await self._abort_print()
            raise
        # 无论轮询成功与否都要发 PrintEnd 收尾，否则打印机会一直挂在会话里
        try:
            await self._wait_print_finished(options)
        finally:
            await self._finish_print()

    async def _print_b1(self, encoded: EncodedImage, options: PrintOptions, on_progress=None) -> None:
        """B1 / D110_M：printStart7b + setPageSize6b（份数写在 page size 里）。

        这条流程**不能**用 setPageSize4b —— JS 库注释：「B1 系列首次打印空白、
        或打印多份，请改用 setPageSize6b」。也**不发** PrintQuantity。
        """
        total = options.quantity
        # printStart7b：u16(总页数) + 4 个 0 + pageColor
        start_data = pkt.u16be(total) + bytes([0, 0, 0, 0, 0])
        await self._request(pkt.CMD_SET_DENSITY, bytes([options.density & 0xFF]), timeout=2.0)
        await self._request(pkt.CMD_SET_LABEL_TYPE, bytes([options.label_type & 0xFF]), timeout=2.0)
        await self._request(pkt.CMD_PRINT_START, start_data, timeout=3.0)
        try:
            await self._request(pkt.CMD_PAGE_START, timeout=2.0)
            await self._request(
                pkt.CMD_SET_PAGE_SIZE,
                self._page_size_payload(encoded.rows, encoded.cols, total, TASK_B1),
                timeout=2.0,
            )
            await self._send_rows(encoded, options, on_progress)
            await self._page_end_with_retry()
        except Exception:
            await self._abort_print()
            raise
        try:
            await self._wait_print_finished(options)
        finally:
            await self._finish_print()

    async def _print_d110m_v4(self, encoded: EncodedImage, options: PrintOptions, on_progress=None) -> None:
        """D110_M（协议 v4+）/ D11_H 等：printStart9b + setPageSize13b。"""
        total = options.quantity
        # printStart9b：u16(总页数) + 4 个 0 + pageColor + speed + flag
        start_data = pkt.u16be(total) + bytes([0, 0, 0, 0, 0, options.speed & 0xFF, 0])
        await self._request(pkt.CMD_SET_LABEL_TYPE, bytes([options.label_type & 0xFF]), timeout=2.0)
        await self._request(pkt.CMD_SET_DENSITY, bytes([options.density & 0xFF]), timeout=2.0)
        await self._request(pkt.CMD_PRINT_START, start_data, timeout=3.0)
        try:
            # B21_PRO 在 PrintStart 之后第一个包会丢，先发一个单向 PrintStatus 占位
            await self._send_one_way(pkt.CMD_PRINT_STATUS)
            await self._request(
                pkt.CMD_SET_PAGE_SIZE,
                self._page_size_payload(encoded.rows, encoded.cols, total, TASK_D110M_V4),
                timeout=2.0,
            )
            await self._send_rows(encoded, options, on_progress)
            await self._page_end_with_retry()
        except Exception:
            await self._abort_print()
            raise
        try:
            await self._wait_print_finished(options)
        finally:
            await self._finish_print(heartbeat_after=True)

    # --------------------------------------------------------- 公共步骤

    async def _send_rows(self, encoded: EncodedImage, options: PrintOptions, on_progress=None) -> None:
        printhead = options.printhead_pixels or self.info.printhead_pixels or D110_PRINTHEAD_PIXELS
        row_packets = rows_to_packets(encoded, printhead, single_line_rows=True)
        total = max(1, len(row_packets))
        t = trace_mod.get()
        if t is not None:
            t.event(f"开始发送位图数据：共 {total} 行（每行单独成包，明细写入日志文件）")
            for cmd, data in row_packets[:2]:
                t.event(f"  样例行 cmd=0x{cmd:02X} 数据={data.hex(' ')}")
        for i, (command, data) in enumerate(row_packets):
            self._check_cancel()
            await self._request(command, data, timeout=1.0)
            if on_progress is not None:
                on_progress(0.1 + 0.8 * (i + 1) / total)
        if t is not None:
            segments = sum(1 for r in encoded.rows_data if r.row_data is not None)
            filled = sum(r.repeat for r in encoded.rows_data if r.row_data is not None)
            # 合并行要按 repeat 加权，否则统计会偏小
            dots = sum(
                bin(b).count("1") * r.repeat
                for r in encoded.rows_data
                if r.row_data
                for b in r.row_data
            )
            t.event(f"位图数据发送完毕：{total} 行，非空白 {filled} 行（{segments} 段），"
                    f"黑色点阵共 {dots} 点"
                    f"（{'⚠ 图像几乎全白，请检查内容' if dots < 20 else '正常'}）")

    async def _send_one_way(self, command: int, data: bytes = b"\x01") -> None:
        """发一个不等应答的包（某些型号会吞掉 PrintStart/PrintEnd 后的第一个包）。"""
        p = pkt.NiimbotPacket(command, data)
        t = trace_mod.get()
        if t is not None:
            t.packet("out", p)
        async with self._lock:
            await self.transport.write(p.to_bytes())

    async def _abort_print(self) -> None:
        try:
            await self._request(pkt.CMD_PRINT_END, timeout=2.0)
        except Exception:  # noqa: BLE001
            pass

    async def _finish_print(self, heartbeat_after: bool = False) -> None:
        """收尾：发 PrintEnd。用在 finally 里，所以绝不能抛异常掩盖原始错误。"""
        try:
            end = await self._request(pkt.CMD_PRINT_END, timeout=5.0)
            if end is not None and end.data and end.data[0] == 0:
                log.warning("PrintEnd 返回 0，可能未完成")
        except Exception as e:  # noqa: BLE001
            log.warning("PrintEnd 失败：%s", e)
        if heartbeat_after:
            # B21_PRO 会丢掉 PrintEnd 后的第一个包，补一个单向心跳占位
            try:
                await self._send_one_way(pkt.CMD_HEARTBEAT, bytes([1]))
            except Exception:  # noqa: BLE001
                pass

    async def _page_end_with_retry(self, attempts: int = 20) -> None:
        """PageEnd 可能返回 0（未接受），需要重试到 1 为止。"""
        for _ in range(attempts):
            resp = await self._request(pkt.CMD_PAGE_END, timeout=2.0)
            if resp is not None and resp.data and resp.data[0] == 1:
                return
            await asyncio.sleep(0.05)
        raise PrinterError("PageEnd 始终未被接受")

    async def _wait_print_finished(self, options: PrintOptions) -> None:
        deadline = time.monotonic() + options.status_timeout * max(1, options.quantity)
        while time.monotonic() < deadline:
            self._check_cancel()
            page, _p, _f, err = await self.get_print_status()
            if err:
                raise PrinterError(f"打印错误 {err}：{pkt.PRINT_ERRORS.get(err, '未知')}")
            if page >= options.quantity:
                return
            await asyncio.sleep(options.status_poll_interval)
        raise PrinterError("等待打印完成超时")
