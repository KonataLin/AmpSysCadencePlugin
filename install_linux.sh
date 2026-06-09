#!/usr/bin/env bash
set -euo pipefail

PLUGIN_ROOT="${1:-/opt/AmpSysCadencePlugin}"
ENGINE_ROOT="${2:-$PLUGIN_ROOT}"
CDSINIT="${3:-$HOME/.cdsinit}"

mkdir -p "$(dirname "$PLUGIN_ROOT")" "$(dirname "$ENGINE_ROOT")" "$HOME/bin"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "$SCRIPT_DIR" != "$PLUGIN_ROOT" ]]; then
  mkdir -p "$PLUGIN_ROOT"
  cp -a "$SCRIPT_DIR/." "$PLUGIN_ROOT/"
fi

CORE_BIN="$PLUGIN_ROOT/core/linux_x86_64/ampsys_core"
if [[ -f "$CORE_BIN" ]]; then
  chmod +x "$CORE_BIN" || true
fi

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

cat > "$HOME/bin/py" <<SHIM
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
chmod +x "$HOME/bin/py"

export AMPSYS_PLUGIN_ROOT="$PLUGIN_ROOT"
export AMPSYS_ENGINE_ROOT="$ENGINE_ROOT"
export AMPSYS_PYCMD="py -3"
if [[ -n "$INSTALL_PYTHON" ]]; then
  export AMPSYS_PYTHON3="$INSTALL_PYTHON"
fi
export PATH="$HOME/bin:$PATH"

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
bashrc_set_export "AMPSYS_PYCMD" "py -3"
if [[ -n "$INSTALL_PYTHON" ]]; then
  bashrc_set_export "AMPSYS_PYTHON3" "$INSTALL_PYTHON"
fi
if ! grep -q 'HOME/bin' "$BASHRC" 2>/dev/null; then
  printf 'export PATH="$HOME/bin:$PATH"\n' >> "$BASHRC"
fi

echo "[AmpSys] Linux install complete"
echo "  Plugin: $PLUGIN_ROOT"
echo "  Engine: $ENGINE_ROOT"
echo "  .cdsinit: $CDSINIT"
echo "  Python command in Cadence: py -3"
echo "  Environment check: py -3 $PLUGIN_ROOT/tools/check_environment.py"
echo "  Current shell refresh: source ~/.bashrc"
echo "  Or temporary PATH: export PATH=\"\$HOME/bin:\$PATH\""
