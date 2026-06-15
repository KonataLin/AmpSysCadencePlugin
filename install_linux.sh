#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REQUESTED_ROOT="${1:-${AMPSYS_INSTALL_ROOT:-/opt/AmpSysCadencePlugin}}"
ENGINE_ROOT_ARG="${2:-}"
CDSINIT="${3:-$HOME/.cdsinit}"

warn() { printf '[AmpSys][WARN] %s\n' "$*" >&2; }
die() { printf '[AmpSys][ERROR] %s\n' "$*" >&2; exit 1; }

abs_path() {
  local p="$1"
  mkdir -p "$(dirname "$p")" 2>/dev/null || true
  if command -v realpath >/dev/null 2>&1; then
    realpath -m "$p"
  else
    python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$p"
  fi
}

can_write_target() {
  local target="$1"
  local probe="$target"
  while [[ ! -e "$probe" && "$probe" != "/" ]]; do
    probe="$(dirname "$probe")"
  done
  [[ -w "$probe" ]]
}

PLUGIN_ROOT="$(abs_path "$REQUESTED_ROOT")"
if ! can_write_target "$PLUGIN_ROOT"; then
  if [[ "${1:-}" != "" ]]; then
    die "Cannot write to $PLUGIN_ROOT. Run with sudo for this target, or choose a user-writable path, e.g. bash install_linux.sh \"\$HOME/.local/share/AmpSysCadencePlugin\""
  fi
  PLUGIN_ROOT="$(abs_path "${XDG_DATA_HOME:-$HOME/.local/share}/AmpSysCadencePlugin")"
  warn "Default /opt install is not writable; using user install: $PLUGIN_ROOT"
fi

ENGINE_ROOT="${ENGINE_ROOT_ARG:-$PLUGIN_ROOT}"
ENGINE_ROOT="$(abs_path "$ENGINE_ROOT")"

mkdir -p "$PLUGIN_ROOT" "$ENGINE_ROOT" "$HOME/bin"

copy_payload() {
  local src="$1"
  local dst="$2"
  if [[ "$src" == "$dst" ]]; then
    return 0
  fi
  mkdir -p "$dst"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a \
      --exclude='.git/' \
      --exclude='workspace/' \
      --exclude='*.log' \
      --exclude='__pycache__/' \
      "$src/" "$dst/"
  else
    (cd "$src" && tar \
      --exclude='./.git' \
      --exclude='./workspace' \
      --exclude='*.log' \
      --exclude='__pycache__' \
      -cf - .) | (cd "$dst" && tar -xf -)
  fi
}

copy_payload "$SCRIPT_DIR" "$PLUGIN_ROOT"

LINUX_CORE_ARCHIVE="$PLUGIN_ROOT/core/linux_x86_64.tar.gz"
LINUX_CORE_DIR="$PLUGIN_ROOT/core/linux_x86_64"
if [[ ! -x "$LINUX_CORE_DIR/ampsys_core/ampsys_core" && ! -x "$LINUX_CORE_DIR/ampsys_core" && -f "$LINUX_CORE_ARCHIVE" ]]; then
  mkdir -p "$PLUGIN_ROOT/core"
  rm -rf "$LINUX_CORE_DIR"
  tar -xzf "$LINUX_CORE_ARCHIVE" -C "$PLUGIN_ROOT/core"
fi

CORE_BIN=""
for candidate in \
  "$LINUX_CORE_DIR/ampsys_core/ampsys_core" \
  "$LINUX_CORE_DIR/ampsys_core"; do
  if [[ -f "$candidate" ]]; then
    CORE_BIN="$candidate"
    chmod +x "$CORE_BIN" || true
    break
  fi
done

GUI_EXE=""
for candidate in \
  "$PLUGIN_ROOT/gui/linux_x86_64/ampsys_gui/ampsys_gui" \
  "$PLUGIN_ROOT/gui/linux_x86_64/ampsys_gui"; do
  if [[ -f "$candidate" ]]; then
    GUI_EXE="$candidate"
    chmod +x "$GUI_EXE" || true
    break
  fi
done

