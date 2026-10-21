# GodHand 1.6 AI 使用指南

GodHand 让 AI 使用已保存的靶机和工具执行 AWD 任务，并在同一个任务库中查看结果。先完成接入，再用自然语言描述目标，例如“检查这台靶机的 SSH 状态”“备份站点”“部署监测并打开页面”。

## 在 Codex 中安装技能

标准技能包位于 skills/godhand/。将整个 godhand 文件夹复制到用户技能目录：

    $destination = Join-Path $env:USERPROFILE '.agents/skills'
    New-Item -ItemType Directory -Path $destination -Force
    Copy-Item -LiteralPath './skills/godhand' -Destination $destination -Recurse

已有同名技能时，先备份该文件夹，再更新。Codex 识别技能后，可使用 $godhand 调用；未显示时重新打开 Codex。项目内使用也可复制到项目的 .agents/skills/。安装位置与技能格式见[官方说明](https://learn.chatgpt.com/docs/build-skills)。

技能负责选择工作流程，MCP 负责实际调用。在“AI 中枢 → Codex 接入”写入配置，并到 Codex 的 MCP 设置重启 godhand。默认 stdio 无需手工设置令牌。

攻击模块使用 `attack_*` 能力。先查询 `capabilities` 获取精确字段；`attack_dfunc_check` 与 `attack_mix_once` 必须使用 `targets` 返回且 `allowed=true` 的目标 ID。`attack_mix_once` 只执行一轮并返回结构化 Flag 结果，不自动提交；需要提交时再调用现有 `flag_submit`。`attack_phpinfo_analyze` 只接受文本，phpggc 的参数使用字符串数组传递，不拼接 shell 命令。

## 先确认连接和目标

    {"action":"echo","arguments":{"message":"GodHand ready"}}
    {"action":"health"}
    {"action":"targets"}

targets 返回目标 ID、来源及 allowed 状态。远程任务使用明确 ID；allowed 为 false 时，在 GodHand 中停止服务、选择允许目标、保存并启动。新增或更新连接的方法见[靶机导入](docs/靶机导入.md)。

## 按任务选择能力

| 用户想做什么 | 查询方向 |
| --- | --- |
| WAF 部署、规则和流量 | waf_* |
| Flag 通道、获取与提交 | flag_* |
| 监控与远程文件 | monitor_* |
| 通信与部署 | channel_* |
| 备份、源码、数据库、SSH、进程和 IP 管理 | ops_* |
| AoiAWD 监测、主机审计和报告 | defense_* |
| 模板渲染 | template_render |
| Tabby、WinSCP、脚本与自定义工具 | action=tools，再用 run_tool |

用 capabilities 查询操作和参数，tools 查询工具及依赖。例如：

    {"action":"capabilities","arguments":{"operation":"ops_backup"}}

填写查询返回的参数后提交任务。下面是对本机目录进行源码检查的例子：

    {
      "action":"run",
      "arguments":{
        "operation":"ops_audit_local",
        "target":"local",
        "parameters":{"directory":"C:/CTF/site"},
        "timeout":60
      }
    }

远程任务把 target 替换为 targets 返回的 ID；本机任务使用 local。能力目录为当前版本的实时依据，按需查询具体操作即可。

## 执行后继续查结果

run 和 run_tool 返回 result.id。查询该 ID：

    {"action":"task","arguments":{"task_id":"返回的ID"}}
    {"action":"logs","arguments":{"task_id":"返回的ID","after":0,"limit":100}}
    {"action":"cancel","arguments":{"task_id":"返回的ID"}}

完成后报告：执行的操作与目标、最终状态、关键结果、产物或备份位置，以及需要用户处理的事项。持续监控保留任务 ID，按用户要求停止并确认清理结果。

相同请求需要重试时，可使用相同 idempotency_key；参数也应相同。远程修改返回 needs_review 或平台提交返回 unknown 时，先查询目标/平台状态，再决定下一步。

## AoiAWD 常用流程

1. 对目标查询 defense_aoi_probe 参数并检查环境。
2. 查询 defense_aoi_deploy，按需要填写监测目录、PHP Web 或 ELF 路径，再部署。
3. 使用 defense_aoi_open 获取该目标的本机监测 URL。
4. 验证对应事件；结束部署时使用 defense_aoi_remove 恢复。

Core、Web 和 MongoDB 在 GodHand 所在 Windows 机器运行。Linux 靶机部署探针，通过已有 SSH 连接回传。PWN 监测生成单独的 .guardianed 文件；题目启动切换按具体任务处理。细节见[防御中心](docs/防御中心.md)。

## 可以直接对 AI 说

- “用 GodHand 列出靶机、允许状态和缺少的工具依赖。”
- “查询这台靶机的备份能力，按我提供的目录执行备份并返回产物位置。”
- “检查目标的 AoiAWD 环境，告诉我需要填写哪些部署参数。”
- “查看任务 ID 的结果和新增日志，完成后给我一段简洁说明。”

完整可安装指令及专项流程见 [SKILL.md](skills/godhand/SKILL.md)。客户端排查见 [MCP 接入](docs/MCP接入.md)。
