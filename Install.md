# Install

Linux / WSL only.

## 1. Extract

```bash
unzip AmpSysCadencePlugin_release.zip -d ~/AmpSysCadencePlugin_release
cd ~/AmpSysCadencePlugin_release
```

## 2. Install

```bash
sudo bash install_linux.sh /opt/AmpSysCadencePlugin
source ~/.bashrc
```

## 3. Start Cadence

Run Virtuoso from your design workspace:

```bash
cd ~/Desktop/SDADC
virtuoso &
```

Open a schematic and use the `AmpSys` menu.

## Notes

- Select the PDK model file manually in the GUI.
- Spectre can be selected manually or found with AutoSearch.
- The plugin does not include any PDK files or simulators.
