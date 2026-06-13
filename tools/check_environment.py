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
import tempfile
import time
from pathlib import Path


ROOT = Path(os.environ.get("AMPSYS_PLUGIN_ROOT", "") or Path(__file__).resolve().parents[1]).expanduser().resolve()
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


def split_command(text: str) -> list[str]:
    parts = shlex.split(text, posix=(os.name != "nt"))
    if os.name == "nt":
        parts = [part.strip().strip('"').strip("'") for part in parts]
    return parts


def py_command() -> list[str]:
    candidates: list[list[str]] = []
    configured = os.environ.get("AMPSYS_PYCMD", "").strip()
    if configured:
        candidates.append(split_command(configured))

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
    fallback = Path.cwd() / "ampsys_environment.log"
    payload["log_fallback_errors"] = errors
    try:
        fallback.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return fallback
    except Exception as exc:
        errors.append({"path": str(fallback), "error": str(exc)})
    fallback = Path.home() / "ampsys_environment.log"
    payload["log_fallback_errors"] = errors
    try:
        fallback.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return fallback
    except Exception as exc:
        errors.append({"path": str(fallback), "error": str(exc)})
    tmp_fallback = Path(tempfile.gettempdir()) / "ampsys_environment.log"
    tmp_fallback.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return tmp_fallback


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

    cmd = py_command()
    payload["python_command"] = cmd
    quick_code = (
        "import json, os, sys; "
        f"sys.path.insert(0, {str(ROOT / 'cli')!r}); "
        "import ampsys_gui, ampsys_runner; "
        "os.environ['AMPSYS_PLUGIN_ROOT']=str(ampsys_gui.ROOT); "
        "os.environ['AMPSYS_ENGINE_ROOT']=str(ampsys_gui.DEFAULT_ENGINE_ROOT); "
        "core=ampsys_runner.find_core_executable(); "
        "print(json.dumps({"
        "'gui_import':'ok',"
        "'default_engine_root':str(ampsys_gui.DEFAULT_ENGINE_ROOT),"
        "'default_cache_dir':str(ampsys_gui.default_lut_cache_dir(ampsys_gui.WORKSPACE)),"
        "'default_temp_dir':str(ampsys_gui.default_runtime_temp_dir(ampsys_gui.WORKSPACE)),"
        "'objective_weight_defaults':ampsys_gui.OBJECTIVE_WEIGHT_DEFAULTS,"
        "'core_executable':str(core or ''),"
        "'source_engine':ampsys_runner.has_source_engine(ampsys_gui.DEFAULT_ENGINE_ROOT),"
        "'compiled_engine':ampsys_runner.has_compiled_engine(ampsys_gui.DEFAULT_ENGINE_ROOT),"
        "'runner_would_delegate':ampsys_runner.should_delegate_to_core('optimize', ['optimize']),"
        "'runner_would_delegate_self_test':ampsys_runner.should_delegate_to_core('self-test', ['self-test']),"
        "'runner_would_delegate_optimize':ampsys_runner.should_delegate_to_core('optimize', ['optimize']),"
        "'runner_would_delegate_spectre_benchmark':ampsys_runner.should_delegate_to_core('spectre-benchmark', ['spectre-benchmark']),"
        "'spectre_threads_auto':ampsys_runner.auto_spectre_threads({'spectre_threads':'auto'}),"
        "'spectre_accel_default':ampsys_runner.spectre_accel_label({'spectre_accel':'auto'})"
        "}, ensure_ascii=False))"
    )
    payload["quick_check_command"] = cmd + ["-X", "utf8", "-c", quick_code]
    try:
        proc = subprocess.run(
            payload["quick_check_command"],
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
        )
        payload["quick_check_returncode"] = proc.returncode
        payload["quick_check_output"] = proc.stdout
        try:
            payload["quick_check_json"] = json.loads(proc.stdout)
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

