"""Explicit Windows executable/script definitions and dependency checks."""
from dataclasses import dataclass, asdict, field
import json
from pathlib import Path
import re
import shutil
import sys
import copy

from AI中枢.core import atomic_json, redact
from .paths import resolve


@dataclass
class ToolSpec:
    id: str
    name: str
    kind: str = 'program'
    executable: str = ''
    arguments: list = field(default_factory=list)
    working_directory: str = ''
    parameters: dict = field(default_factory=dict)
    dependencies: list = field(default_factory=list)
    timeout: float = 120
    read_only: bool = False


class Registry:
    def __init__(self,store):
        self.store=store;self.path=store.path/'tools.json'
        self.defaults=[
            ToolSpec('tabby','Tabby SSH 终端','gui',read_only=True),
            ToolSpec('winscp','WinSCP 文件管理','gui',read_only=True),
            ToolSpec('python_script','Python 脚本','script',resolve('python',store.root,store.path),['{file}'],parameters={'file':{'type':'string','description':'本机脚本绝对路径'}},dependencies=['file']),
            ToolSpec('php_script','PHP 脚本','script',resolve('php',store.root,store.path),['{file}'],parameters={'file':{'type':'string'}},dependencies=['file']),
            ToolSpec('powershell_script','PowerShell 脚本','script',resolve('powershell',store.root,store.path), ['-NoProfile','-File','{file}'],parameters={'file':{'type':'string'}},dependencies=['file'])]

    def custom(self):
        raw=json.loads(self.path.read_text(encoding='utf-8-sig')) if self.path.exists() else []
        if not isinstance(raw,list):raise ValueError('工具清单应为数组，原文件已保留')
        return [ToolSpec(**r) for r in raw]

    def all(self):
        from AI中枢.operations import catalogue
        for spec in self.defaults:
            if spec.id.endswith('_script'):spec.executable=resolve(spec.id.removesuffix('_script'),self.store.root,self.store.path)
        builtins=[ToolSpec(s.id,s.title,'builtin',arguments=[s.id],parameters=s.fields,read_only=s.read_only) for s in catalogue().values()]
        return self.defaults+builtins+self.custom()
    def get(self,identity):
        for item in self.all():
            if item.id==identity:return copy.deepcopy(item)
        raise ValueError('未知工具：'+identity)

    def save(self,record):
        spec=ToolSpec(**record)
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}',spec.id):raise ValueError('工具标识只支持小写字母、数字、下划线和连字符')
        if spec.kind not in ['program','script','gui']:raise ValueError('自定义工具类型应为 program、script 或 gui')
        if not isinstance(spec.arguments,list) or not all(isinstance(v,str) for v in spec.arguments):raise ValueError('启动参数应为字符串数组')
        if not 0<float(spec.timeout)<=86400:raise ValueError('超时应在 0..86400 秒')
        if spec.id in [s.id for s in self.defaults]:raise ValueError('内置工具不能覆盖；可另存为新标识')
        rows=[asdict(s) for s in self.custom() if s.id!=spec.id]+[asdict(spec)]
        atomic_json(self.path,rows);return asdict(spec)

    def remove(self,identity):
        rows=[asdict(s) for s in self.custom() if s.id!=identity]
        atomic_json(self.path,rows)

    def check_dependencies(self,spec):
        if not isinstance(spec.dependencies,list):raise ValueError('dependencies 必须是数组')
        for dependency in spec.dependencies:
            if dependency=='file':continue  # Parameter-specific file checked on submission.
            if not isinstance(dependency,str):raise ValueError('依赖项应为程序名或绝对路径')
            candidate=Path(dependency)
            if not candidate.is_absolute():candidate=self.store.root/candidate
            if not candidate.exists() and not shutil.which(dependency):raise FileNotFoundError('缺少依赖：'+dependency)

    def command(self,spec,parameters):
        self.check_dependencies(spec)
        if not spec.executable:raise FileNotFoundError('未配置程序／解释器路径：'+spec.name)
        executable=shutil.which(spec.executable) or spec.executable
        if not Path(executable).is_file():raise FileNotFoundError('程序不存在：'+spec.executable)
        args=[]
        for template in spec.arguments:
            found=re.fullmatch(r'\{([A-Za-z_][A-Za-z0-9_]*)\}',template)
            if found:
                key=found.group(1)
                if key not in parameters:raise ValueError('缺少参数：'+key)
                args.append(str(parameters[key]))
            elif '{' in template or '}' in template:raise ValueError('参数占位符必须独立成一个参数项')
            else:args.append(template)
        if spec.kind=='script':
            script=Path(parameters.get('file','')).resolve()
            if not script.is_file():raise FileNotFoundError('脚本文件不存在')
        extra=parameters.get('args',[])
        if not isinstance(extra,list) or not all(isinstance(v,str) for v in extra):raise ValueError('args 必须是字符串数组')
        cwd=Path(spec.working_directory or self.store.root).resolve()
        if not cwd.is_dir():raise FileNotFoundError('工作目录不存在')
        return [str(executable),*args,*extra],cwd

    def status(self):
        result=[]
        from tool_launchers import _find_executable
        for spec in self.all():
            row=asdict(spec);row['available']=False
            try:
                self.check_dependencies(spec)
                if spec.kind=='gui' and spec.id in ('tabby','winscp'):
                    row['executable']=str(_find_executable('Tabby' if spec.id=='tabby' else 'WinSCP','Tabby.exe' if spec.id=='tabby' else 'WinSCP.exe',self.store.root))
                elif spec.kind=='builtin':
                    if 'phpggc' in spec.id:
                        php=resolve('php',self.store.root,self.store.path)
                        if not php or not Path(php).is_file():raise FileNotFoundError('缺少 PHP CLI；请在“工具路径”选择 php.exe')
                        phpggc=resolve('phpggc',self.store.root,self.store.path)
                        if not phpggc or not Path(phpggc).is_file():raise FileNotFoundError('缺少 phpggc；请在“工具路径”选择 phpggc 文件')
                elif not spec.executable or not Path(shutil.which(spec.executable) or spec.executable).is_file():
                    raise FileNotFoundError('未找到程序／解释器：'+(spec.executable or spec.id))
                row['available']=True;row['reason']='可启动；运行时检查具体参数和依赖文件'
            except Exception as exc:row['reason']=str(exc)
            result.append(redact(row))
        return result

