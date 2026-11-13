"""NIIMBOT 打印助手 —— Nothing OS 风格界面。

左侧写字并预览，右侧设置与打印。底层 I/O 在后台 asyncio 线程里跑。
"""

from __future__ import annotations

import ctypes
import hashlib
import datetime
import io
import json
import os
import queue
import re
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image as PILImage

import fluent as fl
import fonts  # 导入即把 assets/fonts 下的官方字体注册到本进程（必须早于 tk.Tk）
import settings
from i18n import fmt, get_lang, set_lang, tr, untr
from paths import data_path
from version import __version__
from fluent import (
    ACCENT,
    BG,
    CARD,
    CARD_ALT,
    CARD_BORDER,
    CONTROL_BG,
    CONTROL_BORDER,
    DIVIDER,
    TEXT,
    TEXT_DISABLED,
    TEXT_ON_ACCENT,
    TEXT_SECONDARY,
    Theme,
)
from niimbot import trace as trace_mod
from niimbot.client import (
    TASK_AUTO,
    TASK_B1,
    TASK_D110,
    TASK_D110M_V4,
    NiimbotClient,
    PrintCancelled,
    PrintOptions,
    density_range_for,
)
from niimbot.encoder import ORIENTATIONS, simulate_output
from niimbot.transport import (
    BackgroundLoop,
    BleTransport,
    SerialTransport,
    list_serial_ports,
    scan_ble_devices,
)
from render import (
    LayoutResult,
    mm_to_px,
    render_diagnostic_label,
    render_landscape_label,
    render_portrait_label,
    render_test_label,
)

APP_TITLE = "NIIMBOT 打印助手"


_TkLabel = tk.Label


def refresh_palette() -> None:
    """把 fluent 当前的调色板同步到本模块的全局名字。

    `from fluent import BG` 是导入那一刻的值拷贝，切主题后不会自己变，
    所以每次重建界面前都要跑一遍。
    """
    g = globals()
    for name in fl.THEME_KEYS:
        g[name] = getattr(fl, name)
    g["MUTED"] = g["TEXT_SECONDARY"]
    g["WARN"] = g["DANGER"]


def Label(parent, text: str = "", **kwargs):
    """带翻译的 Label —— 省得每个调用点都写一遍 tr()。"""
    return _TkLabel(parent, text=tr(text), **kwargs)

# 名字以这些开头的，基本可以确定是标签打印机
_PRINTER_PREFIXES = ("NIIMBOT", "D11", "D110", "B1", "B21", "B3S", "D101")
# 名字里带这些的，基本不可能是打印机（耳机、手表、手机、电视……）
_NON_PRINTER_HINTS = (
    "AIRPOD", "BUDS", "HEADSET", "HEADPHONE", "SPEAKER", "SOUNDBAR", "AUDIO",
    "WATCH", "BAND", "TRACKER", "FITNESS",
    "PHONE", "IPHONE", "IPAD", "MACBOOK", "REDMI", "XIAOMI", "HUAWEI",
    "GALAXY", "ONEPLUS", "OPPO", "VIVO", "HONOR",
    "KEYBOARD", "MOUSE", "TV", "DISPLAY", "MONITOR", "OBD", "BEACON",
)


def device_kind(name: str) -> str:
    """printer / other / unknown —— 对得上机型前缀的、明显不是的、看不出来的。"""
    upper = name.upper()
    if "NIIMBOT" in upper or any(upper.startswith(p) for p in _PRINTER_PREFIXES):
        return "printer"
    if any(h in upper for h in _NON_PRINTER_HINTS):
        return "other"
    return "unknown"


def apply_titlebar(root: tk.Misc, dark: bool) -> None:
    """把窗口标题栏染成和界面同色。

    不染的话，系统浅色时深色界面顶上会顶着一条白条。Win11 才能自定义颜色，
    更早的系统只切深色模式开关；失败静默跳过。
    """
    if sys.platform != "win32":
        return
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()

        # 沉浸式深色模式：Win10 1809+ 用属性 20，早期版本用 19
        flag = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(flag), ctypes.sizeof(flag))

        # 自定义标题栏颜色要 Win11（build 22000+）
        if sys.getwindowsversion().build >= 22000:
            caption = 0x000000 if dark else 0xF2F2F2   # COLORREF 是 0x00BBGGRR
            text = 0xFFFFFF if dark else 0x000000
            # 35=标题栏底色 36=标题文字 34=边框
            for attr, value in ((35, caption), (36, text), (34, caption)):
                color = ctypes.c_int(value)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(color), ctypes.sizeof(color))
    except Exception:  # noqa: BLE001
        pass


LABEL_PRESETS = ["30 mm", "40 mm", "50 mm", "75 mm", "109 mm", "自定义"]
LABEL_TYPE_NAMES = ["间隙标签", "透明标签", "连续标签"]
LABEL_TYPE_VALUES = [1, 5, 3]
SYMBIOLOGIES = [("Code128", "code128"), ("EAN-13", "ean13"),
                ("UPC-A", "upca"), ("Code39", "code39")]

# 附件在标签右侧的排列顺序（从左到右）
EXTRA_ORDERS = {
    "图片·条码·二维码": "pic,bc,qr",
    "二维码·条码·图片": "qr,bc,pic",
}

HISTORY_FILE = "打印记录.jsonl"

# 浓度档位文案：D110 系列 1-3 档，D110_M / D11_H 是 1-5 档
# 开箱即用的模板：覆盖最常见的几种标签。
# {date} / {time} / {year} 在套用时替换成当天日期，不用每次手改。
BUILTIN_TEMPLATES = {
    "资产标签": "ASSET\nAV-0001234\n张三\n{date}",
    "PCBA 料号": "MODEL-A\nPCBA-0001234\nA0\n{date}",
    "CPU 型号": "CPU Ryzen5 216\n4.70GHz 6CORE\n100-000002278",
    "SSD 标签": "SSD_1TB_M2_2280\nSN:ABC123456\n{date}",
    "端口标识": "LAN-01\nSW-A12\nGi0/1",
    "样机勿动": "SAMPLE\nDVT-01\n请勿移动",
    # ---- 带分隔栏：整行 `---` 画细线、`===` 画粗线，长度自动撑满纸宽
    "资产·分栏": "ASSET\n---\nAV-0001234\n张三\n---\n{date}",
    "料号·分栏": "MODEL-A\n---\nPCBA-0001234\nA0\n---\n{date}",
    "工牌·分栏": "张三\n===\n研发部\n---\n{date}",
}

# 旧版内置模板的 sha256 前 16 位。命中说明用户没改过，可安全替换；
# 存哈希是为了仓库里不再出现当初的示例内容。
_LEGACY_TEMPLATE_HASHES = {
    "9ca9e7a5584f736a",
    "c9d6003f52ea28ec",
    "684d4bd5b2257bf2",
    "c999fe1ea1faefa3",
    "04f5a6db902ff816",
}


def _is_legacy_template(text: str) -> bool:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] in _LEGACY_TEMPLATE_HASHES

# 内置模板的版本号：加了新模板就把这里 +1，老用户下次启动会补上新增的那些
TEMPLATE_SEED_VERSION = 3

DENSITY_WORDS_3 = ["浅", "标准", "深"]
DENSITY_WORDS_5 = ["最浅", "浅", "标准", "深", "最深"]

