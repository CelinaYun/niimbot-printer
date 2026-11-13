"""界面文案的中英资源表。

设计取舍：以**中文原文作为 key**，而不是给每处文案编一个 key。
好处是控件创建时可以直接 ``tr(text)`` 自动翻译，不必改动上百个调用点；
漏收录的字符串原样返回，最坏也就是留一句中文，不会崩。

带参数的文案（f-string）匹配不到静态表，要用 :func:`fmt`，
把模板写成 ``{name}`` 占位符的形式。
"""

from __future__ import annotations

LANGS = [("zh", "中"), ("en", "EN")]
_lang = "zh"

# ---------------------------------------------------------------- 静态文案
# 键 = 中文原文（必须与代码里完全一致），值 = 英文
_EN: dict[str, str] = {
    # —— 顶栏 / 状态
    "未连接": "Disconnected",
    "已连接": "Connected",
    "已断开": "Disconnected",
    "连接失败": "Connect failed",
    "连接中": "Connecting",

    # —— 连接卡
    "打印机连接": "Connection",
    "字号已缩到 {n}，让最长的一行放进 {width} 点宽":
        "Font size reduced to {n} so the longest line fits {width}px",
    "（已隐藏 {n} 个不像打印机的）": " ({n} unrelated hidden)",
    "最大字号": "Max font",
    "已调到这张标签能放下的最大字号：{n}": "Largest font that fits this label: {n}",
    "横版": "Landscape",
    "竖版": "Portrait",
    "版式": "Layout",
    "竖版 · {mm} mm 标签 · 字号 {size} · 每行约 {chars} 字 · {n}/{max} 行":
        "Portrait · {mm} mm label · {size}px · ~{chars} chars/line · {n}/{max} lines",
    "浅色": "Light",
    "深色": "Dark",
    "连接": "Connect",
    "蓝牙": "Bluetooth",
    "USB 串口": "USB Serial",
    "扫描": "Scan",
    "扫描中…": "Scanning…",
    "断开": "Disconnect",

    # —— 输入
    "输入文字": "Text",
    "每行一句，沿标签长度方向横向打印": "One line per row, printed along the length",
    "模板": "Template",
    "存为模板": "Save as",
    "删除": "Delete",
    "每行打一张": "One label per line",
    "一行一个标签，适合连着打多个料号": "Each line becomes its own label",

    # —— 预览
    "预览": "Preview",
    "✓ 内容在标签范围内": "✓ Content fits the label",

    # —— 附加内容
    "附加内容（可选）": "Extras (optional)",
    "二维码": "QR code",
    "填内容就生成，留空不打": "Filled in = printed",
    "大小": "Size",
    "条码": "Barcode",
    "留空不打": "Empty = none",
    "图片": "Image",
    "未选择": "None",
    "选择图片": "Choose",
    "粘贴": "Paste",
    "清除": "Clear",
    "排列": "Order",
    "附件在标签上的左右顺序": "Left-to-right order on the label",
    "图片·条码·二维码": "Image · Barcode · QR",
    "二维码·条码·图片": "QR · Barcode · Image",

    # —— 打印设置
    "打印设置": "Print settings",
    "标签长度": "Length",
    "字号": "Font size",
    "加粗": "Bold",
    "对齐": "Align",
    "左": "Left",
    "中": "Center",
    "右": "Right",
    "纸张类型": "Paper",
    "份数": "Copies",
    "浓度": "Density",
    "浅": "Light",
    "标准": "Standard",
    "深": "Dark",
    "最浅": "Lightest",
    "最深": "Darkest",
    "高级 / 校准": "Advanced",
    "打印方向": "Orientation",
    "字反了才需要改": "Only if the print is mirrored",
    "打印协议": "Protocol",
    "打空白才需要换": "Only if it prints blank",
    "正常": "Normal",
    "左右翻转": "Mirror H",
    "上下翻转": "Mirror V",
    "旋转 180°": "Rotate 180°",
    "自定义": "Custom",
    "间隙标签": "With gaps",
    "透明标签": "Transparent",
    "连续标签": "Continuous",

    # —— 动作
    "打  印": "PRINT",
    "取  消": "CANCEL",
    "诊断页": "Diagnose",
    "测试页": "Test page",
    "走纸": "Feed",
    "记录": "History",
    "就绪": "Ready",
    "日志": "Log",
    "打印完成": "Printed",
    "打印失败": "Failed",
    "已取消": "Cancelled",
    "正在取消…": "Cancelling…",
    "正在发送数据…": "Sending…",
    "知道了": "Got it",
    "关闭": "Close",
    "打印记录": "Print history",
    "所有文件": "All files",

    # —— 提示语（对话框 / 横幅）
    "请先扫描并选择一个设备": "Scan and pick a device first",
    "请先连接打印机": "Connect the printer first",
    "内容是空的，打出来会是空白标签。\n确定要打吗？": "The label is empty and will print blank.\nContinue?",
    "份数不合法": "Invalid copy count",
    "要连续打印 {n} 份，每份都得等打印机走完，耗时会长。\n确定继续吗？":
        "Printing {n} copies means waiting for each one.\nContinue?",
    "批量模式下每行打一张，但内容是空的。": "Batch mode prints one label per line, but the text is empty.",
    "正在打印，请等它结束再断开": "Printing in progress — cancel or wait before disconnecting",
    "剪贴板里没有图片。先截图或复制一张图片再试。": "No image in the clipboard. Copy or screenshot one first.",
    "剪贴板里是文件，但没有可用的图片文件。": "The clipboard holds files, but none are usable images.",
    "没有找到设备。请确认打印机已开机，并关闭手机上的 NIIMBOT App。":
        "No device found. Make sure the printer is on and the phone app is closed.",
    "内容是空的，先输入内容再存模板": "Enter some text before saving a template",
    "给这套内容起个名字（例如「CPU 长型号」）：": "Name this content (e.g. CPU long model):",
    "模板「{name}」已存在，覆盖吗？": "Template “{name}” exists. Overwrite?",
    "先在「模板」下拉框里选一个": "Pick a template from the list first",
    "删除模板「{name}」？": "Delete template “{name}”?",
    "还没有打印记录。\n每次打印成功后记一条，方便回看和找回内容。":
        "No print history yet.\nEach successful print is recorded so you can look it up later.",

    # —— 日志与状态（动态模板）
    "已恢复上次的设置与文字内容": "Restored last settings and text",
    "协议调用记录已开启，写入：{path}": "Protocol log started: {path}",
    "每次连接、发送命令、收到应答都会记录，出问题时把这个文件发给我。":
        "Every connect, command and reply is logged — send me this file if something breaks.",
    "正在扫描设备…": "Scanning devices…",
    "找到 {n} 个设备，在列表里选一台再点「连接」": "Found {n} device(s) — pick one, then Connect",
    "正在自动连接上次用的打印机…": "Reconnecting to the last printer…",
    "自动连接失败：{msg}（可以手动点「连接」重试）": "Auto-connect failed: {msg} — click Connect to retry",
    "连接成功：{info}": "Connected: {info}",
    "已按检测到的纸张设置标签长度 {h} mm（纸宽约 {w} mm，间隙 {g} mm）":
        "Label length set to {h} mm (paper {w} mm wide, gap {g} mm)",
    "未读到 RFID 纸张信息，请手动核对标签长度是否与纸卷一致":
        "No RFID paper info — please check the label length against the roll",
    "浓度范围 {lo}-{hi}（默认 {default}）": "Density range {lo}-{hi} (default {default})",
    "打印流程自动选择：{task}": "Protocol auto-selected: {task}",
    "打印流程沿用手动设置：{task}（型号建议 {want}）":
        "Keeping manual protocol {task} (model suggests {want})",
    "打印机连接已断开（蓝牙掉线、被关机或 USB 被拔）。重新连接后可继续打印。":
        "Printer disconnected (Bluetooth drop, power off, or USB unplugged). Reconnect to continue.",
    "已请求取消，等打印机收尾…": "Cancel requested, waiting for the printer…",
    "开始打印：{labels} 张 × {copies} 份，取向「{orientation}」":
        "Printing {labels} label(s) × {copies} copy(ies), orientation {orientation}",
    "打印已取消（可能已吐出一张不完整的标签）": "Print cancelled (a partial label may have come out)",
    "测试页已输出。三角形指向标签起点（先出纸的一端）。":
        "Test page printed. The triangle marks the label start.",
    "看结果：①整块全黑那块若是白的 → 纸装反了或纸型不对；②条纹正常但没字 → 告诉我；③全都不出 → 打印头或纸的问题。":
        "Check: 1) if the solid black block is blank, the paper is upside down; "
        "2) stripes OK but no text — tell me; 3) nothing at all — printhead or paper issue.",
    "诊断页已输出，请对照上面的说明看。": "Diagnostic page printed — compare with the notes above.",
    "走纸中…": "Feeding…",
    "走纸完成": "Feed done",
    "已套用模板「{name}」": "Applied template “{name}”",
    "已保存模板「{name}」": "Template “{name}” saved",
    "已删除模板「{name}」": "Template “{name}” deleted",
    "批量模式：{n} 行 → 会打 {n} 张（一行一张）": "Batch mode: {n} line(s) → {n} label(s)",
    "批量模式：一行一个标签，先输入内容": "Batch mode: one label per line — type something first",
    "已从剪贴板取到图片": "Image pasted from clipboard",
    "上次的窗口位置已不在当前屏幕上（可能拔掉了外接显示器），已重新居中":
        "Last window position is off-screen (external display removed?) — recentred",
}


