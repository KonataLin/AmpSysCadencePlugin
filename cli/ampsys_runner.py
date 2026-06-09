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
from pathlib import Path
from typing import Any, Dict, List, Optional


ENGINE_PACKAGES = ("AmpSys", "yami", "TheScanner", "acsolver")
CORE_INTERNAL_ENV = "AMPSYS_CORE_INTERNAL"
DISABLE_CORE_ENV = "AMPSYS_DISABLE_CORE"


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
    tag = binary_platform_tag()
    candidates: List[Path] = []
    for root in core_search_roots(plugin_root):
        candidates.extend([
            root / tag / name,
            root / name,
        ])
    for cand in candidates:
        if cand.is_file():
            return cand.resolve()
    return None


def should_delegate_to_core(cmd: str = "") -> bool:
    if os.environ.get(CORE_INTERNAL_ENV) == "1":
        return False
    if os.environ.get(DISABLE_CORE_ENV) == "1":
        return False
    if cmd == "writeback":
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
    proc = subprocess.run([str(exe), *argv], env=env)
    if proc.returncode == 0 and cmd == "optimize" and project_path:
        try:
            run_writeback(project_path)
        except Exception as exc:
            print(f"[AmpSys] WARNING: source writeback regeneration failed: {exc}", file=sys.stderr)
    return int(proc.returncode)


