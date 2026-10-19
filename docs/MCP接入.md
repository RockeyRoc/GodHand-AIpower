# GodHand 统一 MCP 接入

所有 Agent 共用一个 Streamable HTTP 地址：`http://127.0.0.1:18765/mcp`，只注册一个 MCP 工具 **godhand**。各模块及外部工具均通过这个入口调用，共用目标配置、调度器、任务数据库及日志。GUI 本机管理通道独立鉴权，不作为 Agent 接口。

1. 运行 GodHand.exe，或无界面启动 `GodHand.exe --mcp`。源码入口为 `python main_work.py`。
2. 在“机子管理”保存比赛主机；在“AI 中枢”配置允许目标、能力和提交平台 URL。修改前先停止服务，保存后启动。
3. 复制凭据，在启动 Agent 的 PowerShell 中设置 `$env:GODHAND_MCP_TOKEN = '复制的凭据'`。示例配置只引用环境变量；关闭终端后需重新设置。
4. 所有客户端登记同一个 godhand 服务，无需为各模块再配置服务。

Claude Code `.mcp.json`（也可通过客户端用户级 MCP 配置供所有项目使用）：

```json
{"mcpServers":{"godhand":{"type":"http","url":"http://127.0.0.1:18765/mcp","headers":{"Authorization":"Bearer ${GODHAND_MCP_TOKEN}"}}}}
```

启动后用 `/mcp` 或 `claude mcp list` 检查连接，按客户端提示启用。GodHand 的导出按钮只生成示例。

Codex `config.toml`：

```toml
[mcp_servers.godhand]
url = "http://127.0.0.1:18765/mcp"
bearer_token_env_var = "GODHAND_MCP_TOKEN"
```

唯一工具的输入为 `{action, arguments}`，输出为 `{action, result}`。arguments 根据动作填写；不支持的动作、缺失字段、额外字段及错误类型返回工具错误。

| action | arguments | 用途 |
|---|---|---|
| health | 无 | 服务、统一工具名、能力数量 |
| targets | 无 | 已保存目标标识，凭据脱敏 |
| capabilities | 无 | 全部业务操作、字段及默认值 |
| tools | 无 | 全部工具、路径及缺失依赖 |
| run | operation、target、parameters、timeout、idempotency_key | 提交业务；后三项可省略 |
| run_tool | tool、target、parameters、idempotency_key | 启动工具；后三项可省略，默认 local |
| task | task_id | 持久化状态和结果 |
| tasks | limit（可省略） | 最近任务 |
| logs | task_id、after、limit | 脱敏事件；后两项可省略 |
| cancel | task_id | 停止任务或持续操作 |
| echo | message（可省略） | 本机连通性检查 |

先用 godhand 调用 `{"action":"echo","arguments":{"message":"GodHand ready"}}`，再查询 capabilities、targets、tools。

运行脚本：

```json
{"action":"run_tool","arguments":{"tool":"python_script","target":"local","parameters":{"file":"C:/比赛 工具/check.py","args":["--label","有空格的名称"]}}}
```

本地源码审计：

```json
{"action":"run","arguments":{"operation":"ops_audit_local","target":"local","parameters":{"directory":"C:/比赛/site"},"timeout":60}}
```

长任务立即返回 result.id。用 `{"action":"task","arguments":{"task_id":"返回的 id"}}` 查询，logs 读取事件，cancel 停止。停止后确认终态与清理记录。断连不自动取消任务，重连仍可查同一 id；同一幂等标识重试不会重复创建任务，参数必须一致。支持资源读取的客户端也可读取 `godhand://tasks/{id}`。

升级后重连以刷新工具列表。旧版 godhand_echo、godhand_run 等独立工具已合并，改用统一 godhand 的对应 action。

默认监听本机回环地址。全局调用表示各 Agent 共用入口和全部已启用模块；目标与能力范围仍由本机配置。切换界面目标不改变已提交任务的连接快照。

同一数据目录只有一个服务；GUI 附着独立无界面服务后，关窗口保留该服务。由窗口启动的服务随窗口结束。管理数据默认在 管理工具/GodHand，--data-dir 可隔离比赛。凭据与请求使用当前 Windows 用户 DPAPI 加密。

重启后只读中断任务为 interrupted，远程修改任务为 needs_review，应核对靶机再重提。平台的 accepted、rejected、duplicate、rate_limited、unknown 分开记录，未知结果先查平台。端口冲突会明确报错，修改后同步客户端配置。轮换凭据后重设环境变量并重连。
