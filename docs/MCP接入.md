# GodHand 1.6 MCP 接入

GodHand 为全部业务提供一个 MCP 工具 godhand。Codex 默认通过本机 stdio 进程接入；通用 HTTP 客户端使用固定地址 http://127.0.0.1:18765/mcp。两种方式共用同一机子库、任务和日志。

## Codex 桌面 App：推荐接入方式

1. 打开 GodHand 的“AI 中枢”，选择允许目标并保存配置。
2. 在“Codex 接入”选择“用户级（所有项目）”或“项目级（GodHand 目录）”。
3. 点击“写入 Codex 配置”。完成提示显示文件位置与备份位置。
4. 打开 Codex 的 MCP 设置，重启 godhand，或重新打开 Codex；确认显示已连接。
5. 让 AI 执行 echo、health 和 targets，确认能够查询版本与目标。

默认配置使用 stdio，包含当前安装路径和数据目录，预览可直接复制。运行便携版时，command 指向其 GodHand.exe；源码模式使用当前 Python 与 main_work.py。无需设置 GODHAND_MCP_TOKEN。

    [mcp_servers.godhand]
    command = "C:/CTF/GodHand/GodHand.exe"
    args = ["--mcp-stdio", "--data-dir", "C:/CTF/GodHand/管理工具/GodHand"]
    startup_timeout_sec = 30

路径替换为实际安装位置。移动 GodHand 后重新生成配置。用户级默认使用 CODEX_HOME/config.toml；未设置 CODEX_HOME 时使用用户目录下 .codex/config.toml。项目级使用当前 GodHand 目录下 .codex/config.toml，需在 Codex 中信任该项目。

写入前备份原文件，仅替换 mcp_servers.godhand 表及其子表，并校验 TOML 和其他配置内容。配置不包含连接凭据。命令行写入方式：

    GodHand.exe --install-codex-config user
    GodHand.exe --install-codex-config project

需要 HTTP 配置时附加 --codex-transport http。配置字段与 stdio 支持见[官方 MCP 文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)。

## 通用 HTTP 客户端

在“AI 中枢 → 高级设置”点击“导出通用配置”，生成 Codex stdio、Claude Code HTTP 和通用 HTTP 示例。HTTP 地址固定为：

    http://127.0.0.1:18765/mcp

点击“复制 HTTP 凭据命令”，在用于启动客户端的 PowerShell 中粘贴执行，再从该终端启动客户端。命令设置当前终端的 GODHAND_MCP_TOKEN，配置通过 Bearer 使用该值。

    [mcp_servers.godhand]
    url = "http://127.0.0.1:18765/mcp"
    bearer_token_env_var = "GODHAND_MCP_TOKEN"

该临时变量不会自动进入已经打开的桌面 App。Codex 桌面用户优先使用上方 stdio 接入。轮换 HTTP 凭据后，重新复制命令并连接客户端。

## 选择目标与应用设置

在“机子管理”添加记录；批量导入见[靶机导入](靶机导入.md)。在 AI 中枢按“停止服务 → 选择允许目标 → 保存配置 → 启动服务”应用范围，页面显示已选数量。

高级设置中的“启用能力”填写能力标识，* 表示全部。“提交平台 URL”填写本场比赛需要的地址。“导入后自动授权新记录”默认关闭；开启后应用于后续新导入项。目标凭据由本机保存，任务使用明确的目标 ID。

## 统一接口

输入为 action 和 arguments，输出为 action 和 result。

| action | 参数概要 | 用途 |
| --- | --- | --- |
| echo / health | echo 可传 message | 检查连接、版本和后台 |
| targets | 无 | 查询目标和 allowed 状态 |
| targets_import | records、on_duplicate | 录入目标 |
| targets_update | id、changes、expected_revision | 更新明确字段 |
| capabilities | 可指定 operation | 查询业务操作与参数 |
| tools | 无 | 查询工具和依赖 |
| run | operation、target、parameters、timeout、idempotency_key | 提交业务任务 |
| run_tool | tool、target、parameters、idempotency_key | 启动工具 |
| task / cancel | task_id | 查询或停止任务 |
| tasks | 可传 limit | 查询最近任务 |
| logs | task_id、after、limit | 读取事件 |

capabilities 返回具体字段及默认值，提交前按需查询。run 与 run_tool 返回 result.id；后续以 task_id 查询。logs 可用上次事件的 seq 作为 after。重试相同请求时，可复用相同 idempotency_key。

    {"action":"echo","arguments":{"message":"GodHand ready"}}
    {"action":"capabilities","arguments":{"operation":"ops_audit_local"}}
    {"action":"run","arguments":{"operation":"ops_audit_local","target":"local","parameters":{"directory":"C:/CTF/site"},"timeout":60}}
    {"action":"task","arguments":{"task_id":"返回的ID"}}

## 服务与比赛数据

默认数据目录为 管理工具/GodHand。GUI、HTTP 和 stdio 使用同一目录时共享任务。固定端口同时运行一套后台；切换比赛时先停止原后台，再使用新的 --data-dir。

stdio 附着已有后台时，桥接会话结束后保留后台；没有后台时由桥接启动，其会话退出时结束自己启动的后台。由 GUI 启动的服务随窗口关闭；附着独立服务时，关闭窗口保留后台。

needs_review 表示远程修改结果需核对；interrupted 为中断的只读任务。Flag 提交 unknown 时先查询平台记录。

## 排查连接

| 现象 | 处理 |
| --- | --- |
| Codex 尚未出现 godhand | 检查配置位置，重启该 MCP 连接或重新打开 Codex |
| 程序移动后启动失败 | 在新位置重新写入配置 |
| 端口 18765 被占用 | 停止占用程序，或使用正在运行 GodHand 的同一数据目录 |
| 服务凭据不匹配 | 核对当前比赛目录；HTTP 客户端再检查令牌是否已轮换 |
| 后台仍是旧版本 | 停止旧后台后启动 1.6 |
| 检测到旧自定义端口服务 | 先停止旧服务；再次启动时自动备份配置并迁移到 18765 |
| 自检通过但 AI 尚未连接 | 自检验证后台与 MCP 协议；到 Codex 设置查看客户端连接状态 |

“连接自检”逐项检查后台、鉴权、协议初始化、工具发现和 echo。实际业务结果通过 task、logs 及对应目标检查确认。
