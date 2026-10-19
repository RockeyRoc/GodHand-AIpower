"""Safe launch adapters for the optional portable SSH tools."""
from dataclasses import dataclass
import ipaddress
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import quote


class ToolLaunchError(RuntimeError):
    """A user-facing failure to locate or start an external tool."""


@dataclass(frozen=True)
class SSHConnection:
    host: str
    port: int
    username: str = ""
    label: str = ""

    def __post_init__(self):
        object.__setattr__(self, "host", _normalise_host(self.host))
        try:
            port = int(self.port)
        except (TypeError, ValueError):
            raise ValueError("SSH 端口必须是 1 到 65535 之间的整数")
        if isinstance(self.port, bool) or not 1 <= port <= 65535:
            raise ValueError("SSH 端口必须是 1 到 65535 之间的整数")
        object.__setattr__(self, "port", port)
        username = str(self.username or "").strip()
        if username and not re.fullmatch(r"[A-Za-z0-9._~-]+", username):
            raise ValueError("SSH 用户名仅支持字母、数字和 . _ - ~ 字符")
        object.__setattr__(self, "username", username)
        object.__setattr__(self, "label", str(self.label or "").strip())

    @classmethod
    def from_machine(cls, machine):
        if not isinstance(machine, dict):
            raise ValueError("请选择有效的主机记录")

        host = _normalise_host(machine.get("ip") or machine.get("ssh_host") or "")
        raw_port = str(machine.get("ssh_port") or "2202").strip()
        try:
            port = int(raw_port)
        except (TypeError, ValueError):
            raise ValueError("SSH 端口必须是 1 到 65535 之间的整数")
        if not 1 <= port <= 65535:
            raise ValueError("SSH 端口必须是 1 到 65535 之间的整数")

        username = str(machine.get("ssh_user") or "ctf").strip()
        label = str(machine.get("name") or "").strip()
        return cls(host=host, port=port, username=username, label=label)


@dataclass(frozen=True)
class LaunchResult:
    tool: str
    pid: int


def _normalise_host(raw_host):
    host = str(raw_host or "").strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if not host or any(char.isspace() for char in host):
        raise ValueError("主机 IP 或域名不能为空，且不能包含空格")
    try:
        return ipaddress.ip_address(host).compressed
    except ValueError:
        if ":" in host or any(char in host for char in "@/?#%\\"):
            raise ValueError("主机地址格式无效；IPv6 请填写完整地址，不要附带端口")
        try:
            ascii_host = host.rstrip(".").encode("idna").decode("ascii")
        except UnicodeError:
            raise ValueError("主机域名格式无效")
        if not ascii_host or len(ascii_host) > 253:
            raise ValueError("主机域名格式无效")
        labels = ascii_host.split(".")
        if any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
               for label in labels):
            raise ValueError("主机域名格式无效")
        return ascii_host.lower()


def _application_dir(app_dir=None):
    return Path(app_dir).resolve() if app_dir else Path(__file__).resolve().parent


def _find_executable(tool_name, executable_name, app_dir=None):
    root = _application_dir(app_dir)
    candidates = (
        root / "tools" / tool_name / executable_name,
        root / tool_name / executable_name,
        root.parent / tool_name / executable_name,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    expected = root / "tools" / tool_name / executable_name
    raise ToolLaunchError(
        f"未找到 {tool_name}。请将工具放入便携目录的 tools\\{tool_name}，"
        f"或与 GodHand 文件夹并列放置；预期文件：{expected}"
    )


def _start_process(tool_name, executable, arguments, environment=None):
    try:
        process = subprocess.Popen(
            [str(executable), *arguments],
            cwd=str(executable.parent),
            env=environment,
            shell=False,
            close_fds=True,
        )
    except (OSError, ValueError) as exc:
        raise ToolLaunchError(f"{tool_name} 启动失败：{exc}") from exc
    return LaunchResult(tool=tool_name, pid=process.pid)


def _connection_host(connection):
    try:
        parsed = ipaddress.ip_address(connection.host)
    except ValueError:
        return connection.host
    return f"[{parsed.compressed}]" if parsed.version == 6 else parsed.compressed


def launch_terminal(connection, app_dir=None, config_dir=None):
    """Start Tabby's SSH quick-connect without passing or storing a password."""
    if not isinstance(connection, SSHConnection):
        raise TypeError("connection must be an SSHConnection")
    executable = _find_executable("Tabby", "Tabby.exe", app_dir)
    app_root = _application_dir(app_dir)
    portable_data = app_root / "tools" / "Tabby" / "data"
    if executable.parent == (app_root / "tools" / "Tabby").resolve() and portable_data.is_dir():
        tabby_config = portable_data
    else:
        tabby_config = Path(config_dir or (app_root / "管理工具")) / "Tabby"
    try:
        tabby_config.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ToolLaunchError(f"无法创建 Tabby 本地配置目录：{exc}") from exc

    host = _connection_host(connection)
    target = f"{host}:{connection.port}"
    if connection.username:
        target = f"{connection.username}@{target}"
    environment = os.environ.copy()
    environment["TABBY_CONFIG_DIRECTORY"] = str(tabby_config.resolve())
    return _start_process("Tabby 终端", executable,
                          ["quickConnect", "ssh", target], environment)


def launch_file_manager(connection, app_dir=None, config_dir=None):
    """Start an isolated WinSCP instance with an app-local INI configuration."""
    if not isinstance(connection, SSHConnection):
        raise TypeError("connection must be an SSHConnection")
    executable = _find_executable("WinSCP", "WinSCP.exe", app_dir)
    app_root = _application_dir(app_dir)
    ini_directory = Path(config_dir or (app_root / "管理工具"))
    try:
        ini_directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ToolLaunchError(f"无法创建 WinSCP 本地配置目录：{exc}") from exc

    host = _connection_host(connection)
    user = f"{quote(connection.username, safe='-._~')}@" if connection.username else ""
    session = f"sftp://{user}{host}:{connection.port}/"
    ini_path = (ini_directory / "WinSCP.ini").resolve()
    return _start_process("WinSCP 文件管理", executable,
                          ["/newinstance", f"/ini={ini_path}", session])


# Backwards-compatible descriptive aliases used by the UI and future adapters.
launch_tabby = launch_terminal
launch_winscp = launch_file_manager
