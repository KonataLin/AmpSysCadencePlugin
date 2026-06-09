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
from ampsys_runner import expected_library_markers, find_core_executable, has_compiled_engine, has_source_engine, library_ready_marker, resolve_engine_root, resolve_hspice_cmd


ROOT = Path(__file__).resolve().parents[1]
RUNNER = Path(__file__).resolve().with_name("ampsys_runner.py")
WORKSPACE = ROOT / "workspace"
REPO_URL = "https://github.com/KonataLin/AmpSysCadencePlugin"
ISSUES_URL = "https://github.com/KonataLin/AmpSysCadencePlugin/issues"
SPONSOR_URL = "https://www.afdian.com/a/LocyDragon"

BG = "#f5f7fb"
PANEL = "#ffffff"
PANEL_2 = "#eef3fb"
INK = "#172033"
MUTED = "#5e6f86"
LINE = "#d6deea"
CHART_BG = "#ffffff"
ACCENT = "#2563eb"
ACCENT_2 = "#16a34a"
WARN = "#b7791f"
BAD = "#dc2626"
FLOW_OK = "✓"
FLOW_PENDING = "×"

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


def py_command() -> List[str]:
    candidates: List[List[str]] = []
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


def read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
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
            "cache_dir": str(ROOT / "libraries"),
            "temp_dir": str(ROOT / "workspace" / "tmp"),
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
            "fitness_a": 0.7,
            "fitness_b": 1.2,
            "fitness_c": 1.0,
            "fitness_d": 0.25,
            "fitness_e": 0.4,
            "fitness_f": 0.2,
            "fitness_g": 0.28,
        },
            "config": {
            "population_size": 40,
            "max_generations": 30,
            "verbose": True,
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
    candidates = [
        Path.home() / "Desktop" / "autoflow_cache",
        Path.home() / "ampsys_lut" / "autoflow_cache",
        Path.home() / "ampsys_lut",
    ]
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
    temp_dir = str(project_dir / "tmp")
    if lib.get("temp_dir") != temp_dir:
        lib["temp_dir"] = temp_dir
        changed = True

    if os.name != "nt":
        for key in ("model_path", "hspice_dir", "hspice_cmd"):
            if is_windows_path(lib.get(key)):
                lib[key] = "" if key != "hspice_cmd" else "hspice -mt 2"
                changed = True
        cache_text = str(lib.get("cache_dir") or "")
        cache_path = Path(cache_text).expanduser() if cache_text else Path()
        if is_windows_path(cache_text) or not cache_pair_in_dir(cache_path):
            inferred = infer_cache_dir()
            if inferred:
                lib["cache_dir"] = str(inferred)
                cache_path = inferred
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
        self.project = default_project(self.project_path)
        if self.project_path.exists():
            deep_update(self.project, read_json(self.project_path, {}))
        self.project.setdefault("cadence", {})
        self.project.setdefault("library", {})
        self.project.setdefault("specs", {})
        self.project.setdefault("config", {})
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
        self.telemetry_events: List[Dict[str, Any]] = []
        self.result_data: Dict[str, Any] = {}
        self.last_points: List[Dict[str, Any]] = []
        self.flow_status_vars: Dict[str, tk.StringVar] = {}
        self.flow_status_labels: Dict[str, tk.Label] = {}
        self.runner_log_path: Optional[Path] = None
        self.build_started_at = 0.0
        self.scroll_canvases: List[tk.Canvas] = []
        self.status_update_after: Optional[str] = None
        self.status_var = tk.StringVar(root, "Ready")
        self.progress_var = tk.DoubleVar(root, 0.0)

        self.setup_style()
        self.build_ui()
        self.bind_status_traces()
        self.refresh_device_table()
        self.refresh_results()
        if args.netlist and not self.devices:
            self.parse_netlist_from_gui()
        logging.info("AmpSys GUI started. project=%s root=%s", self.project_path, ROOT)

    def setup_style(self) -> None:
        self.root.tk.call("tk", "scaling", 1.12)
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
        self.root.option_add("*Font", f"{self.ui_font_family} 11")
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
        style.configure("Link.TButton", background="#eaf2ff", foreground=ACCENT, borderwidth=1, padding=(12, 7), font=self.font_normal)
        style.map("Link.TButton", background=[("active", "#dbeafe")], foreground=[("active", ACCENT)])
        style.configure("TButton", background=PANEL_2, foreground=INK, borderwidth=1, focusthickness=0, padding=(14, 8), font=self.font_normal)
        style.map("TButton", background=[("active", "#dce8f8")], foreground=[("disabled", "#98a4b5")])
        style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff", font=self.font_normal)
        style.map("Accent.TButton", background=[("active", "#1d4ed8")])
        style.configure("Danger.TButton", background="#fee2e2", foreground=BAD, font=self.font_normal)
        style.map("Danger.TButton", background=[("active", "#fecaca")])
        style.configure("TEntry", fieldbackground="#ffffff", foreground=INK, insertcolor=INK, bordercolor=LINE, padding=6, font=self.font_normal)
        style.configure("TCombobox", fieldbackground="#ffffff", foreground=INK, bordercolor=LINE, arrowcolor=INK, font=self.font_normal)
        style.configure("TCheckbutton", background=BG, foreground=INK, font=self.font_normal)
        style.configure("Card.TCheckbutton", background=PANEL, foreground=INK, font=self.font_normal)
        style.configure("Horizontal.TScale", background=PANEL)
        style.configure("Treeview", background="#ffffff", foreground=INK, fieldbackground="#ffffff", bordercolor=LINE, rowheight=34, font=self.font_normal)
        style.configure("Treeview.Heading", background=PANEL_2, foreground=INK, relief="flat", font=self.font_bold)
        style.map("Treeview", background=[("selected", "#dbeafe")], foreground=[("selected", INK)])
        style.configure("Horizontal.TProgressbar", background=ACCENT_2, troughcolor="#e7edf6", bordercolor="#e7edf6", lightcolor=ACCENT_2, darkcolor=ACCENT_2)

    def setup_logging(self) -> None:
        for handler in list(logging.getLogger().handlers):
            logging.getLogger().removeHandler(handler)
        logging.basicConfig(
            filename=str(self.log_path),
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            encoding="utf-8",
        )

    def report_callback_exception(self, exc_type, exc_value, exc_tb) -> None:
        logging.exception("Tk callback failed", exc_info=(exc_type, exc_value, exc_tb))
        messagebox.showerror("AmpSys GUI error", f"{exc_value}\n\nLog: {self.log_path}")

    def bind_status_traces(self) -> None:
        for bag in (self.lib_vars, self.spec_vars, self.cfg_vars, self.top_vars):
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
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=20, pady=(16, 10))
        header.grid_columnconfigure(0, weight=1)
        header.grid_columnconfigure(1, weight=0)

        title_box = tk.Frame(header, bg=BG)
        title_box.grid(row=0, column=0, sticky="ew")
        tk.Label(title_box, text="AmpSys", bg=BG, fg=INK, font=self.font_heading).grid(row=0, column=0, sticky="w")
        mode_text = "Windows LUT Builder" if self.can_build_library_here() else "Linux Cache-Only"
        tk.Label(title_box, text=mode_text, bg="#eaf2ff", fg=ACCENT, font=self.font_bold, padx=10, pady=4).grid(row=0, column=1, sticky="w", padx=(14, 0), pady=(4, 0))
        tk.Label(title_box, text="Cadence schematic sizing cockpit", bg=BG, fg=MUTED, font=self.font_bold).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))

        actions = tk.Frame(header, bg=BG)
        actions.grid(row=0, column=1, sticky="e")
        ttk.Button(actions, text="Repo", style="Link.TButton", command=lambda: self.open_url(REPO_URL)).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Issues", style="Link.TButton", command=lambda: self.open_url(ISSUES_URL)).pack(side="left", padx=6)
        ttk.Button(actions, text="Sponsor", style="Link.TButton", command=lambda: self.open_url(SPONSOR_URL)).pack(side="left", padx=6)
        ttk.Button(actions, text="Open Workspace", command=self.open_workspace).pack(side="left", padx=(12, 0))
        ttk.Button(actions, text="Load Project", command=self.load_project_dialog).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Save Project", command=self.save_project).pack(side="left", padx=(8, 0))

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=20, pady=(0, 18))
        self.main_page = self.add_scroll_page(body)
        self.root.bind_all("<MouseWheel>", self.on_mousewheel, add="+")
        self.root.bind_all("<Button-4>", self.on_mousewheel, add="+")
        self.root.bind_all("<Button-5>", self.on_mousewheel, add="+")

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

    def card(self, parent: tk.Widget, title: str) -> ttk.Frame:
        frame = ttk.Frame(parent, style="Card.TFrame", padding=14)
        label = ttk.Label(frame, text=title, style="Card.TLabel", font=self.font_bold)
        label.grid(row=0, column=0, columnspan=8, sticky="w", pady=(0, 10))
        return frame

    def field(self, parent: tk.Widget, label: str, var: tk.Variable, row: int, col: int, width: int = 20, browse: str = "") -> ttk.Entry:
        ttk.Label(parent, text=label, style="MutedCard.TLabel").grid(row=row, column=col, padx=(12, 6), pady=8, sticky="w")
        ent = ttk.Entry(parent, textvariable=var, width=width)
        ent.grid(row=row, column=col + 1, padx=(0, 6), pady=8, sticky="ew")
        if browse:
            def choose() -> None:
                if browse == "dir":
                    val = filedialog.askdirectory(initialdir=str(Path(var.get() or ROOT).expanduser()))
                else:
                    val = filedialog.askopenfilename(initialdir=str(Path(var.get() or ROOT).expanduser().parent))
                if val:
                    var.set(val)
            ttk.Button(parent, text="...", command=choose, width=3).grid(row=row, column=col + 2, padx=(0, 8), pady=8, sticky="w")
        return ent

    def combo(self, parent: tk.Widget, label: str, var: tk.Variable, row: int, col: int, values: Iterable[str], width: int = 16) -> ttk.Combobox:
        ttk.Label(parent, text=label, style="MutedCard.TLabel").grid(row=row, column=col, padx=(12, 6), pady=8, sticky="w")
        cb = ttk.Combobox(parent, textvariable=var, values=list(values), width=width, state="readonly")
        cb.grid(row=row, column=col + 1, padx=(0, 8), pady=8, sticky="ew")
        return cb

    def check(self, parent: tk.Widget, text: str, var: tk.Variable, row: int, col: int) -> ttk.Checkbutton:
        cb = ttk.Checkbutton(parent, text=text, variable=var, style="Card.TCheckbutton")
        cb.grid(row=row, column=col, padx=12, pady=7, sticky="w")
        return cb

    def flow_section(self, parent: tk.Widget, key: str, title: str, row: int) -> ttk.Frame:
        frame = ttk.Frame(parent, style="Shell.TFrame", padding=(16, 14, 16, 14))
        frame.grid(row=row, column=0, sticky="ew", pady=(0, 12))
        frame.grid_columnconfigure(1, weight=1)
        status = tk.Label(frame, textvariable=self.flow_status_vars[key], width=3, bg="#fee2e2", fg=BAD, font=(self.ui_font_family, 13, "bold"), padx=4, pady=3)
        status.grid(row=0, column=0, rowspan=2, padx=(0, 14), sticky="n")
        self.flow_status_labels[key] = status
        ttk.Label(frame, text=title, style="Card.TLabel", font=self.font_section).grid(row=0, column=1, sticky="w")
        content = ttk.Frame(frame, style="StepBody.TFrame")
        content.grid(row=1, column=1, sticky="ew", pady=(10, 0))
        for col in (1, 3, 5):
            content.grid_columnconfigure(col, weight=1)
        return content

    def set_flow_status(self, key: str, ok: bool) -> None:
        if key in self.flow_status_vars:
            self.flow_status_vars[key].set(FLOW_OK if ok else FLOW_PENDING)
        label = self.flow_status_labels.get(key)
        if label:
            if ok:
                label.configure(bg="#dcfce7", fg="#15803d")
            else:
                label.configure(bg="#fee2e2", fg=BAD)

    def can_build_library_here(self) -> bool:
        return os.name == "nt"

    def cache_config_ready(self, lib: Dict[str, Any]) -> bool:
        return bool(lib.get("cache_dir")) and bool(lib.get("nmos_name")) and bool(lib.get("pmos_name"))

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

        missing_current = [str(d.get("name", "")) for d in mos if safe_float(d.get("current"), 0.0) <= 0]
        if missing_current:
            issues.append("Set Id uA for every MOS before Run: " + ", ".join(missing_current[:30]))

        return issues

    def spec_setup_issues(self) -> List[str]:
        required = {
            "gain_min": "Gain min dB",
            "gbw": "GBW MHz",
            "pm_min": "PM min deg",
            "load_cap": "Load cap pF",
            "V_in_cm": "V in cm",
            "V_out_cm": "V out cm",
            "saturation_margin": "Saturation margin",
        }
        missing = [label for key, label in required.items() if not str(self.spec_vars.get(key, "")).strip()]
        issues: List[str] = []
        if missing:
            issues.append("Complete these Specs fields: " + ", ".join(missing))
        if safe_int(self.cfg_vars.get("population_size"), 0) <= 0:
            issues.append("Population must be a positive integer.")
        if safe_int(self.cfg_vars.get("max_generations"), 0) <= 0:
            issues.append("Generations must be a positive integer.")
        return issues

    def update_flow_statuses(self) -> None:
        if not self.flow_status_vars:
            return
        self.auto_fill_lut_from_cache()
        lib = self.coerce_library()
        self.set_flow_status("library", self.cache_config_ready(lib) and self.library_ready(lib))

        self.set_flow_status("devices", not self.device_setup_issues())

        self.set_flow_status("specs", not self.spec_setup_issues())
        telemetry = Path(self.collect_project()["telemetry_path"])
        self.set_flow_status("run", bool(self.proc and self.proc.poll() is None) or telemetry.is_file() or Path(self.project_path.parent / "ampsys_optimize.log").is_file())
        self.set_flow_status("results", Path(self.project_path.parent / "result.json").is_file())

    def build_flow_page(self) -> None:
        page = self.main_page
        page.grid_columnconfigure(0, weight=1)
        self.flow_status_vars = {key: tk.StringVar(self.root, FLOW_PENDING) for key in ("library", "devices", "specs", "run", "results")}

        flow = ttk.Frame(page, style="Shell.TFrame", padding=(16, 12, 16, 12))
        flow.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        flow_items = [
            ("library", "LUT Cache"),
            ("devices", "Devices"),
            ("specs", "Specs"),
            ("run", "Run"),
            ("results", "Results"),
        ]
        for idx, (key, label) in enumerate(flow_items):
            ttk.Label(flow, textvariable=self.flow_status_vars[key], style="Card.TLabel", font=self.font_bold).grid(row=0, column=idx * 2, padx=(0, 6))
            ttk.Label(flow, text=label, style="MutedCard.TLabel").grid(row=0, column=idx * 2 + 1, padx=(0, 24))

        row = 1
        lut = self.flow_section(page, "library", "LUT Cache", row)
        self.field(lut, "Cache dir", self.lib_vars.vars["cache_dir"], 0, 0, width=58, browse="dir")
        self.field(lut, "NMOS name", self.lib_vars.vars["nmos_name"], 1, 0)
        self.field(lut, "PMOS name", self.lib_vars.vars["pmos_name"], 1, 2)
        self.field(lut, "Corner/lib", self.lib_vars.vars["model_lib"], 2, 0)
        self.field(lut, "Temp C", self.lib_vars.vars["temperature"], 2, 2)
        self.field(lut, "VDD V", self.lib_vars.vars["process_vdd"], 3, 0)
        if self.can_build_library_here():
            self.field(lut, "Model path", self.lib_vars.vars["model_path"], 4, 0, width=58, browse="file")
            self.field(lut, "HSPICE dir", self.lib_vars.vars["hspice_dir"], 5, 0, width=42, browse="dir")
            ttk.Button(lut, text="Build Library", command=lambda: self.start_runner("build-library")).grid(row=6, column=0, columnspan=2, padx=12, pady=8, sticky="ew")
        row += 1

        devices = self.flow_section(page, "devices", "Device Currents", row)
        toolbar = ttk.Frame(devices, style="StepBody.TFrame")
        toolbar.grid(row=0, column=0, columnspan=6, sticky="ew")
        ttk.Button(toolbar, text="Add MOS", command=self.add_device).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="Remove Selected", style="Danger.TButton", command=self.remove_selected_devices).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Apply Editor", command=self.apply_device_editor).pack(side="left", padx=6)
        self.warning_label = ttk.Label(toolbar, text="", style="MutedCard.TLabel")
        self.warning_label.pack(side="right")

        cols = ("name", "type", "nodes", "current_uA", "match_group", "bw", "value")
        self.device_tree = ttk.Treeview(devices, columns=cols, show="headings", selectmode="extended", height=8)
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
        self.device_tree.grid(row=1, column=0, columnspan=6, sticky="ew", pady=(8, 6))
        self.device_tree.bind("<<TreeviewSelect>>", lambda _e: self.load_device_editor())
        self.dev_edit = {k: tk.StringVar(self.root, "") for k in ("name", "type", "nodes", "current_uA", "match_group", "bw", "value")}
        labels = [("Name", "name"), ("Type", "type"), ("Nodes", "nodes"), ("Id uA", "current_uA"), ("Match", "match_group"), ("BW", "bw"), ("R/C", "value")]
        for idx, (lab, key) in enumerate(labels):
            ttk.Label(devices, text=lab, style="MutedCard.TLabel").grid(row=2 + (idx // 4) * 2, column=idx % 4, padx=6, pady=(4, 2), sticky="w")
            ttk.Entry(devices, textvariable=self.dev_edit[key], width=30 if key == "nodes" else 16).grid(row=3 + (idx // 4) * 2, column=idx % 4, padx=6, pady=(0, 8), sticky="ew")

        row += 1
        specs = self.flow_section(page, "specs", "Specs And Run", row)
        self.display_field(specs, "Gain min dB", "gain_min", 0, 0)
        self.display_field(specs, "GBW MHz", "gbw", 0, 2, scale=1e6)
        self.display_field(specs, "PM min deg", "pm_min", 1, 0)
        self.display_field(specs, "Load cap pF", "load_cap", 1, 2, scale=1e-12)
        self.display_field(specs, "V in cm", "V_in_cm", 2, 0)
        self.display_field(specs, "V out cm", "V_out_cm", 2, 2)
        self.display_field(specs, "Saturation margin", "saturation_margin", 3, 0)
        self.field(specs, "Population", self.cfg_vars.vars["population_size"], 3, 2)
        self.field(specs, "Generations", self.cfg_vars.vars["max_generations"], 4, 0)
        ttk.Button(specs, text="Run Optimization", style="Accent.TButton", command=lambda: self.start_runner("optimize")).grid(row=5, column=0, columnspan=2, padx=12, pady=10, sticky="ew")
        ttk.Button(specs, text="Stop", style="Danger.TButton", command=self.stop_process).grid(row=5, column=2, columnspan=2, padx=12, pady=10, sticky="ew")
        ttk.Progressbar(specs, variable=self.progress_var, maximum=100, length=260).grid(row=6, column=0, columnspan=4, padx=12, pady=(4, 2), sticky="ew")
        ttk.Label(specs, textvariable=self.status_var, style="MutedCard.TLabel").grid(row=7, column=0, columnspan=4, padx=12, sticky="w")

        row += 1
        viz = self.flow_section(page, "run", "Run Visualization And Log", row)
        viz.grid_columnconfigure(0, weight=1)
        viz.grid_columnconfigure(1, weight=1)
        left = ttk.Frame(viz, style="StepBody.TFrame")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        ttk.Label(left, text="Convergence", style="Card.TLabel", font=self.font_bold).pack(anchor="w")
        self.conv_canvas = tk.Canvas(left, bg=CHART_BG, highlightthickness=0, height=220)
        self.conv_canvas.pack(fill="both", expand=True, pady=(8, 8))
        ttk.Label(left, text="Runner log", style="Card.TLabel", font=self.font_bold).pack(anchor="w")
        self.log_text = tk.Text(left, bg="#ffffff", fg=INK, insertbackground=INK, height=10, relief="solid", bd=1, wrap="word")
        self.log_text.pack(fill="both", expand=False, pady=(8, 0))
        right = ttk.Frame(viz, style="StepBody.TFrame")
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        ttk.Label(right, text="Population metric web", style="Card.TLabel", font=self.font_bold).pack(anchor="w")
        self.web_canvas = tk.Canvas(right, bg=CHART_BG, highlightthickness=0, height=390)
        self.web_canvas.pack(fill="both", expand=True, pady=(8, 0))

        row += 1
        results = self.flow_section(page, "results", "Results And Cadence Writeback", row)
        result_top = ttk.Frame(results, style="StepBody.TFrame")
        result_top.grid(row=0, column=0, sticky="ew", columnspan=8)
        ttk.Button(result_top, text="Refresh Result", command=self.refresh_results).pack(side="left", padx=(0, 8))
        ttk.Button(result_top, text="Generate SKILL Writeback", command=self.generate_writeback).pack(side="left", padx=8)
        ttk.Button(result_top, text="Confirm and Apply in Cadence", style="Accent.TButton", command=self.request_cadence_apply).pack(side="left", padx=8)
        self.metrics_label = ttk.Label(result_top, text="", style="MutedCard.TLabel")
        self.metrics_label.pack(side="right")
        cols = ("name", "type", "W_um", "L_um", "fingers", "Id_uA", "gm_mS", "Vgs", "Vds", "Vdsat")
        self.result_tree = ttk.Treeview(results, columns=cols, show="headings", height=8)
        for col in cols:
            self.result_tree.heading(col, text=col)
            self.result_tree.column(col, width=110, minwidth=90, stretch=True)
        self.result_tree.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self.draw_empty_charts()
        self.update_flow_statuses()

    def display_field(self, parent: tk.Widget, label: str, key: str, row: int, col: int, scale: float = 1.0) -> None:
        if scale != 1.0:
            var = self.spec_vars.vars[key]
            var.set(fmt_si(var.get(), scale))
            setattr(var, "_ampsys_scale", scale)
        self.field(parent, label, self.spec_vars.vars[key], row, col)

    def add_device(self) -> None:
        self.devices.append({"name": f"M{len(self.devices)+1}", "type": "nmos", "nodes": ["D", "G", "S", "B"], "current": 10e-6, "match_group": "", "bw_factor": 1.0})
        self.refresh_device_table()

    def remove_selected_devices(self) -> None:
        selected = set(self.device_tree.selection())
        if not selected:
            return
        self.devices = [d for idx, d in enumerate(self.devices) if str(idx) not in selected]
        self.refresh_device_table()

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
        self.update_flow_statuses()

    def current_parse_signature(self) -> str:
        return "|".join([
            str(self.top_vars.get("netlist_path", "")),
            str(self.lib_vars.get("nmos_name", "")),
            str(self.lib_vars.get("pmos_name", "")),
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
            pins, devices, warnings = parse_netlist(path, split_csv(self.lib_vars.get("nmos_name")), split_csv(self.lib_vars.get("pmos_name")))
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
            self.refresh_device_table()
            suffix = f" with {len(warnings)} warning(s)" if warnings else ""
            self.status_var.set(f"Parsed {len(devices)} devices from {path.name}{suffix}")
            self.last_parse_signature = self.current_parse_signature()
            self.update_flow_statuses()
        except Exception as exc:
            messagebox.showerror("AmpSys netlist parse failed", str(exc))

    def collect_project(self) -> Dict[str, Any]:
        project = default_project(self.project_path)
        project["project_dir"] = str(self.project_path.parent)
        project["engine_root"] = self.top_vars.get("engine_root")
        project["netlist_path"] = self.top_vars.get("netlist_path")
        project["skip_kcl"] = True
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
        project["library"]["temp_dir"] = str(self.project_path.parent / "tmp")
        project["specs"] = self.coerce_specs()
        project["specs"]["enable_vds_iteration"] = True
        project["config"] = self.coerce_config()
        project["config"]["fast_mode"] = True
        project["config"]["verbose"] = True
        project["devices"] = self.devices
        project["passives"] = []
        return project

    def coerce_library(self) -> Dict[str, Any]:
        raw = self.lib_vars.as_strings()
        bools = {"force_rescan", "use_batch_mode"}
        floats = {"temperature", "process_vdd", "L_min", "scan_width", "vgs_start", "vgs_stop", "vgs_step", "vds_start", "vds_stop", "vds_step", "vsb_start", "vsb_stop", "vsb_step"}
        ints = {"batch_size", "batch_timeout_ms"}
        out: Dict[str, Any] = {}
        for k, v in raw.items():
            if k in bools:
                out[k] = bool(v)
            elif k in floats:
                out[k] = safe_float(v)
            elif k in ints:
                out[k] = safe_int(v)
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
                out[key] = ""
            else:
                scale = getattr(var, "_ampsys_scale", 1.0)
                out[key] = safe_float(val) * scale
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

    def save_project(self) -> None:
        self.project = self.collect_project()
        self.project_path.parent.mkdir(parents=True, exist_ok=True)
        self.project_path.write_text(json.dumps(self.project, indent=2, ensure_ascii=False), encoding="utf-8")
        logging.info("Project saved: %s", self.project_path)
        self.status_var.set(f"Saved {self.project_path}")
        self.update_flow_statuses()

    def load_project_dialog(self) -> None:
        path = filedialog.askopenfilename(initialdir=str(self.project_path.parent), filetypes=[("AmpSys project", "*.json"), ("All files", "*.*")])
        if not path:
            return
        messagebox.showinfo("AmpSys", "The selected project will open in a new GUI process.")
        subprocess.Popen(py_command() + [str(Path(__file__).resolve()), "--project", path], close_fds=True)

    def open_workspace(self) -> None:
        self.project_path.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(self.project_path.parent))
        else:
            subprocess.Popen(["xdg-open", str(self.project_path.parent)])

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
                ("cache_dir", "Cache dir"),
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
        self.telemetry_events.clear()
        self.last_points.clear()
        self.log_text.delete("1.0", "end")
        self.status_var.set(f"Starting {cmd}...")
        telemetry = Path(self.collect_project()["telemetry_path"])
        if telemetry.exists():
            telemetry.write_text("", encoding="utf-8")
        command = py_command() + [str(RUNNER), cmd, "--project", str(self.project_path)]
        env = os.environ.copy()
        env["AMPSYS_ENGINE_ROOT"] = self.top_vars.get("engine_root")
        env["AMPSYS_PLUGIN_ROOT"] = str(ROOT)
        logging.info("Starting runner: %s", command)
        self.proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT), env=env)
        self.update_flow_statuses()
        threading.Thread(target=self.consume_stdout, daemon=True).start()
        self.root.after(200, self.poll_process)

    def consume_stdout(self) -> None:
        if not self.proc or not self.proc.stdout:
            return
        for line in self.proc.stdout:
            self.stdout_queue.put(line)

    def poll_process(self) -> None:
        while True:
            try:
                line = self.stdout_queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.insert("end", line)
            self.log_text.see("end")
            if self.runner_log_path:
                with self.runner_log_path.open("a", encoding="utf-8") as f:
                    f.write(line)
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
        telemetry = Path(self.collect_project()["telemetry_path"])
        if not telemetry.exists():
            return
        lines = telemetry.read_text(encoding="utf-8", errors="ignore").splitlines()
        for line in lines[self.telemetry_seen:]:
            try:
                event = json.loads(line)
            except Exception:
                continue
            self.telemetry_events.append(event)
            self.handle_event(event)
        self.telemetry_seen = len(lines)

    def handle_event(self, event: Dict[str, Any]) -> None:
        status = event.get("status", "")
        phase = event.get("phase", "")
        if status == "generation":
            gen = event.get("generation", 0)
            max_gen = event.get("max_generations", 1)
            self.progress_var.set(100 * safe_float(event.get("progress"), gen / max(1, max_gen)))
            best = event.get("best", {})
            self.status_var.set(f"Gen {gen}/{max_gen}  best={best.get('fitness', 0):.4g}")
            self.last_points = event.get("points", [])
            self.draw_charts()
        elif status == "start":
            if phase == "build_library" and event.get("total_points"):
                self.status_var.set(f"Building LUT: {event.get('total_points'):,} HSPICE points")
                self.progress_var.set(2)
            else:
                self.status_var.set(f"{phase} started")
        elif status == "done":
            self.progress_var.set(100)
            self.status_var.set(f"{phase} done")
        elif status == "error":
            self.status_var.set(event.get("message", "Error"))

    def draw_empty_charts(self) -> None:
        self.conv_canvas.delete("all")
        self.web_canvas.delete("all")
        self.conv_canvas.create_text(30, 30, anchor="nw", fill=MUTED, text="Run optimization to draw fitness history.", font=self.font_bold)
        self.web_canvas.create_text(30, 30, anchor="nw", fill=MUTED, text="Population metrics will appear here per generation.", font=self.font_bold)

    def draw_charts(self) -> None:
        gen_events = [e for e in self.telemetry_events if e.get("status") == "generation"]
        fitness = [safe_float(e.get("best", {}).get("fitness"), 0.0) for e in gen_events]
        self.draw_line(self.conv_canvas, fitness)
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
        canvas.create_text(pad, 14, anchor="nw", fill=INK, text=f"Best fitness {values[-1]:.5g}", font=self.font_bold)

    def draw_metric_web(self, canvas: tk.Canvas, points: List[Dict[str, Any]]) -> None:
        canvas.delete("all")
        w = max(10, canvas.winfo_width())
        h = max(10, canvas.winfo_height())
        pad = 52
        axes = [("gain", "Gain"), ("gbw", "GBW"), ("pm", "PM"), ("power", "Power"), ("noise", "Noise"), ("fitness", "Fit")]
        canvas.create_rectangle(0, 0, w, h, fill=CHART_BG, outline="")
        if not points:
            canvas.create_text(pad, pad, anchor="nw", fill=MUTED, text="Waiting for population points...")
            return
        xs = [pad + i * (w - 2 * pad) / max(1, len(axes) - 1) for i in range(len(axes))]
        for x, (_key, label) in zip(xs, axes):
            canvas.create_line(x, pad, x, h - pad, fill=LINE)
            canvas.create_text(x, h - pad + 18, text=label, fill=MUTED, font=self.font_small)
        mins: Dict[str, float] = {}
        maxs: Dict[str, float] = {}
        for key, _ in axes:
            vals = [safe_float(p.get(key), 0.0) for p in points]
            mins[key], maxs[key] = min(vals), max(vals)
            if math.isclose(mins[key], maxs[key]):
                mins[key] -= 1.0
                maxs[key] += 1.0
        sorted_points = sorted(points, key=lambda p: safe_float(p.get("fitness"), 0.0))
        sample = sorted_points[-80:]
        for p in sample:
            fit = safe_float(p.get("fitness"), 0.0)
            fit_norm = (fit - mins["fitness"]) / (maxs["fitness"] - mins["fitness"])
            color = self.mix_color("#c7d2fe", ACCENT_2, fit_norm)
            coords = []
            for x, (key, _label) in zip(xs, axes):
                val = safe_float(p.get(key), 0.0)
                if key in ("power", "noise"):
                    norm = 1.0 - (val - mins[key]) / (maxs[key] - mins[key])
                else:
                    norm = (val - mins[key]) / (maxs[key] - mins[key])
                y = h - pad - norm * (h - 2 * pad)
                coords.extend([x, y])
            if len(coords) >= 4:
                canvas.create_line(*coords, fill=color, width=1)
        best = sorted_points[-1]
        best_coords = []
        for x, (key, _label) in zip(xs, axes):
            val = safe_float(best.get(key), 0.0)
            norm = (val - mins[key]) / (maxs[key] - mins[key])
            if key in ("power", "noise"):
                norm = 1.0 - norm
            y = h - pad - norm * (h - 2 * pad)
            best_coords.extend([x, y])
        canvas.create_line(*best_coords, fill=ACCENT_2, width=3)
        canvas.create_text(pad, 18, anchor="nw", fill=INK, text=f"{len(points)} individuals, bright line = current best", font=self.font_bold)

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
        self.metrics_label.config(text=f"Gain {metrics.get('dc_gain', 0):.2f} dB | GBW {metrics.get('gbw', 0)/1e6:.2f} MHz | PM {metrics.get('pm', 0):.1f} deg | Power {metrics.get('power', 0)*1e6:.2f} uW")
        for d in self.result_data.get("devices", []):
            self.result_tree.insert("", "end", values=(
                d.get("name", ""),
                d.get("type", ""),
                fmt_si(d.get("W", 0), 1e-6),
                fmt_si(d.get("L", 0), 1e-6),
                d.get("fingers", ""),
                fmt_si(d.get("Id", 0), 1e-6),
                fmt_si(d.get("gm", 0), 1e-3),
                fmt_si(d.get("Vgs", 0)),
                fmt_si(d.get("Vds", 0)),
                fmt_si(d.get("Vdsat", 0)),
            ))

    def generate_writeback(self) -> bool:
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
            output = subprocess.check_output(py_command() + [str(RUNNER), "writeback", "--project", str(self.project_path)], text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))
            logging.info("Writeback generated: %s", output.strip())
            messagebox.showinfo("AmpSys", "SKILL writeback generated:\n" + output.strip())
            return True
        except subprocess.CalledProcessError as exc:
            logging.exception("Writeback generation failed")
            messagebox.showerror("AmpSys", exc.output or str(exc))
            return False

    def request_cadence_apply(self) -> None:
        if not self.generate_writeback():
            return
        request_path = self.project_path.parent / "apply.request"
        request_path.write_text(str(time.time()), encoding="utf-8")
        msg = f"Apply request written:\n{request_path}\n\nIf this GUI was launched from Cadence, the SKILL timer will apply it. Otherwise use AmpSys -> Apply Last Result."
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
