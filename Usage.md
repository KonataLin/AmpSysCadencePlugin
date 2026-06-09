# AmpSys Cadence Plugin 使用说明

## 1. 支持和目录

支持：

```text
Windows x86_64
Linux x86_64 + glibc >= 2.17
```

不支持 ARM、macOS、Alpine/musl、glibc < 2.17、32-bit 系统。

安装时整个 release 目录要一起复制。正常情况下：

```text
PluginRoot = <plugin-root>
EngineRoot = <plugin-root>
```

## 2. 安装

Windows：

```powershell
powershell -ExecutionPolicy Bypass -File <plugin-root>\install_windows.ps1 `
  -PluginRoot <plugin-root> `
  -EngineRoot <plugin-root>
py -3 <plugin-root>\tools\check_environment.py
```

Linux，必须用真正跑 Virtuoso 的用户执行：

```bash
bash /opt/AmpSysCadencePlugin/install_linux.sh \
  /opt/AmpSysCadencePlugin \
  /opt/AmpSysCadencePlugin \
  ~/.cdsinit
source ~/.bashrc
py -3 /opt/AmpSysCadencePlugin/tools/check_environment.py
```

检查结果需要有：

```text
"status": "ok"
"tkinter": "ok"
```

## 3. Windows 建 Cache

启动 GUI：

```powershell
py -3 <plugin-root>\cli\ampsys_gui.py
```

Windows 建库填写：

```text
Model path, HSPICE dir, Cache dir
NMOS name, PMOS name, Corner/lib, Temp C, VDD V
```

点击 `Build Library`。建好后的 cache 目录应包含：

```text
nmos_*.pkl
pmos_*.pkl
nmos_*_data/
pmos_*_data/
```

如果通过 examples 建库，复制 example 的 `temp_dir` 下的：

```text
<temp-dir>/autoflow_cache
```

## 4. 复制 Cache 到 Linux

复制整个 cache 目录，不要只复制 pkl。推荐放到：

```text
~/ampsys_lut/<pdk>_<corner>
```

Linux GUI 里重新选择 Linux 本机路径，不要沿用 Windows 盘符路径。

## 5. Linux / Virtuoso 使用

Linux 侧不跑 HSPICE，不需要 PDK model path，只使用 Windows 已建好的 cache。

从已加载环境的 shell 启动 Virtuoso：

```bash
source ~/.bashrc
virtuoso &
```

打开 schematic，点击：

```text
AmpSys -> Extract Current Schematic...
```

GUI 会自动解析当前 schematic 顶层 netlist。用户不需要处理 netlist 文件，也不需要 Parse。

注意：当前版本导出的是正在打开的这一层 schematic。请打开真正包含 MOS 管的运放 schematic；如果打开的是 testbench 或只包含子模块的顶层，GUI 可能解析不到 MOS。

Linux GUI 第一块是 `LUT Cache`，只填：

```text
Cache dir
NMOS name
PMOS name
Corner/lib
Temp C
VDD V
```

Linux GUI 不显示 `Model path`、`HSPICE dir`、`Build Library`。`LUT Cache` 变成勾以后才能 Run。

## 6. Run 和回填

Devices 里每个 MOS 必填：

```text
Id uA
```

Specs 常用字段：

```text
常用目标：Gain min dB, GBW MHz, PM min deg, Load cap pF
可选初猜：V in cm, V out cm, Saturation margin
运行规模：Population, Generations
```

运行：

```text
Run Optimization
```

完成后：

```text
Confirm and Apply in Cadence
```

如果没自动回填，在 Virtuoso 里点：

```text
AmpSys -> Apply Last Result
```

## 7. 命名和日志

硬性 net 名：

```text
VDD, GND, Vin, Vout
```

可选/架构相关 net 名：

```text
Vb_*      用作 bias 节点命名；如果存在，AmpSys 会按 bias 规则处理
Vb_inp    某些折叠/差分架构会用到，不是全局必选
Vb_inn    某些差分/CMRR 场景会用到，不是全局必选
```

MOS model 名称必须和 GUI 的 `NMOS name` / `PMOS name` 一致。多个名称用逗号分隔。

出错看当前工程目录：

```text
ampsys_gui.log
ampsys_build-library.log
ampsys_optimize.log
telemetry.jsonl
result.json
ampsys_result.il
```

## 8. 工程 `.cdsinit` 注意事项

如果某个 Virtuoso 工程目录本身带有 `.cdsinit`，Cadence 可能优先加载工程目录的 `.cdsinit`，导致用户 home 目录下的 `~/.cdsinit` 没有生效。此时需要把 AmpSys loader 也加入工程 `.cdsinit`：

```skill
load(strcat(getShellEnvVar("AMPSYS_PLUGIN_ROOT") "/skill/ampsys_init.il"))
```

或者重新运行 Linux 安装脚本，把第三个参数指定为工程 `.cdsinit`：

```bash
bash /opt/AmpSysCadencePlugin/install_linux.sh \
  /opt/AmpSysCadencePlugin \
  /opt/AmpSysCadencePlugin \
  /path/to/project/.cdsinit
```

## 9. 反馈和支持

- 项目主页：https://github.com/KonataLin/AmpSysCadencePlugin
- 问题反馈：https://github.com/KonataLin/AmpSysCadencePlugin/issues
- 赞助支持：https://www.afdian.com/a/LocyDragon
