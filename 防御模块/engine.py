"""Safe adapters for defensive tools already installed on authorized Linux hosts."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import ipaddress
import json
import re
import shlex
import stat
import time
from pathlib import Path


MAX_OUTPUT = 256 * 1024
MAX_CONFIG = 1024 * 1024
JAIL_CONFIG = "/etc/fail2ban/jail.d/godhand.local"
AIDE_DB = "/var/lib/aide/aide.db"
WATCHBIRD_CONFIG = "/tmp/watchbird/watchbird.conf"
ABSENT_MARKER = b"GODHAND-ABSENT-FILE\n"
WATCHBIRD_LOGS = {"under_attack_log", "web_log", "all_requests"}
WATCHBIRD_SWITCHES = {
    "waf_headers", "waf_ddos", "waf_upload", "waf_special_char", "waf_sql",
    "waf_rce", "waf_ldpreload", "waf_lfi", "waf_unserialize", "waf_flag",
    "response_content_match",
}
WORKFLOWS = {
    "host_audit": ("主机巡检", ("defense_probe", "defense_fail2ban_status", "defense_lynis_audit")),
    "web_check": ("Web 目录检查", ("defense_probe", "defense_watchbird_status", "defense_yara_scan")),
    "integrity_review": ("文件变更复核", ("defense_probe", "defense_aide_check")),
}
SOURCES = {
    "fail2ban": {"url": "https://github.com/fail2ban/fail2ban", "license": "GPL-2.0-or-later", "integration": "仅调用目标已有 fail2ban-client"},
    "watchbird": {"url": "https://github.com/leohearts/awd-watchbird", "license": "AGPL-3.0; upstream source credits CC-BY-NC-SA 4.0 component", "integration": "仅管理目标已有 Watchbird 配置、日志和 auto_prepend_file"},
    "lynis": {"url": "https://github.com/CISOfy/lynis", "license": "GPL-3.0", "integration": "仅执行目标已有 Lynis"},
    "yara": {"url": "https://github.com/VirusTotal/yara", "license": "BSD-3-Clause", "integration": "仅执行目标已有 YARA 和用户指定规则"},
    "aide": {"url": "https://github.com/aide/aide", "license": "GPL-2.0-or-later", "integration": "仅执行目标已有 AIDE"},
    "cyberstrikeai": {"url": "https://github.com/AIPentest/CyberStrikeAI", "license": "Apache-2.0", "integration": "仅参考声明式参数、流程编排和证据报告结构；无运行时代码"},
}


class DefenseError(ValueError):
    """A rejected action or invalid/unavailable remote configuration."""


def _field(kind="value", default="", required=False, description="", choices=None):
    item = {"kind": kind, "default": default, "required": required}
    if description:
        item["description"] = description
    if choices:
        item["choices"] = list(choices)
    return item


def catalogue():
    """Return the small, explicit MCP interface for defensive operations."""
    descriptions = {
        "defense_probe": ("检查远程防御组件及版本", True, {
            "webdir": _field(default="/var/www/html", description="已安装 Watchbird 的 Web 根目录。"),
        }),
        "defense_fail2ban_status": ("查询 Fail2Ban 服务、Jail 和封禁 IP", True, {}),
        "defense_fail2ban_configure": ("写入或撤销 GodHand 专属 SSH Jail；会备份配置、验证并重载 Fail2Ban", False, {
            "enabled": _field(default=False, description="首次接入默认不启用。"),
            "management_ip": _field(required=True, description="必填：GodHand 管理端/运维出口到此主机的来源 IP 或 CIDR，不是被管理主机 IP。"),
            "maxretry": _field(default=5), "findtime": _field(default=600), "bantime": _field(default=600),
        }),
        "defense_fail2ban_ban": ("封禁已存在 Jail 中的指定 IP", False, {
            "jail": _field(default="sshd", required=True), "ip": _field(required=True),
        }),
        "defense_fail2ban_unban": ("从已存在 Jail 解封指定 IP", False, {
            "jail": _field(default="sshd", required=True), "ip": _field(required=True),
        }),
        "defense_fail2ban_rollback": ("恢复指定备份的 GodHand Fail2Ban 配置", False, {
            "backup": _field(required=True, description="defense_fail2ban_configure 返回的备份路径。"),
        }),
        "defense_watchbird_status": ("检查目标已安装 Watchbird 的版本、PHP 配置和运行状态", True, {
            "webdir": _field(default="/var/www/html"),
        }),
        "defense_watchbird_logs": ("读取目标 Watchbird 防护日志；日志可能包含请求数据", True, {
            "module": _field(default="under_attack_log", choices=sorted(WATCHBIRD_LOGS)), "lines": _field(default=100),
        }),
        "defense_watchbird_stats": ("统计 Watchbird 最近日志中的请求和攻击记录", True, {}),
        "defense_watchbird_configure": ("备份后更新 Watchbird 的单项防护开关", False, {
            "key": _field(required=True, description="选取能力目录中列出的单个防护开关。", choices=sorted(WATCHBIRD_SWITCHES)),
            "enabled": _field(default=True),
        }),
        "defense_watchbird_rollback": ("恢复指定 Watchbird 配置备份", False, {"backup": _field(required=True)}),
        "defense_waf_switch": ("备份后在现有 GodHand WAF 与 Watchbird 之间切换 auto_prepend_file", False, {
            "webdir": _field(default="/var/www/html"), "engine": _field(default="watchbird", required=True, choices=("godhand", "watchbird", "off")),
        }),
        "defense_waf_rollback": ("恢复 Web 目录中 .user.ini 的指定备份", False, {
            "webdir": _field(default="/var/www/html"), "backup": _field(required=True),
        }),
        "defense_lynis_audit": ("执行已安装 Lynis 的只读主机审计", True, {}),
        "defense_yara_scan": ("用目标上已有 YARA 规则扫描指定目录", True, {
            "rules_path": _field(required=True), "scan_path": _field(default="/var/www/html"),
        }),
        "defense_aide_init": ("显式初始化 AIDE 文件完整性基线", False, {}),
        "defense_aide_check": ("检查 AIDE 文件完整性基线", True, {}),
        "defense_aide_promote": ("备份现有数据库后显式提交 AIDE 新基线", False, {}),
        "defense_workflow_run": ("按预设巡检流程收集可复核证据", True, {
            "workflow": _field(required=True, description="host_audit、web_check 或 integrity_review。", choices=sorted(WORKFLOWS)),
            "rules_path": _field(description="web_check 的 YARA 规则绝对路径。"),
            "scan_path": _field(default="/var/www/html"),
        }),
        "defense_report": ("根据已保存任务生成 JSON 与 Markdown 防御报告", True, {
            "task_ids": _field(kind="list", default=[], description="1–100 个本地防御任务 ID；留空时读取最近 100 个。"),
        }),
    }
    from AI中枢.core import OperationSpec
    output = {}
    for identity, (title, read_only, fields) in descriptions.items():
        output[identity] = OperationSpec(
            identity, title, "DefenseHandler", identity, [], read_only,
            identity == "defense_report",
            "SSH 只访问本机加密目标库中的已授权目标；远程程序不会自动安装。副作用说明见标题和参数。",
            fields,
        )
    return output


def safe_remote_path(value, label="远程路径"):
    """Accept only explicit absolute POSIX paths and reject traversal."""
    if not isinstance(value, str) or not value.startswith("/"):
        raise DefenseError(label + "必须是绝对路径")
    if any(token in value for token in ("\0", "\n", "\r")) or any(part == ".." for part in value.split("/")):
        raise DefenseError(label + "包含不允许的路径内容")
    if len(value) > 1024:
        raise DefenseError(label + "过长")
    return value


def safe_allowlist(value):
    """Normalize a nonempty whitelist of hosts and CIDR networks."""
    if not isinstance(value, str) or len(value) > 4096:
        raise DefenseError("启用 SSH 封禁前，请填写管理 IP 或 CIDR 白名单")
    entries = []
    for part in re.split(r"[\s,]+", value.strip()):
        if not part:
            continue
        try:
            item = ipaddress.ip_network(part, strict=False) if "/" in part else ipaddress.ip_address(part)
        except ValueError:
            raise DefenseError("管理白名单包含无效 IP/CIDR：" + part) from None
        if isinstance(item, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
            minimum = 8 if item.version == 4 else 32
            if item.prefixlen < minimum:
                raise DefenseError("管理白名单网段过宽；IPv4 至少使用 /8，IPv6 至少使用 /32")
            if item.network_address.is_unspecified or item.network_address.is_multicast:
                raise DefenseError("管理白名单不能包含未指定或组播地址")
        elif item.is_unspecified or item.is_multicast:
            raise DefenseError("管理白名单不能使用未指定或组播地址")
        canonical = str(item)
        if canonical not in entries:
            entries.append(canonical)
    if not entries:
        raise DefenseError("启用 SSH 封禁前，请填写管理 IP 或 CIDR 白名单")
    for loopback in ("127.0.0.1/8", "::1/128"):
        if loopback not in entries:
            entries.append(loopback)
    return entries


def safe_ip(value):
    try:
        return str(ipaddress.ip_address(value))
    except (ValueError, TypeError):
        raise DefenseError("IP 地址格式无效") from None


def _bounded_integer(value, label, minimum, maximum):
    if isinstance(value, bool):
        raise DefenseError(label + "必须是整数")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]{1,12}", value.strip()):
        result = int(value.strip())
    else:
        raise DefenseError(label + "必须是整数")
    if not minimum <= result <= maximum:
        raise DefenseError(label + "超出允许范围")
    return result


def parse_waf_flags(serialized, changes):
    """Edit only integer/bool flags in a Watchbird PHP serialized config."""
    if not isinstance(serialized, bytes) or len(serialized) > MAX_CONFIG:
        raise DefenseError("Watchbird 配置缺失或超过 1 MiB")
    if not serialized.startswith(b"O:13:\"configmanager\":"):
        raise DefenseError("配置不是 Watchbird configmanager；拒绝反序列化或改写")
    output = serialized
    for key, enabled in changes.items():
        if key not in WATCHBIRD_SWITCHES or not isinstance(enabled, bool):
            raise DefenseError("Watchbird 防护开关无效")
        name = key.encode("ascii")
        pattern = re.compile(rb"(s:" + str(len(name)).encode("ascii") + rb':"' + re.escape(name) + rb'";)([ib]:[01];)')
        matches = list(pattern.finditer(output))
        if len(matches) != 1:
            raise DefenseError("Watchbird 配置中未能唯一确认防护开关：" + key)
        match = matches[0]
        token = b"i:1;" if enabled else b"i:0;"
        output = output[:match.start()] + match.group(1) + token + output[match.end():]
    return output


def replace_auto_prepend(content, engine, webdir="/var/www/html"):
    """Change a known GodHand/Watchbird auto-prepend while preserving other lines."""
    if engine not in ("godhand", "watchbird", "off"):
        raise DefenseError("WAF 引擎仅支持 godhand、watchbird、off")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise DefenseError("PHP 加载配置不是 UTF-8；拒绝覆盖") from None
    lines = text.splitlines(keepends=True)
    found = []
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*auto_prepend_file\s*=\s*)([^\r\n;]+)(\s*(?:[;#].*)?\r?\n?)$", line, re.IGNORECASE)
        if match:
            found.append((index, match))
    if len(found) > 1:
        raise DefenseError("配置包含多个 auto_prepend_file；请先手动整理")
    if found:
        index, match = found[0]
        current_path = match.group(2).strip().strip("\"'").replace("\\", "/")
        allowed = {"waf_strong.php", "watchbird.php", webdir.rstrip("/") + "/waf_strong.php", webdir.rstrip("/") + "/watchbird.php"}
        if current_path not in allowed:
            raise DefenseError("auto_prepend_file 指向其他组件；拒绝修改")
        if engine == "off":
            lines.pop(index)
        else:
            filename = "waf_strong.php" if engine == "godhand" else "watchbird.php"
            lines[index] = match.group(1) + webdir.rstrip("/") + "/" + filename + match.group(3)
    elif engine != "off":
        newline = "\r\n" if "\r\n" in text else "\n"
        filename = "waf_strong.php" if engine == "godhand" else "watchbird.php"
        lines.append("auto_prepend_file=" + webdir.rstrip("/") + "/" + filename + newline)
    return "".join(lines).encode("utf-8")


def _parse_jails(output):
    match = re.search(r"Number of jail:\s*(\d+).*?Jail list:\s*([^\r\n]*)", output, re.S | re.I)
    if not match:
        return []
    return [name for name in re.split(r"[,\s]+", match.group(2).strip()) if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name)]


def parse_lynis(output):
    findings = []
    for line in output.splitlines():
        match = re.search(r"(?:\[\s*(WARN|WARNING|SUGGESTION|FOUND)\s*\]|\b(WARNINGS?|SUGGESTIONS?)\s*:\s*)(.*)", line, re.I)
        if match:
            text = (match.group(3) or line).strip()
            severity = "high" if (match.group(1) or match.group(2) or "").upper() == "FOUND" else "medium"
            findings.append({"source": "lynis", "severity": severity, "title": text[:300], "evidence": line[:1000]})
    return findings[:500]


def parse_yara(output):
    findings = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.lower().startswith(("yara:", "error:", "fatal:")):
            raise DefenseError(line[:1000])
        rule, _, match = line.partition(" ")
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", rule) and match:
            findings.append({"source": "yara", "severity": "high", "title": rule, "evidence": match[:1500]})
    return findings[:2000]


def parse_aide(output):
    """Convert AIDE's Added/Changed/Removed sections into file-level evidence."""
    findings = []
    section = ""
    pattern = re.compile(r"^\s*(Added|Changed|Removed)\s+(?:entries|files)\s*:\s*$", re.I)
    for raw in output.splitlines():
        line = raw.strip()
        match = pattern.match(line)
        if match:
            section = match.group(1).lower()
            continue
        if re.match(r"^(?:Changed|Added|Removed)\s+(?:entries|files)\s*:", line, re.I):
            section = ""
        if not section or not line or line.startswith("Total number"):
            continue
        if re.fullmatch(r"[-=]{3,}", line):
            continue
        findings.append({"source": "aide", "severity": "high",
                         "title": section.title() + " file: " + line[:250], "evidence": line[:1500]})
        if len(findings) >= 2000:
            break
    return findings


