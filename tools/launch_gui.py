#!/usr/bin/env python3
"""Small detached launcher for Cadence -> AmpSys GUI.

Cadence SKILL `system()` calls are shell-dependent.  This script keeps the
SKILL command simple, starts the real Tk GUI with the current Python runtime,
connects GUI stdout/stderr to a launch log, and exits immediately.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def append_log(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", errors="replace") as fp:
        fp.write(f"{now()} [AmpSys launcher] {json.dumps(payload, ensure_ascii=False)}\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch AmpSys GUI from Cadence.")
    parser.add_argument("--gui", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("gui_args", nargs=argparse.REMAINDER)
    return parser.parse_args()


def clean_remainder(items: List[str]) -> List[str]:
    if items and items[0] == "--":
        return items[1:]
    return items


def python_executable() -> str:
    if sys.executable:
        return sys.executable
    configured = os.environ.get("AMPSYS_PYTHON3", "").strip()
    if configured:
        return configured
    return "python" if os.name == "nt" else "python3"


def main() -> int:
    args = parse_args()
    gui = Path(args.gui).expanduser().resolve()
    log_path = Path(args.log).expanduser().resolve()
    gui_args = clean_remainder(list(args.gui_args))
    plugin_root = gui.parents[1] if len(gui.parents) > 1 else gui.parent
    pyexe = python_executable()
    cmd = [pyexe, str(gui), *gui_args]

    context = {
        "event": "start",
        "cmd": cmd,
        "gui": str(gui),
        "log": str(log_path),
        "cwd": str(plugin_root),
        "launcher_python": sys.executable,
        "spawn_python": pyexe,
        "launcher_version": sys.version,
        "platform": sys.platform,
        "env": {
            "AMPSYS_PLUGIN_ROOT": os.environ.get("AMPSYS_PLUGIN_ROOT", ""),
            "AMPSYS_ENGINE_ROOT": os.environ.get("AMPSYS_ENGINE_ROOT", ""),
            "AMPSYS_CORE_ROOT": os.environ.get("AMPSYS_CORE_ROOT", ""),
            "AMPSYS_PYCMD": os.environ.get("AMPSYS_PYCMD", ""),
            "AMPSYS_PYTHON3": os.environ.get("AMPSYS_PYTHON3", ""),
            "DISPLAY": os.environ.get("DISPLAY", ""),
        },
    }
    append_log(log_path, context)

    if not gui.is_file():
        append_log(log_path, {"event": "error", "error": f"GUI script not found: {gui}"})
        return 2

    log_file = None
    try:
        log_file = log_path.open("a", encoding="utf-8", errors="replace")
        kwargs: Dict[str, Any] = {
            "cwd": str(plugin_root),
            "stdout": log_file,
            "stderr": subprocess.STDOUT,
            "stdin": subprocess.DEVNULL,
            "close_fds": os.name != "nt",
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(cmd, **kwargs)
        log_file.write(f"{now()} [AmpSys launcher] {json.dumps({'event': 'spawned', 'pid': proc.pid}, ensure_ascii=False)}\n")
        log_file.flush()
        log_file.close()
        print(json.dumps({"status": "spawned", "pid": proc.pid, "log": str(log_path)}, ensure_ascii=False))
        return 0
    except Exception as exc:
        if log_file:
            try:
                log_file.close()
            except Exception:
                pass
        append_log(log_path, {"event": "error", "error": str(exc), "traceback": traceback.format_exc()})
        print(json.dumps({"status": "error", "error": str(exc), "log": str(log_path)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
