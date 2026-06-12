#!/usr/bin/env python3
"""AmpSys Cadence GUI.

This file is intentionally open and readable.  The private AmpSys engine is
loaded by ampsys_runner.py from AMPSYS_ENGINE_ROOT or an obfuscated release.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import queue
import re
import shutil
import shlex
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk
from typing import Any, Dict, Iterable, List, Optional

from ampsys_netlist import parse_netlist
from ampsys_runner import DEFAULT_WRITEBACK_SETTINGS, expected_library_markers, find_core_executable, has_compiled_engine, has_source_engine, library_ready_marker, resolve_engine_root, resolve_hspice_cmd


def detect_plugin_root() -> Path:
    configured = os.environ.get("AMPSYS_PLUGIN_ROOT", "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.exists():
            return candidate.resolve()
    return Path(__file__).resolve().parents[1]


ROOT = detect_plugin_root()
RUNNER = ROOT / "cli" / "ampsys_runner.py"
WORKSPACE = ROOT / "workspace"
REPO_URL = "https://github.com/KonataLin/AmpSysCadencePlugin"
ISSUES_URL = "https://github.com/KonataLin/AmpSysCadencePlugin/issues"
SPONSOR_URL = "https://www.afdian.com/a/LocyDragon"

BG = "#f6f8fc"
PANEL = "#ffffff"
PANEL_2 = "#edf4ff"
INK = "#111827"
MUTED = "#66758b"
LINE = "#d8e1ee"
CHART_BG = "#fbfdff"
ACCENT = "#2563eb"
ACCENT_2 = "#10b981"
ACCENT_3 = "#f59e0b"
WARN = "#b7791f"
BAD = "#dc2626"
GOOD_BG = "#ecfdf5"
BAD_BG = "#fff1f2"
NEUTRAL_BG = "#ffffff"
FLOW_OK = "OK"
FLOW_PENDING = "NO"
STDOUT_LINES_PER_TICK = 120
LOG_TEXT_MAX_LINES = 2500
TELEMETRY_READ_BYTES = 256 * 1024
TELEMETRY_EVENTS_PER_TICK = 300

FONT_CANDIDATES = (
    "Segoe UI",
    "Inter",
    "Arial",
    "Liberation Sans",
    "DejaVu Sans",
    "Noto Sans",
    "Helvetica",
)



def detect_default_engine_root() -> Path:
    configured = os.environ.get("AMPSYS_ENGINE_ROOT", "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.exists():
            return candidate.resolve()
    if find_core_executable(ROOT):
        return ROOT
    try:
        root = resolve_engine_root(ROOT)
        if has_compiled_engine(root) or has_source_engine(root):
            return root
    except Exception:
        pass
    try:
        return resolve_engine_root(ROOT.parent)
    except Exception:
        return ROOT

DEFAULT_ENGINE_ROOT = detect_default_engine_root()


def env_path(name: str) -> Optional[Path]:
    text = os.environ.get(name, "").strip()
    return Path(text).expanduser() if text else None


def preferred_windows_temp_root() -> Optional[Path]:
    if os.name != "nt":
        return None
    env = env_path("AMPSYS_TEMP_DIR")
    if env:
        return env
    h_root = Path(r"H:\AmpSysTemp")
    if h_root.drive and Path(h_root.drive + "\\").exists():
        return h_root
    return None


def default_runtime_temp_dir(project_dir: Path) -> Path:
    return preferred_windows_temp_root() or (project_dir / "tmp")


def default_lut_cache_dir(project_dir: Path) -> Path:
    env = env_path("AMPSYS_CACHE_DIR")
    if env:
        return env
    win_temp = preferred_windows_temp_root()
    if win_temp:
        return win_temp / "autoflow_cache"
    return ROOT / "libraries"


def command_available(cmd: List[str]) -> bool:
    if not cmd:
        return False
    exe = cmd[0]
    return Path(exe).expanduser().exists() or shutil.which(exe) is not None


def python_command_works(cmd: List[str]) -> bool:
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


def split_command(text: str) -> List[str]:
    parts = shlex.split(text, posix=(os.name != "nt"))
    if os.name == "nt":
        parts = [part.strip().strip('"').strip("'") for part in parts]
    return parts


def py_command() -> List[str]:
    candidates: List[List[str]] = []
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


def gui_relaunch_command(args: List[str]) -> List[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return py_command() + [str(Path(__file__).resolve()), *args]


def runner_command(cmd: str, project_path: Path) -> List[str]:
    core = find_core_executable(ROOT)
    if core:
        return [str(core), cmd, "--project", str(project_path)]
    return py_command() + [str(RUNNER), cmd, "--project", str(project_path)]


def safe_float(value: Any, default: float = 0.0) -> float:
    if value in ("", None):
        return default
    try:
        return float(value)
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def rel_or_abs(path: str) -> str:
    return str(Path(path).expanduser()) if path else ""


def dialog_initial_dir(value: Any, fallback: Path) -> str:
    text = str(value or "").strip()
    candidates: List[Path] = []
    if text and not (os.name != "nt" and is_windows_path(text)):
        try:
            path = Path(text).expanduser()
            candidates.append(path if path.is_dir() else path.parent)
        except Exception:
            pass
    candidates.append(fallback)
    for candidate in candidates:
        try:
            if candidate.exists():
                return str(candidate)
        except Exception:
            continue
    return str(fallback)


def split_csv(text: str) -> List[str]:
    return [x.strip() for x in text.replace(";", ",").split(",") if x.strip()]


def fmt_si(value: Any, scale: float = 1.0, digits: int = 5) -> str:
    try:
        val = float(value) / scale
    except Exception:
        return ""
    if abs(val) >= 1000 or (val and abs(val) < 0.001):
        return f"{val:.{digits}g}"
    return f"{val:.{digits}f}".rstrip("0").rstrip(".")


def sanitize_runner_output(text: str) -> str:
    text = re.sub(r"(?im)\bbest_fitness\b\s*[:=]\s*[-+0-9.eE]+", "convergence: [hidden]", text)
    text = re.sub(r"(?im)\bfitness\b\s*[:=]\s*[-+0-9.eE]+", "convergence: [hidden]", text)
    text = re.sub(r"(?im)\bfitness\b\s+[-+0-9.eE]+", "convergence: [hidden]", text)
    text = re.sub(r"(?i)\bbest_fitness\b", "convergence", text)
    text = re.sub(r"(?i)\bfitness\b", "convergence", text)
    return text


METRIC_AXIS_DEFS = [
    ("gain", "Gain"),
    ("gbw", "GBW"),
    ("pm", "PM"),
    ("cmrr", "CMRR"),
    ("psrr", "PSRR"),
    ("power", "Power"),
    ("noise", "Noise"),
    ("area_um2", "Area"),
    ("convergence", "Conv"),
]
LOWER_BETTER_METRICS = {"power", "noise", "area_um2"}
OBJECTIVE_WEIGHT_DEFS = [
    ("fitness_b", "Gain priority", 1.20, "main", "validated"),
    ("fitness_a", "Bandwidth priority", 0.70, "main", "validated"),
    ("fitness_e", "CMRR priority", 0.20, "main", "differential/eligible topologies"),
    ("fitness_f", "PSRR priority", 0.10, "main", "topology dependent"),
    ("fitness_d", "Noise priority", 0.25, "main", "validated"),
    ("fitness_g", "Area pressure", 0.10, "main", "effective; conservative default"),
    ("fitness_c", "Power priority", 0.00, "hidden", "fixed-current flow"),
]
OBJECTIVE_WEIGHT_DEFAULTS = {key: default for key, _label, default, _group, _note in OBJECTIVE_WEIGHT_DEFS}


def point_metric_axes(points: List[Dict[str, Any]]) -> List[tuple]:
    axes = [
        (key, label)
        for key, label in METRIC_AXIS_DEFS
        if key == "convergence" or any(key in point for point in points)
    ]
    return axes if len(axes) >= 2 else [("convergence", "Convergence")]


def read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def deep_update(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_update(base[key], value)
        else:
            base[key] = value
    return base


def default_project(project_path: Path) -> Dict[str, Any]:
    project_dir = project_path.parent
    return {
        "project_dir": str(project_dir),
        "engine_root": str(DEFAULT_ENGINE_ROOT),
        "netlist_path": "",
        "telemetry_path": str(project_dir / "telemetry.jsonl"),
        "result_path": str(project_dir / "result.json"),
        "skill_result_path": str(project_dir / "ampsys_result.il"),
        "skip_kcl": True,
        "cadence": {"lib": "", "cell": "", "view": "schematic"},
        "library": {
            "model_path": "",
            "nmos_name": "n18",
            "pmos_name": "p18",
            "model_lib": "tt",
            "temperature": 25.0,
            "process_vdd": 1.8,
            "hspice_dir": "",
            "hspice_cmd": "hspice -mt 2",
            "cache_dir": str(default_lut_cache_dir(project_dir)),
            "temp_dir": str(default_runtime_temp_dir(project_dir)),
            "force_rescan": False,
            "L_min": 0.18e-6,
            "L_list": "",
            "scan_width": 10e-6,
            "vgs_start": 0.0,
            "vgs_stop": 1.8,
            "vgs_step": 0.02,
            "vds_start": 0.05,
            "vds_stop": 1.8,
            "vds_step": 0.05,
            "vsb_start": 0.0,
            "vsb_stop": 0.0,
            "vsb_step": 0.1,
            "use_batch_mode": True,
            "batch_size": 20,
            "batch_timeout_ms": 50,
        },
        "specs": {
            "gain_min": 60.0,
            "gbw": 20e6,
            "pm_min": 60.0,
            "power_max": 600e-6,
            "load_cap": 5e-12,
            "saturation_margin": 0.01,
            "V_in_cm": 0.9,
            "V_out_cm": 0.9,
            "area_knee_um2": 500.0,
            "enable_vds_iteration": True,
            "vds_max_iter": 20,
            "vds_tol": 1e-3,
            "vds_damping": 0.5,
            "kcl_safe_tol": 0.05,
            "kcl_dead_tol": 0.15,
            "current_mismatch_tol": 0.05,
            "fitness_a": OBJECTIVE_WEIGHT_DEFAULTS["fitness_a"],
            "fitness_b": OBJECTIVE_WEIGHT_DEFAULTS["fitness_b"],
            "fitness_c": OBJECTIVE_WEIGHT_DEFAULTS["fitness_c"],
            "fitness_d": OBJECTIVE_WEIGHT_DEFAULTS["fitness_d"],
            "fitness_e": OBJECTIVE_WEIGHT_DEFAULTS["fitness_e"],
            "fitness_f": OBJECTIVE_WEIGHT_DEFAULTS["fitness_f"],
            "fitness_g": OBJECTIVE_WEIGHT_DEFAULTS["fitness_g"],
        },
            "config": {
            "population_size": 40,
            "max_generations": 30,
            "verbose": False,
            "parallel": True,
            "print_details": False,
            "enable_kvl_check": True,
            "fast_mode": True,
            "debug_log": False,
            "monitor": False,
            "monitor_interval": 5.0,
            "elite_ratio": 0.1,
            "crossover_prob": 0.85,
            "mutation_prob": 0.5,
            "mutation_sigma_gmid": 1.5,
            "mutation_sigma_L": 0.5e-6,
            "mutation_sigma_I": 10e-6,
            "tournament_size": 3,
            "selection_strategy": "tournament",
            "rank_pressure": 1.8,
            "de_mutation_prob": 0.5,
            "random_guy_ratio": 0.1,
            "cataclysm_patience": 5,
            "cataclysm_threshold": 0.001,
            "convergence_patience": 9999,
            "convergence_threshold": 1e-6,
            "use_adaptive_mutation": True,
            "hspice_max_parallel": 4,
            "n_parallel_workers": 0,
            "random_seed": 42,
            "pm_penalty_k": 0.01,
            "adaptive_population": True,
            "adaptive_target_fill_rate": 0.7,
            "adaptive_pop_max_ratio": 5.0,
        },
        "settings": dict(DEFAULT_WRITEBACK_SETTINGS),
        "devices": [],
        "passives": [],
    }


def is_windows_path(value: Any) -> bool:
    return bool(re.match(r"^[A-Za-z]:[\\/]", str(value or "").strip()))


def cache_pair_in_dir(cache_dir: Path) -> Optional[Path]:
    if not cache_dir.is_dir():
        return None
    for nmos_pkl in sorted(cache_dir.glob("nmos_*.pkl")):
        key = nmos_pkl.name[len("nmos_"):-len(".pkl")]
        if (cache_dir / f"pmos_{key}.pkl").is_file():
            return nmos_pkl
    return None


def infer_cache_dir() -> Optional[Path]:
    candidates = []
    env_cache = env_path("AMPSYS_CACHE_DIR")
    if env_cache:
        candidates.append(env_cache)
    win_temp = preferred_windows_temp_root()
    if win_temp:
        candidates.extend([win_temp / "autoflow_cache", win_temp])
    candidates.extend([
        Path.home() / "Desktop" / "autoflow_cache",
        Path.home() / "ampsys_lut" / "autoflow_cache",
        Path.home() / "ampsys_lut",
    ])
    for candidate in candidates:
        if cache_pair_in_dir(candidate):
            return candidate
        child = candidate / "autoflow_cache"
        if cache_pair_in_dir(child):
            return child
    return None


def infer_cache_triplet(cache_dir: Path) -> Dict[str, str]:
    nmos_pkl = cache_pair_in_dir(cache_dir)
    if not nmos_pkl:
        return {}
    key = nmos_pkl.name[len("nmos_"):-len(".pkl")]
    base = re.sub(r"_t[^_]*_vsb.*$", "", key)
    parts = base.split("_")
    if len(parts) < 3:
        return {}
    return {
        "nmos_name": parts[-3],
        "pmos_name": parts[-2],
        "model_lib": parts[-1],
    }


def sanitize_loaded_project(project: Dict[str, Any], project_path: Path) -> bool:
    changed = False
    project_dir = project_path.parent
    fixed_paths = {
        "project_dir": str(project_dir),
        "engine_root": str(DEFAULT_ENGINE_ROOT),
        "telemetry_path": str(project_dir / "telemetry.jsonl"),
        "result_path": str(project_dir / "result.json"),
        "skill_result_path": str(project_dir / "ampsys_result.il"),
    }
    for key, value in fixed_paths.items():
        if project.get(key) != value:
            project[key] = value
            changed = True

    lib = project.setdefault("library", {})
    settings = project.setdefault("settings", {})
    for key, value in DEFAULT_WRITEBACK_SETTINGS.items():
        if settings.get(key) in (None, ""):
            settings[key] = value
            changed = True

    temp_dir = str(default_runtime_temp_dir(project_dir))
    if lib.get("temp_dir") != temp_dir:
        lib["temp_dir"] = temp_dir
        changed = True

    cache_text = str(lib.get("cache_dir") or "")
    cache_path = Path(cache_text).expanduser() if cache_text else Path()
    if not cache_text or is_windows_path(cache_text) != (os.name == "nt") or not cache_pair_in_dir(cache_path):
        inferred = infer_cache_dir()
        if inferred:
            lib["cache_dir"] = str(inferred)
            cache_path = inferred
            changed = True

    if os.name != "nt":
        for key in ("model_path", "hspice_dir", "hspice_cmd"):
            if is_windows_path(lib.get(key)):
                lib[key] = "" if key != "hspice_cmd" else "hspice -mt 2"
                changed = True
        triplet = infer_cache_triplet(cache_path)
        if triplet:
            for key, value in triplet.items():
                if str(lib.get(key) or "").strip() in ("", "nmos", "pmos") or key == "model_lib":
                    if lib.get(key) != value:
                        lib[key] = value
                        changed = True

    return changed


class VarBag:
    def __init__(self, parent: tk.Misc, data: Dict[str, Any]):
        self.parent = parent
        self.vars: Dict[str, tk.Variable] = {}
        for key, value in data.items():
            self.vars[key] = self.make(value)

    def make(self, value: Any) -> tk.Variable:
        if isinstance(value, bool):
            return tk.BooleanVar(self.parent, value=value)
        return tk.StringVar(self.parent, value="" if value is None else str(value))

    def get(self, key: str, default: Any = "") -> Any:
        var = self.vars.get(key)
        return var.get() if var else default

    def set(self, key: str, value: Any) -> None:
        if key not in self.vars:
            self.vars[key] = self.make(value)
        else:
            self.vars[key].set(value)

    def as_strings(self) -> Dict[str, Any]:
        return {k: v.get() for k, v in self.vars.items()}


class AmpSysGUI:
    def __init__(self, root: tk.Tk, args: argparse.Namespace):
        self.root = root
        self.root.title("AmpSys Cadence Plugin")
        self.root.geometry("1320x860")
        self.root.minsize(1120, 720)

        self.project_path = Path(args.project).resolve() if args.project else WORKSPACE / "ampsys_project.json"
        self.project_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path = self.project_path.parent / "ampsys_gui.log"
        self.setup_logging()
        self.root.report_callback_exception = self.report_callback_exception
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.project = default_project(self.project_path)
        if self.project_path.exists():
            deep_update(self.project, read_json(self.project_path, {}))
        self.project.setdefault("cadence", {})
        self.project.setdefault("library", {})
        self.project.setdefault("specs", {})
        self.project.setdefault("config", {})
        self.project.setdefault("settings", dict(DEFAULT_WRITEBACK_SETTINGS))
        self.project.setdefault("devices", [])
        self.project.setdefault("passives", [])

        if args.netlist:
            self.project["netlist_path"] = str(Path(args.netlist).resolve())
        if args.cadence_lib:
            self.project["cadence"]["lib"] = args.cadence_lib
        if args.cadence_cell:
            self.project["cadence"]["cell"] = args.cadence_cell
        if args.cadence_view:
            self.project["cadence"]["view"] = args.cadence_view

        if sanitize_loaded_project(self.project, self.project_path):
            self.project_path.parent.mkdir(parents=True, exist_ok=True)
            self.project_path.write_text(json.dumps(self.project, indent=2, ensure_ascii=False), encoding="utf-8")
            logging.info("Sanitized stale project paths and LUT defaults: %s", self.project_path)

        self.cadence_netlist_mode = bool(
            self.project.get("netlist_path")
            and self.project["cadence"].get("lib")
            and self.project["cadence"].get("cell")
        )
        self.last_parse_signature = ""
        self.lib_vars = VarBag(root, self.project["library"])
        self.spec_vars = VarBag(root, self.project["specs"])
        self.cfg_vars = VarBag(root, self.project["config"])
        self.settings_vars = VarBag(root, self.project["settings"])
        self.top_vars = VarBag(root, {
            "engine_root": self.project.get("engine_root", str(DEFAULT_ENGINE_ROOT)),
            "netlist_path": self.project.get("netlist_path", ""),
            "skip_kcl": bool(self.project.get("skip_kcl", True)),
            "cadence_lib": self.project["cadence"].get("lib", ""),
            "cadence_cell": self.project["cadence"].get("cell", ""),
            "cadence_view": self.project["cadence"].get("view", "schematic"),
        })

        self.devices: List[Dict[str, Any]] = list(self.project.get("devices", []))
        self.warnings: List[str] = []
        self.proc: Optional[subprocess.Popen] = None
        self.active_cmd = ""
        self.stdout_queue: "queue.Queue[str]" = queue.Queue()
        self.telemetry_seen = 0
        self.telemetry_offset = 0
        self.telemetry_remainder = ""
        self.telemetry_pending: List[str] = []
        self.telemetry_events: List[Dict[str, Any]] = []
        self.result_data: Dict[str, Any] = {}
        self.last_points: List[Dict[str, Any]] = []
        self.flow_status_vars: Dict[str, tk.StringVar] = {}
        self.flow_detail_vars: Dict[str, tk.StringVar] = {}
        self.flow_status_labels: Dict[str, tk.Label] = {}
        self.flow_tracker_labels: Dict[str, tk.Label] = {}
        self.runner_log_path: Optional[Path] = None
        self.build_started_at = 0.0
        self.scroll_canvases: List[tk.Canvas] = []
        self.status_update_after: Optional[str] = None
        self.status_var = tk.StringVar(root, "Ready")
        self.progress_var = tk.DoubleVar(root, 0.0)
        self.bulk_current_var = tk.StringVar(root, "")
        self.field_entries: Dict[str, ttk.Entry] = {}
        self.weight_scale_vars: Dict[str, tk.DoubleVar] = {}
        self.settings_visible = tk.BooleanVar(root, False)

        self.setup_style()
        self.build_ui()
        self.bind_status_traces()
        self.refresh_device_table()
        self.refresh_results()
        self.log_startup_context(args)
        if args.netlist:
            self.parse_netlist_from_gui()
        logging.info("AmpSys GUI started. project=%s root=%s", self.project_path, ROOT)

    def log_startup_context(self, args: argparse.Namespace) -> None:
        try:
            payload = {
                "argv": sys.argv,
                "args": vars(args),
                "root": str(ROOT),
                "project_path": str(self.project_path),
                "log_path": str(self.log_path),
                "default_engine_root": str(DEFAULT_ENGINE_ROOT),
                "python_command": py_command(),
                "python_executable": sys.executable,
                "python_version": sys.version,
                "platform": sys.platform,
                "os_name": os.name,
                "cwd": os.getcwd(),
                "env": {
                    "AMPSYS_PLUGIN_ROOT": os.environ.get("AMPSYS_PLUGIN_ROOT", ""),
                    "AMPSYS_ENGINE_ROOT": os.environ.get("AMPSYS_ENGINE_ROOT", ""),
                    "AMPSYS_CORE_ROOT": os.environ.get("AMPSYS_CORE_ROOT", ""),
                    "AMPSYS_PYCMD": os.environ.get("AMPSYS_PYCMD", ""),
                    "AMPSYS_PYTHON3": os.environ.get("AMPSYS_PYTHON3", ""),
                    "DISPLAY": os.environ.get("DISPLAY", ""),
                    "PATH": os.environ.get("PATH", ""),
                },
                "project": self.project,
            }
            logging.info("Startup context:\n%s", json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        except Exception:
            logging.exception("Could not write startup context")

    def setup_style(self) -> None:
        try:
            self.root.tk.call("tk", "scaling", 1.12)
        except Exception:
            pass
        families = set(tkfont.families(self.root))
        default_family = tkfont.nametofont("TkDefaultFont").actual("family")
        self.ui_font_family = next((name for name in FONT_CANDIDATES if name in families), default_family)
        self.font_normal = (self.ui_font_family, 11)
        self.font_small = (self.ui_font_family, 10)
        self.font_section = (self.ui_font_family, 14, "bold")
        self.font_heading = (self.ui_font_family, 23, "bold")
        self.font_bold = (self.ui_font_family, 11, "bold")

        for name, size, weight in (
            ("TkDefaultFont", 11, "normal"),
            ("TkTextFont", 11, "normal"),
            ("TkMenuFont", 11, "normal"),
            ("TkHeadingFont", 11, "bold"),
            ("TkCaptionFont", 11, "normal"),
            ("TkSmallCaptionFont", 10, "normal"),
            ("TkIconFont", 11, "normal"),
            ("TkTooltipFont", 10, "normal"),
        ):
            try:
                tkfont.nametofont(name).configure(family=self.ui_font_family, size=size, weight=weight)
            except Exception:
                pass
        self.root.option_add("*Font", f"{{{self.ui_font_family}}} 11")
        self.root.configure(bg=BG)
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(".", background=BG, foreground=INK, fieldbackground=PANEL, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE, font=self.font_normal)
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=PANEL, relief="solid", borderwidth=1)
        style.configure("Shell.TFrame", background=PANEL, relief="solid", borderwidth=1)
        style.configure("Step.TFrame", background=PANEL)
        style.configure("StepBody.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=INK, font=self.font_normal)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=self.font_normal)
        style.configure("Card.TLabel", background=PANEL, foreground=INK, font=self.font_normal)
        style.configure("MutedCard.TLabel", background=PANEL, foreground=MUTED, font=self.font_normal)
        style.configure("Section.TLabel", background=PANEL, foreground=INK, font=self.font_section)
        style.configure("Link.TButton", background="#eaf2ff", foreground=ACCENT, borderwidth=1, padding=(12, 7), font=self.font_normal)
        style.map("Link.TButton", background=[("active", "#dbeafe")], foreground=[("active", ACCENT)])
        style.configure("TButton", background=PANEL_2, foreground=INK, borderwidth=1, focusthickness=0, padding=(14, 8), font=self.font_normal)
        style.map("TButton", background=[("active", "#dce8f8")], foreground=[("disabled", "#98a4b5")])
        style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff", font=self.font_normal)
        style.map("Accent.TButton", background=[("active", "#1d4ed8")])
        style.configure("Danger.TButton", background="#fee2e2", foreground=BAD, font=self.font_normal)
        style.map("Danger.TButton", background=[("active", "#fecaca")])
        style.configure("TEntry", fieldbackground="#ffffff", foreground=INK, insertcolor=INK, bordercolor=LINE, padding=7, font=self.font_normal)
        style.configure("Valid.TEntry", fieldbackground=GOOD_BG, foreground=INK, insertcolor=INK, bordercolor=ACCENT_2, padding=7, font=self.font_normal)
        style.configure("Invalid.TEntry", fieldbackground=BAD_BG, foreground=INK, insertcolor=INK, bordercolor=BAD, padding=7, font=self.font_normal)
        style.configure("Neutral.TEntry", fieldbackground=NEUTRAL_BG, foreground=INK, insertcolor=INK, bordercolor=LINE, padding=7, font=self.font_normal)
        style.configure("TCombobox", fieldbackground="#ffffff", foreground=INK, bordercolor=LINE, arrowcolor=INK, font=self.font_normal)
        style.configure("TCheckbutton", background=BG, foreground=INK, font=self.font_normal)
        style.configure("Card.TCheckbutton", background=PANEL, foreground=INK, font=self.font_normal)
        style.configure("Horizontal.TScale", background=PANEL)
        style.configure("Treeview", background="#ffffff", foreground=INK, fieldbackground="#ffffff", bordercolor=LINE, rowheight=32, font=self.font_normal)
        style.configure("Treeview.Heading", background=PANEL_2, foreground=INK, relief="flat", font=self.font_bold)
        style.map("Treeview", background=[("selected", "#dbeafe")], foreground=[("selected", INK)])
        style.configure("Horizontal.TProgressbar", background=ACCENT_2, troughcolor="#e7edf6", bordercolor="#e7edf6", lightcolor=ACCENT_2, darkcolor=ACCENT_2)

    def setup_logging(self) -> None:
        for handler in list(logging.getLogger().handlers):
            logging.getLogger().removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
        logging.basicConfig(
            filename=str(self.log_path),
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            encoding="utf-8",
        )

    def report_callback_exception(self, exc_type, exc_value, exc_tb) -> None:
        logging.exception("Tk callback failed", exc_info=(exc_type, exc_value, exc_tb))
        messagebox.showerror("AmpSys GUI error", f"{exc_value}\n\nLog: {self.log_path}")

    def close(self) -> None:
        try:
            logging.shutdown()
        finally:
            self.root.destroy()

    def bind_status_traces(self) -> None:
        for bag in (self.lib_vars, self.spec_vars, self.cfg_vars, self.settings_vars, self.top_vars):
            for var in bag.vars.values():
                var.trace_add("write", self.schedule_status_update)

    def schedule_status_update(self, *_args) -> None:
        if not self.flow_status_vars:
            return
        if self.status_update_after:
            try:
                self.root.after_cancel(self.status_update_after)
            except Exception:
                pass
        self.status_update_after = self.root.after(150, self.run_scheduled_status_update)

    def run_scheduled_status_update(self) -> None:
        self.status_update_after = None
        self.update_flow_statuses()
        if self.cadence_netlist_mode and self.should_auto_reparse_netlist():
            self.root.after(10, self.parse_netlist_from_gui)

    def build_ui(self) -> None:
        header = tk.Frame(self.root, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
        header.pack(fill="x", padx=22, pady=(18, 12))
        header.grid_columnconfigure(1, weight=1)
        header.grid_columnconfigure(2, weight=0)

        tk.Frame(header, bg=ACCENT, width=5).grid(row=0, column=0, rowspan=2, sticky="nsw")
        title_box = tk.Frame(header, bg=PANEL)
        title_box.grid(row=0, column=1, rowspan=2, sticky="ew", padx=(18, 14), pady=14)
        tk.Label(title_box, text="AmpSys", bg=PANEL, fg=INK, font=self.font_heading).grid(row=0, column=0, sticky="w")
        mode_text = "Windows LUT Builder" if self.can_build_library_here() else "Linux Cache-Only"
        tk.Label(title_box, text=mode_text, bg="#eaf2ff", fg=ACCENT, font=self.font_bold, padx=10, pady=4).grid(row=0, column=1, sticky="w", padx=(14, 0), pady=(4, 0))
        tk.Label(title_box, text="Cadence schematic optimizer", bg=PANEL, fg=MUTED, font=self.font_bold).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))

        actions = tk.Frame(header, bg=PANEL)
        actions.grid(row=0, column=2, sticky="e", padx=(0, 16), pady=(14, 5))
        ttk.Button(actions, text="Open Workspace", command=self.open_workspace).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Open Log", command=self.open_current_log).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Load Project", command=self.load_project_dialog).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Save Project", command=self.save_project).pack(side="left", padx=(8, 0))

        links = tk.Frame(header, bg=PANEL)
        links.grid(row=1, column=2, sticky="e", padx=(0, 16), pady=(0, 14))
        ttk.Button(links, text="GitHub", style="Link.TButton", command=lambda: self.open_url(REPO_URL)).pack(side="left", padx=(0, 6))
        ttk.Button(links, text="Issues", style="Link.TButton", command=lambda: self.open_url(ISSUES_URL)).pack(side="left", padx=6)
        ttk.Button(links, text="Sponsor", style="Link.TButton", command=lambda: self.open_url(SPONSOR_URL)).pack(side="left", padx=6)

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=22, pady=(0, 18))
        self.main_page = self.add_scroll_page(body)
        self.root.bind_all("<MouseWheel>", self.on_mousewheel, add="+")
        self.root.bind_all("<Button-4>", self.on_mousewheel, add="+")
        self.root.bind_all("<Button-5>", self.on_mousewheel, add="+")
        self.root.bind_all("<Control-s>", self.handle_save_shortcut, add="+")
        self.root.bind_all("<Control-S>", self.handle_save_shortcut, add="+")
        self.root.bind_all("<F5>", self.handle_refresh_shortcut, add="+")

        self.build_flow_page()

    def open_url(self, url: str) -> None:
        try:
            webbrowser.open_new_tab(url)
        except Exception as exc:
            logging.exception("Could not open URL: %s", url)
            messagebox.showerror("AmpSys", f"Could not open link:\n{url}\n\n{exc}")

    def add_scroll_page(self, parent: tk.Widget) -> ttk.Frame:
        outer = ttk.Frame(parent)
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(0, weight=1)
        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0, borderwidth=0)
        scroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")

        def configure_inner(_event=None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfigure(window_id, width=canvas.winfo_width())

        inner.bind("<Configure>", configure_inner)
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window_id, width=event.width))
        self.scroll_canvases.append(canvas)
        outer.pack(fill="both", expand=True)
        return inner

    def scroll_canvas_for(self, widget: tk.Widget) -> Optional[tk.Canvas]:
        cur: Optional[tk.Widget] = widget
        while cur is not None:
            if cur in self.scroll_canvases:
                return cur  # type: ignore[return-value]
            cur = getattr(cur, "master", None)
        return None

    def on_mousewheel(self, event) -> None:
        canvas = self.scroll_canvas_for(event.widget)
        if not canvas:
            return
        if getattr(event, "num", None) == 4:
            delta = -3
        elif getattr(event, "num", None) == 5:
            delta = 3
        else:
            delta = -int(event.delta / 120) if event.delta else 0
        if delta:
            canvas.yview_scroll(delta, "units")

    def handle_save_shortcut(self, _event=None) -> str:
        self.save_project()
        return "break"

    def handle_refresh_shortcut(self, _event=None) -> str:
        self.refresh_results()
        self.status_var.set("Results refreshed.")
        return "break"

    def card(self, parent: tk.Widget, title: str) -> ttk.Frame:
        frame = ttk.Frame(parent, style="Card.TFrame", padding=14)
        label = ttk.Label(frame, text=title, style="Card.TLabel", font=self.font_bold)
        label.grid(row=0, column=0, columnspan=8, sticky="w", pady=(0, 10))
        return frame

    def field(self, parent: tk.Widget, label: str, var: tk.Variable, row: int, col: int, width: int = 20, browse: str = "", field_key: str = "") -> ttk.Entry:
        cell = ttk.Frame(parent, style="StepBody.TFrame")
        span = 4 if width >= 40 and col == 0 else 2
        cell.grid(row=row, column=col, columnspan=span, padx=(6, 8), pady=6, sticky="ew")
        cell.grid_columnconfigure(0, weight=1)
        ttk.Label(cell, text=label, style="MutedCard.TLabel").grid(row=0, column=0, columnspan=2, pady=(0, 3), sticky="w")
        ent = ttk.Entry(cell, textvariable=var, width=width)
        ent.grid(row=1, column=0, padx=(0, 6 if browse else 0), sticky="ew")
        if field_key:
            self.field_entries[field_key] = ent
        if browse:
            def choose() -> None:
                initial = dialog_initial_dir(var.get(), ROOT)
                if browse == "dir":
                    val = filedialog.askdirectory(initialdir=initial)
                else:
                    val = filedialog.askopenfilename(initialdir=initial)
                if val:
                    var.set(val)
            ttk.Button(cell, text="...", command=choose, width=3).grid(row=1, column=1, sticky="e")
        return ent

    def combo(self, parent: tk.Widget, label: str, var: tk.Variable, row: int, col: int, values: Iterable[str], width: int = 16) -> ttk.Combobox:
        cell = ttk.Frame(parent, style="StepBody.TFrame")
        cell.grid(row=row, column=col, columnspan=2, padx=(6, 8), pady=6, sticky="ew")
        cell.grid_columnconfigure(0, weight=1)
        ttk.Label(cell, text=label, style="MutedCard.TLabel").grid(row=0, column=0, pady=(0, 3), sticky="w")
        cb = ttk.Combobox(cell, textvariable=var, values=list(values), width=width, state="readonly")
        cb.grid(row=1, column=0, sticky="ew")
        return cb

    def check(self, parent: tk.Widget, text: str, var: tk.Variable, row: int, col: int) -> ttk.Checkbutton:
        cb = ttk.Checkbutton(parent, text=text, variable=var, style="Card.TCheckbutton")
        cb.grid(row=row, column=col, padx=12, pady=7, sticky="w")
        return cb

    def objective_weight_control(self, parent: tk.Widget, key: str, label: str, default: float, row: int, col: int, note: str = "") -> None:
        text_var = self.spec_vars.vars[key]
        initial = max(0.0, min(3.0, safe_float(text_var.get(), default)))
        scale_var = tk.DoubleVar(self.root, initial)
        self.weight_scale_vars[key] = scale_var

        cell = ttk.Frame(parent, style="StepBody.TFrame")
        cell.grid(row=row, column=col, columnspan=2, padx=(6, 8), pady=7, sticky="ew")
        cell.grid_columnconfigure(0, weight=1)
        ttk.Label(cell, text=label, style="MutedCard.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 3))
        hint = f"V2 default {default:.2f}" + (f" · {note}" if note else "")
        ttk.Label(cell, text=hint, style="MutedCard.TLabel").grid(row=0, column=1, sticky="e", pady=(0, 3))

        def set_from_scale(value: str) -> None:
            text_var.set(f"{max(0.0, min(3.0, safe_float(value, default))):.2f}")

        def set_from_entry(_event=None) -> str:
            value = max(0.0, min(3.0, safe_float(text_var.get(), default)))
            text_var.set(f"{value:.2f}")
            scale_var.set(value)
            self.save_project_silent()
            return "break"

        scale = ttk.Scale(cell, from_=0.0, to=3.0, variable=scale_var, command=set_from_scale)
        scale.grid(row=1, column=0, sticky="ew", padx=(0, 8))
        entry = ttk.Entry(cell, textvariable=text_var, width=7)
        entry.grid(row=1, column=1, sticky="e")
        entry.bind("<Return>", set_from_entry)
        entry.bind("<FocusOut>", set_from_entry)
        self.field_entries[f"specs.{key}"] = entry

    def tree_with_scrollbars(self, parent: tk.Widget, row: int, columnspan: int, columns: Iterable[str], height: int, selectmode: str = "browse") -> ttk.Treeview:
        box = ttk.Frame(parent, style="StepBody.TFrame")
        box.grid(row=row, column=0, columnspan=columnspan, sticky="nsew", pady=(8, 6))
        box.grid_columnconfigure(0, weight=1)
        box.grid_rowconfigure(0, weight=1)
        tree = ttk.Treeview(box, columns=tuple(columns), show="headings", selectmode=selectmode, height=height)
        yscroll = ttk.Scrollbar(box, orient="vertical", command=tree.yview)
        xscroll = ttk.Scrollbar(box, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        return tree

    def flow_section(self, parent: tk.Widget, key: str, title: str, row: int) -> ttk.Frame:
        frame = ttk.Frame(parent, style="Shell.TFrame", padding=(18, 16, 18, 16))
        frame.grid(row=row, column=0, sticky="ew", pady=(0, 12))
        frame.grid_columnconfigure(2, weight=1)
        order = {"library": 1, "devices": 2, "specs": 3, "run": 4, "results": 5}.get(key, 0)
        tk.Label(frame, text=f"{order:02d}", bg="#eaf2ff", fg=ACCENT, font=self.font_bold, padx=10, pady=5).grid(row=0, column=0, padx=(0, 12), sticky="n")
        status = tk.Label(frame, textvariable=self.flow_status_vars[key], width=3, bg="#fee2e2", fg=BAD, font=(self.ui_font_family, 13, "bold"), padx=4, pady=3)
        status.grid(row=0, column=1, padx=(0, 14), sticky="n")
        self.flow_status_labels[key] = status
        title_box = ttk.Frame(frame, style="StepBody.TFrame")
        title_box.grid(row=0, column=2, sticky="ew")
        ttk.Label(title_box, text=title, style="Section.TLabel").pack(anchor="w")
        ttk.Label(title_box, textvariable=self.flow_detail_vars[key], style="MutedCard.TLabel").pack(anchor="w", pady=(2, 0))
        content = ttk.Frame(frame, style="StepBody.TFrame")
        content.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(14, 0))
        for col in (0, 2, 4):
            content.grid_columnconfigure(col, weight=1)
        return content

    def set_flow_status(self, key: str, ok: bool, detail: str = "") -> None:
        if key in self.flow_status_vars:
            self.flow_status_vars[key].set(FLOW_OK if ok else FLOW_PENDING)
        if key in self.flow_detail_vars:
            self.flow_detail_vars[key].set(detail)
        labels = [self.flow_status_labels.get(key), self.flow_tracker_labels.get(key)]
        for label in labels:
            if not label:
                continue
            if ok:
                label.configure(bg="#dcfce7", fg="#15803d")
            else:
                label.configure(bg="#fee2e2", fg=BAD)

    def set_entry_state(self, field_key: str, state: str) -> None:
        entry = self.field_entries.get(field_key)
        if not entry:
            return
        style = {
            "ok": "Valid.TEntry",
            "bad": "Invalid.TEntry",
            "neutral": "Neutral.TEntry",
        }.get(state, "Neutral.TEntry")
        try:
            entry.configure(style=style)
        except Exception:
            pass

    def text_is_positive(self, value: Any) -> bool:
        return bool(str(value or "").strip()) and safe_float(value, 0.0) > 0

    def text_is_nonnegative(self, value: Any) -> bool:
        return bool(str(value or "").strip()) and safe_float(value, -1.0) >= 0

    def terminal_order_valid(self, value: Any) -> bool:
        aliases = {
            "D": "D", "DRAIN": "D",
            "G": "G", "GATE": "G",
            "S": "S", "SOURCE": "S",
            "B": "B", "BULK": "B", "BODY": "B", "SUB": "B", "SUBSTRATE": "B",
        }
        tokens = [x.strip().upper() for x in re.split(r"[\s,;/|]+", str(value or "")) if x.strip()]
        roles = [aliases.get(tok, tok) for tok in tokens]
        return len(roles) == 4 and set(roles) == {"D", "G", "S", "B"}

    def update_field_styles(self) -> None:
        lib = self.coerce_library()
        library_ok = self.cache_config_ready(lib) and self.library_ready(lib)
        cache_text = str(self.lib_vars.get("cache_dir", "")).strip()
        self.set_entry_state("library.cache_dir", "ok" if library_ok else ("bad" if cache_text else "neutral"))
        self.set_entry_state("library.nmos_name", "ok" if str(lib.get("nmos_name", "")).strip() else "bad")
        self.set_entry_state("library.pmos_name", "ok" if str(lib.get("pmos_name", "")).strip() else "bad")
        self.set_entry_state("library.model_lib", "ok" if str(lib.get("model_lib", "")).strip() else "bad")
        self.set_entry_state("library.temperature", "ok" if str(self.lib_vars.get("temperature", "")).strip() else "bad")
        self.set_entry_state("library.process_vdd", "ok" if self.text_is_positive(self.lib_vars.get("process_vdd", "")) else "bad")
        if self.can_build_library_here():
            model_text = str(lib.get("model_path", "")).strip()
            model_ok = bool(model_text) and Path(model_text).expanduser().is_file()
            self.set_entry_state("library.model_path", "ok" if model_ok else ("bad" if model_text else "neutral"))
            hspice_text = str(self.lib_vars.get("hspice_dir", "")).strip()
            self.set_entry_state("library.hspice_dir", "ok" if hspice_text else "neutral")

        for key in ("gain_min", "gbw", "pm_min", "load_cap"):
            text = str(self.spec_vars.get(key, "")).strip()
            self.set_entry_state(f"specs.{key}", "ok" if self.text_is_positive(text) else ("neutral" if not text else "bad"))
        for key in ("V_in_cm", "V_out_cm", "saturation_margin"):
            text = str(self.spec_vars.get(key, "")).strip()
            self.set_entry_state(f"specs.{key}", "ok" if self.text_is_nonnegative(text) else ("neutral" if not text else "bad"))
        self.set_entry_state("config.population_size", "ok" if safe_int(self.cfg_vars.get("population_size"), 0) > 0 else "bad")
        self.set_entry_state("config.max_generations", "ok" if safe_int(self.cfg_vars.get("max_generations"), 0) > 0 else "bad")

        for key in ("nmos_terminal_order", "pmos_terminal_order"):
            self.set_entry_state(f"settings.{key}", "ok" if self.terminal_order_valid(self.settings_vars.get(key, "")) else "bad")
        for key in ("width_aliases", "finger_width_aliases", "length_aliases", "finger_aliases", "passive_value_aliases"):
            self.set_entry_state(f"settings.{key}", "ok" if split_csv(str(self.settings_vars.get(key, "")).replace(" ", ",")) else "bad")
        self.set_entry_state("settings.multiplier_aliases", "ok" if str(self.settings_vars.get("multiplier_aliases", "")).strip() else "neutral")
        self.set_entry_state("settings.multiplier_value", "ok" if str(self.settings_vars.get("multiplier_value", "")).strip() else "neutral")
        geometry_decimals = safe_int(self.settings_vars.get("geometry_decimals", ""), -1)
        self.set_entry_state("settings.geometry_decimals", "ok" if 0 <= geometry_decimals <= 9 else "bad")

    def can_build_library_here(self) -> bool:
        return os.name == "nt"

    def cache_config_ready(self, lib: Dict[str, Any]) -> bool:
        return bool(lib.get("nmos_name")) and bool(lib.get("pmos_name"))

    def library_ready(self, lib: Dict[str, Any]) -> bool:
        return library_ready_marker(lib, self.project_path) is not None

    def auto_fill_lut_from_cache(self) -> None:
        cache_text = str(self.lib_vars.get("cache_dir", "")).strip()
        if not cache_text or is_windows_path(cache_text):
            inferred = infer_cache_dir()
            if not inferred:
                return
            self.lib_vars.set("cache_dir", str(inferred))
            cache_path = inferred
        else:
            cache_path = Path(cache_text).expanduser()

        candidates = [cache_path]
        if cache_path.name != "autoflow_cache":
            candidates.append(cache_path / "autoflow_cache")

        for candidate in candidates:
            if not cache_pair_in_dir(candidate):
                continue
            triplet = infer_cache_triplet(candidate)
            if not triplet:
                return
            if candidate != cache_path:
                self.lib_vars.set("cache_dir", str(candidate))
            for key, value in triplet.items():
                current = str(self.lib_vars.get(key, "")).strip()
                if key == "model_lib" or current in ("", "nmos", "pmos"):
                    self.lib_vars.set(key, value)
            return

    def device_setup_issues(self) -> List[str]:
        issues: List[str] = []
        if not self.devices:
            if str(self.top_vars.get("netlist_path", "")).strip():
                detail = "\n".join(self.warnings[:12])
                base = "No devices were parsed from the extracted schematic netlist. Check NMOS/PMOS names and the current schematic."
                return [base + (("\n\nNetlist warnings:\n" + detail) if detail else "")]
            return ["No devices are loaded yet. Launch from Virtuoso with AmpSys -> Extract Current Schematic before running optimization."]

        if self.warnings:
            issues.append("Netlist naming/parse warnings:\n" + "\n".join(self.warnings[:20]))

        unknown = [str(d.get("name", "")) for d in self.devices if d.get("type", d.get("kind", "")) == "unknown_mos"]
        if unknown:
            issues.append("These MOS model names were not recognized as NMOS/PMOS: " + ", ".join(unknown[:30]))

        mos = [d for d in self.devices if d.get("type", d.get("kind", "")) in ("nmos", "pmos")]
        if not mos:
            issues.append("No NMOS/PMOS devices were parsed. Check NMOS name, PMOS name, and netlist model names.")
            return issues

        bad_nodes = [str(d.get("name", "")) for d in mos if len(d.get("nodes", [])) != 4]
        if bad_nodes:
            issues.append("These MOS devices do not have D/G/S/B pins: " + ", ".join(bad_nodes[:30]))

        node_set = {str(node) for d in mos for node in d.get("nodes", [])}
        missing_nets = [name for name in ("VDD", "GND", "Vin", "Vout") if name not in node_set]
        if missing_nets:
            issues.append("Required net names are missing: " + ", ".join(missing_nets) + ". Use exact names VDD, GND, Vin, Vout.")

        missing_current = [str(d.get("name", "")) for d in mos if safe_float(d.get("current"), 0.0) <= 0]
        if missing_current:
            issues.append("Set Id uA for every MOS before Run: " + ", ".join(missing_current[:30]))

        return issues

    def spec_setup_issues(self) -> List[str]:
        issues: List[str] = []
        positive_fields = {
            "gain_min": "Gain min dB",
            "gbw": "GBW MHz",
            "pm_min": "PM min deg",
            "load_cap": "Load cap pF",
        }
        nonnegative_fields = {
            "saturation_margin": "Saturation margin",
            "V_in_cm": "V in cm",
            "V_out_cm": "V out cm",
        }
        for key, label in positive_fields.items():
            text = str(self.spec_vars.get(key, "")).strip()
            if text and safe_float(text, 0.0) <= 0:
                issues.append(f"{label} must be positive when set.")
        for key, label in nonnegative_fields.items():
            text = str(self.spec_vars.get(key, "")).strip()
            if text and safe_float(text, -1.0) < 0:
                issues.append(f"{label} must be non-negative when set.")
        for key, label, _default, _group, _note in OBJECTIVE_WEIGHT_DEFS:
            text = str(self.spec_vars.get(key, "")).strip()
            if text and safe_float(text, -1.0) < 0:
                issues.append(f"{label} must be non-negative.")
        if safe_int(self.cfg_vars.get("population_size"), 0) <= 0:
            issues.append("Population must be a positive integer.")
        if safe_int(self.cfg_vars.get("max_generations"), 0) <= 0:
            issues.append("Generations must be a positive integer.")
        return issues

    def settings_setup_issues(self) -> List[str]:
        issues: List[str] = []
        if not self.terminal_order_valid(self.settings_vars.get("nmos_terminal_order", "")):
            issues.append("NMOS terminal order must contain exactly D, G, S, B once.")
        if not self.terminal_order_valid(self.settings_vars.get("pmos_terminal_order", "")):
            issues.append("PMOS terminal order must contain exactly D, G, S, B once.")
        settings = self.settings_vars.as_strings()
        for key, label in (
            ("width_aliases", "Width aliases"),
            ("finger_width_aliases", "Finger-width aliases"),
            ("length_aliases", "Length aliases"),
            ("finger_aliases", "Finger aliases"),
            ("passive_value_aliases", "Passive value aliases"),
        ):
            if not split_csv(str(settings.get(key, "")).replace(" ", ",")):
                issues.append(f"{label} cannot be empty.")
        if str(settings.get("width_mode", "auto")).lower() not in {"auto", "finger", "total"}:
            issues.append("Width writeback must be auto, finger, or total.")
        geometry_decimals = safe_int(settings.get("geometry_decimals", ""), -1)
        if geometry_decimals < 0 or geometry_decimals > 9:
            issues.append("Geometry decimals must be an integer from 0 to 9.")
        return issues

    def update_flow_statuses(self) -> None:
        if not self.flow_status_vars:
            return
        self.auto_fill_lut_from_cache()
        lib = self.coerce_library()
        cache_config_ok = self.cache_config_ready(lib)
        library_ok = cache_config_ok and self.library_ready(lib)
        if not cache_config_ok:
            library_detail = "NMOS name and PMOS name are required."
        elif library_ok:
            library_detail = "Cache is ready."
        else:
            library_detail = "Cache files were not found for the selected names/corner."
        self.set_flow_status("library", library_ok, library_detail)

        device_issues = self.device_setup_issues()
        mos_count = len([d for d in self.devices if d.get("type", d.get("kind", "")) in ("nmos", "pmos")])
        device_detail = f"{mos_count} MOS ready." if not device_issues else device_issues[0].splitlines()[0]
        self.set_flow_status("devices", not device_issues, device_detail)

        spec_issues = self.spec_setup_issues() + self.settings_setup_issues()
        self.set_flow_status("specs", not spec_issues, "Specs are ready." if not spec_issues else spec_issues[0])
        telemetry = Path(self.collect_project()["telemetry_path"])
        run_ok = bool(self.proc and self.proc.poll() is None) or telemetry.is_file() or Path(self.project_path.parent / "ampsys_optimize.log").is_file()
        run_detail = "Run is active." if self.proc and self.proc.poll() is None else ("Run log is available." if run_ok else "Not started.")
        self.set_flow_status("run", run_ok, run_detail)
        result_ok = Path(self.project_path.parent / "result.json").is_file()
        self.set_flow_status("results", result_ok, "Result file is available." if result_ok else "No result yet.")
        self.update_field_styles()

    def build_flow_page(self) -> None:
        page = self.main_page
        page.grid_columnconfigure(0, weight=1)
        self.flow_status_vars = {key: tk.StringVar(self.root, FLOW_PENDING) for key in ("library", "devices", "specs", "run", "results")}
        self.flow_detail_vars = {key: tk.StringVar(self.root, "") for key in ("library", "devices", "specs", "run", "results")}

        flow = ttk.Frame(page, style="Shell.TFrame", padding=(18, 14, 18, 14))
        flow.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        flow_items = [
            ("library", "LUT Cache"),
            ("devices", "Devices"),
            ("specs", "Specs"),
            ("run", "Run"),
            ("results", "Results"),
        ]
        for col in range(len(flow_items)):
            flow.grid_columnconfigure(col, weight=1, uniform="flow")
        for idx, (key, label) in enumerate(flow_items):
            cell = tk.Frame(flow, bg=PANEL)
            cell.grid(row=0, column=idx, sticky="ew", padx=(0 if idx == 0 else 6, 0 if idx == len(flow_items) - 1 else 6))
            cell.grid_columnconfigure(1, weight=1)
            badge = tk.Label(cell, textvariable=self.flow_status_vars[key], width=3, bg="#fee2e2", fg=BAD, font=(self.ui_font_family, 12, "bold"), padx=3, pady=3)
            badge.grid(row=0, column=0, rowspan=2, padx=(0, 8), sticky="w")
            self.flow_tracker_labels[key] = badge
            tk.Label(cell, text=f"{idx + 1:02d}", bg=PANEL, fg=ACCENT, font=self.font_small).grid(row=0, column=1, sticky="w")
            tk.Label(cell, text=label, bg=PANEL, fg=INK, font=self.font_bold).grid(row=1, column=1, sticky="w")

        row = 1
        lut = self.flow_section(page, "library", "LUT Cache", row)
        self.field(lut, "Cache dir", self.lib_vars.vars["cache_dir"], 0, 0, width=58, browse="dir", field_key="library.cache_dir")
        self.field(lut, "NMOS name", self.lib_vars.vars["nmos_name"], 1, 0, field_key="library.nmos_name")
        self.field(lut, "PMOS name", self.lib_vars.vars["pmos_name"], 1, 2, field_key="library.pmos_name")
        self.field(lut, "Corner/lib", self.lib_vars.vars["model_lib"], 2, 0, field_key="library.model_lib")
        self.field(lut, "Temp C", self.lib_vars.vars["temperature"], 2, 2, field_key="library.temperature")
        self.field(lut, "VDD V", self.lib_vars.vars["process_vdd"], 3, 0, field_key="library.process_vdd")
        if self.can_build_library_here():
            self.field(lut, "Model path", self.lib_vars.vars["model_path"], 4, 0, width=58, browse="file", field_key="library.model_path")
            self.field(lut, "HSPICE dir", self.lib_vars.vars["hspice_dir"], 5, 0, width=42, browse="dir", field_key="library.hspice_dir")
            ttk.Button(lut, text="Build Library", command=lambda: self.start_runner("build-library")).grid(row=6, column=0, columnspan=2, padx=12, pady=8, sticky="ew")
        row += 1

        devices = self.flow_section(page, "devices", "Device Currents", row)
        toolbar = ttk.Frame(devices, style="StepBody.TFrame")
        toolbar.grid(row=0, column=0, columnspan=6, sticky="ew")
        ttk.Button(toolbar, text="Add MOS", command=self.add_device).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="Select All", command=self.select_all_devices).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Remove Selected", style="Danger.TButton", command=self.remove_selected_devices).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Apply Edit", command=self.apply_device_editor).pack(side="left", padx=6)
        bulk = ttk.Frame(toolbar, style="StepBody.TFrame")
        bulk.pack(side="left", padx=(18, 0))
        ttk.Label(bulk, text="Id uA", style="MutedCard.TLabel").pack(side="left", padx=(0, 4))
        self.bulk_current_entry = ttk.Entry(bulk, textvariable=self.bulk_current_var, width=9)
        self.bulk_current_entry.pack(side="left", padx=(0, 6))
        self.bulk_current_entry.bind("<Return>", self.apply_bulk_current_from_entry)
        ttk.Button(bulk, text="Set Selected", command=lambda: self.apply_bulk_current(selected_only=True)).pack(side="left", padx=(0, 6))
        ttk.Button(bulk, text="Set All", command=lambda: self.apply_bulk_current(selected_only=False)).pack(side="left")
        self.warning_label = ttk.Label(toolbar, text="", style="MutedCard.TLabel")
        self.warning_label.pack(side="right")

        cols = ("name", "type", "nodes", "current_uA", "match_group", "bw", "value")
        self.device_tree = self.tree_with_scrollbars(devices, 1, 6, cols, height=8, selectmode="extended")
        for col, text, width in [
            ("name", "Name", 100),
            ("type", "Type", 74),
            ("nodes", "D/G/S/B or pins", 340),
            ("current_uA", "Id uA", 92),
            ("match_group", "Match", 100),
            ("bw", "BW factor", 90),
            ("value", "R/C value", 110),
        ]:
            self.device_tree.heading(col, text=text)
            self.device_tree.column(col, width=width, minwidth=width, stretch=(col == "nodes"))
        self.device_tree.bind("<<TreeviewSelect>>", lambda _e: self.load_device_editor())
        self.dev_edit = {k: tk.StringVar(self.root, "") for k in ("name", "type", "nodes", "current_uA", "match_group", "bw", "value")}
        labels = [("Name", "name"), ("Type", "type"), ("Nodes", "nodes"), ("Id uA", "current_uA"), ("Match", "match_group"), ("BW", "bw"), ("R/C", "value")]
        editor = ttk.Frame(devices, style="StepBody.TFrame")
        editor.grid(row=2, column=0, columnspan=6, sticky="ew", pady=(4, 0))
        for col in range(4):
            editor.grid_columnconfigure(col, weight=1)
        for idx, (lab, key) in enumerate(labels):
            cell = ttk.Frame(editor, style="StepBody.TFrame")
            cell.grid(row=idx // 4, column=idx % 4, padx=(6, 8), pady=5, sticky="ew")
            cell.grid_columnconfigure(0, weight=1)
            ttk.Label(cell, text=lab, style="MutedCard.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 3))
            entry = ttk.Entry(cell, textvariable=self.dev_edit[key], width=30 if key == "nodes" else 16)
            entry.grid(row=1, column=0, sticky="ew")
            entry.bind("<Return>", lambda _event: self.apply_device_editor())

        row += 1
        specs = self.flow_section(page, "specs", "Specs & Optimization", row)
        self.display_field(specs, "Gain min dB", "gain_min", 0, 0)
        self.display_field(specs, "GBW MHz", "gbw", 0, 2, scale=1e6)
        self.display_field(specs, "PM min deg", "pm_min", 1, 0)
        self.display_field(specs, "Load cap pF", "load_cap", 1, 2, scale=1e-12)
        self.display_field(specs, "V in cm opt", "V_in_cm", 2, 0)
        self.display_field(specs, "V out cm opt", "V_out_cm", 2, 2)
        self.display_field(specs, "Sat margin opt", "saturation_margin", 3, 0)
        self.field(specs, "Population", self.cfg_vars.vars["population_size"], 3, 2, field_key="config.population_size")
        self.field(specs, "Generations", self.cfg_vars.vars["max_generations"], 4, 0, field_key="config.max_generations")
        ttk.Label(specs, text="V2 Objective Priorities", style="Card.TLabel", font=self.font_bold).grid(row=5, column=0, columnspan=4, sticky="w", padx=12, pady=(12, 0))
        ttk.Label(specs, text="Unit-normalized V2 priorities. Fixed-current flow keeps Power internal.", style="MutedCard.TLabel").grid(row=5, column=2, columnspan=2, sticky="e", padx=12, pady=(12, 0))
        main_weights = [item for item in OBJECTIVE_WEIGHT_DEFS if item[3] == "main"]
        for idx, (key, label, default, _group, note) in enumerate(main_weights):
            self.objective_weight_control(specs, key, label, default, 6 + idx // 2, (idx % 2) * 2, note)
        action_row = 6 + ((len(main_weights) + 1) // 2)
        ttk.Button(specs, text="Check Setup", command=self.show_setup_check).grid(row=action_row, column=0, columnspan=2, padx=12, pady=10, sticky="ew")
        ttk.Button(specs, text="Run Optimization", style="Accent.TButton", command=lambda: self.start_runner("optimize")).grid(row=action_row + 1, column=0, columnspan=2, padx=12, pady=10, sticky="ew")
        ttk.Button(specs, text="Stop", style="Danger.TButton", command=self.stop_process).grid(row=action_row + 1, column=2, columnspan=2, padx=12, pady=10, sticky="ew")
        ttk.Progressbar(specs, variable=self.progress_var, maximum=100, length=260).grid(row=action_row + 2, column=0, columnspan=4, padx=12, pady=(4, 2), sticky="ew")
        ttk.Label(specs, textvariable=self.status_var, style="MutedCard.TLabel").grid(row=action_row + 3, column=0, columnspan=4, padx=12, sticky="w")

        row += 1
        settings = ttk.Frame(page, style="Shell.TFrame", padding=(18, 14, 18, 14))
        settings.grid(row=row, column=0, sticky="ew", pady=(0, 12))
        settings.grid_columnconfigure(1, weight=1)
        self.settings_toggle_text = tk.StringVar(self.root, "Show Settings")
        ttk.Label(settings, text="Settings", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(settings, text="Defaults cover normal CDF termOrder and common MOS CDF names.", style="MutedCard.TLabel").grid(row=0, column=1, sticky="w", padx=(12, 0))
        ttk.Button(settings, textvariable=self.settings_toggle_text, command=self.toggle_settings).grid(row=0, column=2, sticky="e")
        self.settings_body = ttk.Frame(settings, style="StepBody.TFrame")
        self.settings_body.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(14, 0))
        for col in (0, 2, 4):
            self.settings_body.grid_columnconfigure(col, weight=1)
        self.field(self.settings_body, "NMOS terminal order", self.settings_vars.vars["nmos_terminal_order"], 0, 0, field_key="settings.nmos_terminal_order")
        self.field(self.settings_body, "PMOS terminal order", self.settings_vars.vars["pmos_terminal_order"], 0, 2, field_key="settings.pmos_terminal_order")
        self.combo(self.settings_body, "Width writeback", self.settings_vars.vars["width_mode"], 0, 4, ("auto", "finger", "total"))
        self.field(self.settings_body, "Width aliases", self.settings_vars.vars["width_aliases"], 1, 0, width=34, field_key="settings.width_aliases")
        self.field(self.settings_body, "Finger-width aliases", self.settings_vars.vars["finger_width_aliases"], 1, 2, width=34, field_key="settings.finger_width_aliases")
        self.field(self.settings_body, "Geometry decimals", self.settings_vars.vars["geometry_decimals"], 1, 4, field_key="settings.geometry_decimals")
        self.field(self.settings_body, "Length aliases", self.settings_vars.vars["length_aliases"], 2, 0, width=34, field_key="settings.length_aliases")
        self.field(self.settings_body, "Finger aliases", self.settings_vars.vars["finger_aliases"], 2, 2, width=34, field_key="settings.finger_aliases")
        self.field(self.settings_body, "Multiplier aliases", self.settings_vars.vars["multiplier_aliases"], 2, 4, width=34, field_key="settings.multiplier_aliases")
        self.field(self.settings_body, "Multiplier value", self.settings_vars.vars["multiplier_value"], 3, 0, field_key="settings.multiplier_value")
        self.field(self.settings_body, "Passive value aliases", self.settings_vars.vars["passive_value_aliases"], 3, 2, width=34, field_key="settings.passive_value_aliases")
        advanced_weights = [item for item in OBJECTIVE_WEIGHT_DEFS if item[3] == "advanced"]
        term_row = 4
        if advanced_weights:
            ttk.Label(self.settings_body, text="Advanced V2 Priorities", style="Card.TLabel", font=self.font_bold).grid(row=term_row, column=0, columnspan=6, sticky="w", padx=6, pady=(14, 0))
            for idx, (key, label, default, _group, note) in enumerate(advanced_weights):
                self.objective_weight_control(self.settings_body, key, label, default, term_row + 1 + idx // 3, (idx % 3) * 2, note)
            term_row += 2 + ((len(advanced_weights) + 2) // 3)
        ttk.Label(self.settings_body, text="Terminal Order Preview", style="Card.TLabel", font=self.font_bold).grid(row=term_row, column=0, columnspan=6, sticky="w", padx=6, pady=(12, 0))
        term_cols = ("name", "type", "model", "raw", "order", "dgbs")
        self.term_tree = self.tree_with_scrollbars(self.settings_body, term_row + 1, 6, term_cols, height=5)
        for col, text, width in [
            ("name", "Name", 100),
            ("type", "Type", 76),
            ("model", "Model", 120),
            ("raw", "Raw pins", 250),
            ("order", "Order", 100),
            ("dgbs", "D/G/S/B used by AmpSys", 330),
        ]:
            self.term_tree.heading(col, text=text)
            self.term_tree.column(col, width=width, minwidth=width, stretch=(col in {"raw", "dgbs"}))
        if not self.settings_visible.get():
            self.settings_body.grid_remove()

        row += 1
        viz = self.flow_section(page, "run", "Live Convergence", row)
        viz.grid_columnconfigure(0, weight=1)
        viz.grid_columnconfigure(1, weight=1)
        left = ttk.Frame(viz, style="StepBody.TFrame")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        ttk.Label(left, text="Convergence", style="Card.TLabel", font=self.font_bold).pack(anchor="w")
        self.conv_canvas = tk.Canvas(left, bg=CHART_BG, highlightthickness=0, height=220)
        self.conv_canvas.pack(fill="both", expand=True, pady=(8, 8))
        self.conv_canvas.bind("<Configure>", self.redraw_charts)
        log_header = ttk.Frame(left, style="StepBody.TFrame")
        log_header.pack(fill="x")
        ttk.Label(log_header, text="Runner log", style="Card.TLabel", font=self.font_bold).pack(side="left")
        ttk.Button(log_header, text="Open Log", command=self.open_current_log).pack(side="right", padx=(6, 0))
        ttk.Button(log_header, text="Clear View", command=self.clear_log_view).pack(side="right")
        log_box = ttk.Frame(left, style="StepBody.TFrame")
        log_box.pack(fill="both", expand=False, pady=(8, 0))
        log_box.grid_columnconfigure(0, weight=1)
        self.log_text = tk.Text(log_box, bg="#ffffff", fg=INK, insertbackground=INK, height=10, relief="solid", bd=1, wrap="word")
        log_scroll = ttk.Scrollbar(log_box, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll.grid(row=0, column=1, sticky="ns")
        right = ttk.Frame(viz, style="StepBody.TFrame")
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        ttk.Label(right, text="Population metric web", style="Card.TLabel", font=self.font_bold).pack(anchor="w")
        self.web_canvas = tk.Canvas(right, bg=CHART_BG, highlightthickness=0, height=390)
        self.web_canvas.pack(fill="both", expand=True, pady=(8, 0))
        self.web_canvas.bind("<Configure>", self.redraw_charts)

        row += 1
        results = self.flow_section(page, "results", "Results & Cadence Writeback", row)
        result_top = ttk.Frame(results, style="StepBody.TFrame")
        result_top.grid(row=0, column=0, sticky="ew", columnspan=8)
        ttk.Button(result_top, text="Refresh Result", command=self.refresh_results).pack(side="left", padx=(0, 8))
        ttk.Button(result_top, text="Confirm and Apply in Cadence", style="Accent.TButton", command=self.request_cadence_apply).pack(side="left", padx=8)
        self.metrics_label = ttk.Label(result_top, text="", style="MutedCard.TLabel")
        self.metrics_label.pack(side="right")
        cols = ("name", "type", "W_total_um", "W_finger_um", "L_um", "fingers", "Id_uA", "gm_mS", "Vgs", "Vds", "Vdsat")
        self.result_tree = self.tree_with_scrollbars(results, 1, 8, cols, height=8)
        for col in cols:
            self.result_tree.heading(col, text=col)
            self.result_tree.column(col, width=110, minwidth=90, stretch=True)

        row += 1
        footer = ttk.Frame(page, style="Shell.TFrame", padding=(18, 12, 18, 12))
        footer.grid(row=row, column=0, sticky="ew", pady=(0, 6))
        footer.grid_columnconfigure(0, weight=1)
        ttk.Label(footer, text="Project links", style="Card.TLabel", font=self.font_bold).grid(row=0, column=0, sticky="w")
        ttk.Label(footer, text="Bug reports and release updates are handled on GitHub.", style="MutedCard.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 0))
        footer_buttons = ttk.Frame(footer, style="StepBody.TFrame")
        footer_buttons.grid(row=0, column=1, rowspan=2, sticky="e")
        ttk.Button(footer_buttons, text="GitHub Repo", style="Link.TButton", command=lambda: self.open_url(REPO_URL)).pack(side="left", padx=(0, 6))
        ttk.Button(footer_buttons, text="Report Issue", style="Link.TButton", command=lambda: self.open_url(ISSUES_URL)).pack(side="left", padx=6)
        ttk.Button(footer_buttons, text="Sponsor", style="Link.TButton", command=lambda: self.open_url(SPONSOR_URL)).pack(side="left", padx=6)
        self.draw_empty_charts()
        self.update_flow_statuses()

    def display_field(self, parent: tk.Widget, label: str, key: str, row: int, col: int, scale: float = 1.0) -> None:
        if scale != 1.0:
            var = self.spec_vars.vars[key]
            var.set(fmt_si(var.get(), scale))
            setattr(var, "_ampsys_scale", scale)
        self.field(parent, label, self.spec_vars.vars[key], row, col, field_key=f"specs.{key}")

    def toggle_settings(self) -> None:
        visible = not self.settings_visible.get()
        self.settings_visible.set(visible)
        if hasattr(self, "settings_body"):
            if visible:
                self.settings_body.grid()
                self.settings_toggle_text.set("Hide Settings")
            else:
                self.settings_body.grid_remove()
                self.settings_toggle_text.set("Show Settings")
        self.refresh_terminal_table()

    def refresh_terminal_table(self) -> None:
        if not hasattr(self, "term_tree"):
            return
        self.term_tree.delete(*self.term_tree.get_children())
        for d in self.devices:
            dtype = d.get("type", d.get("kind", ""))
            if dtype not in ("nmos", "pmos", "unknown_mos"):
                continue
            raw = d.get("raw_nodes") or d.get("nodes", [])
            nodes = d.get("nodes", [])
            self.term_tree.insert("", "end", values=(
                d.get("name", ""),
                dtype,
                d.get("model", ""),
                " ".join(raw),
                d.get("terminal_order", ""),
                " ".join(nodes),
            ))

    def add_device(self) -> None:
        self.devices.append({"name": f"M{len(self.devices)+1}", "type": "nmos", "nodes": ["D", "G", "S", "B"], "current": 10e-6, "match_group": "", "bw_factor": 1.0})
        self.refresh_device_table()
        self.save_project_silent()

    def remove_selected_devices(self) -> None:
        selected = set(self.device_tree.selection())
        if not selected:
            return
        self.devices = [d for idx, d in enumerate(self.devices) if str(idx) not in selected]
        self.refresh_device_table()
        self.save_project_silent()

    def select_all_devices(self) -> None:
        if not hasattr(self, "device_tree"):
            return
        self.device_tree.selection_set(self.device_tree.get_children())
        self.status_var.set(f"Selected {len(self.device_tree.get_children())} row(s).")

    def load_device_editor(self) -> None:
        selected = self.device_tree.selection()
        if not selected:
            return
        d = self.devices[int(selected[0])]
        self.dev_edit["name"].set(d.get("name", ""))
        self.dev_edit["type"].set(d.get("type", d.get("kind", "")))
        self.dev_edit["nodes"].set(" ".join(d.get("nodes", [])))
        self.dev_edit["current_uA"].set(fmt_si(d.get("current", ""), 1e-6))
        self.dev_edit["match_group"].set(d.get("match_group", ""))
        self.dev_edit["bw"].set(str(d.get("bw_factor", 1.0)))
        self.dev_edit["value"].set("" if d.get("value") in (None, "") else str(d.get("value")))

    def apply_device_editor(self) -> None:
        selected = self.device_tree.selection()
        targets = [int(x) for x in selected] if selected else []
        if not targets:
            return
        for idx in targets:
            d = self.devices[idx]
            for key in ("name", "type", "match_group"):
                val = self.dev_edit[key].get().strip()
                if val:
                    d[key] = val
            nodes = split_csv(self.dev_edit["nodes"].get().replace(" ", ","))
            if nodes:
                d["nodes"] = nodes
            current = self.dev_edit["current_uA"].get().strip()
            if current:
                d["current"] = safe_float(current) * 1e-6
            bw = self.dev_edit["bw"].get().strip()
            d["bw_factor"] = safe_float(bw, 1.0) if bw else 1.0
            val = self.dev_edit["value"].get().strip()
            if val:
                d["value"] = safe_float(val)
        self.refresh_device_table()
        self.save_project_silent()

    def apply_bulk_current(self, selected_only: bool) -> None:
        current_text = self.bulk_current_var.get().strip()
        current_uA = safe_float(current_text, 0.0)
        if current_uA <= 0:
            messagebox.showwarning("AmpSys", "Enter a positive Id uA value first.")
            return
        if selected_only:
            selected = self.device_tree.selection()
            if not selected:
                messagebox.showwarning("AmpSys", "Select one or more MOS rows first, or use Set All.")
                return
            candidate_indices = [int(x) for x in selected]
        else:
            candidate_indices = list(range(len(self.devices)))
        changed = 0
        for idx in candidate_indices:
            if idx < 0 or idx >= len(self.devices):
                continue
            dtype = self.devices[idx].get("type", self.devices[idx].get("kind", ""))
            if dtype not in ("nmos", "pmos"):
                continue
            self.devices[idx]["current"] = current_uA * 1e-6
            changed += 1
        self.refresh_device_table()
        self.status_var.set(f"Set Id={current_uA:g} uA for {changed} MOS device(s).")
        self.save_project_silent()

    def apply_bulk_current_from_entry(self, _event=None) -> str:
        self.apply_bulk_current(selected_only=bool(self.device_tree.selection()))
        return "break"

    def refresh_device_table(self) -> None:
        if not hasattr(self, "device_tree"):
            return
        self.device_tree.delete(*self.device_tree.get_children())
        for idx, d in enumerate(self.devices):
            dtype = d.get("type", d.get("kind", ""))
            self.device_tree.insert("", "end", iid=str(idx), values=(
                d.get("name", ""),
                dtype,
                " ".join(d.get("nodes", [])),
                fmt_si(d.get("current", ""), 1e-6),
                d.get("match_group", ""),
                d.get("bw_factor", ""),
                "" if d.get("value") in (None, "") else d.get("value"),
            ))
        if hasattr(self, "warning_label"):
            issue_count = len(self.device_setup_issues())
            if issue_count:
                text = f"{len(self.devices)} devices. {issue_count} setup issue(s); Run shows details."
            elif self.warnings:
                text = f"{len(self.devices)} devices. {len(self.warnings)} warning(s)."
            else:
                text = f"{len(self.devices)} devices. Required nets: VDD GND Vin Vout. Vb_* optional bias."
            self.warning_label.config(text=text)
        self.refresh_terminal_table()
        self.update_flow_statuses()

    def current_parse_signature(self) -> str:
        path_text = str(self.top_vars.get("netlist_path", "")).strip()
        try:
            netlist_mtime = str(Path(path_text).stat().st_mtime_ns) if path_text else ""
        except Exception:
            netlist_mtime = ""
        return "|".join([
            path_text,
            netlist_mtime,
            str(self.lib_vars.get("nmos_name", "")),
            str(self.lib_vars.get("pmos_name", "")),
            str(self.settings_vars.get("nmos_terminal_order", "")),
            str(self.settings_vars.get("pmos_terminal_order", "")),
        ])

    def should_auto_reparse_netlist(self) -> bool:
        path_text = str(self.top_vars.get("netlist_path", "")).strip()
        if not path_text or not Path(path_text).exists():
            return False
        return self.current_parse_signature() != self.last_parse_signature

    def parse_netlist_from_gui(self) -> None:
        path_text = self.top_vars.get("netlist_path", "")
        if not path_text:
            messagebox.showwarning("AmpSys", "Please choose a netlist first.")
            return
        path = Path(path_text)
        if not path.exists():
            messagebox.showerror("AmpSys", f"Netlist not found:\n{path}")
            return
        try:
            settings = self.coerce_settings()
            pins, devices, warnings = parse_netlist(
                path,
                split_csv(self.lib_vars.get("nmos_name")),
                split_csv(self.lib_vars.get("pmos_name")),
                {
                    "nmos": settings.get("nmos_terminal_order", "D G S B"),
                    "pmos": settings.get("pmos_terminal_order", "D G S B"),
                },
            )
            old_by_name = {d.get("name"): d for d in self.devices}
            merged = []
            for rec in devices:
                row = rec.to_json()
                old = old_by_name.get(row["name"], {})
                for key in ("current", "match_group", "gmid", "L", "vds_estimate", "bw_factor"):
                    if old.get(key) not in (None, ""):
                        row[key] = old[key]
                row.setdefault("bw_factor", 1.0)
                merged.append(row)
            self.devices = merged
            self.warnings = warnings
            logging.info(
                "Parsed netlist=%s pins=%s warnings=%s devices=%s",
                path,
                pins,
                warnings,
                json.dumps(self.devices, indent=2, ensure_ascii=False, default=str),
            )
            self.refresh_device_table()
            suffix = f" with {len(warnings)} warning(s)" if warnings else ""
            self.status_var.set(f"Parsed {len(devices)} devices from {path.name}{suffix}")
            self.last_parse_signature = self.current_parse_signature()
            self.update_flow_statuses()
            self.save_project_silent()
        except Exception as exc:
            messagebox.showerror("AmpSys netlist parse failed", str(exc))

    def collect_project(self) -> Dict[str, Any]:
        project = default_project(self.project_path)
        project["project_dir"] = str(self.project_path.parent)
        project["engine_root"] = self.top_vars.get("engine_root")
        project["netlist_path"] = self.top_vars.get("netlist_path")
        project["skip_kcl"] = False
        project["telemetry_path"] = str(self.project_path.parent / "telemetry.jsonl")
        project["result_path"] = str(self.project_path.parent / "result.json")
        project["skill_result_path"] = str(self.project_path.parent / "ampsys_result.il")
        project["cadence"] = {
            "lib": self.top_vars.get("cadence_lib"),
            "cell": self.top_vars.get("cadence_cell"),
            "view": self.top_vars.get("cadence_view"),
        }
        project["library"] = self.coerce_library()
        project["library"]["force_rescan"] = False
        project["library"]["temp_dir"] = str(default_runtime_temp_dir(self.project_path.parent))
        project["specs"] = self.coerce_specs()
        project["specs"]["enable_vds_iteration"] = True
        project["config"] = self.coerce_config()
        project["config"]["fast_mode"] = True
        project["config"]["verbose"] = False
        project["settings"] = self.coerce_settings()
        project["devices"] = self.devices
        project["passives"] = []
        return project

    def coerce_library(self) -> Dict[str, Any]:
        raw = self.lib_vars.as_strings()
        bools = {"force_rescan", "use_batch_mode"}
        floats = {"temperature", "process_vdd", "L_min", "scan_width", "vgs_start", "vgs_stop", "vgs_step", "vds_start", "vds_stop", "vds_step", "vsb_start", "vsb_stop", "vsb_step"}
        ints = {"batch_size", "batch_timeout_ms"}
        defaults = default_project(self.project_path)["library"]
        string_defaults = {"model_lib", "hspice_cmd", "cache_dir", "temp_dir"}
        out: Dict[str, Any] = {}
        for k, v in raw.items():
            text = str(v).strip() if v is not None else ""
            if k in bools:
                out[k] = bool(v)
            elif k in floats:
                out[k] = safe_float(text, safe_float(defaults.get(k), 0.0)) if text else defaults.get(k, 0.0)
            elif k in ints:
                out[k] = safe_int(text, safe_int(defaults.get(k), 0)) if text else defaults.get(k, 0)
            elif k in string_defaults and text == "":
                out[k] = defaults[k]
            else:
                out[k] = rel_or_abs(v)
        return out

    def coerce_specs(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for key, var in self.spec_vars.vars.items():
            val = var.get()
            if key == "enable_vds_iteration":
                out[key] = bool(val)
            elif val == "":
                continue
            else:
                scale = getattr(var, "_ampsys_scale", 1.0)
                out[key] = safe_float(val) * scale
        for key, default in OBJECTIVE_WEIGHT_DEFAULTS.items():
            out[key] = max(0.0, safe_float(out.get(key), default))
        return out

    def coerce_config(self) -> Dict[str, Any]:
        bools = {"verbose", "parallel", "print_details", "enable_kvl_check", "fast_mode", "debug_log", "monitor", "use_adaptive_mutation", "adaptive_population"}
        ints = {"population_size", "max_generations", "tournament_size", "cataclysm_patience", "convergence_patience", "hspice_max_parallel", "n_parallel_workers"}
        out: Dict[str, Any] = {}
        for k, var in self.cfg_vars.vars.items():
            val = var.get()
            if k in bools:
                out[k] = bool(val)
            elif k in ints:
                out[k] = safe_int(val)
            elif k == "selection_strategy":
                out[k] = str(val)
            elif k == "random_seed":
                out[k] = safe_int(val) if str(val).strip() else None
            else:
                out[k] = safe_float(val)
        return out

    def coerce_settings(self) -> Dict[str, Any]:
        out = dict(DEFAULT_WRITEBACK_SETTINGS)
        for key, value in self.settings_vars.as_strings().items():
            text = str(value).strip() if value is not None else ""
            if text:
                out[key] = text
        out["width_mode"] = str(out.get("width_mode", "auto")).strip().lower()
        if out["width_mode"] not in {"auto", "finger", "total"}:
            out["width_mode"] = "auto"
        out["geometry_decimals"] = str(max(0, min(9, safe_int(out.get("geometry_decimals", 2), 2))))
        return out

    def save_project(self) -> None:
        self.project = self.collect_project()
        self.project_path.parent.mkdir(parents=True, exist_ok=True)
        self.project_path.write_text(json.dumps(self.project, indent=2, ensure_ascii=False), encoding="utf-8")
        logging.info("Project saved: %s", self.project_path)
        self.status_var.set(f"Saved {self.project_path}")
        self.update_flow_statuses()

    def save_project_silent(self) -> None:
        try:
            self.project = self.collect_project()
            self.project_path.parent.mkdir(parents=True, exist_ok=True)
            self.project_path.write_text(json.dumps(self.project, indent=2, ensure_ascii=False), encoding="utf-8")
            logging.info("Project auto-saved: %s", self.project_path)
        except Exception:
            logging.exception("Project auto-save failed: %s", self.project_path)

    def load_project_dialog(self) -> None:
        path = filedialog.askopenfilename(initialdir=str(self.project_path.parent), filetypes=[("AmpSys project", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            subprocess.Popen(gui_relaunch_command(["--project", path]), close_fds=True)
            self.status_var.set(f"Opening project: {path}")
        except Exception as exc:
            logging.exception("Could not open project in a new GUI process: %s", path)
            messagebox.showerror("AmpSys", f"Could not open project:\n{path}\n\n{exc}")

    def open_desktop_path(self, target: Path, label: str) -> None:
        path = str(target)
        try:
            if os.name == "nt":
                os.startfile(path)
                return
            candidates = []
            if sys.platform == "darwin":
                candidates.append(["open", path])
            candidates.extend([
                ["xdg-open", path],
                ["gio", "open", path],
            ])
            for cmd in candidates:
                if shutil.which(cmd[0]):
                    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
            raise FileNotFoundError("No desktop opener found (xdg-open/gio/open).")
        except Exception as exc:
            logging.exception("Could not open %s: %s", label, path)
            messagebox.showinfo("AmpSys", f"{label}:\n{path}\n\nCould not open automatically:\n{exc}")

    def open_workspace(self) -> None:
        self.project_path.parent.mkdir(parents=True, exist_ok=True)
        self.open_desktop_path(self.project_path.parent, "Workspace")

    def open_current_log(self) -> None:
        candidates = [
            self.runner_log_path,
            self.project_path.parent / "ampsys_optimize.log",
            self.project_path.parent / "ampsys_build-library.log",
            self.log_path,
        ]
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                self.open_desktop_path(Path(candidate), "Log")
                return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.touch(exist_ok=True)
        self.open_desktop_path(self.log_path, "Log")

    def clear_log_view(self) -> None:
        if hasattr(self, "log_text"):
            self.log_text.delete("1.0", "end")
            self.status_var.set("Log view cleared.")

    def setup_issues_for_optimize(self) -> List[str]:
        project = self.collect_project()
        lib = project["library"]
        issues: List[str] = []
        missing_lut_fields = [
            label for key, label in (
                ("nmos_name", "NMOS name"),
                ("pmos_name", "PMOS name"),
            )
            if not str(lib.get(key, "")).strip()
        ]
        if missing_lut_fields:
            issues.append("Complete these LUT fields first: " + ", ".join(missing_lut_fields))
        elif not self.library_ready(lib):
            markers = "\n".join(str(p) for p in expected_library_markers(lib, self.project_path)[:6])
            issues.append("LUT cache is not ready. Expected one of:\n" + markers)
        issues.extend(self.device_setup_issues())
        issues.extend(self.spec_setup_issues())
        issues.extend(self.settings_setup_issues())
        return issues

    def show_setup_check(self) -> None:
        issues = self.setup_issues_for_optimize()
        self.update_flow_statuses()
        if issues:
            self.status_var.set(f"Setup check found {len(issues)} issue(s).")
            messagebox.showwarning("AmpSys setup check", "\n\n".join(issues[:12]))
            return
        self.status_var.set("Setup is ready.")
        messagebox.showinfo("AmpSys setup check", "Setup is ready to run.")

    def validate_before_run(self, cmd: str) -> bool:
        project = self.collect_project()
        lib = project["library"]
        if cmd == "build-library":
            if not self.can_build_library_here():
                messagebox.showwarning("AmpSys", "Build Library is only enabled on Windows in this release. On Linux, copy an existing LUT cache and select Cache dir.")
                self.update_flow_statuses()
                return False
            if not lib.get("model_path"):
                messagebox.showwarning("AmpSys", "Model path is required before building the LUT cache.")
                self.update_flow_statuses()
                return False
            model_path = Path(str(lib.get("model_path", ""))).expanduser()
            if not model_path.is_file():
                messagebox.showwarning("AmpSys", f"Model path was not found:\n{model_path}")
                self.update_flow_statuses()
                return False
            try:
                resolve_hspice_cmd(lib)
            except Exception as exc:
                messagebox.showwarning("AmpSys", str(exc))
                self.update_flow_statuses()
                return False
        missing_lut_fields = [
            label for key, label in (
                ("nmos_name", "NMOS name"),
                ("pmos_name", "PMOS name"),
            )
            if not str(lib.get(key, "")).strip()
        ]
        if missing_lut_fields:
            messagebox.showwarning("AmpSys", "Complete these LUT fields first:\n" + ", ".join(missing_lut_fields))
            self.update_flow_statuses()
            return False
        if cmd == "optimize":
            if not self.library_ready(lib):
                markers = "\n".join(str(p) for p in expected_library_markers(lib, self.project_path)[:6])
                if self.can_build_library_here():
                    should_build = messagebox.askyesno(
                        "AmpSys",
                        "LUT cache is not ready.\n\n"
                        f"Expected one of:\n{markers}\n\n"
                        "Click Yes to build the LUT cache now. Click No to choose an existing Cache dir first.",
                    )
                else:
                    messagebox.showwarning(
                        "AmpSys",
                        "LUT cache is not ready.\n\n"
                        "On Linux/Virtuoso, select the cache directory copied from the Windows HSPICE build.\n\n"
                        f"Expected one of:\n{markers}",
                    )
                    should_build = False
                self.update_flow_statuses()
                if should_build:
                    self.root.after(10, lambda: self.start_runner("build-library"))
                return False

            issues = self.device_setup_issues()
            if issues:
                messagebox.showwarning("AmpSys", "Device setup is incomplete:\n\n" + "\n\n".join(issues))
                self.update_flow_statuses()
                return False
            spec_issues = self.spec_setup_issues()
            if spec_issues:
                messagebox.showwarning("AmpSys", "Specs are incomplete:\n\n" + "\n\n".join(spec_issues))
                self.update_flow_statuses()
                return False
            settings_issues = self.settings_setup_issues()
            if settings_issues:
                messagebox.showwarning("AmpSys", "Settings are incomplete:\n\n" + "\n\n".join(settings_issues))
                self.update_flow_statuses()
                return False
        return True

    def start_runner(self, cmd: str) -> None:
        if self.proc and self.proc.poll() is None:
            messagebox.showwarning("AmpSys", "A run is already active.")
            return
        if not self.validate_before_run(cmd):
            return
        self.save_project()
        self.runner_log_path = self.project_path.parent / f"ampsys_{cmd}.log"
        self.runner_log_path.write_text(f"AmpSys runner log: {cmd}\nProject: {self.project_path}\nStarted: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n", encoding="utf-8")
        self.progress_var.set(0)
        self.active_cmd = cmd
        self.build_started_at = time.time()
        self.telemetry_seen = 0
        self.telemetry_offset = 0
        self.telemetry_remainder = ""
        self.telemetry_pending.clear()
        self.telemetry_events.clear()
        self.last_points.clear()
        self.log_text.delete("1.0", "end")
        self.status_var.set(f"Starting {cmd}...")
        telemetry = Path(self.collect_project()["telemetry_path"])
        if telemetry.exists():
            telemetry.write_text("", encoding="utf-8")
        env = os.environ.copy()
        env["AMPSYS_ENGINE_ROOT"] = self.top_vars.get("engine_root")
        env["AMPSYS_PLUGIN_ROOT"] = str(ROOT)
        command = runner_command(cmd, self.project_path)
        logging.info("Starting runner: %s", command)
        logging.info("Runner project snapshot:\n%s", json.dumps(self.collect_project(), indent=2, ensure_ascii=False, default=str))
        logging.info("Runner log path: %s", self.runner_log_path)
        try:
            self.proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT), env=env)
        except Exception as exc:
            logging.exception("Runner launch failed")
            if self.runner_log_path:
                with self.runner_log_path.open("a", encoding="utf-8") as f:
                    f.write(f"\nRunner launch failed: {exc}\n")
            self.status_var.set("Runner launch failed")
            self.active_cmd = ""
            self.progress_var.set(0)
            messagebox.showerror("AmpSys runner failed to start", f"{exc}\n\nLog: {self.runner_log_path}")
            self.update_flow_statuses()
            return
        self.update_flow_statuses()
        threading.Thread(target=self.consume_stdout, daemon=True).start()
        self.root.after(200, self.poll_process)

    def consume_stdout(self) -> None:
        if not self.proc or not self.proc.stdout:
            return
        for line in self.proc.stdout:
            self.stdout_queue.put(line)

    def poll_process(self) -> None:
        batch: List[str] = []
        while len(batch) < STDOUT_LINES_PER_TICK:
            try:
                batch.append(self.stdout_queue.get_nowait())
            except queue.Empty:
                break
        if batch:
            text = sanitize_runner_output("".join(batch))
            self.log_text.insert("end", text)
            self.log_text.see("end")
            if self.runner_log_path:
                with self.runner_log_path.open("a", encoding="utf-8") as f:
                    f.write(text)
            try:
                end_line = int(float(self.log_text.index("end-1c").split(".")[0]))
                if end_line > LOG_TEXT_MAX_LINES:
                    self.log_text.delete("1.0", f"{end_line - LOG_TEXT_MAX_LINES}.0")
            except Exception:
                pass
        if batch and not self.stdout_queue.empty():
            self.root.after(10, self.poll_process)
            return
        self.read_telemetry()
        self.animate_build_progress()
        if self.proc and self.proc.poll() is None:
            self.root.after(350, self.poll_process)
        else:
            code = self.proc.poll() if self.proc else 0
            if code == 0:
                self.progress_var.set(100)
                self.status_var.set("Done")
                self.refresh_results()
                self.update_flow_statuses()
                logging.info("Runner finished successfully")
            else:
                self.status_var.set(f"Runner exited with code {code}")
                logging.error("Runner failed with code %s. log=%s", code, self.runner_log_path)
                messagebox.showerror("AmpSys runner failed", f"Runner exited with code {code}. Check the Run log.")
            self.proc = None
            self.active_cmd = ""
            self.update_flow_statuses()

    def animate_build_progress(self) -> None:
        if self.active_cmd != "build-library" or not self.proc or self.proc.poll() is not None:
            return
        elapsed = max(0.0, time.time() - self.build_started_at)
        current = self.progress_var.get()
        target = min(95.0, 8.0 + 87.0 * (1.0 - math.exp(-elapsed / 45.0)))
        if target > current:
            self.progress_var.set(target)
            self.status_var.set(f"Building LUT library... {target:.0f}%")

    def stop_process(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            logging.warning("Runner terminated by user")
            self.status_var.set("Stopping...")

    def read_telemetry(self) -> None:
        telemetry = Path(self.project.get("telemetry_path") or self.project_path.parent / "telemetry.jsonl")
        if self.telemetry_pending:
            lines = self.telemetry_pending
            self.telemetry_pending = []
        else:
            if not telemetry.exists():
                return
            try:
                if telemetry.stat().st_size < self.telemetry_offset:
                    self.telemetry_offset = 0
                    self.telemetry_remainder = ""
                    self.telemetry_pending = []
                with telemetry.open("r", encoding="utf-8", errors="ignore") as f:
                    f.seek(self.telemetry_offset)
                    chunk = f.read(TELEMETRY_READ_BYTES)
                    self.telemetry_offset = f.tell()
            except OSError:
                return
            if not chunk:
                return
            text = self.telemetry_remainder + chunk
            if text.endswith("\n"):
                lines = text.splitlines()
                self.telemetry_remainder = ""
            else:
                lines = text.splitlines()
                self.telemetry_remainder = lines.pop() if lines else text
        batch = lines[:TELEMETRY_EVENTS_PER_TICK]
        if len(lines) > TELEMETRY_EVENTS_PER_TICK:
            self.telemetry_pending = lines[TELEMETRY_EVENTS_PER_TICK:]
        for line in batch:
            try:
                event = json.loads(line)
            except Exception:
                continue
            self.telemetry_events.append(event)
            self.handle_event(event)
        self.telemetry_seen += len(batch)
        if self.telemetry_pending:
            self.root.after(10, self.read_telemetry)

    def handle_event(self, event: Dict[str, Any]) -> None:
        status = event.get("status", "")
        phase = event.get("phase", "")
        if status == "generation":
            gen = event.get("generation", 0)
            max_gen = event.get("max_generations", 1)
            self.progress_var.set(100 * safe_float(event.get("progress"), gen / max(1, max_gen)))
            self.status_var.set(f"Gen {gen}/{max_gen}  convergence updated")
            self.last_points = event.get("points", [])
            self.draw_charts()
        elif status == "start":
            if phase == "build_library" and event.get("total_points"):
                self.status_var.set(f"Building LUT: {event.get('total_points'):,} HSPICE points")
                self.progress_var.set(2)
            elif phase == "optimize":
                self.status_var.set("Optimization started")
                self.progress_var.set(1)
            else:
                self.status_var.set(f"{phase} started")
        elif status == "done":
            self.progress_var.set(100)
            self.status_var.set(f"{phase} done")
        elif status == "error":
            self.status_var.set(event.get("message", "Error"))

    def redraw_charts(self, _event=None) -> None:
        if not hasattr(self, "conv_canvas") or not hasattr(self, "web_canvas"):
            return
        if self.telemetry_events or self.result_data:
            self.draw_charts()
        else:
            self.draw_empty_charts()

    def draw_empty_charts(self) -> None:
        self.conv_canvas.delete("all")
        self.web_canvas.delete("all")
        self.conv_canvas.create_text(30, 30, anchor="nw", fill=MUTED, text="Run optimization to draw convergence history.", font=self.font_bold)
        self.web_canvas.create_text(30, 30, anchor="nw", fill=MUTED, text="Population metrics will appear here per generation.", font=self.font_bold)

    def draw_charts(self) -> None:
        gen_events = [e for e in self.telemetry_events if e.get("status") == "generation"]
        convergence = [safe_float(e.get("best", {}).get("convergence"), 0.0) for e in gen_events]
        if not convergence and self.result_data:
            convergence = [safe_float(self.result_data.get("convergence"), 1.0)]
        self.draw_line(self.conv_canvas, convergence)
        self.draw_metric_web(self.web_canvas, self.last_points)

    def draw_line(self, canvas: tk.Canvas, values: List[float]) -> None:
        canvas.delete("all")
        w = max(10, canvas.winfo_width())
        h = max(10, canvas.winfo_height())
        pad = 36
        canvas.create_rectangle(0, 0, w, h, fill=CHART_BG, outline="")
        canvas.create_line(pad, h - pad, w - pad, h - pad, fill=LINE)
        canvas.create_line(pad, pad, pad, h - pad, fill=LINE)
        if not values:
            canvas.create_text(pad, pad, anchor="nw", fill=MUTED, text="Waiting for telemetry...")
            return
        vmin, vmax = min(values), max(values)
        if math.isclose(vmin, vmax):
            vmin -= 1.0
            vmax += 1.0
        points = []
        for i, val in enumerate(values):
            x = pad + (w - 2 * pad) * i / max(1, len(values) - 1)
            y = h - pad - (h - 2 * pad) * (val - vmin) / (vmax - vmin)
            points.extend([x, y])
        if len(points) >= 4:
            canvas.create_line(*points, fill=ACCENT_2, width=3, smooth=True)
        for x, y in zip(points[0::2], points[1::2]):
            canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill=ACCENT_2, outline="")
        canvas.create_text(pad, 14, anchor="nw", fill=INK, text="Convergence trend", font=self.font_bold)

    def draw_metric_web(self, canvas: tk.Canvas, points: List[Dict[str, Any]]) -> None:
        canvas.delete("all")
        w = max(10, canvas.winfo_width())
        h = max(10, canvas.winfo_height())
        pad = 28
        axes = point_metric_axes(points)
        canvas.create_rectangle(0, 0, w, h, fill=CHART_BG, outline="")
        if not points:
            if self.draw_result_metric_summary(canvas, w, h, pad):
                return
            canvas.create_text(pad, pad, anchor="nw", fill=MUTED, text="Waiting for population points...")
            return
        mins: Dict[str, float] = {}
        maxs: Dict[str, float] = {}
        for key, _ in axes:
            vals = [safe_float(p.get(key), 0.0) for p in points if key in p or key == "convergence"] or [0.0]
            mins[key], maxs[key] = min(vals), max(vals)
            if math.isclose(mins[key], maxs[key]):
                mins[key] -= 1.0
                maxs[key] += 1.0

        cx = w / 2
        cy = h / 2 + 10
        radius = max(34.0, min((w - 140) / 2, (h - 92) / 2))
        label_radius = radius + 18
        angles = [(-math.pi / 2) + (2 * math.pi * i / max(1, len(axes))) for i in range(len(axes))]

        def clamp(value: float) -> float:
            return max(0.0, min(1.0, value))

        def metric_norm(point: Dict[str, Any], key: str) -> float:
            span = maxs[key] - mins[key]
            if math.isclose(span, 0.0):
                norm = 0.5
            else:
                norm = (safe_float(point.get(key), 0.0) - mins[key]) / span
            if key in LOWER_BETTER_METRICS:
                norm = 1.0 - norm
            return clamp(norm)

        def xy(norm: float, angle: float) -> tuple:
            r = radius * clamp(norm)
            return cx + math.cos(angle) * r, cy + math.sin(angle) * r

        for ring in range(1, 6):
            rr = radius * ring / 5
            coords: List[float] = []
            for angle in angles:
                coords.extend([cx + math.cos(angle) * rr, cy + math.sin(angle) * rr])
            if len(coords) >= 6:
                canvas.create_line(*(coords + coords[:2]), fill=LINE, width=1)
        for angle, (_key, label) in zip(angles, axes):
            sx, sy = xy(1.0, angle)
            canvas.create_line(cx, cy, sx, sy, fill=LINE)
            lx = cx + math.cos(angle) * label_radius
            ly = cy + math.sin(angle) * label_radius
            if lx < cx - 8:
                anchor = "e"
            elif lx > cx + 8:
                anchor = "w"
            elif ly < cy:
                anchor = "s"
            else:
                anchor = "n"
            canvas.create_text(lx, ly, text=label, fill=MUTED, font=self.font_small, anchor=anchor)

        sorted_points = sorted(points, key=lambda p: safe_float(p.get("convergence"), 0.0))
        if len(sorted_points) > 80:
            sample = [sorted_points[int(i * (len(sorted_points) - 1) / 79)] for i in range(80)]
        else:
            sample = sorted_points
        for p in sample:
            convergence = safe_float(p.get("convergence"), 0.0)
            conv_norm = (convergence - mins["convergence"]) / (maxs["convergence"] - mins["convergence"])
            color = self.mix_color("#c7d2fe", ACCENT_2, conv_norm)
            coords = []
            for angle, (key, _label) in zip(angles, axes):
                x, y = xy(metric_norm(p, key), angle)
                coords.extend([x, y])
            if len(coords) >= 6:
                canvas.create_line(*(coords + coords[:2]), fill=color, width=1)
                for x, y in zip(coords[0::2], coords[1::2]):
                    canvas.create_oval(x - 1.4, y - 1.4, x + 1.4, y + 1.4, fill=color, outline="")
        best = sorted_points[-1]
        best_coords = []
        for angle, (key, _label) in zip(angles, axes):
            x, y = xy(metric_norm(best, key), angle)
            best_coords.extend([x, y])
        if len(best_coords) >= 6:
            canvas.create_line(*(best_coords + best_coords[:2]), fill=ACCENT_2, width=3)
            for x, y in zip(best_coords[0::2], best_coords[1::2]):
                canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill=ACCENT_2, outline="")
        canvas.create_text(pad, 18, anchor="nw", fill=INK, text=f"Population radar: {len(points)} individuals", font=self.font_bold)
        canvas.create_text(w - pad, 18, anchor="ne", fill=MUTED, text="bright = current best", font=self.font_small)

    def draw_result_metric_summary(self, canvas: tk.Canvas, w: int, h: int, pad: int) -> bool:
        metrics = self.result_data.get("metrics", {}) if isinstance(self.result_data, dict) else {}
        if not metrics:
            return False
        values: List[tuple] = []
        if "dc_gain" in metrics:
            values.append(("Gain", min(1.0, max(0.0, safe_float(metrics.get("dc_gain"), 0.0) / 100.0)), f"{safe_float(metrics.get('dc_gain'), 0.0):.1f} dB"))
        if "gbw" in metrics:
            values.append(("GBW", min(1.0, max(0.0, math.log10(max(1.0, safe_float(metrics.get("gbw"), 0.0))) / 10.0)), f"{safe_float(metrics.get('gbw'), 0.0)/1e6:.2f} MHz"))
        if "pm" in metrics:
            values.append(("PM", min(1.0, max(0.0, safe_float(metrics.get("pm"), 0.0) / 90.0)), f"{safe_float(metrics.get('pm'), 0.0):.1f} deg"))
        if safe_float(metrics.get("cmrr"), 0.0) > 0:
            values.append(("CMRR", min(1.0, max(0.0, safe_float(metrics.get("cmrr"), 0.0) / 100.0)), f"{safe_float(metrics.get('cmrr'), 0.0):.1f} dB"))
        if safe_float(metrics.get("psrr"), 0.0) > 0:
            values.append(("PSRR", min(1.0, max(0.0, safe_float(metrics.get("psrr"), 0.0) / 100.0)), f"{safe_float(metrics.get('psrr'), 0.0):.1f} dB"))
        if "power" in metrics:
            values.append(("Power", 1.0 - min(1.0, max(0.0, safe_float(metrics.get("power"), 0.0) / 1e-3)), f"{safe_float(metrics.get('power'), 0.0)*1e6:.1f} uW"))
        if safe_float(metrics.get("noise"), 0.0) > 0:
            values.append(("Noise", 1.0 - min(1.0, max(0.0, safe_float(metrics.get("noise"), 0.0) / 1e-6)), f"{safe_float(metrics.get('noise'), 0.0):.2g}"))
        if safe_float(metrics.get("area_um2"), 0.0) > 0:
            values.append(("Area", 1.0 - min(1.0, max(0.0, safe_float(metrics.get("area_um2"), 0.0) / 1000.0)), f"{safe_float(metrics.get('area_um2'), 0.0):.1f} um2"))
        values.append(("Conv", safe_float(self.result_data.get("convergence"), 1.0), "complete"))
        cx = w / 2
        cy = h / 2 + 10
        radius = max(34.0, min((w - 140) / 2, (h - 92) / 2))
        label_radius = radius + 18
        angles = [(-math.pi / 2) + (2 * math.pi * i / max(1, len(values))) for i in range(len(values))]
        for ring in range(1, 6):
            rr = radius * ring / 5
            ring_coords: List[float] = []
            for angle in angles:
                ring_coords.extend([cx + math.cos(angle) * rr, cy + math.sin(angle) * rr])
            if len(ring_coords) >= 6:
                canvas.create_line(*(ring_coords + ring_coords[:2]), fill=LINE, width=1)
        coords: List[float] = []
        for angle, (label, norm, _text) in zip(angles, values):
            norm = max(0.0, min(1.0, norm))
            x = cx + math.cos(angle) * radius * norm
            y = cy + math.sin(angle) * radius * norm
            coords.extend([x, y])
            sx = cx + math.cos(angle) * radius
            sy = cy + math.sin(angle) * radius
            canvas.create_line(cx, cy, sx, sy, fill=LINE)
            lx = cx + math.cos(angle) * label_radius
            ly = cy + math.sin(angle) * label_radius
            if lx < cx - 8:
                anchor = "e"
            elif lx > cx + 8:
                anchor = "w"
            elif ly < cy:
                anchor = "s"
            else:
                anchor = "n"
            canvas.create_text(lx, ly, text=label, fill=MUTED, font=self.font_small, anchor=anchor)
        if len(coords) >= 6:
            canvas.create_line(*(coords + coords[:2]), fill=ACCENT_2, width=3)
            for x, y in zip(coords[0::2], coords[1::2]):
                canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill=ACCENT_2, outline="")
        canvas.create_text(pad, 18, anchor="nw", fill=INK, text="Final result metrics", font=self.font_bold)
        return True

    def mix_color(self, a: str, b: str, t: float) -> str:
        t = max(0.0, min(1.0, t))
        ar, ag, ab = int(a[1:3], 16), int(a[3:5], 16), int(a[5:7], 16)
        br, bg, bb = int(b[1:3], 16), int(b[3:5], 16), int(b[5:7], 16)
        return f"#{int(ar+(br-ar)*t):02x}{int(ag+(bg-ag)*t):02x}{int(ab+(bb-ab)*t):02x}"

    def refresh_results(self) -> None:
        if not hasattr(self, "result_tree"):
            return
        path = Path(self.collect_project()["result_path"])
        self.result_tree.delete(*self.result_tree.get_children())
        if not path.exists():
            self.metrics_label.config(text="No result yet")
            return
        self.result_data = read_json(path, {})
        metrics = self.result_data.get("metrics", {})
        metric_parts = []
        if "dc_gain" in metrics:
            metric_parts.append(f"Gain {safe_float(metrics.get('dc_gain'), 0.0):.2f} dB")
        if "gbw" in metrics:
            metric_parts.append(f"GBW {safe_float(metrics.get('gbw'), 0.0)/1e6:.2f} MHz")
        if "pm" in metrics:
            metric_parts.append(f"PM {safe_float(metrics.get('pm'), 0.0):.1f} deg")
        if safe_float(metrics.get("cmrr"), 0.0) > 0:
            metric_parts.append(f"CMRR {safe_float(metrics.get('cmrr'), 0.0):.1f} dB")
        if safe_float(metrics.get("psrr"), 0.0) > 0:
            metric_parts.append(f"PSRR {safe_float(metrics.get('psrr'), 0.0):.1f} dB")
        if "power" in metrics:
            metric_parts.append(f"Power {safe_float(metrics.get('power'), 0.0)*1e6:.2f} uW")
        if safe_float(metrics.get("area_um2"), 0.0) > 0:
            metric_parts.append(f"Area {safe_float(metrics.get('area_um2'), 0.0):.1f} um2")
        self.metrics_label.config(text=" | ".join(metric_parts) if metric_parts else "Result loaded")
        for d in self.result_data.get("devices", []):
            self.result_tree.insert("", "end", values=(
                d.get("name", ""),
                d.get("type", ""),
                fmt_si(d.get("W", 0), 1e-6),
                fmt_si(d.get("W_finger", 0), 1e-6),
                fmt_si(d.get("L", 0), 1e-6),
                d.get("fingers", ""),
                fmt_si(d.get("Id", 0), 1e-6),
                fmt_si(d.get("gm", 0), 1e-3),
                fmt_si(d.get("Vgs", 0)),
                fmt_si(d.get("Vds", 0)),
                fmt_si(d.get("Vdsat", 0)),
            ))
        self.draw_charts()

    def generate_writeback(self, show_message: bool = False) -> bool:
        self.save_project()
        result_path = Path(self.collect_project()["result_path"])
        if not result_path.is_file():
            messagebox.showwarning(
                "AmpSys",
                "No optimization result has been generated yet.\n\nRun Optimization first, then use Confirm and Apply in Cadence.",
            )
            self.update_flow_statuses()
            return False
        try:
            output = subprocess.check_output(runner_command("writeback", self.project_path), text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))
            logging.info("Writeback generated: %s", output.strip())
            if show_message:
                messagebox.showinfo("AmpSys", "SKILL writeback generated:\n" + output.strip())
            return True
        except subprocess.CalledProcessError as exc:
            logging.exception("Writeback generation failed")
            messagebox.showerror("AmpSys", exc.output or str(exc))
            return False

    def request_cadence_apply(self) -> None:
        if not self.generate_writeback(show_message=False):
            return
        request_path = self.project_path.parent / "apply.request"
        try:
            request_path.write_text(str(time.time()), encoding="utf-8")
        except Exception as exc:
            logging.exception("Could not write Cadence apply request: %s", request_path)
            messagebox.showerror("AmpSys", f"Could not write apply request:\n{request_path}\n\n{exc}\n\nLog: {self.log_path}")
            return
        skill_path = Path(self.collect_project()["skill_result_path"])
        msg = (
            f"SKILL writeback generated:\n{skill_path}\n\n"
            f"Apply request written:\n{request_path}\n\n"
            "If this GUI was launched from Cadence, the SKILL timer will apply it. "
            "Otherwise use AmpSys -> Apply Last Result."
        )
        logging.info("Cadence apply request written: %s", request_path)
        messagebox.showinfo("AmpSys", msg)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AmpSys Cadence GUI")
    parser.add_argument("--project", default="")
    parser.add_argument("--netlist", default="")
    parser.add_argument("--cadence-lib", default="")
    parser.add_argument("--cadence-cell", default="")
    parser.add_argument("--cadence-view", default="schematic")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    root = tk.Tk()
    app = AmpSysGUI(root, args)
    root.mainloop()


if __name__ == "__main__":
    main()



