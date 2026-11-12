"""GUI 冒烟测试：启动界面、刷新预览、按窗口句柄截图、退出。不连接打印机。"""

import ctypes
import sys
import time
import tkinter as tk
from ctypes import wintypes

from PIL import Image


def grab_window(hwnd: int) -> Image.Image:
    """用 PrintWindow 抓指定窗口，不依赖屏幕坐标与遮挡。"""
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError("GetWindowRect failed")
    w = rect.right - rect.left
    h = rect.bottom - rect.top

    hwnd_dc = user32.GetWindowDC(hwnd)
    mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
    bmp = gdi32.CreateCompatibleBitmap(hwnd_dc, w, h)
    gdi32.SelectObject(mem_dc, bmp)
    ctypes.windll.user32.PrintWindow(hwnd, mem_dc, 3)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
        ]

    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = w
    bmi.biHeight = -h
    bmi.biPlanes = 1
    bmi.biBitCount = 32

    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem_dc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(hwnd, hwnd_dc)
    return Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)


def main() -> int:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:  # noqa: BLE001
        pass

    import gui as gui_mod

    root = tk.Tk()
    app = gui_mod.App(root)
    root.lift()
    root.update_idletasks()
    root.update()
    time.sleep(0.5)
    root.update()

    # 改文字应触发预览刷新
    app.text_box.delete("1.0", "end")
    app.text_box.insert("1.0", "横版测试 ABC 123")
    app._render_preview()
    root.update()
    time.sleep(0.3)
    root.update()

    hwnd = int(root.winfo_id())
    hwnd = ctypes.windll.user32.GetParent(hwnd) or hwnd
    img = grab_window(hwnd)
    img.save("_gui_win.png")
    print("screenshot ok", img.size)

    probe = img.convert("RGB")
    w, h = probe.size
    for name, (x, y) in {
        "topbar": (int(w * 0.5), int(h * 0.03)),
        "left panel": (int(w * 0.30), int(h * 0.55)),
        "right panel": (int(w * 0.85), int(h * 0.55)),
    }.items():
        print(f"  {name:12s} {probe.getpixel((x, y))}")

    print("preview image:", app.preview_image.size if app.preview_image else None)
    app.portrait_var.set("横版")   # 断言按横版写，先固定住，避免受用户设置影响
    app._render_preview()
    assert app.preview_image is not None, "预览图应已生成"
    # 横版：宽 = 标签长度，高 = 打印头 96 点
    assert app.preview_image.height == 96, app.preview_image.size
    assert app.preview_image.width > 100, app.preview_image.size

    root.destroy()
    print("gui smoke ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
