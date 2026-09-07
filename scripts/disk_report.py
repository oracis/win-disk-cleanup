# -*- coding: utf-8 -*-
"""
盘面摸底（Step 1）— 只读，不删任何东西。

用法:
    python disk_report.py                # 扫本机所有固定盘
    python disk_report.py C:/ D:/

输出:
  - 每个盘的 总容量 / 剩余 / 占用率
  - 系统级大头: hiberfil.sys pagefile.sys swapfile.sys Windows.old $WINDOWS.~BT
  - 休眠开关状态（读注册表，不用 sc/reg 命令——非管理员 shell 常被拦）
  - WinSxS 大小提示
"""
import os
import sys
import ctypes
import winreg
from ctypes import wintypes

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
try:
    ctypes.windll.kernel32.SetConsoleOutputCP(65001)
except Exception:
    pass

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

DRIVE_FIXED = 3


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return "%.2f %s" % (n, unit)
        n /= 1024.0
    return "%.2f TB" % n


def drive_info(root):
    avail = ctypes.c_ulonglong()
    total = ctypes.c_ulonglong()
    free = ctypes.c_ulonglong()
    if not _GetDiskFreeSpaceExW(root, ctypes.byref(avail),
                                ctypes.byref(total), ctypes.byref(free)):
        return None
    return total.value, free.value, total.value - free.value


def list_fixed_drives():
    out = []
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    for i in range(26):
        if mask & (1 << i):
            root = "%s:/" % chr(ord("A") + i)
            if _GetDriveTypeW(root) == DRIVE_FIXED:
                out.append(root)
    return out


def hibernate_enabled():
    """返回 True/False/None(读不到)"""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Power")
        val, _ = winreg.QueryValueEx(key, "HibernateEnabled")
        winreg.CloseKey(key)
        return bool(val)
    except Exception:
        return None


def sys_file(path):
    try:
        return os.path.getsize(path)
    except Exception:
        return None


def main():
    roots = [a if a.endswith(("/", "\\")) else a + "/" for a in sys.argv[1:]]
    if not roots:
        roots = list_fixed_drives()

    print("========== 盘面 ==========")
    for r in roots:
        info = drive_info(r)
        if not info:
            print("  %s  <读取失败>" % r)
            continue
        total, free, used = info
        pct = (used / total * 100) if total else 0
        bar = "#" * int(pct / 5) + "." * (20 - int(pct / 5))
        print("  %s  剩余 %10s / 总 %10s   %5.1f%%  [%s]"
              % (r, human(free), human(total), pct, bar))

    print("\n========== 系统级大头 ==========")
    sysroot = os.environ.get("SystemDrive", "C:")
    targets = [
        ("休眠文件  hiberfil.sys", "%s\\hiberfil.sys" % sysroot),
        ("页面文件  pagefile.sys", "%s\\pagefile.sys" % sysroot),
        ("交换文件  swapfile.sys", "%s\\swapfile.sys" % sysroot),
    ]
    for label, p in targets:
        s = sys_file(p)
        print("  %-28s %s" % (label, human(s) if s is not None else "不存在"))

    for p in ("%s\\Windows.old" % sysroot, "%s\\$WINDOWS.~BT" % sysroot):
        if os.path.isdir(p):
            print("  [!] 发现升级残留: %s  -> 用系统「磁盘清理 - 清理系统文件」删，别手动 rm" % p)

    h = hibernate_enabled()
    state = {True: "开启（hiberfil.sys 占内存中40%，可用 powercfg /h off 关闭）",
             False: "已关闭", None: "读不到（权限不足）"}[h]
    print("\n========== 休眠 ==========\n  HibernateEnabled = %s" % state)

    print("\n========== 提示 ==========")
    print("  WinSxS 清理: Dism /Online /Cleanup-Image /StartComponentCleanup")
    print("  （不要加 /ResetBase，否则无法卸载已安装的更新）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
