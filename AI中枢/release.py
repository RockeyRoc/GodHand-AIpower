"""Explicit release inputs shared by both build entrypoints."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
from .core import app_root,atomic_json
from .templates import clean,PRIVATE
from .version import VERSION,numeric

TEMPLATES=['watchdog.sh','watchdog.php','waf_strong.php','waf_ban.php','waf_admin.php','tinyfilemanager.php','target_monitor.php','rsa_agent.php','immortal_shell.php','creep_rsa_ae.php','creep_rsa.php','.waf.dat.php','.waf.dat','.creep.dat','.creep.php']
MODULES={'攻击模块':['batch_runner.py','dfunc_bypass.py','mix_attack.py','phpggc_wrapper.py','rsa_crypto.py'],'防御模块':['__init__.py','auto_fix.py','patch_deploy.py','fix_library.json','engine.py','aoi.py','aoi_bundle.py','SOURCES.md']}
ROOT_FILES=['main_work.py','main.py','dashboard.py','tool_launchers.py','build_exe.py','build_work.py','logo.ico','logo.png','icon.png','README.md','skill.md','使用说明.txt','requirements.lock.txt']

def source_manifest(root):
    files=[Path(n) for n in ROOT_FILES]
    for directory,names in MODULES.items():files.extend(Path(directory)/n for n in names)
    for directory in ['AI中枢','工具管理模块']:
        files.extend(p.relative_to(root) for p in (root/directory).glob('*.py') if p.name!='prepare_gui.py')
        files.extend(p.relative_to(root) for p in (root/directory).glob('*.json'))
    files.extend(p.relative_to(root) for p in (root/'防御模块'/'aoi_assets').rglob('*') if p.is_file())
    files.extend(p.relative_to(root) for p in (root/'docs').rglob('*') if p.is_file() and 'docx' not in [part.casefold() for part in p.relative_to(root).parts[:-1]])
    return files

def verify(package):
    forbidden={'rsa_keys.json','credentials.dpapi','hub.json','machines.json','app_config.json','flag_hunter_config.json','WinSCP.ini','waf_key.txt','tool-paths.json'}
    rows=[]
    for path in package.rglob('*'):
        if not path.is_file() or path.name=='release-manifest.json':continue
        relative=path.relative_to(package)
        if any(part.casefold() in ('docx','.acceptance','.target-tests') for part in relative.parts[:-1]):raise ValueError('发布包包含排除目录：'+str(relative))
        if path.name in forbidden or path.suffix in ('.log','.sqlite3','.dpapi'):raise ValueError('发布包包含运行数据：'+str(relative))
        if path.suffix.lower() in ('.php','.py','.json','.txt','.md') and path.stat().st_size<10*1024*1024:
            if re.search(r'-----BEGIN [^-\r\n]*PRIVATE KEY-----\s+[A-Za-z0-9+/=\r\n]{80,}-----END [^-\r\n]*PRIVATE KEY-----',path.read_text(encoding='utf-8',errors='replace')):raise ValueError('发布包含历史私钥：'+str(relative))
        with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        rows.append({'path':relative.as_posix(),'size':path.stat().st_size,'sha256':digest})
    atomic_json(package/'release-manifest.json',{'version':VERSION,'files':rows,'excluded':'docx directory, personal config, RSA keys, task DB, workspaces, logs, build caches'})
    return len(rows)

def main(portable=False,argv=None):
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');parser.add_argument('--output');parser.add_argument('--cache');parser.add_argument('--tools-root');parser.add_argument('--skip-freeze',action='store_true')
    args=parser.parse_args(argv);root=app_root()
    output=Path(args.output or root.parent/'GodHand-AIpower-便携版'/('GodHand-'+VERSION)).resolve()
    cache=Path(args.cache or root.parent/'GodHand-tmp'/'mcp-build').resolve()
    files=source_manifest(root)
    expected=json.loads((root/'AI中枢'/'business_fields.json').read_text(encoding='utf-8'))['source_sha256']
    actual=hashlib.sha256((root/'main_work.py').read_text(encoding='utf-8-sig').encode()).hexdigest()
    if expected!=actual:raise ValueError('业务源已变化，请先运行 python AI中枢/generate.py')
    missing=[str(p) for p in files if not (root/p).is_file()]
    if missing:raise FileNotFoundError(', '.join(missing))
    if args.check:print(json.dumps({'entry':'main_work.py','output':str(output),'cache':str(cache),'sources':[p.as_posix() for p in files],'templates':TEMPLATES},ensure_ascii=False,indent=2));return 0
    cache.mkdir(parents=True,exist_ok=True)
    resources=cache/'resources';resources.mkdir(exist_ok=True)
    for name in TEMPLATES:
        destination=resources/'上传靶机'/name;destination.parent.mkdir(exist_ok=True)
        destination.write_text(clean((root/'上传靶机'/name).read_text(encoding='utf-8-sig')),encoding='utf-8')
    vendor=root/'AI中枢'/'vendor'
    version_file=cache/'windows-version.txt'
    major,minor,patch,build=numeric()
    version_file.write_text(f"VSVersionInfo(ffi=FixedFileInfo(filevers=({major},{minor},{patch},{build}),prodvers=({major},{minor},{patch},{build}),mask=0x3f,flags=0,OS=0x40004,fileType=1,subtype=0,date=(0,0)),kids=[StringFileInfo([StringTable('040904B0',[StringStruct('FileDescription','GodHand'),StringStruct('ProductName','GodHand'),StringStruct('FileVersion','{VERSION}.0'),StringStruct('ProductVersion','{VERSION}'),StringStruct('InternalName','GodHand'),StringStruct('OriginalFilename','GodHand.exe')])]),VarFileInfo([VarStruct('Translation',[1033,1200])])])",encoding='utf-8')
    if not args.skip_freeze:
        command=[sys.executable,'-c','import site,sys,runpy;site.addsitedir(sys.argv.pop(1));runpy.run_module("PyInstaller",run_name="__main__")',str(vendor),'--noconfirm','--clean','--onedir','--windowed','--contents-directory','_runtime','--name','GodHand','--icon',str(root/'logo.ico'),'--version-file',str(version_file),'--distpath',str(cache/'dist'),'--workpath',str(cache/'work'),'--specpath',str(cache),'--paths',str(root),'--paths',str(vendor)]
        for directory in MODULES:command.extend(['--paths',str(root/directory)])
        for module in ['AI中枢.business','AI中枢.entry','AI中枢.worker','AI中枢.gui','AI中枢.gui_bridge','AI中枢.defense_gui','AI中枢.version','AI中枢.stdio_bridge','AI中枢.clientconfig','AI中枢.target_store','AI中枢.storage','防御模块.engine','防御模块.aoi','auto_fix','patch_deploy','rsa_crypto','phpggc_wrapper','dfunc_bypass','batch_runner','mix_attack','dashboard','tool_launchers','uvicorn.logging','uvicorn.loops.asyncio','uvicorn.protocols.http.h11_impl','uvicorn.lifespan.on']:
            command.extend(['--hidden-import',module])
        for package in ['mcp_types','httpx2','httpcore2','starlette','anyio','pydantic','ttkbootstrap','paramiko','cryptography','PIL']:
            command.extend(['--collect-all',package])
        command.extend(['--collect-data','mcp','--collect-submodules','mcp.server','--collect-submodules','mcp.client','--collect-submodules','mcp.shared'])
        for package in ['mcp','mcp-types','httpx2','httpcore2','starlette','anyio','pydantic','pydantic_core','uvicorn']:
            command.extend(['--copy-metadata',package])
        command.extend(['--add-data',str(resources/'上传靶机')+os.pathsep+'上传靶机','--add-data',str(root/'AI中枢'/'business_fields.json')+os.pathsep+'AI中枢','--add-data',str(root/'logo.png')+os.pathsep+'.','--add-data',str(root/'icon.png')+os.pathsep+'.'])
        command.extend(['--add-data',str(root/'防御模块'/'aoi_assets')+os.pathsep+'防御模块/aoi_assets'])
        for directory,names in MODULES.items():
            for name in names:
                if not name.endswith('.py'):command.extend(['--add-data',str(root/directory/name)+os.pathsep+directory])
        command.append(str(root/'main_work.py'))
        with (cache/'pyinstaller.log').open('w',encoding='utf-8') as log:subprocess.run(command,cwd=root,stdout=log,stderr=subprocess.STDOUT,check=True)
    if output.exists():raise FileExistsError('请使用新发布目录，避免覆盖现有包：'+str(output))
    output.mkdir(parents=True)
    shutil.copy2(cache/'dist'/'GodHand'/'GodHand.exe',output/'GodHand.exe')
    shutil.copytree(cache/'dist'/'GodHand'/'_runtime',output/'_runtime')
    for name in ['README.md','skill.md','使用说明.txt','logo.png','logo.ico']:shutil.copy2(root/name,output/name)
    if (root/'docs').is_dir():shutil.copytree(root/'docs',output/'docs',ignore=shutil.ignore_patterns('docx','DOCX','Docx'))
    shutil.copytree(root/'防御模块'/'aoi_assets',output/'防御模块'/'aoi_assets')
    aoi=root/'tools'/'AoiAWD'
    if not (aoi/'bundle.json').is_file():raise FileNotFoundError('缺少 Windows 原生 AoiAWD 配套环境，请先运行 防御模块/aoi_bundle.py')
    shutil.copytree(aoi,output/'tools'/'AoiAWD')
    for directory,names in MODULES.items():
        for name in names:
            target=output/directory/name;target.parent.mkdir(exist_ok=True);shutil.copy2(root/directory/name,target)
    shutil.copytree(resources/'上传靶机',output/'上传靶机')
    if portable:
        tools=Path(args.tools_root) if args.tools_root else root.parent.parent/'YL_Phantom_Portable_Tools'
        if not tools.is_dir():raise FileNotFoundError('缺少 Tabby/WinSCP 工具源；请设置 --tools-root')
        for tool in ['Tabby','WinSCP']:
            shutil.copytree(tools/tool,output/'tools'/tool,ignore=shutil.ignore_patterns('data','userData','logs','*.log','WinSCP.ini','config.yaml','config.yml','storage.json','Local Storage','Session Storage'))
    count=verify(output)
    if portable:
        with zipfile.ZipFile(output.with_name(output.name+'.zip'),'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
            for path in output.rglob('*'):
                if path.is_file():archive.write(path,path.relative_to(output.parent))
        archive_path=output.with_name(output.name+'.zip')
        with archive_path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        archive_path.with_name(archive_path.name+'.sha256').write_text(digest+'  '+archive_path.name+'\n',encoding='utf-8')
    print(json.dumps({'output':str(output),'files':count},ensure_ascii=False));return 0
