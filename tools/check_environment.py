#!/usr/bin/env python3
"""AmpSys release environment checker."""

from __future__ import annotations

import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "cli" / "ampsys_runner.py"
LOG = ROOT / "ampsys_environment.log"


def command_available(cmd: list[str]) -> bool:
    if not cmd:
        return False
    exe = cmd[0]
    return Path(exe).expanduser().exists() or shutil.which(exe) is not None


def python_command_works(cmd: list[str]) -> bool:
    if not command_available(cmd):
        return False
    try:
        proc = subprocess.run(
            cmd + ["-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        return proc.returncode == 0
    except Exception:
        return False


def py_command() -> list[str]:
    candidates: list[list[str]] = []
    configured = os.environ.get("AMPSYS_PYCMD", "").strip()
    if configured:
        candidates.append(shlex.split(configured, posix=(os.name != "nt")))

    if os.name == "nt":
        candidates.extend([["py", "-3"], [sys.executable], ["python"]])
    else:
        candidates.extend([
            ["py", "-3"],
            [str(Path.home() / "bin" / "py"), "-3"],
            [sys.executable],
            ["python3.12"],
            ["python3.11"],
            ["python3.10"],
            ["python3.9"],
            ["python3.8"],
            ["python3"],
        ])

    seen = set()
    for cmd in candidates:
        cmd = [x for x in cmd if x]
        key = tuple(cmd)
        if key in seen:
            continue
        seen.add(key)
        if python_command_works(cmd):
            return cmd
    return [sys.executable] if sys.executable else ["python3"]


def write_first_available(payload: dict[str, object], candidates: list[Path]) -> Path:
    errors: list[dict[str, str]] = []
    for path in candidates:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if errors:
                payload["log_fallback_errors"] = errors
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            return path
        except Exception as exc:
            errors.append({"path": str(path), "error": str(exc)})
    fallback = Path.home() / "ampsys_environment.log"
    payload["log_fallback_errors"] = errors
    fallback.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return fallback


def main() -> int:
    payload: dict[str, object] = {
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "plugin_root": str(ROOT),
        "runner": str(RUNNER),
        "gui_python": sys.version,
        "gui_python_executable": sys.executable,
        "system": platform.platform(),
        "machine": platform.machine(),
        "status": "unknown",
    }
    try:
        import tkinter  # noqa: F401
        payload["tkinter"] = "ok"
    except Exception as exc:
        payload["tkinter"] = "error"
        payload["tkinter_error"] = str(exc)

    cmd = py_command() + [str(RUNNER), "self-test"]
    payload["self_test_command"] = cmd
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=120,
        )
        payload["self_test_returncode"] = proc.returncode
        payload["self_test_output"] = proc.stdout
        try:
            payload["self_test_json"] = json.loads(proc.stdout)
        except Exception:
            pass
        payload["status"] = "ok" if proc.returncode == 0 and payload.get("tkinter") == "ok" else "error"
    except Exception as exc:
        payload["status"] = "error"
        payload["error"] = str(exc)

    log_path = write_first_available(payload, [LOG, ROOT / "workspace" / "ampsys_environment.log"])
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"\n[AmpSys] Environment log: {log_path}")
    return 0 if payload["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