def normalize_watchbird_log(text):
    return str(text)[-MAX_OUTPUT:].replace("\r\n", "\n")


@contextmanager
def ssh_session(target, timeout=12, preserve_errors=False):
    import paramiko
    host = str(target.get("ip", "")).strip()
    port = target.get("ssh_port", 22)
    username = str(target.get("ssh_user", "ctf"))
    password = target.get("ssh_pass", target.get("pass", target.get("password", "")))
    if not host or host == "local":
        raise DefenseError("请选取已授权并保存 SSH 连接资料的 Linux 目标")
    try:
        port = int(port)
    except (ValueError, TypeError):
        raise DefenseError("SSH 端口无效") from None
    if not 1 <= port <= 65535:
        raise DefenseError("SSH 端口必须为 1–65535")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(hostname=host, port=port, username=username, password=password or None,
                    look_for_keys=True, allow_agent=True, timeout=timeout, banner_timeout=timeout, auth_timeout=timeout)
        yield ssh
    except Exception as exc:
        if preserve_errors:
            raise
        raise DefenseError("SSH 连接或远程操作失败：" + str(exc)[:700]) from None
    finally:
        ssh.close()


def run_remote(ssh, command, timeout=45):
    if not isinstance(command, str) or not command or len(command) > 32768:
        raise DefenseError("拒绝无效远程命令")
    stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout, get_pty=False)
    channel = stdout.channel
    deadline = time.monotonic() + timeout
    out = bytearray()
    error = bytearray()
    try:
        while not channel.exit_status_ready() or channel.recv_ready() or channel.recv_stderr_ready():
            if time.monotonic() >= deadline:
                channel.close()
                raise TimeoutError("远程命令超过 " + str(timeout) + " 秒")
            progressed = False
            if channel.recv_ready():
                chunk = channel.recv(32768)
                if chunk:
                    if len(out) < MAX_OUTPUT:
                        out.extend(chunk[:MAX_OUTPUT - len(out)])
                    progressed = True
            if channel.recv_stderr_ready():
                chunk = channel.recv_stderr(32768)
                if chunk:
                    if len(error) < 8192:
                        error.extend(chunk[:8192 - len(error)])
                    progressed = True
            if not progressed:
                time.sleep(0.01)
        code = int(channel.recv_exit_status())
    except Exception as exc:
        raise DefenseError("读取远程程序结果失败：" + str(exc)[:500]) from None
    return code, out.decode("utf-8", "replace"), error.decode("utf-8", "replace")