for path in "$PLUGIN_ROOT/cli" "$PLUGIN_ROOT/skill" "$PLUGIN_ROOT/tools" "$PLUGIN_ROOT/assets" "$PLUGIN_ROOT/core"; do
  [[ -e "$path" ]] && chmod -R a+rX "$path" || true
done
[[ -n "$GUI_EXE" && -d "$PLUGIN_ROOT/gui" ]] && chmod -R a+rX "$PLUGIN_ROOT/gui" || true
chmod a+rx "$PLUGIN_ROOT" || true
mkdir -p "$PLUGIN_ROOT/workspace"
chmod -R a+rwX "$PLUGIN_ROOT/workspace" || true
touch "$PLUGIN_ROOT/ampsys_environment.log" 2>/dev/null || true
chmod a+rw "$PLUGIN_ROOT/ampsys_environment.log" 2>/dev/null || true

python_ok() {
  local candidate="${1:-}"
  [[ -z "$candidate" ]] && return 1
  if [[ "$candidate" == */* ]]; then
    [[ -x "$candidate" ]] || return 1
  else
    command -v "$candidate" >/dev/null 2>&1 || return 1
  fi
  "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1
}

INSTALL_PYTHON=""
for candidate in \
  "${AMPSYS_PYTHON3:-}" \
  "${CONDA_PREFIX:-}/bin/python3" \
  "$HOME/miniconda3/bin/python3" \
  "$HOME/anaconda3/bin/python3" \
  "$(command -v python3.12 2>/dev/null || true)" \
  "$(command -v python3.11 2>/dev/null || true)" \
  "$(command -v python3.10 2>/dev/null || true)" \
  "$(command -v python3.9 2>/dev/null || true)" \
  "$(command -v python3.8 2>/dev/null || true)" \
  "$(command -v python3 2>/dev/null || true)"; do
  if python_ok "$candidate"; then
    INSTALL_PYTHON="$candidate"
    break
  fi
done

PY_SHIM="$HOME/bin/py"
cat > "$PY_SHIM" <<SHIM
#!/usr/bin/env bash
if [[ "\${1:-}" == "-3" ]]; then
  shift
fi
AMPSYS_INSTALL_PYTHON="$INSTALL_PYTHON"
python_ok() {
  local candidate="\${1:-}"
  [[ -z "\$candidate" ]] && return 1
  if [[ "\$candidate" == */* ]]; then
    [[ -x "\$candidate" ]] || return 1
  else
    command -v "\$candidate" >/dev/null 2>&1 || return 1
  fi
  "\$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1
}
for candidate in \
  "\${AMPSYS_PYTHON3:-}" \
  "\${CONDA_PREFIX:-}/bin/python3" \
  "\$HOME/miniconda3/bin/python3" \
  "\$HOME/anaconda3/bin/python3" \
  "\$AMPSYS_INSTALL_PYTHON" \
  python3.12 \
  python3.11 \
  python3.10 \
  python3.9 \
  python3.8 \
  python3; do
  if python_ok "\$candidate"; then
    exec "\$candidate" "\$@"
  fi
done
echo "AmpSys requires Python >= 3.8. Set AMPSYS_PYTHON3 to a valid python3 executable." >&2
exit 127
SHIM
chmod +x "$PY_SHIM"

write_launcher() {
  local path="$1"
  local body="$2"
  printf '%s\n' "$body" > "$path"
  chmod +x "$path"
}

write_launcher "$HOME/bin/ampsys-runner" "#!/usr/bin/env bash
export AMPSYS_PLUGIN_ROOT=\"$PLUGIN_ROOT\"
export AMPSYS_ENGINE_ROOT=\"$ENGINE_ROOT\"
exec \"$PY_SHIM\" -3 \"$PLUGIN_ROOT/cli/ampsys_runner.py\" \"\$@\""

if [[ -n "$GUI_EXE" ]]; then
  write_launcher "$HOME/bin/ampsys-gui" "#!/usr/bin/env bash
