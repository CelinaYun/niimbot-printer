"""协议调用记录：把每一次收发都打印到控制台并写进日志文件。

用法：程序启动后自动开启，日志同时出现在：

1. 启动窗口（控制台）里
2. 程序目录下的 ``打印记录.log``

每条记录形如::

    [12:34:56.789] >> 55 55 21 01 02 22 AA AA   SetDensity density=2
    [12:34:56.812] << 55 55 31 01 01 31 AA AA   SetDensityResp ok=1
"""

from __future__ import annotations

import datetime
import os
import sys
import threading

from . import packet as pkt

if True:  # 允许从源码直接运行
    try:
        from paths import data_path as _data_path
    except ImportError:  # 作为 niimbot 包被引用时
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from paths import data_path as _data_path

# ------------------------------------------------------------- 命令名

_REQ_NAMES = {
    pkt.CMD_PRINT_START: "PrintStart",
    pkt.CMD_PAGE_START: "PageStart",
    pkt.CMD_PRINTER_INFO: "PrinterInfo",
    pkt.CMD_PRINT_QUANTITY: "PrintQuantity",
    pkt.CMD_SET_PAGE_SIZE: "SetPageSize",
    pkt.CMD_PRINT_CLEAR: "PrintClear",
    pkt.CMD_SET_DENSITY: "SetDensity",
    pkt.CMD_SET_PRINT_SPEED: "SetPrintSpeed",
    pkt.CMD_SET_LABEL_TYPE: "SetLabelType",
    pkt.CMD_PRINT_EMPTY_ROW: "PrintEmptyRow",
    pkt.CMD_PRINT_BITMAP_ROW: "PrintBitmapRow",
    pkt.CMD_PRINT_BITMAP_ROW_INDEXED: "PrintBitmapRowIndexed",
    pkt.CMD_PRINT_STATUS: "PrintStatus",
    pkt.CMD_PRINTER_STATUS_DATA: "PrinterStatusData",
    pkt.CMD_CONNECT: "Connect",
    pkt.CMD_HEARTBEAT: "Heartbeat",
    pkt.CMD_PAGE_END: "PageEnd",
    pkt.CMD_PRINT_END: "PrintEnd",
}

_RESP_NAMES = {
    pkt.RX_NOT_SUPPORTED: "NotSupported",
    pkt.RX_PRINT_START: "PrintStartResp",
    pkt.RX_PAGE_START: "PageStartResp",
    pkt.RX_PRINTER_INFO_DENSITY: "InfoDensity",
    pkt.RX_PRINTER_INFO_SPEED: "InfoSpeed",
    pkt.RX_PRINTER_INFO_LABEL_TYPE: "InfoLabelType",
    pkt.RX_PRINTER_INFO_AUTO_SHUTDOWN: "InfoAutoShutdown",
    pkt.RX_PRINTER_INFO_SERIAL: "InfoSerial",
    pkt.RX_PRINTER_INFO_MODEL_CODE: "InfoModelId",
    pkt.RX_PRINTER_INFO_SOFT_VERSION: "InfoSoftVer",
    pkt.RX_PRINTER_INFO_CHARGE: "InfoBattery",
    pkt.RX_PRINTER_INFO_HARD_VERSION: "InfoHardVer",
    pkt.RX_PRINTER_INFO_BT_ADDR: "InfoBtAddr",
    pkt.RX_PRINT_QUANTITY: "PrintQuantityResp",
    pkt.RX_SET_PAGE_SIZE: "SetPageSizeResp",
    pkt.RX_PRINT_CLEAR: "PrintClearResp",
    pkt.RX_SET_DENSITY: "SetDensityResp",
    pkt.RX_SET_PRINT_SPEED: "SetSpeedResp",
    pkt.RX_SET_LABEL_TYPE: "SetLabelTypeResp",
    pkt.RX_PRINTER_STATUS_DATA: "PrinterStatusDataResp",
    pkt.RX_CONNECT: "ConnectResp",
    pkt.RX_HEARTBEAT_ADV1: "HeartbeatAdv1",
    pkt.RX_HEARTBEAT_ADV2: "HeartbeatAdv2",
    pkt.RX_HEARTBEAT_PRINTER_INFO: "HeartbeatPrinterInfo",
    pkt.RX_PRINT_ERROR: "PrintError",
    pkt.RX_PRINTER_PAGE_INDEX: "PrinterPageIndex",
    pkt.RX_PAGE_END: "PageEndResp",
    pkt.RX_PRINT_END: "PrintEndResp",
    pkt.RX_PRINT_STATUS: "PrintStatusResp",
}

