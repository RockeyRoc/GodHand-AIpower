<div align="center">

<img src="logo.png" alt="GodHand Logo" width="128">

# GodHand AIpower

**面向 AWD / CTF 的图形化工具管理与 MCP AI 中枢**

![Version](https://img.shields.io/badge/version-1.5.0-00b894)
![Platform](https://img.shields.io/badge/platform-Windows-0078D4)
![MCP](https://img.shields.io/badge/MCP-Streamable_HTTP-7c3aed)

[快速开始](#快速开始) · [AI 接入](#ai-接入) · [靶机导入](#靶机导入) · [文档](#文档) · [来源与鸣谢](#来源与鸣谢)

</div>

## 简介

GodHand 基于 [Wwlsyl / YL_Phantom](https://github.com/Wwlsyl/YL_Phantom) 导入工程开发，将原有 AWD 业务整合到同一界面，并增加外部工具登记、持久化任务管理和 MCP 服务。用户可以在图形界面操作，也可以让支持 MCP 的 AI 客户端通过统一入口调用已启用的能力。

**一个服务地址、一个 MCP 工具：** 所有客户端共用 `http://127.0.0.1:18765/mcp`，以 `godhand(action, arguments)` 查询能力、提交任务、查看结果和停止任务。目标连接资料由本机保存，允许范围在 AI 中枢中配置。

本项目用于已获授权的 AWD / CTF 竞赛及自建靶场。保留原项目作者归属，具体见[来源与鸣谢](#来源与鸣谢)。

## 功能概览

| 模块 | 主要功能 |
| --- | --- |
| 总览与机子管理 | 主机记录、连接信息和运行状态概览 |
| WAF 管理 | 部署、检查、更新、规则编辑、流量查看与心跳 |
| Flag 获取 | 通道检查、轮次获取、平台提交及历史记录 |
| 靶机监控 | 监控日志、攻击排行、告警及远程文件管理 |
| 通信与部署 | 原项目通信组件、RSA 通信、部署与状态检查 |
| 综合运维 | 备份还原、源码检查、数据库、加固、IP 管理、定时备份、主机发现、SSH、进程、PHP 信息及防御中心 |
| 工具管理 | 业务能力检索、Tabby / WinSCP、自定义程序与 Python / PHP / PowerShell 脚本、依赖检查 |
| AI 中枢 | MCP 服务、客户端配置导出、目标与能力范围、凭据、健康检查 |
| 任务管理 | 后台执行、状态与日志、产物、超时、取消、幂等提交及重启恢复 |

当前业务目录登记 **102 个业务动作**，另提供 **5 个内置工具入口**；具体参数通过 `capabilities` 和 `tools` 查询。接口登记与各场景的验证情况见能力覆盖表。

### 1.5.0 更新

- 防御中心只做 AoiAWD 探针部署、环境检查、监测页连接与卸载恢复；旧防御动作保留 MCP 接口。
- AoiAWD Core、Web 和 MongoDB 随 GodHand 配套提供，在 Windows 原生运行，不依赖 Docker 或 WSL。
- Linux 靶机部署 RoundWorm 文件与进程探针、PHP Web 探针和可选 Guardian PWN 探针；原 ELF 保留，另生成 `.guardianed` 程序。
- 每个目标使用独立监测页面及数据命名空间。回传通过现有 SSH 连接，靶机只监听回环端口。
- PHP 部署先备份并逐文件检查语法；卸载恢复部署内容，保留期间的其他编辑。断线时保持原业务响应。
- 新增 [skill.md](skill.md)。接入、开发、能力与验收 Word 文档放在 `docx/` 文件夹；仅该文件夹排除 Git 提交和便携打包，原有 `docs/` 文档继续保留。
- 上游原始源码、实际修改后的对应源码及许可随 AoiAWD 配套环境提供；来源见[第三方来源](防御模块/SOURCES.md)。

### 1.4.0 更新

- 综合运维新增“防御中心”，GUI 与 MCP 共用同一任务服务和能力目录。
- 通过 SSH 检查目标已有 Fail2Ban、Watchbird、Lynis、YARA、AIDE；目标缺少程序时报告原因，不自动安装。
- 提供 Fail2Ban Jail 状态、白名单策略、封禁/解封、备份和恢复；首次接入默认只检查，启用策略需要单独提交。
- 提供 Watchbird 状态、日志与样本统计、单项防护开关、配置恢复，以及校验后切换现有 PHP WAF。
- 提供 Lynis 主机审计、指定规则/目录的 YARA 扫描、AIDE 基线初始化/检查/显式更新和三种证据收集流程。
- 任务结果包含结构化发现和脱敏证据，可导出 JSON/Markdown 报告；新增能力通过既有唯一入口 `godhand(action, arguments)` 使用。
- 便携包只包含自编写适配器，不内置上游防御程序。来源、许可证及验证范围见[来源与许可证](防御模块/SOURCES.md)和[1.4.0 验收记录](docs/1.4.0-实现与验收.md)。

### 1.3.0 更新

- 新增 stdio 传输通道：`GodHand.exe --mcp-stdio`，与 HTTP 共用同一目标库、调度器和任务数据库，只支持 stdio 的客户端可直接接入。
- AI 中枢新增“写入 Codex 配置”，可把 `mcp_servers.godhand` 写入用户级或项目级 `config.toml`；只替换该表，先备份原文件并校验 TOML。
- 新增“导入后自动授权”本机开关，默认关闭；开启时需二次确认，关闭时导入项仍按 ID 手动选择。
- 统一版本号到 `AI中枢/version.py`，EXE 资源、MCP 服务信息和发布清单均由该常量生成。
- 重建 1.3.0 核心 EXE 与便携 ZIP。

### 1.2.0 更新

- 新增 `targets_import`，通过唯一 MCP 工具单条或批量录入靶机。
- 新增本机目标选择窗口，支持搜索、刷新及批量选择筛选结果。
- 导入记录在当前比赛数据目录中加密保存，重复连接默认跳过并保留原凭据。
- MCP 导入项按目标 ID 授权，导入后需在 AI 中枢中选择、保存并重启服务。

## 快速开始

### 源码运行

当前开发与检查环境为 **Windows、Python 3.14**。凭据保护使用 Windows DPAPI，进程管理使用 Windows Job；源码运行需要带 Tkinter 的 Python。

在 PowerShell 中执行：

```powershell
git clone https://github.com/RockeyRoc/GodHand-AIpower.git
cd GodHand-AIpower
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe main_work.py
```

`main.py` 是兼容入口，也可使用 `python main.py`。GitHub 仓库提供源码、模板和 skill.md；接入、开发、能力与验收文档仅在本地保存；`GodHand.exe`、`_runtime`、本机安装的第三方工具与 Python 依赖不随源码提交，克隆后需要安装依赖并配置所需工具。

### 首次配置

1. 在“机子管理”保存靶机连接记录，或通过 MCP 导入靶机。
2. 在“工具管理 → 工具路径”配置 Python、PHP、PowerShell、phpggc 等所需程序。
3. 在“AI 中枢”配置允许目标、启用能力和提交平台地址；修改配置前停止服务，保存后启动。
4. 复制 MCP 凭据，配置 AI 客户端；先用 `echo` 检查连接，再查询目标、能力和工具。

Tabby、WinSCP 需要单独提供程序文件。PHP 工具需要 PHP CLI；phpggc 还需要对应脚本。缺失依赖会在工具页显示，程序不会在比赛期间自动下载。详见工具接入。

### 无界面服务与诊断

以下命令在已安装依赖的 Python 环境中执行：

```powershell
# 仅启动 MCP 服务，使用独立比赛数据目录
python main_work.py --mcp --data-dir ./GodHand-test-data/match-a

# 检查版本、能力与工具依赖
python main_work.py --doctor --data-dir ./GodHand-test-data/match-a
```

使用已构建的便携包时运行 `GodHand.exe`；无界面启动为 `GodHand.exe --mcp`。请保留与 EXE 配套的 `_runtime` 目录。源码更新与便携程序构建是独立步骤，现有 EXE 的版本以其实际构建结果为准。

## AI 接入

默认服务地址为 `http://127.0.0.1:18765/mcp`，使用 Bearer 凭据。先在启动客户端的 PowerShell 中设置环境变量，再从该终端启动客户端：

```powershell
$env:GODHAND_MCP_TOKEN = '替换为 AI 中枢显示的凭据'
```

### Claude Code

项目 `.mcp.json` 示例：

```json
{
  "mcpServers": {
    "godhand": {
      "type": "http",
      "url": "http://127.0.0.1:18765/mcp",
      "headers": {"Authorization": "Bearer ${GODHAND_MCP_TOKEN}"}
    }
  }
}
```

### Codex

可在“AI 中枢 → 写入 Codex 配置”中选择用户级或项目级配置；写入前备份原文件，只更新 `mcp_servers.godhand` 表。手动配置示例：

客户端 `config.toml` 示例：

```toml
[mcp_servers.godhand]
url = "http://127.0.0.1:18765/mcp"
bearer_token_env_var = "GODHAND_MCP_TOKEN"
```

### stdio 客户端

需要由客户端启动本机进程时，可选择 stdio 传输。以下为便携程序配置示例，路径替换为实际安装位置：

```toml
[mcp_servers.godhand]
command = "C:/CTF/GodHand/GodHand.exe"
args = ["--mcp-stdio", "--data-dir", "C:/CTF/GodHand-data/match-a"]
```

源码模式可用 Python 可执行文件作为 `command`，将 `main_work.py` 的绝对路径放在 `args` 首项。HTTP 与 stdio 配置二选一；两种通道共用本机服务和任务库。stdio 启动的服务随该会话结束，需长期运行时先独立启动 `--mcp`。

应用内可导出示例配置；修改地址、轮换凭据或更新服务后，重新连接客户端以刷新工具列表。完整说明见MCP 接入。

### 统一调用方式

调用 MCP 工具 `godhand` 时，输入为 `{action, arguments}`，输出为 `{action, result}`：

```json
{"action":"echo","arguments":{"message":"GodHand ready"}}
```

| action | 用途 |
| --- | --- |
| `health` | 查看版本、服务状态和能力数量 |
| `targets` / `targets_import` | 查询目标及允许状态 / 导入目标 |
| `targets_update` | 按稳定 ID 显式更新字段，可用记录版本避免过期覆盖 |
| `capabilities` / `tools` | 查询业务参数 / 工具和依赖 |
| `run` / `run_tool` | 提交业务任务 / 启动工具或脚本 |
| `task` / `tasks` | 查询单个任务 / 最近任务 |
| `logs` / `cancel` | 读取任务事件 / 停止任务 |
| `echo` | 检查客户端与服务连通性 |

例如，对本机目录执行源码检查：

```json
{
  "action": "run",
  "arguments": {
    "operation": "ops_audit_local",
    "target": "local",
    "parameters": {"directory": "C:/CTF/site"},
    "timeout": 60
  }
}
```

长任务返回 `result.id`；后续用 `task`、`logs`、`cancel` 的 `arguments.task_id` 指定该 ID。客户端断连后仍可查询同一任务；相同幂等标识及相同参数重试不会重复创建任务。

## 靶机导入

单条与批量导入均使用 `records` 数组。以下为文档示例地址，使用时替换为比赛靶机：

```json
{
  "action": "targets_import",
  "arguments": {
    "records": [
      {"name":"team1","ip":"192.0.2.10","port":80,"ssh_port":22,"ssh_user":"ctf"},
      {"name":"team2","ip":"192.0.2.11","port":8080,"ssh_port":2202,"ssh_user":"ctf"}
    ],
    "on_duplicate": "skip"
  }
}
```

每次支持 **1–500 条**、最多 **1 MiB**，每个比赛数据目录最多保存 **5000 条** MCP 靶机。`ip` 为必填项，支持单个 IPv4、IPv6 或域名；默认 Web 端口 `80`、SSH 端口 `22`、SSH 用户 `ctf`。

导入后，在 **AI 中枢 → 选择允许目标（含 MCP 导入）** 中选择目标，确定后停止服务、保存配置并重新启动。通过 `targets` 确认 `allowed` 状态，再使用返回的 `mcp_...` ID 提交任务。同名或同 IP 的多条记录必须使用明确 ID。

导入不会自动连接靶机。“导入后自动授权新记录”默认关闭，关闭时需按上述步骤手动授权；在本机明确开启该开关后，新记录会自动加入允许范围，已有记录不会追溯授权。凭据字段、去重规则和完整示例见[靶机导入说明](docs/靶机导入.md)。

## 运行数据与模板

服务配置、任务数据库和加密凭据默认保存在 `管理工具/GodHand`；MCP 服务可通过 `--data-dir` 隔离比赛数据。GUI 与 MCP 共用该比赛目录中的加密机子库，首次读取时备份并迁入旧记录。迁移、更新与恢复方法见 [统一机子库说明](docs/机子库迁移与恢复.md)。

凭据使用当前 Windows 用户的 DPAPI 保护。源码仓库排除个人连接配置、`rsa_keys.json`、运行数据库、日志及构建缓存。RSA 部署模板使用 `__GODHAND_RSA_PUBLIC__` / `__GODHAND_RSA_PRIVATE__` 占位符，通过 `template_render` 渲染为本机密钥对应的产物后再使用；密钥由本机生成和保存。

同一数据目录只运行一个服务。窗口自行启动的服务随窗口关闭；附着已独立启动的服务时，关闭窗口保留该服务。服务重启后，`interrupted` 表示只读任务被中断，`needs_review` 表示远程修改结果需要核对；Flag 提交状态为 `unknown` 时，应先检查平台记录。

## 项目结构

```text
GodHand-AIpower/
├── main_work.py          # GUI 主入口及业务源
├── main.py               # 兼容入口
├── dashboard.py          # 总览
├── tool_launchers.py     # 外部工具启动
├── AI中枢/               # MCP、任务服务、Worker、目标导入与模板
├── 工具管理模块/         # 工具登记、路径与依赖检查
├── 攻击模块/             # 原项目通信、批量脚本及 PHP 工具
├── 防御模块/             # 修复与补丁部署
├── 上传靶机/             # 部署模板
├── docs/                 # 随源码与便携包发布的文档
├── docx/                 # 本地 Word 文档，不提交或打包
├── skill.md              # AI 调用与部署指南
├── requirements.lock.txt # Python 依赖版本
├── build_exe.py          # 核心程序构建入口
└── build_work.py         # 便携包构建入口
```

`管理工具/`、`tools/`、`AI中枢/vendor/` 和 `_runtime/` 按本机配置、依赖安装或构建结果生成，不纳入 Git 提交。

## 开发与构建

修改 `main_work.py` 中的业务实现后，重新生成 Worker 业务和字段元数据：

```powershell
python AI中枢/generate.py
python -B -m unittest AI中枢.test_target_store AI中枢.test_targets AI中枢.test_acceptance AI中枢.test_transports -v
python build_work.py --check
```

测试使用本机模拟服务及独立测试数据目录。GUI、MCP 协议、本机模拟与真实靶场的验证范围分别记录在验收文档中。

构建核心程序或含外部工具的便携包：

```powershell
python build_exe.py --output ../GodHand-build/GodHand-core-1.5.0 --cache ../GodHand-tmp/build-core
python build_work.py --output ../GodHand-build/GodHand-portable-1.5.0 --cache ../GodHand-tmp/build-portable --tools-root C:/CTF/PortableTools
```

输出目录必须是新目录。便携构建的 `--tools-root` 目录需包含 `Tabby/` 与 `WinSCP/` 程序文件及许可证；构建会排除个人配置、运行数据和历史密钥，并生成文件校验清单及便携 ZIP。详见开发与发布。

## 文档

| 文档 | 内容 |
| --- | --- |
| [MCP 接入](docs/MCP接入.md) | 客户端配置、动作参数、任务与服务生命周期 |
| [1.4.0 发行说明](docs/1.4.0-发行说明.md) | 新增能力、便携包内容与验证边界 |
| [1.4.0 实现与验收](docs/1.4.0-实现与验收.md) | 防御中心、MCP 能力、验证分层与限制 |
| [防御中心操作说明](docs/防御中心.md) | 目标要求、能力参数、流程与回滚 |
| [防御来源与许可证](防御模块/SOURCES.md) | 上游来源、许可证及适配边界 |
| [1.3.0 实现状态](docs/1.3.0-本轮实现状态.md) | 本轮交付范围、验证结果与未实现项 |
| [1.3.0 同步检查](docs/1.3.0-同步检查.md) | 便携包校验、开发版同步与回归测试 |
| [靶机导入](docs/靶机导入.md) | 字段、批量限制、去重和本机授权 |
| [工具接入](docs/工具接入.md) | 工具登记、解释器、依赖和参数 |
| [开发与发布](docs/开发与发布.md) | 业务生成、检查、构建和发布 |
| [能力覆盖表](docs/能力覆盖表.md) | 业务动作与验证范围 |
| [1.2.0 靶机导入验收](docs/靶机导入验收.md) | 本机导入、授权、协议及构建检查记录 |
| [1.1.0 统一入口验收](docs/统一入口验收.md) | 统一 MCP 入口、界面及便携程序检查记录 |
| [历史验收](docs/验收记录.md) | 早期检查记录 |
| [来源与鸣谢](docs/来源与鸣谢.md) | 原项目与第三方归属 |


AI 调用指南见 [skill.md](skill.md)。本地 `docx/` 文件夹不提交 Git，也不进入便携包；`docs/` 文档正常保留。

构建 Windows 监测配套环境：`python 防御模块/aoi_bundle.py`。脚本校验固定上游版本与归档 SHA-256，并构建静态 Linux x86_64 探针。首次构建需要联网；完整便携包在运行时无需下载依赖。

## 来源与鸣谢

- 原项目：[YL_Phantom](https://github.com/Wwlsyl/YL_Phantom)，作者 **[Wwlsyl](https://github.com/Wwlsyl)**。感谢原作者提供 AWD 工具工程；GodHand 在导入工程上整合界面，并扩展工具管理、任务服务和 MCP 接入。
- 当前仓库：[RockeyRoc / GodHand-AIpower](https://github.com/RockeyRoc/GodHand-AIpower)。
- Tabby、WinSCP 及 Python 第三方依赖分别遵循其自身许可证与作者归属。

当前仓库未声明统一开源许可证，请查阅原项目及各依赖的许可条款；原项目的用途说明不等同于 MIT、Apache 等开源授权。补充归属见来源与鸣谢。
