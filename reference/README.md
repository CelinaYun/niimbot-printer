# 参考源码（reference）

这里的 `.ts` 文件**不参与本项目运行**。它们是当初逆向 NIIMBOT 打印机协议时用来对照的上游
TypeScript 源码片段，保留下来是为了日后查阅命令号、封包字段和型号映射，避免再去翻网页。

| 文件 | 上游位置 | 在本项目里对应什么 |
| --- | --- | --- |
| `enumerations.ts` | `src/packets/enumerations.ts` | 命令号、纸张类型、PrinterInfo 查询类型的取值 → `niimbot/packet.py` 的常量表 |
| `packet_parser.ts` | `src/packets/packet_parser.ts` | 各命令应答的字段解析 → `niimbot/packet.py` 的分包重组、`niimbot/client.py` 的读取逻辑 |
| `printer_models.ts` | `src/printers/printer_models.ts` | 型号库与打印能力表 → `niimbot/client.py` 的 `_MODEL_TASK` 映射 |

## 来源与许可

- 上游仓库：[MultiMote/niimbluelib](https://github.com/MultiMote/niimbluelib)（NIIMBOT 协议目前最完整的开源实现）
- 许可证：**MIT**，Copyright (c) 2024 MultiMote，全文见同目录 `LICENSE-niimbluelib`

按 MIT 条款，再分发这些文件时须保留上述版权声明与许可声明。

## 使用注意

本项目 Python 侧是按协议**重新实现**的，并非这些 TypeScript 文件的逐行翻译，两边结构不一一对应。
改动 `niimbot/` 里的协议逻辑时，不需要同步修改这里的文件——它们只作为事实核对用的参照物。