def prepare_project_for_core(argv: List[str]) -> List[str]:
    if not argv or argv[0] not in {"build-library", "optimize"}:
        return argv
    try:
        idx = argv.index("--project")
        project_path = Path(argv[idx + 1]).resolve()
    except Exception:
        return argv

    project = json.loads(project_path.read_text(encoding="utf-8-sig"))
    lib = project.get("library", {})
    changed = False
    if lib.get("hspice_dir"):
        lib["hspice_cmd"] = resolve_hspice_cmd(lib)
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


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def append_event(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


DEFAULT_WRITEBACK_SETTINGS: Dict[str, Any] = {
    "nmos_terminal_order": "D G S B",
    "pmos_terminal_order": "D G S B",
    "width_mode": "auto",
    "width_aliases": "w,W,wr,width",
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
            "model_path": lib.get("model_path", ""),
            "nmos_name": lib.get("nmos_name", ""),
            "pmos_name": lib.get("pmos_name", ""),
            "model_lib": lib.get("model_lib", ""),
            "temperature": lib.get("temperature", ""),
            "process_vdd": lib.get("process_vdd", ""),
            "hspice_dir": lib.get("hspice_dir", ""),
            "hspice_cmd": lib.get("hspice_cmd", ""),
            "L_min": lib.get("L_min", ""),
            "L_list": lib.get("L_list", ""),
            "scan_width": lib.get("scan_width", ""),
            "vgs": [lib.get("vgs_start", ""), lib.get("vgs_stop", ""), lib.get("vgs_step", "")],
            "vds": [lib.get("vds_start", ""), lib.get("vds_stop", ""), lib.get("vds_step", "")],
            "vsb": [lib.get("vsb_start", ""), lib.get("vsb_stop", ""), lib.get("vsb_step", "")],
        },
        "specs": specs,
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


def library_cache_dir(lib: Dict[str, Any], project_path: Path) -> Path:
    return Path(lib.get("cache_dir") or project_path.parent / "libraries").expanduser()


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
    if manifest.is_file():
        return manifest
    pair = find_ampflow_cache_pair(lib, cache_dir)
    if pair:
        return pair[0]
    child = cache_dir / "autoflow_cache"
    if child.is_dir():
        child_manifest = child / "manifest.json"
        if child_manifest.is_file():
            return child_manifest
        child_pair = find_ampflow_cache_pair(lib, child)
        if child_pair:
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

    if not direct_pair and child_pair:
        cache_dir = child
        lib["cache_dir"] = str(child)
        direct_pair = child_pair

    manifest = cache_dir / "manifest.json"
    if manifest.is_file():
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


def create_flow(project: Dict[str, Any]):
    engine_root = add_engine_path(project.get("engine_root", ""))
    from AmpSys import AmpFlow, AmpFlowConfig

    lib = project["library"]
    run_cfg = project.get("config", {})
    cache_dir = lib.get("cache_dir") or str(Path(project.get("project_dir", ".")).resolve() / "libraries")
    temp_dir = lib.get("temp_dir") or str(Path(project.get("project_dir", ".")).resolve() / "workspace" / "tmp")

    flow = AmpFlow.from_pdk(
        model_path=lib["model_path"],
        cellname_nmos=lib["nmos_name"],
        cellname_pmos=lib["pmos_name"],
        model_lib=lib.get("model_lib", "tt"),
        L_list=parse_float_list(lib.get("L_list")),
        L_min=get_float(lib, "L_min", 0.18e-6),
        VGS_range=parse_range(lib, "vgs", (0.0, get_float(lib, "process_vdd", 1.8), 0.02)),
        VDS_range=parse_range(lib, "vds", (0.05, get_float(lib, "process_vdd", 1.8), 0.05)),
        VSB_range=parse_range(lib, "vsb", (0.0, 0.0, 0.1)),
        scan_width=get_float(lib, "scan_width", 10e-6),
        temperature=get_float(lib, "temperature", 25.0),
        use_batch_mode=bool(lib.get("use_batch_mode", True)),
        batch_size=get_int(lib, "batch_size", 20),
        batch_timeout_ms=get_int(lib, "batch_timeout_ms", 50),
        process_vdd=get_float(lib, "process_vdd", 1.8),
        hspice_cmd=resolve_hspice_cmd(lib),
        cache_dir=cache_dir,
        temp_dir=temp_dir,
        force_rescan=bool(lib.get("force_rescan", False)),
        verbose=bool(run_cfg.get("verbose", True)),
    )
    flow.config = AmpFlowConfig(
        population_size=get_int(run_cfg, "population_size", 50),
        max_generations=get_int(run_cfg, "max_generations", 20),
        verbose=bool(run_cfg.get("verbose", True)),
        parallel=bool(run_cfg.get("parallel", True)),
        print_details=bool(run_cfg.get("print_details", False)),
        enable_kvl_check=bool(run_cfg.get("enable_kvl_check", True)),
        fast_mode=bool(run_cfg.get("fast_mode", True)),
        debug_log=bool(run_cfg.get("debug_log", False)),
        monitor=bool(run_cfg.get("monitor", False)),
        monitor_interval=get_float(run_cfg, "monitor_interval", 5.0),
    )
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
    for d in project.get("devices", []):
        dtype = d.get("type") or d.get("kind")
        if dtype in ("nmos", "pmos"):
            current = get_float(d, "current", 0.0)
            if current <= 0:
                current = get_float(d, "Id", 0.0)
            if current <= 0:
                raise ValueError(f"{d.get('name')}: current must be specified before run.")
            mos.append(
                MosIntent(
                    name=d["name"],
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
    return mos, passives


def metrics_from_individual(ind) -> Dict[str, Any]:
    ac = getattr(ind, "ac_result", None)
    sized = getattr(ind, "sized_data", None)
    out = {"fitness": float(getattr(ind, "fitness", 0.0) or 0.0)}
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
        out.update(
            power=float(getattr(sized, "total_power", 0.0) or 0.0),
            current=float(getattr(sized, "total_current", 0.0) or 0.0),
        )
    return out


def summarize_result(result) -> Dict[str, Any]:
    sized = getattr(result, "best_sized_data", None)
    ac = getattr(result, "best_ac_result", None)
    devices = []
    passives = {}
    if sized:
        passives = dict(getattr(sized, "passives", {}) or {})
        for name, mos in getattr(sized, "transistors", {}).items():
            devices.append(
                {
                    "name": name,
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
    return {
        "best_fitness": float(getattr(result, "best_fitness", 0.0) or 0.0),
        "total_generations": int(getattr(result, "total_generations", 0) or 0),
        "total_evaluations": int(getattr(result, "total_evaluations", 0) or 0),
        "metrics": metrics,
        "devices": devices,
        "passives": passives,
    }


def write_skill_result(
    result_json: Path,
    skill_path: Path,
    lib: str = "",
    cell: str = "",
    view: str = "schematic",
    settings: Optional[Dict[str, Any]] = None,
) -> None:
    data = json.loads(result_json.read_text(encoding="utf-8-sig"))
    wb = merged_writeback_settings(settings)
    width_aliases = split_aliases(wb.get("width_aliases"), DEFAULT_WRITEBACK_SETTINGS["width_aliases"])
    length_aliases = split_aliases(wb.get("length_aliases"), DEFAULT_WRITEBACK_SETTINGS["length_aliases"])
    finger_aliases = split_aliases(wb.get("finger_aliases"), DEFAULT_WRITEBACK_SETTINGS["finger_aliases"])
    multiplier_aliases = split_aliases(wb.get("multiplier_aliases"), DEFAULT_WRITEBACK_SETTINGS["multiplier_aliases"])
    passive_aliases = split_aliases(wb.get("passive_value_aliases"), DEFAULT_WRITEBACK_SETTINGS["passive_value_aliases"])
    width_mode = str(wb.get("width_mode") or "auto").strip().lower()
    if width_mode not in {"auto", "finger", "total"}:
        width_mode = "auto"

    def fmt_u(value: float) -> str:
        return f"{value * 1e6:.6g}u"

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
    l_list = parse_float_list(lib.get("L_list"))
    n_l = len(l_list) if l_list else 20
    vgs = parse_range(lib, "vgs", (0.0, get_float(lib, "process_vdd", 1.8), 0.02))
    vds = parse_range(lib, "vds", (0.05, get_float(lib, "process_vdd", 1.8), 0.05))
    vsb = parse_range(lib, "vsb", (0.0, 0.0, 0.1))
    n_vgs = int((vgs[1] - vgs[0]) / vgs[2]) + 1 if vgs[2] else 1
    n_vds = int((vds[1] - vds[0]) / vds[2]) + 1 if vds[2] else 1
    n_vsb = int((vsb[1] - vsb[0]) / vsb[2]) + 1 if vsb[2] else 1
    total_points = n_l * n_vgs * n_vds * n_vsb * 2
    append_event(telemetry, {
        "phase": "build_library",
        "status": "start",
        "progress": 0.02,
        "total_points": total_points,
        "grid": {"L": n_l, "VGS": n_vgs, "VDS": n_vds, "VSB": n_vsb},
        "time": time.time(),
    })
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
    mos, passives = make_intents(project)
    flow.set_topology(mos, passives, skip_kcl=bool(project.get("skip_kcl", False)))

    from yami.optimizer import GeneticOptimizer, OptimizerConfig
    from yami.objectives import ObjectiveFactory
    import numpy as np

    specs = project["specs"]
    if flow._context is None:
        flow._build_context(specs)

    weight_keys = ("fitness_a", "fitness_b", "fitness_c", "fitness_d", "fitness_e", "fitness_f", "fitness_g")
    objective_kwargs = {k: specs[k] for k in weight_keys if k in specs}
    objective = ObjectiveFactory.create("Balanced", flow._context.spec, **objective_kwargs)
    cfg = project.get("config", {})
    opt_config = OptimizerConfig(
        population_size=get_int(cfg, "population_size", 50),
        max_generations=get_int(cfg, "max_generations", 20),
        elite_ratio=get_float(cfg, "elite_ratio", 0.1),
        crossover_prob=get_float(cfg, "crossover_prob", 0.85),
        mutation_prob=get_float(cfg, "mutation_prob", 0.5),
        mutation_sigma_gmid=get_float(cfg, "mutation_sigma_gmid", 1.5),
        mutation_sigma_L=get_float(cfg, "mutation_sigma_L", 0.5e-6),
        mutation_sigma_I=get_float(cfg, "mutation_sigma_I", 10e-6),
        tournament_size=get_int(cfg, "tournament_size", 3),
        selection_strategy=cfg.get("selection_strategy", "tournament"),
        rank_pressure=get_float(cfg, "rank_pressure", 1.8),
        de_mutation_prob=get_float(cfg, "de_mutation_prob", 0.5),
        random_guy_ratio=get_float(cfg, "random_guy_ratio", 0.1),
        cataclysm_patience=get_int(cfg, "cataclysm_patience", 5),
        cataclysm_threshold=get_float(cfg, "cataclysm_threshold", 0.001),
        verbose=bool(cfg.get("verbose", True)),
        parallel_evaluation=bool(cfg.get("parallel", True)),
        n_parallel_workers=(get_int(cfg, "n_parallel_workers", 0) or None),
        hspice_max_parallel=get_int(cfg, "hspice_max_parallel", 4),
        debug_log=bool(cfg.get("debug_log", False)),
        convergence_patience=get_int(cfg, "convergence_patience", 9999),
        convergence_threshold=get_float(cfg, "convergence_threshold", 1e-6),
        use_adaptive_mutation=bool(cfg.get("use_adaptive_mutation", True)),
        pm_penalty_k=get_float(cfg, "pm_penalty_k", 0.01),
        adaptive_population=bool(cfg.get("adaptive_population", True)),
        adaptive_target_fill_rate=get_float(cfg, "adaptive_target_fill_rate", 0.7),
        adaptive_pop_max_ratio=get_float(cfg, "adaptive_pop_max_ratio", 5.0),
        random_seed=cfg.get("random_seed") or None,
    )

    if flow.config.fast_mode:
        from TheScanner import set_fast_mode
        set_fast_mode(True)

    rng = np.random.default_rng(get_int(cfg, "random_seed", 42))
    initial_pop = [
        flow._context.create_random_genome(flow._context, rng)
        for _ in range(opt_config.population_size)
    ]
    optimizer = GeneticOptimizer(context=flow._context, objective=objective, config=opt_config)

    def callback(gen, opt):
        stats = opt.generation_stats[-1] if opt.generation_stats else {}
        best = metrics_from_individual(opt.best_individual) if opt.best_individual else {}
        points = []
        for idx, ind in enumerate(getattr(opt, "population", [])[:250]):
            row = metrics_from_individual(ind)
            row["idx"] = idx
            points.append(row)
        append_event(
            telemetry,
            {
                "phase": "optimize",
                "status": "generation",
                "generation": gen + 1,
                "max_generations": opt_config.max_generations,
                "progress": (gen + 1) / max(1, opt_config.max_generations),
                "stats": stats,
                "best": best,
                "points": points,
                "time": time.time(),
            },
        )
        return False

    try:
        result = optimizer.optimize(initial_population=initial_pop, callback=callback)
        flow._last_result = result
        summary = summarize_result(result)
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
    finally:
        if flow.config.fast_mode:
            from TheScanner import set_fast_mode
            set_fast_mode(False)


def run_writeback(project_path: Path) -> None:
    project = json.loads(project_path.read_text(encoding="utf-8-sig"))
    print_project_summary(project, project_path, "writeback")
    result_path = Path(project.get("result_path") or project_path.with_name("result.json"))
    skill_path = Path(project.get("skill_result_path") or project_path.with_name("ampsys_result.il"))
    cad = project.get("cadence", {})
    write_skill_result(result_path, skill_path, cad.get("lib", ""), cad.get("cell", ""), cad.get("view", "schematic"), project.get("settings", {}))
    print(skill_path)


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
    if should_delegate_to_core(cmd0):
        raise SystemExit(delegate_to_core(argv))

    parser = argparse.ArgumentParser(description="AmpSys headless runner")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("build-library", "optimize", "writeback"):
        p = sub.add_parser(name)
        p.add_argument("--project", required=True)
    sub.add_parser("self-test")
    args = parser.parse_args(argv)
    project_path = Path(args.project).resolve() if hasattr(args, "project") else None
    if args.cmd == "build-library":
        assert project_path is not None
        run_build_library(project_path)
    elif args.cmd == "optimize":
        assert project_path is not None
        run_optimize(project_path)
    elif args.cmd == "writeback":
        assert project_path is not None
        run_writeback(project_path)
    elif args.cmd == "self-test":
        run_self_test()


if __name__ == "__main__":
    main()
