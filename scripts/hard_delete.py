# -*- coding: utf-8 -*-
"""
Windows 硬删除工具 — 直调 WinAPI，绕过 Python os 层的删除钩子/回收站策略。

用法:
    python hard_delete.py <路径> [<路径2> ...]        # 删除（默认打印计划后执行）
    python hard_delete.py <路径> --dry-run            # 只统计，不删
    python hard_delete.py <路径> --keep-dir           # 只清内容，保留目录本身
    python hard_delete.py <路径> --log out.log        # 追加日志

设计要点:
  - 目录自底向上删（os.walk topdown=False），先文件后目录
  - 每个路径先 SetFileAttributesW(NORMAL)，否则只读/系统文件删不掉
  - 统计与删除分离：先算大小给用户看，再执行
  - 全程 flush 输出，方便后台运行时读进度
"""
import os
import sys
import time
import ctypes
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


def _clear_attr(p):
    try:
        _SetFileAttributesW(p, FILE_ATTRIBUTE_NORMAL)
    except Exception:
        pass


def dir_stats(path, budget=None):
    """统计 (字节数, 文件数, 是否超时)"""
    deadline = time.time() + budget if budget else None
    total = 0
    count = 0
    timeout = False
    for root, dirs, files in os.walk(path):
        if deadline and time.time() > deadline:
            timeout = True
            break
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
                count += 1
            except Exception:
                pass
    return total, count, timeout


def hard_delete(path, keep_dir=False):
    """返回 (释放字节数, 失败数)"""
    freed = 0
    fail = 0
    if not os.path.exists(path):
        return 0, 0

    # 单文件
    if not os.path.isdir(path):
        try:
            freed = os.path.getsize(path)
        except Exception:
            freed = 0
        _clear_attr(path)
        return (freed, 0) if _DeleteFileW(path) else (0, 1)

    # 目录：自底向上
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


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit)
        n /= 1024.0
    return "%.1f GB" % n


def main():
    args = [a for a in sys.argv[1:]]
    dry = "--dry-run" in args
    keep = "--keep-dir" in args
    logfile = None
    if "--log" in args:
        i = args.index("--log")
        if i + 1 < len(args):
            logfile = args[i + 1]
            del args[i:i + 2]
    if "--dry-run" in args:
        args.remove("--dry-run")
    if "--keep-dir" in args:
        args.remove("--keep-dir")

    paths = args
    if not paths:
        print(__doc__)
        return 1

    logf = open(logfile, "a", encoding="utf-8") if logfile else None

    def out(msg):
        print(msg, flush=True)
        if logf:
            logf.write(msg + "\n")
            logf.flush()

    out("=== %s %s ===" % ("DRY RUN" if dry else "DELETE", time.strftime("%Y-%m-%d %H:%M:%S")))
    grand_total = 0
    t0 = time.time()

    for p in paths:
        p = os.path.abspath(os.path.expandvars(p))
        if not os.path.exists(p):
            out("\n[skip] 不存在  %s" % p)
            continue
        total, count, tmo = dir_stats(p, budget=60)
        grand_total += total
        out("\n>> %s" % p)
        out("   %s / %d 文件%s" % (human(total), count, "  [统计超时，实际更大]" if tmo else ""))
        if dry:
            continue
        if keep and os.path.isdir(p):
            freed = 0
            fail = 0
            for e in os.scandir(p):
                f, fa = hard_delete(e.path, keep_dir=False)
                freed += f
                fail += fa
        else:
            freed, fail = hard_delete(p, keep_dir=False)
        out("   释放 %s，失败 %d，剩余存在=%s" % (human(freed), fail, os.path.exists(p)))

    out("\n=== 合计 %s，耗时 %.1fs ===" % (human(grand_total), time.time() - t0))
    if logf:
        logf.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
