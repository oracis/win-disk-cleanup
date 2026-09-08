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
import subprocess
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


# ---------- 注册表清理 / 卸载器引导 ----------
# 仅允许在白名单范围内的注册表项被删除，防误删系统关键项。
_REG_DELETE_PREFIXES = [
    "HKLM\\SOFTWARE\\MICROSOFT\\WINDOWS\\CURRENTVERSION\\UNINSTALL\\",
    "HKLM\\SOFTWARE\\WOW6432NODE\\MICROSOFT\\WINDOWS\\CURRENTVERSION\\UNINSTALL\\",
    "HKCU\\SOFTWARE\\MICROSOFT\\WINDOWS\\CURRENTVERSION\\UNINSTALL\\",
    "HKLM\\SOFTWARE\\MICROSOFT\\WINDOWS\\CURRENTVERSION\\INSTALLER\\USERDATA\\",
]

UNINSTALL_ROOTS = [
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
]
_ROOT_NAMES = {winreg.HKEY_LOCAL_MACHINE: "HKLM", winreg.HKEY_CURRENT_USER: "HKCU"}


def _safe_read(k, name, default=""):
    try:
        v, _ = winreg.QueryValueEx(k, name)
        return v
    except Exception:
        return default


def _expand_env(s):
    try:
        return os.path.expandvars(s) if s else s
    except Exception:
        return s


def _exe_of(cmd):
    """从卸载命令里取出可执行文件路径（处理引号包裹）。"""
    if not cmd:
        return None
    c = cmd.strip()
    if c.startswith('"'):
        end = c.find('"', 1)
        exe = c[1:end] if end != -1 else c[1:]
    else:
        exe = c.split(' ')[0]
    return _expand_env(exe)


def _exe_missing(us):
    """判断卸载命令里的可执行文件是否真的不存在（区分 MSI/系统内置命令）。"""
    exe = _exe_of(us)
    if not exe:
        return False
    if os.path.exists(exe):
        return False
    base = os.path.basename(exe).lower()
    # MsiExec / rundll32 / cmd / powershell / control 等是系统自带，恒存在
    sysroot = os.environ.get("SystemRoot", "C:\\Windows")
    import shutil
    candidates = [
        os.path.join(sysroot, "system32", base),
        os.path.join(sysroot, base),
    ]
    for c in candidates:
        if os.path.exists(c):
            return False
    if shutil.which(base):
        return False
    return True


def _split_reg_path(full):
    full = full.replace("/", "\\")
    idx = full.find("\\")
    if idx < 0:
        raise ValueError("bad reg path")
    root, sub = full[:idx], full[idx + 1:]
    if root == "HKLM":
        return winreg.HKEY_LOCAL_MACHINE, sub
    if root == "HKCU":
        return winreg.HKEY_CURRENT_USER, sub
    if root == "HKCR":
        return winreg.HKEY_CLASSES_ROOT, sub
    raise ValueError("unsupported root: " + root)


def _reg_enum_subkeys(hkey, subpath):
    out = []
    try:
        k = winreg.OpenKey(hkey, subpath, 0, winreg.KEY_READ)
    except Exception:
        return out
    try:
        n = winreg.QueryInfoKey(k)[0]
        for i in range(n):
            try:
                name = winreg.EnumKey(k, i)
                out.append((hkey, subpath + "\\" + name))
            except Exception:
                pass
    finally:
        winreg.CloseKey(k)
    return out


def _reg_key_exists(full_path):
    try:
        hive, sub = _split_reg_path(full_path)
        k = winreg.OpenKey(hive, sub, 0, winreg.KEY_READ)
        winreg.CloseKey(k)
        return True
    except Exception:
        return False


def _reg_path_ok(full_path):
    up = full_path.replace("/", "\\").upper()
    return any(up.startswith(p) for p in _REG_DELETE_PREFIXES)


