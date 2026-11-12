# 界面字体：来源、版权与使用范围

`assets/fonts/` 下的 `.otf` 是 **Nothing** 的官方品牌字体，用于本工具的界面显示。

| 文件 | 字体 | 用在哪 |
| --- | --- | --- |
| `NDot-55.otf` | NDot 55 | 点阵显示字体，顶栏的 NIIMBOT 字标 |
| `NType82-Regular.otf` | NType 82 | 打字机风格正文与卡片标题 |
| `NType82-Headline.otf` | NType 82 Headline | 大号标题 |
| `NType82Mono-Regular.otf` | NType 82 Mono | 等宽数字与技术标签 |

来源：<https://nothing.wiki/download/>（Nothing 品牌规范页的下载入口）

## 版权

**版权归 Nothing Technology Limited 所有。**

本仓库包含这些文件，只是为了让克隆下来的人能直接看到完整的界面效果，
**不构成对这些字体的任何授权、转让或再许可**。

## 可以做什么，不可以做什么

| | |
| --- | --- |
| ✅ 随本工具运行、在界面中显示 | 可以 |
| ❌ 单独提取出来安装到系统 | 不可以 |
| ❌ 用于其他项目、商业设计、宣传物料 | 不可以 |
| ❌ 再分发 `.otf` 文件本身 | 不可以 |

如果权利人认为此处包含不妥，联系后会立即移除。
移除后程序会自动退回 `Consolas` / `微软雅黑`，功能完全不受影响。

## 程序如何处理字体

`fonts.py` 用 `AddFontResourceExW` 配合 `FR_PRIVATE` 注册字体，
**只对当前进程生效**：不写注册表、不装进系统、关掉程序就失效。

字体缺失时不报错，`Theme` 会按顺序回退：

- 显示字体（顶栏字标）：`Ndot 55` → `Ndot` → `Consolas` → `Courier New`
- 等宽（数字、技术标签）：`NType 82 Mono` → `Consolas` → `Courier New`
- 正文中文：`Microsoft YaHei UI` → `Microsoft YaHei` → `Segoe UI`

所以即使把整个 `assets/fonts/` 目录删掉，程序依然能正常启动和打印，
只是界面变成普通字体的样子。
