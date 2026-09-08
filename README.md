# win-disk-cleanup

Windows 磁盘空间清理技能包 —— C 盘告急时，安全地找出能删、能挪的东西。

**零依赖**：纯 Python 3 标准库 + `ctypes`，不装任何包，不做任何联网操作。

实测战绩：一台 256G NVMe 的 Win10 机器，C 盘 **21.8GB → 39.0GB 剩余，累计释放 17.2GB**（占用率 82% → 67%）。

---

## 为什么需要它

常见的"一键清理"工具要么删得不痛不痒，要么误删要命的东西。这个技能包的核心不是"删哪些路径"，而是一套**判定规则**：

- 看着像缓存的 `_cache` 目录，里面可能躺着 Python 解释器（`Roaming\uv`）或全局 CLI 工具（`Roaming\npm`）——删了命令全失效
- 12.57GB 的"重复副本"可能只是硬链接别名，删它一分钱空间都腾不出来
- `Windows\Installer` 删了，程序就再也卸不掉了

所以：**先扫、再分级、让用户勾、备份好、才动手。**

---

## 五条铁律

1. **默认只读**。先出清单，用户点头才删。没明确同意前，一个字节都不动。
2. **缓存/残包 → 永久删除**；**个人文件 → 回收站**（`SHFileOperationW` + `FOF_ALLOWUNDO`）。
3. **动注册表前必须 `reg export` 备份**，并告诉用户备份在哪、怎么回滚。
4. **每次删除写日志**（路径 + 大小 + 成功/失败）。
5. **看不懂的目录先查清楚**，别猜。

---

## 目录结构

```
win-disk-cleanup/
├── SKILL.md                    # 技能主文件：流程 + 判定规则 + 踩坑清单
├── scripts/
│   ├── disk_report.py          # Step 1 盘面摸底（只读）
│   ├── scan_tree.py            # Step 2 分层限时扫描
│   ├── hard_delete.py          # Step 4 WinAPI 硬删除
│   └── template_admin.bat      # 提权模板（避开 bat 三大坑）
└── references/
    └── local-findings.md       # 真实机器实测案例（路径别照抄）
```

---

## 快速上手

### 1. 盘面摸底（只读）

```bat
python scripts/disk_report.py
```

输出每个盘的剩余/占用率、`hiberfil.sys`/`pagefile.sys` 大小、休眠开关状态、升级残留提示。

### 2. 分层扫描（限时，避免扫到天荒地老）

```bat
python scripts/scan_tree.py C:/Users/你的用户名 --budget 120 --top 25
python scripts/scan_tree.py C:/ --exclude Windows,ProgramData
```

每层独立限时，超时的项标 `TMO`——**数字偏低，不是精确值**。

### 3. 分四档让用户勾

| 档 | 内容 | 处理 |
|---|---|---|
| **A 零风险** | 缓存、日志、安装包残包、空目录 | 直接建议清 |
| **B 需确认** | 重复数据、疑似闲置程序 | 列出让用户选 |
| **C 只能卸载** | 完整安装的程序 | 走卸载器，**别硬删目录** |
| **D 禁删** | 见 SKILL.md 判定规则 | 明确列出 + 说明理由 |

### 4. 执行（先 dry-run）

```bat
python scripts/hard_delete.py "C:\path\to\cache" --dry-run
python scripts/hard_delete.py "C:\path\to\cache" --log clean.log
python scripts/hard_delete.py "C:\path\to\cache" --keep-dir
```

`-–dry-run` 只统计不删；`-–keep-dir` 只清内容保留目录；`-–log` 写日志。

---

## 三个关键设计

**为什么删除要调 WinAPI 而不是 `os.remove`**

在受管环境里，Python 的 `shutil.rmtree` / `os.remove` 可能被钩子改送回收站。回收站满时会 fail-closed 报错——文件根本没删，还慢得离谱（10 万文件能跑几十分钟）。所以直接 `ctypes` 调 `DeleteFileW` / `RemoveDirectoryW`，绕开 os 层。

**为什么 bat 要写成纯 ASCII 的 goto 链**

cmd 有三个坑，每一个都会让窗口一闪而过：

