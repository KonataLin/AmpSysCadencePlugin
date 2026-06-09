<div align="center">

# AmpSys Cadence Plugin

**Cadence Virtuoso 原理图驱动的 AmpSys 自动尺寸优化插件**

从 schematic 抽取网表，在 Python GUI 中配置 LUT cache、器件电流和指标，调用受保护的 AmpSys core 完成优化，并通过 SKILL 将结果写回 Cadence。

[![Alpha Release](https://img.shields.io/github/v/release/KonataLin/AmpSysCadencePlugin?include_prereleases&label=alpha%20release&color=f59e0b)](https://github.com/KonataLin/AmpSysCadencePlugin/releases/tag/v0.1.0-alpha.0)
[![Platform](https://img.shields.io/badge/platform-Windows%20x86__64%20%7C%20Linux%20x86__64-2563eb)](#平台支持)
[![Cadence](https://img.shields.io/badge/Cadence-Virtuoso-10b981)](#cadence-内使用)
[![Python](https://img.shields.io/badge/Python-3.8%2B-3776ab)](#快速开始)
[![Core](https://img.shields.io/badge/core-protected%20binary-111827)](#发布包结构)
[![Issues](https://img.shields.io/github/issues/KonataLin/AmpSysCadencePlugin?label=issues&color=dc2626)](https://github.com/KonataLin/AmpSysCadencePlugin/issues)

<p>
  <a href="https://github.com/KonataLin/AmpSysCadencePlugin/releases/tag/v0.1.0-alpha.0"><b>下载 Alpha 包</b></a>
  &nbsp;|&nbsp;
  <a href="Usage.md"><b>中文使用说明</b></a>
  &nbsp;|&nbsp;
  <a href="https://github.com/KonataLin/AmpSysCadencePlugin/issues"><b>提交 Issue</b></a>
  &nbsp;|&nbsp;
  <a href="https://www.afdian.com/a/LocyDragon"><b>赞助支持</b></a>
</p>

</div>

---

> [!CAUTION]
> `v0.1.0-alpha.0` 是未完成真实项目全流程验证的集成测试版本。它适合用于安装、建 LUT、Virtuoso 抽取、优化、日志诊断和回填测试；在你完成本地验证前，不要把它当作稳定生产版本。

## 一眼看懂

```mermaid
flowchart LR
  A["Windows<br/>HSPICE 建 LUT"] --> B["复制完整<br/>autoflow_cache"]
  B --> C["Linux / Virtuoso<br/>打开 schematic"]
  C --> D["AmpSys 菜单<br/>抽取当前原理图"]
  D --> E["Python GUI<br/>检查 cache 与器件电流"]
  E --> F["Protected Core<br/>优化与实时 telemetry"]
  F --> G["SKILL Writeback<br/>写回 W/L/NF 参数"]
```

| 角色 | 它负责什么 |
| --- | --- |
| Cadence / SKILL | 菜单入口、当前 schematic 抽取、结果回填 |
| Python GUI | 单页流程、cache 检查、器件电流、spec、进度、日志和可视化 |
| AmpSys Core | 私有优化算法、LUT 查询、收敛和结果生成 |
| Release Wrapper | Windows/Linux 自动选择二进制 core，降低 Python 版本绑定风险 |

## 为什么需要它

| 常见痛点 | AmpSys Cadence Plugin 的处理方式 |
| --- | --- |
| 手动改 MOS 尺寸、反复跑仿真很慢 | 从 schematic 抽取器件，优化后自动生成写回脚本 |
| HSPICE 建表和 Virtuoso 使用环境经常不在同一台机器 | Windows 建 LUT，Linux/Virtuoso 只消费 cache |
| GUI 出错容易静默，Cadence/Python/core 之间很难定位 | 每个环节都落 `.log`、`telemetry.jsonl`、`result.json` |
| 算法不能开源，但用户又需要能安装和调试 | GUI/SKILL/wrapper 开源，核心算法以受保护二进制发布 |

## 功能亮点

| 模块 | 能力 |
| --- | --- |
| Cadence 菜单 | `AmpSys -> Extract Current Schematic...`、`Open AmpSys GUI`、`Apply Last Result` |
| 网表抽取 | 直接抽取当前打开的 schematic 层级，不要求用户手动找 netlist |
| 单页 GUI | LUT cache、器件电流、关键 spec、运行规模、日志和回填集中在一个流程里 |
| Cache 工作流 | Windows/HSPICE 建库；Linux/Virtuoso 侧 cache-only，不再要求 model path 或 HSPICE |
| 优化可视化 | 进度条、收敛曲线、population 指标视图、详细 runner 输出 |
| 参数回填 | 支持常见 CDF 参数别名，例如 `w/W`、`l/L`、`nf/fingers` |
| 诊断日志 | `ampsys_skill.log`、`ampsys_launch.log`、`ampsys_gui.log`、`ampsys_optimize.log` 等 |

## 平台支持

| 平台 | 状态 | 说明 |
| --- | --- | --- |
| Windows x86_64 | 支持 | GUI、HSPICE LUT 建库、环境检查 |
| Linux x86_64, glibc >= 2.17 | 支持 | Virtuoso 集成、cache-only 优化、SKILL 回填 |
| Linux x86_64, glibc < 2.17 | 不支持 | 当前 release core 未面向更旧 glibc 构建 |
| ARM / macOS / Alpine musl / 32-bit | 不支持 | 当前没有对应 release core |

## 下载

请优先使用 GitHub Release 里的完整压缩包，而不是 GitHub 自动生成的 Source code 包：

| 推荐下载 | 链接 |
| --- | --- |
| 完整 Alpha 测试包，包含 Windows/Linux 受保护 core | [AmpSysCadencePlugin_v0.1.0-alpha.0.zip](https://github.com/KonataLin/AmpSysCadencePlugin/releases/download/v0.1.0-alpha.0/AmpSysCadencePlugin_v0.1.0-alpha.0.zip) |

如果你使用 `git clone`，需要确保 Git LFS 已安装，并拉取到 `core/windows_amd64/ampsys_core.exe` 与 `core/linux_x86_64/ampsys_core`。

## 快速开始

### Windows

```powershell
powershell -ExecutionPolicy Bypass -File <plugin-root>\install_windows.ps1 `
  -PluginRoot <plugin-root> `
  -EngineRoot <plugin-root>

py -3 <plugin-root>\tools\check_environment.py
py -3 <plugin-root>\cli\ampsys_gui.py
```

Windows 侧主要用于 HSPICE LUT cache 建库。建好后复制完整 cache 目录到 Linux。

### Linux / Virtuoso

```bash
bash /opt/AmpSysCadencePlugin/install_linux.sh \
  /opt/AmpSysCadencePlugin \
  /opt/AmpSysCadencePlugin \
  ~/.cdsinit

source ~/.bashrc
py -3 /opt/AmpSysCadencePlugin/tools/check_environment.py
```

环境检查至少应看到：

```text
"status": "ok"
"tkinter": "ok"
```

## Cadence 内使用

1. 在 Windows GUI 中用 HSPICE 建好 LUT cache。
2. 将完整 cache 目录复制到 Linux。
3. 从已经加载 AmpSys 环境变量的 shell 启动 `virtuoso`。
4. 打开真正包含待优化 MOS 管的 schematic。
5. 点击 `AmpSys -> Extract Current Schematic...`。
6. 在 GUI 里确认 `LUT Cache = OK`。
7. 为每个 MOS 填写 `Id uA`，再设置关键 spec 和运行规模。
8. 点击 `Check Setup`，确认流程状态都通过。
9. 点击 `Run Optimization`。
10. 优化完成后点击 `Confirm and Apply in Cadence`，或在 Virtuoso 中点击 `AmpSys -> Apply Last Result`。

## 命名约定

硬性全局 net 名：

```text
VDD
GND
Vin
Vout
```

可选结构相关 net 名：

```text
Vb_*
Vb_inp
Vb_inn
V_in_cm
V_out_cm
```

`Vb_*`、`Vb_inp`、`Vb_inn` 不是全局必选项。没有这些 net 时，GUI 不应该只因为它们缺失而阻止优化。

## 发布包结构

```text
cli/                    Python GUI 与开放 runner wrapper
skill/                  Cadence SKILL 菜单、抽取和回填
tools/                  环境检查与 GUI launcher
core/                   Windows/Linux 受保护 AmpSys core 二进制
install_windows.ps1     Windows 环境配置脚本
install_linux.sh        Linux/Virtuoso 环境配置脚本
Usage.md                完整中文使用说明
release_manifest.json   release 构建元数据
```

不要发布内部源码目录，例如 `AmpSys/`、`yami/`、`TheScanner/`、`acsolver/`。当前仓库面向用户发布的是 GUI、SKILL、wrapper、安装脚本和受保护 core。

## 日志与反馈

反馈问题时，请尽量附上相关日志：

```text
<plugin-root>/workspace/ampsys_skill.log
<plugin-root>/workspace/<lib>_<cell>/ampsys_launch.log
<plugin-root>/workspace/<lib>_<cell>/ampsys_gui.log
<plugin-root>/workspace/<lib>_<cell>/ampsys_optimize.log
<plugin-root>/workspace/<lib>_<cell>/telemetry.jsonl
<plugin-root>/workspace/<lib>_<cell>/result.json
<plugin-root>/workspace/<lib>_<cell>/ampsys_result.il
```

| 资源 | 地址 |
| --- | --- |
| 完整使用说明 | [Usage.md](Usage.md) |
| 问题反馈 | [GitHub Issues](https://github.com/KonataLin/AmpSysCadencePlugin/issues) |
| 赞助支持 | [爱发电 LocyDragon](https://www.afdian.com/a/LocyDragon) |
