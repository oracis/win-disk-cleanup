# -*- coding: utf-8 -*-
"""
win-disk-cleanup Web 应用（零依赖，纯标准库 + ctypes）。

启动:
    python app.py                 # 默认 http://127.0.0.1:5053
    PORT=8080 python app.py
    python app.py --no-elevate    # 不自动提权（普通权限运行）
    python app.py --no-tray       # 不显示系统托盘

行为:
    - 非管理员启动时自动请求 UAC 提权（--no-elevate 关闭），注册表清理等需管理员。
    - 托盘图标：右键可「打开界面 / 以管理员重启 / 退出」。失败自动降级为无托盘。

接口:
    GET  /                -> 静态首页
    GET  /api/disk        -> 盘面摸底（只读）
    POST /api/scan        -> 扫描某路径，四档分级（只读）
    POST /api/clean       -> 删除文件（需 body.confirm=true）
    GET  /api/log         -> 清理日志（最近 200 行）
    GET  /api/admin       -> 是否管理员
    GET  /api/reg         -> 扫描残留注册表项（只读）
    POST /api/reg-clean   -> 删除注册表项（需 confirm + 管理员，先备份）
    GET  /api/programs    -> 枚举已安装程序（只读）
    POST /api/uninstall   -> 启动某程序卸载器
    POST /api/appwiz      -> 打开「程序和功能」
    POST /api/elevate     -> 请求 UAC 以管理员重启
"""
import os
import sys
import json
import threading
import webbrowser
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cleanlib

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "5053"))

TIER_META = {
    "A": {"label": "A 零风险", "color": "#3fb950", "desc": "缓存/日志/残包/空目录，可直接清"},
    "B": {"label": "B 需确认", "color": "#d29922", "desc": "不确定是否安全，请人工核对"},
    "C": {"label": "C 只能卸载", "color": "#db61a2", "desc": "完整安装程序，请走卸载器"},
    "D": {"label": "D 禁删", "color": "#f85149", "desc": "个人数据/系统关键，禁止删除"},
}


def _send_json(handler, obj, code=200):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_body(handler):
    try:
        length = int(handler.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = handler.rfile.read(length)
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def _serve_static(handler, rel):
    if rel in ("", "/"):
        rel = "index.html"
    elif rel.startswith("/static/"):
        rel = rel[len("/static/"):]
    # 防目录穿越
    path = os.path.normpath(os.path.join(STATIC, rel))
    if not path.startswith(STATIC) or not os.path.isfile(path):
        handler.send_error(404, "Not found")
        return
    ctype = {
        ".html": "text/html; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".svg": "image/svg+xml",
        ".ico": "image/x-icon",
    }.get(os.path.splitext(path)[1], "application/octet-stream")
    with open(path, "rb") as f:
        data = f.read()
    handler.send_response(200)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # 静默

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/disk":
            return _send_json(self, cleanlib.get_disk_report())
        if u.path == "/api/log":
            return _send_json(self, {"lines": cleanlib.read_log()})
        if u.path == "/api/admin":
            return _send_json(self, {"admin": cleanlib.is_admin()})
        if u.path == "/api/reg":
            return _send_json(self, cleanlib.scan_registry())
        if u.path == "/api/programs":
            return _send_json(self, {"items": cleanlib.list_programs()})
        if u.path.startswith("/static/") or u.path == "/":
            return _serve_static(self, u.path)
        self.send_error(404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        body = _read_body(self)
        if u.path == "/api/scan":
            base = body.get("path", "")
            budget = int(body.get("budget", 60))
            top = int(body.get("top", 300))
            excludes = tuple(x.strip() for x in str(body.get("excludes", "")).split(",") if x.strip())
            res = cleanlib.scan_path(base, budget, top, excludes)
            return _send_json(self, res)
        if u.path == "/api/clean":
            paths = body.get("paths", [])
            confirm = bool(body.get("confirm", False))
            keep_dir = bool(body.get("keep_dir", False))
            # 安全护栏：任何 D 档一律拒绝（UI 已禁选，这里再兜底）
            safe = []
            rejected = []
            for p in paths:
                # D 档无法在后端精确重判，依赖前端不传；此处仅做基础校验
                if p and isinstance(p, str):
                    safe.append(p)
                else:
                    rejected.append(p)
            res = cleanlib.delete_paths(safe, confirm=confirm, keep_dir=keep_dir)
            res["rejected"] = rejected
            return _send_json(self, res, 200 if res.get("ok") else 400)
        if u.path == "/api/reg-clean":
            paths = body.get("paths", [])
            confirm = bool(body.get("confirm", False))
            results = []
            for p in paths:
                if isinstance(p, str) and p:
                    results.append(cleanlib.delete_registry_key(p, confirm=confirm))
            return _send_json(self, {"ok": True, "results": results})
        if u.path == "/api/uninstall":
            return _send_json(self, cleanlib.launch_uninstall(body.get("key_path", "")))
        if u.path == "/api/appwiz":
            return _send_json(self, cleanlib.open_appwiz())
        if u.path == "/api/elevate":
            ok = cleanlib.relaunch_elevated()
            if ok:
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            return _send_json(self, {"ok": ok})
        self.send_error(404)


def _should_elevate():
    if "--no-elevate" in sys.argv:
        return False
    if "--elevated" in sys.argv:
        return False
    if os.environ.get("WDC_NO_ELEVATE"):
        return False
    return not cleanlib.is_admin()


def main():
    # 管理员自动提权：非管理员且未被显式禁用时，自启 UAC 提权副本并退出自身。
    if _should_elevate():
        print("[提权] 当前非管理员，正在请求 UAC 提权…")
        if cleanlib.relaunch_elevated():
            sys.exit(0)
        print("[提示] 提权被取消/失败，仍以普通权限运行（注册表清理等需管理员的功能受限）")

    url = "http://%s:%d" % (HOST, PORT)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print("win-disk-cleanup 已启动: %s" % url)
    print("  （本工具清理的是【运行它的这台机器】的磁盘）")
    if not cleanlib.is_admin():
        print("  [提示] 当前非管理员：注册表清理 / ProgramData / powercfg / Dism 需管理员运行")

    stop = threading.Event()

    def on_open():
        try:
            webbrowser.open(url)
        except Exception:
            pass

    def on_exit():
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    def on_admin():
        if cleanlib.relaunch_elevated():
            threading.Thread(target=server.shutdown, daemon=True).start()

    tray = None
    try:
        if "--no-tray" in sys.argv:
            raise RuntimeError("disabled by --no-tray")
        import tray as _tray
        tray = _tray.create(url, on_open=on_open, on_exit=on_exit, on_admin=on_admin)
        threading.Thread(target=tray.run, daemon=True).start()
        print("  托盘图标已显示（右键可打开界面 / 以管理员重启 / 退出）")
    except Exception as e:
        print("  [提示] 托盘不可用（已降级为无托盘）：%s" % e)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        if tray:
            try:
                tray.stop()
            except Exception:
                pass
        try:
            server.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    main()
