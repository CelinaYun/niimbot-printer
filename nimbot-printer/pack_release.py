"""把 dist 里打好的程序目录打包成可直接发给别人的 zip。

用法：
    1. 先打包程序本体
       .venv\\Scripts\\python.exe -m PyInstaller --noconfirm --clean --onedir ^
           --windowed --name "NIIMBOT打印助手" --add-data "assets;assets" ^
           --collect-all bleak main.py
    2. 再生成 zip
       .venv\\Scripts\\python.exe pack_release.py

产物：上一级目录下的 NIIMBOT打印助手-v<版本>.zip
"""

from __future__ import annotations

import os
import shutil
import zipfile

from version import __version__

APP_DIR_NAME = "NIIMBOT打印助手"
SRC = os.path.abspath(os.path.join("dist", APP_DIR_NAME))
STAGE_ROOT = os.path.abspath("_stage")
STAGE = os.path.join(STAGE_ROOT, f"{APP_DIR_NAME}-v{__version__}")
OUT_ZIP = os.path.abspath(f"../{APP_DIR_NAME}-v{__version__}.zip")

README = f"""NIIMBOT 标签打印助手 v{__version__}

【怎么启动】
1. 把整个文件夹解压到任意位置（别只把 exe 拖出来，依赖在同级的 _internal 里）
2. 双击「NIIMBOT打印助手.exe」
3. 启动约 1 秒

【怎么打印】
1. 打印机用 USB 线插电脑，或打开蓝牙
2. 「打印机连接」选「USB 串口」→ 扫描 → 选中 COM 口 → 连接
3. 左边输入文字，每行一句；右边调字号和标签长度
4. 先点「测试页」确认方向和浓度对，再点「打印」

【最常踩的几个坑】
· 标签长度必须和纸卷一致。12mm 宽的纸通常 30mm 长，
  设长了会留白，设短了会切断内容。
· 蓝牙连不上：先关掉手机上的 NIIMBOT App，它会占着打印机。
· 打出来空白或字很淡：换「打印协议」或调高「浓度」。
· 字是反的：在「高级 / 校准」里改「打印方向」。
· 想竖着贴：把「版式」切成「竖版」，同样一张纸能写 7~8 行短文字。

【出问题怎么办】
程序会在 %LOCALAPPDATA%\\Niimbot打印助手\\ 里写日志。
把当天那个「打印记录-年月日.log」发给制作者，里面每一步都有记录。

【系统要求】
Windows 10 / 11（64 位）。不需要安装 Python 或任何运行库。
"""

FONT_NOTE = """关于界面字体

本程序界面使用了 Nothing 官方的 Ndot 55、NType 82、NType 82 Mono 字体，
版权归 Nothing Technology Limited 所有，仅作为界面显示随本程序使用，
不得单独提取、再分发或用于其他项目。

字体来源：https://nothing.wiki/
"""


def main() -> None:
    if not os.path.isdir(SRC):
        raise SystemExit(f"找不到打包好的程序目录：{SRC}\n请先执行 PyInstaller 打包。")

    if os.path.isdir(STAGE_ROOT):
        shutil.rmtree(STAGE_ROOT, ignore_errors=True)
    shutil.copytree(SRC, STAGE)

    with open(os.path.join(STAGE, "先看我.txt"), "w", encoding="utf-8-sig") as f:
        f.write(README)
    with open(os.path.join(STAGE, "字体说明.txt"), "w", encoding="utf-8-sig") as f:
        f.write(FONT_NOTE)

    if os.path.exists(OUT_ZIP):
        os.remove(OUT_ZIP)

    count = 0
    with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, _, files in os.walk(STAGE):
            for name in files:
                full = os.path.join(root, name)
                rel = os.path.relpath(full, STAGE)
                z.write(full, os.path.join(os.path.basename(STAGE), rel))
                count += 1

    shutil.rmtree(STAGE_ROOT, ignore_errors=True)

    size_mb = os.path.getsize(OUT_ZIP) / 1024 / 1024
    print(f"打包完成：{count} 个文件，{size_mb:.1f} MB")
    print(f"输出：{OUT_ZIP}")


if __name__ == "__main__":
    main()