_INFO_TYPES = {
    pkt.INFO_DENSITY: "Density",
    pkt.INFO_SPEED: "Speed",
    pkt.INFO_LABEL_TYPE: "LabelType",
    pkt.INFO_LANGUAGE: "Language",
    pkt.INFO_AUTO_SHUTDOWN: "AutoShutdown",
    pkt.INFO_MODEL_ID: "ModelId",
    pkt.INFO_SOFT_VERSION: "SoftVersion",
    pkt.INFO_BATTERY: "Battery",
    pkt.INFO_SERIAL: "Serial",
    pkt.INFO_HARD_VERSION: "HardVersion",
    pkt.INFO_BT_ADDR: "BluetoothAddr",
}


def command_name(command: int) -> str:
    return _REQ_NAMES.get(command) or _RESP_NAMES.get(command) or f"Cmd(0x{command:02X})"


def _u16(data: bytes, offset: int = 0) -> int:
    return pkt.u16be_read(data, offset) if len(data) >= offset + 2 else -1


def describe(p: pkt.NiimbotPacket) -> str:
    """把封包数据翻译成一句人话，便于排查。"""
    c, d = p.command, p.data
    name = command_name(c)

    if c == pkt.CMD_CONNECT:
        return f"{name} 旗标={d.hex() or '（无）'}"
    if c == pkt.RX_CONNECT:
        labels = {0: "拒绝", 1: "已连接(旧)", 2: "已连接(新,v1)", 3: "已连接(v3+)"}
        v = d[0] if d else -1
        return f"{name} 结果={v} ({labels.get(v, '未知')})"
    if c == pkt.CMD_PRINT_START:
        if len(d) == 1:
            return f"{name} 1字节 标志={d[0]}"
        if len(d) == 7:
            return f"{name} 7字节 总页数={_u16(d)} 颜色={d[6]}"
        if len(d) == 9:
            return f"{name} 9字节 总页数={_u16(d)} 颜色={d[6]} 速度={d[7]}"
        return f"{name} {len(d)}字节 {d.hex()}"
    if c == pkt.CMD_SET_PAGE_SIZE:
        if len(d) == 4:
            return f"{name} 4字节 rows={_u16(d)} cols={_u16(d, 2)}"
        if len(d) == 6:
            return f"{name} 6字节 rows={_u16(d)} cols={_u16(d, 2)} copies={_u16(d, 4)}"
        if len(d) == 13:
            return (f"{name} 13字节 rows={_u16(d)} cols={_u16(d, 2)} "
                    f"copies={_u16(d, 4)} cutHeight={_u16(d, 6)} cutType={d[8]}")
        return f"{name} {len(d)}字节 {d.hex()}"
    if c == pkt.CMD_PRINT_QUANTITY:
        return f"{name} 份数={_u16(d)}"
    if c == pkt.CMD_SET_DENSITY:
        return f"{name} 浓度={d[0] if d else '?'}"
    if c == pkt.CMD_SET_LABEL_TYPE:
        return f"{name} 标签类型={d[0] if d else '?'}"
    if c == pkt.CMD_PRINT_BITMAP_ROW:
        row, repeat = _u16(d), (d[5] if len(d) > 5 else -1)
        return f"{name} 行={row} 重复={repeat} 位图={max(0, len(d) - 6)}字节"
    if c == pkt.CMD_PRINT_BITMAP_ROW_INDEXED:
        row, repeat = _u16(d), (d[5] if len(d) > 5 else -1)
        return f"{name} 行={row} 重复={repeat} 索引={max(0, len(d) - 6)}字节"
    if c == pkt.CMD_PRINT_EMPTY_ROW:
        return f"{name} 行={_u16(d)} 重复={d[2] if len(d) > 2 else '?'}"
    if c == pkt.CMD_PRINTER_INFO:
        t = d[0] if d else -1
        return f"{name} 查询={t} ({_INFO_TYPES.get(t, '未知')})"
    if c == pkt.RX_PRINT_STATUS:
        page = _u16(d)
        extra = ""
        if len(d) == 10:
            extra = f" 错误码={d[8]}"
        return f"{name} 当前页={page} 进度={d[2] if len(d) > 2 else '?'}%/{d[3] if len(d) > 3 else '?'}%{extra}"
    if c == pkt.RX_PRINT_ERROR:
        code = d[0] if d else -1
        return f"{name} 错误码={code} ({pkt.PRINT_ERRORS.get(code, '未知')})"
    if c == pkt.CMD_PRINTER_STATUS_DATA or c == pkt.RX_PRINTER_STATUS_DATA:
        if len(d) >= 13:
            n = d[11] * 100 + d[12]
            ver = 5 if n >= 302 else (4 if n >= 300 else (3 if n >= 204 else 0))
            return f"{name} 支持彩色={d[10]} 版本字段={n} → 协议 v{ver}"
        return f"{name} {len(d)}字节 {d.hex()}"
    if c in (pkt.CMD_PAGE_START, pkt.CMD_PAGE_END, pkt.CMD_PRINT_END,
             pkt.CMD_PRINT_CLEAR, pkt.CMD_PRINT_STATUS, pkt.CMD_HEARTBEAT):
        return f"{name} data={d.hex() or '（无）'}"

    return f"{name} {len(d)}字节 {d.hex()}" if d else name


