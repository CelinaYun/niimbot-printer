"""连接层：蓝牙 BLE（bleak）与 USB 串口（pyserial）。

两条通道共用同一套封包格式；BLE 通知会分片，由上层 :mod:`niimbot.packet`
的 :func:`parse_packet_bundle` 负责重组。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from abc import ABC, abstractmethod
from typing import Callable

log = logging.getLogger("niimbot.transport")

# NIIMBOT 通用 GATT 服务与特征（同一特征既写又通知）
SERVICE_UUID = "e7810a71-73ae-499d-8c15-faa9aef0c3f2"
CHAR_UUID = "bef8d6c9-9c21-4c9e-b632-bd58c1009f9f"

# 打印机蓝牙名称常见前缀
NAME_PREFIXES = (
    "NIIMBOT",
    "D11",
    "D110",
    "D11S",
    "B1",
    "B21",
    "B3",
    "B16",
    "B18",
    "H1",
    "N1",
    "P1",
    "P2",
)

PACKET_INTERVAL_S = 0.01  # 两包之间的最小间隔


class TransportError(Exception):
    pass


class Transport(ABC):
    def __init__(self) -> None:
        self._on_receive: Callable[[bytes], None] | None = None
        self._on_disconnect: Callable[[], None] | None = None

    def set_receive_callback(self, cb: Callable[[bytes], None] | None) -> None:
        self._on_receive = cb

    def set_disconnect_callback(self, cb: Callable[[], None] | None) -> None:
        """连接意外断开时回调（蓝牙掉线、USB 被拔）。"""
        self._on_disconnect = cb

    def _notify_disconnect(self) -> None:
        cb = self._on_disconnect
        if cb is not None:
            try:
                cb()
            except Exception:  # noqa: BLE001
                pass

    def _deliver(self, data: bytes) -> None:
        if data and self._on_receive:
            self._on_receive(data)

    async def raw_exchange(self, payload: bytes, wait: float = 1.5) -> bytes:
        """发送原始字节并收集一段时间内的原始回包，用于探测设备身份。"""
        buf = bytearray()
        prev = self._on_receive
        self.set_receive_callback(buf.extend)
        try:
            await self.write(payload)
            await asyncio.sleep(wait)
        finally:
            self.set_receive_callback(prev)
        return bytes(buf)

    @abstractmethod
    async def open(self, target: str) -> str:
        """连接目标设备，返回设备名称。"""

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def write(self, data: bytes) -> None: ...

    @property
    @abstractmethod
    def kind(self) -> str: ...


class BleTransport(Transport):
    """蓝牙低功耗通道。"""

    def __init__(self) -> None:
        super().__init__()
        self._client = None
        self._char_uuid: str | None = None

    @property
    def kind(self) -> str:
        return "ble"

    async def open(self, target: str) -> str:
        from bleak import BleakClient

        self._client = BleakClient(target, disconnected_callback=self._handle_disconnect)
        try:
            await self._client.connect(timeout=20.0)
        except Exception as e:  # noqa: BLE001 - bleak 抛的是实现相关异常
            raise TransportError(f"蓝牙连接失败：{e}") from e

        if not self._client.is_connected:
            raise TransportError("蓝牙连接失败：设备未连接")

        self._char_uuid = await self._find_characteristic()
        await self._client.start_notify(self._char_uuid, self._notify_handler)
        name = self._client.address
        return str(name)

    def _handle_disconnect(self, _client) -> None:
        log.info("BLE 断开")
        self._notify_disconnect()

    async def _find_characteristic(self) -> str:
        client = self._client
        assert client is not None

        # 优先使用已知 NIIMBOT 特征
        try:
            svcs = client.services
            for svc in svcs:
                for char in svc.characteristics:
                    if str(char.uuid).lower() == CHAR_UUID:
                        return CHAR_UUID
        except Exception:  # noqa: BLE001
            pass

        # 兜底：找同时支持 notify + write-without-response 的特征
        for svc in client.services:
            if len(str(svc.uuid)) < 8:
                continue
            for char in svc.characteristics:
                props = {p.lower() for p in char.properties}
                if "notify" in props and ("write-without-response" in props or "write" in props):
                    return str(char.uuid)

        raise TransportError("未找到可用的打印特征（notify + write）")

    def _notify_handler(self, _handle: int, data: bytearray) -> None:
        self._deliver(bytes(data))

    async def write(self, data: bytes) -> None:
        if self._client is None or not self._client.is_connected:
            raise TransportError("蓝牙未连接")
        assert self._char_uuid is not None
        try:
            await self._client.write_gatt_char(self._char_uuid, data, response=False)
        except Exception as e:  # noqa: BLE001
            raise TransportError(f"蓝牙写入失败：{e}") from e
        await asyncio.sleep(PACKET_INTERVAL_S)

    async def close(self) -> None:
        if self._client is not None:
            try:
                if self._client.is_connected:
                    await self._client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            self._client = None


class SerialTransport(Transport):
    """USB 虚拟串口通道（115200 8N1）。"""

    def __init__(self) -> None:
        super().__init__()
        self._port = None
        self._reader_task: asyncio.Task | None = None

    @property
    def kind(self) -> str:
        return "serial"

    async def open(self, target: str) -> str:
        import serial

        try:
            self._port = serial.Serial(target, baudrate=115200, timeout=0.2, write_timeout=5)
        except Exception as e:  # noqa: BLE001
            raise TransportError(f"串口打开失败：{e}") from e

        self._reader_task = asyncio.get_running_loop().create_task(self._read_loop())
        return target

    async def _read_loop(self) -> None:
        assert self._port is not None
        loop = asyncio.get_running_loop()
        while self._port is not None and self._port.is_open:
            try:
                waiting = await loop.run_in_executor(None, lambda: self._port.in_waiting)
                if waiting:
                    data = await loop.run_in_executor(None, self._port.read, waiting)
                    self._deliver(data)
                else:
                    await asyncio.sleep(0.02)
            except Exception:  # noqa: BLE001
                break
        # 串口被拔掉或读失败：让上层把状态改成「已断开」，别继续显示「已连接」
        if self._port is not None and not self._port.is_open:
            self._notify_disconnect()

    async def write(self, data: bytes) -> None:
        if self._port is None or not self._port.is_open:
            raise TransportError("串口未连接")
        try:
            self._port.write(data)
        except Exception as e:  # noqa: BLE001
            raise TransportError(f"串口写入失败：{e}") from e
        await asyncio.sleep(PACKET_INTERVAL_S)

    async def close(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._reader_task = None
        if self._port is not None:
            try:
                self._port.close()
            except Exception:  # noqa: BLE001
                pass
            self._port = None


async def scan_ble_devices(timeout: float = 6.0) -> list[tuple[str, str]]:
    """扫描附近的蓝牙设备，返回 ``[(名称, 地址), ...]``。

    优先返回名称像 NIIMBOT 打印机的设备。
    """
    from bleak import BleakScanner

    try:
        devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
    except Exception as e:  # noqa: BLE001
        raise TransportError(f"蓝牙扫描失败：{e}") from e

    found: list[tuple[str, str]] = []
    for addr, (dev, adv) in devices.items():
        name = (dev.name or adv.local_name or "").strip()
        if not name:
            continue
        found.append((name, addr))

    def rank(item: tuple[str, str]) -> int:
        upper = item[0].upper()
        for i, prefix in enumerate(NAME_PREFIXES):
            if upper.startswith(prefix):
                return i
        return len(NAME_PREFIXES)

    found.sort(key=rank)
    return found


def list_serial_ports() -> list[tuple[str, str]]:
    """列出串口，返回 ``[(显示名, 端口), ...]``。"""
    from serial.tools import list_ports

    out: list[tuple[str, str]] = []
    for p in list_ports.comports():
        desc = f"{p.device} — {p.description}"
        out.append((desc, p.device))
    return out


class BackgroundLoop:
    """在后台线程跑一条 asyncio 事件循环，供 tkinter 同步调用。"""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="niimbot-io", daemon=True)
        self._started = threading.Event()
        self._thread.start()
        self._started.wait(5)

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._started.set()
        self._loop.run_forever()

    def submit(self, coro):
        """提交协程，返回 concurrent.futures.Future。"""
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def call(self, coro, timeout: float | None = None):
        """提交协程并阻塞等待结果。"""
        return self.submit(coro).result(timeout)

    def stop(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=3)
