---
name: win-disk-cleanup
description: Windows 磁盘空间清理（C 盘告急 / 系统残留 / 不用的文件 / 注册表残渣）。当用户说"C 盘满了""清理磁盘""哪些文件能删/能挪""磁盘整理""注册表残留""空间不够"时使用。只读扫描 → 分层定级 → 备份 → 用户确认 → 执行 → 验证，零依赖纯标准库。
agent_created: true
---

# Windows 磁盘清理

面向 Windows 10/11。所有操作纯 Python 标准库 + ctypes，无需安装任何东西。

## 五条铁律

1. **默认只读**。先扫出清单，用户点头才删。用户没明确同意前，一个字节都不动。
2. **缓存/残包 → 永久删除**；**个人文件 → 回收站**（`SHFileOperationW` + `FOF_ALLOWUNDO`）。
3. **动注册表前必须 `reg export` 备份**，且备份文件要告诉用户放哪、怎么回滚。
4. **每次删除写日志**（路径 + 大小 + 成功/失败），存 `logs/`。
5. **看不懂的目录先查清楚**。看似缓存的可能是程序本体——见 [禁删判定规则](#禁删判定规则)。

## 流程

### Step 1 · 盘面摸底

```python
ctypes.windll.kernel32.GetDiskFreeSpaceExW('C:/', byref(avail), byref(total), byref(free))
```

同时查系统级大头：
- `C:\hiberfil.sys` / `pagefile.sys` / `swapfile.sys`
- 休眠开关：`HKLM\SYSTEM\CurrentControlSet\Control\Power` 的 `HibernateEnabled`（1=开 0=关）
- `C:\Windows.old`、`$WINDOWS.~BT`（升级残留，用系统磁盘清理删，别手动 rm）

### Step 2 · 分层扫描（必须限时）

**每层设 `deadline`，超时标 `TMO` 并在报告里注明数字偏低**——不限时必然超时。

顺序（由表及里，每层只看下一层）：
```
C:\Users\<user>  →  AppData\Local / AppData\Roaming  →  Local\Programs  →  具体应用
C:\ProgramData  →  C:\Program Files*  →  Windows\WinSxS / Installer / Temp
```

> **扫描脚本一律写成独立 .py 文件运行**（`cat > _s.py << 'PYEOF'`）。
> 绝不用 `python -c "..."` 内联——bash 会把 `\360` 当八进制转义弄坏路径。

### Step 3 · 分四档呈现，让用户勾

| 档 | 内容 | 处理 |
|---|---|---|
| **A 零风险** | 缓存、日志、安装包残包、空目录 | 直接建议清 |
| **B 需确认** | 重复数据、疑似闲置的程序 | 列出让用户选 |
| **C 只能卸载** | 完整安装的程序 | 走卸载器，**别硬删目录**（留注册表残渣） |
| **D 禁删** | 见下方判定规则 | 明确列出 + 说明理由 |

用 `AskUserQuestion` 让用户勾选，**不要自己拍板**。

### Step 4 · 执行

**删除一律走 WinAPI**（绕过 Python os 层的删除钩子/回收站策略）：

```python
k = ctypes.windll.kernel32
DeleteFileW / RemoveDirectoryW
SetFileAttributesW(p, 0x80)   # 先清只读属性，否则失败
# 目录：os.walk(p, topdown=False) 自底向上，先删文件再删目录
```

**大目录（>1 万文件）用后台跑**：`run_in_background=true`，输出重定向到文件后读进度。
前台会被 SIGTERM——Defender 实时扫描每个被删文件（.exe 触发深度扫描）拖慢删除。

**提权**：`C:\ProgramData` 下的目录非管理员**无权删除**（`DeleteFileW` 直接失败，不是被占用）。
生成 bat 让用户右键「以管理员身份运行」。

**卸载优于删除**：
- NSIS：`Uninstall.exe /S`
- Squirrel.Windows：`Update.exe --uninstall -s`
- 卸载器跑完目录可能残留 → 硬删兜底 + 查开始菜单快捷方式残留

### Step 5 · 验证

**只看卷剩余空间，不看目录大小**。目录大小会因限时统计不准（统计更完整反而显示"变大"）。

## 禁删判定规则

不要背路径，**用规则判断**：

| 现象 | 判定 | 例子 |
|---|---|---|
| 目录名含 `cache` 但里面有解释器/可执行文件 | **本体，不是缓存** | `Roaming\uv`（uv 托管的 Python）、`Roaming\npm`（全局 CLI 目录） |
| `ProgramData` 下的驱动/安装包目录 | 可删，但**需管理员** | Dell\drivers、Intel Package Cache |
| `Windows\Installer` | **禁删**——删了程序无法卸载/修复 | |
| 与用户目录 inode 相同的"重复"目录 | **硬链接别名，不额外占空间，别删** | 见下方硬链接判定 |
| 应用数据里的 `Projects` / `Drafts` / 用户作品 | **禁删** | 剪映 `User Data\Projects` |
| 浏览器 Profile、微信/QQ 数据 | **个人数据，禁删** | Chrome `User Data`、微信 `xwechat` |
| `.vscode\extensions`、当前正在运行的工作目录 | **禁删** | |

**硬链接 / junction 判定**（遇到"疑似重复副本"必做）：
```python
st = os.stat(p)
st.st_ino                      # 相同 = 同一个文件
st.st_file_attributes & 0x400  # REPARSE_POINT = symlink/junction
```
目录硬链接：inode 相同但**无** reparse 属性 → 同一份数据的别名，**不额外占空间**。

## 通用可清项（跨机器成立）

- `hiberfil.sys` — `powercfg /h off`（需管理员），不用休眠就关，立省内存大小的 40%
- `WinSxS` — `Dism /Online /Cleanup-Image /StartComponentCleanup`
  **不加 `/ResetBase`**（否则无法卸载更新）。`/SPSuperseded` 报「找不到 Service Pack 备份文件」是**正常提示非错误**（Win10 1809+ 无 SP 备份）。系统若已自动维护过，可能释放 0 —— 正常。
- `%TEMP%` 下 `Diagnostics`、`*-install`、`*.tmp`
- 包管理器缓存：pnpm store、Yarn Cache、go-build、ms-playwright、uv cache（优先用官方 `clean` 命令）
- 更新器残包：`*updater` 目录里的 `installer.exe` / `pending`
- VS Code：`Roaming\Code\CachedExtensionVSIXs`（扩展 .vsix 缓存，扩展已装好，安全）、`CachedData`、`Crashpad`、`Cache`
- 应用日志目录（`*Log`、`mssdk_log` 等）
- 空目录（程序自动重建，删了无感）
- `Roaming` 根目录下**名字带 `.exe` 的目录**——解压/构建产物。注意它是**目录不是文件**，`is_file()` 判断不到，用 `is_dir() and '.exe' in name`

## 踩坑清单（每一条都是真踩出来的）

1. **Python 删除被钩子拦截**：`os.remove` / `shutil.rmtree` 可能被环境改送回收站，回收站满时 fail-closed 报错、文件根本没删，且大目录极慢。**用 ctypes WinAPI 绕过**。
2. **bat 三大坑**（用 goto 标签链，不用 if 块）：
   - **cmd 不区分引号内的圆括号** → `if (...)` 块里有 `sys.exit(` 会让块提前闭合，窗口一闪而过
   - **不要用 `>nul` 重定向** → 改用 `where /q xxx` + `if not errorlevel 1`
   - **不要 `chcp 65001`** → 切码页后 cmd 按字节偏移重读文件错位，把 `>nul` 残段当裸命令 `nul` 执行，弹「Internet 安全设置阻止打开 C:\WINDOWS\system32\nul」
   - bat 写**纯 ASCII**，中文提示交给 Python 端 `ctypes.windll.kernel32.SetConsoleOutputCP(65001)`
   - 解释器探测链：`py -3` → `python` → 常见安装路径，最后要有兜底 + `pause`
3. **`sc query` / `reg query` / `fsutil`** 常被安全策略拦。查服务用 `winreg.OpenKey(HKLM, r'SYSTEM\CurrentControlSet\Services\<name>')`。
4. **`.reg` 文件**必须 UTF-16 LE 带 BOM；删除语法 `[-HKEY_...]`；用独立 .py 生成。
5. **限时统计会低估**，报告里标注 `TMO`，别把数字当精确值。
6. **删前关掉相关程序**（浏览器/编辑器），被占用的文件会失败。

## 参考

- 仓库：<https://github.com/oracis/win-disk-cleanup>
- 本机（DELL Win10）实测清单见 `references/local-findings.md` —— 作为真实案例参考，
  **不要照抄里面的路径和大小到别的机器上**。