# ---------------------------------------------------------------- 带参数的模板
_EN.update({
    "NIIMBOT 打印助手": "NIIMBOT Label Printer",
    "快捷键：Ctrl+回车 打印 · Ctrl+T 测试页 · Ctrl+D 诊断页 · Ctrl+F 走纸 · Ctrl+O 选图片 · F5 扫描 · Esc 取消":
        "Shortcuts: Ctrl+Enter print · Ctrl+T test · Ctrl+D diagnose · "
        "Ctrl+F feed · Ctrl+O image · F5 scan · Esc cancel",
    "界面语言已切换：{lang}": "Interface language switched to {lang}",
    "浓度 {v}": "Level {v}",
    "预览第 1 张 / 共 {total} 张 · ": "Preview 1 of {total} · ",
    "字号 {n} 太大——标签宽度只有约 {max} 点，已按 {max} 排版":
        "Size {n} is too large — the label is only ~{max}px tall, using {max}",
    "{n} 行放不下（字号 {size} 最多 {max} 行）——字号已自动缩到 {new}，或请减少行数":
        "{n} lines don't fit at {size}px (max {max}) — shrunk to {new}, or drop a line",
    "第 {i} 行「{text}…」过长约 {over} 点，会顶出标签，请缩短或改小字号":
        "Line {i} “{text}…” is ~{over}px too long — shorten it or use a smaller size",
    "文字超出标签长度，请换更长的标签或缩小字号":
        "Text exceeds the label length — use a longer label or smaller font",
    "内容为空，打出来会是一张空白标签": "Nothing to print — this label would be blank",
    "{mm} mm 标签 · 字号 {size} · 每行约 {chars} 字 · {n}/{max} 行":
        "{mm} mm label · {size}px · ~{chars} chars/line · {n}/{max} lines",
    "二维码生成失败：{err}": "QR generation failed: {err}",
    "条码生成失败{hint}：{err}": "Barcode generation failed{hint}: {err}",
    "图片文件不存在：{path}": "Image not found: {path}",
    "图片打不开：{err}": "Cannot open image: {err}",
})

