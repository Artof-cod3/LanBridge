#!/usr/bin/env bash
# LanBridge installer for Debian 11/12/13 and Ubuntu 22.04/24.04.
#
#   sudo ./install.sh                  # CLI + dashboard
#   sudo ./install.sh --with openvpn   # also install openvpn (or --with vxlan)
#   ./install.sh --self-test           # verify this tree, no root, no changes
#
# The installer ends with a self-test: imports, CLI, config round-trip,
# doctor, and profile generation for all three transports. If any of those
# fail the install is reported as failed - it never prints "Done" over a
# broken tree.
set -euo pipefail

PREFIX="${PREFIX:-/opt/lanbridge}"
BIN="/usr/local/bin/lanbridge"
WANT=""
SELFTEST_ONLY=0
SRC="$(cd "$(dirname "$0")" && pwd)"

while [ $# -gt 0 ]; do
  case "$1" in
    --with)      WANT="${2:-}"; shift 2 ;;
    --with=*)    WANT="${1#*=}"; shift ;;
    --prefix)    PREFIX="${2:?}"; shift 2 ;;
    --self-test) SELFTEST_ONLY=1; shift ;;
    -h|--help)   sed -n '2,12p' "$0"; exit 0 ;;
    *)           printf 'unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
done

# Debian guarantees python3; Git Bash and friends may only expose `python`.
find_python() {
  local candidate
  for candidate in python3 python; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,9) else 1)' 2>/dev/null; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}

die()  { printf '\nFAILED: %s\n' "$1" >&2; exit 1; }
ok()   { printf '  [ok]   %s\n' "$1"; }
todo() { printf '  [....] %s\n' "$1"; }

# ---------------------------------------------------------------- self-test
# $1 python interpreter, $2 "installed" | "source"
selftest() {
  local py="$1" mode="$2"
  local tmp rc=0
  tmp="$(mktemp -d)"
  # shellcheck disable=SC2064
  trap "rm -rf '$tmp'" RETURN

  if [ "$mode" = "installed" ]; then
    export PYTHONPATH="$PREFIX"
    lb() { "$BIN" "$@"; }
  else
    export PYTHONPATH="$SRC"
    lb() { "$py" -m lanbridge "$@"; }
  fi

  printf '\nSelf-test (%s mode, %s)\n' "$mode" "$("$py" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"

  # 1. Every module imports, including the ones only the dashboard uses.
  todo "importing all modules"
  "$py" - <<'PY' || die "self-test: module import failed (see traceback above)"
import importlib, sys
mods = [
    "lanbridge", "lanbridge.settings", "lanbridge.sysinfo", "lanbridge.doctor",
    "lanbridge.preflight", "lanbridge.guide", "lanbridge.cli",
    "lanbridge.gen", "lanbridge.gen.common", "lanbridge.gen.openvpn",
    "lanbridge.gen.softether", "lanbridge.gen.vxlan", "lanbridge.gen.firewall",
    "lanbridge.dashboard.server",
]
bad = []
for name in mods:
    try:
        importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001 - report, do not hide
        bad.append(f"{name}: {type(exc).__name__}: {exc}")
if bad:
    print("\n".join(bad), file=sys.stderr)
    sys.exit(1)
print(f"       {len(mods)} modules imported")
PY
  ok "imports"

  # 2. The CLI answers. In installed mode this goes through the launcher, so a
  #    broken shebang or PYTHONPATH is caught here and not at first use.
  todo "cli --version"
  local version
  version="$(lb --version)" || die "self-test: 'lanbridge --version' failed"
  case "$version" in
    lanbridge\ *) ok "cli --version ($version)" ;;
    *) die "self-test: unexpected --version output: $version" ;;
  esac

  # 3. Config round-trip: write a plan, read it back, check derived values.
  todo "config round-trip"
  lb init "$tmp/site.toml" >/dev/null || die "self-test: 'init' failed"
  "$py" - "$tmp/site.toml" <<'PY' || die "self-test: config round-trip failed"