def _require_success(code, output, error, name):
    if code:
        detail = (output or error or name).strip()[-1400:]
        raise DefenseError(name + "失败（退出码 " + str(code) + "）: " + detail)


def _tool_version(ssh, command):
    code, output, _ = run_remote(ssh, command + " 2>&1 | head -n 1", 12)
    return output.strip()[:300] if code == 0 else ""


def _sudo(ssh, command, timeout=35):
    code, output, error = run_remote(ssh, "sudo -n " + command, timeout)
    _require_success(code, output, error, "需要无交互 sudo 权限")
    return output


def _read_file(sftp, path, maximum=MAX_CONFIG):
    handle = sftp.open(path, "rb")
    try:
        data = handle.read(maximum + 1)
    finally:
        handle.close()
    if len(data) > maximum:
        raise DefenseError("远程文件超过大小限制：" + path)
    return data


def _backup_suffix():
    from uuid import uuid4
    return ".godhand-bak-" + uuid4().hex[:12]


def _remote_atomic_write(sftp, path, content, make_backup=True):
    safe_remote_path(path)
    attributes = sftp.stat(path)
    if not stat.S_ISREG(attributes.st_mode):
        raise DefenseError("配置不是普通文件；拒绝覆盖")
    existing = _read_file(sftp, path)
    suffix = _backup_suffix()
    backup = path + suffix
    temporary = path + ".godhand-tmp-" + suffix.rsplit("-", 1)[-1]
    with sftp.open(temporary, "wb") as handle:
        handle.write(content)
        handle.flush()
    sftp.chmod(temporary, attributes.st_mode & 0o777)
    if hasattr(sftp, "posix_rename"):
        if make_backup:
            with sftp.open(backup, "xb") as handle:
                handle.write(existing)
                handle.flush()
            sftp.chmod(backup, attributes.st_mode & 0o777)
        try:
            sftp.posix_rename(temporary, path)
        except Exception:
            try:
                sftp.remove(temporary)
            except Exception:
                pass
            if make_backup:
                try:
                    sftp.remove(backup)
                except Exception:
                    pass
            raise DefenseError("SSH 服务不支持原子配置替换；原文件未变更") from None
    else:
        try:
            sftp.remove(temporary)
        except Exception:
            pass
        raise DefenseError("SSH 服务不支持原子配置替换；原文件未变更")
    return backup if make_backup else ""


