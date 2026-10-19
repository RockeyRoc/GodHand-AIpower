# GodHand — 工具管理与 MCP AI 中枢

GodHand 提供 Tk/ttkbootstrap 图形界面、工具管理和 MCP AI 中枢。所有外部 Agent 共用一个 Streamable HTTP MCP 地址，并通过唯一工具 `godhand(action, arguments)` 调用全部业务与工具；应用内提供配置和状态。

启动：`python main_work.py`，兼容入口 `python main.py`。无界面服务：`python main_work.py --mcp`。便携版运行 `GodHand.exe`，保留同目录 `_runtime`。

- 工具管理：原有能力清单、Tabby/WinSCP、自定义 Windows 程序及 Python/PHP/PowerShell 脚本、依赖、任务和日志。
- AI 中枢：服务启停、目标范围、凭据、健康检查和 Claude Code/Codex 配置导出。
- main_work.py：统一表单与业务源；main.py：兼容转发。
- AI中枢：MCP、任务服务、Worker、生成的无 Tk 业务实现。
- 工具管理模块：登记、依赖与 argv 执行。
- 攻击模块 / 防御模块 / 上传靶机 / 管理工具：沿用导入项目布局。

项目来源与作者归属见 [来源与鸣谢](docs/来源与鸣谢.md)。

参阅 [接入说明](docs/MCP接入.md)、[工具说明](docs/工具接入.md)、[开发与发布](docs/开发与发布.md)、[能力覆盖表](docs/能力覆盖表.md)、[最新验收](docs/统一入口验收.md)、[历史验收记录](docs/验收记录.md)。

构建：`python build_work.py --check` 预检；`python build_work.py --output 新目录 --cache GodHand-tmp/build --tools-root 外部工具目录` 生成便携包与 ZIP。构建不会打包个人配置、任务库、密钥和旧日志。