import sys
from lanbridge.settings import Settings
cfg = Settings.load(__import__("pathlib").Path(sys.argv[1]))
assert cfg.network.prefixlen == 24, cfg.site.subnet
assert cfg.netmask == "255.255.255.0", cfg.netmask
assert cfg.broadcast == "192.168.50.255", cfg.broadcast
assert cfg.site.gateway == "192.168.50.1", cfg.site.gateway
print(f"       {cfg.site.subnet} gw {cfg.site.gateway} client {cfg.client.static_ip}")
PY
  ok "config round-trip"

  # 4. doctor must run to completion. Exit 1 means "plan has findings", which
  #    is a legitimate result; a traceback (2) or a crash is not.
  todo "doctor"
  lb -c "$tmp/site.toml" doctor --json > "$tmp/doctor.json" 2> "$tmp/doctor.err" || rc=$?
  if [ "$rc" -gt 1 ]; then
    sed -n '1,20p' "$tmp/doctor.err" >&2
    die "self-test: doctor crashed (exit $rc)"
  fi
  "$py" - "$tmp/doctor.json" <<'PY' || die "self-test: doctor produced invalid JSON"
import json, sys
data = json.load(open(sys.argv[1]))
assert set(data) >= {"verdict", "counts", "checks"}, sorted(data)
assert data["verdict"] in {"ok", "warn", "fail"}, data["verdict"]
print(f'       verdict={data["verdict"]} checks={len(data["checks"])}')
PY
  ok "doctor (exit $rc, valid JSON)"

  # 5. Every transport, both roles. This is the part that actually touches
  #    every template, so a broken f-string cannot survive an install.
  for transport in softether openvpn vxlan; do
    todo "generating $transport"
    lb -c "$tmp/site.toml" gen --transport "$transport" --role both -o "$tmp/out" >/dev/null \
      || die "self-test: generation failed for $transport"
    for role in host client; do
      local dir="$tmp/out/$transport-$role"
      [ -d "$dir" ] || die "self-test: $transport/$role directory missing"
      # shellcheck disable=SC2012
      local count
      count="$(ls -A "$dir" | wc -l | tr -d ' ')"
      [ "$count" -ge 2 ] || die "self-test: $transport/$role produced only $count files"
      if find "$dir" -type f -size 0 | grep -q .; then
        die "self-test: $transport/$role has empty files"
      fi
    done
    ok "$transport profiles (host + client)"
  done

  # 6. Installed mode: prove the launcher works with a clean environment, so a
  #    stray PYTHONPATH in the shell is not masking a broken install.
  if [ "$mode" = "installed" ]; then
    todo "launcher in a clean environment"
    env -i PATH=/usr/local/bin:/usr/bin:/bin "$BIN" --version >/dev/null 2>&1 \
      || die "self-test: launcher fails without an inherited environment"
    ok "launcher (clean env)"
  fi

  printf 'Self-test passed.\n'
}

# --------------------------------------------------------------- self-test only
if [ "$SELFTEST_ONLY" -eq 1 ]; then
  PY="$(find_python)" || die "no Python 3.9+ found (tried python3, python)"
  selftest "$PY" source
  exit $?
fi

# ------------------------------------------------------------------ platform
[ "$(id -u)" -eq 0 ] || die "run with sudo: sudo ./install.sh"
[ -r /etc/os-release ] || die "no /etc/os-release; this installer targets Debian/Ubuntu"
# shellcheck disable=SC1091
. /etc/os-release
case "${ID:-}${ID_LIKE:-}" in
  *debian*|*ubuntu*) printf 'Target: %s\n' "${PRETTY_NAME:-$ID}" ;;
  *) die "unsupported distro '${PRETTY_NAME:-unknown}'. Debian/Ubuntu only." ;;
esac

# -------------------------------------------------------------- dependencies
# apt on a long-lived release can disagree with its own mirror: the index
# advertises a version the pool no longer serves (Debian 11 does this for
# python3 after a security point release). Retry, then verify with dpkg
# instead of trusting apt's exit code.
all_installed() {
  local pkg
  for pkg in "$@"; do
    dpkg -s "$pkg" >/dev/null 2>&1 || return 1
  done
  return 0
}

install_pkgs() {
  apt-get update -qq >/dev/null 2>&1 || true
  apt-get install -y -qq "$@" >/dev/null 2>&1 && all_installed "$@" && return 0

  printf '  apt failed once; retrying with a fresh index and --fix-missing\n'
  apt-get update -qq >/dev/null 2>&1 || true
  apt-get -o Acquire::Retries=3 install -y -qq --fix-missing "$@" >/dev/null 2>&1 || true
  all_installed "$@" && return 0

  # Last resort: a -security index can advertise a version whose pool file no
  # longer exists (Debian 11 does this for python3). The release pocket still
  # has the package, so prefer it rather than dying on a 404.
  if [ -n "${VERSION_CODENAME:-}" ]; then
    printf '  security pocket looks stale; preferring the %s release pocket\n' "$VERSION_CODENAME"
    apt-get install -y -qq -t "$VERSION_CODENAME" "$@" >/dev/null 2>&1 || true
    all_installed "$@" && return 0
  fi
  return 1
}

