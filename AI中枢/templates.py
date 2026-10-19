"""Clean release templates; materialize keys only when explicitly requested."""
from pathlib import Path
import re
from .core import app_root,resource_root
PRIVATE=re.compile(r'-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----',re.S)
PUBLIC=re.compile(r'-----BEGIN PUBLIC KEY-----.*?-----END PUBLIC KEY-----',re.S)
def clean(text):return PUBLIC.sub('__GODHAND_RSA_PUBLIC__',PRIVATE.sub('__GODHAND_RSA_PRIVATE__',text))
def render(name,destination):
    if Path(name).name!=name:raise ValueError('template 只接受模板文件名')
    source=resource_root()/'上传靶机'/name
    if not source.is_file():raise FileNotFoundError('模板不存在：'+name)
    text=clean(source.read_text(encoding='utf-8-sig'))
    if '__GODHAND_RSA_' in text:
        import sys
        sys.path.insert(0,str(resource_root()/'攻击模块'))
        from rsa_crypto import load_keys
        keys=load_keys();text=text.replace('__GODHAND_RSA_PRIVATE__',keys['private'].strip()).replace('__GODHAND_RSA_PUBLIC__',keys['public'].strip())
    output=Path(destination);output.parent.mkdir(parents=True,exist_ok=True);output.write_text(text,encoding='utf-8')
    return {'artifact':str(output),'template':name}