def _remote_snapshot(sftp, path):
    """Create a recoverable sibling snapshot, including a marker for absent files."""
    safe_remote_path(path)
    exists = _exists(sftp, path)
    if exists:
        attributes = sftp.stat(path)
        if not stat.S_ISREG(attributes.st_mode):
            raise DefenseError("目标不是普通文件；拒绝备份或覆盖")
        content = _read_file(sftp, path)
        mode = attributes.st_mode & 0o777
    else:
        content = ABSENT_MARKER
        mode = 0o600
    backup = path + _backup_suffix()
    with sftp.open(backup, "xb") as handle:
        handle.write(content)
        handle.flush()
    sftp.chmod(backup, mode)
    return backup, exists


def _restore_snapshot(sftp, target, backup):
    safe_remote_path(target)
    safe_remote_path(backup)
    if not isinstance(backup, str) or not re.fullmatch(re.escape(target) + r"\.godhand-bak-[a-f0-9]{12}", backup):
        raise DefenseError("备份路径必须与目标配置路径匹配")
    content = _read_file(sftp, backup, MAX_CONFIG)
    if content == ABSENT_MARKER:
        if _exists(sftp, target):
            attributes = sftp.stat(target)
            if not stat.S_ISREG(attributes.st_mode):
                raise DefenseError("当前目标不是普通文件；拒绝删除")
            sftp.remove(target)
        return "removed_created_file"
    if _exists(sftp, target):
        _remote_atomic_write(sftp, target, content)
    else:
        _remote_atomic_create(sftp, target, content)
    return "restored"


def _managed_fail2ban_config(parameters):
    if not isinstance(parameters.get("enabled", False), bool):
        raise DefenseError("enabled 必须为布尔值")
    if not parameters.get("enabled"):
        return ""
    ip_allow = safe_allowlist(parameters.get("management_ip", ""))
    maxretry = _bounded_integer(parameters.get("maxretry", 5), "maxretry", 1, 100)
    findtime = _bounded_integer(parameters.get("findtime", 600), "findtime", 1, 86400)
    bantime = _bounded_integer(parameters.get("bantime", 600), "bantime", 1, 604800)
    return (
        "[godhand-ssh]\n"
        "enabled = true\nfilter = sshd\nbackend = systemd\nport = ssh\n"
        f"maxretry = {maxretry}\nfindtime = {findtime}s\nbantime = {bantime}s\n"
        "ignoreip = " + " ".join(ip_allow) + "\n"
    )


def _fail2ban_status(ssh):
    code, output, error = run_remote(ssh, "command -v fail2ban-client >/dev/null 2>&1 || { echo 'MISSING:fail2ban-client'; exit 127; }; sudo -n fail2ban-client status", 30)
    if code:
        if "MISSING:fail2ban-client" in output:
            raise FileNotFoundError("目标未安装 Fail2Ban（fail2ban-client）")
        code, output, error = run_remote(ssh, "fail2ban-client status", 25)
        _require_success(code, output, error, "Fail2Ban 状态查询（需要可访问 daemon 的权限）")
    jails = _parse_jails(output)
    if not jails and re.search(r"no jail|not running", output, re.I):
        raise DefenseError("Fail2Ban 尚未运行或没有配置 Jail")
    details = []
    for jail in jails:
        detail_code, detail, detail_error = run_remote(ssh, "sudo -n fail2ban-client status " + shlex.quote(jail), 20)
        if detail_code:
            detail_code, detail, detail_error = run_remote(ssh, "fail2ban-client status " + shlex.quote(jail), 20)
        _require_success(detail_code, detail, detail_error, "Fail2Ban Jail 状态查询")
        count = re.search(r"Currently banned:\s*(\d+)", detail, re.I)
        ip_list = re.search(r"Banned IP list:\s*([^\r\n]*)", detail, re.I)
        addresses = []
        if ip_list:
            for candidate in re.split(r"[\s,]+", ip_list.group(1).strip()):
                if candidate:
                    try:
                        addresses.append(safe_ip(candidate))
                    except DefenseError:
                        continue
        details.append({"name": jail, "currently_banned": int(count.group(1)) if count else len(addresses),
                        "banned_ips": addresses[:200], "raw": detail[-8000:]})
    version_code, version, _ = run_remote(ssh, "fail2ban-client --version 2>&1 | head -n 1", 10)
    return {"service": "fail2ban", "status": "running", "version": version.strip() if version_code == 0 else "",
            "jails": jails, "jail_details": details,
            "godhand_jail": "godhand-ssh" in jails, "raw": output[-12000:]}


def _configure_fail2ban(ssh, parameters):
    config = _managed_fail2ban_config(parameters)
    upload = "/tmp/godhand-fail2ban-" + re.sub(r"[^a-f0-9]", "", __import__("uuid").uuid4().hex[:16])
    with ssh.open_sftp() as sftp:
        with sftp.open(upload, "wb") as handle:
            handle.write(config.encode("utf-8"))
    quoted_upload = shlex.quote(upload)
    target = shlex.quote(JAIL_CONFIG)
    backup_path = JAIL_CONFIG + _backup_suffix()
    backup = shlex.quote(backup_path)
    install = f"set -u; if test -f {target}; then cp -a {target} {backup}; else printf 'GODHAND-ABSENT-FILE\\n' > {backup}; chmod 0600 {backup}; fi; "
    if config:
        install += f"install -o root -g root -m 0644 {quoted_upload} {target} && "
        install += "if fail2ban-client -t && fail2ban-client reload; then rm -f " + quoted_upload + "; echo CONFIGURED; "
        install += f"else if test -f {backup} && grep -qx 'GODHAND-ABSENT-FILE' {backup}; then rm -f {target}; elif test -f {backup}; then cp -a {backup} {target}; else rm -f {target}; fi; fail2ban-client reload >/dev/null 2>&1 || true; rm -f {quoted_upload}; exit 1; fi"
    else:
        install += f"if rm -f {target} && fail2ban-client -t && fail2ban-client reload; then rm -f {quoted_upload}; echo DISABLED; "
        install += f"else if test -f {backup} && grep -qx 'GODHAND-ABSENT-FILE' {backup}; then rm -f {target}; elif test -f {backup}; then cp -a {backup} {target}; fi; fail2ban-client reload >/dev/null 2>&1 || true; rm -f {quoted_upload}; exit 1; fi"
    code, output, error = run_remote(ssh, "sudo -n sh -c " + shlex.quote(install), 50)
    _require_success(code, output, error, "Fail2Ban 策略检查/加载")
    return {"status": "enabled" if config else "disabled", "backup": backup_path,
            "allowlist": safe_allowlist(parameters.get("management_ip", "")) if config else []}


