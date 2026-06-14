#!/usr/bin/env python3
"""Build a hardened AmpSys Cadence Plugin release.

Published files stay readable for GUI/SKILL/installers.  Private AmpSys engine
packages are compiled into a standalone native core executable, so users do not
need Python-version-specific .pyd/.so engine directories.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
import platform
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ENGINE_PACKAGES = ("AmpSys", "yami", "TheScanner", "acsolver")
NUMBA_DECORATOR_RE = re.compile(
    r"^(\s*)@(numba\.)?(njit|jit|vectorize|guvectorize|generated_jit|cfunc)\b.*$"
)
OPEN_ITEMS = (
    "cli",
    "skill",
    "assets",
    "tools",
    ".gitignore",
    "install_windows.ps1",
    "install_linux.sh",
    "requirements_runtime.txt",
    "README.md",
    "Usage.md",
    "Install.md",
)

RELEASE_EXCLUDED_MODULES = (
    "yami.autoflow.cosim",
)

COSIM_RELEASE_ERROR = (
    "cosim is not included in the AmpSys Cadence Plugin release. "
    "This release supports LUT build, optimization, and Cadence writeback only."
)


def resolve_engine_root() -> Path:
    for candidate in (PLUGIN_ROOT.parent, PLUGIN_ROOT):
        if all((candidate / pkg).exists() for pkg in ENGINE_PACKAGES):
            return candidate
    return PLUGIN_ROOT.parent


ENGINE_ROOT = resolve_engine_root()


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


def binary_platform_tag() -> str:
    return f"{os_name()}_{normalized_arch()}"


def python_platform_tag() -> str:
    return f"{binary_platform_tag()}_py{sys.version_info.major}{sys.version_info.minor}"


def core_executable_name() -> str:
    return "ampsys_core.exe" if sys.platform.startswith("win") else "ampsys_core"


def run(cmd: List[str], cwd: Path | None = None, env: Dict[str, str] | None = None) -> None:
    print("+", " ".join(str(x) for x in cmd))
    subprocess.check_call(cmd, cwd=str(cwd) if cwd else None, env=env)


def temporary_build_directory(prefix: str) -> tempfile.TemporaryDirectory:
    if sys.platform.startswith("win"):
        candidates = [
            os.environ.get("AMPSYS_BUILD_TMP", ""),
            str(PLUGIN_ROOT.parent / ".ampsys_build_tmp"),
        ]
        for text in candidates:
            if not text:
                continue
            try:
                parent = Path(text)
                parent.mkdir(parents=True, exist_ok=True)
                return tempfile.TemporaryDirectory(prefix=prefix, dir=str(parent))
            except Exception:
                continue
    return tempfile.TemporaryDirectory(prefix=prefix)


def nuitka_available() -> bool:
    try:
        subprocess.check_output([sys.executable, "-m", "nuitka", "--version"], text=True, errors="ignore")
        return True
    except Exception:
        return False


def pyinstaller_available() -> bool:
    try:
        subprocess.check_output([sys.executable, "-m", "PyInstaller", "--version"], text=True, errors="ignore")
        return True
    except Exception:
        return False


def cython_available() -> bool:
    try:
        subprocess.check_output([sys.executable, "-c", "import Cython"], text=True, errors="ignore", stderr=subprocess.STDOUT)
        return True
    except Exception:
        return False


def pyarmor_cmd() -> List[str]:
    exe = shutil.which("pyarmor")
    if exe:
        return [exe]
    scripts = Path(sysconfig.get_path("scripts") or "")
    for name in ("pyarmor.exe", "pyarmor", "pyarmor-8.exe", "pyarmor-8"):
        cand = scripts / name
        if cand.exists():
            return [str(cand)]
    return []


def copy_open_items(dst: Path) -> None:
    for item in OPEN_ITEMS:
        src = PLUGIN_ROOT / item
        if not src.exists():
            continue
        target = dst / item
        if src.resolve() == target.resolve():
            continue
        if src.is_dir():
            ignore = shutil.ignore_patterns(
                "__pycache__",
                "*.pyc",
                "telemetry.jsonl",
                "result.json",
                "*.request",
                "workspace",
                "libraries",
            )
            shutil.copytree(src, target, ignore=ignore, dirs_exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)


def clean_release_outputs(dst: Path) -> None:
    for name in ("core", "engines"):
        target = dst / name
        if target.exists():
            shutil.rmtree(target)
    for pattern in ("*.build", "*.dist", "*.onefile-build"):
        for item in dst.glob(pattern):
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()


class DocstringStripper(ast.NodeTransformer):
    def _strip_body(self, node):
        self.generic_visit(node)
        body = getattr(node, "body", None)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(getattr(body[0], "value", None), ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
        return node

    def visit_Module(self, node: ast.Module):  # type: ignore[override]
        return self._strip_body(node)

    def visit_ClassDef(self, node: ast.ClassDef):  # type: ignore[override]
        return self._strip_body(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):  # type: ignore[override]
        return self._strip_body(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):  # type: ignore[override]
        return self._strip_body(node)


def strip_docstrings(path: Path) -> bool:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        tree = DocstringStripper().visit(tree)
        ast.fix_missing_locations(tree)
        path.write_text(ast.unparse(tree) + "\n", encoding="utf-8")
        return True
    except Exception as exc:
        print(f"[AmpSys] Warning: docstring strip skipped for {path}: {exc}")
        return False


def private_ignore_patterns(pkg: str):
    patterns = ["__pycache__", "*.pyc", ".git", "*.log", "*.raw", "*.lis"]
    if pkg == "yami":
        patterns.append("cosim.py")
    return shutil.ignore_patterns(*patterns)


def make_writable(path: Path) -> None:
    try:
        path.chmod(0o700 if path.is_dir() else 0o600)
    except Exception:
        pass


def safe_unlink(path: Path) -> None:
    if not path.exists():
        return
    make_writable(path)
    path.unlink()


def safe_rmtree(path: Path) -> None:
    if not path.exists():
        return

    def on_error(_func, failed_path, _exc_info):
        failed = Path(failed_path)
        make_writable(failed)
        if failed.is_dir():
            failed.rmdir()
        else:
            failed.unlink()

    shutil.rmtree(path, onerror=on_error)


def remove_cosim_from_init(init_path: Path) -> None:
    if not init_path.is_file():
        return
    text = init_path.read_text(encoding="utf-8", errors="ignore")
    text = re.sub(r"(?m)^\s*from\s+\.cosim\s+import\s+.*\n", "", text)
    text = re.sub(r"(?m)^\s*['\"]run_cosim['\"],\s*\n", "", text)
    text = re.sub(r"(?m)^\s*['\"]cosim_from_result['\"],\s*\n", "", text)
    init_path.write_text(text, encoding="utf-8")


def replace_cosim_method(source_path: Path, import_line: str) -> None:
    if not source_path.is_file():
        return
    text = source_path.read_text(encoding="utf-8", errors="ignore")
    if import_line not in text:
        return
    text = text.replace(
        import_line,
        f"        raise RuntimeError({COSIM_RELEASE_ERROR!r})\n",
        1,
    )
    source_path.write_text(text, encoding="utf-8")


def prune_release_only_private_modules(private_root: Path) -> None:
    cosim_py = private_root / "yami" / "autoflow" / "cosim.py"
    if cosim_py.exists():
        safe_unlink(cosim_py)
    remove_cosim_from_init(private_root / "yami" / "autoflow" / "__init__.py")
    replace_cosim_method(
        private_root / "yami" / "autoflow" / "flow.py",
        "        from .cosim import run_cosim\n",
    )
    replace_cosim_method(
        private_root / "AmpSys" / "engine" / "flow.py",
        "        from yami.autoflow.cosim import run_cosim\n",
    )


def minimize_package_init_sources(private_root: Path) -> None:
    """Keep package entrypoints importable without shipping descriptive source text."""
    init_exports = {
        "AmpSys/__init__.py": (
            "from .core.intent import MosIntent, PassiveIntent\n"
            "from .core.validator import KCLValidator\n"
            "from .engine.flow import AmpFlow, AmpFlowConfig\n"
            "__all__ = ['AmpFlow', 'AmpFlowConfig', 'MosIntent', 'PassiveIntent', 'KCLValidator']\n"
        ),
        "AmpSys/core/__init__.py": "",
        "AmpSys/engine/__init__.py": (
            "from .flow import AmpFlow, AmpFlowConfig\n"
            "__all__ = ['AmpFlow', 'AmpFlowConfig']\n"
        ),
        "TheScanner/__init__.py": (
            "from .config import ScannerConfig\n"
            "from .netlist_generator import HSPICENetlistGenerator\n"
            "from .simulator import HSPICESimulator, HSPICEError, SweepResult\n"
            "from .scanner import MOSScanner, run_scan\n"
            "from .database import MOSDatabase, save_db, load_db, export_to_numpy, export_to_csv\n"
            "from .database import K_BOLTZMANN, Q_ELECTRON, set_temp_dir, get_temp_dir\n"
            "from .lookup import query, Lookup, design_transistor, lookup_vgs, extract_ekv_params\n"
            "from .lookup import HSPICECache, get_hspice_cache, run_parallel_hspice, run_data_batch_hspice\n"
            "from .lookup import set_fast_mode, is_fast_mode\n"
            "from .batch_manager import BatchHSPICEManager, BatchTimeoutError, BatchSimulationError, batch_query\n"
        ),
        "yami/__init__.py": "",
        "yami/autoflow/__init__.py": (
            "from .tags import Tag, Device, is_anchor_tag, is_input_tag, is_passive_tag, get_default_current_ratio\n"
            "from .topology import CircuitTopology, DependencySolver\n"
            "from .flow import AutoFlow, AutoFlowConfig\n"
        ),
        "acsolver/__init__.py": (
            "from .api.circuit_analyzer import CircuitAnalyzer, AnalysisConfig, AnalysisResult\n"
            "from .core.symbols import SymbolManager\n"
            "from .core.nodes import Node, NodeManager\n"
            "from .core.components import Component, Resistor, Capacitor, Inductor, MOSFET, MOSFETConfig\n"
            "from .parser.netlist_parser import NetlistParser\n"
            "from .solver.mna_builder import MNABuilder\n"
            "from .solver.equation_solver import EquationSolver\n"
            "from .solver.transfer_function import TransferFunctionSolver\n"
            "from .solver.noise_analyzer import NoiseAnalyzer\n"
            "from .core.noise import NoiseSource, NoiseResult\n"
            "from .output.latex_formatter import LaTeXFormatter\n"
            "__all__ = ['CircuitAnalyzer', 'AnalysisConfig', 'AnalysisResult', 'SymbolManager', 'Node', 'NodeManager', 'Component', 'Resistor', 'Capacitor', 'Inductor', 'MOSFET', 'MOSFETConfig', 'NetlistParser', 'MNABuilder', 'EquationSolver', 'TransferFunctionSolver', 'NoiseAnalyzer', 'NoiseSource', 'NoiseResult', 'LaTeXFormatter']\n"
        ),
    }
    for rel, text in init_exports.items():
        path = private_root / rel
        if path.is_file():
            path.write_text(text, encoding="utf-8")


def prepare_private_stage(stage: Path, harden_source: bool = True) -> Path:
    work = stage / "work"
    private_root = work / "private"
    cli_root = work / "cli"
    private_root.mkdir(parents=True, exist_ok=True)
    cli_root.mkdir(parents=True, exist_ok=True)

    for pkg in ENGINE_PACKAGES:
        src = ENGINE_ROOT / pkg
        if not src.exists():
            raise FileNotFoundError(f"Private package not found: {src}")
        shutil.copytree(
            src,
            private_root / pkg,
            ignore=private_ignore_patterns(pkg),
            dirs_exist_ok=True,
        )
    prune_release_only_private_modules(private_root)
    minimize_package_init_sources(private_root)
    for name in ("ampsys_core.py", "ampsys_runner.py", "ampsys_netlist.py"):
        shutil.copy2(PLUGIN_ROOT / "cli" / name, cli_root / name)

    if harden_source:
        for py in private_root.rglob("*.py"):
            strip_docstrings(py)
        for py in cli_root.rglob("*.py"):
            strip_docstrings(py)
    return work


def strip_native_binary(path: Path) -> None:
    if sys.platform.startswith("linux"):
        strip = shutil.which("strip")
        if strip and path.exists():
            try:
                run([strip, "--strip-all", str(path)])
            except subprocess.CalledProcessError:
                print(f"[AmpSys] Warning: strip failed for {path}")


def assert_release_excludes_absent(root: Path) -> None:
    leaked = [
        path
        for path in root.rglob("*")
        if any(part.lower().startswith("cosim") for part in path.parts)
    ]
    if leaked:
        details = "\n".join(str(path) for path in leaked[:20])
        raise SystemExit(f"Release-only excluded module leaked into build output:\n{details}")


def maybe_external_protect(binary: Path, protector: str) -> str:
    if not protector:
        return ""
    template = protector.replace("{input}", str(binary)).replace("{output}", str(binary))
    cmd = template if sys.platform.startswith("win") else template
    try:
        subprocess.check_call(cmd, shell=True)
        return protector
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"External protector failed: {exc}") from exc


def build_private_binaries_with_nuitka(private_root: Path, dst: Path, jobs: int) -> None:
    if not nuitka_available():
        raise SystemExit(
            "Nuitka is required to compile private packages.\n"
            "Install builder dependencies:\n"
            "  py -3 -m pip install nuitka ordered-set zstandard pyinstaller"
        )
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)
    for pkg in ENGINE_PACKAGES:
        run([
            sys.executable,
            "-m",
            "nuitka",
            "--mode=package",
            str(private_root / pkg),
            f"--output-dir={dst}",
            "--remove-output",
            "--python-flag=no_docstrings",
            "--no-pyi-file",
            f"--jobs={jobs}",
        ])


def private_binary_files(compiled_root: Path) -> List[Path]:
    suffixes = (".pyd", ".so", ".dll", ".dylib")
    found: List[Path] = []
    for pkg in ENGINE_PACKAGES:
        matches = [p for p in compiled_root.glob(f"{pkg}.*") if p.suffix.lower() in suffixes]
        if not matches:
            raise FileNotFoundError(f"Compiled private binary not found for {pkg} in {compiled_root}")
        found.append(matches[0])
    return found


def private_module_sources(private_root: Path, exclude: Optional[Set[Path]] = None) -> List[Path]:
    exclude = {p.resolve() for p in (exclude or set())}
    sources: List[Path] = []
    for py in private_root.rglob("*.py"):
        parts = set(py.relative_to(private_root).parts)
        if "__pycache__" in parts or "examples" in parts:
            continue
        if py.name == "__init__.py":
            continue
        if py.resolve() in exclude:
            continue
        sources.append(py)
    return sorted(sources)


def module_name_for_source(private_root: Path, py: Path) -> str:
    return ".".join(py.relative_to(private_root).with_suffix("").parts)


def private_module_names(private_root: Path, sources: Optional[List[Path]] = None) -> List[str]:
    modules: List[str] = []
    for py in sources if sources is not None else private_module_sources(private_root):
        modules.append(module_name_for_source(private_root, py))
    return modules


def numba_decorator_sources(private_root: Path) -> List[Path]:
    sources: List[Path] = []
    for py in private_module_sources(private_root):
        text = py.read_text(encoding="utf-8", errors="ignore")
        if any(NUMBA_DECORATOR_RE.match(line.rstrip("\r\n")) for line in text.splitlines()):
            sources.append(py)
    return sorted(sources)


def prune_private_sources_after_cython(private_root: Path, keep_py: Optional[Set[Path]] = None) -> None:
    keep_py = {p.resolve() for p in (keep_py or set())}
    for junk in [
        private_root / "AmpSys" / "examples",
        private_root / "AmpSys" / "API.md",
        private_root / "build",
    ]:
        if junk.is_dir():
            shutil.rmtree(junk, ignore_errors=True)
        elif junk.exists():
            junk.unlink()
    for path in private_root.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".c", ".cpp", ".html", ".pyx"}:
            path.unlink()
    for py in private_root.rglob("*.py"):
        if py.name != "__init__.py":
            if py.resolve() in keep_py:
                continue
            py.unlink()
    for cache in private_root.rglob("__pycache__"):
        if cache.is_dir():
            shutil.rmtree(cache, ignore_errors=True)


def build_private_binaries_with_cython(private_root: Path, jobs: int) -> List[str]:
    if not cython_available():
        raise SystemExit(
            "Cython is required for cython-core protection.\n"
            "Install builder dependencies:\n"
            "  py -3 -m pip install Cython wheel pyinstaller\n"
            "  python3 -m pip install --user Cython wheel pyinstaller"
        )
    py_only_sources = numba_decorator_sources(private_root)
    if py_only_sources:
        print(
            "[AmpSys] Keeping numba JIT modules as frozen bytecode: "
            + ", ".join(module_name_for_source(private_root, py) for py in py_only_sources)
        )
    sources = private_module_sources(private_root, exclude=set(py_only_sources))
    if not sources:
        raise SystemExit(f"No private Python modules found under {private_root}")
    modules = private_module_names(private_root, sources + py_only_sources)
    setup_py = private_root / "_ampsys_cython_build.py"
    cython_build_dir = Path(os.environ.get("AMPSYS_CYTHON_BUILD_DIR", "") or (private_root / "_cython_c")).resolve()
    cython_build_dir.mkdir(parents=True, exist_ok=True)
    build_temp = Path(os.environ.get("AMPSYS_CYTHON_BUILD_TEMP", "") or (private_root / "_build_temp")).resolve()
    build_temp.mkdir(parents=True, exist_ok=True)
    source_literals = ",\n    ".join(repr(str(p)) for p in sources)
    cython_threads = 0 if sys.platform.startswith("win") else max(1, jobs)
    setup_py.write_text(
        f"""
