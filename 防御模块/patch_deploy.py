#!/usr/bin/env python3
"""
GodHand 补丁生成 + 部署模块
根据漏洞发现自动生成补丁代码，通过webshell/SSH部署到靶机
"""
import os, json, base64, re, requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_FILE = os.path.join(BASE, '防御模块', 'fix_library.json')
DEPLOY_LOG = os.path.join(BASE, '防御模块', 'patch_deploy_log.json')

PATCH_TEMPLATES = {'php-waf': "<?php\n// Defensive request rules - {patch_id}\n$__YL_RULES = json_decode('{rules_json}', true);\nif (!is_array($__YL_RULES)) {{\n    http_response_code(500); exit('Invalid defensive rule configuration');\n}}\n$__YL_METHOD = $_SERVER['REQUEST_METHOD'] ?? 'GET';\nforeach ($__YL_RULES as $rule) {{\n    if (($rule['method'] ?? 'GET') !== $__YL_METHOD) continue;\n    $source = $__YL_METHOD === 'POST' ? $_POST : $_GET;\n    $param = $rule['param'];\n    if (!array_key_exists($param, $source)) continue;\n    if (!is_string($source[$param])) {{\n        http_response_code(403); exit('Blocked invalid parameter');\n    }}\n    $pattern = '~' . str_replace('~', '\\\\~', $rule['pattern']) . '~i';\n    $match = @preg_match($pattern, $source[$param]);\n    if ($match === false) {{\n        http_response_code(500); exit('Invalid defensive rule pattern');\n    }}\n    if ($match === 1) {{\n        http_response_code(403); exit('Request blocked by defensive rule');\n    }}\n}}\n", 'php-harden': "<?php\n// GodHand Hardening - {patch_id}\n// Disable dangerous functions & features\nini_set('display_errors', 0);\nini_set('allow_url_include', 0);\nini_set('allow_url_fopen', 0);\nini_set('session.use_only_cookies', 1);\nini_set('session.cookie_httponly', 1);\nini_set('expose_php', 0);\n\n// Disable dangerous functions at runtime\n$__YL_DISABLED = [{disabled_funcs}];\nforeach ($__YL_DISABLED as $f) {{\n    if (function_exists($f)) {{\n        // Cannot actually disable at runtime, but this serves as a marker\n    }}\n}}\n"}


class PatchGenerator:
    def __init__(self):
        with open(LIB_FILE, encoding='utf-8') as f:
            self.lib = json.load(f)['vulnerabilities']

    def generate_patches(self, findings):
        patches = []
        by_vuln = {}
        for f in findings:
            vid = f['vuln_id']
            if vid not in by_vuln:
                by_vuln[vid] = []
            by_vuln[vid].append(f)

        for vid, items in by_vuln.items():
            vuln_info = None
            for v in self.lib:
                if v['id'] == vid:
                    vuln_info = v
                    break
            if not vuln_info:
                continue

            patch = {
                'patch_id': f"YL-{vid}-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                'vuln_id': vid,
                'name': vuln_info['name'],
                'severity': vuln_info['severity'],
                'count': len(items),
                'fix_fast': vuln_info['fix']['fast'],
                'fix_secure': vuln_info['fix']['secure'],
                'code_fast': vuln_info['fix'].get('code_fast', ''),
                'code_secure': vuln_info['fix'].get('code_secure', ''),
                'waf_rule': vuln_info['fix'].get('waf_rule', ''),
                'affecting_files': [
                    {'file': f.get('remote_file', f.get('file', '?')), 'line': f['line'], 'match': f['match']}
                    for f in items
                ],
                'timestamp': datetime.now().isoformat(),
            }
            patches.append(patch)

        return patches

    def generate_waf_patch(self, patches):
        if not patches:
            return None
        rules = []
        seen = set()
        for patch in patches:
            pattern = patch.get('waf_rule')
            if not isinstance(pattern, str) or not pattern:
                continue
            for finding in patch.get('affecting_files', []):
                parameter, method = 'id', 'GET'
                match = re.search(r"\$_(GET|POST)\s*\[\s*['\"]([^'\"]+)['\"]\s*\]", finding.get('match', ''))
                if match:
                    method, parameter = match.groups()
                key = (pattern, method, parameter)
                if key not in seen:
                    seen.add(key)
                    rules.append({'name': str(patch.get('name', 'rule')), 'pattern': pattern,
                                  'param': parameter, 'method': method})
        if not rules:
            return None
        encoded = json.dumps(rules, ensure_ascii=True).replace('\\', '\\\\').replace("'", "\\'")
        identifier = re.sub(r'[^a-zA-Z0-9_.-]', '_', str(patches[0].get('patch_id', 'local')))
        return PATCH_TEMPLATES['php-waf'].format(patch_id=identifier, rules_json=encoded)