def hex_bytes(data: bytes, limit: int = 48) -> str:
    text = data[:limit].hex(" ")
    return text + (" …" if len(data) > limit else "")


# 单日日志超过这个大小就转存一份，避免打几千张时把磁盘写爆
MAX_LOG_BYTES = 5 * 1024 * 1024
# 更早的日志文件自动删除
KEEP_DAYS = 30


class Tracer:
    """把协议事件写到控制台和 ``打印记录.log``。

    位图行包（每张标签几百条）**始终写入文件**，但只在
    ``verbose_rows=True`` 时打到控制台，避免把关键的握手/设置命令淹掉。
    """

    ROW_COMMANDS = {
        pkt.CMD_PRINT_BITMAP_ROW,
        pkt.CMD_PRINT_BITMAP_ROW_INDEXED,
        pkt.CMD_PRINT_EMPTY_ROW,
    }

    def __init__(self, path: str = "打印记录.log", console: bool = True,
                 verbose_rows: bool = False) -> None:
        # 按天分文件、追加写：以前每次启动都以 "w" 覆盖，上一张的排查记录就没了
        self.path = self._dated_path(os.path.abspath(path))
        self.console = console
        self.verbose_rows = verbose_rows
        self._lock = threading.Lock()
        self._lines = 0
        self._started = datetime.datetime.now()
        try:
            fresh = not os.path.exists(self.path)
            # 首次写 utf-8-sig（带 BOM，记事本能正确识别中文），之后追加不再重复 BOM
            with open(self.path, "a", encoding="utf-8-sig" if fresh else "utf-8") as f:
                f.write(f"\n===== 会话开始 {self._started:%Y-%m-%d %H:%M:%S} =====\n")
        except OSError:
            pass
        self._trim_if_big()
        self._prune()

    @staticmethod
    def _dated_path(base: str) -> str:
        root, ext = os.path.splitext(base)
        return f"{root}-{datetime.date.today():%Y%m%d}{ext}"

    def _trim_if_big(self) -> None:
        """单日日志超过上限时只保留末尾一段。

        前半截对排障没用，但会把文件撑大；截断比另开备份文件简单，
        也不会在目录里留一堆 .1 .2 .3。
        """
        try:
            if not os.path.exists(self.path):
                return
            if os.path.getsize(self.path) <= MAX_LOG_BYTES:
                return
            keep = MAX_LOG_BYTES // 2
            with open(self.path, "rb") as f:
                f.seek(-keep, os.SEEK_END)
                tail = f.read()
            cut = tail.find(b"\n")          # 从整行处切开，避免半行
            if cut > 0:
                tail = tail[cut + 1:]
            with open(self.path, "wb") as f:
                f.write(b"\xef\xbb\xbf")     # 补回 UTF-8 BOM，记事本才认中文
                f.write("（更早的日志已自动清理）\n".encode("utf-8"))
                f.write(tail)
        except OSError:
            pass

    def _prune(self, keep: int = KEEP_DAYS) -> None:
        """只保留最近 N 天的记录，避免目录堆成山。出错直接跳过。"""
        folder = os.path.dirname(self.path)
        stem = os.path.basename(os.path.splitext(self.path)[0]).rsplit("-", 1)[0]
        ext = os.path.splitext(self.path)[1]
        try:
            names = sorted(n for n in os.listdir(folder)
                           if n.startswith(stem + "-") and n.endswith(ext))
        except OSError:
            return
        for name in names[:-keep]:
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass

    # --------------------------------------------------------- 基础

    def _emit(self, line: str, console: bool = True) -> None:
        with self._lock:
            self._lines += 1
            stamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
            text = f"[{stamp}] {line}"
            if self.console and console:
                # 打包成 exe 后控制台可能用 cp1252 编码，中文会抛 UnicodeEncodeError；
                # --windowed 时 sys.stdout 干脆是 None。这属于"打不出日志"，不能拖垮程序
                try:
                    print(text, flush=True)
                except (UnicodeEncodeError, AttributeError, OSError, ValueError):
                    pass
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(text + "\n")
            except OSError:
                pass

    def event(self, message: str) -> None:
        """记录一条普通事件（连接、选择打印流程、错误等）。"""
        self._emit(f"** {message}")

    def raw_out(self, data: bytes) -> None:
        self._emit(f">> {hex_bytes(data)}")

    def raw_in(self, data: bytes) -> None:
        # 原始字节只写文件（解析行里已经带了 hex），避免控制台刷屏
        self._emit(f"<< {hex_bytes(data)}   (原始)", console=False)

    def orphan_bytes(self, data: bytes) -> None:
        """收到字节但解不出完整封包时打到控制台，多半是分包或格式问题。"""
        self._emit(f"!! 收到字节但未解出封包：{hex_bytes(data)}", console=True)

    def packet(self, direction: str, p: pkt.NiimbotPacket) -> None:
        arrow = ">>" if direction == "out" else "<<"
        is_row = p.command in self.ROW_COMMANDS
        self._emit(f"{arrow} {hex_bytes(p.to_bytes()):52s} {describe(p)}",
                   console=self.verbose_rows or not is_row)

    def done(self) -> str:
        elapsed = (datetime.datetime.now() - self._started).total_seconds()
        self._emit(f"** 记录结束，共 {self._lines} 行，用时 {elapsed:.1f} 秒")
        return self.path


# 模块级默认实例；client 会往这里写。
tracer: Tracer | None = None


def enable(path: str | None = None, console: bool = True) -> Tracer:
    global tracer
    tracer = Tracer(path or _data_path("打印记录.log"), console)
    tracer.event(f"日志文件：{tracer.path}")
    return tracer


def get() -> Tracer | None:
    return tracer