ACCENT = fl.ACCENT
DANGER = fl.DANGER
MUTED = TEXT_SECONDARY
WARN = DANGER


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        # 语言和配色都必须在建界面之前定下来，否则第一屏是错的
        _saved = settings.load()
        set_lang(str(_saved.get("lang", "zh")))
        fl.set_theme(str(_saved.get("theme", "dark")))
        refresh_palette()
        root.title(f"{tr(APP_TITLE)}  v{__version__}")
        root.configure(bg=BG)
        # 只保证「能用」，不硬要求 1040×830 —— 小屏笔记本也能完整操作
        root.minsize(fl.S(880), fl.S(560))
        root.geometry(self._default_geometry())

        self.theme = Theme(root)
        self.loop = BackgroundLoop()
        self.client: NiimbotClient | None = None
        self.preview_image = None
        self._preview_photo = None
        self._ui_queue: queue.Queue = queue.Queue()
        self._busy = False
        self._printing = False
        self._templates: dict[str, str] = {}
        self._templates_seeded = 0
        self._removed_templates: set[str] = set()
        self._image_path = ""
        self._strip_dots = 96  # 打印头点数，连接后按机器报的值更新

        self.trace = trace_mod.enable("打印记录.log", console=True)
        self.trace.event(f"屏幕 {root.winfo_screenwidth()}x{root.winfo_screenheight()}"
                         f" · 缩放 {fl.get_scale()} · 几何 {self._default_geometry()}")

        self._build_style()
        self._build_ui()
        self._bind_shortcuts()
        self._restore_settings()
        self._render_preview()
        try:
            import platform
            self._log(f"NIIMBOT 打印助手 v{__version__} · Python "
                      f"{sys.version.split()[0]} · {platform.platform()}")
        except Exception:  # noqa: BLE001
            self._log(f"NIIMBOT 打印助手 v{__version__}")
        self._log(fmt("协议调用记录已开启，写入：{path}", path=self.trace.path))
        self._log("每次连接、发送命令、收到应答都会记录，出问题时把这个文件发给我。")
        self._log("快捷键：Ctrl+回车 打印 · Ctrl+T 测试页 · Ctrl+D 诊断页 · "
                  "Ctrl+F 走纸 · Ctrl+O 选图片 · F5 扫描 · Esc 取消")

        root.after(60, self._poll_ui_queue)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _default_geometry(self) -> str:
        """按屏幕可用区域算初始尺寸：不超过屏幕，居中偏上。"""
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w = min(fl.S(1240), int(sw * 0.92))
        h = min(fl.S(840), int(sh * 0.88))
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2 - fl.S(16))
        return f"{w}x{h}+{x}+{y}"

    # ------------------------------------------------------------ 样式

    def _build_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            # clam 才允许完整改色；vista 会强制走系统浅色主题
            style.theme_use("clam")
        except tk.TclError:
            pass

        t = self.theme
        style.configure(".", background=BG, foreground=TEXT, fieldbackground=CONTROL_BG,
                        bordercolor=fl.CONTROL_BORDER, darkcolor=CARD, lightcolor=CARD,
                        troughcolor=CARD_ALT, font=t.body)

        style.configure("TCombobox", fieldbackground=CONTROL_BG, background=CONTROL_BG,
                        foreground=TEXT, arrowcolor=TEXT_SECONDARY,
                        bordercolor=fl.CONTROL_BORDER, arrowsize=13, padding=4)
        style.map("TCombobox",
                  fieldbackground=[("readonly", CONTROL_BG), ("disabled", fl.CARD)],
                  foreground=[("readonly", TEXT), ("disabled", TEXT_DISABLED)],
                  selectbackground=[("readonly", CONTROL_BG)],
                  selectforeground=[("readonly", TEXT)],
                  bordercolor=[("focus", ACCENT)],
                  arrowcolor=[("active", TEXT)])

        # 下拉展开的那一层是独立 Listbox，要单独配色，否则弹出是白底
        self.root.option_add("*TCombobox*Listbox.background", fl.CARD_ALT)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", TEXT)

        style.configure("Vertical.TScrollbar", background=fl.CONTROL_BORDER, troughcolor=BG,
                        bordercolor=BG, arrowcolor=TEXT_SECONDARY, arrowsize=10,
                        width=8, relief="flat")
        style.map("Vertical.TScrollbar", background=[("active", fl.CONTROL_BORDER_HOVER)])

        style.configure("TScale", background=CARD, troughcolor=fl.CARD_BORDER,
                        bordercolor=CARD, lightcolor=CARD, darkcolor=CARD)
        style.configure("Horizontal.TScale", background=CARD, troughcolor=fl.CARD_BORDER)

        # tk 经典控件（Spinbox / Scale）由 tk 绘制，只能靠 option 数据库统一配色
        for pattern, value in (
            ("*Spinbox.background", fl.CARD_ALT),
            ("*Spinbox.foreground", TEXT),
            ("*Spinbox.insertBackground", TEXT),
            ("*Spinbox.buttonBackground", CONTROL_BG),
            ("*Spinbox.buttonForeground", TEXT_SECONDARY),
            ("*Spinbox.highlightBackground", BG),
            ("*Spinbox.highlightColor", ACCENT),
            ("*Spinbox.relief", "flat"),
            ("*Scale.background", CARD),
            ("*Scale.troughColor", fl.CARD_BORDER),
            ("*Scale.highlightBackground", CARD),
        ):
            self.root.option_add(pattern, value)

    # ------------------------------------------------------------ 界面骨架

    def _build_ui(self) -> None:
        self._build_topbar()
        self._build_banner()

        # 左右两列各自独立滚动：窗口变矮时右列的「打印」不会被左列的长内容
        # 顶出视口。整页共用一个滚动条时，滚左列会把右列一起带走。
        main = tk.Frame(self.root, bg=BG)
        main.pack(fill="both", expand=True, padx=fl.S(16), pady=(0, fl.S(16)))
        self.main = main

        self.left_scroll = fl.ScrollFrame(main, bg=BG)
        self.left_scroll.pack(side="left", fill="both", expand=True, padx=(0, fl.S(10)))
        left = self.left_scroll.body

        right_wrap = tk.Frame(main, bg=BG, width=fl.S(380))
        right_wrap.pack(side="right", fill="y")
        right_wrap.pack_propagate(False)
        self.right_scroll = fl.ScrollFrame(right_wrap, bg=BG)
        self.right_scroll.pack(fill="both", expand=True)
        right = self.right_scroll.body

        self._build_conn_card(left)
        self._build_text_card(left)
        self._build_preview_card(left)
        self._build_extra_card(left)

        self._build_settings_card(right)
        self._build_action_card(right)
        self._build_log_card(right)

        # 界面建完后统一挂滚轮（文字框自己会滚，已排除）
        self.left_scroll.bind_wheel_tree()
        self.right_scroll.bind_wheel_tree()

    def _build_banner(self) -> None:
        """顶部提示条：错误摆到眼前，不用去日志里翻。"""
        self.banner = tk.Frame(self.root, bg=fl.DANGER_SUBTLE,
                               highlightthickness=1, highlightbackground=fl.ACCENT_BORDER)
        self._banner_label = Label(
            self.banner, text="", bg=fl.DANGER_SUBTLE, fg=DANGER,
            font=self.theme.body, justify="left", anchor="w",
            wraplength=fl.S(860))
        self._banner_label.pack(side="left", fill="x", expand=True,
                                padx=(fl.S(12), fl.S(8)), pady=fl.S(8))
        self._banner_close = fl.FluentButton(self.banner, "知道了", self.hide_banner,
                                             style="subtle", theme=self.theme,
                                             height=fl.S(26))
        self._banner_close.pack(side="right", padx=(0, fl.S(8)))

    def show_banner(self, text: str, kind: str = "error") -> None:
        """在顶部显示一条提示，同时写进日志（两处都能看到）。"""
        text = tr(text)
        palette = {
            "error": (fl.DANGER_SUBTLE, DANGER, fl.ACCENT_BORDER),
            "info": (fl.CARD_ALT, fl.TEXT, fl.CONTROL_BORDER),
        }
        bg, fg, border = palette.get(kind, palette["error"])
        self.banner.configure(bg=bg, highlightbackground=border)
        self._banner_label.configure(text=text, bg=bg, fg=fg)
        if not self.banner.winfo_ismapped():
            self.banner.pack(fill="x", padx=fl.S(16), pady=(0, fl.S(8)),
                             before=self.main)

    def hide_banner(self) -> None:
        self.banner.pack_forget()

    # ------------------------------------------------------------ 语言

    def _on_lang_change(self) -> None:
        if not self._ui_alive():
            return
        want = "en" if self.lang_var.get() == "EN" else "zh"
        if want == get_lang():
            return
        set_lang(want)
        self._rebuild_ui()
        try:
            self._save_settings()
        except Exception:  # noqa: BLE001
            pass

    def _toggle_theme(self) -> None:
        if not self._ui_alive():
            return
        want = "light" if fl.get_theme() == "dark" else "dark"
        fl.set_theme(want)
        refresh_palette()
        self._rebuild_ui()
        try:
            self._save_settings()
        except Exception:  # noqa: BLE001
            pass

    def _ui_alive(self) -> bool:
        """窗口还在不在。

        变量被回收时 tkinter 会 unset 它，Tcl 的 unset 同样触发 write trace，
        于是旧变量的回调查到已经关掉的窗口上 —— 关窗瞬间要能安静退出。
        """
        try:
            return bool(self.root.winfo_exists())
        except Exception:  # noqa: BLE001
            return False

    def _rebuild_ui(self) -> None:
        """切换语言后整体重建界面。

        文案散在几十个控件里，逐个改属性容易漏；重建一遍最可靠。
        重建前把用户正在编辑的东西拍个快照，建完再填回去。
        """
        snap = {
            "text": self.text_box.get("1.0", "end").rstrip("\n"),
            "height": self.height_mm.get(),
            "font": self.font_size.get(),
            "qr": self.qr_size.get(),
            "qty": self.quantity.get(),
            "qr_value": self.qr_var.get(),
            "barcode_value": self.barcode_var.get(),
            "barcode_sym": untr(self.barcode_sym.get()),
            "label_type": untr(self.label_type.get()),
            "orientation": self._orientation(),
            "portrait": self._is_portrait(),
            "protocol": self._protocol(),
            "device": self.device_var.get(),
            "density": self._density_value(),
            "density_range": (self._density_lo, self._density_hi),
            "batch": self.batch_var.get(),
            "extra_order": untr(self.extra_order_var.get()),
            "log": self.log.get("1.0", "end").rstrip("\n"),
            "templates": dict(self._templates),
            "image": self._image_path,
            "conn_mode": self.conn_mode.get(),
        }

        refresh_palette()  # 主题可能在重建前刚被切过
        for widget in (self.topbar, self.banner, self.main):
            widget.destroy()

        self.root.configure(bg=BG)
        self._build_style()   # ttk 控件要按新配色重新配置
        self._build_ui()

        # ---- 把快照填回去
        self.text_box.delete("1.0", "end")
        self.text_box.insert("1.0", snap["text"])
        for widget, value in ((self.height_mm, snap["height"]),
                              (self.font_size, snap["font"]),
                              (self.qr_size, snap["qr"]),
                              (self.quantity, snap["qty"])):
            self._set_spin(widget, value)
        self._on_length_edited()  # 让规格下拉跟着长度走

        self.qr_var.set(snap["qr_value"])
        self.barcode_var.set(snap["barcode_value"])
        self.barcode_sym.set(tr(snap["barcode_sym"]))
        self.label_type.set(tr(snap["label_type"]))
        self.print_task_combo.set(tr(snap["protocol"]))
        self.rotate.set(tr(snap["orientation"]))
        self.extra_order_var.set(tr(snap["extra_order"]))
        self.conn_mode.set(snap["conn_mode"])
        if snap["device"]:
            self.device_combo["values"] = [snap["device"]]
            self.device_var.set(snap["device"])

        self._density_lo, self._density_hi = snap["density_range"]
        self.density_seg.set_options(self._density_words())
        self._set_density(snap["density"])

        self.portrait_var.set("竖版" if snap["portrait"] else "横版")
        self.batch_var.set(snap["batch"])
        self._templates = snap["templates"]
        self._refresh_templates()

        self._image_path = snap["image"]
        if self._image_path:
            self.image_label.config(text=os.path.basename(self._image_path), fg=TEXT)

        # 日志整体重来：旧内容是切换前语言写的，混着看更乱
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")
        self._log(fmt("界面语言已切换：{lang}",
                      lang="English" if get_lang() == "en" else "中文"))

        if self.client is not None:
            self._set_status("已连接", True)
        else:
            self._set_status("未连接", None)

        self._update_adv_hint()
        self._render_preview()
        apply_titlebar(self.root, fl.get_theme() == "dark")

    def _log_raw(self, text: str) -> None:
        """按原文写日志（不翻译）——切换语言时用来还原已有内容。"""
        self.log.config(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    # ------------------------------------------------------------ 快捷键

    def _bind_shortcuts(self) -> None:
        """全局快捷键。用 Ctrl 组合，避开输入框里的普通按键。"""
        table = {
            "<Control-Return>": self._print,
            "<Control-KP_Enter>": self._print,
            "<Control-t>": self._print_test,
            "<Control-d>": self._print_diag,
            "<Control-f>": self._feed,
            "<Control-o>": self._pick_image,
            "<Control-Shift-V>": self._paste_image,
            "<Control-Shift-v>": self._paste_image,
            "<F5>": self._scan_devices,
            "<Control-l>": self._clear_text,
            "<Escape>": self._on_escape,
        }
        for seq, fn in table.items():
            self.root.bind_all(seq, self._as_shortcut(fn))

    @staticmethod
    def _as_shortcut(fn):
        def wrapper(_event=None):
            fn()
            return "break"  # 别再往控件里传，避免在文字框里插入换行
        return wrapper

    def _clear_text(self) -> None:
        self.text_box.delete("1.0", "end")
        self._render_preview()

    def _on_escape(self) -> None:
        """Esc：打印中 = 取消，否则把焦点还给文字框。"""
        if self._printing:
            self._print()
            return
        self.text_box.focus_set()

    def _build_topbar(self) -> None:
        bar = tk.Frame(self.root, bg=BG, height=fl.S(56))
        bar.pack(fill="x", padx=fl.S(16), pady=(fl.S(14), fl.S(10)))
        self.topbar = bar

        # Ndot 的字形左边距是负的，不留一点空隙时首字母会被裁掉半边
        Label(bar, text="NIIMBOT", bg=BG, fg=TEXT,
              font=self.theme.title).pack(side="left", padx=(fl.S(3), 0))
        Label(bar, text="LABEL PRINTER", bg=BG, fg=ACCENT,
                 font=self.theme.label).pack(side="left", padx=(fl.S(10), 0),
                                             pady=(fl.S(7), 0))

        # Glyph 灯带：三格点亮的红色方块，呼应 Nothing 的机身灯效
        glyph = tk.Canvas(bar, width=fl.S(76), height=fl.S(14), bg=BG,
                          highlightthickness=0, bd=0)
        glyph.pack(side="left", padx=(fl.S(18), 0), pady=(fl.S(6), 0))
        for i in range(6):
            x = i * fl.S(12)
            glyph.create_rectangle(x, 0, x + fl.S(4), fl.S(14),
                                   fill=ACCENT if i < 3 else fl.CARD_BORDER, outline="")

        right = tk.Frame(bar, bg=BG)
        right.pack(side="right")

        self.status_pill = tk.Canvas(right, width=fl.S(190), height=fl.S(30), bg=BG,
                                     highlightthickness=0, bd=0)
        self.status_pill.pack(side="right", padx=(8, 0))
        self._status_pill_items = []
        self.status_text = Label(right, text="", bg=BG, fg=MUTED,
                                 font=self.theme.caption)
        self.status_text.pack(side="right")

        # 语言切换：放状态左边，随时可切
        self.lang_var = tk.StringVar(value="EN" if get_lang() == "en" else "ZH")
        self.lang_seg = fl.Segmented(right, ["ZH", "EN"], self.lang_var,
                                     theme=self.theme, height=fl.S(26))
        self.lang_seg.pack(side="right", padx=(0, fl.S(14)))
        self.lang_var.trace_add("write", lambda *_: self._on_lang_change())

        # 主题切换：按钮上写的是「点一下会变成什么」
        self.theme_btn = fl.FluentButton(
            right, "浅色" if fl.get_theme() == "dark" else "深色",
            self._toggle_theme, style="subtle", theme=self.theme,
            height=fl.S(26))
        self.theme_btn.pack(side="right", padx=(0, fl.S(10)))

        self._set_status("未连接", None)

    # ------------------------------------------------------------ 左列


    def _build_conn_card(self, parent) -> None:
        card = fl.Card(parent, title="打印机连接", theme=self.theme)
        card.pack(fill="x", pady=(0, 12))
        body = card.body

        row = tk.Frame(body, bg=CARD)
        row.pack(fill="x", pady=(2, 0))

        self.conn_mode = tk.StringVar(value="蓝牙")
        fl.Segmented(row, ["蓝牙", "USB 串口"], self.conn_mode,
                     theme=self.theme).pack(side="left")

        self.device_var = tk.StringVar()
        self.device_combo = ttk.Combobox(row, textvariable=self.device_var,
                                         state="readonly", width=24)
        self.device_combo.pack(side="left", fill="x", expand=True,
                               padx=(fl.S(12), 0))

        # 按钮单独一行：英文的 CONNECT / DISCONNECT 比中文长得多，
        # 挤在同一行会被裁掉
        row2 = tk.Frame(body, bg=CARD)
        row2.pack(fill="x", pady=(fl.S(8), 0))

        self.scan_btn = fl.FluentButton(row2, "扫描", self._scan_devices,
                                        style="standard", theme=self.theme)
        self.scan_btn.pack(side="left", padx=(0, fl.S(8)))

        self.connect_btn = fl.FluentButton(row2, "连接", self._connect,
                                           style="accent", theme=self.theme)
        self.connect_btn.pack(side="left", padx=(0, fl.S(8)))

        self.disconnect_btn = fl.FluentButton(row2, "断开", self._disconnect,
                                              style="subtle", theme=self.theme)
        self.disconnect_btn.pack(side="left")
        self.disconnect_btn.set_enabled(False)

    def _build_text_card(self, parent) -> None:
        card = fl.Card(parent, title="输入文字", theme=self.theme)
        card.pack(fill="x", pady=(0, 12))

        Label(card.body, text="每行一句，沿标签长度方向横向打印",
                 bg=CARD, fg=MUTED, font=self.theme.caption).pack(anchor="w", pady=(0, 6))

        # 常用内容存成模板，下次选一下就行
        r = tk.Frame(card.body, bg=CARD)
        r.pack(fill="x", pady=(0, fl.S(6)))
        Label(r, text="模板", bg=CARD, fg=MUTED, font=self.theme.body).pack(
            side="left", padx=(0, fl.S(8)))
        self.template_var = tk.StringVar()
        self.template_box = ttk.Combobox(r, textvariable=self.template_var,
                                         state="readonly", width=18)
        self.template_box.pack(side="left")
        self.template_box.bind("<<ComboboxSelected>>", lambda e: self._apply_template())
        fl.FluentButton(r, "存为模板", self._save_template, style="subtle",
                        theme=self.theme, height=fl.S(26)).pack(side="left", padx=(fl.S(8), fl.S(4)))
        fl.FluentButton(r, "删除", self._delete_template, style="subtle",
                        theme=self.theme, height=fl.S(26)).pack(side="left")

        wrap = tk.Frame(card.body, bg=CARD, highlightthickness=1,
                        highlightbackground=fl.CONTROL_BORDER, highlightcolor=ACCENT)
        wrap.pack(fill="x")
        self.text_box = tk.Text(wrap, height=5, font=self.theme.body,
                                relief="flat", bd=0, wrap="word", undo=True,
                                bg=fl.CARD_ALT, fg=TEXT, insertbackground=TEXT,
                                selectbackground=fl.ACCENT, selectforeground=TEXT,
                                padx=10, pady=8, highlightthickness=0)
        self.text_box.pack(fill="both", expand=True)
        self.text_box.bind("<KeyRelease>", lambda e: self._render_preview())
        self.text_box.bind("<FocusIn>", lambda e: wrap.config(highlightbackground=ACCENT))
        self.text_box.bind("<FocusOut>", lambda e: wrap.config(highlightbackground=fl.CONTROL_BORDER))

        # 批量：一行一个标签，省得改一次打一张
        r = tk.Frame(card.body, bg=CARD)
        r.pack(fill="x", pady=(fl.S(10), 0))
        self.batch_var = tk.BooleanVar(value=False)
        fl.Toggle(r, self.batch_var, theme=self.theme).pack(side="left", padx=(0, fl.S(8)))
        Label(r, text="每行打一张", bg=CARD, fg=TEXT,
                 font=self.theme.body).pack(side="left")
        Label(r, text="一行一个标签，适合连着打多个料号", bg=CARD, fg=MUTED,
                 font=self.theme.caption).pack(side="left", padx=(fl.S(10), 0))
        self.batch_var.trace_add("write", lambda *_: self._on_batch_toggle())

    def _build_preview_card(self, parent) -> None:
        card = fl.Card(parent, title="预览", theme=self.theme)
        card.pack(fill="x", pady=(0, 12))

        self.preview_canvas = tk.Canvas(card.body, height=fl.S(140), bg=fl.PREVIEW_BG,
                                        highlightthickness=1,
                                        highlightbackground=fl.DIVIDER, bd=0)
        self.preview_canvas.pack(fill="x")
        self.preview_canvas.bind("<Configure>", lambda e: self._render_preview())
        self.size_hint = Label(card.body, text="", bg=CARD, fg=MUTED,
                                  font=self.theme.label, justify="left")
        self.size_hint.pack(anchor="w", pady=(8, 0))
        self.warn_hint = Label(card.body, text="", bg=CARD, fg=WARN,
                                  font=self.theme.body, justify="left", wraplength=760)
        self.warn_hint.pack(anchor="w", pady=(2, 0))

    def _build_extra_card(self, parent) -> None:
        card = fl.Card(parent, title="附加内容（可选）", theme=self.theme)
        card.pack(fill="x", pady=(0, 12))

        r = tk.Frame(card.body, bg=CARD)
        r.pack(fill="x", pady=fl.S(2))
        Label(r, text="二维码", bg=CARD, fg=MUTED, width=8, anchor="w",
              font=self.theme.body).pack(side="left")
        self.qr_var = tk.StringVar()
        # 先摆右侧固定宽度的控件，会伸缩的输入框最后摆（吃剩余空间）。
        # 反过来写的话窗口一窄，「大小」就被挤出可视区。
        self.qr_size = tk.Spinbox(r, from_=32, to=96, width=4,
                                  command=self._render_preview,
                                  font=self.theme.label, relief="flat", bd=1)
        self.qr_size.delete(0, "end")
        self.qr_size.insert(0, "64")
        self.qr_size.pack(side="right")
        Label(r, text="大小", bg=CARD, fg=MUTED, font=self.theme.caption).pack(
            side="right", padx=(fl.S(6), fl.S(4)))
        fl.FluentEntry(r, self.qr_var, placeholder="填内容就生成，留空不打",
                       theme=self.theme).pack(side="left", fill="x", expand=True,
                                              padx=(0, fl.S(8)))
        self.qr_var.trace_add("write", lambda *_: self._render_preview())

        r = tk.Frame(card.body, bg=CARD)
        r.pack(fill="x", pady=fl.S(2))
        Label(r, text="条码", bg=CARD, fg=MUTED, width=8, anchor="w",
              font=self.theme.body).pack(side="left")
        self.barcode_var = tk.StringVar()
        self.barcode_sym = ttk.Combobox(r, values=[tr(s[0]) for s in SYMBIOLOGIES],
                                        state="readonly", width=9)
        self.barcode_sym.current(0)
        self.barcode_sym.pack(side="right")   # 同上：固定宽度的先占右侧
        self.barcode_sym.bind("<<ComboboxSelected>>", lambda e: self._render_preview())
        fl.FluentEntry(r, self.barcode_var, placeholder="留空不打",
                       theme=self.theme).pack(side="left", fill="x", expand=True,
                                              padx=(0, fl.S(8)))
        self.barcode_var.trace_add("write", lambda *_: self._render_preview())

        r = tk.Frame(card.body, bg=CARD)
        r.pack(fill="x", pady=(fl.S(2), 0))
        Label(r, text="图片", bg=CARD, fg=MUTED, width=8, anchor="w",
              font=self.theme.body).pack(side="left")
        # 按钮从右缘排起，状态文字最后占剩余空间 —— 窄窗口下按钮不会被挤掉
        fl.FluentButton(r, "清除", self._clear_image, style="subtle",
                        theme=self.theme, height=fl.S(28)).pack(side="right")
        fl.FluentButton(r, "粘贴", self._paste_image, style="subtle",
                        theme=self.theme, height=fl.S(28)).pack(
                            side="right", padx=(0, fl.S(6)))
        fl.FluentButton(r, "选择图片", self._pick_image, style="standard",
                        theme=self.theme, height=fl.S(28)).pack(
                            side="right", padx=(0, fl.S(6)))
        self.image_label = Label(r, text="未选择", bg=CARD, fg=MUTED,
                                 font=self.theme.caption)
        self.image_label.pack(side="left", fill="x", expand=True,
                              padx=(0, fl.S(8)))

        # 多个附件时谁在左谁在右
        r = tk.Frame(card.body, bg=CARD)
        r.pack(fill="x", pady=(fl.S(6), 0))
        Label(r, text="排列", bg=CARD, fg=MUTED, width=8, anchor="w",
                 font=self.theme.body).pack(side="left")
        self.extra_order_var = tk.StringVar(value=list(EXTRA_ORDERS)[0])
        order_box = ttk.Combobox(r, textvariable=self.extra_order_var,
                                 values=[tr(v) for v in EXTRA_ORDERS], state="readonly", width=16)
        order_box.pack(side="left")
        order_box.bind("<<ComboboxSelected>>", lambda e: self._render_preview())
        Label(r, text="附件在标签上的左右顺序", bg=CARD, fg=MUTED,
                 font=self.theme.caption).pack(side="left", padx=(fl.S(10), 0))

    # ------------------------------------------------------------ 右列

    def _build_settings_card(self, parent) -> None:
        card = fl.Card(parent, title="打印设置", theme=self.theme)
        card.pack(fill="x", pady=(0, 12))

        def row(label: str) -> tk.Frame:
            r = tk.Frame(card.body, bg=CARD)
            r.pack(fill="x", pady=4)
            Label(r, text=label, bg=CARD, fg=MUTED, width=8, anchor="w",
                     font=self.theme.body).pack(side="left")
            return r

        # 版式放最上面：它决定后面所有排版参数的含义
        r = row("版式")
        self.portrait_var = tk.StringVar(value="横版")
        fl.Segmented(r, ["横版", "竖版"], self.portrait_var, theme=self.theme,
                     height=fl.S(28)).pack(side="left")
        Label(r, text="竖版适合竖着贴的窄标签", bg=CARD, fg=MUTED,
              font=self.theme.caption).pack(side="left", padx=(fl.S(8), 0))
        self.portrait_var.trace_add("write", lambda *_: self._render_preview())

        r = row("标签长度")
        self.size_preset = ttk.Combobox(r, values=[tr(v) for v in LABEL_PRESETS], state="readonly", width=8)
        self.size_preset.current(1)
        self.size_preset.pack(side="left")
        self.size_preset.bind("<<ComboboxSelected>>", lambda e: self._on_preset_change())
        self.height_mm = tk.Spinbox(r, from_=10, to=200, width=5,
                                    font=self.theme.label, relief="flat", bd=1)
        self.height_mm.delete(0, "end")
        self.height_mm.insert(0, "40")
        self.height_mm.pack(side="left", padx=(8, 4))
        self.height_mm.bind("<KeyRelease>", lambda e: self._on_length_edited())
        self.height_mm.configure(command=self._on_length_edited)
        Label(r, text="mm", bg=CARD, fg=MUTED, font=self.theme.caption).pack(side="left")

        r = row("字号")
        self.font_size = tk.Spinbox(r, from_=8, to=48, width=5, command=self._render_preview,
                                    font=self.theme.label, relief="flat", bd=1)
        self.font_size.delete(0, "end")
        self.font_size.insert(0, "28")
        self.font_size.pack(side="left")
        self.font_size.bind("<KeyRelease>", lambda e: self._render_preview())
        self.max_font_btn = fl.FluentButton(r, tr("最大字号"), self._fit_font_size,
                                           style="standard", theme=self.theme)
        self.max_font_btn.pack(side="left", padx=(fl.S(6), 0))

        self.bold_var = tk.BooleanVar(value=False)
        fl.Toggle(r, self.bold_var, theme=self.theme).pack(side="left", padx=(16, 6))
        Label(r, text="加粗", bg=CARD, fg=TEXT, font=self.theme.body).pack(side="left")
        self.bold_var.trace_add("write", lambda *_: self._render_preview())

        r = row("对齐")
        self.align_var = tk.StringVar(value="左")
        fl.Segmented(r, ["左", "中", "右"], self.align_var, theme=self.theme).pack(side="left")
        self.align_var.trace_add("write", lambda *_: self._render_preview())

        r = row("纸张类型")
        self.label_type = ttk.Combobox(r, values=[tr(v) for v in LABEL_TYPE_NAMES], state="readonly", width=10)
        self.label_type.current(0)
        self.label_type.pack(side="left")

        r = row("份数")
        self.quantity = tk.Spinbox(r, from_=1, to=99, width=5,
                                   font=self.theme.label, relief="flat", bd=1)
        self.quantity.delete(0, "end")
        self.quantity.insert(0, "1")
        self.quantity.pack(side="left")

        r = row("浓度")
        # 用分段档位代替滑条：深色下更清楚，也不用猜数字代表多黑
        self.density_var = tk.StringVar(value="标准")
        self._density_lo, self._density_hi = 1, 3
        self.density_seg = fl.Segmented(r, DENSITY_WORDS_3, self.density_var,
                                        theme=self.theme, height=fl.S(28))
        self.density_seg.pack(side="left")
        self.density_label = Label(r, text="", bg=CARD, fg=MUTED, width=8, anchor="w",
                                      font=self.theme.label)
        self.density_label.pack(side="left", padx=(fl.S(8), 0))
        self.density_var.trace_add("write", lambda *_: self._on_density_change())

        # ---- 高级：打印方向 / 打印协议都是排障开关，平时不该占版面
        self.adv = fl.Collapsible(card.body, "高级 / 校准", theme=self.theme)
        self.adv.pack(fill="x", pady=(fl.S(10), 0))

        r = tk.Frame(self.adv.body, bg=CARD)
        r.pack(fill="x", pady=fl.S(3))
        Label(r, text="打印方向", bg=CARD, fg=MUTED, width=8, anchor="w",
                 font=self.theme.body).pack(side="left")
        self.rotate = ttk.Combobox(r, values=[tr(v) for v in ORIENTATIONS], state="readonly", width=11)
        self.rotate.set(tr("左右翻转"))
        self.rotate.pack(side="left")
        self.rotate.bind("<<ComboboxSelected>>", lambda e: self._on_advanced_change())
        Label(r, text="字反了才需要改", bg=CARD, fg=MUTED,
                 font=self.theme.caption).pack(side="left", padx=(8, 0))

        r = tk.Frame(self.adv.body, bg=CARD)
        r.pack(fill="x", pady=fl.S(3))
        Label(r, text="打印协议", bg=CARD, fg=MUTED, width=8, anchor="w",
                 font=self.theme.body).pack(side="left")
        self.print_task_combo = ttk.Combobox(
            r, values=[tr(v) for v in (TASK_AUTO, TASK_D110, TASK_B1, TASK_D110M_V4)],
            state="readonly", width=10)
        self.print_task_combo.current(0)
        self.print_task_combo.pack(side="left")
        self.print_task_combo.bind("<<ComboboxSelected>>", lambda e: self._on_advanced_change())
        Label(r, text="打空白才需要换", bg=CARD, fg=MUTED,
                 font=self.theme.caption).pack(side="left", padx=(8, 0))

        self._update_adv_hint()

    def _on_density_change(self, _value=None) -> None:
        """档位旁标出实际数值，方便和旧设置、日志对上。"""
        self.density_label.config(text=fmt("浓度 {v}", v=self._density_value()))

    def _density_words(self) -> list[str]:
        return DENSITY_WORDS_5 if (self._density_hi - self._density_lo) >= 4 else DENSITY_WORDS_3

    def _density_value(self) -> int:
        words = self._density_words()
        try:
            return self._density_lo + words.index(self.density_var.get())
        except ValueError:
            return self._density_lo

    def _set_density(self, value: int) -> None:
        value = max(self._density_lo, min(self._density_hi, int(value)))
        self.density_var.set(self._density_words()[value - self._density_lo])

    def _on_advanced_change(self) -> None:
        self._update_adv_hint()
        self._render_preview()

    def _update_adv_hint(self) -> None:
        """收起状态下也能看到当前的高级设置，不必展开。"""
        if hasattr(self, "adv"):
            self.adv.set_hint(f"{tr(self._orientation())} · {self._protocol()}")

    def _build_action_card(self, parent) -> None:
        card = fl.Card(parent, theme=self.theme)
        card.pack(fill="x", pady=(0, 12))

        self.print_btn = fl.FluentButton(card.body, "打  印", self._print,
                                         style="accent", theme=self.theme, height=fl.S(48))
        self.print_btn.pack(fill="x")

        r = tk.Frame(card.body, bg=CARD)
        r.pack(fill="x", pady=(fl.S(10), 0))
        # 诊断页不再用强调色：主操作只留「打印」一个，避免抢注意力
        for label, fn, style in (("诊断页", self._print_diag, "standard"),
                                 ("测试页", self._print_test, "standard"),
                                 ("走纸", self._feed, "standard"),
                                 ("记录", self._show_history, "subtle")):
            fl.FluentButton(r, label, fn, style=style, theme=self.theme,
                            height=fl.S(32)).pack(side="left", fill="x",
                                                  expand=True, padx=(0, fl.S(5)))

        self.progress = fl.FluentProgress(card.body, theme=self.theme)
        self.progress.pack(fill="x", pady=(12, 4))
        self.progress_text = Label(card.body, text="就绪", bg=CARD, fg=MUTED,
                                      font=self.theme.caption)
        self.progress_text.pack(anchor="w")

    def _build_log_card(self, parent) -> None:
        card = fl.Card(parent, title="日志", theme=self.theme)
        card.pack(fill="x", pady=(0, 0))
        self.log = tk.Text(card.body, height=7, relief="flat", bd=0,
                           font=self.theme.label, state="disabled",
                           bg=fl.LOG_BG, fg=fl.LOG_FG, insertbackground=TEXT,
                           padx=8, pady=6, highlightthickness=1,
                           highlightbackground=fl.DIVIDER)
        self.log.pack(fill="x")

    # ------------------------------------------------------------ 预览

    def _get_label_size(self) -> tuple[int, int]:
        try:
            h_mm = float(self.height_mm.get())
        except (ValueError, tk.TclError):
            h_mm = 40.0
        h_mm = max(5.0, min(200.0, h_mm))
        return self._strip_dots, mm_to_px(h_mm)

    def _layout_params(self) -> dict:
        """把界面上的排版参数收成一份快照（预览与批量打印共用）。"""
        strip_dots, length_dots = self._get_label_size()
        try:
            font_size = max(8, int(float(self.font_size.get())))
        except (ValueError, tk.TclError):
            font_size = 28
        try:
            qr_size = int(float(self.qr_size.get()))
        except (ValueError, tk.TclError):
            qr_size = 64
        align = {"左": "left", "中": "center", "右": "right"}.get(self.align_var.get(), "left")
        sym = untr(self.barcode_sym.get())
        symbology = next((c for n, c in SYMBIOLOGIES if n == sym), "code128")
        return dict(
            strip_dots=strip_dots,
            length_dots=length_dots,
            font_size=font_size,
            bold=bool(self.bold_var.get()),
            align=align,
            qr_value=self.qr_var.get(),
            qr_size=qr_size,
            barcode_value=self.barcode_var.get(),
            barcode_symbology=symbology,
            image_path=self._image_path,
            extra_order=self._extra_order(),
        )

    def _extra_order(self) -> str:
        return EXTRA_ORDERS.get(untr(self.extra_order_var.get()), "pic,bc,qr")

    def _is_portrait(self) -> bool:
        return self.portrait_var.get() == "竖版"

    def _layout(self, text: str) -> LayoutResult:
        fn = render_portrait_label if self._is_portrait() else render_landscape_label
        return fn(text=text, **self._layout_params())

    def _fit_font_size(self) -> None:
        """一键把字号设成这张标签能放下的最大值。

        预览会自动缩字号，但用户看不出这张纸的上限。用程序上限 48 渲染一次，
        fit_layout 算出的就是当前内容、当前纸型下的最大可用字号。
        """
        text = self.text_box.get("1.0", "end").rstrip("\n")
        current = self.font_size.get()
        try:
            self._set_spin(self.font_size, 48)
            used = self._layout(text).font_used
        except Exception as e:  # noqa: BLE001
            self._log_error("求最大字号失败：", e)
            used = 0
        if not used:
            self._set_spin(self.font_size, current)
            return
        self._set_spin(self.font_size, used)
        self._render_preview()
        self._log(fmt("已调到这张标签能放下的最大字号：{n}", n=used))

    def _render_preview(self) -> None:
        params = self._layout_params()
        strip_dots, length_dots = params["strip_dots"], params["length_dots"]
        text = self.text_box.get("1.0", "end").rstrip("\n")
        if getattr(self, "batch_var", None) is not None and self.batch_var.get():
            # 批量模式下预览第一张：一行就是一张标签
            lines = self._batch_lines()
            text = lines[0] if lines else ""
        result: LayoutResult = self._layout(text)
        self.preview_image = result.image

        # 因为放不下被自动缩小时，把实际字号写回输入框——
        # 否则「框里写着 40、实际按 28 印」，用户改完也看不出原因
        # 只有横版才回写字号：竖版纸只有 84 点宽，缩字号是常态，
        # 回写会把用户设的值一次次改小
        if (not self._is_portrait() and result.font_used
                and result.font_used < params["font_size"]):
            self._set_spin(self.font_size, result.font_used)

        # 预览直接显示「纸上会是什么样」：与打印走同一套方向换算，
        # 所以看到是正的，打出来就是正的，不用试打
        shown = simulate_output(result.image, self._orientation())

        cw = int(self.preview_canvas.winfo_width() or 720)
        ch = int(self.preview_canvas.winfo_height() or 140)
        if cw < 20 or ch < 20:
            # 窗口还没映射时 winfo_width() 返回 1，此时跳过，等 <Configure> 再画
            return
        iw, ih = result.image.size
        scale = min((cw - 24) / iw, (ch - 24) / ih, 2.5)
        disp_w = max(8, int(iw * scale))
        disp_h = max(8, int(ih * scale))

        disp = shown.resize((disp_w, disp_h), PILImage.Resampling.NEAREST)
        buf = io.BytesIO()
        disp.save(buf, format="PPM")
        self._preview_photo = tk.PhotoImage(data=buf.getvalue())

        self.preview_canvas.delete("all")
        x0 = (cw - disp_w) // 2
        y0 = (ch - disp_h) // 2
        fl.round_rect(self.preview_canvas, x0 - 2, y0 - 2, x0 + disp_w + 2, y0 + disp_h + 2,
                      6, fill="#FFFFFF", outline=fl.PAPER_BORDER)
        self.preview_canvas.create_image(x0, y0, anchor="nw", image=self._preview_photo)

        mm = length_dots / mm_to_px(1)
        capacity = result.capacity or f"12 × {mm:.0f} mm"
        if getattr(self, "batch_var", None) is not None and self.batch_var.get():
            capacity = fmt("预览第 1 张 / 共 {total} 张 · ", total=len(self._batch_lines())) + capacity
        self.size_hint.config(text=capacity)

        if result.warnings:
            self.warn_hint.config(
                text="\n".join(f"⚠ {w}" for w in result.warnings), fg=WARN)
        else:
            self.warn_hint.config(text="✓ 内容在标签范围内", fg=fl.SUCCESS)

    def _on_preset_change(self) -> None:
        name = untr(self.size_preset.get())
        if name.startswith("自定义"):
            self.height_mm.focus_set()
        else:
            self.height_mm.delete(0, "end")
            self.height_mm.insert(0, name.replace(" mm", ""))
        self._render_preview()

    def _on_length_edited(self) -> None:
        """手动改标签长度时，把规格切到「自定义」，避免显示对不上。"""
        try:
            value = float(self.height_mm.get())
        except (ValueError, tk.TclError):
            self.size_preset.set("自定义")
            self._render_preview()
            return
        for label in LABEL_PRESETS[:-1]:
            if abs(float(label.replace(" mm", "")) - value) < 0.01:
                self.size_preset.set(tr(label))
                break
        else:
            self.size_preset.set("自定义")
        self._render_preview()

    def _pick_image(self) -> None:
        p = filedialog.askopenfilename(
            title="选择图片",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp *.gif"), ("所有文件", "*.*")])
        if p:
            self._set_image(p)

    def _set_image(self, path: str) -> None:
        self._image_path = path
        self.image_label.config(text=os.path.basename(path), fg=TEXT)
        self._render_preview()

    def _paste_image(self) -> None:
        """从剪贴板取图（截图后直接按 Ctrl+Shift+V，不用先存文件）。"""
        try:
            from PIL import ImageGrab
            data = ImageGrab.grabclipboard()
        except Exception as e:  # noqa: BLE001
            self.show_banner(f"读取剪贴板失败：{e}")
            return

        if data is None:
            self.show_banner("剪贴板里没有图片。先截图或复制一张图片再试。")
            return
        if isinstance(data, list):
            paths = [p for p in data if os.path.isfile(p)]
            if paths:
                self._set_image(paths[0])
            else:
                self.show_banner("剪贴板里是文件，但没有可用的图片文件。")
            return

        # 拿到的是 PIL 图像：先落到临时文件，复用现有的图片路径逻辑
        try:
            path = os.path.join(tempfile.gettempdir(), "niimbot-paste.png")
            data.convert("L").save(path)
        except Exception as e:  # noqa: BLE001
            self.show_banner(f"剪贴板图片保存失败：{e}")
            return
        self._set_image(path)
        self._log("已从剪贴板取到图片")

    def _clear_image(self) -> None:
        self._image_path = ""
        self.image_label.config(text="未选择", fg=MUTED)
        self._render_preview()

    # ------------------------------------------------------------ 状态胶囊

    def _set_status(self, text: str, ok: bool | None) -> None:
        text = tr(text)
        # 已连接用白点、失败用红点，底分别是深灰和深红 —— Nothing 不用绿色
        color = {True: TEXT, False: ACCENT, None: TEXT_DISABLED}[ok]
        pill = {True: fl.CARD_ALT, False: fl.ACCENT_SUBTLE, None: fl.CARD}[ok]
        c = self.status_pill
        c.delete("all")
        w, h = 190, 30
        fl.round_rect(c, 0, 0, w - 1, h - 1, h / 2, fill=pill, outline=pill)
        c.create_oval(12, h / 2 - 4, 20, h / 2 + 4, fill=color, outline="")
        c.create_text(28, h / 2, anchor="w", text=text, fill=TEXT, font=self.theme.label)

    # ------------------------------------------------------------ 连接

    def _scan_devices(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.scan_btn.set_enabled(False)
        self.scan_btn.set_text("扫描中…")
        self._log("正在扫描设备…")

        def work():
            if self.conn_mode.get() == "蓝牙":
                return self.loop.call(scan_ble_devices(timeout=6.0), timeout=20)
            return list_serial_ports()

        def done(result):
            self._busy = False
            self.scan_btn.set_enabled(True)
            self.scan_btn.set_text("扫描")
            items, hidden = [], 0
            for name, addr in result:
                kind = device_kind(name)
                if kind == "other":
                    hidden += 1          # 明显不是打印机，不往列表里塞
                    continue
                items.append((0 if kind == "printer" else 1, name, addr))
            items.sort(key=lambda it: (it[0], it[1].upper()))

            values = [f"{'★' if rank == 0 else '·'} {name}   [{addr}]"
                      for rank, name, addr in items]
            self.device_combo["values"] = values

            if values:
                keep = self.device_var.get()
                keep_addr = keep.split("[")[-1].rstrip("]").strip() if "[" in keep else ""
                matched = [v for v in values if keep_addr and keep_addr in v]
                if matched:
                    self.device_combo.current(values.index(matched[0]))
                else:
                    # 只有唯一一台像打印机时才自动选中；附近有多台时不替用户做决定
                    likely = [v for v in values if v.startswith("★")]
                    if len(likely) == 1:
                        self.device_combo.current(values.index(likely[0]))
                    else:
                        self.device_combo.set("")

                msg = fmt("找到 {n} 个设备，在列表里选一台再点「连接」", n=len(values))
                if hidden:
                    msg += fmt("（已隐藏 {n} 个不像打印机的）", n=hidden)
                self._log(msg)
            else:
                self._log("没有找到设备。请确认打印机已开机，并关闭手机上的 NIIMBOT App。")

        def fail(err):
            self._busy = False
            self.scan_btn.set_enabled(True)
            self.scan_btn.set_text("扫描")
            self.show_banner(f"扫描失败：{str(err).splitlines()[0]}")
            self._log_error("扫描失败：", err)

        self._run_bg(work, done, fail)

    def _connect(self, auto: bool = False) -> None:
        if self._busy:
            return
        raw = self.device_var.get()
        if not raw:
            messagebox.showinfo(APP_TITLE, "请先扫描并选择一个设备")
            return

        target = raw.split("[")[-1].rstrip("]").strip()
        self._busy = True
        self.connect_btn.set_enabled(False)
        self._log(f"正在连接 {raw.split('[')[0].strip().lstrip('★· ')} …")

        def work():
            transport = BleTransport() if self.conn_mode.get() == "蓝牙" else SerialTransport()
            client = NiimbotClient(transport)
            # 掉线时让界面跟着变，别继续显示「已连接」
            client.on_disconnect = self._handle_conn_lost
            info = self.loop.call(client.connect(target), timeout=40)
            return client, info

        def done(result):
            self._busy = False
            self.connect_btn.set_enabled(True)
            self.client, info = result
            self.disconnect_btn.set_enabled(True)
            parts = [f"型号 ID {info.model_id}"]
            if info.battery is not None:
                parts.append(f"电量 {info.battery}%")
            if info.soft_version:
                parts.append(f"固件 {info.soft_version}")
            parts.append(f"协议 v{info.protocol_version}")
            parts.append(f"流程 {info.print_task}")
            self.status_text.config(text=" · ".join(parts))
            self._set_status("已连接", True)
            self._log("连接成功：" + " · ".join(parts))
            self._apply_printer_defaults(info)

        def fail(err):
            self._busy = False
            self.connect_btn.set_enabled(True)
            self._set_status("连接失败", False)
            first = str(err).splitlines()[0]
            if auto:
                # 自动重连失败只写日志：用户没主动点连接，别拿横幅砸他
                self._log(fmt("自动连接失败：{msg}（可以手动点「连接」重试）", msg=first))
            else:
                self.show_banner(f"连接失败：{first}")
            self._log_error("连接失败：", err)

        self._run_bg(work, done, fail)

    def _disconnect(self) -> None:
        if self._busy:
            messagebox.showinfo(APP_TITLE, "正在打印，请等它结束再断开")
            return
        client = self.client
        self.client = None
        self.disconnect_btn.set_enabled(False)
        self.status_text.config(text="")
        self._set_status("未连接", None)
        if client is not None:
            self._run_bg(
                lambda: self.loop.call(client.disconnect(), timeout=8),
                lambda _: self._log("已断开连接"),
                lambda e: self._log_error("断开时出错：", e))

    def _handle_conn_lost(self) -> None:
        """从 I/O 线程回调：转交 UI 线程处理，绝不直接碰控件。"""
        self._ui_queue.put(("ok", self._after_conn_lost, None))

    def _after_conn_lost(self, _=None) -> None:
        if self.client is None:
            return
        self.client = None
        self._printing = False
        self._busy = False
        self.connect_btn.set_enabled(True)
        self.disconnect_btn.set_enabled(False)
        self.print_btn.set_text("打  印")
        self.progress_text.config(text="连接已断开")
        self._set_status("已断开", False)
        self.status_text.config(text="")
        msg = "打印机连接已断开（蓝牙掉线、被关机或 USB 被拔）。重新连接后可继续打印。"
        self.show_banner(msg)
        self._log(msg)

    def _apply_printer_defaults(self, info) -> None:
        # 打印头宽度以机器报的为准
        self._strip_dots = int(info.printhead_pixels or 96)

        # 纸张规格由 RFID 自动识别，避免手填填错
        paper = info.paper
        if paper is not None and paper.valid and paper.paper_height_mm > 0:
            h = round(paper.paper_height_mm, 1)
            self.height_mm.delete(0, "end")
            self.height_mm.insert(0, f"{h:g}")
            self._on_length_edited()
            self._log(fmt("已按检测到的纸张设置标签长度 {h} mm（纸宽约 {w} mm，间隙 {g} mm）",
                          h=f"{h:g}", w=f"{paper.paper_width_mm:.0f}",
                          g=f"{paper.gap_mm:.1f}"))
        else:
            self._log("未读到 RFID 纸张信息，请手动核对标签长度是否与纸卷一致")

        lo, hi, default = density_range_for(info.model_id)
        cur = self._density_value()
        self._density_lo, self._density_hi = lo, hi
        self.density_seg.set_options(self._density_words())
        # 只在超出新范围时才改，别覆盖用户自己调的浓度
        if not (lo <= cur <= hi):
            self._set_density(default)
            self._log(fmt("浓度范围 {lo}-{hi}（默认 {default}）", lo=lo, hi=hi, default=default))
        else:
            self._set_density(cur)
        self._on_density_change()

        # 只有「自动」时才按型号选流程；用户手动指定过就尊重
        want = info.print_task
        values = list(self.print_task_combo["values"])
        if tr(want) not in values:
            self.print_task_combo["values"] = [tr(TASK_AUTO), tr(want)]
        if self._protocol() in ("", TASK_AUTO):
            self.print_task_combo.set(tr(want))
            self._log(fmt("打印流程自动选择：{task}", task=want))
        else:
            self._log(fmt("打印流程沿用手动设置：{task}（型号建议 {want}）",
                          task=self._protocol(), want=want))

    def _orientation(self) -> str:
        """当前打印方向（内部值永远是中文，界面可能显示英文）。"""
        return untr(self.rotate.get())

    def _protocol(self) -> str:
        """当前打印协议（内部值永远是中文/常量）。"""
        return untr(self.print_task_combo.get())

    def _current_print_task(self) -> str | None:
        choice = self._protocol()
        return None if choice in ("", TASK_AUTO) else choice

    # ------------------------------------------------------------ 打印

    def _require_client(self) -> NiimbotClient | None:
        if self.client is None:
            messagebox.showinfo(APP_TITLE, "请先连接打印机")
            return None
        return self.client


    def _print(self) -> None:
        # 打印中再按一次 = 取消（此时按钮文字是「取消」）
        if self._printing:
            if self.client is not None:
                self.client.cancel()
                self._log("已请求取消，等打印机收尾…")
                self.progress_text.config(text="正在取消…")
            return

        client = self._require_client()
        if client is None or self._busy:
            return

        self._render_preview()
        if self.preview_image is None:
            return

        content = self.text_box.get("1.0", "end").strip()
        if not content and not (self.qr_var.get().strip() or self.barcode_var.get().strip()
                                or self._image_path):
            if not messagebox.askyesno(APP_TITLE, "内容是空的，打出来会是空白标签。\n确定要打吗？"):
                return

        try:
            quantity = max(1, int(float(self.quantity.get())))
        except (ValueError, tk.TclError):
            messagebox.showinfo(APP_TITLE, "份数不合法")
            return

        if quantity > 10:
            if not messagebox.askyesno(
                    APP_TITLE,
                    f"要连续打印 {quantity} 份，每份都得等打印机走完，耗时会长。\n确定继续吗？"):
                return

        idx = self.label_type.current()
        label_type = LABEL_TYPE_VALUES[idx] if 0 <= idx < len(LABEL_TYPE_VALUES) else 1

        image = self.preview_image.copy()
        orientation = self._orientation()

        # 批量模式：一行一张；否则就是预览里这一张
        if self.batch_var.get():
            lines = self._batch_lines()
            if not lines:
                messagebox.showinfo(APP_TITLE, "批量模式下每行打一张，但内容是空的。")
                return
            images = [self._layout(line).image.copy() for line in lines]
        else:
            images = [image]

        history_text = ("\n".join(self._batch_lines()) if self.batch_var.get()
                        else content)

        self._busy = True
        self._printing = True
        self.print_btn.set_text("取  消")
        self.progress.set(0)
        self.progress_text.config(text="正在发送数据…")
        self._log(fmt("开始打印：{labels} 张 × {copies} 份，取向「{orientation}」",
                          labels=len(images), copies=quantity, orientation=orientation))

        def work():
            opts = PrintOptions(quantity=quantity, density=self._density_value(),
                                label_type=label_type)
            total = len(images)
            for idx, img in enumerate(images):
                def report(fraction, idx=idx, total=total):
                    overall = (idx + fraction) / total * 100
                    self._ui_queue.put(("ok", self.progress.set, overall))

                self.loop.call(
                    client.print_image(img, opts, on_progress=report,
                                       landscape=not self._is_portrait(),
                                       task=self._current_print_task(),
                                       orientation=orientation),
                    timeout=300)

        def done(_):
            self._busy = False
            self._printing = False
            self.print_btn.set_text("打  印")
            self.progress.set(100)
            self.progress_text.config(text="打印完成")
            self._log("打印完成 ✔")
            self._append_history(history_text, len(images), quantity)
            try:
                self._save_settings()
            except Exception:  # noqa: BLE001
                pass

        def fail(err):
            self._busy = False
            self._printing = False
            self.print_btn.set_text("打  印")
            if isinstance(err, PrintCancelled):
                # 主动取消不是故障，别刷成红色错误
                self.progress.set(0)
                self.progress_text.config(text="已取消")
                self._log("打印已取消（可能已吐出一张不完整的标签）")
                return
            self.progress_text.config(text="打印失败")
            self.show_banner(f"打印失败：{str(err).splitlines()[0]}")
            self._log_error("打印失败：", err)

        self._run_bg(work, done, fail)

    def _print_test(self) -> None:
        client = self._require_client()
        if client is None or self._busy:
            return

        strip_dots, length_dots = self._get_label_size()
        image = render_test_label(strip_dots, min(length_dots, mm_to_px(40)))

        self._busy = True
        self._log(f"打印测试页…（取向「{self._orientation()}」）")

        def work():
            self.loop.call(
                client.print_image(image, PrintOptions(quantity=1), landscape=True,
                                   task=self._current_print_task(),
                                   orientation=self._orientation()),
                timeout=180)

        def done(_):
            self._busy = False
            self._log("测试页已输出。三角形指向标签起点（先出纸的一端）。")

        def fail(err):
            self._busy = False
            self.show_banner(f"测试页失败：{str(err).splitlines()[0]}")
            self._log_error("测试页失败：", err)

        self._run_bg(work, done, fail)

    def _print_diag(self) -> None:
        """打印诊断页：用来看打印头到底有没有加热。"""
        client = self._require_client()
        if client is None or self._busy:
            return

        strip_dots, length_dots = self._get_label_size()
        image = render_diagnostic_label(strip_dots, length_dots)

        self._busy = True
        self._log(f"打印诊断页…（取向「{self._orientation()}」）")
        self._log("看结果：①整块全黑那块若是白的 → 纸装反了或纸型不对；"
                  "②条纹正常但没字 → 告诉我；③全都不出 → 打印头或纸的问题。")

        def work():
            self.loop.call(
                client.print_image(image, PrintOptions(quantity=1), landscape=True,
                                   task=self._current_print_task(),
                                   orientation=self._orientation()),
                timeout=180)

        def done(_):
            self._busy = False
            self._log("诊断页已输出，请对照上面的说明看。")

        def fail(err):
            self._busy = False
            self.show_banner(f"诊断页失败：{str(err).splitlines()[0]}")
            self._log_error("诊断页失败：", err)

        self._run_bg(work, done, fail)

    def _feed(self) -> None:
        client = self._require_client()
        if client is None or self._busy:
            return
        self._busy = True
        self._log("走纸中…")

        def work():
            self.loop.call(client.feed_labels(1), timeout=60)

        def done(_):
            self._busy = False
            self._log("走纸完成")

        def fail(err):
            self._busy = False
            self.show_banner(f"走纸失败：{str(err).splitlines()[0]}")
            self._log_error("走纸失败：", err)

        self._run_bg(work, done, fail)

    # ------------------------------------------------------------ 模板

    def _refresh_templates(self) -> None:
        names = sorted(self._templates)
        self.template_box["values"] = names
        if self.template_var.get() not in names:
            self.template_var.set("")

    @staticmethod
    def _fill_placeholders(text: str) -> str:
        """把模板里的 {date} / {time} / {year} 换成当前日期。"""
        now = datetime.datetime.now()
        return (text.replace("{date}", now.strftime("%Y-%m-%d"))
                    .replace("{time}", now.strftime("%H:%M"))
                    .replace("{year}", now.strftime("%Y")))

    def _apply_template(self) -> None:
        name = self.template_var.get()
        text = self._templates.get(name)
        if text is None:
            return
        self.text_box.delete("1.0", "end")
        self.text_box.insert("1.0", self._fill_placeholders(text))
        self._render_preview()
        self._log(fmt("已套用模板「{name}」", name=name))

    def _save_template(self) -> None:
        text = self.text_box.get("1.0", "end").rstrip("\n")
        if not text.strip():
            messagebox.showinfo(APP_TITLE, "内容是空的，先输入内容再存模板")
            return
        name = simpledialog.askstring("存为模板", "给这套内容起个名字（例如「CPU 长型号」）：",
                                      parent=self.root)
        if not name or not name.strip():
            return
        name = name.strip()
        if name in self._templates and not messagebox.askyesno(
                APP_TITLE, f"模板「{name}」已存在，覆盖吗？"):
            return
        self._templates[name] = text
        self._refresh_templates()
        self.template_var.set(name)
        self._save_settings()
        self._log(fmt("已保存模板「{name}」", name=name))

    def _delete_template(self) -> None:
        name = self.template_var.get()
        if not name or name not in self._templates:
            messagebox.showinfo(APP_TITLE, "先在「模板」下拉框里选一个")
            return
        if not messagebox.askyesno(APP_TITLE, f"删除模板「{name}」？"):
            return
        self._templates.pop(name, None)
        if name in BUILTIN_TEMPLATES:
            self._removed_templates.add(name)
        self._refresh_templates()
        self._save_settings()
        self._log(fmt("已删除模板「{name}」", name=name))

    # ------------------------------------------------------------ 批量 / 记录

    def _batch_lines(self) -> list[str]:
        """批量模式下每行一张，空行忽略。"""
        return [ln.strip() for ln in self.text_box.get("1.0", "end").splitlines() if ln.strip()]

    def _on_batch_toggle(self) -> None:
        if self.batch_var.get():
            n = len(self._batch_lines())
            self._log(fmt("批量模式：{n} 行 → 会打 {n} 张（一行一张）", n=n) if n
                      else "批量模式：一行一个标签，先输入内容")
        self._render_preview()

    def _append_history(self, text: str, labels: int, quantity: int) -> None:
        entry = {
            "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "text": text[:300],
            "labels": labels,
            "quantity": quantity,
        }
        try:
            with open(data_path(HISTORY_FILE), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _show_history(self) -> None:
        """最近打过什么，方便找回上一次的内容。"""
        rows: list[dict] = []
        try:
            with open(data_path(HISTORY_FILE), encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            rows.append(json.loads(line))
                        except ValueError:
                            continue
        except OSError:
            pass

        win = tk.Toplevel(self.root)
        win.title("打印记录")
        win.configure(bg=BG)
        win.geometry(f"{fl.S(560)}x{fl.S(420)}")
        win.transient(self.root)

        body = tk.Frame(win, bg=CARD)
        body.pack(fill="both", expand=True, padx=fl.S(12), pady=fl.S(12))
        txt = tk.Text(body, font=self.theme.body, relief="flat", bd=0,
                      bg=CARD, fg=TEXT, padx=fl.S(10), pady=fl.S(8), wrap="word")
        txt.pack(fill="both", expand=True)

        if not rows:
            txt.insert("1.0", "还没有打印记录。\n每次打印成功后记一条，方便回看和找回内容。")
        else:
            for r in reversed(rows[-40:]):
                txt.insert("end", f"{r.get('time', '')}   {r.get('labels', 1)} 张 × "
                                  f"{r.get('quantity', 1)} 份\n")
                txt.insert("end", f"    {str(r.get('text', '')).replace(chr(10), ' / ')}\n\n")
        txt.config(state="disabled")

        bar = tk.Frame(win, bg=BG)
        bar.pack(fill="x", padx=fl.S(12), pady=(0, fl.S(12)))
        fl.FluentButton(bar, "关闭", win.destroy, style="standard",
                        theme=self.theme, height=fl.S(30)).pack(side="right")

    # ------------------------------------------------------------ 后台任务

    def _run_bg(self, work, on_done, on_fail) -> None:
        def runner():
            try:
                result = work()
                self._ui_queue.put(("ok", on_done, result))
            except Exception as e:  # noqa: BLE001
                self._ui_queue.put(("err", on_fail, e))

        threading.Thread(target=runner, daemon=True).start()

    def _poll_ui_queue(self) -> None:
        # 单个回调出错不能让整个队列停摆，否则按钮会永远卡在「打印中…」
        while True:
            try:
                _kind, fn, payload = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn(payload)
            except Exception as e:  # noqa: BLE001
                print(f"[ui] 回调出错：{e}", flush=True)
        self.root.after(60, self._poll_ui_queue)

    # ------------------------------------------------------------ 日志

    def _log(self, text: str) -> None:
        text = tr(text)  # 前缀式错误消息（"连接失败：xxx"）靠这层统一翻译
        self.log.config(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def _log_error(self, prefix: str, err) -> None:
        text = str(err).strip() or type(err).__name__
        self._log(f"{prefix}{text.splitlines()[0]}")
        for line in text.splitlines()[1:]:
            self._log("    " + line.strip())

    # ------------------------------------------------------------ 设置持久化

    def _restore_settings(self) -> None:
        """启动时恢复上次的文字、尺寸、取向等，省得每次重设。"""
        s = settings.load()
        self._restored = s

        self.text_box.delete("1.0", "end")
        self.text_box.insert("1.0", str(s["text"]))

        self._set_spin(self.height_mm, s["label_length_mm"])
        self._on_length_edited()  # 让规格下拉跟着长度走，别停在旧的预设名
        self._set_spin(self.font_size, s["font_size"])
        self._set_spin(self.qr_size, s["qr_size"])
        self._set_spin(self.quantity, s["quantity"])

        self.bold_var.set(bool(s["bold"]))
        if untr(s["align"]) in ("左", "中", "右"):
            self.align_var.set(untr(s["align"]))
        if untr(s["orientation"]) in ORIENTATIONS:
            self.rotate.set(tr(untr(s["orientation"])))

        try:
            self._set_density(int(s["density"]))
        except (TypeError, ValueError):
            pass

        if untr(s["label_type"]) in LABEL_TYPE_NAMES:
            self.label_type.set(tr(untr(s["label_type"])))
        if untr(s["print_task"]) in [TASK_AUTO, TASK_D110, TASK_B1, TASK_D110M_V4]:
            self.print_task_combo.set(tr(untr(s["print_task"])))

        self.qr_var.set(str(s.get("qr_value", "")))
        self.barcode_var.set(str(s.get("barcode_value", "")))
        if untr(s.get("barcode_symbology")) in [n for n, _ in SYMBIOLOGIES]:
            self.barcode_sym.set(tr(untr(s.get("barcode_symbology"))))

        path = str(s.get("image_path", "")).strip()
        if path and os.path.exists(path):
            self._image_path = path
            self.image_label.config(text=os.path.basename(path), fg=TEXT)

        self.portrait_var.set("竖版" if bool(s.get("portrait")) else "横版")
        self.batch_var.set(bool(s.get("batch", False)))
        if s.get("extra_order") in EXTRA_ORDERS:
            self.extra_order_var.set(tr(s["extra_order"]))

        tpl = s.get("templates")
        if isinstance(tpl, dict):
            self._templates = {str(k): str(v) for k, v in tpl.items()}

        # 首次运行补上内置模板：用 setdefault，不覆盖用户改过的同名模板。
        # 打过标记之后就不再补，删掉的不会自己回来。
        self._removed_templates = set(s.get("templates_removed") or [])
        seeded = int(s.get("templates_seeded") or 0)
        if seeded < TEMPLATE_SEED_VERSION:
            for name, body in BUILTIN_TEMPLATES.items():
                if name in self._removed_templates:
                    continue      # 用户主动删过的，不再补回来
                current = self._templates.get(name)
                # 没有就补上；内容还是旧版内置原文（说明用户没改过）就换成新的；
                # 用户自己改过的一律保留
                if current is None or _is_legacy_template(current):
                    self._templates[name] = body
            self._templates_seeded = TEMPLATE_SEED_VERSION
            # 立刻落盘：不然用户删掉内置模板后，下次启动又被补回来
            try:
                self._save_settings()
            except Exception:  # noqa: BLE001
                pass
        self._refresh_templates()

        if s.get("conn_mode") in ("蓝牙", "USB 串口"):
            self.conn_mode.set(s["conn_mode"])
        dev = str(s.get("device", "")).strip()
        if dev:
            self.device_combo["values"] = [dev]
            self.device_var.set(dev)


        geo = str(s.get("window_geometry", "")).strip()
        if geo and self._geometry_on_screen(geo):
            try:
                self.root.geometry(geo)
            except tk.TclError:
                pass
        elif geo:
            self._log("上次的窗口位置已不在当前屏幕上（可能拔掉了外接显示器），"
                      "已重新居中")

        # 上次用过设备就自动试连一次，省掉「扫描 → 选设备 → 连接」三步
        if dev:
            self.root.after(900, self._auto_reconnect)

        self._update_adv_hint()

    def _auto_reconnect(self) -> None:
        """启动后自动连上次的设备；失败只写日志，不弹横幅打扰。"""
        if self.client is not None or self._busy or not self.device_var.get():
            return
        self._log("正在自动连接上次用的打印机…")
        self._connect(auto=True)

        self._log("已恢复上次的设置与文字内容")
        apply_titlebar(self.root, fl.get_theme() == "dark")

    def _geometry_on_screen(self, geo: str) -> bool:
        """判断上次保存的窗口位置是否还落在当前屏幕上。

        拔掉外接显示器、改了分辨率之后，旧坐标可能落在屏幕外，
        表现就是「双击了但什么都没出现」。这里判为不可用，改回居中。
        """
        m = re.match(r"^(\d+)x(\d+)([+-]\d+)([+-]\d+)$", geo.strip())
        if not m:
            return False
        w, h = int(m.group(1)), int(m.group(2))
        # 太小的尺寸肯定是坏的：程序异常退出时窗口还没完成布局，
        # winfo_width() 会返回 1，这个 1x1 被存进设置后，下次启动就会缩成一条线
        if w < fl.S(640) or h < fl.S(420):
            return False
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        if w > sw or h > sh:
            return False

        def axis(text: str, size: int, screen: int) -> int:
            raw = int(text)
            return raw if raw >= 0 else screen - size + raw

        x = axis(m.group(3), w, sw)
        y = axis(m.group(4), h, sh)
        # 至少要有 160×100 的可点区域留在屏幕里
        return (x + w > 160) and (y + h > 100) and (x < sw - 160) and (y < sh - 100)

    def _save_settings(self) -> None:
        s = {
            "text": self.text_box.get("1.0", "end").rstrip("\n"),
            "label_length_mm": self._spin_float(self.height_mm, 40.0),
            "font_size": self._spin_int(self.font_size, 28),
            "bold": bool(self.bold_var.get()),
            "align": self.align_var.get(),
            "orientation": self._orientation(),
            "portrait": self._is_portrait(),
            "density": self._density_value(),
            "quantity": self.quantity.get(),
            "label_type": untr(self.label_type.get()),
            "print_task": self._protocol(),
            "qr_value": self.qr_var.get(),
            "qr_size": self.qr_size.get(),
            "barcode_value": self.barcode_var.get(),
            "barcode_symbology": untr(self.barcode_sym.get()),
            "image_path": self._image_path,
            "batch": bool(self.batch_var.get()),
            "extra_order": untr(self.extra_order_var.get()),
            "templates": dict(self._templates),
            "window_geometry": self.root.geometry(),
            "conn_mode": self.conn_mode.get(),
            "device": self.device_var.get(),
            # 这三项是全局状态，不属于界面快照，必须写在这里才真正落盘
            "lang": get_lang(),
            "theme": fl.get_theme(),
            "templates_seeded": self._templates_seeded,
            "templates_removed": sorted(self._removed_templates),
        }
        settings.save(s)

    @staticmethod
    def _set_spin(widget, value) -> None:
        try:
            widget.delete(0, "end")
            widget.insert(0, str(value))
        except tk.TclError:
            pass

    @staticmethod
    def _spin_float(widget, default: float) -> float:
        try:
            return float(widget.get())
        except (ValueError, tk.TclError):
            return default

    @staticmethod
    def _spin_int(widget, default: int) -> int:
        try:
            return int(float(widget.get()))
        except (ValueError, tk.TclError):
            return default

    def _on_close(self) -> None:
        try:
            self._save_settings()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.client is not None:
                self.loop.call(self.client.disconnect(), timeout=3)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.loop.stop()
        except Exception:  # noqa: BLE001
            pass
        self.root.destroy()


def _enable_dpi_awareness() -> float:
    """开启 DPI 感知，返回缩放系数（96 dpi = 1.0）。

    不开的话 Windows 会把整个窗口当位图拉伸，150% 缩放下字和圆角都是糊的。
    """
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # system DPI aware
    except Exception:  # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)
        if dpi:
            return dpi / 96.0
    except Exception:  # noqa: BLE001
        pass
    return 1.0


def main() -> None:
    fl.set_scale(_enable_dpi_awareness())
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