from pathlib import Path
from setuptools import Extension, setup
from Cython.Build import cythonize

private_root = Path({str(private_root)!r})
sources = [
    {source_literals}
]
extensions = []
for src in sources:
    path = Path(src)
    module = ".".join(path.relative_to(private_root).with_suffix("").parts)
    extensions.append(Extension(module, [str(path)]))

setup(
    name="ampsys_private_core",
    ext_modules=cythonize(
        extensions,
        nthreads={cython_threads},
        compiler_directives={{
            "language_level": "3",
            "binding": False,
            "embedsignature": False,
            "emit_code_comments": False,
            "annotation_typing": False,
        }},
        build_dir={str(cython_build_dir)!r},
    ),
)
""",
        encoding="utf-8",
    )
    build_env = os.environ.copy()
    if not sys.platform.startswith("win"):
        cflags = build_env.get("CFLAGS", "").strip()
        if "-std=" not in cflags:
            build_env["CFLAGS"] = (cflags + " -std=gnu99").strip()
        cxxflags = build_env.get("CXXFLAGS", "").strip()
        if "-std=" not in cxxflags:
            build_env["CXXFLAGS"] = (cxxflags + " -std=gnu++11").strip()
    try:
        run([sys.executable, str(setup_py), "build_ext", "--inplace", "--build-temp", str(build_temp)], cwd=private_root, env=build_env)
    finally:
        if setup_py.exists():
            setup_py.unlink()
    for ext in private_root.rglob("*"):
        if ext.is_file() and ext.suffix.lower() in (".pyd", ".so", ".dll", ".dylib"):
            strip_native_binary(ext)
    prune_private_sources_after_cython(private_root, keep_py=set(py_only_sources))
    return modules


def scipy_distn_runtime_patch(tmp_root: Path) -> Optional[Tuple[Path, Path]]:
    """Patch a SciPy/PyInstaller edge case without touching AmpSys sources."""
    spec = importlib.util.find_spec("scipy.stats._distn_infrastructure")
    if spec is None or not spec.origin:
        return None
    source_path = Path(spec.origin)
    if not source_path.is_file():
        return None

    source = source_path.read_text(encoding="utf-8")
    patched = source.replace("del obj\n", "globals().pop('obj', None)\n", 1)
    if patched == source:
        return None

    compat_dir = tmp_root / "runtime_compat"
    data_dir = compat_dir / "ampsys_compat"
    data_dir.mkdir(parents=True, exist_ok=True)
    patched_source = data_dir / "scipy_stats_distn_infrastructure.py"
    patched_source.write_text(patched, encoding="utf-8")

    hook = compat_dir / "pyi_rth_scipy_distn_patch.py"
    hook.write_text(
        """
