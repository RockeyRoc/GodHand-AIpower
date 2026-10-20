<div align="center">

<img src="logo.png" alt="GodHand Logo" width="112">

# GodHand AIpower 1.6.0

**把靶机、AWD 工具和 AI 调用放到同一个工作台。**

本地开发版 · Windows x64 · Codex 默认 stdio 接入

</div>

当前开发目录已验证可运行；便携包的全新目录启动仍在核验，进度见[验收记录](docs/1.6.0-验收记录.md)。

GodHand 将机子管理、WAF、Flag、监控、运维和外部工具整合到一个窗口。你可以直接点击界面操作，也可以在 Codex 中用自然语言调用同一套能力、查看任务和读取结果。

## 五步开始使用

### 1. 启动 GodHand

解压完整便携包，双击 GodHand.exe。EXE 与 _runtime 放在同一目录。进入主窗口后，可以看到“机子管理”“工具管理”和“AI 中枢”等页面。

### 2. 添加靶机

打开“机子管理”，添加比赛靶机的地址和连接资料，保存后在列表中确认记录。已有记录可以继续使用。批量录入方式见[靶机导入](docs/靶机导入.md)。

### 3. 选择交给 AI 操作的目标

打开“AI 中枢”，点击“停止服务”，再点击“选择允许目标”。勾选靶机、确定、保存配置，然后启动服务。页面显示已选目标数量；AI 查询 targets 时，已选记录的 allowed 为 true。

### 4. 接入 Codex

在“Codex 接入”区域选择配置位置，点击“写入 Codex 配置”。默认“用户级”对所有项目生效；“项目级”写入当前 GodHand 目录。

到 Codex 的 MCP 设置中重启 godhand，或重新打开 Codex。看到 godhand 已连接后，即可调用。默认 stdio 会自动启动本机桥接进程，直接读取同一机子库，无需复制令牌或设置环境变量。

可以先对 AI 说：

> 使用 GodHand 检查连接，列出已保存的靶机和当前允许操作的目标。

### 5. 查看任务结果

AI 提交操作后会获得任务 ID。让 AI 继续查询任务和日志，或在“工具管理”的任务列表中选择任务查看状态。耗时操作可通过“停止选中任务”结束。

操作成功以任务结果和对应输出为准。连接检查通过表示服务与协议可用；具体部署效果在相应目标的检查结果中确认。

## 常用入口

| 想做什么 | 打开哪里 |
| --- | --- |
| 添加、编辑或查看连接资料 | 机子管理 |
| 部署、检查 WAF 或调整规则 | WAF 管理 |
| 获取、提交 Flag 和查看历史 | Flag 获取 |
| 查看监控、告警和远程文件 | 靶机监控 |
| 备份、数据库、SSH、进程和源码检查 | 综合运维 |
| 部署 AoiAWD 探针并打开监测页 | 综合运维 → 防御中心 |
| 启动 Tabby、WinSCP 或脚本，查看任务 | 工具管理 |
| 接入 Codex、选择允许目标和检查连接 | AI 中枢 |

外部工具需要的解释器可在“工具管理 → 工具路径”设置。便携包提供 Tabby、WinSCP 和 AoiAWD 配套环境；Python/PHP 用户脚本及 phpggc 按工具页显示的依赖准备。见[工具接入](docs/工具接入.md)。

## 1.6 的接入体验

- AI 中枢按服务状态、Codex 接入、允许目标和高级设置分组，常用按钮保持可见。
- 运行中锁定范围设置，按“停止 → 保存 → 启动”应用修改。
- Codex 默认 stdio；HTTP 客户端共用固定地址 http://127.0.0.1:18765/mcp。
- 配置预览只读；写入 Codex 前备份，仅更新 GodHand 的配置。
- 完整 AI 指南见 [skill.md](skill.md)，可安装技能在 [skills/godhand](skills/godhand/SKILL.md)。

## 用 AI 操作

所有模块共用一个 MCP 工具 godhand，输入包含 action 和 arguments。先查询能力与目标，再提交操作：

    {"action":"echo","arguments":{"message":"GodHand ready"}}
    {"action":"targets"}
    {"action":"capabilities","arguments":{"operation":"ops_backup"}}

任务由 run 或 run_tool 提交；task、logs 查看结果；cancel 停止任务。参数和示例见 [AI 调用指南](skill.md)与 [MCP 接入](docs/MCP接入.md)。GodHand 用于已获授权的 AWD、CTF 及自建靶场，目标范围在本机选择。

## 数据与升级

机子记录、任务和加密凭据默认位于 管理工具/GodHand。升级程序时保留该目录，EXE 与 _runtime 使用同一次构建的配套文件。多个比赛的数据可用 --data-dir 隔离；固定端口同时供一套后台服务使用，切换比赛前先停止原服务。

窗口自行启动的后台随窗口关闭；附着独立后台时，关闭窗口保留后台。恢复和迁移见[机子库迁移与恢复](docs/机子库迁移与恢复.md)。

## 源码运行

使用 Windows、带 Tkinter 的 Python 3.14。在开发目录执行：

    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
    .\.venv\Scripts\python.exe main_work.py

main.py 为兼容入口。源码运行生成的 Codex 配置使用当前 Python 和入口文件的绝对路径；便携程序生成的配置使用其 GodHand.exe。

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [使用说明](使用说明.txt) | 可离线阅读的五步操作说明 |
| [MCP 接入](docs/MCP接入.md) | Codex、通用 HTTP、接口和排查 |
| [AI 调用指南](skill.md) | 安装 skill、常用请求和任务流程 |
| [靶机导入](docs/靶机导入.md) | 批量录入、选择目标和更新资料 |
| [防御中心](docs/防御中心.md) | AoiAWD 部署、监测和恢复 |
| [工具接入](docs/工具接入.md) | 工具路径、自定义工具和依赖 |
| [开发与发布](docs/开发与发布.md) | 架构、检查和打包 |
| [1.6 验收记录](docs/1.6.0-验收记录.md) | 本次测试结果及产物 |
| [能力覆盖表](docs/能力覆盖表.md) | 业务与接口的技术对应 |
| [来源与鸣谢](docs/来源与鸣谢.md) | 作者、上游和许可证 |

历史发行与验收文档保留其适用版本。docs/ 随源码和便携包提供；docx/ 为本地 Word 文档目录，排除 Git 提交和便携打包。

业务基线来自 [Wwlsyl / YL_Phantom](https://github.com/Wwlsyl/YL_Phantom)，第三方组件保留各自归属与许可证。GitHub 稳定版为 1.5.0，本目录及 1.6 便携包用于本地开发。
