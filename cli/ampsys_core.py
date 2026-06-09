#!/usr/bin/env python3
"""Standalone private-core entry point.

This file is safe to publish.  It contains no AmpSys algorithms; release builds
compile it together with the private packages into a native executable.
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path


def _debugger_attached() -> bool:
    if os.environ.get("AMPSYS_ALLOW_DEBUG", "") == "1":
        return False
    if sys.gettrace() is not None:
        return True
    if sys.platform.startswith("win"):
        try:
            return bool(ctypes.windll.kernel32.IsDebuggerPresent())
        except Exception:
            return False
    status = Path("/proc/self/status")
    if status.exists():
        try:
            for line in status.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("TracerPid:"):
                    return int(line.split(":", 1)[1].strip()) != 0
        except Exception:
            return False
    return False


def _add_dev_paths() -> None:
    here = Path(__file__).resolve()
    candidates = []
    try:
        candidates.append(here.parents[2])
    except IndexError:
        pass
    try:
        candidates.append(here.parents[1])
    except IndexError:
        pass
    for cand in candidates:
        if all((cand / pkg).exists() for pkg in ("AmpSys", "yami", "TheScanner", "acsolver")):
            if str(cand) not in sys.path:
                sys.path.insert(0, str(cand))


def main() -> None:
    if _debugger_attached():
        raise SystemExit("AmpSys core refused to run under a debugger.")
    os.environ["AMPSYS_CORE_INTERNAL"] = "1"
    os.environ.setdefault("AMPSYS_NUMBA_CACHE", "0")
    _add_dev_paths()
    from ampsys_runner import main as runner_main

    runner_main(sys.argv[1:])


if __name__ == "__main__":
    main()
