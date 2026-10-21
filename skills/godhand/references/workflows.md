# GodHand 专项与常用流程

所有操作通过 godhand 的 action、arguments 调用。此文件提供工作流程；具体字段、默认值和目标要求用 capabilities 查询。

## Aoi AWD

GodHand 所在 Windows 机器运行配套 Core、Web 和 MongoDB。Linux x86_64 靶机运行探针，使用机子库中的 SSH 资料回传。

1. 查询 defense_aoi_probe，并对明确的目标 ID 检查 SSH、架构、目录及 PHP 环境。
2. 查询 defense_aoi_deploy。watch_paths 为目标绝对目录列表；web=true 时按目标环境配置 PHP Web 捕获；没有 PHP 时跳过 Web 组件。
3. elf_path 可生成单独的 <原文件>.guardianed。保留原 ELF；是否切换真实题目的启动配置由当前任务决定。
4. 提交部署后读取 task 和 logs，确认结果与备份位置。
5. defense_aoi_open 建立回传并返回该目标的本机监测 URL。查看与本次验证对应的实际事件。
6. 结束部署时执行 defense_aoi_remove，查询恢复结果；存在文件冲突时保留备份并报告冲突位置。

defense_aoi_start、defense_aoi_status、defense_aoi_stop 使用 target=local，管理本机服务。每个目标有独立监测页与数据命名空间。SSH 或 GodHand 中断期间暂停采集；PHP 保持原应用响应。需要恢复采集时重新建立连接，期间遗漏的事件不回补。

## 常用流程

### 攻击能力

查询 `attack_*` 能力获得窄参数契约。`attack_dfunc_check` 使用目标库保存的 WebShell 路径与口令；`attack_mix_once` 每次只针对一个已允许目标执行一轮命令，返回 `flags`、`flag_count` 与模式，不自动提交。需要提交时将明确的 Flag 交给 `flag_submit`，保留两个任务 ID。

`attack_phpinfo_analyze` 只解析提供的文本，不从 URL 下载。`attack_phpggc_list`、`attack_phpggc_search`、`attack_phpggc_generate` 使用本机 PHP/phpggc；生成参数必须是独立字符串数组，依赖缺失会返回明确错误。

### 查看目标与工具

查询 targets，展示名称、ID、地址和 allowed 状态。tools 返回工具和缺失依赖。新目标通过 targets_import 的 records 数组导入；更新通过 targets_update 指定 ID 和 changes，已有 revision 时携带 expected_revision。

导入默认保留原连接资料；重复记录返回 differing fields。目标连接发生变化后，按返回的 requires_local_selection 指引用户在本机重新选择。

### 备份与恢复

查询 ops_backup 或 ops_restore，读取目录及产物参数。执行前确认用户指定的目标和目录；完成后给出备份位置或恢复结果。恢复使用明确的备份产物，保留任务 ID 与恢复输出。

### WAF 与主机防御

按目标查询 waf_check、waf_rules_read 等能力。部署、规则写入与切换使用相应能力，并保存返回的备份位置。

defense_probe 检查已安装的 Fail2Ban、Watchbird、Lynis、YARA 和 AIDE。进一步检查或配置时查询对应 defense_* 操作；按任务选择配置、封禁、审计、扫描或恢复动作。Fail2Ban management_ip 是管理端到目标的来源 IP/CIDR。

### 文件、SSH 与进程

monitor_file_* 处理远程文件。ops_ssh_*、ops_process_* 和 ops_db_* 处理 SSH、进程与数据库。查询所需单项能力参数，按用户指定路径、进程或库名执行，返回实际输出和相关产物。

### Flag

查询 flag_test、flag_hunt 或 flag_submit 参数，使用用户指定的目标、比赛轮次及提交平台。持续获取任务保存 ID，按要求停止。accepted、rejected、duplicate、rate_limited 和 unknown 分别报告；unknown 先查平台状态。

### 外部工具

先查询 tools。run_tool 启动已登记工具；参数按其定义填写。Tabby/WinSCP 返回已启动表示程序已创建，连接状态由外部窗口显示。普通脚本任务的子进程随任务结束清理。

## 返回结果

单次任务报告目标、操作、终态、关键发现和产物。持续任务报告当前状态与后续查询 ID。部分结果保留已完成步骤的输出，说明需要继续处理的具体项目。