export AMPSYS_PLUGIN_ROOT=\"$PLUGIN_ROOT\"
export AMPSYS_ENGINE_ROOT=\"$ENGINE_ROOT\"
export AMPSYS_GUI_EXE=\"$GUI_EXE\"
exec \"$GUI_EXE\" \"\$@\""
else
  write_launcher "$HOME/bin/ampsys-gui" "#!/usr/bin/env bash
export AMPSYS_PLUGIN_ROOT=\"$PLUGIN_ROOT\"
export AMPSYS_ENGINE_ROOT=\"$ENGINE_ROOT\"
exec \"$PY_SHIM\" -3 \"$PLUGIN_ROOT/gui/ampsys_gui.py\" \"\$@\""
fi

write_launcher "$HOME/bin/ampsys-check" "#!/usr/bin/env bash
export AMPSYS_PLUGIN_ROOT=\"$PLUGIN_ROOT\"
export AMPSYS_ENGINE_ROOT=\"$ENGINE_ROOT\"
exec \"$PY_SHIM\" -3 \"$PLUGIN_ROOT/tools/check_environment.py\" \"\$@\""

export AMPSYS_PLUGIN_ROOT="$PLUGIN_ROOT"
export AMPSYS_ENGINE_ROOT="$ENGINE_ROOT"
export AMPSYS_PYCMD="$PY_SHIM -3"
[[ -n "$GUI_EXE" ]] && export AMPSYS_GUI_EXE="$GUI_EXE"
[[ -n "$INSTALL_PYTHON" ]] && export AMPSYS_PYTHON3="$INSTALL_PYTHON"
export PATH="$HOME/bin:$PATH"

touch "$CDSINIT"
if ! grep -q "ampsys_init.il" "$CDSINIT" 2>/dev/null; then
  cat >> "$CDSINIT" <<'CDS'

; --- AmpSys Cadence Plugin ---
load(strcat(getShellEnvVar("AMPSYS_PLUGIN_ROOT") "/skill/ampsys_init.il"))
CDS
fi

BASHRC="$HOME/.bashrc"
touch "$BASHRC"
bashrc_set_export() {
  local key="$1"
  local value="$2"
  local escaped="${value//\\/\\\\}"
  escaped="${escaped//&/\\&}"
  if grep -qE "^export ${key}=" "$BASHRC" 2>/dev/null; then
    sed -i "s|^export ${key}=.*|export ${key}=\"${escaped}\"|" "$BASHRC"
  else
    printf 'export %s="%s"\n' "$key" "$value" >> "$BASHRC"
  fi
}

if ! grep -q "AmpSys Cadence Plugin" "$BASHRC" 2>/dev/null; then
  printf '\n# AmpSys Cadence Plugin\n' >> "$BASHRC"
fi
bashrc_set_export "AMPSYS_PLUGIN_ROOT" "$PLUGIN_ROOT"
bashrc_set_export "AMPSYS_ENGINE_ROOT" "$ENGINE_ROOT"
bashrc_set_export "AMPSYS_PYCMD" "$PY_SHIM -3"
[[ -n "$GUI_EXE" ]] && bashrc_set_export "AMPSYS_GUI_EXE" "$GUI_EXE"
[[ -n "$INSTALL_PYTHON" ]] && bashrc_set_export "AMPSYS_PYTHON3" "$INSTALL_PYTHON"
if ! grep -q 'HOME/bin' "$BASHRC" 2>/dev/null; then
  printf 'export PATH="$HOME/bin:$PATH"\n' >> "$BASHRC"
fi

echo "[AmpSys] Linux install complete"
echo "  Plugin: $PLUGIN_ROOT"
echo "  Engine: $ENGINE_ROOT"
echo "  .cdsinit: $CDSINIT"
echo "  Workspace: $PLUGIN_ROOT/workspace"
echo "  Python command in Cadence: $PY_SHIM -3"
echo "  CLI: $HOME/bin/ampsys-runner"
echo "  GUI: $HOME/bin/ampsys-gui"
echo "  Environment check: $HOME/bin/ampsys-check"
if [[ -n "$CORE_BIN" ]]; then
  echo "  Core: $CORE_BIN"
else
  warn "Linux core binary was not found; release package may be incomplete for this architecture."
fi
echo "  Current shell refresh: source ~/.bashrc"
