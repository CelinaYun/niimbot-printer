"""把标签图像编码成打印机可接受的位图行数据。

参考 niimbluelib 的 ``ImageEncoder``：
图像是 1 位位图，每行按 8 像素打包（MSB 在左），连续相同的行合并成 repeat。

D110/D11 的 ``printDirection`` 为 ``left``：源图的 X 轴是走纸方向（rows），
Y 轴是打印头方向（cols，96 点 ≈ 12 mm）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .packet import u16be

# D110 / D11：203 dpi，打印头 96 点 = 12 mm
D110_PRINTHEAD_PIXELS = 96
D110_DPI = 203

MAX_REPEAT = 255  # repeat 是 1 字节字段


@dataclass
class ImageRow:
    row_number: int
    repeat: int
    black_pixels_count: int
    row_data: bytes | None = None  # None 表示空白行


@dataclass
class EncodedImage:
    cols: int  # 打印头方向像素数（应 ≤ 96）
    rows: int  # 走纸方向像素数
    rows_data: list[ImageRow] = field(default_factory=list)


def encode_image(source: "object", print_direction: str = "left") -> EncodedImage:
    """把 PIL Image（任意模式）编码成 ``EncodedImage``。

    ``print_direction="left"`` 时源图会被按 90° 语义读取：源图宽=走纸长度，
    源图高=打印头宽度。若源图是自然竖排标签（宽 96、高=长度），请先
    逆时针旋转 90° 再调用，见 :func:`prepare_source_image`。
    """
    # 统一到 8 位灰度：0 = 黑，255 = 白，避免 "1"/"L"/"RGB" 像素值语义不一致
    if hasattr(source, "convert"):
        source = source.convert("L")

    width = source.width
    height = source.height

    if print_direction == "left":
        original_cols = height
        rows = width
    else:
        original_cols = width
        rows = height

    cols = (original_cols + 7) // 8 * 8
    bytes_per_row = cols // 8

    px = source.load()

    def pixel_is_black(x: int, y: int) -> bool:
        if print_direction == "left":
            # 与 niimbluelib 一致：encoded(x, row) ← source(row, height-1-x)
            sx, sy = y, height - 1 - x
        else:
            sx, sy = x, y
        if sx < 0 or sy < 0 or sx >= width or sy >= height:
            return False
        value = px[sx, sy]
        if isinstance(value, tuple):
            return not (value[0] > 250 and value[1] > 250 and value[2] > 250)
        return value < 128

    rows_data: list[ImageRow] = []

    for row in range(rows):
        row_bytes = bytearray(bytes_per_row)
        black_count = 0
        is_void = True

        for octet in range(bytes_per_row):
            b = 0
            for bit in range(8):
                col = octet * 8 + bit
                if col >= original_cols:
                    continue
                if pixel_is_black(col, row):
                    b |= 1 << (7 - bit)
                    black_count += 1
                    is_void = False
            row_bytes[octet] = b

        entry = ImageRow(
            row_number=row,
            repeat=1,
            black_pixels_count=black_count,
            row_data=None if is_void else bytes(row_bytes),
        )

        if rows_data:
            last = rows_data[-1]
            same = (last.row_data is None and entry.row_data is None) or (
                last.row_data is not None
                and entry.row_data is not None
                and last.row_data == entry.row_data
            )
            if same and last.repeat < MAX_REPEAT:
                last.repeat += 1
            else:
                rows_data.append(entry)
        else:
            rows_data.append(entry)

    return EncodedImage(cols=cols, rows=rows, rows_data=rows_data)


def prepare_source_image(image: "object", rotate: int = 90) -> "object":
    """把自然竖排标签图（宽=打印头 96 点，高=长度）转成编码器期望的源图。

    默认逆时针旋转 90°，这样编码后 ``encoded(row, x)`` 恰好等于原图 ``(x, row)``：
    标签顶边先走纸，左侧对应打印头 0 号点，既不翻转也不镜像。
    """
    if rotate == 0:
        return image
    # PIL: ROTATE_90 = 逆时针 90°
    return image.rotate(rotate, expand=True)


def prepare_landscape_source(image: "object") -> "object":
    """把横版画布（宽=长度、高=打印头点数）转成编码器的源图。

    编码器按 ``encoded(row, x) ← source(row, H-1-x)`` 读取，会把 Y 轴翻转；
    这里预先上下翻转一次，两者抵消，使 ``encoded(row, x)`` 恰好等于画布像素。
    """
    from PIL import Image as _PILImage

    return image.transpose(_PILImage.Transpose.FLIP_TOP_BOTTOM)


# ---------------------------------------------------------------- 打印取向
#
# 选项名说的是**纸上看到的效果**（相对预览）。打印头点序方向在不同机器上
# 可能相反，纸上字反了就换一个，换到和预览一致为止。
#
# 变换作用在「进编码器之前的画布」上。编码器自带一次 Y 轴翻转，所以：
#   正常      → 上下翻转画布，抵消编码器的翻转，纸上 = 预览
#   上下翻转  → 什么都不做，纸上 = 预览上下颠倒
#   左右翻转  → 转 180°，纸上 = 预览左右镜像
#   旋转 180° → 左右翻转画布，纸上 = 预览转 180°

ORIENTATIONS = ("正常", "左右翻转", "上下翻转", "旋转 180°")


def apply_orientation(image: "object", name: str) -> "object":
    """按取向选项变换横版画布，返回编码器的源图。

    这台机器的编码链路里有两处固定反向：编码器读取时按打印头方向反转一次，
    走纸方向又与画布 X 轴相反。两者叠加，等于打印机把源图整体转 180° 出纸。
    所以想让纸上和预览一致，源图必须先自己转 180°（「正常」分支）。

    选项名描述的是**纸上相对预览的效果**；预览侧换算见 :func:`simulate_output`。
    """
    from PIL import Image as _PILImage
    T = _PILImage.Transpose

    if name == "左右翻转":
        return image.transpose(T.FLIP_TOP_BOTTOM)
    if name == "上下翻转":
        return image.transpose(T.FLIP_LEFT_RIGHT)
    if name == "旋转 180°":
        return image
    return image.rotate(180, expand=True)              # 正常


def simulate_output(image: "object", name: str) -> "object":
    """把画布模拟成「纸上会看到的样子」，预览直接用它。

    打印机在链路末端整体转 180°，所以纸上 = 转 180°(源图)。
    这样预览显示的就是出纸效果 —— 看到是正的，打出来就是正的。
    """
    return apply_orientation(image, name).rotate(180, expand=True)


def count_pixels(data: bytes, printhead_pixels: int = D110_PRINTHEAD_PIXELS) -> tuple[list[int], int]:
    """统计位图行的黑像素数，按 3 段拆分（打印机用它做加热控制）。"""
    total = 0
    parts = [0, 0, 0]
    chunk_size = printhead_pixels // 8 // 3  # 96/8/3 = 4
    if chunk_size <= 0:
        chunk_size = 1

    split = len(data) <= chunk_size * 3

    for byte_n, value in enumerate(data):
        chunk_idx = byte_n // chunk_size if split else 0
        for bit_n in range(8):
            if value & (1 << bit_n):
                total += 1
                if split and chunk_idx < 3:
                    parts[chunk_idx] += 1

    if not split:
        parts = [0, total & 0xFF, (total >> 8) & 0xFF]

    return parts, total


def index_pixels(data: bytes) -> bytes:
    """把位图行中为 1 的像素位置转成 2 字节大端索引列表（用于稀疏行）。"""
    out = bytearray()
    for byte_pos, b in enumerate(data):
        for bit_pos in range(8):
            if b & (1 << (7 - bit_pos)):
                out += u16be(byte_pos * 8 + bit_pos)
    return bytes(out)


def rows_to_packets(
    image: EncodedImage,
    printhead_pixels: int = D110_PRINTHEAD_PIXELS,
    single_line_rows: bool = False,
):
    """把 ``EncodedImage`` 转成打印行命令包序列。

    黑像素 ≤ 6 的行用稀疏索引包（``0x83``），否则用整行位图包（``0x85``），
    空白行用 ``0x84``。返回 ``(命令号, 数据)`` 列表。

    ``single_line_rows=True`` 时不合并相邻相同行，每行独立成包且 repeat=1，
    与 NiimPrintX 的真机验证实现保持一致。
    """
    from . import packet as pkt

    out: list[tuple[int, bytes]] = []

    def emit(d: ImageRow, repeat: int) -> None:
        if d.row_data is None:
            out.append((pkt.CMD_PRINT_EMPTY_ROW, pkt.u16be(d.row_number) + bytes([repeat])))
            return

        parts, total = count_pixels(d.row_data, printhead_pixels)

        if total <= 6 and not single_line_rows:
            out.append(
                (
                    pkt.CMD_PRINT_BITMAP_ROW_INDEXED,
                    pkt.u16be(d.row_number)
                    + bytes(parts)
                    + bytes([repeat])
                    + index_pixels(d.row_data),
                )
            )
        else:
            out.append(
                (
                    pkt.CMD_PRINT_BITMAP_ROW,
                    pkt.u16be(d.row_number) + bytes(parts) + bytes([repeat]) + d.row_data,
                )
            )

    for d in image.rows_data:
        if single_line_rows:
            # 已合并的行拆回逐行发送：每包的行号必须递增，
            # 否则打印机在同一行反复加热，出来是空白/糊的。
            for k in range(d.repeat):
                emit(ImageRow(row_number=d.row_number + k, repeat=1,
                              black_pixels_count=d.black_pixels_count,
                              row_data=d.row_data), 1)
        else:
            emit(d, d.repeat)

    return out