def _configure_watchbird(ssh, parameters):
    key = parameters.get("key")
    enabled = parameters.get("enabled", True)
    if key not in WATCHBIRD_SWITCHES or not isinstance(enabled, bool):
        raise DefenseError("Watchbird 开关无效或 enabled 不是布尔值")
    with ssh.open_sftp() as sftp:
        existing = _read_file(sftp, WATCHBIRD_CONFIG)
        updated = parse_waf_flags(existing, {key: enabled})
        backup = _remote_atomic_write(sftp, WATCHBIRD_CONFIG, updated)
    return {"component": "watchbird", "changed": key, "enabled": enabled, "backup": backup}


def _rollback_watchbird(ssh, parameters):
    backup = parameters.get("backup", "")
    with ssh.open_sftp() as sftp:
        status = _restore_snapshot(sftp, WATCHBIRD_CONFIG, backup)
    return {"component": "watchbird", "status": status, "backup": backup}


def _switch_waf(ssh, parameters):
    webdir = safe_remote_path(parameters.get("webdir", "/var/www/html"), "Web 根目录").rstrip("/") or "/"
    engine = parameters.get("engine", "watchbird")
    if engine not in ("godhand", "watchbird", "off"):
        raise DefenseError("引擎仅支持 godhand、watchbird 或 off")
    with ssh.open_sftp() as sftp:
        if engine == "watchbird":
            if not _exists(sftp, webdir + "/watchbird.php"):
                raise FileNotFoundError("指定 Web 根目录没有 watchbird.php；请先安装受信任的 Watchbird 发行版")
            if not _exists(sftp, WATCHBIRD_CONFIG):
                raise FileNotFoundError("Watchbird 尚未安装或配置缺失：" + WATCHBIRD_CONFIG)
            parse_waf_flags(_read_file(sftp, WATCHBIRD_CONFIG), {})
        if engine == "godhand" and not _exists(sftp, webdir + "/waf_strong.php"):
            raise FileNotFoundError("指定 Web 根目录没有 waf_strong.php")
        if engine in ("godhand", "watchbird"):
            script = webdir + ("/watchbird.php" if engine == "watchbird" else "/waf_strong.php")
            command = "command -v php >/dev/null 2>&1 || { echo MISSING_PHP; exit 127; }; php -v 2>&1 | head -n 1; php -l " + shlex.quote(script) + " 2>&1"
            code, output, error = run_remote(ssh, command, 30)
            if code == 127 and "MISSING_PHP" in output:
                raise FileNotFoundError("目标未安装 PHP CLI，无法验证 WAF 兼容性")
            _require_success(code, output, error, "PHP 版本/语法兼容检查")
        if engine == "watchbird":
            command = "php -r 'if ($argc !== 2) exit(2); $v = unserialize(file_get_contents($argv[1]), [\"allowed_classes\" => false]); if (!($v instanceof __PHP_Incomplete_Class)) exit(3); foreach ([\"waf_sql\",\"waf_rce\",\"waf_lfi\"] as $k) if (!property_exists($v, $k)) exit(4);' " + shlex.quote(WATCHBIRD_CONFIG)
            code, output, error = run_remote(ssh, command, 20)
            _require_success(code, output, error, "Watchbird 配置安全加载检查")
        current = _read_file(sftp, webdir + "/.user.ini", MAX_CONFIG) if _exists(sftp, webdir + "/.user.ini") else b""
        replacement = replace_auto_prepend(current, engine, webdir)
        if replacement == current:
            return {"engine": engine, "changed": False, "backup": ""}
        path = webdir + "/.user.ini"
        backup, existed = _remote_snapshot(sftp, path)
        if existed:
            _remote_atomic_write(sftp, path, replacement, make_backup=False)
        else:
            _remote_atomic_create(sftp, path, replacement)
        expected = "" if engine == "off" else webdir.rstrip("/") + ("/waf_strong.php" if engine == "godhand" else "/watchbird.php")
        actual = _read_prepend(_read_file(sftp, path, 4096).decode("utf-8", "replace"))
        if actual != expected:
            if existed:
                _remote_atomic_write(sftp, path, current, make_backup=False)
            elif _exists(sftp, path):
                sftp.remove(path)
            raise DefenseError("PHP 加载配置回读校验失败；已尝试恢复切换前文件")
    return {"engine": engine, "changed": True, "backup": backup,
            "verification": "配置原子替换完成；请通过已授权 Web 目标执行业务检查。"}


def _exists(sftp, path):
    try:
        attributes = sftp.stat(path)
        if not stat.S_ISREG(attributes.st_mode):
            raise DefenseError("目标配置不是普通文件：" + path)
        return True
    except OSError:
        return False


def _remote_atomic_create(sftp, path, content):
    temporary = path + ".godhand-tmp-" + __import__("uuid").uuid4().hex[:12]
    with sftp.open(temporary, "xb") as handle:
        handle.write(content)
        handle.flush()
    sftp.chmod(temporary, 0o644)
    if not hasattr(sftp, "posix_rename"):
        sftp.remove(temporary)
        raise DefenseError("SSH 服务不支持原子配置创建")
    sftp.posix_rename(temporary, path)
    return ""