export DEBIAN_FRONTEND=noninteractive
PKGS="iproute2 nftables ca-certificates"
if ! command -v python3 >/dev/null 2>&1; then
  PKGS="python3 $PKGS"
fi
printf '\nInstalling base packages (%s)...\n' "$PKGS"
# shellcheck disable=SC2086
install_pkgs $PKGS || die "could not install: $PKGS
  Your apt mirror is inconsistent (index and pool disagree). Fix it with:
    apt-get update && apt-get -f install
  then re-run this installer. Nothing has been modified."

command -v python3 >/dev/null || die "python3 is still not on PATH"
printf 'Python: %s\n' "$(python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"

# tomllib ships with 3.11. Older interpreters need tomli installed into the
# SAME interpreter the launcher will use - `apt install python3-tomli` only
# serves Debian's own python3, which is not always the one on PATH.
ensure_tomli() {
  python3 -c 'import tomllib' 2>/dev/null && return 0
  python3 -c 'import tomli' 2>/dev/null && return 0

  printf 'Python < 3.11 - installing tomli into %s\n' "$(command -v python3)"
  if ! python3 -m pip --version >/dev/null 2>&1; then
    install_pkgs python3-pip || true
  fi
  python3 -m pip install --quiet --user tomli >/dev/null 2>&1 \
    || python3 -m pip install --quiet --break-system-packages tomli >/dev/null 2>&1 \
    || python3 -m pip install --quiet tomli >/dev/null 2>&1 \
    || true
  python3 -c 'import tomli' 2>/dev/null && return 0

  printf '  pip did not satisfy the interpreter; trying the distro package\n'
  install_pkgs python3-tomli || true
  python3 -c 'import tomli' 2>/dev/null && return 0
  return 1
}

ensure_tomli || die "tomli is not importable by $(command -v python3).
  LanBridge needs Python 3.11+, or tomli for older interpreters:\n    $(command -v python3) -m pip install tomli\n  then re-run this installer."

case "$WANT" in
  openvpn)  printf 'Installing OpenVPN...\n';  install_pkgs openvpn || die "openvpn install failed" ;;
  vxlan)    printf 'Installing WireGuard tools...\n'; install_pkgs wireguard-tools || die "wireguard-tools install failed" ;;
  softether) printf 'SoftEther is not packaged. Install it, then: sudo /usr/local/vpnserver/vpnserver start\n' ;;
  "")       : ;;
  *)        die "unknown --with value '$WANT' (openvpn|vxlan|softether)" ;;
esac

# ------------------------------------------------------------------- install
printf '\nInstalling to %s\n' "$PREFIX"
mkdir -p "$PREFIX"
for item in lanbridge pyproject.toml README.md examples docs; do
  [ -e "$SRC/$item" ] || continue
  rm -rf "${PREFIX:?}/$item"
  cp -a "$SRC/$item" "$PREFIX/"
done
find "$PREFIX" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

cat > "$BIN" <<LAUNCHER
#!/bin/sh
# LanBridge launcher (installed by install.sh)
PYTHONPATH="$PREFIX\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m lanbridge "\$@"
LAUNCHER
chmod 0755 "$BIN"

# ----------------------------------------------------------------- verify
# If this fails, the install is not usable and we say so with a non-zero exit.
selftest python3 installed || die "installation is present but NOT working; see the failure above"

cat <<EOF

Done - $PREFIX, launcher at $BIN, self-test passed.

  lanbridge init && nano lanbridge.toml     # 1. plan
  lanbridge doctor                          # 2. check this machine
  lanbridge gen -t softether -r both -o out # 3. profiles
  lanbridge dashboard --port 8770           # 4. UI (127.0.0.1 only)

Headless VM: ssh -L 8770:127.0.0.1:8770 user@vm then open http://127.0.0.1:8770

Transport daemons are NOT started here. docs/RUNBOOK.md says which machine
runs which command.
EOF
