"""加载随程序附带的 Nothing 官方字体。

字体用 ``AddFontResourceExW`` + ``FR_PRIVATE`` 注册，**只对当前进程生效**：
不装进系统、不写注册表、不影响其它程序，关掉程序就没了。

用法：``import fonts`` 时自动注册，必须早于 ``tk.Tk()`` 创建 ——
Tk 在初始化时会枚举一次字体族，之后注册的字体它可能看不到。

字体来源与版权见同目录 ``assets/fonts/NOTICE.md``。
"""

from __future__ import annotations

import ctypes
import glob
import os
import sys

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts")

FR_PRIVATE = 0x10  # 进程私有，不污染系统

# 程序里实际会用到的族名（Tk 识别出来的名字，注意 Ndot 带字号后缀）
FAMILY_DOT = "Ndot 55"        # 点阵，用于 logo 式大字
FAMILY_TYPE = "NType 82"       # 打字机风格正文
FAMILY_MONO = "NType 82 Mono"  # 等宽，技术标签与数字

_loaded: list[str] = []


def load_all() -> int:
    """把 assets/fonts 下的字体注册到当前进程，返回成功数量。"""
    if sys.platform != "win32":
        return 0
    if not os.path.isdir(FONT_DIR):
        return 0

    count = 0
    paths = sorted(glob.glob(os.path.join(FONT_DIR, "*.otf")) +
                   glob.glob(os.path.join(FONT_DIR, "*.ttf")))
    for path in paths:
        try:
            added = ctypes.windll.gdi32.AddFontResourceExW(
                os.path.abspath(path), FR_PRIVATE, 0)
        except Exception:  # noqa: BLE001 - 缺字体不该拦住程序启动
            added = 0
        if added:
            count += 1
            _loaded.append(os.path.basename(path))
    return count


def loaded() -> list[str]:
    """已经注册成功的字体文件名。"""
    return list(_loaded)


def available() -> bool:
    return bool(_loaded)


load_all()