def _lynis_audit(ssh):
    code, output, error = run_remote(ssh, "command -v lynis >/dev/null 2>&1 || { echo 'MISSING:lynis'; exit 127; }; lynis audit system --quick --no-colors --quiet", 180)
    if code == 127 and "MISSING:lynis" in output:
        raise FileNotFoundError("目标未安装 Lynis")
    if code and not output.strip():
        _require_success(code, output, error, "Lynis 审计")
    findings = parse_lynis(output + "\n" + error)
    return {"component": "lynis", "version": _tool_version(ssh, "lynis show version"), "return_code": code, "findings": findings,
            "summary": "发现 " + str(len(findings)) + " 条告警/建议", "raw": output[-20000:]}


def _yara_scan(ssh, parameters):
    rules = safe_remote_path(parameters.get("rules_path", ""), "YARA rules_path")
    target = safe_remote_path(parameters.get("scan_path", "/var/www/html"), "YARA scan_path")
    command = "command -v yara >/dev/null 2>&1 || { echo 'MISSING:yara'; exit 127; }; test -r " + shlex.quote(rules) + " || { echo 'MISSING_RULES:" + rules.replace("'", "")[:256] + "'; exit 66; }; yara -r " + shlex.quote(rules) + " " + shlex.quote(target)
    code, output, error = run_remote(ssh, command, 180)
    if code == 127 and "MISSING:yara" in output:
        raise FileNotFoundError("目标未安装 YARA")
    if code == 66 and "MISSING_RULES:" in output:
        raise FileNotFoundError("目标 YARA 规则文件不可读")
    if code not in (0, 1):
        _require_success(code, output, error, "YARA 扫描")
    findings = parse_yara(output)
    return {"component": "yara", "version": _tool_version(ssh, "yara --version"), "scan_path": target, "rules_path": rules,
            "findings": findings, "summary": "匹配 " + str(len(findings)) + " 项规则",
            "raw": output[-20000:], "return_code": code}


def _aide(ssh, action):
    if action == "defense_aide_init":
        command = "command -v aide >/dev/null 2>&1 || { echo 'MISSING:aide'; exit 127; }; sudo -n aide --init"
        timeout = 240
        label = "AIDE 基线初始化"
    elif action == "defense_aide_check":
        command = "command -v aide >/dev/null 2>&1 || { echo 'MISSING:aide'; exit 127; }; sudo -n aide --check"
        timeout = 240
        label = "AIDE 基线检查"
    else:
        target = shlex.quote(AIDE_DB)
        new = shlex.quote(AIDE_DB + ".new")
        backup = shlex.quote(AIDE_DB + _backup_suffix())
        command = "command -v aide >/dev/null 2>&1 || { echo 'MISSING:aide'; exit 127; }; sudo -n sh -c " + shlex.quote(
            f"set -eu; test -f {new}; if test -f {target}; then cp -a {target} {backup}; fi; install -o root -g root -m 0600 {new} {target}; echo BACKUP={backup}"
        )
        timeout = 40
        label = "AIDE 基线提交"
    code, output, error = run_remote(ssh, command, timeout)
    if code == 127 and "MISSING:aide" in output:
        raise FileNotFoundError("目标未安装 AIDE")
    findings = parse_aide(output + "\n" + error)
    if action == "defense_aide_check" and code in (1, 2):
        return {"component": "aide", "status": "changes_detected", "return_code": code,
                "findings": findings, "raw": output[-20000:]}
    _require_success(code, output, error, label)
    return {"component": "aide", "version": _tool_version(ssh, "aide --version"), "status": "initialized" if action == "defense_aide_init" else "promoted" if action == "defense_aide_promote" else "clean",
            "return_code": code, "findings": findings, "raw": output[-20000:]}


def _watchbird_status(ssh, webdir):
    webdir = safe_remote_path(webdir, "Web 根目录").rstrip("/") or "/"
    code, output, error = run_remote(ssh, "php -v 2>&1 | head -n 1; php -l " + shlex.quote(webdir + "/watchbird.php") + " 2>&1", 25)
    with ssh.open_sftp() as sftp:
        config_exists = _exists(sftp, WATCHBIRD_CONFIG)
        script_exists = _exists(sftp, webdir + "/watchbird.php")
        prepend = _read_file(sftp, webdir + "/.user.ini", 4096).decode("utf-8", "replace") if _exists(sftp, webdir + "/.user.ini") else ""
        try:
            config_valid = bool(config_exists and parse_waf_flags(_read_file(sftp, WATCHBIRD_CONFIG), {}) is not None)
        except Exception:
            config_valid = False
    version = output.splitlines()[0].strip() if output.splitlines() else ""
    compatible = bool(code == 0 and re.search(r"No syntax errors detected", output, re.I))
    php_missing = not bool(re.search(r"^PHP\s+\d", version, re.I))
    status_error = error.strip()[-2000:]
    if php_missing:
        status_error = "目标未安装 PHP CLI，无法检查 Watchbird 语法。"
    elif script_exists and code and not status_error:
        status_error = output.strip()[-2000:]
    return {"component": "watchbird", "installed": script_exists and config_exists,
            "config_path": WATCHBIRD_CONFIG if config_exists else "",
            "config_valid": config_valid, "webdir": webdir, "php": version, "php_cli_available": not php_missing, "php_compatible": compatible,
            "php_lint": output.strip()[-3000:], "auto_prepend_file": _read_prepend(prepend),
            "error": status_error}


def _read_prepend(text):
    matches = re.findall(r"^\s*auto_prepend_file\s*=\s*([^\r\n;]+)", text, re.I | re.M)
    return matches[-1].strip() if len(matches) == 1 else ""


def _watchbird_logs(ssh, parameters):
    module = parameters.get("module", "under_attack_log")
    if module not in WATCHBIRD_LOGS:
        raise DefenseError("仅支持读取 Watchbird 的防护访问日志")
    count = _bounded_integer(parameters.get("lines", 100), "lines", 1, 1000)
    path = "/tmp/watchbird/log/" + module + ".txt"
    command = "test -r " + shlex.quote(path) + " || { echo 'MISSING_LOG'; exit 66; }; tail -n " + str(count) + " -- " + shlex.quote(path)
    code, output, error = run_remote(ssh, command, 25)
    if code == 66:
        raise FileNotFoundError("Watchbird 防护日志不可读")
    _require_success(code, output, error, "读取 Watchbird 日志")
    return {"component": "watchbird", "module": module, "log": normalize_watchbird_log(output)}


