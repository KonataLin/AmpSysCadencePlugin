# Contributing

Thanks for helping improve AmpSys Cadence Plugin.

## Project Boundary

This public repository contains GUI code, Cadence SKILL integration, wrappers, installers, and documentation.

Do not commit internal AmpSys algorithm source packages:

- `AmpSys/`
- `yami/`
- `TheScanner/`
- `acsolver/`

Protected core binaries are distributed through GitHub Releases, not as ordinary source files in the repository.

## Useful Checks

Before submitting changes:

```powershell
py -3 -B -c "from pathlib import Path; [compile(Path(p).read_text(encoding='utf-8-sig'), p, 'exec') for p in ['cli/ampsys_gui.py', 'cli/ampsys_runner.py']]"
```

For Cadence/SKILL changes, test in Virtuoso when possible and attach:

```text
ampsys_skill.log
ampsys_launch.log
ampsys_gui.log
ampsys_optimize.log
telemetry.jsonl
result.json
ampsys_result.il
```

## Compatibility Reports

PDKs and Cadence versions vary a lot. Compatibility reports are welcome, especially for:

- CDF parameter names and aliases
- terminal order conventions
- Windows HSPICE path layouts
- Linux glibc / desktop / X11 behavior