import importlib.abc
import importlib.util
import os
import sys

_PATCH_FILE = os.path.join(
    getattr(sys, "_MEIPASS", os.path.dirname(__file__)),
    "ampsys_compat",
    "scipy_stats_distn_infrastructure.py",
)


class _AmpSysScipyDistnPatchLoader(importlib.abc.Loader):
    def create_module(self, spec):
        return None

    def exec_module(self, module):
        module.__file__ = _PATCH_FILE
        module.__loader__ = self
        module.__package__ = "scipy.stats"
        with open(_PATCH_FILE, "rb") as stream:
            code = compile(stream.read(), _PATCH_FILE, "exec")
        exec(code, module.__dict__)


class _AmpSysScipyDistnPatchFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname != "scipy.stats._distn_infrastructure":
            return None
        if not os.path.isfile(_PATCH_FILE):
            return None
        loader = _AmpSysScipyDistnPatchLoader()
        return importlib.util.spec_from_file_location(fullname, _PATCH_FILE, loader=loader)


sys.meta_path.insert(0, _AmpSysScipyDistnPatchFinder())
""".lstrip(),
        encoding="utf-8",
    )
    return hook, patched_source


def build_cython_core(dst: Path, jobs: int, onefile: bool, harden_source: bool, protector: str) -> str:
    if not pyinstaller_available():
        raise SystemExit(
            "PyInstaller is required for cython-core.\n"
            "Install builder dependencies:\n"
            "  py -3 -m pip install pyinstaller Cython wheel"
        )

    core_dir = dst / "core" / binary_platform_tag()
    if core_dir.exists():
        shutil.rmtree(core_dir)
    core_dir.mkdir(parents=True, exist_ok=True)

    with temporary_build_directory(prefix="ampsys_cython_") as tmp:
        tmp_root = Path(tmp)
        stage = prepare_private_stage(tmp_root, harden_source=harden_source)
        cli_root = stage / "cli"
        private_root = stage / "private"
        compiled_modules = build_private_binaries_with_cython(private_root, jobs=jobs)

        bin_sep = ";" if sys.platform.startswith("win") else ":"
        env = os.environ.copy()
        paths = [str(cli_root), str(private_root)]
        env["PYTHONPATH"] = os.pathsep.join(paths + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
        env.setdefault("AMPSYS_NUMBA_CACHE", "0")
        scipy_patch = scipy_distn_runtime_patch(tmp_root)

        hidden_imports = [
            "AmpSys",
            "yami",
            "TheScanner",
            "acsolver",
            "numpy",
            "scipy",
            "scipy.interpolate",
            "scipy.optimize",
            "scipy.signal",
            "sympy",
            "psutil",
            "numba",
            "llvmlite",
            *compiled_modules,
        ]
        excludes = [
            "cv2",
            "IPython",
            "jupyter",
            "lxml",
            "matplotlib",
            "notebook",
            "pandas",
            "PIL",
            "pytest",
            "sklearn",
            "setuptools.tests",
            "sqlalchemy",
            "tensorflow",
            "torch",
        "torchvision",
        "transformers",
        *RELEASE_EXCLUDED_MODULES,
    ]
        cmd = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--console",
            "--optimize",
            "1",
            "--name",
            "ampsys_core",
            "--distpath",
            str(core_dir),
            "--workpath",
            str(tmp_root / "pyinstaller_build"),
            "--specpath",
            str(tmp_root),
            "--paths",
            str(cli_root),
            "--paths",
            str(private_root),
        ]
        cmd.append("--onefile" if onefile else "--onedir")
        if scipy_patch is not None:
            hook, patched_source = scipy_patch
            cmd.extend(["--runtime-hook", str(hook)])
            cmd.extend(["--add-data", f"{patched_source}{bin_sep}ampsys_compat"])
        for item in private_root.rglob("*"):
            if item.is_file() and item.suffix.lower() in (".pyd", ".so", ".dll", ".dylib"):
                rel_parent = item.parent.relative_to(private_root)
                target = "." if str(rel_parent) == "." else str(rel_parent)
                cmd.extend(["--add-binary", f"{item}{bin_sep}{target}"])
        for init_py in private_root.rglob("__init__.py"):
            rel_parent = init_py.parent.relative_to(private_root)
            target = "." if str(rel_parent) == "." else str(rel_parent)
            cmd.extend(["--add-data", f"{init_py}{bin_sep}{target}"])
        for name in hidden_imports:
            cmd.extend(["--hidden-import", name])
        for name in excludes:
            cmd.extend(["--exclude-module", name])
        cmd.append(str(cli_root / "ampsys_core.py"))
        run(cmd, cwd=stage, env=env)

    binary = core_dir / core_executable_name()
    if not binary.exists() and not onefile:
        dist_binary = core_dir / "ampsys_core" / core_executable_name()
        if dist_binary.exists():
            binary = dist_binary
    strip_native_binary(binary)
    assert_release_excludes_absent(core_dir)
    protector_used = maybe_external_protect(binary, protector)
    return "cython-private+pyinstaller-onefile" + (f"+{protector_used}" if protector_used else "") if onefile else "cython-private+pyinstaller-onedir"


def build_hybrid_core(dst: Path, jobs: int, onefile: bool, harden_source: bool, protector: str) -> str:
    if not pyinstaller_available():
        raise SystemExit(
            "PyInstaller is required for hybrid hardened core.\n"
            "Install builder dependencies:\n"
            "  py -3 -m pip install pyinstaller nuitka ordered-set zstandard"
        )

    core_dir = dst / "core" / binary_platform_tag()
    if core_dir.exists():
        shutil.rmtree(core_dir)
    core_dir.mkdir(parents=True, exist_ok=True)

    with temporary_build_directory(prefix="ampsys_hybrid_") as tmp:
        tmp_root = Path(tmp)
        stage = prepare_private_stage(tmp_root, harden_source=harden_source)
        cli_root = stage / "cli"
        private_root = stage / "private"
        compiled_root = tmp_root / "compiled_private"
        build_private_binaries_with_nuitka(private_root, compiled_root, jobs=jobs)
        binaries = private_binary_files(compiled_root)

        bin_sep = ";" if sys.platform.startswith("win") else ":"
        env = os.environ.copy()
        paths = [str(cli_root), str(compiled_root)]
        env["PYTHONPATH"] = os.pathsep.join(paths + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
        env.setdefault("AMPSYS_NUMBA_CACHE", "0")

        hidden_imports = [
            "AmpSys",
            "yami",
            "yami.optimizer",
            "yami.objectives",
            "TheScanner",
            "acsolver",
            "numpy",
            "scipy",
            "scipy.interpolate",
            "scipy.optimize",
            "scipy.signal",
            "sympy",
            "psutil",
            "numba",
            "llvmlite",
        ]
        excludes = [
            "cv2",
            "IPython",
            "jupyter",
            "lxml",
            "matplotlib",
            "notebook",
            "pandas",
            "PIL",
            "pytest",
            "sklearn",
            "setuptools.tests",
            "sqlalchemy",
            "tensorflow",
            "torch",
            "torchvision",
            "transformers",
            *RELEASE_EXCLUDED_MODULES,
        ]
        cmd = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--console",
            "--name",
            "ampsys_core",
            "--distpath",
            str(core_dir),
            "--workpath",
            str(tmp_root / "pyinstaller_build"),
            "--specpath",
            str(tmp_root),
            "--paths",
            str(cli_root),
            "--paths",
            str(compiled_root),
        ]
        cmd.append("--onefile" if onefile else "--onedir")
        for item in binaries:
            cmd.extend(["--add-binary", f"{item}{bin_sep}."])
        for name in hidden_imports:
            cmd.extend(["--hidden-import", name])
        for name in excludes:
            cmd.extend(["--exclude-module", name])
        cmd.append(str(cli_root / "ampsys_core.py"))
        run(cmd, cwd=stage, env=env)

    binary = core_dir / core_executable_name()
    if not binary.exists() and not onefile:
        dist_binary = core_dir / "ampsys_core" / core_executable_name()
        if dist_binary.exists():
            binary = dist_binary
    strip_native_binary(binary)
    assert_release_excludes_absent(core_dir)
    protector_used = maybe_external_protect(binary, protector)
    return "hybrid-nuitka-private+pyinstaller-onefile" + (f"+{protector_used}" if protector_used else "") if onefile else "hybrid-nuitka-private+pyinstaller-onedir"


def build_core_with_nuitka(dst: Path, jobs: int, onefile: bool, harden_source: bool, protector: str) -> str:
    if not nuitka_available():
        raise SystemExit(
            "Nuitka is required for hardened standalone core.\n"
            "Install builder dependencies:\n"
            "  py -3 -m pip install nuitka ordered-set zstandard\n"
            "or on Linux:\n"
            "  python3 -m pip install --user nuitka ordered-set zstandard"
        )

    core_dir = dst / "core" / binary_platform_tag()
    if core_dir.exists():
        shutil.rmtree(core_dir)
    core_dir.mkdir(parents=True, exist_ok=True)

    with temporary_build_directory(prefix="ampsys_harden_") as tmp:
        stage = prepare_private_stage(Path(tmp), harden_source=harden_source)
        entry = stage / "cli" / "ampsys_core.py"
        env = os.environ.copy()
        paths = [str(stage / "cli"), str(stage / "private")]
        env["PYTHONPATH"] = os.pathsep.join(paths + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
        env.setdefault("AMPSYS_NUMBA_CACHE", "0")
        cmd = [
            sys.executable,
            "-m",
            "nuitka",
            "--standalone",
            str(entry),
            f"--output-dir={core_dir}",
            f"--output-filename={core_executable_name()}",
            "--remove-output",
            "--static-libpython=no",
            "--python-flag=no_docstrings",
            "--no-pyi-file",
            "--assume-yes-for-downloads",
            "--nofollow-import-to=AmpSys.examples",
            "--nofollow-import-to=yami.autoflow.cosim",
            "--nofollow-import-to=matplotlib",
            "--nofollow-import-to=pytest",
            f"--jobs={jobs}",
        ]
        if onefile:
            cmd.insert(4, "--onefile")
        for pkg in ENGINE_PACKAGES:
            cmd.append(f"--include-package={pkg}")
        run(cmd, cwd=stage, env=env)

    binary = core_dir / core_executable_name()
    if not binary.exists() and not onefile:
        dist_binary = core_dir / "ampsys_core.dist" / core_executable_name()
        if dist_binary.exists():
            binary = dist_binary
    strip_native_binary(binary)
    assert_release_excludes_absent(core_dir)
    protector_used = maybe_external_protect(binary, protector)
    return "nuitka-onefile-core" + (f"+{protector_used}" if protector_used else "") if onefile else "nuitka-standalone-core"


def clean_engine_outputs(dst: Path) -> None:
    for pkg in ENGINE_PACKAGES:
        target = dst / pkg
        if target.exists():
            shutil.rmtree(target)
        for ext in dst.glob(f"{pkg}.*"):
            if ext.is_file() and ext.suffix.lower() in (".pyd", ".so", ".dll", ".dylib"):
                ext.unlink()
    for runtime in dst.glob("pyarmor_runtime_*"):
        if runtime.is_dir():
            shutil.rmtree(runtime)
        else:
            runtime.unlink()


def build_packages_with_nuitka(dst: Path, jobs: int = 4) -> str:
    if not nuitka_available():
        return ""
    clean_engine_outputs(dst)
    with temporary_build_directory(prefix="ampsys_nuitka_package_") as tmp:
        work = prepare_private_stage(Path(tmp), harden_source=False)
        private_root = work / "private"
        for pkg in ENGINE_PACKAGES:
            run([
                sys.executable,
                "-m",
                "nuitka",
                "--mode=package",
                str(private_root / pkg),
                f"--output-dir={dst}",
                "--remove-output",
                "--no-pyi-file",
                f"--jobs={jobs}",
            ])
    assert_release_excludes_absent(dst)
    return "nuitka-package-legacy"


def pyarmor_help() -> str:
    cmd = pyarmor_cmd()
    if not cmd:
        return ""
    try:
        return subprocess.check_output([*cmd, "gen", "--help"], text=True, errors="ignore")
    except Exception:
        return ""


def supported_pyarmor_options(help_text: str) -> List[str]:
    candidates = [
        "--recursive",
        "--obf-module", "1",
        "--obf-code", "2",
        "--mix-str",
        "--assert-call",
        "--assert-import",
        "--enable-jit",
        "--enable-rft",
        "--private",
    ]
    out: List[str] = []
    i = 0
    while i < len(candidates):
        opt = candidates[i]
        if opt.startswith("--") and opt in help_text:
            out.append(opt)
            if i + 1 < len(candidates) and not candidates[i + 1].startswith("--"):
                out.append(candidates[i + 1])
                i += 1
        i += 1
    if "--recursive" not in out:
        out.insert(0, "--recursive")
    return out


def build_with_pyarmor(dst: Path) -> str:
    help_text = pyarmor_help()
    if not help_text:
        return ""
    with temporary_build_directory(prefix="ampsys_pyarmor_") as tmp:
        work = prepare_private_stage(Path(tmp), harden_source=False)
        package_paths = [str(work / "private" / pkg) for pkg in ENGINE_PACKAGES]
        tiers = [
            ("pyarmor-strong-legacy", supported_pyarmor_options(help_text)),
            ("pyarmor-standard-legacy", ["--recursive"]),
        ]
        for method, opts in tiers:
            clean_engine_outputs(dst)
            try:
                run([*pyarmor_cmd(), "gen", "-O", str(dst), *opts, *package_paths])
                assert_release_excludes_absent(dst)
                return method
            except subprocess.CalledProcessError:
                print(f"[AmpSys] {method} failed, trying next tier...")
    return ""


def write_manifest(dst: Path, method: str, protector: str) -> None:
    manifest_path = dst / "release_manifest.json"
    existing: Dict[str, object] = {}
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    core_dirs = set(existing.get("core_dirs", []) if isinstance(existing.get("core_dirs"), list) else [])
    core_dirs.add(str((Path("core") / binary_platform_tag()).as_posix()))
    payload = {
        "name": "AmpSysCadencePlugin",
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "method": method,
        "platform": binary_platform_tag(),
        "builder_python": python_platform_tag(),
        "core_dirs": sorted(core_dirs),
        "engine_packages": list(ENGINE_PACKAGES),
        "python_command": "py -3",
        "external_protector": protector or "",
        "notes": [
            "GUI/SKILL/CLI wrappers are readable.",
            "Private AmpSys engine packages are compiled into standalone native core binaries.",
            "The core binary is selected by OS/CPU, not by the user's Python minor version.",
            "For stronger commercial anti-tamper, run a VMProtect/Themida-style protector with --protector-cmd.",
        ],
    }
    manifest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build hardened AmpSys release")
    parser.add_argument("--output", default=str(PLUGIN_ROOT.parent / "AmpSysCadencePlugin_release"))
    parser.add_argument(
        "--method",
        choices=("auto", "cython-core", "hybrid-core", "nuitka-core", "nuitka-package", "pyarmor"),
        default="auto",
        help="auto defaults to cython-core: Cython private extensions inside a PyInstaller core executable.",
    )
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--core-mode", choices=("onefile", "standalone"), default="onefile")
    parser.add_argument("--source-harden", action="store_true", help="Rewrite temporary private sources before compilation. Experimental; default is off for compatibility.")
    parser.add_argument("--no-source-harden", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--protector-cmd", default="", help="Optional external protector command. Use {input} for the produced core binary path.")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    dst = Path(args.output).resolve()
    if args.clean and dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)
    if args.clean:
        clean_release_outputs(dst)

    copy_open_items(dst)
    method = ""
    if args.method in ("auto", "cython-core"):
        harden_source = bool(args.source_harden and not args.no_source_harden)
        method = build_cython_core(
            dst=dst,
            jobs=args.jobs,
            onefile=args.core_mode == "onefile",
            harden_source=harden_source,
            protector=args.protector_cmd,
        )
    elif args.method == "hybrid-core":
        harden_source = bool(args.source_harden and not args.no_source_harden)
        method = build_hybrid_core(
            dst=dst,
            jobs=args.jobs,
            onefile=args.core_mode == "onefile",
            harden_source=harden_source,
            protector=args.protector_cmd,
        )
    elif args.method == "nuitka-core":
        harden_source = bool(args.source_harden and not args.no_source_harden)
        method = build_core_with_nuitka(
            dst=dst,
            jobs=args.jobs,
            onefile=args.core_mode == "onefile",
            harden_source=harden_source,
            protector=args.protector_cmd,
        )
    elif args.method == "nuitka-package":
        engine_dst = dst / "engines" / python_platform_tag()
        engine_dst.mkdir(parents=True, exist_ok=True)
        method = build_packages_with_nuitka(engine_dst, jobs=args.jobs)
    elif args.method == "pyarmor":
        engine_dst = dst / "engines" / python_platform_tag()
        engine_dst.mkdir(parents=True, exist_ok=True)
        method = build_with_pyarmor(engine_dst)

    if not method:
        raise SystemExit("No release protection backend succeeded.")
    write_manifest(dst, method, args.protector_cmd)
    print(f"[AmpSys] Release written to {dst}")
    print(f"[AmpSys] Core dir: {dst / 'core' / binary_platform_tag()}")
    print(f"[AmpSys] Protection method: {method}")


if __name__ == "__main__":
    main()
