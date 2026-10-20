# 防御适配器来源与许可证

GodHand 1.4.0 只包含自编写的 Python 适配器。它不会把下列上游程序或平台打包进 Windows 程序，也不会在目标机安装依赖；所有适配器只调用已存在的目标程序或管理已安装的 Watchbird 配置。以下许可证记录以各项目仓库中的许可文件为准。

| 项目 | 上游来源 | 许可证 | 本项目使用范围 |
|---|---|---|---|
| Fail2Ban | <https://github.com/fail2ban/fail2ban> | GPL-2.0-or-later | 通过 SSH 调用现有 `fail2ban-client`，管理单独命名的 GodHand Jail；不复制上游代码。 |
| CyberStrikeAI | <https://github.com/AIPentest/CyberStrikeAI> | Apache-2.0 | 参考声明式参数、任务流程、证据组织与报告结构；未集成平台代码或运行时。 |
| Watchbird | <https://github.com/leohearts/awd-watchbird> | AGPL-3.0 | 通过 SSH 读取或修改目标上已安装的配置和日志，并检查 PHP 语法；未复制或分发 WAF。上游 README 表示该项目不建议用于比赛场景。 |
| Watchbird 的 `getFormData()` 来源 | Watchbird 上游 `watchbird-source.php` 文件头 | CC-BY-NC-SA-4.0 | GodHand 未复制此实现；仅记录上游自身声明的单独来源和条款。 |
| Lynis | <https://github.com/CISOfy/lynis> | GPL-3.0 | 通过 SSH 调用现有 `lynis` 执行只读审计并解析告警。 |
| YARA | <https://github.com/VirusTotal/yara> | BSD-3-Clause | 通过 SSH 调用现有 `yara`，扫描用户明确提供的规则和目录。 |
| AIDE | <https://github.com/aide/aide> | GPL-2.0-or-later | 通过 SSH 调用现有 `aide`；初始化、检查和提交新基线均为独立操作。 |

## 安全与兼容边界

- 新增适配器只接受固定动作和经过校验的参数，不提供任意 SSH shell 能力。
- 目标连接资料由 GodHand 的加密目标库读取，并受本地允许目标列表、任务锁、超时与取消机制约束。
- 首次接入以探测和证据收集为主；封禁、WAF 配置、AIDE 基线初始化或更新都需要单独提交写操作。
- Watchbird 仅适用于目标上已经安装且由操作者确认来源的实例。首版不包含其流量重放、Flag 替换或安装功能。
- Watchbird 上游 README 明确提示其不用于比赛。使用该上游 WAF 前应遵守目标环境和比赛规则；该提示作为来源说明保留，不是 GodHand 代码指令。
- 目标缺失工具、PHP CLI 或权限时，任务会给出可诊断原因，不会尝试安装或提权。

## AoiAWD Windows 配套环境

GodHand 1.5.0 新增 Windows 原生 AoiAWD 运行环境。固定上游版本为 `DasSecurity-HatLab/AoiAWD@0d57da2317685964ccf62d9d7f05a87f699fc8c7`（AGPL-3.0）。构建脚本为 `aoi_bundle.py`，归档地址和 SHA-256 在脚本与生成的 bundle.json 记录。

- 上游 Core 以 PHP 7.4.33 Windows x64（PHP-3.01）与 MongoDB PHP 驱动 1.16.2（Apache-2.0）运行；MongoDB 6.0.26 Community（SSPL-1.0）仅监听本机回环地址。
- RoundWorm、Guardian 为 Linux x86_64 静态构建，使用 inotify-tools v3.14（GPL-2.0）；Zig 0.13.0 仅用于构建，不随运行包分发。
- 配套环境保留 `source/AoiAWD` 原始源码、`source/patched` 修改后的探针源码、`source/inotify-tools` 对应依赖源码和 `core/` 实际运行的修改后 PHP 源码，并保留原项目 LICENSE 与各运行环境的许可文件。
- GodHand 修改包括本机进程管理、按目标隔离、替代 Web 界面、只观察的告警插件、PHP 断线放行与响应保持、RoundWorm 发送与兼容修正、Guardian 子进程等待及断线兼容修正。原始上游 WebMonitor 不直接注入，避免其调试输出和对请求数组的修改。
- 目标系统中不安装 PHP、MongoDB 或系统服务，不依赖目标 root 权限。第三方运行环境来自固定的官方归档；完整运行包离线使用。