def _scan_msi_orphans():
    """扫描孤立的 MSI 产品项（LocalPackage 指向的缓存包已丢失）。需管理员才读得到。"""
    out = []
    base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Installer\UserData"
    try:
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base, 0, winreg.KEY_READ)
    except Exception:
        return out
    try:
        nsid = winreg.QueryInfoKey(k)[0]
        for i in range(nsid):
            try:
                sid = winreg.EnumKey(k, i)
            except Exception:
                continue
            try:
                pk = winreg.OpenKey(k, sid + r"\Products")
            except Exception:
                continue
            try:
                np_ = winreg.QueryInfoKey(pk)[0]
                for j in range(np_):
                    try:
                        pid = winreg.EnumKey(pk, j)
                    except Exception:
                        continue
                    try:
                        ik = winreg.OpenKey(pk, pid + r"\InstallProperties")
                    except Exception:
                        continue
                    try:
                        lp = winreg.QueryValueEx(ik, "LocalPackage")[0]
                    except Exception:
                        winreg.CloseKey(ik)
                        continue
                    if lp and not os.path.exists(_expand_env(lp)):
                        try:
                            name = winreg.QueryValueEx(ik, "DisplayName")[0]
                        except Exception:
                            name = "(MSI %s)" % pid
                        key_path = "HKLM\\" + base + "\\" + sid + "\\Products\\" + pid
                        out.append({
                            "name": name,
                            "publisher": "",
                            "key_path": key_path,
                            "size_kb": 0,
                            "reason": "MSI 缓存包已丢失，残留产品项",
                            "tier": "D",
                            "kind": "msi-orphan",
                        })
                    winreg.CloseKey(ik)
            finally:
                winreg.CloseKey(pk)
    finally:
        winreg.CloseKey(k)
    return out


def scan_registry():
    """只读：找出可安全清理的残留注册表项。tier 一律 D（高危，需显式确认+备份）。"""
    items = []
    for hive, base in UNINSTALL_ROOTS:
        for hk, full in _reg_enum_subkeys(hive, base):
            try:
                k = winreg.OpenKey(hk, full)
            except Exception:
                continue
            try:
                try:
                    name = winreg.QueryValueEx(k, "DisplayName")[0]
                except Exception:
                    continue
                if not name:
                    continue
                us = _safe_read(k, "UninstallString")
                if not us:
                    continue
                if not _exe_missing(us):
                    continue
                try:
                    size_kb = winreg.QueryValueEx(k, "EstimatedSize")[0]
                except Exception:
                    size_kb = 0
                key_path = _ROOT_NAMES[hk] + "\\" + full
                items.append({
                    "name": name,
                    "publisher": _safe_read(k, "Publisher"),
                    "key_path": key_path,
                    "uninstall_string": us,
                    "size_kb": int(size_kb) if isinstance(size_kb, int) else 0,
                    "reason": "卸载程序已不存在，残留注册表项",
                    "tier": "D",
                    "kind": "uninstall-orphan",
                })
            finally:
                winreg.CloseKey(k)
    items += _scan_msi_orphans()
    items.sort(key=lambda x: -x.get("size_kb", 0))
    return {"ok": True, "count": len(items), "items": items}


def delete_registry_key(full_path, confirm=False):
    """删除一个注册表项。先 reg export 备份，再 reg delete。需管理员 + confirm。"""
    if not confirm:
        return {"ok": False, "error": "未确认，已拒绝", "path": full_path, "backup": None}
    if not is_admin():
        return {"ok": False, "error": "需管理员权限（以管理员运行 start.bat）",
                "path": full_path, "backup": None}
    if not _reg_path_ok(full_path):
        return {"ok": False, "error": "路径不在允许的清理范围内（防误删）",
                "path": full_path, "backup": None}
    try:
        if not os.path.isdir(LOG_DIR):
            os.makedirs(LOG_DIR)
    except Exception:
        pass
    ts = time.strftime("%Y%m%d_%H%M%S")
    backup = os.path.join(LOG_DIR, "reg_%s.reg" % ts)
    try:
        subprocess.run(["reg", "export", full_path, backup, "/y"],
                       capture_output=True, timeout=30)
    except Exception:
        backup = None
    r = subprocess.run(["reg", "delete", full_path, "/f"],
                       capture_output=True, timeout=30)
    still = _reg_key_exists(full_path)
    ok = (r.returncode == 0) and not still
    _write_log("=== %s REG-DELETE %s backup=%s ok=%s still=%s ===" %
               (ts, full_path, bool(backup), ok, still))
    return {"ok": ok, "path": full_path, "backup": backup, "still_exists": still}


