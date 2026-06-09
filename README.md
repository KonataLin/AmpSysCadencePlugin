# AmpSys Cadence Plugin

AmpSys Cadence Plugin 是面向 Cadence Virtuoso schematic 的电路尺寸优化插件。
用户在 Virtuoso 中打开待优化 schematic 后，通过 AmpSys 菜单导出当前原理图 netlist，Python GUI 负责配置 LUT cache、器件电流和优化指标，私有 AmpSys core 负责实际优化，结果可回填到 schematic。

## 发布包内容

GitHub 发布时建议使用完整 release 目录：

```text
AmpSysCadencePlugin_release/
  cli/
  skill/
  tools/
  core/
    windows_amd64/ampsys_core.exe
    linux_x86_64/ampsys_core
  install_windows.ps1
  install_linux.sh
  README.md
  Usage.md
  Install.md
  release_manifest.json
```

其中 `cli/`、`skill/`、`tools/check_environment.py`、文档和安装脚本是公开部分。
`core/` 中是已经打包的私有核心；不要发布 `AmpSys/`、`yami/`、`TheScanner/`、`acsolver/` 等内部源码目录。

## 支持范围

```text
Windows x86_64
Linux x86_64 + glibc >= 2.17
```

不支持 ARM、macOS、Alpine/musl、glibc < 2.17 或 32-bit 系统。

## 快速安装

Windows：

```powershell
powershell -ExecutionPolicy Bypass -File <plugin-root>\install_windows.ps1 `
  -PluginRoot <plugin-root> `
  -EngineRoot <plugin-root>
py -3 <plugin-root>\tools\check_environment.py
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

如果工程目录有自己的 `.cdsinit`，需要把安装命令第三个参数换成该工程的 `.cdsinit`，或者在工程 `.cdsinit` 中手动加入：

```skill
load(strcat(getShellEnvVar("AMPSYS_PLUGIN_ROOT") "/skill/ampsys_init.il"))
```

## 基本流程

1. Windows GUI 中用 HSPICE 建好 LUT cache。
2. 把完整 cache 目录复制到 Linux。
3. 从已加载环境的 shell 启动 Virtuoso。
4. 打开真正包含待优化 MOS 的 schematic。
5. 点击 `AmpSys -> Extract Current Schematic...`。
6. GUI 中确认 `LUT Cache` 为 OK，填写每个 MOS 的 `Id uA` 和 specs。
7. 点击 `Run Optimization`。
8. 完成后点击 `Confirm and Apply in Cadence`，或在 Virtuoso 中点击 `AmpSys -> Apply Last Result`。

详细说明见 [Usage.md](Usage.md)。

