"""Generate an honest registration/evidence matrix, separate from test claims."""
from pathlib import Path
import json
from .core import app_root
from .operations import catalogue
def main():
    root=app_root();specs=catalogue();baseline=json.loads((root/'AI中枢'/'baseline.json').read_text(encoding='utf-8'))
    simulated={'waf_check','flag_test','flag_submit','monitor_file_read','channel_exec','ops_audit_local','ops_backup'}
    lines=['# 能力覆盖表','','当前业务源：main_work.py；main.py 为兼容入口。GUI 通过快照调用同一 Worker，MCP 通过 OperationSpec 构建输入。表中验证状态独立于接口登记。','','| 标识 | 能力 | 原实现 | 验证证据 |','|---|---|---|---|']
    for s in specs.values():lines.append(f'| {s.id} | {s.title} | {s.actor}.{s.method} | '+('本机 HTTP/文件模拟通过' if s.id in simulated else '已登记；远程场景待隔离靶场验收')+' |')
    lines+=['','备用入口合并的方法：','',', '.join(baseline['merged_methods']),'','界面辅助动作：文件选择、搜索、表格排序、配置编辑、主机录入导入导出、帮助和全局连接栏保留在原页面。MCP 通过明确参数代替文件对话框；读取主机和启动外部工具分别使用 godhand_targets、godhand_run_tool。修改允许范围和访问凭据仅在本机配置页进行。','','连续任务：waf_heartbeat、monitor_watch、channel_heartbeat、flag_hunt、ops_schedule_backup 通过任务查询和取消接口统一管理。','', '原始基线 SHA-256：','',json.dumps(baseline['baseline'],ensure_ascii=False,indent=2)]
    (root/'docs'/'能力覆盖表.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return len(specs)
if __name__=='__main__':print(main())
