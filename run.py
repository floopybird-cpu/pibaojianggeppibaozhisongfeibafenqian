from __future__ import annotations

import argparse
import json
import msvcrt
import os
from pathlib import Path
import sys
import webbrowser

from app.server import Application, make_server
from app import __version__


def main():
    parser=argparse.ArgumentParser(description="Independent local physics experiment assistant")
    parser.add_argument("--data-dir",type=Path)
    parser.add_argument("--products-dir",type=Path)
    parser.add_argument("--port",type=int,default=0)
    parser.add_argument("--no-browser",action="store_true")
    parser.add_argument("--web-dev",action="store_true",help="Development-only legacy browser viewer")
    parser.add_argument("--native-smoke",type=Path,help="Capture native startup and exit for local QA")
    args=parser.parse_args()
    directory=Path(__file__).resolve().parent
    installed = (directory / "installed.ini").is_file()
    default_data = directory / "data"
    if installed:
        from PySide6.QtCore import QStandardPaths
        default_data = Path(os.environ["LOCALAPPDATA"]) / "PhysicsExperimentAssistant" / "library"
        if args.products_dir is None:
            documents = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
            args.products_dir = Path(documents) / "物理实验助手" / "生成产物"
    data=(args.data_dir or default_data).resolve()
    data.mkdir(parents=True,exist_ok=True)
    lock=(data/"host.lock").open("a+b")
    if lock.tell()==0:lock.write(b"0");lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:
        if not args.web_dev:
            from app.native import activate_existing
            activate_existing(data);lock.close();return
        launcher=data/"authority/launcher.json"
        if launcher.exists() and not args.no_browser:
            url=json.loads(launcher.read_text(encoding="utf-8"))["url"]
            if url.startswith("http://127.0.0.1:"):webbrowser.open(url)
        print("本资料库的助手已经运行，已使用同一实例。",flush=True)
        lock.close()
        return
    if args.web_dev and not (directory/"web/dist/main.js").exists():
        raise SystemExit("前端构建缺失，请按 README 运行 npm ci 和 npm run build")
    app=Application(directory,data,products_root=args.products_dir)
    if not args.web_dev:
        from app.native import run_native
        print("Physics Harness",__version__,"native desktop ready",flush=True)
        launcher=app.store.authority/"launcher.json"
        launcher.write_text(json.dumps({"mode":"native","pid":os.getpid()}),encoding="utf-8")
        try:
            return run_native(app,args.native_smoke)
        finally:
            launcher.unlink(missing_ok=True);lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1);lock.close()
    server=make_server(app,args.port)
    url=f"http://127.0.0.1:{server.server_port}/#token={app.token}"
    (app.store.authority/"launcher.json").write_text(json.dumps({"url":url,"port":server.server_port}),encoding="utf-8")
    print("Physics Harness",__version__,"ready on local port",server.server_port,flush=True)
    print("请保留此窗口；按 Ctrl+C 真正退出。关闭网页后可重新运行启动入口找回实验。",flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=.3)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        (app.store.authority/"launcher.json").unlink(missing_ok=True)
        lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1);lock.close()


if __name__=="__main__":
    try:
        main()
    except Exception:
        import traceback
        log=Path(__file__).resolve().parent/"data/startup-error.log";log.parent.mkdir(parents=True,exist_ok=True)
        log.write_text(traceback.format_exc(),encoding="utf-8")
        raise
