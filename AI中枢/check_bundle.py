"""Compare bundled business bytecode with delivered source, ignoring filenames."""
import hashlib
import json
from pathlib import Path
import sys
import types

def normalize(code):
    def constant(value):
        if isinstance(value,types.CodeType):return normalize(value)
        if isinstance(value,tuple):return tuple(constant(v) for v in value)
        if isinstance(value,frozenset):return ('frozenset',tuple(sorted(repr(v) for v in value)))
        return value
    return (code.co_code,tuple(constant(v) for v in code.co_consts),code.co_names,code.co_varnames,code.co_argcount,code.co_posonlyargcount,code.co_kwonlyargcount,code.co_flags,code.co_freevars,code.co_cellvars)

def main():
    from PyInstaller.loader.pyimod01_archive import ZlibArchiveReader
    root=Path(__file__).resolve().parent.parent;reader=ZlibArchiveReader(sys.argv[1]);rows=[]
    modules=['AI中枢.'+name for name in ['core','service','worker','server','entry','snapshot','operations','business','gui','gui_bridge','submission','templates']]+['工具管理模块.registry']
    for module in modules:
        source=root/Path(*module.split('.')).with_suffix('.py');code=reader.extract(module)
        same=any(normalize(code)==normalize(compile(source.read_text(encoding='utf-8-sig'),str(source),'exec',optimize=optimization)) for optimization in (0,1,2))
        rows.append({'module':module,'source_match':same,'sha256':hashlib.sha256(source.read_bytes()).hexdigest()})
    print(json.dumps(rows,ensure_ascii=False,indent=2))
    if not all(r['source_match'] for r in rows):raise SystemExit(1)
if __name__=='__main__':main()
