# GodHand AIpower 1.4.0 远端发布核验记录

## GitHub 发布

- Release：[GodHand AIpower 1.4.0](https://github.com/RockeyRoc/GodHand-AIpower/releases/tag/v1.4.0)
- 标签：`v1.4.0` 解引用到实现提交 `43a8f8fd93e3a9154baf853c992bf5ab9ac8b9bb`；推送时 `origin/main` 与该提交一致。
- 便携 ZIP：[GodHand-1.4.0_20261020.zip](https://github.com/RockeyRoc/GodHand-AIpower/releases/download/v1.4.0/GodHand-1.4.0_20261020.zip)，281,199,330 字节。
- ZIP SHA-256：`ee4ab0a2b9d9476ad42a693dedd2f38d9dced1cb205dcb3b862b8a66424c31f4`；与 GitHub Release API 返回的附件摘要一致。
- 校验文件：[GodHand-1.4.0_20261020.zip.sha256](https://github.com/RockeyRoc/GodHand-AIpower/releases/download/v1.4.0/GodHand-1.4.0_20261020.zip.sha256)，94 字节；下载回读内容与本地校验文件一致。
- 核心程序目录：`GodHand-1.4.0-core_20261020`。
- 开发版原 1.3.0 EXE 与运行库备份：`GodHand-dev-backups/GodHand-1.3.0-before-1.4.0_20261020`。同步后的 EXE `--doctor` 和 `--check-ui` 检查通过。

## 验证

- 61 项回归测试通过；防御适配器测试使用本地夹具和模拟 SSH。
- 源码与便携 EXE 的 `--doctor`、`--check-ui` 通过；便携 EXE MCP HTTP 冒烟发现 `godhand`，完成防御能力读取、报告任务和取消。
- 当前本机 MCP 服务报告版本 `1.4.0`、95 项业务动作，其中防御操作 20 项；服务地址和认证配置保持原值。
- 未在真实隔离 Linux 主机上验证 Fail2Ban、Watchbird、Lynis、YARA、AIDE；真实目标验证仍需隔离环境和目标凭据。
