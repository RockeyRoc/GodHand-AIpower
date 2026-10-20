"""Explicit catalogue for all business actions in the GodHand entrypoints."""
from dataclasses import asdict
import json
from .core import OperationSpec, resource_root


GROUPS={
 'WafTab':('waf', [('deploy','部署 WAF','start',['deploy']),('check','检查 WAF','start',['check']),('update','更新 WAF','start',['update']),('remove','卸载 WAF','start',['remove']),('rules_read','读取规则','load_rules',[]),('rules_write','保存规则','save_rules',[]),('traffic','读取攻击流量','fetch_traffic',[]),('heartbeat','WAF 心跳','toggle_heartbeat',[])]),
 'FlagTab':('flag',[('test','测试通道','test_conn',[]),('submit_test','测试提交平台','test_submit',[]),('hunt','轮次 Flag 获取','start_hunt',[]),('submit','手动提交 Flag','manual_submit',[]),('history_clear','清空提交历史','clear_history',[])]),
 'MonitorTab':('monitor',[('watch','启动监控','toggle',[]),('attack_clear','清空攻击排行','clear_atk',[]),('attack_export','导出攻击排行','export_atk',[]),('file_list','列举文件','fm_refresh',[]),('file_parent','返回上级目录','fm_go_up',[]),('file_upload','上传文件','fm_upload_file',[]),('file_mkdir','创建目录','fm_mkdir',[]),('file_search','搜索文件','fm_search',[]),('file_save','保存远程文件','fm_save_file',[])]),
 'CreepTab':('channel',[('deploy','部署通信脚本','do_deploy',[]),('implant','上传通信组件','do_implant',[]),('spread','批量通信部署','do_spread',[]),('check','检查通信','do_check',[]),('cleanup','清理通信组件','do_kill',[]),('exec','执行通信任务','do_exec',[]),('ssh_backdoor','SSH 通道部署','do_ssh_backdoor',[]),('ssh_check','SSH 通道检查','do_ssh_back_check',[]),('heartbeat','通信心跳','toggle_c_hb',[])]),
 'OpsTab':('ops',[(name,title,method,[]) for name,title,method in [
 ('backup','创建备份','do_backup'),('restore','恢复备份','do_restore'),('scanner','检查可疑文件','do_scan'),('db_show','列出数据库','do_db_show'),('db_create','创建数据库','do_db_create'),('db_tables','列出数据库表','do_db_tables'),('db_data','查询表数据','do_db_data'),('db_sql','执行 SQL','do_db_sql'),('db_dump','备份数据库','do_db_dump'),('db_chpass','修改数据库密码','do_db_chpass'),('harden','应用加固','do_harden'),('audit_remote','审计远程源码','af_scan_remote'),('audit_local','审计本地源码','af_scan_local'),('patch','生成与部署补丁','af_deploy_patch'),('ip_ban','添加 IP 黑名单','do_ipban'),('ip_unban','移除 IP 黑名单','do_ipunban'),('ip_list','查看 IP 黑名单','do_ipban_list'),('ip_batch','批量 IP 黑名单','do_ipban_batch'),('schedule_backup','启动定时备份','toggle_sched_backup'),('discover','发现主机','do_discover'),('ssh_test','测试 SSH','do_ssh_test'),('ssh_password','修改 SSH 密码','do_ssh_chpass'),('ssh_command','执行 SSH 命令','do_ssh_cmd'),('ssh_batch','批量 SSH 命令','do_ssh_batch'),('process_list','列出进程','do_ps'),('process_kill','结束进程','do_kill_proc'),('port_block','管理端口规则','do_block_port'),('phpinfo','读取 PHP 信息','do_phpinfo')]]),
 'ToolsTab':('legacy_tool',[(name,title,method,[]) for name,title,method in [('phpggc_list','列出 phpggc 链','ggc_refresh'),('phpggc_search','搜索 phpggc 链','ggc_search'),('phpggc_generate','生成 phpggc 产物','ggc_generate'),('df_check','分析 PHP 禁用函数','df_check'),('df_parse','解析 PHP 信息','df_parse_pi'),('batch','运行批量脚本','br_run')]])
}


def catalogue():
    metadata=json.loads((resource_root()/'AI中枢'/'business_fields.json').read_text(encoding='utf-8'))
    specs={}
    read_markers=['check','test','list','read','show','tables','data','traffic','search','phpinfo','df_','discover','audit','process_list']
    for actor,(prefix,actions) in GROUPS.items():
        methods=set(metadata['methods'].get(actor,[]))
        for key,title,method,args in actions:
            if method not in methods:continue
            fields={k:v for k,v in metadata['fields'][actor].items() if v.get('kind') in ('value','text') and not k.startswith('_') and not any(t in k.lower() for t in ['btn','button','label','frame','tree','log','pass','pwd','token','secret','spass'])}
            local=(key in ['audit_local','df_parse','phpggc_list','phpggc_search','phpggc_generate','batch'])
            specs[prefix+'_'+key]=OperationSpec(prefix+'_'+key,title,actor,method,args,any(t in key for t in read_markers),local,'参数字段沿用GodHand 表单；目标凭据由服务端读取。',fields)
    extras={
      'WafTab': [('ssh_deploy','SSH 部署 WAF','ssh_deploy_waf'),('ssh_shell','SSH 创建通信入口','ssh_create_shell')],
      'FlagTab':[('ssh_deploy','SSH 部署 Flag 通信入口','ssh_deploy_flag')],
      'MonitorTab':[('deploy','SSH 部署监控','deploy_monitor'),('file_read','读取远程文件','fm_req'),('file_delete','删除远程文件','fm_req'),('file_download','下载远程文件','fm_req'),('log_export','导出监控日志','export_log')],
      'CreepTab':[('ssh_deploy','SSH 部署通信组件','ssh_deploy_creep'),('heartbeat','通信心跳','toggle_creep_hb')]}
    for actor,items in extras.items():
        prefix=GROUPS[actor][0]
        for key,title,method in items:
            template=next(s for s in specs.values() if s.actor==actor)
            specs[prefix+'_'+key]=OperationSpec(prefix+'_'+key,title,actor,method,[],key in ['file_read','file_download'],False,'SSH 部署使用已保存目标凭据。文件操作明确提供 remote_path。',template.fields)
    for spec in specs.values():
        spec.fields.update({k:{'kind':'value','default':v} for k,v in {'command':'','remote_path':'','content':'','pid':'','block_port':'','scan_mode':'tcp','scan_ports':'80','findings':[],'webdir':'/var/www/html'}.items()})
    specs['template_render']=OperationSpec('template_render','渲染本机通信模板','TemplateHandler','render',local=True,fields={'template':{'kind':'value','default':'rsa_agent.php'}})
    from 防御模块.engine import catalogue as defense_catalogue
    specs.update(defense_catalogue())
    return specs

