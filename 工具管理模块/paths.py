"""Resolve local optional tool extensions without downloads or import-time execution."""
import json
from pathlib import Path
import shutil

KEYS = ('python', 'php', 'powershell', 'phpggc')


def settings(root, data_dir=None):
    path = Path(data_dir or Path(root)/'管理工具'/'GodHand')/'tool-paths.json'
    values = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    if not isinstance(values, dict):
        raise ValueError('工具路径配置应为对象；原文件已保留')
    return values


def resolve(name, root, data_dir=None):
    root = Path(root)
    configured = settings(root, data_dir).get(name, '')
    if configured:
        path = Path(configured)
        if not path.is_absolute():path = root/path
        return str(path.resolve())
    bundled = {
        'python': ['tools/Python/python.exe'],
        'php': ['tools/PHP/php.exe'],
        'powershell': ['tools/PowerShell/pwsh.exe'],
        'phpggc': ['第三方工具/phpggc/phpggc', 'tools/phpggc/phpggc'],
    }
    for relative in bundled[name]:
        if (root/relative).is_file():return str((root/relative).resolve())
    commands = {'python': ['python'], 'php': ['php'], 'powershell': ['powershell', 'pwsh'], 'phpggc': []}
    for command in commands[name]:
        found = shutil.which(command)
        if found and 'windowsapps' not in found.lower():return found
    return ''


def save(root, data_dir, values):
    from AI中枢.core import atomic_json
    if set(values)-set(KEYS):raise ValueError('未知工具路径配置')
    for name, value in values.items():
        if not isinstance(value, str):raise ValueError('路径应为字符串')
        if value and not Path(value).is_absolute():raise ValueError('请选择绝对路径')
        if value and not Path(value).is_file():raise FileNotFoundError(name+' 文件不存在：'+value)
    atomic_json(Path(data_dir)/'tool-paths.json', values)
