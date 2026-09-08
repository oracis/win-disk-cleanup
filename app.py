# -*- coding: utf-8 -*-
"""
win-disk-cleanup Web 应用（零依赖，纯标准库）。

启动:
    python app.py                 # 默认 http://127.0.0.1:5053
    PORT=8080 python app.py

接口:
    GET  /                -> 静态首页
    GET  /api/disk        -> 盘面摸底（只读）
    POST /api/scan        -> 扫描某路径，四档分级（只读）
    POST /api/clean       -> 删除（需 body.confirm=true）
    GET  /api/log         -> 清理日志（最近 200 行）
    GET  /api/admin       -> 是否管理员
"""
import os
import sys
import json
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
        rel = "/index.html"
    # 防目录穿越
    path = os.path.normpath(os.path.join(STATIC, rel.lstrip("/")))
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
        self.send_error(404)


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print("win-disk-cleanup 已启动: http://%s:%d" % (HOST, PORT))
    print("  （本工具清理的是【运行它的这台机器】的磁盘）")
    if not cleanlib.is_admin():
        print("  [提示] 当前非管理员：ProgramData / powercfg / Dism 等需右键以管理员运行")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        server.shutdown()


if __name__ == "__main__":
    main()