class PatchDeployer:
    def __init__(self):
        self.deploy_log = []
        if os.path.exists(DEPLOY_LOG):
            try:
                with open(DEPLOY_LOG) as f:
                    self.deploy_log = json.load(f)
            except:
                self.deploy_log = []

    def deploy_via_webshell(self, ip, port, shell_url, shell_pass, content, filename, https=False, timeout=10):
        proto = 'https' if https else 'http'
        base = f'{proto}://{ip}:{port}' if port != 80 else f'{proto}://{ip}'
        ws_url = base + shell_url
        session = requests.Session()
        session.headers.update({'User-Agent': 'Mozilla/5.0'})

        b64_content = base64.b64encode(content.encode()).decode()
        cmd = f"file_put_contents('{filename}', base64_decode('{b64_content}')); chmod('{filename}', 0644); echo 'OK';"

        try:
            r = session.post(ws_url, data={shell_pass: cmd}, timeout=timeout)
            success = r.status_code == 200 and 'OK' in r.text
        except:
            success = False

        record = {
            'ip': ip, 'filename': filename, 'success': success,
            'timestamp': datetime.now().isoformat(),
        }
        self.deploy_log.append(record)
        with open(DEPLOY_LOG, 'w') as f:
            json.dump(self.deploy_log, f, indent=2)

        return success

    def deploy_auto_fix(self, ip, port, shell_url, shell_pass, patches, https=False):
        results = []
        for p in patches:
            for af in p['affecting_files']:
                src_file = af['file']
                backup_name = src_file + '.bak'
                # 备份原文件
                cmd = f"cp {src_file} {backup_name}; echo 'OK';"
                session = requests.Session()
                session.headers.update({'User-Agent': 'Mozilla/5.0'})
                proto = 'https' if https else 'http'
                base = f'{proto}://{ip}:{port}' if port != 80 else f'{proto}://{ip}'
                try:
                    r = session.post(base + shell_url, data={shell_pass: cmd}, timeout=10)
                    backed_up = 'OK' in r.text
                except:
                    backed_up = False

                results.append({
                    'file': src_file,
                    'backup': backup_name,
                    'backed_up': backed_up,
                    'patch_id': p['patch_id'],
                    'fix_fast': p['fix_fast'],
                    'fix_secure': p['fix_secure'],
                })
        return results


def auto_fix(ip, port, shell_url, shell_pass, findings, https=False):
    pg = PatchGenerator()
    patches = pg.generate_patches(findings)
    if not patches:
        return [], []

    waf_code = pg.generate_waf_patch(patches)
    if not waf_code:
        return patches, []
    pd = PatchDeployer()

    deploy_results = []
    # 部署 WAF 补丁
    if waf_code:
        ok = pd.deploy_via_webshell(ip, port, shell_url, shell_pass, waf_code,
                                     '/var/www/html/.yl_phantom_patch.php', https)
        deploy_results.append({'type': 'waf', 'file': '.yl_phantom_patch.php', 'success': ok})

    # 部署 user.ini 自动加载补丁
    user_ini = "auto_prepend_file = /var/www/html/.yl_phantom_patch.php\n"
    ok = pd.deploy_via_webshell(ip, port, shell_url, shell_pass, user_ini,
                                 '/var/www/html/.user.ini', https)
    deploy_results.append({'type': 'config', 'file': '.user.ini', 'success': ok})

    return patches, deploy_results


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='GodHand 补丁部署')
    parser.add_argument('--ip', required=True)
    parser.add_argument('--port', type=int, default=80)
    parser.add_argument('--shell-url', default='/shell.php')
    parser.add_argument('--pass', dest='shell_pass', default='cmd')
    parser.add_argument('--findings', help='漏洞发现JSON文件')
    parser.add_argument('--https', action='store_true')
    args = parser.parse_args()

    if args.findings:
        with open(args.findings) as f:
            findings = json.load(f)
        patches, deploy_results = auto_fix(args.ip, args.port, args.shell_url, args.shell_pass, findings, args.https)
        print(json.dumps({'patches': patches, 'deploy_results': deploy_results}, indent=2, ensure_ascii=False))
    else:
        print('请先扫描漏洞，保存结果为JSON，再传入 --findings')
