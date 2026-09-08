# -*- coding: utf-8 -*-
"""
win-disk-cleanup 核心库（零依赖，纯标准库 + ctypes）。

把原 scripts/ 三个脚本的能力收敛成一个可被 Web / CLI 统一调用的库：
  - get_disk_report()   盘面摸底（只读）
  - scan_path()         分层限时扫描 + 四档自动分级（只读）
  - delete_paths()      执行删除（直调 WinAPI 绕过钩子，写日志）
  - is_admin()          是否以管理员运行

安全铁律（见 SKILL.md）：
  1. 默认只读。scan / disk 一个字节都不动。
  2. 删除必须显式调用 delete_paths()，且调用方要拿到用户确认。
  3. 缓存/残包永久删；个人文件走回收站（本库 hard_delete 走 WinAPI 永久删，
     仅用于"明确可清"的缓存/残包；个人文件请在 UI 层标记禁选）。
  4. 每次删除写日志到 logs/clean.log。
"""
import os
import sys
import time
import json
import ctypes
import winreg
from ctypes import wintypes

# ---------- 控制台 UTF-8（Windows） ----------
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
try:
    ctypes.windll.kernel32.SetConsoleOutputCP(65001)
except Exception:
    pass

# ---------- WinAPI 绑定 ----------
_k = ctypes.windll.kernel32
_GetDiskFreeSpaceExW = _k.GetDiskFreeSpaceExW
_GetDiskFreeSpaceExW.argtypes = [wintypes.LPCWSTR,
                                 ctypes.POINTER(ctypes.c_ulonglong),
                                 ctypes.POINTER(ctypes.c_ulonglong),
                                 ctypes.POINTER(ctypes.c_ulonglong)]
_GetDiskFreeSpaceExW.restype = wintypes.BOOL
_GetDriveTypeW = _k.GetDriveTypeW
_GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
_GetDriveTypeW.restype = wintypes.UINT

_DeleteFileW = _k.DeleteFileW
_DeleteFileW.argtypes = [wintypes.LPCWSTR]
_DeleteFileW.restype = wintypes.BOOL
_RemoveDirectoryW = _k.RemoveDirectoryW
_RemoveDirectoryW.argtypes = [wintypes.LPCWSTR]
_RemoveDirectoryW.restype = wintypes.BOOL
_SetFileAttributesW = _k.SetFileAttributesW
_SetFileAttributesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
_SetFileAttributesW.restype = wintypes.BOOL

FILE_ATTRIBUTE_NORMAL = 0x80
DRIVE_FIXED = 3
REPARSE_POINT = 0x400

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
LOG_FILE = os.path.join(LOG_DIR, "clean.log")


