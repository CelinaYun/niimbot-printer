"""把用户的设置和上次的内容存在程序目录下，下次打开自动恢复。

存到 ``打印设置.json``，损坏或缺失就退回默认值，不影响启动。
"""

from __future__ import annotations

import json
import os

from paths import data_path

FILENAME = "打印设置.json"

DEFAULTS = {
    # 首次启动留空：以前这里塞了「资产编号 NB-2026-001 / 负责人 张三」的示例，
    # 用户会误以为是自己的内容，直接打出带示例文字的标签
    "text": "",
    "label_length_mm": 40.0,
    "font_size": 28,
    "bold": False,
    "align": "左",
    "orientation": "正常",
    "portrait": False,
    "density": 2,
    "quantity": "1",
    "label_type": "间隙标签",
    "print_task": "自动",
    "qr_value": "",
    "qr_size": "64",
    "barcode_value": "",
    "barcode_symbology": "Code128",
    "image_path": "",
    "batch": False,
    "extra_order": "图片·条码·二维码",
    "templates": {},
    "templates_seeded": 0,
    "templates_removed": [],
    "lang": "zh",
    "theme": "dark",
    "window_geometry": "",
    "conn_mode": "蓝牙",
    "device": "",
}


def _path(base: str | None = None) -> str:
    return os.path.join(base, FILENAME) if base else data_path(FILENAME)


def load(base: str | None = None) -> dict:
    """读取设置；文件缺失或损坏时返回默认值。"""
    data = dict(DEFAULTS)
    try:
        with open(_path(base), encoding="utf-8-sig") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            for key in DEFAULTS:
                if key in saved:
                    data[key] = saved[key]
    except (OSError, ValueError):
        pass
    return data


def save(values: dict, base: str | None = None) -> str:
    """只保留已知字段，写盘。返回文件路径。"""
    data = {k: values.get(k, DEFAULTS[k]) for k in DEFAULTS}
    path = _path(base)
    try:
        with open(path, "w", encoding="utf-8-sig") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
    return path
