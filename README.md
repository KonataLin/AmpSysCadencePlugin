# AmpSys Cadence Plugin

[![Platform](https://img.shields.io/badge/platform-Windows%20x86__64%20%7C%20Linux%20x86__64-2563eb)](#支持范围)
[![Cadence](https://img.shields.io/badge/Cadence-Virtuoso-10b981)](#典型流程)
[![Python](https://img.shields.io/badge/Python-3.8%2B-3776ab)](#快速开始)
[![Core](https://img.shields.io/badge/core-protected%20binary-f59e0b)](#发布包内容)
[![Issues](https://img.shields.io/badge/issues-GitHub-111827)](https://github.com/KonataLin/AmpSysCadencePlugin/issues)

AmpSys Cadence Plugin 是面向 Cadence Virtuoso schematic 的模拟电路尺寸优化插件。
用户在 Virtuoso 中打开待优化 schematic 后，通过 AmpSys 菜单导出当前原理图 netlist；Python GUI 负责配置 LUT cache、器件电流和优化指标；私有 AmpSys core 负责优化；结果可以回填到 schematic。

适合的工作流是：Windows/HSPICE 建 LUT，Linux/Virtuoso 使用已有 LUT cache 进行快速优化和回填。

## 功能亮点

- Cadence 菜单集成：`AmpSys -> Extract Current Schematic...`
- 自动导出当前打开 schematic 的顶层 netlist。
- 单页 GUI：LUT cache、器件电流、spec、运行日志、收敛可视化和结果回填集中在一个流程里。
- Windows 支持 HSPICE LUT 建库；Linux/Virtuoso 侧只使用已有 cache，不再跑 HSPICE。
- 支持 Windows 与 Linux x86_64 release core 自动选择。
- 结果生成 `ampsys_result.il`，可通过 Cadence SKILL 写回 MOS 尺寸参数。
- 详细日志链路，便于定位 Cadence/Python/core 交互问题。

## 支持范围

```text
Windows x86_64
Linux x86_64 + glibc >= 2.17
Python 3.8+
```

暂不支持 ARM、macOS、Alpine/musl、glibc < 2.17、32-bit 系统。

## 发布包内容

GitHub 发布使用完整仓库目录即可：

```text
cli/                    Python GUI 与 runner wrapper
skill/                  Cadence SKILL 菜单、netlist 导出、结果回填
tools/                  环境检查与 Cadence GUI launcher
core/                   已打包的私有 AmpSys core 二进制
install_windows.ps1     Windows 环境配置
install_linux.sh        Linux/Virtuoso 环境配置
Usage.md                详细中文使用说明
```

`core/` 中只包含 release 二进制。不要发布内部源码目录，例如 `AmpSys/`、`yami/`、`TheScanner/`、`acsolver/`。

## 快速开始

Windows：

```powershell
powershell -ExecutionPolicy Bypass -File <plugin-root>\install_windows.ps1 `
  -PluginRoot <plugin-root> `
  -EngineRoot <plugin-root>

py -3 <plugin-root>\tools\check_environment.py
py -3 <plugin-root>\cli\ampsys_gui.py
```

Linux / Virtuoso：

```bash
bash /opt/AmpSysCadencePlugin/install_linux.sh \
  /opt/AmpSysCadencePlugin \
  /opt/AmpSysCadencePlugin \
  ~/.cdsinit

source ~/.bashrc
py -3 /opt/AmpSysCadencePlugin/tools/check_environment.py
```

环境检查需要看到：

```text
"status": "ok"
"tkinter": "ok"
```

## 典型流程

1. Windows GUI 中用 HSPICE 建好 LUT cache。
2. 将完整 cache 目录复制到 Linux。
3. 从已加载 AmpSys 环境变量的 shell 启动 Virtuoso。
4. 打开真正包含待优化 MOS 管的 schematic。
5. 点击 `AmpSys -> Extract Current Schematic...`。
6. GUI 中确认 `LUT Cache` 为 `OK`。
7. 填写每个 MOS 的 `Id uA`，设置 spec 和运行规模。
8. 点击 `Run Optimization`。
9. 完成后点击 `Confirm and Apply in Cadence`，或在 Virtuoso 中点击 `AmpSys -> Apply Last Result`。

## Net 命名约定

硬性全局 net 名：

```text
VDD
GND
Vin
Vout
```

可选 bias/common-mode 名：

```text
Vb_*
Vb_inp
Vb_inn
V_in_cm
V_out_cm
```

`Vb_*`、`Vb_inp`、`Vb_inn` 不是全局必选项。缺少它们不会阻止运行。

## 日志位置

GUI 和 Cadence 交互问题优先看：

```text
<plugin-root>/workspace/ampsys_skill.log
<plugin-root>/workspace/<lib>_<cell>/ampsys_launch.log
<plugin-root>/workspace/<lib>_<cell>/ampsys_gui.log
<plugin-root>/workspace/<lib>_<cell>/ampsys_optimize.log
<plugin-root>/workspace/<lib>_<cell>/telemetry.jsonl
<plugin-root>/workspace/<lib>_<cell>/result.json
<plugin-root>/workspace/<lib>_<cell>/ampsys_result.il
```

## 详细文档

- [Usage.md](Usage.md)：完整安装、Windows 建 LUT、Linux/Virtuoso 使用、命名和日志说明。
- [GitHub Issues](https://github.com/KonataLin/AmpSysCadencePlugin/issues)：反馈问题时请附上相关 log。
- [爱发电 LocyDragon](https://www.afdian.com/a/LocyDragon)：赞助支持。