- **不区分引号内的圆括号** —— `if (...)` 块里有 `sys.exit(` 会让块提前闭合
- **`>nul` 重定向** —— 改用 `where /q xxx` + `if not errorlevel 1`
- **`chcp 65001`** —— 切码页后 cmd 按字节偏移重读文件错位，会把残留当裸命令执行，弹「Internet 安全设置阻止打开 `C:\WINDOWS\system32\nul`」

中文显示交给 Python 端 `SetConsoleOutputCP(65001)` 处理。

**为什么大目录要后台跑**

Defender 实时扫描每个被删文件（`.exe` 还会触发深度扫描），前台跑会被超时 SIGTERM。超过 1 万文件的目录扔后台，输出重定向到文件后读进度。

---

## Web 应用（图形界面）

不想敲命令行？项目自带一个**零依赖的本地 Web 应用**，把上面的流程包成了浏览器界面：盘面卡片 → 选目录扫描 → 四档分级勾选 → 二次确认删除 → 日志。

```
win-disk-cleanup-app/
├── app.py          # 标准库 http.server，提供 /api/* 与静态页面
├── cleanlib.py     # 核心：盘面/扫描分级/删除/日志（被 Web 与 CLI 共用）
├── static/         # index.html + app.js + style.css（暗色主题）
└── start.bat       # 一键启动（纯 ASCII，绕过 bat 三大坑）
```

**启动**

```bat
双击 start.bat          # 或： python app.py
```

然后浏览器打开 http://127.0.0.1:5053 。

**端口 / 绑定可改**（环境变量）：

```bat
set PORT=8080
python app.py           # 监听 127.0.0.1:8080
```

**要点**

- 分级逻辑（`classify()`）把每个扫描项标成 A/B/C/D，D 档在界面上**直接禁选**，删之前还有一次弹窗确认 —— 和命令行版同样的保守策略。
- 删除走 `cleanlib.delete_paths()`，直调 WinAPI 绕过 os 层钩子，每次写 `logs/clean.log`。
- **清理的是「运行它的这台机器」的磁盘**。本机双击运行就清你自己的 C 盘；若发布到云服务器，清的是服务器那台机器，不是你的电脑 —— 别搞混。

## 通用可清项（跨机器成立）

- `hiberfil.sys` —— `powercfg /h off`（需管理员），不用休眠就关，立省内存的 40%
- `WinSxS` —— `Dism /Online /Cleanup-Image /StartComponentCleanup`，**不加 `/ResetBase`**
- `%TEMP%` 下的 `Diagnostics`、`*-install`、`*.tmp`
- 包管理器缓存：pnpm store、Yarn Cache、go-build、ms-playwright、uv cache（优先用官方 `clean` 命令）
- VS Code：`CachedExtensionVSIXs`、`CachedData`、`Crashpad`、`Cache`
- 空目录（程序自动重建，删了无感）
- 卸载器优先级：`Uninstall.exe /S`（NSIS）、`Update.exe --uninstall -s`（Squirrel）

## 禁删清单（记住规则，别背路径）

| 现象 | 判定 |
|---|---|
| 目录名含 `cache` 但里面有解释器/可执行文件 | **本体，不是缓存** |
| `Windows\Installer` | **禁删** —— 删了无法卸载/修复程序 |
| 与用户目录 inode 相同的"重复"目录 | **硬链接别名，不额外占空间** |
| 应用数据里的 `Projects` / `Drafts` / 用户作品 | **禁删** |
| 浏览器 Profile、微信/QQ 数据 | **个人数据，禁删** |

---

## 安装为 WorkBuddy 技能

```bat
xcopy /E /I win-disk-cleanup %USERPROFILE%\.workbuddy\skills\win-disk-cleanup
```

或直接把本仓库克隆到 `~/.workbuddy/skills/win-disk-cleanup/`。

触发词：「C 盘满了」「清理磁盘」「磁盘整理」「注册表残留」「哪些文件能删」。

---

## 免责声明

- 脚本按「先 dry-run、用户确认后执行」设计，**请务必先看输出再删**。
- `references/local-findings.md` 里的路径和大小是**某一台特定机器**的结果，换机器请用判定规则重新扫，别照抄。
- 作者不对任何数据损失负责。删之前备份你想留的东西。

## License

MIT
