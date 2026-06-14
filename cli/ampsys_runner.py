#!/usr/bin/env python3
"""Headless AmpSys runner used by the GUI and Cadence SKILL plugin."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import traceback
import platform
import subprocess
import shutil
import shlex
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional


ENGINE_PACKAGES = ("AmpSys", "yami", "TheScanner", "acsolver")
CORE_INTERNAL_ENV = "AMPSYS_CORE_INTERNAL"
DISABLE_CORE_ENV = "AMPSYS_DISABLE_CORE"
ALLOWED_SPECTRE_ACCEL = {
    "", "auto", "default",
    "off", "none", "false", "0", "baseline", "spectre",
    "+aps", "aps", "++aps", "ppaps", "aps++",
}


def normalized_arch() -> str:
    arch = platform.machine().lower()
    if arch in ("amd64", "x86_64"):
        return "amd64" if sys.platform.startswith("win") else "x86_64"
    if arch in ("aarch64", "arm64"):
        return "arm64"
    return arch.replace(" ", "_")


def os_name() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "macos"
    return sys.platform


def platform_tag() -> str:
    return f"{os_name()}_{normalized_arch()}_py{sys.version_info.major}{sys.version_info.minor}"


def binary_platform_tag() -> str:
    return f"{os_name()}_{normalized_arch()}"


def core_executable_name() -> str:
    return "ampsys_core.exe" if sys.platform.startswith("win") else "ampsys_core"


def core_search_roots(plugin_root: Path) -> List[Path]:
    roots: List[Path] = []
    env_root = os.environ.get("AMPSYS_CORE_ROOT", "").strip()
    if env_root:
        roots.append(Path(env_root).expanduser())
    roots.extend([
        plugin_root / "core",
        plugin_root / "bin",
        plugin_root,
    ])
    return roots


def find_core_executable(plugin_root: Optional[Path] = None) -> Optional[Path]:
    plugin_root = (plugin_root or Path(__file__).resolve().parents[1]).resolve()
    name = core_executable_name()
    stem = Path(name).stem
    tag = binary_platform_tag()
    candidates: List[Path] = []
    for root in core_search_roots(plugin_root):
        candidates.extend([
            root / tag / name,
            root / tag / stem / name,
            root / tag / name / name,
            root / name,
            root / stem / name,
            root / name / name,
        ])
    for cand in candidates:
        if cand.is_file():
            return cand.resolve()
    return None


def should_delegate_to_core(cmd: str = "", argv: Optional[List[str]] = None) -> bool:
    if os.environ.get(CORE_INTERNAL_ENV) == "1":
        return False
    if os.environ.get(DISABLE_CORE_ENV) == "1":
        return False
    if cmd in {"", "-h", "--help"} or (argv and any(arg in {"-h", "--help"} for arg in argv)):
        return False
    if cmd in {"writeback", "diagnose", "spectre-benchmark", "compare-cache"}:
        return False
    return find_core_executable() is not None

def project_arg_path(argv: List[str]) -> Optional[Path]:
    try:
        idx = argv.index("--project")
        return Path(argv[idx + 1]).resolve()
    except Exception:
        return None


def delegate_to_core(argv: List[str]) -> int:
    exe = find_core_executable()
    if exe is None:
        return 127
    cmd = argv[0] if argv else ""
    project_path = project_arg_path(argv)
    if project_path and cmd in {"build-library", "optimize", "writeback"}:
        try:
            project = json.loads(project_path.read_text(encoding="utf-8-sig"))
            print_project_summary(project, project_path, cmd)
        except Exception as exc:
            print(f"[AmpSys] WARNING: could not print project summary: {exc}", file=sys.stderr)
    argv = prepare_project_for_core(argv)
    env = os.environ.copy()
    env[CORE_INTERNAL_ENV] = "1"
    plugin_root = Path(__file__).resolve().parents[1]
    env.setdefault("AMPSYS_PLUGIN_ROOT", str(plugin_root))
    env.setdefault("AMPSYS_ENGINE_ROOT", str(plugin_root))
    env.setdefault("AMPSYS_NUMBA_CACHE", "0")
    if project_path and cmd == "build-library":
        try:
            project = json.loads(project_path.read_text(encoding="utf-8-sig"))
            lib = project.get("library", {})
            if normalize_simulator_backend(lib) == "spectre":
                validate_spectre_accel(lib)
                apply_spectre_env_settings(env, lib)
        except Exception as exc:
            print(f"[AmpSys] WARNING: could not prepare Spectre environment: {exc}", file=sys.stderr)
    proc = subprocess.run([str(exe), *argv], env=env)
    if proc.returncode == 0 and cmd == "optimize" and project_path:
        try:
            run_writeback(project_path)
        except Exception as exc:
            print(f"[AmpSys] WARNING: source writeback regeneration failed: {exc}", file=sys.stderr)
    return int(proc.returncode)


def prepare_project_for_core(argv: List[str]) -> List[str]:
    if not argv or argv[0] not in {"build-library", "optimize", "spectre-benchmark"}:
        return argv
    try:
        idx = argv.index("--project")
        project_path = Path(argv[idx + 1]).resolve()
    except Exception:
        return argv

    project = json.loads(project_path.read_text(encoding="utf-8-sig"))
    lib = project.get("library", {})
    changed = False
    backend = normalize_simulator_backend(lib)
    if backend == "spectre" and isolate_spectre_cache_dir(lib):
        changed = True
    if backend == "hspice" and lib.get("hspice_dir"):
        lib["hspice_cmd"] = resolve_hspice_cmd(lib)
        changed = True
    if backend == "spectre":
        validate_spectre_accel(lib)
        if lib.get("spectre_dir"):
            lib["spectre_cmd"] = resolve_spectre_cmd(lib)
            changed = True
    marker = prepare_existing_library_cache(lib, project_path)
    if marker:
        changed = True
    if changed:
        project["library"] = lib
        project_path.write_text(json.dumps(project, indent=2, ensure_ascii=False), encoding="utf-8")
    return argv


def has_compiled_engine(root: Path) -> bool:
    suffixes = (".pyd", ".so", ".dll", ".dylib")
    return all(any(p.name.startswith(pkg + ".") and p.suffix.lower() in suffixes for p in root.glob(pkg + ".*")) for pkg in ENGINE_PACKAGES)


def has_source_engine(root: Path) -> bool:
    return all((root / pkg).exists() for pkg in ENGINE_PACKAGES)



def available_engine_dirs(base: Path) -> List[str]:
    engines = base / "engines"
    if not engines.exists():
        return []
    return sorted(p.name for p in engines.iterdir() if p.is_dir())


def resolve_engine_root(base: Path) -> Path:
    base = base.resolve()
    candidates = [
        base / "engines" / platform_tag(),
        base / "engines" / f"{os_name()}_{normalized_arch()}",
        base,
    ]
    for cand in candidates:
        if cand.exists() and (has_compiled_engine(cand) or has_source_engine(cand)):
            return cand
    if (base / "engines").exists():
        found = ", ".join(available_engine_dirs(base)) or "none"
        raise RuntimeError(
            f"No AmpSys engine package matches this environment: {platform_tag()}. "
            f"Available engine dirs under {base / 'engines'}: {found}. "
            "Build or copy the matching .pyd/.so package for this OS, CPU, and Python version."
        )
    return base


def add_engine_path(engine_root: str) -> Path:
    if os.environ.get(CORE_INTERNAL_ENV) == "1" and getattr(sys, "frozen", False):
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)).resolve()
        _scrub_external_engine_paths(
            root,
            [
                engine_root,
                os.environ.get("AMPSYS_ENGINE_ROOT", ""),
                os.environ.get("AMPSYS_PLUGIN_ROOT", ""),
            ],
        )
        return root
    root_text = engine_root or os.environ.get("AMPSYS_ENGINE_ROOT", "")
    if root_text:
        root = resolve_engine_root(Path(root_text).expanduser())
    else:
        plugin_root = Path(__file__).resolve().parents[1]
        root = resolve_engine_root(plugin_root)
        if not (has_compiled_engine(root) or has_source_engine(root)):
            root = resolve_engine_root(Path(__file__).resolve().parents[2])
    root = root.resolve()
    if has_compiled_engine(root):
        os.environ.setdefault("AMPSYS_NUMBA_CACHE", "0")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _resolve_sys_path_entry(entry: str) -> Optional[Path]:
    if not entry:
        return None
    try:
        return Path(entry).expanduser().resolve()
    except Exception:
        return None


def _scrub_external_engine_paths(bundle_root: Path, explicit_roots: List[str]) -> None:
    """Keep frozen private core isolated from accidental source-engine imports."""
    bundle_root = bundle_root.resolve()
    blocked: List[Path] = []
    for text in explicit_roots:
        if not text:
            continue
        try:
            blocked.append(Path(text).expanduser().resolve())
        except Exception:
            continue

    cleaned: List[str] = []
    for entry in sys.path:
        path = _resolve_sys_path_entry(entry)
        if path is None:
            continue
        if path == bundle_root:
            continue
        if _path_is_relative_to(path, bundle_root):
            cleaned.append(entry)
            continue
        if any(path == root or _path_is_relative_to(path, root) for root in blocked):
            continue
        if all((path / pkg).exists() for pkg in ENGINE_PACKAGES):
            continue
        cleaned.append(entry)

    sys.path[:] = [str(bundle_root), *cleaned]


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def dir_stats(path: Path) -> Dict[str, Any]:
    files = 0
    bytes_total = 0
    pkl_files: List[str] = []
    if path.is_dir():
        for item in path.rglob("*"):
            if not item.is_file():
                continue
            files += 1
            try:
                size = item.stat().st_size
            except OSError:
                size = 0
            bytes_total += size
            if item.suffix.lower() == ".pkl":
                pkl_files.append(str(item))
    return {"path": str(path), "files": files, "bytes": bytes_total, "pkl_files": pkl_files}


def cache_layout_summary(path: Path) -> Dict[str, Any]:
    path = path.expanduser()
    pkl_files = sorted(path.glob("*.pkl")) if path.is_dir() else []
    data_dirs = sorted(p for p in path.glob("*_data") if p.is_dir()) if path.is_dir() else []
    nmos = sorted(path.glob("nmos_*.pkl")) if path.is_dir() else []
    pmos = sorted(path.glob("pmos_*.pkl")) if path.is_dir() else []
    stats = dir_stats(path)
    return {
        **stats,
        "exists": path.is_dir(),
        "pkl_count": len(pkl_files),
        "nmos_pkl": [str(p) for p in nmos],
        "pmos_pkl": [str(p) for p in pmos],
        "data_dir_count": len(data_dirs),
        "data_dirs": [str(p) for p in data_dirs],
        "nmos_bytes": sum(p.stat().st_size for p in nmos if p.is_file()),
        "pmos_bytes": sum(p.stat().st_size for p in pmos if p.is_file()),
    }


def ratio_or_none(a: float, b: float) -> Optional[float]:
    if not b:
        return None
    return a / b


def append_event(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


DEFAULT_WRITEBACK_SETTINGS: Dict[str, Any] = {
    "nmos_terminal_order": "D G S B",
    "pmos_terminal_order": "D G S B",
    "width_mode": "auto",
    "geometry_decimals": "2",
    "width_aliases": "w,W,wr,width",
    "finger_width_aliases": "fw,wf,w_finger,W_finger,finger_width,fingerWidth,fingerW,widthPerFinger",
    "length_aliases": "l,L,lr,length",
    "finger_aliases": "nf,nfin,nFin,fingers,finger,ng",
    "multiplier_aliases": "m,mult,multiplier",
    "multiplier_value": "1",
    "passive_value_aliases": "value,r,res,resistance,c,cap,capacitance",
}


def merged_writeback_settings(settings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged = dict(DEFAULT_WRITEBACK_SETTINGS)
    if isinstance(settings, dict):
        for key, value in settings.items():
            if value not in (None, ""):
                merged[key] = value
    return merged


def split_aliases(value: Any, default: str = "") -> List[str]:
    text = str(value if value not in (None, "") else default)
    items = [x.strip() for x in re.split(r"[,;\s]+", text) if x.strip()]
    out: List[str] = []
    seen = set()
    for item in items:
        if item not in seen:
            out.append(item)
            seen.add(item)
    return out


def skill_quote(value: Any) -> str:
    text = str(value)
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def skill_string_list(items: List[str]) -> str:
    return "list(" + " ".join(skill_quote(x) for x in items) + ")"


def project_debug_summary(project: Dict[str, Any], project_path: Path, cmd: str) -> Dict[str, Any]:
    lib = project.get("library", {})
    cfg = project.get("config", {})
    specs = project.get("specs", {})
    public_specs = {
        (f"convergence_weight_{key[len('fitness_'):]}" if str(key).startswith("fitness_") else key): value
        for key, value in specs.items()
    }
    devices = []
    for d in project.get("devices", []):
        devices.append({
            "name": d.get("name", ""),
            "type": d.get("type", d.get("kind", "")),
            "model": d.get("model", ""),
            "nodes": d.get("nodes", []),
            "raw_nodes": d.get("raw_nodes", []),
            "terminal_order": d.get("terminal_order", ""),
            "current_A": d.get("current", d.get("Id", "")),
            "match_group": d.get("match_group", ""),
            "bw_factor": d.get("bw_factor", ""),
        })
    return {
        "cmd": cmd,
        "project_path": str(project_path),
        "project_dir": project.get("project_dir", ""),
        "engine_root": project.get("engine_root", ""),
        "netlist_path": project.get("netlist_path", ""),
        "telemetry_path": project.get("telemetry_path", ""),
        "result_path": project.get("result_path", ""),
        "skill_result_path": project.get("skill_result_path", ""),
        "cadence": project.get("cadence", {}),
        "library": {
            "cache_dir": lib.get("cache_dir", ""),
            "temp_dir": lib.get("temp_dir", ""),
            "model_path": lib.get("model_path", ""),
            "nmos_name": lib.get("nmos_name", ""),
            "pmos_name": lib.get("pmos_name", ""),
            "model_lib": lib.get("model_lib", ""),
            "temperature": lib.get("temperature", ""),
            "process_vdd": lib.get("process_vdd", ""),
            "simulator_backend": lib.get("simulator_backend", ""),
            "hspice_dir": lib.get("hspice_dir", ""),
            "hspice_cmd": lib.get("hspice_cmd", ""),
            "spectre_dir": lib.get("spectre_dir", ""),
            "spectre_cmd": lib.get("spectre_cmd", ""),
            "spectre_threads": lib.get("spectre_threads", ""),
            "spectre_accel": lib.get("spectre_accel", ""),
            "spectre_l_param": lib.get("spectre_l_param", ""),
            "spectre_w_param": lib.get("spectre_w_param", ""),
            "spectre_extra_params": lib.get("spectre_extra_params", ""),
            "L_min": lib.get("L_min", ""),
            "W_min": lib.get("W_min", ""),
            "L_grid": lib.get("L_grid", ""),
            "W_grid": lib.get("W_grid", ""),
            "W_finger_min": lib.get("W_finger_min", ""),
            "W_finger_max": lib.get("W_finger_max", ""),
            "L_list": lib.get("L_list", ""),
            "scan_width": lib.get("scan_width", ""),
            "vgs": [lib.get("vgs_start", ""), lib.get("vgs_stop", ""), lib.get("vgs_step", "")],
            "vds": [lib.get("vds_start", ""), lib.get("vds_stop", ""), lib.get("vds_step", "")],
            "vsb": [lib.get("vsb_start", ""), lib.get("vsb_stop", ""), lib.get("vsb_step", "")],
        },
        "specs": public_specs,
        "config": {
            "population_size": cfg.get("population_size", ""),
            "max_generations": cfg.get("max_generations", ""),
            "fast_mode": cfg.get("fast_mode", ""),
            "verbose": cfg.get("verbose", ""),
            "parallel": cfg.get("parallel", ""),
            "random_seed": cfg.get("random_seed", ""),
        },
        "settings": project.get("settings", {}),
        "device_count": len(devices),
        "devices": devices,
        "passives": project.get("passives", []),
        "platform": {
            "system": platform.platform(),
            "machine": platform.machine(),
            "python": sys.version.split()[0],
            "executable": sys.executable,
            "binary_platform_tag": binary_platform_tag(),
            "core_executable": str(find_core_executable() or ""),
        },
    }


def print_project_summary(project: Dict[str, Any], project_path: Path, cmd: str) -> None:
    summary = project_debug_summary(project, project_path, cmd)
    print("[AmpSys] Project summary begin")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print("[AmpSys] Project summary end")


def get_float(mapping: Dict[str, Any], key: str, default: float) -> float:
    try:
        val = mapping.get(key, default)
        if val in ("", None):
            return default
        return float(val)
    except Exception:
        return default


def get_int(mapping: Dict[str, Any], key: str, default: int) -> int:
    try:
        val = mapping.get(key, default)
        if val in ("", None):
            return default
        return int(float(val))
    except Exception:
        return default


def parse_float_list(value: Any) -> Optional[List[float]]:
    if value in (None, ""):
        return None
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            try:
                out.append(float(item))
            except Exception:
                pass
        return out or None
    text = str(value).replace(";", ",")
    out = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(float(part))
        except Exception:
            pass
    return out or None


def parse_range(mapping: Dict[str, Any], prefix: str, default: tuple) -> tuple:
    return (
        get_float(mapping, f"{prefix}_start", default[0]),
        get_float(mapping, f"{prefix}_stop", default[1]),
        get_float(mapping, f"{prefix}_step", default[2]),
    )


def model_stem(model_path: Any) -> str:
    text = str(model_path or "").strip().strip('"').strip("'")
    return Path(text.replace("\\", "/")).stem


def tag_float(value: Any) -> str:
    return str(value).replace(".", "p").replace("-", "m")


def cache_scan_tags_match_library(key: str, lib: Dict[str, Any]) -> bool:
    match = re.search(r"_t(?P<temp>[^_]+)_vsb(?P<vsb>.+)$", key)
    if not match:
        return True
    expected_temp = tag_float(get_float(lib, "temperature", 25.0))
    expected_vsb = "_".join(tag_float(x) for x in parse_range(lib, "vsb", (0.0, 0.0, 0.1)))
    return match.group("temp") == expected_temp and match.group("vsb") == expected_vsb


def ampflow_cache_keys(lib: Dict[str, Any]) -> List[str]:
    pdk_name = model_stem(lib.get("model_path"))
    if not pdk_name:
        return []
    nmos = str(lib.get("nmos_name") or "nmos").strip()
    pmos = str(lib.get("pmos_name") or "pmos").strip()
    model_lib = str(lib.get("model_lib") or "tt").strip()
    base = f"{pdk_name}_{nmos}_{pmos}_{model_lib}"
    temperature = get_float(lib, "temperature", 25.0)
    vsb = parse_range(lib, "vsb", (0.0, 0.0, 0.1))
    full = f"{base}_t{tag_float(temperature)}_vsb{'_'.join(tag_float(x) for x in vsb)}"
    keys = [full, base]
    return list(dict.fromkeys(keys))


def split_model_names(value: Any, default: str) -> List[str]:
    parts = [p.strip() for p in str(value or "").replace(";", ",").split(",") if p.strip()]
    return parts or [default]


def cache_key_matches_library(key: str, lib: Dict[str, Any]) -> bool:
    if key in ampflow_cache_keys(lib):
        return True
    if not cache_scan_tags_match_library(key, lib):
        return False
    model_lib = str(lib.get("model_lib") or "tt").strip()
    for nmos in split_model_names(lib.get("nmos_name"), "nmos"):
        for pmos in split_model_names(lib.get("pmos_name"), "pmos"):
            if model_lib:
                if f"_{nmos}_{pmos}_{model_lib}" in key:
                    return True
            elif f"_{nmos}_{pmos}_" in key:
                return True
    return False


def infer_model_stem_from_cache_key(key: str, lib: Dict[str, Any]) -> str:
    model_lib = str(lib.get("model_lib") or "tt").strip()
    for nmos in split_model_names(lib.get("nmos_name"), "nmos"):
        for pmos in split_model_names(lib.get("pmos_name"), "pmos"):
            token = f"_{nmos}_{pmos}_{model_lib}" if model_lib else f"_{nmos}_{pmos}_"
            idx = key.find(token)
            if idx > 0:
                return key[:idx]
    return key


def spectre_cache_dir_from(cache_dir: Any) -> str:
    text = str(cache_dir or "").strip()
    if not text:
        return text
    normalized = text.replace("\\", "/").rstrip("/")
    if normalized.endswith("/autoflow_cache"):
        return normalized + "_spectre"
    return text


def isolate_spectre_cache_dir(lib: Dict[str, Any]) -> bool:
    if normalize_simulator_backend(lib) != "spectre":
        return False
    isolated = spectre_cache_dir_from(lib.get("cache_dir"))
    if isolated and isolated != lib.get("cache_dir"):
        lib["cache_dir"] = isolated
        return True
    return False


def library_cache_dir(lib: Dict[str, Any], project_path: Path) -> Path:
    return Path(lib.get("cache_dir") or project_path.parent / "libraries").expanduser()


def backend_matches_library(lib: Dict[str, Any], saved_lib: Dict[str, Any]) -> bool:
    """Keep HSPICE and Spectre caches format-compatible but never silently mix them."""
    requested = normalize_simulator_backend(lib)
    saved = normalize_simulator_backend(saved_lib) if isinstance(saved_lib, dict) else "auto"
    if requested == "auto" or saved == "auto":
        return True
    return requested == saved


def manifest_backend_matches(manifest: Path, lib: Dict[str, Any]) -> bool:
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8-sig"))
        saved_lib = payload.get("library") or {}
    except Exception:
        return True
    return backend_matches_library(lib, saved_lib)


def raw_cache_pair_allowed_without_manifest(lib: Dict[str, Any], cache_dir: Path) -> bool:
    """Allow legacy raw .pkl pairs, but avoid silent HSPICE/Spectre cache mixups."""
    if normalize_simulator_backend(lib) != "spectre":
        return True
    normalized = str(cache_dir).replace("\\", "/").rstrip("/").lower()
    return normalized.endswith("/autoflow_cache_spectre") or normalized.endswith("_spectre") or "/spectre" in normalized


def ampflow_cache_pairs(lib: Dict[str, Any], cache_dir: Path) -> List[tuple]:
    pairs = []
    for key in ampflow_cache_keys(lib):
        pairs.append((cache_dir / f"nmos_{key}.pkl", cache_dir / f"pmos_{key}.pkl"))
    return pairs


def find_ampflow_cache_pair(lib: Dict[str, Any], cache_dir: Path) -> Optional[tuple]:
    for nmos_pkl, pmos_pkl in ampflow_cache_pairs(lib, cache_dir):
        if nmos_pkl.is_file() and pmos_pkl.is_file():
            return nmos_pkl, pmos_pkl
    if cache_dir.is_dir():
        for nmos_pkl in sorted(cache_dir.glob("nmos_*.pkl")):
            key = nmos_pkl.name[len("nmos_"):-len(".pkl")]
            if not cache_key_matches_library(key, lib):
                continue
            pmos_pkl = cache_dir / f"pmos_{key}.pkl"
            if pmos_pkl.is_file():
                return nmos_pkl, pmos_pkl
    return None


def library_ready_marker(lib: Dict[str, Any], project_path: Path) -> Optional[Path]:
    cache_dir = library_cache_dir(lib, project_path)
    manifest = cache_dir / "manifest.json"
    if manifest.is_file() and manifest_backend_matches(manifest, lib):
        return manifest
    pair = find_ampflow_cache_pair(lib, cache_dir)
    if pair and raw_cache_pair_allowed_without_manifest(lib, cache_dir):
        return pair[0]
    child = cache_dir / "autoflow_cache"
    if child.is_dir():
        child_manifest = child / "manifest.json"
        if child_manifest.is_file() and manifest_backend_matches(child_manifest, lib):
            return child_manifest
        child_pair = find_ampflow_cache_pair(lib, child)
        if child_pair and raw_cache_pair_allowed_without_manifest(lib, child):
            return child_pair[0]
    return None


def expected_library_markers(lib: Dict[str, Any], project_path: Path) -> List[Path]:
    cache_dir = library_cache_dir(lib, project_path)
    markers = [cache_dir / "manifest.json"]
    pairs = ampflow_cache_pairs(lib, cache_dir)
    if pairs:
        for nmos_pkl, pmos_pkl in pairs:
            markers.extend([nmos_pkl, pmos_pkl])
    else:
        markers.extend([cache_dir / "nmos_*.pkl", cache_dir / "pmos_*.pkl"])
    child = cache_dir / "autoflow_cache"
    markers.append(child / "manifest.json")
    child_pairs = ampflow_cache_pairs(lib, child)
    if child_pairs:
        for nmos_pkl, pmos_pkl in child_pairs:
            markers.extend([nmos_pkl, pmos_pkl])
    else:
        markers.extend([child / "nmos_*.pkl", child / "pmos_*.pkl"])
    return markers


def prepare_existing_library_cache(lib: Dict[str, Any], project_path: Path) -> Optional[Path]:
    cache_dir = library_cache_dir(lib, project_path)
    direct_pair = find_ampflow_cache_pair(lib, cache_dir)
    child = cache_dir / "autoflow_cache"
    child_pair = find_ampflow_cache_pair(lib, child) if child.is_dir() else None

    if direct_pair and not raw_cache_pair_allowed_without_manifest(lib, cache_dir):
        direct_pair = None
    if child_pair and not raw_cache_pair_allowed_without_manifest(lib, child):
        child_pair = None

    if not direct_pair and child_pair:
        cache_dir = child
        lib["cache_dir"] = str(child)
        direct_pair = child_pair

    manifest = cache_dir / "manifest.json"
    if manifest.is_file() and manifest_backend_matches(manifest, lib):
        if not model_stem(lib.get("model_path")):
            if direct_pair:
                key = direct_pair[0].name[len("nmos_"):-len(".pkl")]
                stem = infer_model_stem_from_cache_key(key, lib)
                if stem:
                    lib["model_path"] = str(cache_dir / f"{stem}.lib")
            else:
                try:
                    manifest_payload = json.loads(manifest.read_text(encoding="utf-8-sig"))
                    saved_model = (manifest_payload.get("library") or {}).get("model_path")
                    if saved_model:
                        lib["model_path"] = saved_model
                except Exception:
                    pass
        return manifest
    if not direct_pair:
        return None

    if not model_stem(lib.get("model_path")):
        key = direct_pair[0].name[len("nmos_"):-len(".pkl")]
        stem = infer_model_stem_from_cache_key(key, lib)
        if stem:
            lib["model_path"] = str(cache_dir / f"{stem}.lib")

    def copy_pair(src_pair: tuple, dst_pair: tuple) -> None:
        import shutil
        for src, dst in zip(src_pair, dst_pair):
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.resolve() != dst.resolve():
                shutil.copy2(src, dst)
            src_data = src.with_name(src.stem + "_data")
            if src_data.is_dir():
                dst_data = dst.parent / src_data.name
                if src_data.resolve() != dst_data.resolve():
                    shutil.copytree(src_data, dst_data, dirs_exist_ok=True)

    pairs = ampflow_cache_pairs(lib, cache_dir)
    if pairs:
        preferred_nmos, preferred_pmos = pairs[0]
        found_nmos, found_pmos = direct_pair
        try:
            copy_pair((found_nmos, found_pmos), (preferred_nmos, preferred_pmos))
            direct_pair = (preferred_nmos, preferred_pmos)
        except OSError:
            staging_dir = project_path.parent / "libraries"
            lib["cache_dir"] = str(staging_dir)
            staged_pairs = ampflow_cache_pairs(lib, staging_dir)
            if staged_pairs:
                copy_pair((found_nmos, found_pmos), staged_pairs[0])
                cache_dir = staging_dir
                direct_pair = staged_pairs[0]

    manifest_payload = {
        "status": "ready",
        "library": lib,
        "cache_marker": str(direct_pair[0]),
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        write_json(manifest, manifest_payload)
        return manifest
    except OSError:
        return direct_pair[0]


def resolve_hspice_cmd(lib: Dict[str, Any]) -> str:
    hspice_dir = str(lib.get("hspice_dir") or "").strip().strip('"').strip("'")
    legacy_cmd = str(lib.get("hspice_cmd") or "hspice -mt 2").strip()
    if not hspice_dir:
        return legacy_cmd or "hspice -mt 2"

    path = Path(hspice_dir).expanduser()
    if path.is_file():
        return str(path)

    if not path.exists():
        if "/" not in hspice_dir and "\\" not in hspice_dir:
            return hspice_dir
        raise FileNotFoundError(f"HSPICE dir was not found: {path}")

    names = ("hspice.exe", "hspice.bat", "hspice.cmd") if sys.platform.startswith("win") else ("hspice",)
    subdirs = ("", "WIN64", "win64", "bin", "BIN")
    candidates: List[Path] = []
    for subdir in subdirs:
        base = path / subdir if subdir else path
        for name in names:
            candidates.append(base / name)

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    tried = ", ".join(str(c) for c in candidates[:8])
    raise FileNotFoundError(f"HSPICE executable was not found under {path}. Tried: {tried}")


def _path_text(value: Any) -> str:
    return str(value or "").strip().strip('"').strip("'")


def _looks_like_spectre_model(path: Any) -> bool:
    text = _path_text(path).replace("\\", "/").lower()
    return text.endswith(".scs") or "/spectre/" in text or text.endswith("_spe.lib") or "_spe." in text


def _looks_like_hspice_model(path: Any) -> bool:
    text = _path_text(path).replace("\\", "/").lower()
    return "/hspice/" in text or (text.endswith(".lib") and not _looks_like_spectre_model(text))


def normalize_simulator_backend(lib: Dict[str, Any]) -> str:
    raw = str(lib.get("simulator_backend") or lib.get("simulator") or "auto").strip().lower()
    aliases = {
        "": "auto",
        "auto": "auto",
        "hspice": "hspice",
        "hspice-only": "hspice",
        "spectre": "spectre",
        "cadence spectre": "spectre",
    }
    mode = aliases.get(raw, raw)
    if mode not in {"auto", "hspice", "spectre"}:
        raise ValueError(f"Unknown simulator backend: {raw}")
    if mode != "auto":
        return mode
    model = str(lib.get("model_path") or "").replace("\\", "/").lower()
    if _looks_like_spectre_model(model):
        return "spectre"
    if _looks_like_hspice_model(model):
        return "hspice"
    if str(lib.get("hspice_dir") or "").strip():
        return "hspice"
    if str(lib.get("spectre_dir") or "").strip() or os.environ.get("SPECTRE_CMD") or os.environ.get("SPECTRE"):
        return "spectre"
    if sys.platform.startswith("linux"):
        return "spectre"
    return "hspice"


def _spectre_candidate_paths(root: Path) -> List[Path]:
    names = ("spectre.exe", "spectre.bat", "spectre.cmd") if sys.platform.startswith("win") else ("spectre",)
    subdirs = (
        "",
        "tools.lnx86/spectre/bin/64bit",
        "spectre/bin/64bit",
        "bin",
        "BIN",
        "tools/bin",
        "tools.lnx86/bin",
        "tools.lnx86/spectre/bin",
        "spectre/bin",
    )
    out: List[Path] = []
    for sub in subdirs:
        base = root / sub if sub else root
        for name in names:
            out.append(base / name)
    for pattern in (
        "*/bin/spectre",
        "*/*/bin/spectre",
        "*/tools.lnx86/bin/spectre",
        "*/tools.lnx86/spectre/bin/64bit/spectre",
        "*/tools/bin/spectre",
    ):
        try:
            out.extend(root.glob(pattern))
        except OSError:
            pass
    return out


def _split_cmd_args(text: str) -> List[str]:
    if not text:
        return []
    try:
        parts = shlex.split(text, posix=(os.name != "nt"))
    except Exception:
        parts = text.split()
    if os.name == "nt":
        parts = _repair_windows_cmd_parts(parts)
    return parts


def _repair_windows_cmd_parts(parts: List[str]) -> List[str]:
    if not parts or parts[0].startswith(("+", "-")):
        return parts
    cleaned = [str(p).strip().strip('"').strip("'") for p in parts]
    for idx in range(len(cleaned) - 1, -1, -1):
        candidate = " ".join(cleaned[: idx + 1]).strip()
        name = Path(candidate).name.lower()
        if name not in {"spectre.exe", "spectre.bat", "spectre.cmd", "spectre"}:
            continue
        try:
            if Path(candidate).expanduser().is_file():
                return [candidate, *cleaned[idx + 1:]]
        except OSError:
            continue
    return cleaned


def _quote_cmd_part(text: str) -> str:
    if not text:
        return text
    if any(ch.isspace() for ch in text) or any(ch in text for ch in ('"', "'")):
        return '"' + text.replace('"', '\\"') + '"'
    return text


def _spectre_extra_args(explicit_cmd: str) -> List[str]:
    parts = _split_cmd_args(explicit_cmd)
    if not parts:
        return []
    first = Path(parts[0].strip('"').strip("'")).name.lower()
    if first.startswith("spectre") or first.endswith("spectre.exe"):
        return parts[1:]
    if parts[0].startswith("+") or parts[0].startswith("-"):
        return parts
    return parts[1:]


def _cpu_count_for_spectre() -> int:
    """Best-effort CPU count that works on desktops, WSL, servers and schedulers."""
    env_names = (
        "AMPSYS_CPU_COUNT",
        "SLURM_CPUS_PER_TASK",
        "SLURM_JOB_CPUS_PER_NODE",
        "PBS_NP",
        "LSB_DJOB_NUMPROC",
        "NUMBER_OF_PROCESSORS",
    )
    for name in env_names:
        text = str(os.environ.get(name, "")).strip()
        if not text:
            continue
        match = re.search(r"\d+", text)
        if match:
            value = int(match.group(0))
            if value > 0:
                return value
    return max(1, os.cpu_count() or 1)


def auto_spectre_threads(lib: Optional[Dict[str, Any]] = None) -> int:
    """Choose Spectre threads per process without oversubscribing NMOS/PMOS scans."""
    lib = lib or {}
    raw = str(lib.get("spectre_threads") or os.environ.get("AMPSYS_SPECTRE_THREADS") or "auto").strip().lower()
    if raw in {"", "auto", "default"}:
        cpu = _cpu_count_for_spectre()
        try:
            max_threads = max(1, int(float(os.environ.get("AMPSYS_SPECTRE_MAX_THREADS_PER_PROC", "8") or "8")))
        except Exception:
            max_threads = 8
        # AmpFlow builds NMOS and PMOS in parallel, so each Spectre process should
        # get about half of the useful cores. Leave one core free on larger boxes.
        usable = max(1, cpu - 1) if cpu >= 6 else cpu
        return max(1, min(max_threads, usable // 2 if usable >= 4 else usable))
    if raw in {"off", "false", "none", "0", "1x"}:
        return 1
    match = re.search(r"\d+", raw)
    if match:
        return max(1, int(match.group(0)))
    return 1


def _spectre_args_have_threads(args: List[str]) -> bool:
    for idx, arg in enumerate(args):
        low = arg.lower()
        if low.startswith("+mt") or low.startswith("-mt") or low.startswith("++mt"):
            return True
        if low in {"mt", "nthreads", "threads", "+multithread", "-multithread"}:
            return True
        if "nthreads=" in low:
            return True
        if low in {"-maxw", "+maxw"} and idx + 1 < len(args):
            return True
    return False


def apply_auto_spectre_threads(cmd_parts: List[str], lib: Optional[Dict[str, Any]] = None) -> List[str]:
    if _spectre_args_have_threads(cmd_parts):
        return cmd_parts
    accel_raw = str((lib or {}).get("spectre_accel") or os.environ.get("AMPSYS_SPECTRE_ACCEL") or "auto").strip().lower()
    if accel_raw in {"off", "none", "false", "0", "baseline", "spectre"}:
        return cmd_parts
    threads = auto_spectre_threads(lib)
    if threads <= 1:
        return cmd_parts
    return [*cmd_parts, f"+mt={threads}"]


def spectre_accel_label(lib: Optional[Dict[str, Any]] = None) -> str:
    lib = lib or {}
    raw = str(lib.get("spectre_accel") or os.environ.get("AMPSYS_SPECTRE_ACCEL") or "auto").strip()
    if not raw or raw.lower() in {"auto", "default"}:
        return "auto (+aps, fallback to baseline)"
    if raw.lower() in {"off", "none", "false", "0", "baseline", "spectre"}:
        return "off (baseline Spectre)"
    return raw


def _is_plain_spectre_aps(value: str) -> bool:
    low = str(value or "").strip().lower()
    return low in {"+aps", "++aps", "aps", "ppaps", "aps++"}


def validate_spectre_accel(lib: Optional[Dict[str, Any]] = None) -> None:
    lib = lib or {}
    accel = str(lib.get("spectre_accel") or os.environ.get("AMPSYS_SPECTRE_ACCEL") or "auto").strip().lower()
    if accel not in ALLOWED_SPECTRE_ACCEL:
        raise ValueError("Spectre accel must be auto, ++aps, +aps, or off. APS preset modes and +xps are not allowed for LUT builds.")
    cmd_sources = [
        str(lib.get("spectre_cmd") or "").strip(),
        str(os.environ.get("SPECTRE_CMD") or "").strip(),
        str(os.environ.get("SPECTRE") or "").strip(),
    ]
    for cmd in cmd_sources:
        if not cmd:
            continue
        _validate_spectre_cmd_args(_split_cmd_args(cmd))


def _validate_spectre_cmd_args(parts: List[str]) -> None:
    for part in parts:
        low = part.lower()
        if low.startswith("+xps") or low.startswith("++xps"):
            raise ValueError("Spectre +xps is not supported by the AmpSys LUT flow. Remove +xps from Spectre cmd.")
        if (low.startswith("+aps") or low.startswith("++aps")) and not _is_plain_spectre_aps(low):
            raise ValueError("Only plain +aps/++aps are allowed for LUT builds. Remove APS preset modes from Spectre cmd.")


def apply_spectre_env_settings(env: Dict[str, str], lib: Dict[str, Any]) -> None:
    env["AMPSYS_SPECTRE_THREADS"] = str(lib.get("spectre_threads") or "auto")
    env["AMPSYS_SPECTRE_ACCEL"] = str(lib.get("spectre_accel") or "auto")
    env.setdefault("AMPSYS_SPECTRE_MAXNOTES", "1")
    optional_env = {
        "spectre_batch_points": "AMPSYS_SPECTRE_BATCH_POINTS",
        "spectre_batch_workers": "AMPSYS_SPECTRE_BATCH_WORKERS",
        "spectre_device_workers": "AMPSYS_SPECTRE_DEVICE_WORKERS",
        "spectre_scratch": "AMPSYS_SPECTRE_SCRATCH",
        "spectre_max_combos_per_batch": "AMPSYS_SPECTRE_MAX_COMBOS_PER_BATCH",
    }
    for key, env_name in optional_env.items():
        value = str(lib.get(key) or "").strip()
        if value and value.lower() not in {"auto", "default"}:
            env[env_name] = value


def _spectre_env_cmd_parts() -> List[str]:
    for env_name in ("SPECTRE_CMD", "SPECTRE"):
        value = os.environ.get(env_name, "").strip()
        if value:
            return _split_cmd_args(value)
    return []


def _is_spectre_program_name(text: str) -> bool:
    name = Path(text.strip().strip('"').strip("'")).name.lower()
    return name in {"spectre", "spectre.exe", "spectre.bat", "spectre.cmd"}


def _resolve_spectre_head(head: str) -> Optional[str]:
    text = str(head or "").strip().strip('"').strip("'")
    if not text:
        return None
    path = Path(os.path.expandvars(text)).expanduser()
    if path.is_file():
        return str(path)
    if "/" not in text and "\\" not in text and _is_spectre_program_name(text):
        candidates = find_spectre_candidates({"spectre_cmd": ""}, max_results=1)
        if candidates:
            return candidates[0]
    if "/" not in text and "\\" not in text:
        found = shutil.which(text)
        if found:
            return found
    return None


def find_spectre_candidates(lib: Optional[Dict[str, Any]] = None, max_results: int = 12) -> List[str]:
    """Find likely Spectre executables without guessing any PDK/model file."""
    lib = lib or {}
    candidates: List[Path] = []

    def add_path(value: Any) -> None:
        text = str(value or "").strip().strip('"').strip("'")
        if not text:
            return
        path = Path(text).expanduser()
        if path.is_file():
            candidates.append(path)
        elif path.is_dir():
            candidates.extend(_spectre_candidate_paths(path))
        elif "/" not in text and "\\" not in text:
            found = shutil.which(text)
            if found:
                candidates.append(Path(found))

    add_path(lib.get("spectre_dir"))
    add_path(lib.get("spectre_cmd") or "spectre")
    for env_name in ("SPECTRE_CMD", "SPECTRE"):
        add_path(os.environ.get(env_name, ""))
    found = shutil.which("spectre")
    if found:
        candidates.append(Path(found))

    roots: List[Path] = []
    for env_name in ("MMSIMHOME", "SPECTRE_HOME", "CDS_ROOT", "CDSHOME", "CDS_INST_DIR", "CADENCE_HOME"):
        value = os.environ.get(env_name, "").strip()
        if value:
            roots.append(Path(value).expanduser())
    if sys.platform.startswith("linux"):
        roots.extend(Path(p) for p in (
            "/opt/cadence",
            "/cadence",
            "/tools/cadence",
            "/tools/eda",
            "/eda/cadence",
            "/apps/cadence",
            "/usr/local/cadence",
        ))
        for pattern in ("/home/*/opt/cadence/*", "/home/*/cadence/*", "/home/*/eda/cadence/*"):
            roots.extend(Path(p) for p in Path("/").glob(pattern.lstrip("/")))
    elif sys.platform.startswith("win"):
        roots.extend(Path(p) for p in (
            r"C:\Cadence",
            r"C:\Program Files\Cadence",
            r"D:\Cadence",
        ))

    for root in roots:
        if root.exists():
            candidates.extend(_spectre_candidate_paths(root))

    unique: List[Path] = []
    seen = set()
    for cand in candidates:
        try:
            resolved = cand.resolve()
        except Exception:
            resolved = cand
        if not cand.is_file() and not resolved.is_file():
            continue
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(cand)

    def score(path: Path) -> tuple:
        text = str(path).replace("\\", "/").lower()
        s = 0
        s -= 60 if "/spectre/bin/64bit/spectre" in text else 0
        s -= 30 if "/mmsim" in text else 0
        s -= 20 if "/bin/spectre" in text or text.endswith("/spectre") else 0
        s -= 10 if "/opt/cadence" in text else 0
        return (s, len(text), text)

    return [str(p) for p in sorted(unique, key=score)[:max_results]]


def resolve_spectre_cmd(lib: Dict[str, Any]) -> str:
    explicit_dir = str(lib.get("spectre_dir") or "").strip().strip('"').strip("'")
    explicit_cmd = str(lib.get("spectre_cmd") or "").strip()
    extra_args = _spectre_extra_args(explicit_cmd)
    cmd_parts = _split_cmd_args(explicit_cmd)
    cmd_head = cmd_parts[0] if cmd_parts else ""
    if cmd_head:
        cmd_path = Path(cmd_head.strip('"').strip("'")).expanduser()
        try:
            if cmd_path.is_file():
                return " ".join(apply_auto_spectre_threads([_quote_cmd_part(str(cmd_path)), *cmd_parts[1:]], lib)).strip()
        except OSError:
            pass

    if explicit_dir:
        path = Path(explicit_dir).expanduser()
        if path.is_file():
            exe = str(path)
            return " ".join(apply_auto_spectre_threads([_quote_cmd_part(exe), *extra_args], lib)).strip()
        if not path.exists():
            if "/" not in explicit_dir and "\\" not in explicit_dir:
                return " ".join(apply_auto_spectre_threads([explicit_dir, *extra_args], lib)).strip()
            raise FileNotFoundError(f"Spectre dir was not found: {path}")
        for candidate in _spectre_candidate_paths(path):
            if candidate.is_file():
                return " ".join(apply_auto_spectre_threads([_quote_cmd_part(str(candidate)), *extra_args], lib)).strip()
        tried = ", ".join(str(c) for c in _spectre_candidate_paths(path)[:10])
        raise FileNotFoundError(f"Spectre executable was not found under {path}. Tried: {tried}")

    if explicit_cmd:
        cmd_head = cmd_parts[0] if cmd_parts else explicit_cmd
        if cmd_head.startswith(("+", "-")):
            env_parts = _spectre_env_cmd_parts()
            base = _resolve_spectre_head(env_parts[0]) if env_parts else (_resolve_spectre_head("spectre") or "spectre")
            return " ".join(apply_auto_spectre_threads([_quote_cmd_part(base), *cmd_parts], lib)).strip()

        resolved_head = _resolve_spectre_head(cmd_head)
        if resolved_head:
            return " ".join(apply_auto_spectre_threads([_quote_cmd_part(resolved_head), *cmd_parts[1:]], lib)).strip()

        if _is_spectre_program_name(cmd_head):
            env_parts = _spectre_env_cmd_parts()
            if env_parts:
                env_head = _resolve_spectre_head(env_parts[0]) or env_parts[0]
                args = env_parts[1:] if cmd_parts == [cmd_head] else cmd_parts[1:]
                return " ".join(apply_auto_spectre_threads([_quote_cmd_part(env_head), *args], lib)).strip()
        else:
            if "/" not in cmd_head and "\\" not in cmd_head:
                return " ".join(apply_auto_spectre_threads(cmd_parts, lib)).strip()
            raise FileNotFoundError(f"Spectre executable was not found: {cmd_head}")

    env_parts = _spectre_env_cmd_parts()
    if env_parts:
        env_head = _resolve_spectre_head(env_parts[0]) or env_parts[0]
        return " ".join(apply_auto_spectre_threads([_quote_cmd_part(env_head), *env_parts[1:]], lib)).strip()

    found = _resolve_spectre_head("spectre")
    if found:
        return " ".join(apply_auto_spectre_threads([_quote_cmd_part(found)], lib)).strip()

    raise FileNotFoundError(
        "Spectre executable was not found. Put spectre on PATH, set SPECTRE_CMD, "
        "or fill Spectre dir with the directory that contains the spectre executable."
    )

def create_flow(project: Dict[str, Any]):
    engine_root = add_engine_path(project.get("engine_root", ""))
    from AmpSys import AmpFlow, AmpFlowConfig
    import inspect

    lib = project["library"]
    run_cfg = project.get("config", {})
    cache_dir = lib.get("cache_dir") or str(Path(project.get("project_dir", ".")).resolve() / "libraries")
    temp_dir = lib.get("temp_dir") or str(Path(project.get("project_dir", ".")).resolve() / "workspace" / "tmp")
    simulator_backend = normalize_simulator_backend(lib)
    if simulator_backend == "spectre" and isolate_spectre_cache_dir(lib):
        cache_dir = str(lib.get("cache_dir") or cache_dir)
    if simulator_backend == "spectre":
        validate_spectre_accel(lib)
        apply_spectre_env_settings(os.environ, lib)

    from_pdk_kwargs = {
        "model_path": lib.get("model_path", ""),
        "cellname_nmos": lib["nmos_name"],
        "cellname_pmos": lib["pmos_name"],
        "model_lib": lib.get("model_lib", "tt"),
        "L_list": parse_float_list(lib.get("L_list")) or generate_l_grid(get_float(lib, "L_min", 0.18e-6) or 0.18e-6, 25.0 * (get_float(lib, "L_min", 0.18e-6) or 0.18e-6)),
        "L_min": get_float(lib, "L_min", 0.18e-6),
        "W_min": get_float(lib, "W_min", get_float(lib, "L_min", 0.18e-6)),
        "W_grid": get_float(lib, "W_grid", max((get_float(lib, "L_min", 0.18e-6) or 0.18e-6) / 10.0, 0.001e-6)),
        "L_grid": get_float(lib, "L_grid", max((get_float(lib, "L_min", 0.18e-6) or 0.18e-6) / 10.0, 0.001e-6)),
        "W_finger_max": get_float(lib, "W_finger_max", 10e-6),
        "W_finger_min": get_float(lib, "W_finger_min", get_float(lib, "L_min", 0.18e-6)),
        "VGS_range": parse_range(lib, "vgs", (0.0, get_float(lib, "process_vdd", 1.8), 0.002)),
        "VDS_range": parse_range(lib, "vds", (0.05, get_float(lib, "process_vdd", 1.8), 0.02)),
        "VSB_range": parse_range(lib, "vsb", (0.0, 0.0, 0.1)),
        "scan_width": get_float(lib, "scan_width", 10e-6),
        "temperature": get_float(lib, "temperature", 25.0),
        "use_batch_mode": bool(lib.get("use_batch_mode", True)),
        "batch_size": get_int(lib, "batch_size", 20),
        "batch_timeout_ms": get_int(lib, "batch_timeout_ms", 50),
        "process_vdd": get_float(lib, "process_vdd", 1.8),
        "simulator_backend": simulator_backend,
        "spectre_cmd": resolve_spectre_cmd(lib) if simulator_backend == "spectre" else str(lib.get("spectre_cmd") or "spectre"),
        "spectre_l_param": str(lib.get("spectre_l_param") or "l"),
        "spectre_w_param": str(lib.get("spectre_w_param") or "w"),
        "spectre_extra_params": str(lib.get("spectre_extra_params") or ""),
        "hspice_cmd": resolve_hspice_cmd(lib) if simulator_backend == "hspice" else str(lib.get("hspice_cmd") or "hspice -mt 2"),
        "cache_dir": cache_dir,
        "temp_dir": temp_dir,
        "force_rescan": bool(lib.get("force_rescan", False)),
        "verbose": bool(run_cfg.get("verbose", False)),
    }
    supported = set(inspect.signature(AmpFlow.from_pdk).parameters)
    flow = AmpFlow.from_pdk(**{k: v for k, v in from_pdk_kwargs.items() if k in supported})
    flow_config_kwargs = dict(
        population_size=get_int(run_cfg, "population_size", 50),
        max_generations=get_int(run_cfg, "max_generations", 20),
        verbose=bool(run_cfg.get("verbose", False)),
        parallel=bool(run_cfg.get("parallel", True)),
        print_details=bool(run_cfg.get("print_details", False)),
        enable_kvl_check=bool(run_cfg.get("enable_kvl_check", True)),
        fast_mode=bool(run_cfg.get("fast_mode", True)),
        debug_log=bool(run_cfg.get("debug_log", False)),
        random_seed=get_int(run_cfg, "random_seed", 42),
        monitor=bool(run_cfg.get("monitor", False)),
        monitor_interval=get_float(run_cfg, "monitor_interval", 5.0),
    )
    config_supported = set(inspect.signature(AmpFlowConfig).parameters)
    flow.config = AmpFlowConfig(**{k: v for k, v in flow_config_kwargs.items() if k in config_supported})
    return flow, engine_root


def ensure_library_ready(project: Dict[str, Any], project_path: Path) -> None:
    prepare_existing_library_cache(project.setdefault("library", {}), project_path)
    marker = library_ready_marker(project.get("library", {}), project_path)
    if marker is None:
        expected = "\n".join(str(p) for p in expected_library_markers(project.get("library", {}), project_path)[:8])
        raise FileNotFoundError(
            "LUT library was not found. "
            "Run `build-library` first, or set library.cache_dir to an existing AmpFlow cache directory. "
            f"Expected one of:\n{expected}"
        )


def make_intents(project: Dict[str, Any]):
    from AmpSys import MosIntent, PassiveIntent

    mos = []
    passives = []
    mos_name_map: Dict[str, str] = {}
    used_mos_names: set = set()

    def internal_mos_name(original: str) -> str:
        raw = str(original or "").strip()
        safe = re.sub(r"[^A-Za-z0-9_]", "_", raw) or "MOS"
        if safe[:1].upper() != "M":
            safe = f"M_{safe}"
        candidate = safe
        idx = 2
        while candidate in used_mos_names:
            candidate = f"{safe}_{idx}"
            idx += 1
        used_mos_names.add(candidate)
        mos_name_map[candidate] = raw
        return candidate

    for d in project.get("devices", []):
        dtype = d.get("type") or d.get("kind")
        if dtype in ("nmos", "pmos"):
            current = get_float(d, "current", 0.0)
            if current <= 0:
                current = get_float(d, "Id", 0.0)
            if current <= 0:
                raise ValueError(f"{d.get('name')}: current must be specified before run.")
            original_name = d["name"]
            amp_name = internal_mos_name(original_name)
            mos.append(
                MosIntent(
                    name=amp_name,
                    mos_type=dtype,
                    nodes=list(d["nodes"][:4]),
                    Id=current,
                    match_group=d.get("match_group") or None,
                    gmid=d.get("gmid") or None,
                    L=d.get("L") or None,
                    vds_estimate=d.get("vds_estimate") or None,
                    bw_factor=get_float(d, "bw_factor", 1.0),
                )
            )
        elif dtype in ("res", "cap"):
            passives.append(
                PassiveIntent(
                    name=d["name"],
                    ptype=dtype,
                    nodes=list(d["nodes"][:2]),
                    value=d.get("value"),
                )
            )
    for p in project.get("passives", []):
        dtype = p.get("type") or p.get("ptype")
        if dtype in ("res", "cap"):
            passives.append(
                PassiveIntent(
                    name=p["name"],
                    ptype=dtype,
                    nodes=list(p["nodes"][:2]),
                    value=p.get("value"),
                )
            )
    return mos, passives, mos_name_map


def individual_fitness(ind) -> float:
    try:
        return float(getattr(ind, "fitness", 0.0) or 0.0)
    except Exception:
        return 0.0


def metrics_from_individual(ind) -> Dict[str, Any]:
    ac = getattr(ind, "ac_result", None)
    sized = getattr(ind, "sized_data", None)
    out: Dict[str, Any] = {}
    if ac:
        out.update(
            gain=float(getattr(ac, "dc_gain", 0.0) or 0.0),
            gbw=float(getattr(ac, "gbw", 0.0) or 0.0),
            pm=float(getattr(ac, "pm", 0.0) or 0.0),
            noise=float(getattr(ac, "input_referred_noise", 0.0) or 0.0),
            cmrr=float(getattr(ac, "cmrr", 0.0) or 0.0),
            psrr=float(getattr(ac, "psrr", 0.0) or 0.0),
        )
    if sized:
        total_area_um2 = sum(
            float(getattr(mos, "W", 0.0) or 0.0) * float(getattr(mos, "L", 0.0) or 0.0)
            for mos in getattr(sized, "transistors", {}).values()
        ) * 1e12
        out.update(
            power=float(getattr(sized, "total_power", 0.0) or 0.0),
            current=float(getattr(sized, "total_current", 0.0) or 0.0),
            area_um2=total_area_um2,
        )
    return out


def build_generation_event(gen: int, optimizer: Any, best_convergence: Optional[float] = None) -> Dict[str, Any]:
    population = list(getattr(optimizer, "population", []) or [])
    population.sort(key=individual_fitness, reverse=True)
    fitness_values = [individual_fitness(ind) for ind in population]
    f_min = min(fitness_values) if fitness_values else 0.0
    f_max = max(fitness_values) if fitness_values else 1.0
    f_span = f_max - f_min
    points: List[Dict[str, Any]] = []
    for rank, ind in enumerate(population[:120]):
        point = metrics_from_individual(ind)
        if f_span > 0:
            point["convergence"] = (individual_fitness(ind) - f_min) / f_span
        else:
            point["convergence"] = 1.0 - (rank / max(1, min(len(population), 120) - 1))
        points.append(point)
    best_ind = getattr(optimizer, "best_individual", None)
    best = metrics_from_individual(best_ind) if best_ind is not None else (points[0] if points else {})
    max_generations = int(getattr(getattr(optimizer, "config", None), "max_generations", 0) or getattr(optimizer, "max_generations", 0) or 0)
    generation = int(gen) + 1
    if best_convergence is not None:
        best["convergence"] = max(0.0, min(1.0, float(best_convergence)))
    elif points:
        best["convergence"] = max(float(point.get("convergence") or 0.0) for point in points)
    else:
        best["convergence"] = 0.0
    stats = {}
    return {
        "phase": "optimize",
        "status": "generation",
        "generation": generation,
        "max_generations": max_generations,
        "progress": generation / max(1, max_generations),
        "best": best,
        "stats": stats,
        "points": points,
        "population_size": len(population),
        "time": time.time(),
    }


def install_optimizer_telemetry(telemetry: Path) -> None:
    try:
        from yami.optimizer import GeneticOptimizer
    except Exception as exc:
        print(f"[AmpSys] WARNING: live telemetry disabled: {exc}", file=sys.stderr)
        return

    if getattr(GeneticOptimizer, "_ampsys_telemetry_installed", False):
        GeneticOptimizer._ampsys_telemetry_path = telemetry
        return

    original_optimize = GeneticOptimizer.optimize

    def optimize_with_telemetry(self, initial_population=None, callback=None):
        telemetry_path = getattr(GeneticOptimizer, "_ampsys_telemetry_path", telemetry)
        public_state = {
            "baseline_best": None,
            "best_seen": None,
            "scale": 1e-9,
            "public": 0.0,
        }

        def public_convergence(optimizer: Any) -> float:
            population = list(getattr(optimizer, "population", []) or [])
            fitness_values = [individual_fitness(ind) for ind in population]
            best_ind = getattr(optimizer, "best_individual", None)
            if best_ind is not None:
                fitness_values.append(individual_fitness(best_ind))
            if not fitness_values:
                return float(public_state["public"])
            current_best = max(fitness_values)
            if public_state["baseline_best"] is None:
                public_state["baseline_best"] = current_best
                public_state["best_seen"] = current_best
            public_state["best_seen"] = max(float(public_state["best_seen"]), current_best)
            spread = max(fitness_values) - min(fitness_values)
            local_scale = max(abs(spread), abs(current_best) * 0.05, 1e-9)
            public_state["scale"] = max(float(public_state["scale"]), local_scale)
            improvement = max(0.0, float(public_state["best_seen"]) - float(public_state["baseline_best"]))
            visible = 1.0 - math.exp(-improvement / max(float(public_state["scale"]), 1e-9))
            public_state["public"] = max(float(public_state["public"]), visible)
            return float(public_state["public"])

        def telemetry_callback(gen, optimizer):
            try:
                append_event(Path(telemetry_path), build_generation_event(gen, optimizer, public_convergence(optimizer)))
            except Exception as exc:
                print(f"[AmpSys] WARNING: live telemetry event failed: {exc}", file=sys.stderr)
            if callback:
                return bool(callback(gen, optimizer))
            return False

        return original_optimize(self, initial_population=initial_population, callback=telemetry_callback)

    GeneticOptimizer.optimize = optimize_with_telemetry
    GeneticOptimizer._ampsys_telemetry_path = telemetry
    GeneticOptimizer._ampsys_telemetry_installed = True


def summarize_result(result, mos_name_map: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    mos_name_map = mos_name_map or {}
    sized = getattr(result, "best_sized_data", None)
    ac = getattr(result, "best_ac_result", None)
    devices = []
    passives = {}
    if sized:
        passives = dict(getattr(sized, "passives", {}) or {})
        for name, mos in getattr(sized, "transistors", {}).items():
            original_name = mos_name_map.get(name, name)
            devices.append(
                {
                    "name": original_name,
                    "internal_name": name,
                    "type": getattr(mos, "mos_type", ""),
                    "W": float(getattr(mos, "W", 0.0) or 0.0),
                    "L": float(getattr(mos, "L", 0.0) or 0.0),
                    "Id": float(getattr(mos, "Id", 0.0) or 0.0),
                    "gm": float(getattr(mos, "gm", 0.0) or 0.0),
                    "gds": float(getattr(mos, "gds", 0.0) or 0.0),
                    "gmb": float(getattr(mos, "gmb", 0.0) or 0.0),
                    "Vgs": float(getattr(mos, "Vgs", 0.0) or 0.0),
                    "Vds": float(getattr(mos, "Vds", 0.0) or 0.0),
                    "Vdsat": float(getattr(mos, "Vdsat", 0.0) or 0.0),
                    "Cgg": float(getattr(mos, "Cgg", 0.0) or 0.0),
                    "Cgs": float(getattr(mos, "Cgs", 0.0) or 0.0),
                    "Cgd": float(getattr(mos, "Cgd", 0.0) or 0.0),
                    "fingers": int(getattr(mos, "N_fingers", 1) or 1),
                    "W_finger": float(getattr(mos, "W_finger", 0.0) or 0.0),
                }
            )
    metrics = {}
    if ac:
        metrics = {
            "dc_gain": float(getattr(ac, "dc_gain", 0.0) or 0.0),
            "gbw": float(getattr(ac, "gbw", 0.0) or 0.0),
            "pm": float(getattr(ac, "pm", 0.0) or 0.0),
            "noise": float(getattr(ac, "input_referred_noise", 0.0) or 0.0),
            "cmrr": float(getattr(ac, "cmrr", 0.0) or 0.0),
            "psrr": float(getattr(ac, "psrr", 0.0) or 0.0),
        }
    if sized:
        metrics["power"] = float(getattr(sized, "total_power", 0.0) or 0.0)
        metrics["current"] = float(getattr(sized, "total_current", 0.0) or 0.0)
        total_area_um2 = sum(
            float(getattr(mos, "W", 0.0) or 0.0) * float(getattr(mos, "L", 0.0) or 0.0)
            for mos in getattr(sized, "transistors", {}).values()
        ) * 1e12
        metrics["area_um2"] = total_area_um2
        metrics["area_per_device_um2"] = total_area_um2 / max(1, len(getattr(sized, "transistors", {}) or {}))
    return {
        "convergence": 1.0,
        "total_generations": int(getattr(result, "total_generations", 0) or 0),
        "total_evaluations": int(getattr(result, "total_evaluations", 0) or 0),
        "metrics": metrics,
        "devices": devices,
        "passives": passives,
    }


def validate_result_summary(summary: Dict[str, Any]) -> None:
    devices = summary.get("devices") or []
    passives = summary.get("passives") or {}
    if not devices and not passives:
        raise RuntimeError(
            "AmpSys optimization finished without any sized devices. "
            "No writeback file was generated. Check the optimizer log for synthesis "
            "violations, topology recognition, LUT coverage, and terminal order."
        )


def write_skill_result(
    result_json: Path,
    skill_path: Path,
    lib: str = "",
    cell: str = "",
    view: str = "schematic",
    settings: Optional[Dict[str, Any]] = None,
) -> None:
    data = json.loads(result_json.read_text(encoding="utf-8-sig"))
    validate_result_summary(data)
    wb = merged_writeback_settings(settings)
    width_aliases = split_aliases(wb.get("width_aliases"), DEFAULT_WRITEBACK_SETTINGS["width_aliases"])
    finger_width_aliases = split_aliases(wb.get("finger_width_aliases"), DEFAULT_WRITEBACK_SETTINGS["finger_width_aliases"])
    length_aliases = split_aliases(wb.get("length_aliases"), DEFAULT_WRITEBACK_SETTINGS["length_aliases"])
    finger_aliases = split_aliases(wb.get("finger_aliases"), DEFAULT_WRITEBACK_SETTINGS["finger_aliases"])
    multiplier_aliases = split_aliases(wb.get("multiplier_aliases"), DEFAULT_WRITEBACK_SETTINGS["multiplier_aliases"])
    passive_aliases = split_aliases(wb.get("passive_value_aliases"), DEFAULT_WRITEBACK_SETTINGS["passive_value_aliases"])
    width_mode = str(wb.get("width_mode") or "auto").strip().lower()
    if width_mode not in {"auto", "finger", "total"}:
        width_mode = "auto"
    try:
        geometry_decimals = int(float(wb.get("geometry_decimals", 2)))
    except Exception:
        geometry_decimals = 2
    geometry_decimals = max(0, min(9, geometry_decimals))

    def fmt_u(value: float) -> str:
        return f"{float(value or 0.0) * 1e6:.{geometry_decimals}f}u"

    def fmt_passive(name: str, value: float) -> str:
        if name.upper().startswith("R"):
            if abs(value) >= 1e3:
                return f"{value / 1e3:.6g}k"
            return f"{value:.6g}"
        if name.upper().startswith("C"):
            return f"{value * 1e12:.6g}p"
        return f"{value:.6g}"

    lines = ["; Auto-generated by AmpSys Cadence Plugin"]
    lines.append(f"ampsys_writeback_width_mode = {skill_quote(width_mode)}")
    lines.append(f"ampsys_writeback_finger_width_aliases = {skill_string_list(finger_width_aliases)}")
    lines.append(f"ampsys_writeback_finger_aliases = {skill_string_list(finger_aliases)}")
    lines.append("ampsys_result_devices = list(")
    for dev in data.get("devices", []):
        fingers = max(1, int(float(dev.get("fingers", 1) or 1)))
        w_total = float(dev.get("W", 0.0) or 0.0)
        w_finger = float(dev.get("W_finger", 0.0) or 0.0)
        if w_finger <= 0 and fingers > 0:
            w_finger = w_total / fingers
        actions = [
            "list("
            + " ".join([
                skill_quote("width"),
                skill_quote(fmt_u(w_finger if w_finger > 0 else w_total)),
                skill_quote(fmt_u(w_total)),
                skill_string_list(width_aliases),
            ])
            + ")",
            "list("
            + " ".join([
                skill_quote("length"),
                skill_quote(fmt_u(dev.get("L", 0.0))),
                skill_string_list(length_aliases),
            ])
            + ")",
            "list("
            + " ".join([
                skill_quote("fingers"),
                skill_quote(str(fingers)),
                skill_string_list(finger_aliases),
            ])
            + ")",
        ]
        mult_value = str(wb.get("multiplier_value", "")).strip()
        if multiplier_aliases and mult_value:
            actions.append(
                "list("
                + " ".join([
                    skill_quote("multiplier"),
                    skill_quote(mult_value),
                    skill_string_list(multiplier_aliases),
                ])
                + ")"
            )
        lines.append(f"  list({skill_quote(dev.get('name', ''))} list({' '.join(actions)}))")
    raw_passives = data.get("passives", {}) or {}
    if isinstance(raw_passives, dict):
        passive_items = raw_passives.items()
    elif isinstance(raw_passives, list):
        passive_items = ((item.get("name", ""), item.get("value", 0.0)) for item in raw_passives if isinstance(item, dict))
    else:
        passive_items = []
    for name, value in passive_items:
        if not name:
            continue
        pval = fmt_passive(name, float(value or 0.0))
        action = "list(" + " ".join([skill_quote("value"), skill_quote(pval), skill_string_list(passive_aliases)]) + ")"
        lines.append(f"  list({skill_quote(name)} list({action}))")
    lines.append(")")
    if lib and cell:
        lines.append(f"ampsys_result_lib = {skill_quote(lib)}")
        lines.append(f"ampsys_result_cell = {skill_quote(cell)}")
        lines.append(f"ampsys_result_view = {skill_quote(view or 'schematic')}")
    lines.append('printf("[AmpSys] Loaded %d sizing records.\\n" length(ampsys_result_devices))')
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_build_library(project_path: Path) -> None:
    project = json.loads(project_path.read_text(encoding="utf-8-sig"))
    print_project_summary(project, project_path, "build-library")
    telemetry = Path(project.get("telemetry_path") or project_path.with_name("telemetry.jsonl"))
    lib = project.get("library", {})
    model_text = str(lib.get("model_path") or "").strip()
    if not model_text:
        raise FileNotFoundError(
            "Model path is required for build-library. Select the exact HSPICE/Spectre .lib/.scs file from your PDK."
        )
    model_path = Path(model_text).expanduser()
    if not model_path.is_file():
        raise FileNotFoundError(f"Model path was not found: {model_path}")
    grid = project_grid_summary(lib)
    append_event(telemetry, {
        "phase": "build_library",
        "status": "start",
        "progress": 0.02,
        "total_points": grid["total_points"],
        "grid": {k: grid[k] for k in ("L", "VGS", "VDS", "VSB")},
        "simulator": normalize_simulator_backend(lib),
        "cache_dir": str(lib.get("cache_dir") or ""),
        "temp_dir": str(lib.get("temp_dir") or ""),
        "spectre_threads": str(lib.get("spectre_threads") or "auto"),
        "spectre_threads_resolved": auto_spectre_threads(lib),
        "spectre_accel": spectre_accel_label(lib),
        "spectre_batch_points": str(lib.get("spectre_batch_points") or "auto"),
        "spectre_batch_workers": str(lib.get("spectre_batch_workers") or "auto"),
        "spectre_device_workers": str(lib.get("spectre_device_workers") or "auto"),
        "spectre_scratch": str(lib.get("spectre_scratch") or ""),
        "spectre_max_combos_per_batch": str(lib.get("spectre_max_combos_per_batch") or "auto"),
        "time": time.time(),
    })
    os.environ["AMPSYS_BUILD_TELEMETRY"] = str(telemetry)
    flow, engine_root = create_flow(project)
    manifest = {
        "status": "ready",
        "engine_root": str(engine_root),
        "library": project["library"],
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    lib_dir = Path(project["library"].get("cache_dir") or project_path.parent / "libraries")
    write_json(lib_dir / "manifest.json", manifest)
    append_event(telemetry, {"phase": "build_library", "status": "done", "progress": 1.0, "time": time.time()})
    print(f"[AmpSys] Library ready: {lib_dir}")


def tiny_spectre_project(project: Dict[str, Any], project_path: Path, accel: str, root: Path) -> Dict[str, Any]:
    bench = json.loads(json.dumps(project))
    bench["project_dir"] = str(root)
    bench["telemetry_path"] = str(root / f"telemetry_{accel.replace('+', 'p').replace('=', '_')}.jsonl")
    bench["result_path"] = str(root / "result.json")
    bench["skill_result_path"] = str(root / "ampsys_result.il")
    lib = bench.setdefault("library", {})
    lib["simulator_backend"] = "spectre"
    lib["spectre_accel"] = accel
    lib["cache_dir"] = str(root / f"cache_{accel.replace('+', 'p').replace('=', '_')}")
    lib["temp_dir"] = str(root / "tmp")
    lib["force_rescan"] = True
    source_l = expanded_l_list(lib)
    l_points = max(1, get_int_from_env("AMPSYS_SPECTRE_BENCH_L_POINTS", 1))
    lib["L_list"] = source_l[:l_points]
    vdd = get_float(lib, "process_vdd", 1.8)
    vgs_start, vgs_full_stop, vgs_step = parse_range(lib, "vgs", (0.0, vdd, 0.002))
    vds_start, vds_full_stop, vds_step = parse_range(lib, "vds", (0.05, vdd, 0.02))
    vsb_start, vsb_full_stop, vsb_step = parse_range(lib, "vsb", (0.0, 0.0, 0.1))
    vgs_points = min(range_point_count(vgs_start, vgs_full_stop, vgs_step), max(2, get_int_from_env("AMPSYS_SPECTRE_BENCH_VGS_POINTS", 9)))
    vds_points = min(range_point_count(vds_start, vds_full_stop, vds_step), max(2, get_int_from_env("AMPSYS_SPECTRE_BENCH_VDS_POINTS", 4)))
    vsb_points = min(range_point_count(vsb_start, vsb_full_stop, vsb_step), max(1, get_int_from_env("AMPSYS_SPECTRE_BENCH_VSB_POINTS", 1)))
    lib["vgs_start"] = vgs_start
    lib["vgs_stop"] = vgs_start + vgs_step * (vgs_points - 1)
    lib["vgs_step"] = vgs_step
    lib["vds_start"] = vds_start
    lib["vds_stop"] = vds_start + vds_step * (vds_points - 1)
    lib["vds_step"] = vds_step
    lib["vsb_start"] = vsb_start
    lib["vsb_stop"] = vsb_start + vsb_step * (vsb_points - 1) if vsb_step else vsb_start
    lib["vsb_step"] = vsb_step
    lib["use_batch_mode"] = True
    return bench


def get_int_from_env(name: str, default: int) -> int:
    try:
        value = int(float(os.environ.get(name, "") or default))
        return value if value > 0 else default
    except Exception:
        return default


def project_grid_summary(lib: Dict[str, Any]) -> Dict[str, int]:
    l_list = expanded_l_list(lib)
    n_l = len(l_list)
    vgs = parse_range(lib, "vgs", (0.0, get_float(lib, "process_vdd", 1.8), 0.002))
    vds = parse_range(lib, "vds", (0.05, get_float(lib, "process_vdd", 1.8), 0.02))
    vsb = parse_range(lib, "vsb", (0.0, 0.0, 0.1))
    n_vgs = range_point_count(*vgs)
    n_vds = range_point_count(*vds)
    n_vsb = range_point_count(*vsb)
    return {"L": n_l, "VGS": n_vgs, "VDS": n_vds, "VSB": n_vsb, "total_points": n_l * n_vgs * n_vds * n_vsb * 2}


def range_point_count(start: float, stop: float, step: float) -> int:
    if not step:
        return 1
    span = (stop - start) / step
    if span < 0:
        return 1
    return max(1, int(round(span)) + 1)


def expanded_l_list(lib: Dict[str, Any]) -> List[float]:
    explicit = parse_float_list(lib.get("L_list"))
    if explicit:
        return explicit
    l_min = get_float(lib, "L_min", 0.18e-6) or 0.18e-6
    l_max = 25.0 * l_min
    return generate_l_grid(l_min, l_max)


def generate_l_grid(l_min: float, l_max: float, num_points: int = 200, mode: str = "adaptive") -> List[float]:
    if mode == "adaptive":
        threshold = min(l_max, max(3.0 * l_min, 2.8 * l_min))
        if l_min < threshold < l_max:
            n_short = int(num_points * 0.6)
            n_long = num_points - n_short
            l_short = geomspace(l_min, threshold, n_short)
            l_long = geomspace(threshold, l_max, n_long + 1)[1:]
            values = l_short + l_long
        else:
            values = geomspace(l_min, l_max, num_points)
    elif mode == "log":
        values = geomspace(l_min, l_max, num_points)
    else:
        values = linspace(l_min, l_max, num_points)
    return [round(float(value), 12) for value in values]


def linspace(start: float, stop: float, count: int) -> List[float]:
    if count <= 1:
        return [float(start)]
    step = (stop - start) / (count - 1)
    return [start + step * idx for idx in range(count)]


def geomspace(start: float, stop: float, count: int) -> List[float]:
    if count <= 1:
        return [float(start)]
    if start <= 0 or stop <= 0:
        return linspace(start, stop, count)
    log_start = math.log(start)
    log_stop = math.log(stop)
    return [math.exp(value) for value in linspace(log_start, log_stop, count)]


def spectre_benchmark_estimate(full_grid: Dict[str, int], modes: List[Dict[str, Any]]) -> Dict[str, Any]:
    full_points = int(full_grid.get("total_points") or 0)
    estimates: List[Dict[str, Any]] = []
    ok_modes = [m for m in modes if m.get("status") == "ok"]
    for mode in ok_modes:
        bench_grid = mode.get("grid") or {}
        bench_points = int(bench_grid.get("total_points") or 0)
        elapsed = float(mode.get("elapsed_s") or 0.0)
        cache = mode.get("cache") or {}
        bytes_total = int(cache.get("bytes") or 0)
        points_per_second = (bench_points / elapsed) if bench_points > 0 and elapsed > 0 else 0.0
        estimated_elapsed = (full_points / points_per_second) if points_per_second > 0 else None
        estimated_bytes = (bytes_total * full_points / bench_points) if bench_points > 0 else None
        estimates.append({
            "accel": mode.get("accel", ""),
            "bench_points": bench_points,
            "bench_elapsed_s": elapsed,
            "points_per_second": points_per_second,
            "full_grid_points": full_points,
            "scale_factor": (full_points / bench_points) if bench_points > 0 else None,
            "estimated_elapsed_s": estimated_elapsed,
            "estimated_elapsed_min": (estimated_elapsed / 60.0) if estimated_elapsed is not None else None,
            "estimated_cache_bytes": int(estimated_bytes) if estimated_bytes is not None else None,
            "under_20min_target": (estimated_elapsed <= 20 * 60) if estimated_elapsed is not None else None,
        })
    best = None
    if estimates:
        best = min(
            (item for item in estimates if item["estimated_elapsed_s"] is not None),
            key=lambda item: item["estimated_elapsed_s"],
            default=None,
        )
    max_scale = max((float(item.get("scale_factor") or 0.0) for item in estimates), default=0.0)
    confidence = "medium" if ok_modes and max_scale <= 5000 else "low"
    if max_scale > 50_000:
        confidence = "very_low"
    return {
        "full_grid": full_grid,
        "estimates": estimates,
        "best_accel": best.get("accel") if best else None,
        "best_estimated_elapsed_min": best.get("estimated_elapsed_min") if best else None,
        "best_under_20min_target": best.get("under_20min_target") if best else None,
        "confidence": confidence,
        "note": (
            "Estimate is based on a controlled small Spectre LUT sample using the same parser/cache format. "
            "It avoids a full LUT run. For full-resolution grids, tiny samples are dominated by Spectre startup "
            "and can be misleading; use a same-spacing sample with multiple L points before committing to a full LUT. "
            "Runtime can differ with license checkout, filesystem, PDK model cost, and large-batch Spectre scaling."
        ),
    }


def run_spectre_benchmark(project_path: Path, modes: Optional[List[str]] = None) -> None:
    project = json.loads(project_path.read_text(encoding="utf-8-sig"))
    lib = project.get("library", {})
    if normalize_simulator_backend({**lib, "simulator_backend": "spectre"}) != "spectre":
        raise ValueError("Internal error: spectre benchmark must use simulator_backend=spectre.")
    model_text = str(lib.get("model_path") or "").strip()
    if not model_text:
        raise FileNotFoundError("Model path is required for Spectre benchmark. Select the exact Spectre .lib/.scs file manually.")
    if not Path(model_text).expanduser().is_file():
        raise FileNotFoundError(f"Model path was not found: {model_text}")
    modes = modes or ["auto"]
    root = project_path.parent / "spectre_benchmark"
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    summary: Dict[str, Any] = {
        "status": "ok",
        "project": str(project_path),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": "Small-grid benchmark only; it estimates full LUT runtime without modifying the real cache_dir.",
        "full_grid": project_grid_summary(lib),
        "modes": [],
    }
    for accel in modes:
        bench_project = tiny_spectre_project(project, project_path, accel, root / accel.replace("+", "p").replace("=", "_"))
        bench_path = Path(bench_project["project_dir"]) / "project.json"
        write_json(bench_path, bench_project)
        t0 = time.time()
        try:
            if getattr(sys, "frozen", False):
                cmd = [sys.executable, "build-library", "--project", str(bench_path)]
            else:
                cmd = [sys.executable, str(Path(__file__).resolve()), "build-library", "--project", str(bench_path)]
            proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
            elapsed = time.time() - t0
            log_path = bench_path.with_name("build_library_stdout.log")
            log_path.write_text(proc.stdout or "", encoding="utf-8", errors="ignore")
            if proc.returncode != 0:
                raise RuntimeError(f"build-library failed for accel={accel}, code={proc.returncode}. See {log_path}")
            cache_dir = Path(bench_project["library"]["cache_dir"])
            summary["modes"].append({
                "accel": accel,
                "status": "ok",
                "elapsed_s": elapsed,
                "grid": project_grid_summary(bench_project["library"]),
                "cache": dir_stats(cache_dir),
                "telemetry": bench_project["telemetry_path"],
                "log": str(log_path),
            })
        except Exception as exc:
            summary["modes"].append({
                "accel": accel,
                "status": "error",
                "elapsed_s": time.time() - t0,
                "grid": project_grid_summary(bench_project.get("library", {})),
                "error": str(exc),
                "telemetry": bench_project.get("telemetry_path", ""),
            })
    summary["full_grid_estimate"] = spectre_benchmark_estimate(summary["full_grid"], summary["modes"])
    write_json(root / "summary.json", summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def run_compare_cache(reference: Path, candidate: Path, output: Optional[Path] = None) -> None:
    ref = cache_layout_summary(reference)
    cand = cache_layout_summary(candidate)
    result = {
        "status": "ok" if ref["exists"] and cand["exists"] else "error",
        "reference": ref,
        "candidate": cand,
        "ratios": {
            "total_bytes_candidate_over_reference": ratio_or_none(cand["bytes"], ref["bytes"]),
            "nmos_bytes_candidate_over_reference": ratio_or_none(cand["nmos_bytes"], ref["nmos_bytes"]),
            "pmos_bytes_candidate_over_reference": ratio_or_none(cand["pmos_bytes"], ref["pmos_bytes"]),
            "pkl_count_candidate_over_reference": ratio_or_none(cand["pkl_count"], ref["pkl_count"]),
            "data_dir_count_candidate_over_reference": ratio_or_none(cand["data_dir_count"], ref["data_dir_count"]),
        },
        "layout_match": {
            "same_pkl_count": ref["pkl_count"] == cand["pkl_count"],
            "same_data_dir_count": ref["data_dir_count"] == cand["data_dir_count"],
            "has_nmos_and_pmos": bool(cand["nmos_pkl"]) and bool(cand["pmos_pkl"]),
        },
    }
    if output:
        write_json(output, result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result["status"] != "ok":
        raise SystemExit(1)


def run_optimize(project_path: Path) -> None:
    project = json.loads(project_path.read_text(encoding="utf-8-sig"))
    project["project_dir"] = project.get("project_dir") or str(project_path.parent)
    print_project_summary(project, project_path, "optimize")
    ensure_library_ready(project, project_path)
    telemetry = Path(project.get("telemetry_path") or project_path.with_name("telemetry.jsonl"))
    result_path = Path(project.get("result_path") or project_path.with_name("result.json"))
    skill_path = Path(project.get("skill_result_path") or project_path.with_name("ampsys_result.il"))
    telemetry.write_text("", encoding="utf-8")
    append_event(telemetry, {"phase": "optimize", "status": "start", "time": time.time()})

    flow, _ = create_flow(project)
    mos, passives, mos_name_map = make_intents(project)
    flow.set_topology(mos, passives, skip_kcl=bool(project.get("skip_kcl", False)))

    specs = project["specs"]

    try:
        # Keep the plugin on the same public API path used by AmpSys examples:
        # AmpFlow.from_pdk(...) -> set_topology(...) -> flow.optimize(specs=...).
        install_optimizer_telemetry(telemetry)
        result = flow.optimize(specs=specs)
        summary = summarize_result(result, mos_name_map)
        validate_result_summary(summary)
        write_json(result_path, summary)
        cad = project.get("cadence", {})
        write_skill_result(result_path, skill_path, cad.get("lib", ""), cad.get("cell", ""), cad.get("view", "schematic"), project.get("settings", {}))
        append_event(telemetry, {"phase": "optimize", "status": "done", "result_path": str(result_path), "time": time.time()})
        print(f"[AmpSys] Result written: {result_path}")
        print(f"[AmpSys] SKILL result written: {skill_path}")
    except Exception as exc:
        append_event(
            telemetry,
            {
                "phase": "optimize",
                "status": "error",
                "message": str(exc),
                "traceback": traceback.format_exc(),
                "time": time.time(),
            },
        )
        raise


def run_writeback(project_path: Path) -> None:
    project = json.loads(project_path.read_text(encoding="utf-8-sig"))
    print_project_summary(project, project_path, "writeback")
    result_path = Path(project.get("result_path") or project_path.with_name("result.json"))
    skill_path = Path(project.get("skill_result_path") or project_path.with_name("ampsys_result.il"))
    cad = project.get("cadence", {})
    write_skill_result(result_path, skill_path, cad.get("lib", ""), cad.get("cell", ""), cad.get("view", "schematic"), project.get("settings", {}))
    print(skill_path)


def diagnose_project(project_path: Path) -> None:
    project = json.loads(project_path.read_text(encoding="utf-8-sig"))
    project["project_dir"] = project.get("project_dir") or str(project_path.parent)
    lib = project.get("library", {})
    specs = project.get("specs", {})
    devices = project.get("devices", []) or []
    netlist_text = str(project.get("netlist_path") or "").strip()
    netlist_path = Path(netlist_text).expanduser() if netlist_text else None
    engine_text = str(project.get("engine_root") or os.environ.get("AMPSYS_ENGINE_ROOT", "") or "")
    try:
        engine_root = resolve_engine_root(Path(engine_text).expanduser()) if engine_text else resolve_engine_root(Path(__file__).resolve().parents[1])
        engine_error = ""
    except Exception as exc:
        engine_root = Path(engine_text or "")
        engine_error = str(exc)

    cache_dir = library_cache_dir(lib, project_path)
    marker = library_ready_marker(lib, project_path)
    direct_pair = find_ampflow_cache_pair(lib, cache_dir)
    child_dir = cache_dir / "autoflow_cache"
    child_pair = find_ampflow_cache_pair(lib, child_dir) if child_dir.is_dir() else None
    best_pair = direct_pair or child_pair
    inferred_model_path = ""
    if best_pair:
        key = best_pair[0].name[len("nmos_"):-len(".pkl")]
        stem = infer_model_stem_from_cache_key(key, lib)
        if stem:
            inferred_model_path = str(best_pair[0].parent / f"{stem}.lib")

    mos_devices = [d for d in devices if (d.get("type") or d.get("kind")) in ("nmos", "pmos")]
    passive_devices = [d for d in devices if (d.get("type") or d.get("kind")) in ("res", "cap")]
    missing_current = []
    for d in mos_devices:
        current = get_float(d, "current", 0.0)
        if current <= 0:
            current = get_float(d, "Id", 0.0)
        if current <= 0:
            missing_current.append(str(d.get("name") or "<unnamed>"))

    weight_keys = ("fitness_a", "fitness_b", "fitness_c", "fitness_d", "fitness_e", "fitness_f", "fitness_g")
    weights = {key: specs.get(key, None) for key in weight_keys}
    missing_weights = [key for key, value in weights.items() if value in (None, "")]
    engine_source = has_source_engine(engine_root)
    engine_compiled = has_compiled_engine(engine_root)
    core_exe = find_core_executable()
    would_delegate = should_delegate_to_core("optimize", ["optimize", "--project", str(project_path)])

    issues: List[str] = []
    if engine_error:
        issues.append(f"Engine root error: {engine_error}")
    if not engine_compiled and not core_exe and not engine_source:
        issues.append("No AmpSys engine or protected core was detected.")
    if marker is None:
        issues.append("LUT cache is not ready.")
    if missing_current:
        issues.append("MOS current is missing for: " + ", ".join(missing_current))
    if missing_weights:
        issues.append("V2 weight key(s) missing from specs: " + ", ".join(missing_weights))
    if not mos_devices:
        issues.append("No MOS devices were parsed from project.")
    if netlist_text and netlist_path is not None and not netlist_path.is_file():
        issues.append(f"Netlist path does not exist: {netlist_path}")

    diagnostic = {
        "status": "ok" if not issues else "warning",
        "issues": issues,
        "project": str(project_path),
        "netlist": {
            "path": netlist_text,
            "exists": bool(netlist_path and netlist_path.is_file()),
        },
        "engine": {
            "configured": engine_text,
            "resolved": str(engine_root),
            "error": engine_error,
            "source_engine": engine_source,
            "compiled_engine": engine_compiled,
            "core_executable": str(core_exe or ""),
            "runner_would_delegate_optimize": would_delegate,
        },
        "library": {
            "cache_dir": str(cache_dir),
            "ready": marker is not None,
            "marker": str(marker or ""),
            "direct_pair": [str(p) for p in direct_pair] if direct_pair else [],
            "child_pair": [str(p) for p in child_pair] if child_pair else [],
            "model_path": str(lib.get("model_path") or ""),
            "model_path_stem": model_stem(lib.get("model_path")),
            "inferred_model_path_for_cache_only": inferred_model_path,
            "expected_markers": [str(p) for p in expected_library_markers(lib, project_path)[:10]],
        },
        "devices": {
            "total": len(devices),
            "mos": len(mos_devices),
            "passives": len(passive_devices),
            "missing_current": missing_current,
        },
        "specs": {
            "weights": weights,
            "missing_weight_keys": missing_weights,
            "gain_min": specs.get("gain_min"),
            "gbw": specs.get("gbw"),
            "pm_min": specs.get("pm_min"),
            "load_cap": specs.get("load_cap"),
        },
        "cadence": project.get("cadence", {}),
        "outputs": {
            "telemetry_path": project.get("telemetry_path", ""),
            "result_path": project.get("result_path", ""),
            "skill_result_path": project.get("skill_result_path", ""),
        },
    }
    print(json.dumps(diagnostic, indent=2, ensure_ascii=False))


def run_self_test() -> None:
    info: Dict[str, Any] = {
        "status": "ok",
        "runner": str(Path(__file__).resolve()),
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "platform_tag": platform_tag(),
        "binary_platform_tag": binary_platform_tag(),
        "core_internal": os.environ.get(CORE_INTERNAL_ENV) == "1",
        "core_executable": str(find_core_executable() or ""),
        "engine_root": "",
        "packages": {},
    }
    try:
        engine_root = add_engine_path(os.environ.get("AMPSYS_ENGINE_ROOT", ""))
        info["engine_root"] = str(engine_root)
        for pkg in ENGINE_PACKAGES:
            try:
                mod = __import__(pkg)
                info["packages"][pkg] = {
                    "ok": True,
                    "file": str(getattr(mod, "__file__", "")),
                }
            except Exception as exc:
                info["packages"][pkg] = {"ok": False, "error": str(exc), "traceback": traceback.format_exc()}
                info["status"] = "error"
    except Exception as exc:
        info["status"] = "error"
        info["error"] = str(exc)
        info["traceback"] = traceback.format_exc()
    print(json.dumps(info, indent=2, ensure_ascii=False))
    if info["status"] != "ok":
        raise SystemExit(1)


def main(argv: Optional[List[str]] = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd0 = argv[0] if argv else ""
    if should_delegate_to_core(cmd0, argv):
        raise SystemExit(delegate_to_core(argv))

    parser = argparse.ArgumentParser(description="AmpSys headless runner")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("build-library", "optimize", "writeback", "diagnose", "spectre-benchmark"):
        p = sub.add_parser(name)
        p.add_argument("--project", required=True)
    p_compare = sub.add_parser("compare-cache")
    p_compare.add_argument("--reference", required=True)
    p_compare.add_argument("--candidate", required=True)
    p_compare.add_argument("--output", default="")
    sub.add_parser("self-test")
    args = parser.parse_args(argv)
    project_path = Path(args.project).resolve() if hasattr(args, "project") else None
    if args.cmd == "build-library":
        assert project_path is not None
        run_build_library(project_path)
    elif args.cmd == "spectre-benchmark":
        assert project_path is not None
        run_spectre_benchmark(project_path)
    elif args.cmd == "optimize":
        assert project_path is not None
        run_optimize(project_path)
    elif args.cmd == "writeback":
        assert project_path is not None
        run_writeback(project_path)
    elif args.cmd == "diagnose":
        assert project_path is not None
        diagnose_project(project_path)
    elif args.cmd == "compare-cache":
        run_compare_cache(
            Path(args.reference).expanduser(),
            Path(args.candidate).expanduser(),
            Path(args.output).expanduser() if args.output else None,
        )
    elif args.cmd == "self-test":
        run_self_test()


if __name__ == "__main__":
    main()
