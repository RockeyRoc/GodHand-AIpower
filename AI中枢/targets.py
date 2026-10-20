"""Encrypted MCP target imports. Local configuration alone grants access."""
import hashlib
import ipaddress
import json
import re

FIELDS={'name','ip','port','shell','pass','password','ssh_port','ssh_user','ssh_pass','https','webdir','monitor_pass','creep_pass'}
SECRETS={'pass','password','ssh_pass','monitor_pass','creep_pass'}


def endpoint_key(row):
    """Credentials and display names never change a connection identity."""
    values=[str(row.get(key) or default) for key,default in [
        ('ip',''),('port','80'),('ssh_port','22'),('ssh_user','ctf'),('shell','/shell.php')]]
    values.append(bool(row.get('https',False)))
    return hashlib.sha256(json.dumps(values,ensure_ascii=False).encode('utf-8')).hexdigest()


def normalize(record,index):
    def invalid(field):raise ValueError(f'第 {index} 条靶机的 {field} 格式不正确')
    if not isinstance(record,dict):invalid('记录')
    if set(record)-FIELDS:raise ValueError(f'第 {index} 条靶机包含不支持的字段')
    row={}
    defaults={'name':'','ip':'','port':'80','ssh_port':'22','ssh_user':'ctf','shell':'/shell.php','https':False,'webdir':'/var/www/html'}
    for key in FIELDS:
        if key not in record and key not in defaults:continue
        value=record.get(key,defaults.get(key))
        if key in ('port','ssh_port'):
            if isinstance(value,bool) or not isinstance(value,(int,str)) or not re.fullmatch(r'[0-9]{1,5}',str(value)) or not 1<=int(value)<=65535:invalid(key)
            row[key]=str(int(value))
        elif key=='https':
            if not isinstance(value,bool):invalid(key)
            row[key]=value
        else:
            if not isinstance(value,str) or len(value)>(4096 if key in SECRETS else 2048) or '\x00' in value:invalid(key)
            if key not in SECRETS:
                value=value.strip()
                if any(ord(c)<32 or ord(c)==127 for c in value):invalid(key)
            row[key]=value
    host=row['ip'].strip('[]')
    try:host=ipaddress.ip_address(host).compressed
    except ValueError:
        try:host=host.rstrip('.').encode('idna').decode('ascii').lower()
        except UnicodeError:invalid('ip')
        if not host or len(host)>253 or any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?',part) for part in host.split('.')):invalid('ip')
    if host.casefold()=='local':invalid('ip')
    row['ip']=host
    if not row['name']:row['name']=host+':'+row['port']
    if len(row['name'])>128 or row['name'].casefold()=='local':invalid('name')
    if not re.fullmatch(r'[A-Za-z0-9._~-]{1,128}',row['ssh_user']):invalid('ssh_user')
    if not row['shell'].startswith('/') or row['shell'].startswith('//'):invalid('shell')
    if 'password' in row:
        if 'pass' in row and row['pass']!=row['password']:invalid('pass/password')
        row['pass']=row.pop('password')
    return row


def load_imported(store):
    from .target_store import records
    return records(store,'mcp')


def differing_fields(current, incoming):
    """Return field names only; imported credential values never leave storage."""
    source={key:value for key,value in current.items() if key in FIELDS}
    try:
        source=normalize(source,0)
    except ValueError:
        # Keep malformed historic records visible while still reporting safe,
        # field-name-only differences for the values that can be compared.
        source={key:value for key,value in source.items() if key in FIELDS}
    return sorted(key for key,value in incoming.items()
                  if key in FIELDS and source.get(key)!=value)


def import_targets(store,records,on_duplicate='skip'):
    from .target_store import import_targets as add
    return add(store,records,on_duplicate)


def delete_imported_target(store,identity):
    from .target_store import delete_target
    return delete_target(store,identity,source='mcp')
