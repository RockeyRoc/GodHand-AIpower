"""Shared frozen/source dispatch. Worker mode never imports Tk."""
import argparse
import json
from pathlib import Path
import signal
import time

def _main(argv=None):
    parser=argparse.ArgumentParser(prog='GodHand')
    parser.add_argument('--worker',action='store_true');parser.add_argument('--request')
    parser.add_argument('--mcp',action='store_true');parser.add_argument('--doctor',action='store_true')
    parser.add_argument('--check-ui',action='store_true');parser.add_argument('--data-dir')
    args=parser.parse_args(argv)
    if args.worker:
        if not args.request:parser.error('--worker requires --request')
        from .worker import main as worker
        return worker(args.request)
    from .core import Store
    store=Store(data_dir=args.data_dir)
    if args.doctor:
        from .operations import catalogue
        from 工具管理模块.registry import Registry
        print(json.dumps({'version':'1.2.0','operations':len(catalogue()),'data_dir':str(store.path),'tools':Registry(store).status()},ensure_ascii=False,indent=2));return 0
    if args.check_ui:
        import os
        os.environ['GODHAND_CHECK_UI']='1'
        if args.data_dir:os.environ['GODHAND_DATA_DIR']=args.data_dir
        import main_work
        main_work.AWDToolkit();return 0
    if args.mcp:
        from .server import Hub
        hub=Hub(store);hub.start()
        def stop(*_):hub.stop()
        signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
        try:
            while hub.alive:time.sleep(.25)
        finally:hub.stop()
        return 0
    return 0

def main(argv=None):
    try:return _main(argv)
    except Exception:
        import traceback
        from .core import app_root,redact
        with (app_root()/'god_error.log').open('a',encoding='utf-8') as log:log.write(redact(traceback.format_exc())+'\n')
        return 1
