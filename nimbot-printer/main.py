"""NIIMBOT 打印助手入口。"""

import ctypes
import sys

from gui import main

# 命名互斥体：同一台机器只允许一个实例。蓝牙只有一路，开两个会互相抢连接
_MUTEX_NAME = "Local\\NIIMBOT_Printer_GUI"
_ERROR_ALREADY_EXISTS = 183

_mutex_handle = None  # 必须留引用，句柄被回收就等于没加锁


def _force_utf8() -> None:
    """把标准输出/错误改成 UTF-8。

    打包成 exe 后，控制台默认按系统代码页（简体中文 Windows 上是 cp936，
    英文环境是 cp1252）编码，打印中文会抛 UnicodeEncodeError；
    ``--windowed`` 模式下 sys.stdout 干脆是 None。
    这一层只是让日志能打出来，失败也不该拦住程序启动。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def _already_running() -> bool:
    """已有实例在跑就返回 True。

    用命名互斥体判断：进程退出时系统自动释放，不会像锁文件那样留残留。
    """
    global _mutex_handle
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _mutex_handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        return ctypes.get_last_error() == _ERROR_ALREADY_EXISTS
    except Exception:  # noqa: BLE001 - 非 Windows 或调用失败就当没有锁
        return False


def _focus_existing() -> bool:
    """把已经在跑的那个窗口叫到最前面，返回是否找到。"""
    try:
        import ctypes.wintypes as wt

        user32 = ctypes.windll.user32
        found: list[int] = []

        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def enum_proc(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if "NIIMBOT" in buf.value:
                    found.append(hwnd)
            return True

        user32.EnumWindows(enum_proc, 0)
        if not found:
            return False
        hwnd = found[0]
        user32.ShowWindow(hwnd, 9)          # SW_RESTORE，最小化过也能拉起来
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:  # noqa: BLE001
        return False


def _warn(text: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, text, "NIIMBOT 打印助手", 0x40)
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    _force_utf8()
    if _already_running():
        # 找到原窗口就直接切过去；找不到（被关了或窗口还没画出来）就提示一下
        if not _focus_existing():
            _warn("NIIMBOT 打印助手已经在运行了。\n请在任务栏里找到它的窗口。")
        sys.exit(0)
    main()
