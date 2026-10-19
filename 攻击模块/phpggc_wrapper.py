#!/usr/bin/env python3
import subprocess, json, os, re, tempfile, base64, urllib.parse

BASE = os.path.dirname(os.path.abspath(__file__))
PHPGGC_DIR = os.path.join(BASE, '..', '第三方工具', 'phpggc')
PHPGGC_BIN = os.path.join(PHPGGC_DIR, 'phpggc')

CHAINS_CACHE = None

def _run_phpggc(*args):
    from AI中枢.core import app_root
    from 工具管理模块.paths import resolve
    root=app_root();data_dir=os.environ.get('GODHAND_DATA_DIR')
    php=resolve('php',root,data_dir)
    phpggc=resolve('phpggc',root,data_dir)
    if not php or not os.path.isfile(php):return '', '缺少 PHP CLI：请在工具管理 → 工具路径配置 php.exe', -1
    if not phpggc or not os.path.isfile(phpggc):return '', '缺少 phpggc：请在工具管理 → 工具路径配置 phpggc 文件', -1
    cmd = [php, phpggc] + list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=os.path.dirname(phpggc), creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return r.stdout, r.stderr, r.returncode
    except subprocess.TimeoutExpired:
        return '', 'timeout', -1
    except FileNotFoundError:
        return '', 'php not found', -1

def list_chains(refresh=False):
    global CHAINS_CACHE
    if CHAINS_CACHE and not refresh:
        return CHAINS_CACHE
    out, err, rc = _run_phpggc('-l')
    if rc != 0:
        raise RuntimeError(err or 'phpggc 执行失败')
    chains = []
    for line in out.splitlines():
        m = re.match(r'^([\w/]+)\s+(\S.*?)\s+(RCE|File\s+\w+|Include|SSRF|FD|FW|FR)\s+', line)
        if m:
            chains.append({'name': m.group(1).strip(), 'version': m.group(2).strip(), 'type': m.group(3).strip()})
    CHAINS_CACHE = chains
    return chains

def chain_detail(name):
    out, err, rc = _run_phpggc('-i', name)
    if rc != 0:
        return None
    return out

def generate_payload(chain_name, *params, encoding=None):
    args = [chain_name] + list(params)
    if encoding == 'base64':
        args.append('-b')
    elif encoding == 'url':
        args.append('-u')
    elif encoding == 'json':
        args.append('-j')
    out, err, rc = _run_phpggc(*args)
    if rc != 0:
        return None, err
    return out.strip(), None

def search_chains(keyword):
    all_chains = list_chains()
    kw = keyword.lower()
    return [c for c in all_chains if kw in c['name'].lower() or kw in c['type'].lower()]

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        if sys.argv[1] == 'list':
            for c in list_chains():
                print(f"{c['name']:40s} {c['type']:20s} {c['version']}")
        elif sys.argv[1] == 'search' and len(sys.argv) > 2:
            for c in search_chains(sys.argv[2]):
                print(f"{c['name']:40s} {c['type']:20s} {c['version']}")
        elif sys.argv[1] == 'gen' and len(sys.argv) > 3:
            payload, err = generate_payload(sys.argv[2], *sys.argv[3:])
            if payload: print(payload)
            else: print(f'Error: {err}', file=sys.stderr)
    else:
        print(f'PHPGGC wrapper — {len(list_chains())} chains available')