def _watchbird_stats(ssh):
    results = {}
    for module in sorted(WATCHBIRD_LOGS):
        path = "/tmp/watchbird/log/" + module + ".txt"
        command = "if test -r " + shlex.quote(path) + "; then tail -n 1000 -- " + shlex.quote(path) + "; else echo MISSING_LOG; fi"
        code, output, error = run_remote(ssh, command, 25)
        if code:
            _require_success(code, output, error, "Watchbird 日志统计")
        if output.strip() == "MISSING_LOG":
            results[module] = {"available": False, "sampled_lines": 0, "requests": 0, "attacks": 0}
            continue
        lines = output.splitlines()
        attacks = sum(1 for line in lines if re.search(r"\{Catch attack:|Catch attack:", line, re.I))
        requests = sum(1 for line in lines if re.match(r"^(?:GET|POST|HEAD|PUT|DELETE|OPTIONS|PATCH)\s+", line, re.I))
        results[module] = {"available": True, "sampled_lines": len(lines), "requests": requests,
                           "attacks": attacks, "sample_limit": 1000}
    return {"component": "watchbird", "statistics": results,
            "note": "基于每个日志最近 1000 行的样本统计；日志未读取到报告中。"}


def _probe(ssh, parameters):
    webdir = safe_remote_path(parameters.get("webdir", "/var/www/html"), "Web 根目录").rstrip("/") or "/"
    checks = ["fail2ban-client", "lynis", "yara", "aide", "php"]
    results = {}
    versions = {"fail2ban-client": "fail2ban-client --version", "lynis": "lynis show version",
                "yara": "yara --version", "aide": "aide --version", "php": "php -v"}
    for name in checks:
        command = "if command -v " + name + " >/dev/null 2>&1; then " + versions[name] + " 2>&1 | head -n 1; else echo MISSING; fi"
        code, output, _ = run_remote(ssh, command, 8)
        results[name] = output.strip() if code == 0 else "unavailable"
    with ssh.open_sftp() as sftp:
        results["watchbird"] = "installed" if _exists(sftp, webdir + "/watchbird.php") and _exists(sftp, WATCHBIRD_CONFIG) else "missing or not installed"
        results["watchbird_config"] = WATCHBIRD_CONFIG if results["watchbird"] == "installed" else ""
        results["watchbird_webdir"] = webdir
    return {"target": str(ssh.get_transport().getpeername()[0]), "components": results}


