# 安装说明

仅适用于 Linux / WSL。Windows 侧无需按本文安装。

## 快速安装

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

打开 schematic 后，菜单栏应出现 `AmpSys`。

## 安装脚本参数

```bash
bash install_linux.sh [install_root] [engine_root] [cdsinit_path]
```

上面是 3 个位置参数，不是通配符：

- `install_root`：插件安装目录。默认 `/opt/AmpSysCadencePlugin`。如果不传且没有 `/opt` 写权限，会自动安装到 `$HOME/.local/share/AmpSysCadencePlugin`。
- `engine_root`：核心引擎目录。默认等于 `install_root`，一般留空。
- `cdsinit_path`：Cadence 启动脚本路径。默认 `$HOME/.cdsinit`。如果文件不存在，脚本会自动创建并追加 AmpSys 菜单加载语句。

常见安装方式：

```bash
# 系统级安装
sudo bash install_linux.sh /opt/AmpSysCadencePlugin

# 用户目录安装
bash install_linux.sh "$HOME/.local/share/AmpSysCadencePlugin"

# 指定 .cdsinit
bash install_linux.sh "$HOME/.local/share/AmpSysCadencePlugin" "" "$HOME/.cdsinit"
```

也可以用环境变量传默认值：

```bash
# 不传第一个位置参数时使用
AMPSYS_INSTALL_ROOT="$HOME/.local/share/AmpSysCadencePlugin" bash install_linux.sh

# 手动指定 Python
AMPSYS_PYTHON3=/usr/bin/python3.13 bash install_linux.sh /opt/AmpSysCadencePlugin
```

## Python 要求

需要 Python `>= 3.8`。

脚本会自动寻找 Conda、Miniconda、`python3.12`、`python3.11`、`python3.10`、`python3.9`、`python3.8` 和 `python3`。

如果未来 Python 版本大于 3.12，例如系统的 `python3` 指向 Python 3.13/3.14，只要版本 `>= 3.8` 就可以正常使用。

如果机器上只有 `python3.13` 这类命令、但没有 `python3` 软链接，请手动指定：

```bash
AMPSYS_PYTHON3=/usr/bin/python3.13 bash install_linux.sh /opt/AmpSysCadencePlugin
```

## 注意事项

- PDK model file 需要在 GUI 中手动选择。
- Spectre executable 可以手动选择，也可以用 AutoSearch。
- 本插件不包含任何 PDK、工艺库文件或仿真器。
- 如果自定义 `.cdsinit路径` 的目录不存在或没有写权限，安装会失败；请先创建目录或换到可写路径。
