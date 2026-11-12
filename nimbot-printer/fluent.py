"""Nothing OS 风格控件库，基于 tkinter 实现。

视觉取自 Nothing 的设计语言（nothing.tech / Nothing OS）：
- 只有黑白灰，加一个信号红 #D71921
- 纯黑画布 #000000 + 深灰次表面 #1A1A1A
- 点阵 / 等宽字体做标签，全部大写
- 1px 发丝线，无渐变、无阴影、无高光
- 圆角几乎为零，硬边几何
- 卡片标题前一个红色指示点（Glyph 灯效的呼应）
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from i18n import tr

# ---------------------------------------------------------------- 设计令牌
#
# 取值对齐 Nothing 品牌规范：纯黑白两色画布，灰阶承担层次，
# 信号红只用于「可交互 / 需注意」的地方。
#
# 两套调色板（深色 / 浅色）在运行时用 :func:`set_theme` 整体切换。
# 注意：这些是**模块级变量**，`from fluent import BG` 会拿到当时的快照，
# 所以 gui 那边必须调 refresh_palette() 重新同步（见 gui.py）。

_DARK = {
    "ACCENT": "#D71921",          # N-Red，唯一的彩色
    "ACCENT_HOVER": "#F0252E",
    "ACCENT_PRESSED": "#A81219",
    "ACCENT_SUBTLE": "#2A0E10",   # 深红底（错误条、选中底）
    "ACCENT_BORDER": "#4A1418",

    "BG": "#000000",              # 纯黑画布
    "CARD": "#0D0D0D",            # 卡片面
    "CARD_ALT": "#1A1A1A",        # 次级面（输入框、日志、预览底）
    "CARD_BORDER": "#2A2A2A",
    "DIVIDER": "#1F1F1F",

    "TEXT": "#FFFFFF",
    "TEXT_SECONDARY": "#8A8A8A",  # gray-500
    "TEXT_DISABLED": "#4A4A4A",
    "TEXT_ON_ACCENT": "#FFFFFF",

    "CONTROL_BG": "#121212",
    "CONTROL_HOVER": "#1F1F1F",
    "CONTROL_PRESSED": "#2A2A2A",
    "CONTROL_BORDER": "#333333",  # gray-700
    "CONTROL_BORDER_HOVER": "#5C5C5C",

    "DANGER": "#D71921",
    "DANGER_SUBTLE": "#2A0E10",
    "SUCCESS": "#FFFFFF",         # Nothing 不用绿色：成功态用白点表示

    "PREVIEW_BG": "#1A1A1A",      # 预览画布底
    "LOG_BG": "#0A0A0A",
    "LOG_FG": "#9E9E9E",
    "PAPER_BORDER": "#3A3A3A",    # 预览里那张白纸的描边
}

_LIGHT = {
    "ACCENT": "#D71921",
    "ACCENT_HOVER": "#B5141B",
    "ACCENT_PRESSED": "#8F0F15",
    "ACCENT_SUBTLE": "#FCEDED",
    "ACCENT_BORDER": "#F0C4C6",

    "BG": "#F2F2F2",              # 浅灰画布，让白卡片浮起来
    "CARD": "#FFFFFF",
    "CARD_ALT": "#FAFAFA",
    "CARD_BORDER": "#DADADA",
    "DIVIDER": "#E8E8E8",

    "TEXT": "#000000",
    "TEXT_SECONDARY": "#6B6B6B",
    "TEXT_DISABLED": "#A0A0A0",
    "TEXT_ON_ACCENT": "#FFFFFF",

    "CONTROL_BG": "#FFFFFF",
    "CONTROL_HOVER": "#ECECEC",
    "CONTROL_PRESSED": "#E0E0E0",
    "CONTROL_BORDER": "#CCCCCC",
    "CONTROL_BORDER_HOVER": "#999999",

    "DANGER": "#D71921",
    "DANGER_SUBTLE": "#FCEDED",
    "SUCCESS": "#000000",

    "PREVIEW_BG": "#EFEFEF",
    "LOG_BG": "#F7F7F7",
    "LOG_FG": "#3C3C3C",
    "PAPER_BORDER": "#C8C8C8",
}

THEME_KEYS = tuple(_DARK)
_theme = "dark"

# 初始（深色）—— 让模块级名字在导入时就是可用的值
globals().update(_DARK)
CONTROL_BORDER_FOCUS = ACCENT

RADIUS_CARD = 2             # 硬边，只留 2px 去毛刺
RADIUS_CONTROL = 2
RADIUS_PILL = 2

PAD = 14
GAP = 10


def set_theme(name: str) -> str:
    """切换调色板（dark / light），返回实际生效的名字。"""
    global _theme
    _theme = "light" if str(name).lower().startswith("light") else "dark"
    globals().update(_LIGHT if _theme == "light" else _DARK)
    globals()["CONTROL_BORDER_FOCUS"] = globals()["ACCENT"]
    return _theme


def get_theme() -> str:
    return _theme


# ---------------------------------------------------------------- DPI 缩放
#
# 程序启动时按显示器 DPI 调用 :func:`set_scale`。所有写死的像素尺寸都过
# :func:`S`，字号用「负像素」给 tkinter（负数=像素、正数=点），这样在
# 150% 缩放下界面整体放大但依然锐利，不会像位图拉伸那样发虚。

_SCALE = 1.0


def set_scale(value: float) -> float:
    """设定全局缩放（1.0 = 96 dpi）。返回实际生效值。"""
    global _SCALE
    try:
        _SCALE = max(0.75, min(3.0, float(value)))
    except (TypeError, ValueError):
        _SCALE = 1.0
    return _SCALE


def get_scale() -> float:
    return _SCALE


def S(px: float) -> int:
    """把设计稿像素换算成当前 DPI 下的像素。"""
    return max(1, int(round(float(px) * _SCALE)))


# ---------------------------------------------------------------- 变量 trace 安全
#
# tkinter 的 trace 挂在**变量**上，不挂在控件上。控件被 destroy() 之后 trace
# 依然存在，于是出现两种崩法：
#   1) 旧变量被 GC 时 tkinter 会 globalunsetvar，Tcl 的 unset 同样触发 write
#      trace —— 回调扑向已销毁的控件，winfo_width() 报
#      TclError: bad window path name "....!segmented"
#   2) 界面重建（切语言 / 切主题）后旧变量的 trace 又把旧控件拉起来重画。
# 所以：控件销毁时主动摘掉 trace，重画前再兜一次存活检查。


def alive(widget: tk.Misc | None) -> bool:
    """控件是否还活着（未被 destroy）。已销毁时 tkinter 会抛 TclError。"""
    if widget is None:
        return False
    try:
        return bool(widget.winfo_exists())
    except tk.TclError:
        return False
    except Exception:  # noqa: BLE001
        return False


class _VarWatcher:
    """给控件加上「跟随变量变化重画」的能力，并在销毁时自动解绑。"""

    _trace_id: str | None = None
    _watched_var: tk.Variable | None = None

    def _watch_var(self, variable: tk.Variable) -> None:
        self._watched_var = variable
        self._trace_id = variable.trace_add("write", lambda *_: self._redraw())
        self.bind("<Destroy>", self._release_var, add="+")

    def _release_var(self, event=None) -> None:
        # 子控件的 <Destroy> 会冒泡上来，只处理自己的
        if event is not None and getattr(event, "widget", None) is not self:
            return
        ident, variable = self._trace_id, self._watched_var
        self._trace_id, self._watched_var = None, None
        if ident and variable is not None:
            try:
                variable.trace_remove("write", ident)
            except Exception:  # noqa: BLE001  变量可能已随解释器一起没了
                pass


def _font_family(root: tk.Misc, candidates: list[str]) -> str:
    """按顺序挑第一个装了的字体；最后一项作为兜底（通常是系统自带字体）。"""
    try:
        available = {f.lower() for f in tkfont.families(root)}
    except Exception:  # noqa: BLE001
        return candidates[-1]
    for name in candidates[:-1]:
        if name.lower() in available:
            return name
    return candidates[-1]


class Theme:
    """按窗口解析字体，供控件使用。

    Nothing 的三套字体分工（见 assets/fonts/NOTICE.md）：
    - Ndot 55：点阵，只给 logo 式大字（顶栏）
    - NType 82 Mono：等宽，技术标签与数字
    - 雅黑：所有中文（官方字体只有拉丁字形，中文会回退到衬线宋体，不好看）
    """

    def __init__(self, root: tk.Misc) -> None:
        dot = _font_family(root, ["Ndot 55", "Ndot", "Consolas", "Courier New"])
        mono = _font_family(root, ["NType 82 Mono", "Consolas", "Courier New"])
        sans = _font_family(root, ["Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"])
        self.mono = mono
        self.family = sans
        self.display = dot

        # 负值 = 像素字号，不受 tk 的 point→pixel 换算影响；统一乘 DPI 缩放
        self.caption = (sans, -S(12))
        self.label = (mono, -S(12))
        self.body = (sans, -S(13))
        self.body_strong = (sans, -S(13), "bold")
        self.subtitle = (sans, -S(15), "bold")
        # 点阵字不加粗：合成加粗会把点糊成块；字号给大一点，点才分得开
        self.title = (dot, -S(32))
        self.button = (sans, -S(12), "bold")
        self.large_button = (sans, -S(16), "bold")


# ---------------------------------------------------------------- 绘图


def round_rect(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float,
               radius: float = 8, **kwargs) -> int:
    """在 Canvas 上画圆角矩形。

    用两块矩形填中间、四个扇形填圆角，再用直线加圆弧描边——
    这样圆角是真圆弧，不受光滑多边形插值影响。
    """
    r = max(0.0, min(float(radius), (x2 - x1) / 2, (y2 - y1) / 2))
    fill = kwargs.get("fill", "")
    outline = kwargs.get("outline", "")
    width = int(kwargs.get("width", 1) or 1)
    tags = kwargs.get("tags", "")

    if r <= 0.5:
        return canvas.create_rectangle(x1, y1, x2, y2, **kwargs)

    def _arc(cx: float, cy: float, start: int, style: str, **extra) -> int:
        return canvas.create_arc(cx - r, cy - r, cx + r, cy + r,
                                 start=start, extent=90, style=style, **extra)

    # 填充
    if fill:
        canvas.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline="", tags=tags)
        canvas.create_rectangle(x1, y1 + r, x2, y2 - r, fill=fill, outline="", tags=tags)
        for cx, cy, start in ((x1 + r, y1 + r, 90), (x2 - r, y1 + r, 0),
                              (x2 - r, y2 - r, 270), (x1 + r, y2 - r, 180)):
            _arc(cx, cy, start, tk.PIESLICE, fill=fill, outline="", tags=tags)

    # 描边
    if outline:
        canvas.create_line(x1 + r, y1, x2 - r, y1, fill=outline, width=width, tags=tags)
        canvas.create_line(x1 + r, y2, x2 - r, y2, fill=outline, width=width, tags=tags)
        canvas.create_line(x1, y1 + r, x1, y2 - r, fill=outline, width=width, tags=tags)
        canvas.create_line(x2, y1 + r, x2, y2 - r, fill=outline, width=width, tags=tags)
        for cx, cy, start in ((x1 + r, y1 + r, 90), (x2 - r, y1 + r, 0),
                              (x2 - r, y2 - r, 270), (x1 + r, y2 - r, 180)):
            _arc(cx, cy, start, tk.ARC, outline=outline, width=width, tags=tags)

    return 0


# ---------------------------------------------------------------- 滚动容器


class ScrollFrame(tk.Frame):
    """垂直滚动容器。

    窗口高度不够时（比如 1366×768 的笔记本、或把系统缩放调到 150%），
    放进去的内容可以整页上下滚，不会被裁掉。

    用法：把原来往 ``parent`` 里 pack 的控件改成往 ``.body`` 里 pack，
    内容建完后调用一次 :meth:`bind_wheel_tree` 让滚轮生效。

    文字框 / 列表这类自己会滚的控件不绑滚轮，否则会把页面和文本一起滚走。
    """

    def __init__(self, parent: tk.Misc, bg: str | None = None, **kwargs) -> None:
        # 不能写成 bg: str = BG —— 默认参数在函数定义时就求值，
        # 切到浅色后这里还是深色
        bg = bg or BG
        super().__init__(parent, bg=bg, **kwargs)
        self._bg = bg
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0,
                                yscrollincrement=20)
        self.body = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window(0, 0, anchor="nw", window=self.body)

        self._vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._sync_bar)
        self.canvas.pack(side="left", fill="both", expand=True)

        self.body.bind("<Configure>", self._on_body)
        self.canvas.bind("<Configure>", self._on_canvas)

    # ---------------------------------------------------------- 布局同步

    def _on_body(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event) -> None:
        # 内容宽度始终跟视口一致，避免出现横向滚动
        self.canvas.itemconfigure(self._win, width=event.width)
        self._on_body()

    def _sync_bar(self, lo, hi) -> None:
        """内容装得下就隐藏滚动条，装不下才出现（Fluent 风格：平时看不见）。"""
        need = not (float(lo) <= 0.0 and float(hi) >= 1.0)
        if need and not self._vbar.winfo_ismapped():
            self._vbar.pack(side="right", fill="y")
        elif not need and self._vbar.winfo_ismapped():
            self._vbar.pack_forget()
        self._vbar.set(lo, hi)

    # ---------------------------------------------------------- 滚轮

    def bind_wheel_tree(self, widget: tk.Misc | None = None) -> None:
        """给内容区所有子控件绑滚轮（递归）。建完界面后调用一次。"""
        node = widget or self.body
        for child in node.winfo_children():
            if not isinstance(child, (tk.Text, tk.Listbox, ttk.Scrollbar)):
                child.bind("<MouseWheel>", self._on_wheel, add="+")
                child.bind("<Button-4>", self._on_wheel, add="+")   # X11 上滚
                child.bind("<Button-5>", self._on_wheel, add="+")
            self.bind_wheel_tree(child)

    def _on_wheel(self, event) -> str:
        if self.canvas.yview() == (0.0, 1.0):
            return ""  # 内容没超出，别拦截滚轮
        if getattr(event, "num", None) == 4:
            step = -1
        elif getattr(event, "num", None) == 5:
            step = 1
        else:
            step = -1 if getattr(event, "delta", 0) > 0 else 1
        self.canvas.yview_scroll(step * 3, "units")
        return "break"

    def scroll_to_top(self) -> None:
        self.canvas.yview_moveto(0.0)


# ---------------------------------------------------------------- 卡片


class Card(tk.Frame):
    """白色圆角卡片：宽度跟随父容器，高度跟随内容。"""

    def __init__(self, parent: tk.Misc, title: str | None = None,
                 theme: Theme | None = None, **kwargs) -> None:
        super().__init__(parent, bg=BG, **kwargs)
        self.theme = theme or Theme(parent)
        self._title = tr(title) if title else title
        self._title_h = S(34) if title else 0

        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self.body = tk.Frame(self.canvas, bg=CARD)
        self._win = self.canvas.create_window(S(PAD), S(PAD) + self._title_h,
                                              anchor="nw", window=self.body)

        self.canvas.bind("<Configure>", self._on_canvas, add="+")
        self.body.bind("<Configure>", self._on_body, add="+")
        self.after(80, self._sync)

    def _on_canvas(self, _event) -> None:
        self.canvas.itemconfigure(self._win, width=max(0, self.canvas.winfo_width() - S(PAD) * 2))
        self.canvas.coords(self._win, S(PAD), S(PAD) + self._title_h)
        self._draw()

    def _on_body(self, event) -> None:
        want = event.height + S(PAD) * 2 + self._title_h
        try:
            cur = int(float(self.canvas.cget("height") or 0))
        except ValueError:
            cur = 0
        if cur != want:
            self.canvas.configure(height=want)
        self._draw()

    def _sync(self) -> None:
        self._on_canvas(None)
        self._on_body(type("E", (), {"height": self.body.winfo_reqheight()})())

    def _draw(self) -> None:
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        self.canvas.delete("bg")
        if w <= 2 or h <= 2:
            return
        round_rect(self.canvas, 0.5, 0.5, w - 1, h - 1, RADIUS_CARD,
                   fill=CARD, outline=CARD_BORDER, width=1, tags="bg")
        if self._title:
            # 标题前的红色指示点：Nothing 最常见的识别元素
            r = S(3)
            cx, cy = S(PAD) + r, S(PAD) + S(11)
            self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                                    fill=ACCENT, outline="", tags="bg")
            self.canvas.create_text(cx + r + S(9), cy, anchor="w", text=self._title,
                                    fill=TEXT, font=self.theme.subtitle, tags="bg")
        self.canvas.tag_lower("bg")

    def add(self, child: tk.Widget) -> None:
        """往卡片正文里加控件（用 pack）。"""
        child.pack(in_=self.body)


# ---------------------------------------------------------------- 按钮


class FluentButton(tk.Canvas):
    """圆角按钮：accent（强调色实心）/ standard（白底描边）/ subtle（无边框）。"""

    def __init__(self, parent: tk.Misc, text: str = "", command=None,
                 style: str = "standard", theme: Theme | None = None,
                 height: int | None = None, width: int = 0, **kwargs) -> None:
        self.theme = theme or Theme(parent)
        height = height or S(32)
        font = self.theme.large_button if height >= S(42) else self.theme.button
        self._font = font
        self._text = tr(text).upper()  # Nothing 的按钮标签一律大写
        self._command = command
        self._style = style
        self._height = height
        self._fixed_width = width
        self._enabled = True
        self._state = "normal"
        self._focused = False

        # 按「实际要显示的文本」量宽度：中文 4 个字译成英文可能有 10 个字符，
        # 用原文量会算窄，pack 之后文字就被裁掉
        est = max(width, int(tkfont.Font(root=parent, font=font).measure(self._text)) + S(34))
        super().__init__(parent, width=est, height=height, bg=BG,
                         highlightthickness=0, bd=0, takefocus=1, **kwargs)

        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Enter>", lambda e: self._set_state("hover"))
        self.bind("<Leave>", lambda e: self._set_state("normal"))
        self.bind("<ButtonPress-1>", lambda e: self._set_state("pressed"))
        self.bind("<ButtonRelease-1>", self._release)
        # 键盘可达：Tab 能聚焦，回车/空格等同点击
        self.bind("<FocusIn>", lambda e: self._focus(True))
        self.bind("<FocusOut>", lambda e: self._focus(False))
        self.bind("<Return>", self._keyboard_activate)
        self.bind("<KP_Enter>", self._keyboard_activate)
        self.bind("<space>", self._keyboard_activate)
        self.after(20, self._redraw)

    def _focus(self, on: bool) -> None:
        self._focused = on
        self._redraw()

    def _keyboard_activate(self, _event=None) -> str:
        if self._enabled and self._command:
            self._command()
        return "break"

    def _set_state(self, state: str) -> None:
        if not self._enabled:
            return
        self._state = state
        self._redraw()

    def _release(self, _event=None) -> None:
        if self._enabled and self._state == "pressed":
            self._state = "hover"
            self._redraw()
            if self._command:
                self._command()
        else:
            self._set_state("normal")

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self._state = "normal" if enabled else "disabled"
        self._redraw()

    def _measure(self, text: str) -> int:
        try:
            return max(self._fixed_width, int(tkfont.Font(root=self, font=self._font).measure(text)) + S(34))
        except Exception:  # noqa: BLE001
            return max(self._fixed_width, 60)

    def set_text(self, text: str) -> None:
        self._text = tr(text).upper()
        self.configure(width=self._measure(text))
        self._redraw()

    def _colors(self) -> tuple[str, str, str]:
        """返回 (填充, 边框, 文字)。红色只给主操作，其余靠灰阶分层。"""
        if not self._enabled:
            return ("#0F0F0F", "#242424", TEXT_DISABLED)
        if self._style == "accent":
            fill = {"normal": ACCENT, "hover": ACCENT_HOVER,
                    "pressed": ACCENT_PRESSED}.get(self._state, ACCENT)
            return (fill, fill, TEXT_ON_ACCENT)
        if self._style == "subtle":
            fill = {"normal": CARD, "hover": CARD_ALT,
                    "pressed": CONTROL_PRESSED}.get(self._state, CARD)
            return (fill, fill, TEXT_SECONDARY if self._state == "normal" else TEXT)
        fill = {"normal": CARD, "hover": CARD_ALT,
                "pressed": CONTROL_PRESSED}.get(self._state, CARD)
        border = CONTROL_BORDER_HOVER if self._state != "normal" else CONTROL_BORDER
        return (fill, border, TEXT)

    def _redraw(self) -> None:
        if not self.winfo_exists():
            return
        if not alive(self):
            return
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 2 or h <= 2:
            # 还没完成布局：退回配置尺寸兜底，保证首帧就能画出来
            w, h = int(self["width"]), self._height
        self.delete("all")
        if w <= 2 or h <= 2:
            return
        fill, border, fg = self._colors()
        focused = self._focused and self._enabled
        round_rect(self, 1, 1, w - 2, h - 2, RADIUS_CONTROL,
                   fill=fill, outline=ACCENT if focused else border,
                   width=2 if focused else 1)
        self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=self._font)


# ---------------------------------------------------------------- 分段控件


class Segmented(_VarWatcher, tk.Canvas):
    """Fluent 分段控件（药丸形容器 + 选中项高亮）。"""

    def __init__(self, parent: tk.Misc, options: list[str], variable: tk.StringVar,
                 theme: Theme | None = None, height: int | None = None, **kwargs) -> None:
        self.theme = theme or Theme(parent)
        self.options = list(options)
        self.variable = variable
        self._height = height or S(32)
        self._hover: int | None = None
        self._focused = False

        font = self.theme.body
        widths = [tkfont.Font(root=parent, font=font).measure(tr(o)) + S(30) for o in self.options]
        total = sum(widths) + S(12)
        super().__init__(parent, width=total, height=self._height, bg=BG,
                         highlightthickness=0, bd=0, takefocus=1, **kwargs)
        self._widths = widths

        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: (setattr(self, "_hover", None), self._redraw()))
        # 布局确定后必须重画一次：否则窗口刚起来时控件尺寸还是 1，首帧是空白的，
        # 要等鼠标划过（<Motion>）才显示出来
        self.bind("<Configure>", lambda e: self._redraw())
        # 键盘：左右方向键切换选项
        self.bind("<Left>", lambda e: self._step(-1))
        self.bind("<Right>", lambda e: self._step(1))
        # 不用 focus_get()：焦点控件被销毁时（比如 Combobox 的 popdown 关掉后）
        # 它会抛 KeyError，把这次重绘整个打断
        self.bind("<FocusIn>", lambda e: self._set_focus(True))
        self.bind("<FocusOut>", lambda e: self._set_focus(False))
        self._watch_var(variable)
        self.after(20, self._redraw)

    def _set_focus(self, on: bool) -> None:
        self._focused = on
        self._redraw()

    def set_options(self, options: list[str]) -> None:
        """换一组选项（例如浓度档位随机型从 3 档变 5 档）。"""
        self.options = list(options)
        font = self.theme.body
        self._widths = [tkfont.Font(root=self, font=font).measure(tr(o)) + S(30)
                        for o in self.options]
        self.configure(width=sum(self._widths) + S(12))
        if self.options and self.variable.get() not in self.options:
            self.variable.set(self.options[0])
        self._hover = None
        self._redraw()

    def _step(self, delta: int) -> str:
        try:
            i = self.options.index(self.variable.get())
        except ValueError:
            i = 0
        self.variable.set(self.options[(i + delta) % len(self.options)])
        return "break"

    def _index_at(self, x: float) -> int:
        acc = S(6)
        for i, w in enumerate(self._widths):
            if acc <= x < acc + w:
                return i
            acc += w
        return -1

    def _click(self, event) -> None:
        i = self._index_at(event.x)
        if 0 <= i < len(self.options):
            self.variable.set(self.options[i])

    def _motion(self, event) -> None:
        i = self._index_at(event.x)
        if i != self._hover:
            self._hover = i
            self._redraw()

    def _redraw(self) -> None:
        if not self.winfo_exists():
            return
        if not alive(self):
            return
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 2 or h <= 2:
            # 还没完成布局：退回配置尺寸兜底，保证首帧就能画出来
            w, h = int(self["width"]), self._height
        self.delete("all")
        if w <= 2 or h <= 2:
            return
        round_rect(self, 0.5, 0.5, w - 1, h - 1, RADIUS_PILL, fill=CONTROL_BG,
                   outline=ACCENT if self._focused else CARD_BORDER,
                   width=1)

        current = self.variable.get()
        acc = S(6)
        for i, (label, sw) in enumerate(zip(self.options, self._widths)):
            selected = label == current
            hovered = i == self._hover
            if selected:
                round_rect(self, acc, S(3), acc + sw, h - S(3), RADIUS_CONTROL,
                           fill=ACCENT, outline=ACCENT)
            elif hovered:
                round_rect(self, acc, S(3), acc + sw, h - S(3), RADIUS_CONTROL,
                           fill=CARD_ALT, outline="")
            fg = TEXT_ON_ACCENT if selected else (TEXT if hovered else TEXT_SECONDARY)
            self.create_text(acc + sw / 2, h / 2, text=tr(label), fill=fg,
                             font=self.theme.body_strong if selected else self.theme.body)
            acc += sw


# ---------------------------------------------------------------- 输入框


class FluentEntry(_VarWatcher, tk.Frame):
    """圆角输入框，带占位符与焦点高亮。"""

    def __init__(self, parent: tk.Misc, textvariable: tk.StringVar | None = None,
                 placeholder: str = "", theme: Theme | None = None,
                 width: int = 24, **kwargs) -> None:
        self.theme = theme or Theme(parent)
        super().__init__(parent, bg=CARD, **kwargs)
        self.variable = textvariable or tk.StringVar()
        self.placeholder = tr(placeholder)
        self._has_focus = False

        self.canvas = tk.Canvas(self, height=S(32), bg=CARD, highlightthickness=0, bd=0)
        self.canvas.pack(fill="x")
        self.entry = tk.Entry(self, textvariable=self.variable, font=self.theme.body,
                              relief="flat", bd=0, bg=CARD, fg=TEXT,
                              insertbackground=TEXT, width=width)
        self.entry.place(x=S(9), y=S(6), relwidth=1.0, relheight=1.0,
                         width=-S(18), height=-S(12))

        self.entry.bind("<FocusIn>", lambda e: (setattr(self, "_has_focus", True), self._redraw()))
        self.entry.bind("<FocusOut>", lambda e: (setattr(self, "_has_focus", False), self._redraw()))
        self._watch_var(self.variable)
        self.canvas.bind("<Button-1>", lambda e: self.entry.focus_set())
        self.canvas.bind("<Configure>", lambda e: self._redraw())
        self.after(20, self._redraw)

    def get(self) -> str:
        return self.entry.get()

    def set(self, value: str) -> None:
        self.entry.delete(0, "end")
        self.entry.insert(0, value)

    def _redraw(self) -> None:
        if not self.winfo_exists():
            return
        if not (alive(self) and alive(self.canvas)):
            return
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        self.canvas.delete("all")
        if w <= 2 or h <= 2:
            return
        border = CONTROL_BORDER_FOCUS if self._has_focus else CONTROL_BORDER
        width = 2 if self._has_focus else 1
        round_rect(self.canvas, 1, 1, w - 2, h - 2, RADIUS_CONTROL,
                   fill=CONTROL_BG, outline=border, width=width)

        if not self.variable.get() and not self._has_focus and self.placeholder:
            self.canvas.create_text(S(11), h / 2, anchor="w", text=self.placeholder,
                                    fill=TEXT_DISABLED, font=self.theme.body)


# ---------------------------------------------------------------- 进度条


class FluentProgress(tk.Canvas):
    """圆角进度条。"""

    def __init__(self, parent: tk.Misc, theme: Theme | None = None,
                 height: int | None = None, **kwargs) -> None:
        self.theme = theme or Theme(parent)
        self._value = 0.0
        h = height or S(4)
        super().__init__(parent, height=h, bg=CARD, highlightthickness=0, bd=0, **kwargs)
        self._height = h
        self.bind("<Configure>", lambda e: self._redraw())
        self.after(20, self._redraw)

    def set(self, value: float) -> None:
        self._value = max(0.0, min(100.0, float(value)))
        self._redraw()

    def _redraw(self) -> None:
        if not self.winfo_exists():
            return
        if not alive(self):
            return
        w = self.winfo_width()
        h = self.winfo_height() or self._height
        self.delete("all")
        if w <= 2:
            return
        r = h / 2
        round_rect(self, 0, 0, w - 1, h - 1, r, fill=CARD_BORDER, outline="")
        if self._value > 0:
            fill_w = max(h, (w - 1) * self._value / 100.0)
            round_rect(self, 0, 0, fill_w, h - 1, r, fill=ACCENT, outline="")


# ---------------------------------------------------------------- 可折叠分区


class Collapsible(tk.Frame):
    """可折叠分区（默认收起）。

    用来把「打印方向」「打印协议」这类排障开关收进「高级」，主界面只留
    日常要用的设置。标题行整行可点，也支持 Tab 聚焦 + 回车/空格展开。
    """

    def __init__(self, parent: tk.Misc, title: str, theme: Theme | None = None,
                 expanded: bool = False, bg: str | None = None, **kwargs) -> None:
        self.theme = theme or Theme(parent)
        bg = bg or CARD  # 同上：默认参数会被深色固化
        self._bg = bg
        super().__init__(parent, bg=bg, **kwargs)

        self._expanded = False
        self.header = tk.Frame(self, bg=bg, takefocus=1, cursor="hand2",
                               highlightthickness=0)
        self.header.pack(fill="x")

        self._arrow = tk.Label(self.header, text="▸", bg=bg, fg=TEXT_SECONDARY,
                               font=self.theme.body)
        self._arrow.pack(side="left")
        self._title = tk.Label(self.header, text=tr(title), bg=bg, fg=TEXT,
                               font=self.theme.body_strong)
        self._title.pack(side="left", padx=(2, 0))
        self._hint = tk.Label(self.header, text="", bg=bg, fg=TEXT_SECONDARY,
                              font=self.theme.caption)
        self._hint.pack(side="left", padx=(8, 0))

        self.body = tk.Frame(self, bg=bg)

        for w in (self.header, self._arrow, self._title, self._hint):
            w.bind("<Button-1>", lambda e: self.toggle())
        self.header.bind("<Return>", lambda e: (self.toggle(), "break")[1])
        self.header.bind("<space>", lambda e: (self.toggle(), "break")[1])
        self.header.bind("<FocusIn>", lambda e: self._title.configure(fg=ACCENT))
        self.header.bind("<FocusOut>", lambda e: self._title.configure(fg=TEXT))

        if expanded:
            self.expand()

    def set_hint(self, text: str) -> None:
        """标题右侧的灰色说明，例如「当前：正常」。"""
        self._hint.configure(text=text)

    def toggle(self) -> None:
        self.collapse() if self._expanded else self.expand()

    def expand(self) -> None:
        self._expanded = True
        self._arrow.configure(text="▾")
        self.body.pack(fill="x", pady=(8, 0))

    def collapse(self) -> None:
        self._expanded = False
        self._arrow.configure(text="▸")
        self.body.pack_forget()


# ---------------------------------------------------------------- 开关


class Toggle(_VarWatcher, tk.Canvas):
    """Fluent 开关（左关右开）。"""

    def __init__(self, parent: tk.Misc, variable: tk.BooleanVar,
                 theme: Theme | None = None, **kwargs) -> None:
        self.theme = theme or Theme(parent)
        self.variable = variable
        self._focused = False
        super().__init__(parent, width=S(40), height=S(20), bg=CARD,
                         highlightthickness=0, bd=0, takefocus=1, **kwargs)
        self.bind("<Button-1>", lambda e: self._toggle())
        self.bind("<space>", lambda e: (self._toggle(), "break")[1])
        self.bind("<Return>", lambda e: (self._toggle(), "break")[1])
        self.bind("<FocusIn>", lambda e: self._set_focus(True))
        self.bind("<FocusOut>", lambda e: self._set_focus(False))
        self._watch_var(variable)
        self.after(20, self._redraw)

    def _set_focus(self, on: bool) -> None:
        self._focused = on
        self._redraw()

    def _toggle(self) -> None:
        self.variable.set(not self.variable.get())

    def _redraw(self) -> None:
        if not self.winfo_exists():
            return
        if not alive(self):
            return
        w, h = S(40), S(20)
        self.delete("all")
        on = bool(self.variable.get())
        # Nothing 的开关是方形拨杆，不是圆形
        track = ACCENT if on else CONTROL_BORDER
        round_rect(self, 1, 1, w - 2, h - 2, RADIUS_CONTROL, fill=track, outline=track)
        if self._focused:
            round_rect(self, 0, 0, w - 1, h - 1, RADIUS_CONTROL, fill="", outline=ACCENT, width=1)
        d = h - S(8)
        cx = (w - S(6) - d) if on else S(6)
        self.create_rectangle(cx, S(4), cx + d, S(4) + d, fill=TEXT, outline="")