def list_programs():
    """只读：枚举已安装程序（供卸载器引导）。"""
    items = []
    for hive, base in UNINSTALL_ROOTS:
        for hk, full in _reg_enum_subkeys(hive, base):
            try:
                k = winreg.OpenKey(hk, full)
            except Exception:
                continue
            try:
                try:
                    name = winreg.QueryValueEx(k, "DisplayName")[0]
                except Exception:
                    continue
                if not name:
                    continue
                try:
                    size_kb = winreg.QueryValueEx(k, "EstimatedSize")[0]
                except Exception:
                    size_kb = 0
                key_path = _ROOT_NAMES[hk] + "\\" + full
                items.append({
                    "name": name,
                    "publisher": _safe_read(k, "Publisher"),
                    "install_location": _safe_read(k, "InstallLocation"),
                    "uninstall_string": _safe_read(k, "UninstallString"),
                    "quiet_uninstall_string": _safe_read(k, "QuietUninstallString"),
                    "key_path": key_path,
                    "size_kb": int(size_kb) if isinstance(size_kb, int) else 0,
                })
            finally:
                winreg.CloseKey(k)
    items.sort(key=lambda x: -x.get("size_kb", 0))
    return items[:500]


def get_uninstall_string(key_path):
    try:
        hive, sub = _split_reg_path(key_path)
        k = winreg.OpenKey(hive, sub)
        us = _safe_read(k, "UninstallString") or _safe_read(k, "QuietUninstallString")
        winreg.CloseKey(k)
    except Exception:
        return None
    return us


def _parse_cmd(cmd):
    """拆出 (exe, [args])。"""
    import shlex
    c = cmd.strip()
    if c.startswith('"'):
        end = c.find('"', 1)
        exe = c[1:end] if end != -1 else c[1:]
        rest = c[end + 1:].strip()
    else:
        parts = c.split(' ', 1)
        exe = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
    exe = _expand_env(exe)
    args = shlex.split(rest) if rest else []
    return exe, args


def launch_uninstall(key_path):
    """启动某程序的卸载器。只执行来自 Uninstall 子项的命令，且校验 exe 真实存在。"""
    us = get_uninstall_string(key_path)
    if not us:
        return {"ok": False, "error": "读不到卸载命令"}
    exe, args = _parse_cmd(us)
    if not exe or not os.path.exists(exe):
        return {"ok": False, "error": "卸载程序不存在: %s" % exe}
    try:
        subprocess.Popen([exe] + args, shell=False,
                         creationflags=subprocess.CREATE_NEW_CONSOLE)
        return {"ok": True, "exe": exe, "args": args}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def open_appwiz():
    """打开系统「程序和功能」控制面板。"""
    try:
        subprocess.Popen(["control.exe", "appwiz.cpl"], shell=False,
                         creationflags=subprocess.CREATE_NEW_CONSOLE)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def relaunch_elevated(extra_args=()):
    """以管理员重启自身（UAC）。成功返回 True。已带 --elevated 防循环。"""
    params = '"%s"' % os.path.abspath(sys.argv[0])
    for a in extra_args:
        params += " " + a
    if "--elevated" not in sys.argv and "--no-elevate" not in sys.argv:
        params += " --elevated"
    r = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, params, os.getcwd(), 1)
    return r > 32


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