# 带前缀的错误行（原文里是 f"xx失败：{err}" 拼接）
_PREFIX = {
    "连接成功：": "Connected: ",
    "正在连接 ": "Connecting to ",
    "连接失败：": "Connect failed: ",
    "扫描失败：": "Scan failed: ",
    "打印失败：": "Print failed: ",
    "测试页失败：": "Test page failed: ",
    "诊断页失败：": "Diagnostic failed: ",
    "走纸失败：": "Feed failed: ",
    "断开时出错：": "Disconnect error: ",
    "读取剪贴板失败：": "Clipboard read failed: ",
    "剪贴板图片保存失败：": "Clipboard image save failed: ",
}


def set_lang(lang: str) -> None:
    global _lang
    _lang = "en" if str(lang).lower().startswith("en") else "zh"


def get_lang() -> str:
    return _lang


def is_en() -> bool:
    return _lang == "en"


def tr(text: str) -> str:
    """中文原文 → 当前语言；查不到就原样返回。"""
    if _lang != "en" or not isinstance(text, str):
        return text
    hit = _EN.get(text)
    if hit is not None:
        return hit
    for zh, en in _PREFIX.items():
        if text.startswith(zh):
            return en + text[len(zh):]
    return text


def fmt(template: str, **kw) -> str:
    """先翻译模板，再填充占位符。"""
    try:
        return tr(template).format(**kw)
    except (KeyError, IndexError, ValueError):
        return template


def untr(display: str) -> str:
    """把界面上的（可能是英文的）选项还原成内部使用的中文值。

    这里**不能**加「只在英文模式下才反查」的判断：控件里可能残留着上次
    英文界面写入的英文值，而此时语言已经切回中文。那样会把英文原样存进
    打印设置.json，下次启动匹配不到中文选项，设置就悄悄丢了。
    """
    if not isinstance(display, str):
        return display
    for zh, en in _EN.items():
        if en == display:
            return zh
    return display
