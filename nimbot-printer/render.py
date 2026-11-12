"""把内容渲染成标签位图（灰度，黑字白底）。

**横版**排版：画布宽 = 标签长度（走纸方向），高 = 打印头 96 点（12 mm），
文字沿标签长度方向从左往右写，就像在一条长纸带上写字。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from i18n import fmt, tr

MM_TO_PX = 203 / 25.4  # 203 dpi

_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/dengxian.ttf",
]

_font_cache: dict[tuple[int, bool], Any] = {}


@dataclass
class LayoutResult:
    image: Image.Image
    used_width: int
    overflow: bool
    warnings: list[str] = field(default_factory=list)
    capacity: str = ""
    font_used: int = 0


def _rule_of(line: str) -> int:
    """整行是 `---` 或 `===` 时当作分隔线，返回线粗；否则返回 0。

    用户手打一排短横很难刚好铺满纸宽（不同字号下宽度还不一样），
    所以给定一个约定：整行由 3 个以上 `-` 组成画细线，`=` 组成画粗线，
    长度由排版自动撑满可用宽度。
    """
    text = line.strip()
    if len(text) < 3:
        return 0
    chars = set(text)
    if chars == {"-"}:
        return 1
    if chars == {"="}:
        return 3
    return 0


def get_font(size: int, bold: bool = False):
    key = (size, bold)
    if key in _font_cache:
        return _font_cache[key]

    paths = list(_FONT_CANDIDATES)
    if bold:
        paths = ["C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/simhei.ttf"] + paths

    for path in paths:
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, size)
                _font_cache[key] = font
                return font
            except Exception:  # noqa: BLE001
                continue

    font = ImageFont.load_default()
    _font_cache[key] = font
    return font


def mm_to_px(mm: float) -> int:
    return max(1, round(mm * MM_TO_PX))


# ---------------------------------------------------------------- 符号字体
#
# 主字体（微软雅黑）里绝大多数 emoji 都是豆腐块。Windows 自带 Segoe UI Symbol
# 是单色的，正好适合热敏打印：实测 😀👍⭐✅❤ 等 42 个常用符号全部能渲染。
# PIL 不支持一行内自动 fallback，只能自己按字符切段、分别用不同字体画。

_SYMBOL_FONT_PATH = "C:/Windows/Fonts/seguisym.ttf"
_SYMBOL_RANGES = (
    (0x2190, 0x21FF),     # 箭头
    (0x2300, 0x23FF),     # 技术符号
    (0x25A0, 0x25FF),     # 几何图形 ■□▲●
    (0x2600, 0x27BF),     # 杂项符号、dingbats ★♥✓
    (0x2B00, 0x2BFF),     # 星星等
    (0x1F000, 0x1FAFF),   # emoji 主体 😀👍🚀
)


def get_symbol_font(size: int):
    """符号/emoji 字体；系统没有这个字体时返回 None（退化成主字体）。"""
    key = ("sym", size)
    if key in _font_cache:
        return _font_cache[key]
    font = None
    if os.path.exists(_SYMBOL_FONT_PATH):
        try:
            font = ImageFont.truetype(_SYMBOL_FONT_PATH, size)
        except Exception:  # noqa: BLE001
            font = None
    _font_cache[key] = font
    return font


def _text_fonts(size: int, bold: bool = False):
    return get_font(size, bold), get_symbol_font(size)


def _is_symbol_char(ch: str) -> bool:
    o = ord(ch)
    return any(lo <= o <= hi for lo, hi in _SYMBOL_RANGES)


def _runs(text: str, font, sym):
    """按字符切段：符号/emoji 用专用字体，其余用主字体。

    变体选择符（U+FE0F，比如 "❤️" 的第二个码位）本身不占宽度，直接丢掉。
    """
    if sym is None:
        return [(text, font)] if text else []
    out: list[tuple[str, object]] = []
    for ch in text:
        if 0xFE00 <= ord(ch) <= 0xFE0F:
            continue
        f = sym if _is_symbol_char(ch) else font
        if out and out[-1][1] is f:
            out[-1] = (out[-1][0] + ch, f)
        else:
            out.append((ch, f))
    return out


def measure_text(text: str, font, sym=None) -> int:
    return sum(int(f.getlength(seg)) for seg, f in _runs(text, font, sym))


def draw_text(draw, xy, text: str, font, sym=None, fill=0) -> float:
    x, y = xy
    for seg, f in _runs(text, font, sym):
        draw.text((x, y), seg, font=f, fill=fill)
        x += f.getlength(seg)
    return x


def fit_layout(lines: list[str], avail_w: int, avail_h: int, requested: int,
               bold: bool = False, min_size: int = 8,
               min_gap: float = 1.10, base_gap: float = 1.35):
    """在给定区域内求最大的可行字号，并给出该字号下能用的行距。

    两个约束都随字号单调递增，所以从大到小扫，第一个满足的就是最优：
      - 最长一行（不含分隔线）不超过 ``avail_w``
      - 行数 × 行距 × 字号不超过 ``avail_h``

    行距不再写死，而是由高度约束反解再夹到 [min_gap, base_gap]：
    字号够小时行距回到 base_gap，行数多时才往下压，压到 min_gap 为止。
    """
    n = max(1, len(lines))
    upper = min(48, max(min_size, int(requested)))

    def widest(size: int) -> int:
        font, sym = _text_fonts(size, bold)
        return max((ink_box(line, font, sym)[1] for line in lines
                    if line and not _rule_of(line)), default=0)

    for size in range(upper, min_size - 1, -1):
        if widest(size) > avail_w:
            continue
        gap = avail_h / (n * size)
        if gap < min_gap:
            continue
        return size, min(base_gap, gap)

    return min_size, min_gap


def ink_box(text: str, font, sym=None) -> tuple[int, int]:
    """量出文字**实际墨迹**相对绘制原点的 (左偏移, 宽度)。

    ``textbbox`` 返回的是排版框（含左右边距、按字前宽度算），拿它做对齐
    会让「居中」整体偏移几个像素。这里直接把字画到临时画布上再量黑点。
    """
    if not text:
        return 0, 0
    pad = 4
    w = measure_text(text, font, sym) + pad * 2
    h = int(getattr(font, "size", 16) * 2) + pad * 2
    tmp = Image.new("L", (max(4, w), max(4, h)), 255)
    draw_text(ImageDraw.Draw(tmp), (pad, pad), text, font, sym, fill=0)
    px = tmp.load()
    xs = [x for y in range(tmp.height) for x in range(tmp.width) if px[x, y] < 250]
    if not xs:
        return 0, 0
    return min(xs) - pad, max(xs) - min(xs) + 1


# ---------------------------------------------------------------- 横版排版


def render_landscape_label(
    text: str,
    strip_dots: int,
    length_dots: int,
    font_size: int = 28,
    bold: bool = False,
    align: str = "left",
    qr_value: str = "",
    qr_size: int = 64,
    barcode_value: str = "",
    barcode_symbology: str = "code128",
    image_path: str = "",
    margin: int = 6,
    line_gap: int = 4,
    extra_order: str = "pic,bc,qr",
) -> LayoutResult:
    """横版排版：文字沿标签长度方向书写。

    ``strip_dots`` 是打印头点数（12 mm 纸 = 96），``length_dots`` 是标签长度点数。
    二维码 / 条码 / 图片放在文字右侧（标签更靠后的位置）。
    ``extra_order`` 是附件的**从左到右**排列顺序，取值为 ``pic`` / ``bc`` / ``qr``
    的组合，例如 ``"qr,bc,pic"``。
    """
    img = Image.new("L", (length_dots, strip_dots), 255)
    draw = ImageDraw.Draw(img)

    warnings: list[str] = []
    usable_h = max(8, strip_dots - margin * 2)
    requested_size = max(8, int(font_size))
    if requested_size > 48:          # 与 fit_layout 的上限保持一致，先钳住再判断
        warnings.append(fmt("字号 {n} 太大——最大 {max}，已按 {max} 排版", n=requested_size, max=48))
        requested_size = 48
    lines = text.split("\n") if text else [""]

    # 右侧附件区宽度
    extras_w = 0
    qr_img = barcode_img = picture = None
    if qr_value.strip():
        qr_img, err = _make_qr(qr_value, qr_size)
        if err:
            warnings.append(err)
    if barcode_value.strip():
        barcode_img, err = _make_barcode(barcode_value, barcode_symbology)
        if err:
            warnings.append(err)
    if image_path.strip():
        picture, err = _make_picture(image_path, usable_h)
        if err:
            warnings.append(err)

    # 右侧附件并排排列，预留宽度要按各附件宽度之和算（不是取最大）
    by_kind = {"pic": picture, "bc": barcode_img, "qr": qr_img}
    order = [k.strip() for k in extra_order.split(",") if k.strip() in by_kind]
    for k in by_kind:
        if k not in order:
            order.append(k)
    # 从左到右的视觉顺序
    ordered = [by_kind[k] for k in order if by_kind[k] is not None]

    extras_w = 0
    for extra in ordered:
        extras_w += extra.width + margin

    text_w = max(8, length_dots - margin * 2 - extras_w)

    # 附件先摆好，文字区宽度才确定；再在这个宽度里解出最大字号
    font_size, line_gap = fit_layout(lines, text_w, usable_h, requested_size, bold)
    font, sym = _text_fonts(font_size, bold)
    line_h = max(1, int(font_size * line_gap))
    text_h = line_h * len(lines)
    max_lines = max(1, usable_h // max(1, line_h))

    if font_size < requested_size:
        warnings.append(fmt(
            "{rows} 行放不下——字号已缩到 {n}（行距 {gap:.2f}），或请减少行数",
            n=font_size, gap=line_gap, rows=len(lines), width=text_w, h=usable_h))


    # ---- 边界三：单行不能超过标签长度
    ink: dict[str, tuple[int, int]] = {}
    for i, line in enumerate(lines, 1):
        if not line or _rule_of(line):
            continue
        box = ink_box(line, font, sym)
        ink[line] = box
        if box[1] > text_w:
            warnings.append(fmt(
                "第 {i} 行「{text}…」过长约 {over} 点，会顶出标签，请缩短或改小字号",
                i=i, text=line[:12], over=box[1] - text_w))

    # 文字：按实际墨迹对齐
    y = (strip_dots - text_h) // 2
    used_right = margin
    for line in lines:
        if not line:
            y += line_h
            continue
        thick = _rule_of(line)
        if thick:
            cy = y + line_h // 2
            draw.rectangle([margin, cy, margin + max(4, text_w) - 1, cy + thick - 1],
                           fill=0)
            y += line_h
            used_right = max(used_right, margin + text_w)
            continue
        ink_x, ink_w = ink.get(line, (0, 0))
        if align == "center":
            ink_left = margin + max(0, (text_w - ink_w) // 2)
        elif align == "right":
            ink_left = margin + max(0, text_w - ink_w)
        else:
            ink_left = margin
        draw_text(draw, (ink_left - ink_x, y), line, font, sym)
        y += line_h
        used_right = max(used_right, ink_left + ink_w)

    # 右侧附件：从最右端往回摆，整体视觉顺序与 ordered 一致
    x_extra = length_dots - margin
    for extra in reversed(ordered):
        x_extra -= extra.width
        ey = (strip_dots - extra.height) // 2
        img.paste(extra, (x_extra, ey))
        x_extra -= margin

    overflow = used_right > (length_dots - margin - extras_w)
    if overflow:
        warnings.append(tr("文字超出标签长度，请换更长的标签或缩小字号"))

    if not text.strip() and not (qr_img or barcode_img or picture):
        warnings.append(tr("内容为空，打出来会是一张空白标签"))

    # 容量提示：每行大约能放几个字（按全角字宽≈字号估算）
    chars_per_line = max(1, text_w // max(1, font_size))
    capacity = fmt("{mm} mm 标签 · 字号 {size} · 每行约 {chars} 字 · {n}/{max} 行",
                   mm=f"{length_dots / mm_to_px(1):.0f}", size=font_size,
                   chars=chars_per_line, n=len(lines), max=max_lines)
    if font_size != requested_size:
        capacity += f"（原字号 {requested_size}）"

    return LayoutResult(
        image=img,
        used_width=used_right + extras_w,
        overflow=overflow,
        warnings=warnings,
        capacity=capacity,
        font_used=font_size,
    )


def render_portrait_label(
    text: str,
    strip_dots: int,
    length_dots: int,
    font_size: int = 28,
    bold: bool = False,
    align: str = "left",
    qr_value: str = "",
    qr_size: int = 64,
    barcode_value: str = "",
    barcode_symbology: str = "code128",
    image_path: str = "",
    margin: int = 6,
    line_gap: int = 4,
    extra_order: str = "pic,bc,qr",
) -> LayoutResult:
    """竖版排版：画布宽 = 打印头点数（12 mm 纸 = 96），高 = 标签长度。

    文字沿**纸宽**方向书写，一行写满向下换行。和横版正好互补：
    12×30 mm 的纸，横版能写 1~2 行长文字，竖版能写 7~8 行短文字，
    适合竖着贴的标签。

    附件（二维码 / 条码 / 图片）在文字**下方**横排。
    """
    img = Image.new("L", (strip_dots, length_dots), 255)
    draw = ImageDraw.Draw(img)

    warnings: list[str] = []
    usable_w = max(8, strip_dots - margin * 2)     # 一行能写多宽
    usable_h = max(8, length_dots - margin * 2)    # 整张有多高

    # ---- 附件先占掉底部高度，剩下的才留给文字
    qr_img = barcode_img = picture = None
    if qr_value.strip():
        qr_img, err = _make_qr(qr_value, qr_size)
        if err:
            warnings.append(err)
            qr_img = None
    if barcode_value.strip():
        barcode_img, err = _make_barcode(barcode_value, barcode_symbology)
        if err:
            warnings.append(err)
            barcode_img = None
    if image_path.strip():
        picture, err = _make_picture(image_path, usable_w // 2)
        if err:
            warnings.append(err)
            picture = None

    by_kind = {"pic": picture, "bc": barcode_img, "qr": qr_img}
    order = [k.strip() for k in extra_order.split(",") if k.strip() in by_kind]
    for k in by_kind:
        if k not in order:
            order.append(k)
    ordered = [by_kind[k] for k in order if by_kind[k] is not None]

    extras_h = (max(e.height for e in ordered) + margin) if ordered else 0
    text_area_h = max(8, usable_h - extras_h)

    requested_size = max(8, int(font_size))
    if requested_size > 48:          # 与 fit_layout 的上限保持一致，先钳住再判断
        warnings.append(fmt("字号 {n} 太大——最大 {max}，已按 {max} 排版", n=requested_size, max=48))
        requested_size = 48
    lines = text.split("\n") if text else [""]

    # 字号与行距一次性解出来：优先大字，行距在 [1.10, 1.35] 之间自动让位
    font_size, line_gap = fit_layout(lines, usable_w, text_area_h, requested_size, bold)
    font, sym = _text_fonts(font_size, bold)
    line_h = max(1, int(font_size * line_gap))
    text_h = line_h * len(lines)
    max_lines = max(1, text_area_h // max(1, line_h))

    if font_size < requested_size:
        warnings.append(fmt(
            "{rows} 行放不下——字号已缩到 {n}（行距 {gap:.2f}），或请减少行数",
            n=font_size, gap=line_gap, rows=len(lines), width=usable_w))

    # ---- 边界四：缩到底仍然超宽，只能提示改内容
    ink: dict[str, tuple[int, int]] = {}
    overflow = False
    for i, line in enumerate(lines, 1):
        if not line or _rule_of(line):
            continue
        box = ink_box(line, font, sym)
        ink[line] = box
        if box[1] > usable_w:
            overflow = True
            warnings.append(fmt(
                "第 {i} 行「{text}…」过长约 {over} 点，会顶出标签，请缩短或改小字号",
                i=i, text=line[:12], over=box[1] - usable_w))

    # ---- 绘制：纵向居中，横向按对齐
    y = margin + max(0, (text_area_h - text_h) // 2)
    for line in lines:
        if not line:
            y += line_h
            continue
        thick = _rule_of(line)
        if thick:
            cy = y + line_h // 2
            draw.rectangle([margin, cy, margin + usable_w - 1, cy + thick - 1], fill=0)
            y += line_h
            continue
        ink_x, ink_w = ink.get(line, (0, 0))
        if align == "center":
            left = margin + max(0, (usable_w - ink_w) // 2)
        elif align == "right":
            left = margin + max(0, usable_w - ink_w)
        else:
            left = margin
        draw_text(draw, (left - ink_x, y), line, font, sym)
        y += line_h

    # ---- 附件：底部横排居中
    if ordered:
        tallest = max(e.height for e in ordered)
        total_w = sum(e.width for e in ordered) + margin * (len(ordered) - 1)
        x = max(margin, (strip_dots - total_w) // 2)
        base_y = length_dots - margin - tallest
        for extra in ordered:
            img.paste(extra, (x, base_y + (tallest - extra.height) // 2))
            x += extra.width + margin

    if not text.strip() and not ordered:
        warnings.append(tr("内容为空，打出来会是一张空白标签"))

    chars_per_line = max(1, usable_w // max(1, font_size))
    capacity = fmt("竖版 · {mm} mm 标签 · 字号 {size} · 每行约 {chars} 字 · {n}/{max} 行",
                   mm=f"{length_dots / mm_to_px(1):.0f}", size=font_size,
                   chars=chars_per_line, n=len(lines), max=max_lines)
    if font_size != requested_size:
        capacity += f"（原字号 {requested_size}）"

    used = max((ink.get(line, (0, 0))[1] for line in lines if line), default=0)
    return LayoutResult(
        image=img,
        used_width=used,
        overflow=overflow,
        warnings=warnings,
        capacity=capacity,
        font_used=font_size,
    )

def _make_qr(value: str, size: int) -> tuple[Image.Image | None, str | None]:
    try:
        import qrcode

        qr = qrcode.QRCode(border=1, box_size=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(value)
        qr.make(fit=True)
        raw = qr.make_image(fill_color="black", back_color="white")
        pil = raw.get_image() if hasattr(raw, "get_image") else raw
        size = max(24, min(size, 96))
        return pil.convert("L").resize((size, size), Image.Resampling.NEAREST), None
    except Exception as e:  # noqa: BLE001
        return None, fmt("二维码生成失败：{err}", err=e)


def _make_barcode(value: str, symbology: str) -> tuple[Image.Image | None, str | None]:
    try:
        import barcode as barcode_lib
        from barcode.writer import ImageWriter

        cls = barcode_lib.get_barcode_class(symbology)
        code = cls(value.strip(), writer=ImageWriter())
        raw = code.render(writer_options={"module_height": 6.0, "quiet_zone": 1.0, "write_text": False})
        img = raw.convert("L")
        target_h = 72
        scale = target_h / img.height
        target_w = max(20, int(img.width * scale))
        return img.resize((target_w, target_h), Image.Resampling.NEAREST), None
    except Exception as e:  # noqa: BLE001
        hint = ""
        if symbology == "ean13":
            hint = "（EAN-13 需要 12 位数字）"
        elif symbology == "upca":
            hint = "（UPC-A 需要 11-12 位数字）"
        return None, fmt("条码生成失败{hint}：{err}", hint=hint, err=e)


def _make_picture(path: str, max_h: int) -> tuple[Image.Image | None, str | None]:
    if not path or not os.path.exists(path):
        return None, fmt("图片文件不存在：{path}", path=path) if path else None
    try:
        im = Image.open(path).convert("L")
    except Exception as e:  # noqa: BLE001
        return None, fmt("图片打不开：{err}", err=e)
    scale = min(max_h / im.height, 3.0)
    new_w = max(4, int(im.width * scale))
    new_h = max(4, int(im.height * scale))
    im = im.resize((new_w, new_h), Image.Resampling.LANCZOS)
    return im.point(lambda v: 0 if v < 128 else 255, mode="L"), None


# ---------------------------------------------------------------- 测试页


def render_test_label(strip_dots: int = 96, length_dots: int = 240) -> Image.Image:
    """方向测试标签：三角形指向标签起点（先出纸的一端）。"""
    img = Image.new("L", (length_dots, strip_dots), 255)
    draw = ImageDraw.Draw(img)
    draw.rectangle([1, 1, length_dots - 2, strip_dots - 2], outline=0, width=1)

    # 左端三角形，指向左侧
    cy = strip_dots // 2
    draw.polygon([(8, cy), (30, cy - 16), (30, cy + 16)], fill=0)

    x = 40
    for text, size in (("测试页", 20), ("左 = 起点", 15)):
        font = get_font(size, bold=(size > 18))
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text((x, cy - th // 2 - 2), text, fill=0, font=font)
        x += tw + 16

    # 中段分辨率线条
    lx = x + 10
    for i in range(3):
        draw.rectangle([lx, 14 + i * 12, lx + 3, strip_dots - 15], fill=0)
        lx += 8

    return img


def render_diagnostic_label(strip_dots: int = 96, length_dots: int = 320) -> Image.Image:
    """诊断页：一次打印就能判断问题出在哪一层。

    从标签起点（先出纸的一端）到末端依次是：

    1. **整块全黑** —— 如果这块都是白的，说明打印头没加热：
       要么纸装反了（热敏涂层朝下），要么纸型/浓度不对。
    2. **横向条纹**（沿打印头方向交替）—— 测 96 个点的对齐。
    3. **纵向条纹**（沿走纸方向交替）—— 测走纸步进。
    4. **文字 TEST** —— 测字体渲染。
    5. **棋盘格** —— 测整体位图通路。
    """
    img = Image.new("L", (length_dots, strip_dots), 255)
    draw = ImageDraw.Draw(img)

    seg = max(24, length_dots // 5)
    x = 0

    # 1. 整块全黑
    draw.rectangle([x, 0, x + seg, strip_dots - 1], fill=0)
    x += seg + 2

    # 2. 横向条纹：沿打印头方向（y）交替
    if x < length_dots:
        band = 4
        for y in range(0, strip_dots, band * 2):
            draw.rectangle([x, y, min(x + seg, length_dots), y + band - 1], fill=0)
        x += seg + 2

    # 3. 纵向条纹：沿走纸方向（x）交替
    if x < length_dots:
        band = 4
        for xx in range(x, min(x + seg, length_dots), band * 2):
            draw.rectangle([xx, 0, xx + band - 1, strip_dots - 1], fill=0)
        x += seg + 2

    # 4. 文字
    if x < length_dots:
        font = get_font(30, bold=True)
        for i, text in enumerate(("TEST", "123")):
            bbox = draw.textbbox((0, 0), text, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            ty = (strip_dots // 2 - th) if i == 0 else (strip_dots // 2)
            if ty >= 0:
                draw.text((x, ty), text, fill=0, font=font)
        x += seg + 2

    # 5. 棋盘格
    if x < length_dots:
        cell = 6
        for cy in range(0, strip_dots, cell):
            for cx in range(x, min(x + seg, length_dots), cell):
                if ((cx - x) // cell + cy // cell) % 2 == 0:
                    draw.rectangle([cx, cy, cx + cell - 1, cy + cell - 1], fill=0)

    # 边框，便于看裁切
    draw.rectangle([0, 0, length_dots - 1, strip_dots - 1], outline=0, width=1)
    return img