# ---------- 工具 ----------
def human(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return "%.2f %s" % (n, unit)
        n /= 1024.0
    return "%.2f TB" % n


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _norm(p):
    return os.path.abspath(os.path.expandvars(p)).replace("/", "\\")


# ---------- 盘面摸底（只读） ----------
def list_fixed_drives():
    out = []
    mask = _k.GetLogicalDrives()
    for i in range(26):
        if mask & (1 << i):
            root = "%s:/" % chr(ord("A") + i)
            try:
                if _GetDriveTypeW(root) == DRIVE_FIXED:
                    out.append(root)
            except Exception:
                pass
    return out


def drive_info(root):
    avail = ctypes.c_ulonglong()
    total = ctypes.c_ulonglong()
    free = ctypes.c_ulonglong()
    if not _GetDiskFreeSpaceExW(root, ctypes.byref(avail),
                                ctypes.byref(total), ctypes.byref(free)):
        return None
    return {"root": root, "total": total.value, "free": free.value,
            "used": total.value - free.value,
            "pct": (total.value - free.value) / total.value * 100 if total.value else 0}


def hibernate_enabled():
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SYSTEM\CurrentControlSet\Control\Power")
        val, _ = winreg.QueryValueEx(key, "HibernateEnabled")
        winreg.CloseKey(key)
        return bool(val)
    except Exception:
        return None


def get_disk_report():
    sysroot = os.environ.get("SystemRoot", "C:\\Windows")
    sysdrive = os.environ.get("SystemDrive", "C:")
    drives = [drive_info(r) for r in list_fixed_drives()]
    drives = [d for d in drives if d]

    sys_files = []
    for label, p in (("hiberfil.sys", sysdrive + "\\hiberfil.sys"),
                     ("pagefile.sys", sysdrive + "\\pagefile.sys"),
                     ("swapfile.sys", sysdrive + "\\swapfile.sys")):
        try:
            sz = os.path.getsize(p)
        except Exception:
            sz = None
        sys_files.append({"label": label, "path": p, "size": sz})

    upgrades = []
    for p in (sysdrive + "\\Windows.old", sysdrive + "\\$WINDOWS.~BT"):
        if os.path.isdir(p):
            upgrades.append(p)

    return {
        "drives": drives,
        "sysroot": sysroot,
        "sysdrive": sysdrive,
        "sys_files": sys_files,
        "upgrades": upgrades,
        "hibernate": hibernate_enabled(),
        "admin": is_admin(),
    }


# ---------- 扫描 + 分级（只读） ----------
def _dir_size(path, deadline):
    total = 0
    count = 0
    timeout = False
    try:
        for root, dirs, files in os.walk(path):
            if time.time() > deadline:
                timeout = True
                break
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                    count += 1
                except Exception:
                    pass
    except Exception:
        pass
    return total, count, timeout


def classify(path, name, is_dir, is_reparse):
    """返回 (tier, reason)。tier in {A,B,C,D}。
    A 零风险可直接清 / B 需确认 / C 只能卸载 / D 禁删。
    """
    lp = _norm(path).lower()
    nm = name.lower()

    # ---- D 禁删（优先级最高） ----
    if is_reparse:
        return "D", "目录联接/符号链接别名，不额外占空间，勿删"
    if "\\windows\\installer" in lp:
        return "D", "Windows 安装缓存，删了程序无法卸载/修复"
    if ("\\roaming\\uv" in lp or "\\appdata\\roaming\\uv" in lp) and "\\uv\\cache" not in lp:
        return "D", "uv 托管的 Python 解释器（含 torch），不是缓存"
    if "\\npm" in lp and ("\\roaming\\npm" in lp or "\\appdata\\npm" in lp or "\\appdata\\roaming\\npm" in lp):
        return "D", "npm 全局工具目录，删了命令全失效"
    if "\\user data" in lp and any(b in lp for b in ("chrome", "edge", "brave", "chromium", "firefox")):
        return "D", "浏览器个人数据，禁删"
    if "xwechat" in lp:
        return "D", "微信个人数据，禁删"
    if "\\user data\\projects" in lp:
        return "D", "应用用户草稿作品，禁删"
    if ".workbuddy\\workspace\\sessions" in lp:
        return "D", "WorkBuddy 历史会话，可能藏项目唯一副本"
    if ".vscode\\extensions" in lp:
        return "D", "当前编辑器扩展，删了开发环境坏掉"

    # ---- C 只能卸载 ----
    parent = os.path.dirname(lp)
    if parent.endswith("\\program files") or parent.endswith("\\program files (x86)"):
        return "C", "完整安装程序，请走卸载器，别硬删目录（留注册表残渣）"

    # ---- A 零风险（缓存/日志/残包/空目录） ----
    # 仅真实 Temp 目录（Local\Temp / Windows\Temp），避免 "mytemplate" 之类误判
    if "\\local\\temp" in lp or "\\windows\\temp" in lp:
        return "A", "临时目录内容，可清"
    safe_names = {"cache", "cached", "cacheddata", "cachedextensionvsixs",
                  "crashpad", "temp", "tmp", "diagnostics", "logs", "log",
                  ".npm", "__pycache__"}
    if nm in safe_names:
        return "A", "缓存/日志目录，可清"
    if nm.endswith(".tmp") or nm.endswith(".cache") or nm.endswith(".vsix"):
        return "A", "临时/缓存文件，可清"
    if "cache" in nm:
        return "A", "含 cache 的目录，多为缓存"
    if is_dir and nm.endswith(".exe"):
        return "A", "Roaming 根目录下 .exe 命名的目录=解压/构建残留"
    if "uv\\cache" in lp:
        return "A", "uv 下载缓存，可用 uv cache clean"
    if "pnpm" in lp and ("store" in nm or "cache" in nm):
        return "A", "pnpm 缓存/store"
    if "yarn" in lp and "cache" in nm:
        return "A", "Yarn 缓存"
    if "go-build" in nm:
        return "A", "Go 构建缓存"
    if "ms-playwright" in nm:
        return "A", "Playwright 浏览器缓存"
    if is_dir:
        try:
            if not any(os.scandir(path)):
                return "A", "空目录，程序会自动重建"
        except Exception:
            pass

    # ---- B 需确认（默认，最安全） ----
    return "B", "不确定是否安全，请人工核对后再选"


def scan_path(base, budget=60, top=300, excludes=()):
    base = _norm(base)
    if not os.path.isdir(base):
        return {"ok": False, "error": "不是目录: %s" % base, "items": []}
    deadline = time.time() + budget
    items = []
    truncated = False
    for e in os.scandir(base):
        if time.time() > deadline:
            truncated = True
            break
        if e.name in excludes:
            continue
        try:
            st = e.stat(follow_symlinks=False)
            is_reparse = bool(st.st_file_attributes & REPARSE_POINT)
            if e.is_dir(follow_symlinks=False):
                t, c, tmo = _dir_size(e.path, deadline)
                tier, reason = classify(e.path, e.name, True, is_reparse)
                items.append({"name": e.name, "path": _norm(e.path),
                              "size": t, "size_h": human(t), "files": c,
                              "tmo": tmo, "is_reparse": is_reparse,
                              "tier": tier, "reason": reason, "is_dir": True})
            else:
                tier, reason = classify(e.path, e.name, False, is_reparse)
                items.append({"name": e.name, "path": _norm(e.path),
                              "size": st.st_size, "size_h": human(st.st_size),
                              "files": 1, "tmo": False, "is_reparse": is_reparse,
                              "tier": tier, "reason": reason, "is_dir": False})
        except Exception:
            pass
    items.sort(key=lambda x: -x["size"])
    return {"ok": True, "base": base, "total": sum(x["size"] for x in items),
            "total_h": human(sum(x["size"] for x in items)),
            "truncated": truncated, "count": len(items),
            "items": items[:top]}


# ---------- 删除（写日志，直调 WinAPI） ----------
def _clear_attr(p):
    try:
        _SetFileAttributesW(p, FILE_ATTRIBUTE_NORMAL)
    except Exception:
        pass


def _hard_delete_one(path, keep_dir=False):
    """返回 (freed, fail)"""
    freed = 0
    fail = 0
    if not os.path.exists(path):
        return 0, 0
    if not os.path.isdir(path):
        try:
            freed = os.path.getsize(path)
        except Exception:
            freed = 0
        _clear_attr(path)
        if not _DeleteFileW(path):
            fail += 1
        return freed, fail
    for root, dirs, files in os.walk(path, topdown=False):
        for f in files:
            fp = os.path.join(root, f)
            try:
                freed += os.path.getsize(fp)
            except Exception:
                pass
            _clear_attr(fp)
            if not _DeleteFileW(fp):
                fail += 1
        for d in dirs:
            dp = os.path.join(root, d)
            _clear_attr(dp)
            _RemoveDirectoryW(dp)
    if not keep_dir:
        _clear_attr(path)
        _RemoveDirectoryW(path)
    return freed, fail


def _write_log(line):
    try:
        if not os.path.isdir(LOG_DIR):
            os.makedirs(LOG_DIR)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def delete_paths(paths, confirm=False, keep_dir=False):
    """删除一组路径。confirm 必须为 True 才执行（UI 层二次确认后调用）。
    缓存/残包走永久删；UI 层应禁止把 D 档加入 paths。
    返回 {freed_total, results:[...]}。
    """
    if not confirm:
        return {"ok": False, "error": "未确认，已拒绝", "freed_total": 0, "results": []}
    results = []
    freed_total = 0
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    _write_log("=== %s DELETE (admin=%s) ===" % (ts, is_admin()))
    for p in paths:
        p = _norm(p)
        if not os.path.exists(p):
            results.append({"path": p, "ok": False, "reason": "不存在",
                            "freed": 0, "freed_h": "0 B", "fail": 0,
                            "still_exists": False})
            _write_log("[skip] 不存在 %s" % p)
            continue
        freed, fail = _hard_delete_one(p, keep_dir)
        still = os.path.exists(p)
        freed_total += freed
        results.append({"path": p, "ok": (fail == 0 and not still),
                        "freed": freed, "freed_h": human(freed), "fail": fail,
                        "still_exists": still})
        _write_log("[%s] %s  freed=%s fail=%d still=%s" %
                   ("OK" if (fail == 0 and not still) else "FAIL", p,
                    human(freed), fail, still))
    _write_log("=== 合计释放 %s ===\n" % human(freed_total))
    return {"ok": True, "freed_total": freed_total,
            "freed_total_h": human(freed_total), "results": results}


def read_log(tail=200):
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
        return lines[-tail:]
    except Exception:
        return []


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="win-disk-cleanup core")
    ap.add_argument("cmd", choices=["disk", "scan", "clean"])
    ap.add_argument("path", nargs="?", default=None)
    ap.add_argument("--budget", type=int, default=60)
    ap.add_argument("--top", type=int, default=300)
    ap.add_argument("--exclude", default="")
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--keep-dir", action="store_true")
    args = ap.parse_args()

    if args.cmd == "disk":
        print(json.dumps(get_disk_report(), ensure_ascii=False, indent=2))
    elif args.cmd == "scan":
        ex = tuple(x.strip() for x in args.exclude.split(",") if x.strip())
        print(json.dumps(scan_path(args.path, args.budget, args.top, ex),
                         ensure_ascii=False, indent=2))
    elif args.cmd == "clean":
        print(json.dumps(delete_paths([args.path], args.confirm, args.keep_dir),
                         ensure_ascii=False, indent=2))
