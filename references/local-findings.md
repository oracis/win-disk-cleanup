# 本机实测清单（DELL / Win10 / 256G NVMe SSD）

> 这是 2026-09-06~07 在一台真实机器上跑出来的结果，作为**案例参考**。
> 换机器时不要照抄路径和大小，用主 SKILL.md 的判定规则重新扫。

## 最终成果

C 盘 21.8GB → 39.0GB 剩余，累计释放 **17.2GB**（占用率 82% → 67%）。

| 阶段 | 释放 |
|---|---|
| 关休眠 hiberfil.sys | 6.36 GB |
| 缓存残包 + qtg 构建产物 | 2.1 GB |
| VS Code 缓存 + 剪映日志 | 3.02 GB |
| Dell 驱动包 + Intel 缓存 | 6.78 GB |
| WinSxS 组件清理 | 0（系统已自动维护过） |

## 实际清理的项

| 路径 | 大小 | 备注 |
|---|---|---|
| `C:\hiberfil.sys` | 6.36G | `powercfg /h off` |
| `C:\ProgramData\Dell\drivers` | 6.32G | 含 3 份 1.63G 完全重复（md5 已验证），需管理员 |
| `%APPDATA%\Code\CachedExtensionVSIXs` | 1.41G | 扩展 .vsix 缓存 |
| `%LOCALAPPDATA%\JianyingPro\User Data\Log` | 1.15G | `mssdk_log` 1067MB |
| 360 残留 greencore/secoresdk/GreenCore7z/Microsoft360Office6 | 1.57G | 主程序已卸载，属残留 |
| `%APPDATA%\Code\CachedData`+`Crashpad`+`Cache` | 0.46G | |
| `C:\ProgramData\Intel Package Cache {GUID}` | 0.47G | 需管理员 |
| `%LOCALAPPDATA%\uv\cache` | 2.76G | 用 `uv cache clean` 比手动删快得多 |
| unidic 重复词典（Roaming 那份） | 0.77G | 与 site-packages 逐文件 hash 一致才敢删 |
| scratch-desktop-updater | 0.16G | |
| pnpm store + pnpm-cache | 0.22G | |
| ms-playwright | 0.43G | |
| go-build | 0.11G | |
| anaconda/cm 更新器残包 | 0.28G | |
| Temp（Diagnostics 等） | 0.35G | |
| qtg `*.exe` 目录 ×26 | 0.75G | Roaming 根目录下的解压残留 |
| 空目录 | 2.86 万个 | 实扫 2.8 万，远超预估 5000 |

## 卸载程序（非删目录）

- QQGuild 509MB — NSIS `Uninstall QQGuild.exe /S`
- Teams 211MB — Squirrel `Update.exe --uninstall -s`
- 两者卸载后开始菜单快捷方式、注册表卸载项均正确清理

## 注册表清理

17 个死键（360 全家桶 + 旧版 Teams）：
- HKCU 11 个：直接删（当前用户权限足够）
- HKLM 6 个：生成 `.reg` 让用户双击导入提权删除（`[-HKEY_LOCAL_MACHINE\...]`）
- 全部先 `reg export` 备份到 `registry-backup/`
- **没误伤**：正在使用的 QQ 本体、还装着的 Scratch 3 都完好

## 本机禁删项（踩过的坑）

| 项 | 大小 | 为什么不能删 |
|---|---|---|
| `%APPDATA%\uv` | 1.99G | 是 uv 托管的 **Python 解释器**（含 torch），不是缓存。只有 `uv\cache` 可删 |
| `%APPDATA%\npm` | 1.41G | npm **全局工具目录**（babel/pm2/tsc/vue/wrangler/claude），删了命令全失效 |
| `C:\Windows\Installer` | 1.58G | 删了程序无法卸载/修复 |
| 剪映 `User Data\Projects` | 1.23G | 用户草稿作品 |
| `C:\Program Files\OpenSSH\home\DELL` | 12.57G | 与 `C:\Users\DELL` **inode 相同、无 reparse 属性** = 硬链接别名，不额外占空间 |
| Chrome 1.62G / 微信 xwechat 1.30G | | 个人数据 |
| `.workbuddy\workspace\sessions\<当前会话>` | 1.83G | 正在使用；且历史会话目录里藏过项目的**唯一副本** |

## 本机环境特征

- 三星 PM951 NVMe SSD，C/D/E 三分区，TRIM 已启用 → **不做碎片整理**，只做 TRIM
- 非管理员 shell 下 `sc`/`reg`/`fsutil` 被安全策略拦
- `C:\Python313\python.exe` 是可用的 Python 兜底路径（`where py` 常找不到）
- 删除大量文件时 Defender 实时扫描严重拖慢速度
