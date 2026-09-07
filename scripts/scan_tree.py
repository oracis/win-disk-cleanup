# -*- coding: utf-8 -*-
"""
分层限时扫描 — 找出真正占空间的大头。

用法:
    python scan_tree.py C:/Users/xxx              # 扫一层，默认每层 60s
    python scan_tree.py C:/Users/xxx --budget 120 --top 25
    python scan_tree.py C:/ --exclude AppData,Windows

设计要点:
  - 每层独立限时，超时标 TMO（数字偏低，不是精确值）
  - 输出按大小降序，一眼看到大头
  - 支持排除子树，避免在无意义的目录上耗光预算
"""
import os
import sys
import time
import ctypes

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
try:
    ctypes.windll.kernel32.SetConsoleOutputCP(65001)
except Exception:
    pass


def dir_size(path, deadline):
    total = 0
    count = 0
    timeout = False
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
    return total, count, timeout


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.2f %s" % (n, unit)
        n /= 1024.0
    return "%.2f GB" % n


def scan(base, budget=60, top=20, excludes=()):
    base = os.path.abspath(os.path.expandvars(base))
    if not os.path.isdir(base):
        print("不是目录: %s" % base)
        return
    deadline = time.time() + budget
    items = []
    for e in os.scandir(base):
        if time.time() > deadline:
            print("  [!] 预算耗尽，已扫 %d 项" % len(items))
            break
        if e.name in excludes:
            continue
        try:
            if e.is_dir(follow_symlinks=False):
                t, c, tmo = dir_size(e.path, deadline)
                items.append((e.name, t, c, tmo))
            else:
                items.append((e.name, e.stat().st_size, 1, False))
        except Exception:
            pass
    items.sort(key=lambda x: -x[1])
    total = sum(x[1] for x in items)
    print("\n=== %s   合计 %s ===" % (base, human(total)))
    for name, t, c, tmo in items[:top]:
        flag = "  TMO" if tmo else ""
        print("  %10s %10d 文件%s  %s" % (human(t), c, flag, name))


def main():
    args = sys.argv[1:]
    budget = 60
    top = 20
    excludes = ()
    if "--budget" in args:
        i = args.index("--budget")
        budget = int(args[i + 1])
        del args[i:i + 2]
    if "--top" in args:
        i = args.index("--top")
        top = int(args[i + 1])
        del args[i:i + 2]
    if "--exclude" in args:
        i = args.index("--exclude")
        excludes = tuple(x.strip() for x in args[i + 1].split(","))
        del args[i:i + 2]
    if not args:
        print(__doc__)
        return 1
    for p in args:
        scan(p, budget, top, excludes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
