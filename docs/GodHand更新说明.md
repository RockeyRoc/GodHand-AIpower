> 历史记录 · 适用版本 1.1.0。当前 1.6 使用方法见 [MCP 接入](MCP接入.md)，本轮结果见 [1.6 验收记录](1.6.0-验收记录.md)。

# GodHand 1.1.0

界面标题、总览、帮助、状态栏、运行日志和 Windows EXE 产品信息统一为 GodHand。窗口、横幅、总览和 EXE 使用用户提供的新 Logo。项目所在目录不改名。

工具管理共享 GUI 与 MCP 的工具登记和任务服务。开发版和便携版均放置 `tools/Tabby`、`tools/WinSCP`，目标从机子管理中的记录读取。工具状态为“已启动”仅表示创建进程，不表示远程连接成功。

Python、PHP、PowerShell 解释器和 phpggc 路径可在“工具管理 → 工具路径”设置。解析顺序：本机设置、`tools/Python` / `tools/PHP` / `tools/PowerShell` 扩展目录、系统 PATH。phpggc 还支持 `第三方工具/phpggc/phpggc` 和 `tools/phpggc/phpggc`。配置文件在管理数据目录 `tool-paths.json`，不进入发布包。当前机器缺少 PHP CLI 和 phpggc，因此相应任务会提示缺失依赖。

原批量脚本、PHP 禁用函数分析和 phpggc 表单保留在工具页第二个子页；内置业务能力同时显示在统一清单中。MCP 统一调用 `godhand`，先用 action=tools 检查可用性，再用 action=run_tool 指定工具、参数和目标。任务查询、日志和取消接口与 GUI 一致。

本次仅进行本机模拟、源码检查和构建检查；按用户要求暂不进行真实靶场验收。
