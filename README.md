<div align="center">

# AmpSys Cadence Plugin

**从 Cadence Virtuoso schematic 到 AmpSys 自动尺寸优化的一体化插件**

[![Website](https://img.shields.io/badge/官网-ampsys.aiclab.top-f97316)](http://ampsys.aiclab.top)
[![Release](https://img.shields.io/github/v/release/KonataLin/AmpSysCadencePlugin?include_prereleases&label=release&color=2563eb)](https://github.com/KonataLin/AmpSysCadencePlugin/releases)
[![Tested](https://img.shields.io/badge/tested-SMIC18MMRF%20180nm-10b981)](https://github.com/KonataLin/AmpSysCadencePlugin/releases/tag/v0.1.0-alpha.3)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-111827)](#平台支持)
[![Issues](https://img.shields.io/github/issues/KonataLin/AmpSysCadencePlugin?label=issues&color=dc2626)](https://github.com/KonataLin/AmpSysCadencePlugin/issues)

<p>
  <a href="http://ampsys.aiclab.top"><b>官方网站</b></a>
  &nbsp;·&nbsp;
  <a href="Usage.md"><b>使用指南</b></a>
  &nbsp;·&nbsp;
  <a href="https://github.com/KonataLin/AmpSysCadencePlugin/releases"><b>下载 Release</b></a>
  &nbsp;·&nbsp;
  <a href="https://github.com/KonataLin/AmpSysCadencePlugin/issues/new/choose"><b>反馈问题</b></a>
  &nbsp;·&nbsp;
  <a href="https://www.afdian.com/a/LocyDragon"><b>赞助支持</b></a>
</p>

</div>

---

> [!IMPORTANT]
> **重要声明 / Safety Notice**
>
> AmpSys Cadence Plugin 只用于模拟电路**初版设计辅助、尺寸探索和流程验证**，不能替代设计者的完整仿真、版图验证、PVT/Monte Carlo/可靠性检查和流片前 sign-off。
>
> 本插件不包含任何工艺库文件、PDK、model 文件或商业仿真器，也不提供 HSPICE、Cadence、Spectre、Calibre 等第三方软件授权。用户必须自行合法配置工艺库、仿真器和 EDA 环境。
>
> 本插件始终以公益免费方式提供。因使用、本地修改、二次分发或误用本插件造成的设计错误、项目延期、流片失败、经济损失或其它后果，作者概不负责。

AmpSys Cadence Plugin 把 **Virtuoso schematic 抽取**、**Python GUI 配置**、**AmpSys protected core 优化**、**SKILL 尺寸写回** 串成一个可重复的闭环。典型工作流是：用 Windows/HSPICE 或 Linux/WSL Spectre 建 LUT，Linux/Virtuoso 侧使用 fast LUT 优化并写回器件尺寸。

## 平台支持

| 平台 | 状态 | 用途 |
| --- | --- | --- |
| Windows x86_64 | 支持 | standalone GUI、HSPICE LUT 建表、环境检查 |
| Linux x86_64, glibc >= 2.17 | 支持 | Virtuoso 菜单、standalone GUI、Spectre LUT 建表、fast LUT 优化、SKILL 写回 |
| macOS / ARM / Alpine musl / 32-bit | 暂不支持 | 当前没有对应 protected core |

## 获取方式

普通用户请下载 GitHub Release 里的完整 zip：

[下载最新 Release](https://github.com/KonataLin/AmpSysCadencePlugin/releases/latest)

Release 包应包含：

```text
cli/                    GUI 脚本与公开 runner wrapper
gui/                    Windows/Linux standalone GUI
skill/                  Virtuoso 菜单、schematic 抽取、结果写回
tools/                  环境检查与 GUI launcher
core/                   Windows/Linux protected AmpSys core
install_windows.ps1     Windows 安装脚本
install_linux.sh        Linux/Virtuoso 安装脚本
Usage.md                中文使用指南
README.md               项目主页
```

> 直接 `git clone` 得到的是公开 wrapper 和工程文件，不包含可发布的 protected core。最终用户请优先使用 Release zip。

## Linux / WSL 安装

完整说明见 [INSTALL.md](INSTALL.md)。常用安装流程如下：

```bash
unzip AmpSysCadencePlugin_release.zip -d ~/AmpSysCadencePlugin_release
cd ~/AmpSysCadencePlugin_release
sudo bash install_linux.sh /opt/AmpSysCadencePlugin
source ~/.bashrc
```

在 Cadence 工程目录启动 Virtuoso：

```bash
cd ~/Desktop/SDADC
virtuoso &
```

`install_linux.sh` 支持 3 个位置参数：

```bash
bash install_linux.sh [install_root] [engine_root] [cdsinit_path]
```

- `install_root`：插件安装目录，默认 `/opt/AmpSysCadencePlugin`；没有 `/opt` 写权限且未显式传参时，会自动使用 `$HOME/.local/share/AmpSysCadencePlugin`。
- `engine_root`：核心引擎目录，默认等于 `install_root`，通常留空。
- `cdsinit_path`：Cadence 启动脚本，默认 `$HOME/.cdsinit`，不存在会自动创建。

Python 要求 `>= 3.8`。如果未来系统 `python3` 指向 Python 3.13/3.14，也可以正常使用；如果只有 `python3.13` 命令而没有 `python3` 软链接，可用：

```bash
AMPSYS_PYTHON3=/usr/bin/python3.13 bash install_linux.sh /opt/AmpSysCadencePlugin
```
## 快速开始

Windows 安装：

```powershell
powershell -ExecutionPolicy Bypass -File <plugin-root>\install_windows.ps1 `
  -PluginRoot <plugin-root> `
  -EngineRoot <plugin-root>

py -3 <plugin-root>\tools\check_environment.py
```

Linux / Virtuoso 安装：

```bash
bash <plugin-root>/install_linux.sh \
  <plugin-root> \
  <plugin-root> \
  ~/.cdsinit

source ~/.bashrc
py -3 <plugin-root>/tools/check_environment.py
```

完整流程请看：[Usage.md](Usage.md)

## 推荐工作流

1. 在 GUI 中手动选择 PDK 的 HSPICE/Spectre `.lib/.scs` model 文件，并填写 NMOS/PMOS 名称、工艺角、温度和 cache 目录。
2. 用 Windows/HSPICE 或 Linux/WSL Spectre 点击 `Build Library` 生成 LUT cache。
3. 将完整 cache 目录复制到 Linux。
4. 在 Linux/Virtuoso 中打开待优化 schematic。
5. 点击 `AmpSys -> Extract Current Schematic...`。
6. 在 GUI 里确认 LUT、器件、电流、spec 和权重。
7. 点击 `Run Optimization`。
8. 结果完成后点击 `Confirm and Apply in Cadence` 写回 CDF 参数。

## 日志与诊断

如果遇到问题，请在 Issue 中附上相关日志：

```text
ampsys_skill.log
ampsys_launch.log
ampsys_gui.log
ampsys_optimize.log
telemetry.jsonl
result.json
ampsys_result.il
```

常用入口：

```text
Windows GUI: gui/windows_amd64/ampsys_gui/ampsys_gui.exe
Linux GUI:   gui/linux_x86_64/ampsys_gui/ampsys_gui
Env check:   tools/check_environment.py
```

## 反馈与支持

- 官方网站：[ampsys.aiclab.top](http://ampsys.aiclab.top)
- Bug / 兼容性问题：[GitHub Issues](https://github.com/KonataLin/AmpSysCadencePlugin/issues/new/choose)
- Release 下载：[GitHub Releases](https://github.com/KonataLin/AmpSysCadencePlugin/releases)
- 赞助支持：[爱发电 LocyDragon](https://www.afdian.com/a/LocyDragon)