def _write_markdown(path, data):
    def public(value):
        if isinstance(value, dict):
            return {k: public(v) for k, v in value.items() if k not in ("raw", "log", "stderr", "stdout", "findings")}
        if isinstance(value, list):
            return [public(item) for item in value]
        return value
    summary = public({k: v for k, v in data.items() if k not in ("raw", "log", "findings")})
    findings = data.get("findings", [])
    lines = ["# GodHand 防御报告", "", "- 生成时间（UTC）：" + datetime.now(timezone.utc).isoformat(), "- 组件/流程：" + str(data.get("component", data.get("workflow", "防御巡检"))), "", "## 结果", "", "```json", json.dumps(summary, ensure_ascii=False, indent=2), "```", "", "## 发现", "", "| 严重性 | 来源 | 内容 | 证据 |", "|---|---|---|---|"]
    if findings:
        for item in findings[:1000]:
            title = str(item.get("title", "")).replace("|", "\\|").replace("\n", " ")
            evidence = str(item.get("evidence", ""))[:500].replace("|", "\\|").replace("\n", " ")
            lines.append("| " + str(item.get("severity", "info")) + " | " + str(item.get("source", "defense")) + " | " + title + " | " + evidence + " |")
    else:
        lines.append("| 无 | defense | 未报告结构化发现。请结合原始证据判断。 | |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _report_history(request, task_ids):
    import sqlite3
    from AI中枢.core import redact
    data_dir = Path(request["data_dir"])
    if task_ids:
        if not isinstance(task_ids, list) or len(task_ids) > 100 or any(not re.fullmatch(r"[0-9a-f]{32}", str(item)) for item in task_ids):
            raise DefenseError("task_ids 须为最多 100 个 GodHand 任务 ID")
        placeholders = ",".join("?" for _ in task_ids)
        query = "SELECT id,operation,target,status,result,created,updated FROM tasks WHERE id IN (" + placeholders + ") ORDER BY created DESC"
        args = task_ids
    else:
        query = "SELECT id,operation,target,status,result,created,updated FROM tasks WHERE operation LIKE 'defense_%' ORDER BY created DESC LIMIT 100"
        args = []
    with sqlite3.connect(data_dir / "tasks.sqlite3") as connection:
        rows = connection.execute(query, args).fetchall()
    results = []
    for identity, operation, target, status, payload, created, updated in rows:
        if not str(operation).startswith("defense_"):
            raise DefenseError("报告只能引用防御任务：" + str(identity))
        try:
            result = json.loads(payload) if payload else {}
        except (json.JSONDecodeError, TypeError):
            result = {}
        def safe_report_value(value):
            if isinstance(value, dict):
                private_fields = {"raw", "log", "stdout", "stderr", "headers", "request", "request_body", "response_body", "cookies"}
                return {key: safe_report_value(item) for key, item in value.items() if str(key).casefold() not in private_fields}
            if isinstance(value, list):
                return [safe_report_value(item) for item in value]
            return value

        results.append({"task_id": identity, "operation": operation, "target": target, "status": status,
                        "created": created, "created_at_utc": datetime.fromtimestamp(created, timezone.utc).isoformat() if created else "",
                        "updated": updated, "updated_at_utc": datetime.fromtimestamp(updated, timezone.utc).isoformat() if updated else "",
                        "result": safe_report_value(redact(result))})
    return results


def execute(identity, target, parameters, env, client_factory=ssh_session):
    """Run a fixed defensive action against its task-bound SSH target."""
    from AI中枢.core import atomic_json
    if identity == "defense_report":
        tasks = _report_history(env.request, parameters.get("task_ids", []))
        findings = []
        for task in tasks:
            if task.get("operation") == "defense_report":
                continue
            task_data = (task.get("result") or {}).get("data") or {}
            returned = task_data.get("return") or {}
            task_findings = returned.get("findings", []) if isinstance(returned, dict) else []
            for finding in task_findings:
                item = dict(finding)
                item.setdefault("target", task.get("target", ""))
                item.setdefault("task_id", task.get("task_id", ""))
                item.setdefault("evidence_id", task.get("operation", "defense") + ":" + str(len(findings) + 1))
                findings.append(item)
        from AI中枢.version import VERSION
        data = {"product_version": VERSION, "component": "defense", "report_type": "task_history", "sources": SOURCES,
                "generated_at_utc": datetime.now(timezone.utc).isoformat(), "tasks": tasks,
                "findings": findings, "summary": "已汇总 " + str(len(tasks)) + " 个防御任务；" + str(len(findings)) + " 项发现"}
        json_path = env.path / "defense-report.json"
        md_path = env.path / "defense-report.md"
        atomic_json(json_path, data)
        _write_markdown(md_path, data)
        env.artifact_dirs.append(env.path)
        return {"return": data, "outputs": {"json_report": str(json_path), "markdown_report": str(md_path)}, "artifacts": [str(json_path), str(md_path)]}

    if identity == "defense_workflow_run":
        workflow = parameters.get("workflow")
        if workflow not in WORKFLOWS:
            raise DefenseError("workflow 仅支持 host_audit、web_check、integrity_review")
        title, steps = WORKFLOWS[workflow]
    else:
        title, steps = "", (identity,)

    collected = []
    with client_factory(target) as ssh:
        if title:
            for step in steps:
                if step == "defense_yara_scan" and not parameters.get("rules_path"):
                    collected.append({"operation": step, "status": "skipped", "reason": "web_check 流程需要提供 rules_path"})
                    continue
                try:
                    result = _execute_one(ssh, step, parameters)
                    collected.append({"operation": step, "status": "succeeded", "result": result})
                except Exception as exc:
                    collected.append({"operation": step, "status": "failed", "reason": str(exc)[:1200]})
        else:
            collected.append({"operation": identity, "status": "succeeded", "result": _execute_one(ssh, identity, parameters)})

    failures = [item for item in collected if item["status"] in ("failed", "skipped")]
    findings = []
    for item in collected:
        findings.extend((item.get("result") or {}).get("findings", []))
    from AI中枢.version import VERSION
    timestamp = datetime.now(timezone.utc).isoformat()
    for item in collected:
        result = item.get("result") or {}
        for index, finding in enumerate(result.get("findings", []), 1):
            finding.setdefault("target", target.get("ip", ""))
            finding.setdefault("observed_at_utc", timestamp)
            finding.setdefault("evidence_id", str(item.get("operation", "defense")) + ":" + str(index))
    data = {"product_version": VERSION, "component": "defense", "workflow": title or identity, "target": target.get("ip", ""),
            "sources": SOURCES,
            "timestamp_utc": timestamp, "steps": collected,
            "findings": findings, "summary": str(len(collected) - len(failures)) + "/" + str(len(collected)) + " 步骤完成；" + str(len(findings)) + " 项发现"}
    report = env.path / "defense-report.md"
    _write_markdown(report, data)
    env.artifact_dirs.append(env.path)
    return {"return": data, "failed": bool(failures), "outputs": {"findings": findings, "report_markdown": str(report)}, "artifacts": [str(report)]}


def _execute_one(ssh, identity, parameters):
    if identity == "defense_probe":
        return _probe(ssh, parameters)
    if identity == "defense_fail2ban_status":
        return _fail2ban_status(ssh)
    if identity == "defense_fail2ban_configure":
        return _configure_fail2ban(ssh, parameters)
    if identity in ("defense_fail2ban_ban", "defense_fail2ban_unban"):
        jail = parameters.get("jail", "sshd")
        if not isinstance(jail, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", jail):
            raise DefenseError("Jail 名称无效")
        address = safe_ip(parameters.get("ip", ""))
        current = _fail2ban_status(ssh)
        if jail not in current["jails"]:
            raise DefenseError("目标未配置指定 Jail：" + jail)
        action = "banip" if identity.endswith("_ban") else "unbanip"
        quoted = "fail2ban-client set " + shlex.quote(jail) + " " + action + " " + shlex.quote(address)
        output = _sudo(ssh, quoted, 25)
        return {"jail": jail, "ip": address, "action": action, "output": output[-2000:]}
    if identity == "defense_fail2ban_rollback":
        backup = parameters.get("backup", "")
        if not isinstance(backup, str) or not re.fullmatch(re.escape(JAIL_CONFIG) + r"\.godhand-bak-[a-f0-9]{12}", backup):
            raise DefenseError("备份路径必须来自 GodHand Fail2Ban 配置返回值")
        target = shlex.quote(JAIL_CONFIG)
        source = shlex.quote(backup)
        restore = f"test -f {source}; if grep -qx 'GODHAND-ABSENT-FILE' {source}; then rm -f {target}; else install -o root -g root -m 0644 {source} {target}; fi; fail2ban-client -t && fail2ban-client reload"
        output = _sudo(ssh, "sh -c " + shlex.quote(restore), 45)
        return {"backup": backup, "output": output[-2000:], "status": "restored"}
    if identity == "defense_watchbird_status":
        return _watchbird_status(ssh, parameters.get("webdir", "/var/www/html"))
    if identity == "defense_watchbird_logs":
        return _watchbird_logs(ssh, parameters)
    if identity == "defense_watchbird_stats":
        return _watchbird_stats(ssh)
    if identity == "defense_watchbird_configure":
        return _configure_watchbird(ssh, parameters)
    if identity == "defense_watchbird_rollback":
        return _rollback_watchbird(ssh, parameters)
    if identity == "defense_waf_switch":
        return _switch_waf(ssh, parameters)
    if identity == "defense_waf_rollback":
        webdir = safe_remote_path(parameters.get("webdir", "/var/www/html"), "Web 根目录").rstrip("/") or "/"
        backup = parameters.get("backup", "")
        with ssh.open_sftp() as sftp:
            status = _restore_snapshot(sftp, webdir + "/.user.ini", backup)
        return {"component": "waf", "status": status, "backup": backup}
    if identity == "defense_lynis_audit":
        return _lynis_audit(ssh)
    if identity == "defense_yara_scan":
        return _yara_scan(ssh, parameters)
    if identity in ("defense_aide_init", "defense_aide_check", "defense_aide_promote"):
        return _aide(ssh, identity)
    raise DefenseError("未知防御能力：" + str(identity))
