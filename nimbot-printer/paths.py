"""程序的可写数据目录。

安装到 Program Files 后程序目录不可写，所以设置和日志要放到
``%LOCALAPPDATA%\\Niimbot打印助手``；直接跑源码时就放在代码目录，
方便开发时查看。
"""

from __future__ import annotations

import os
import sys

APP_NAME = "Niimbot打印助手"


def data_dir() -> str:
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        d = os.path.join(base, APP_NAME)
    else:
        d = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(d, exist_ok=True)
    return d


def data_path(name: str) -> str:
    return os.path.join(data_dir(), name)
