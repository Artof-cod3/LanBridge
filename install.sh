#!/usr/bin/env bash
# LanBridge installer for Debian 11/12, Ubuntu 22.04/24.04 and derivatives.
#
#   sudo ./install.sh                 # CLI + dashboard, no tunnel daemon
#   sudo ./install.sh --with openvpn  # also install the openvpn package + TAP tools
#   sudo ./install.sh --with vxlan    # also install wireguard-tools
#
# Idempotent: re-running upgrades the installed copy in place.
set -euo pipefail

PREFIX="${PREFIX:-/opt/lanbridge}"
BIN="/usr/local/bin/lanbridge"
WANT=""

while [ $# -gt 0 ]; do
  case "$1" in
    --with) WANT="${2:-}"; shift 2 ;;
    --with=*) WANT="${1#*=}"; shift ;;
    --prefix) PREFIX="${2:?}"; shift 2 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

say()  { printf '\033[0m%s\n' "$*"; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run with sudo: sudo ./install.sh"

# ---------------------------------------------------------------- platform
[ -r /etc/os-release ] || die "no /etc/os-release; this installer targets Debian/Ubuntu"
. /etc/os-release
case "${ID:-}${ID_LIKE:-}" in
  *debian*|*ubuntu*) : ;;
  *) die "unsupported distro '${PRETTY_NAME:-unknown}'. Debian/Ubuntu only." ;;
esac
say "Target: ${PRETTY_NAME:-$ID}"

# ------------------------------------------------------------ dependencies
say "Installing base packages (python3, iproute2, nftables)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 iproute2 nftables ca-certificates >/dev/null

PYVER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
say "Python found: $PYVER"

# tomllib ships with 3.11; older releases get the tomli backport.
if python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)'; then
  :
else
  say "Python < 3.11 - installing the tomli backport"
  apt-get install -y -qq python3-tomli >/dev/null || die "python3-tomli unavailable; install Python 3.11+"
fi

case "$WANT" in
  openvpn)
    say "Installing OpenVPN (TAP tooling included)"
    apt-get install -y -qq openvpn >/dev/null
    ;;
  vxlan)
    say "Installing WireGuard tools"
    apt-get install -y -qq wireguard-tools >/dev/null
    ;;
  softether)
    say "SoftEther is not in apt. Install it, then: sudo /usr/local/vpnserver/vpnserver start"
    ;;
  "") : ;;
  *) die "unknown --with value '$WANT' (openvpn|vxlan|softether)" ;;
esac

# ----------------------------------------------------------------- install
say "Installing to $PREFIX"
mkdir -p "$PREFIX"
SRC="$(cd "$(dirname "$0")" && pwd)"
# Copy the package and docs, never generated output or VCS data.
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

# -------------------------------------------------------------- smoke test
say "Smoke test:"
if lanbridge --version >/dev/null 2>&1; then
  say "  lanbridge $(lanbridge --version 2>/dev/null | awk '{print $2}')"
else
  die "launcher failed - run: PYTHONPATH=$PREFIX python3 -m lanbridge --version"
fi

cat <<EOF

Done. Next steps (run them in this order):

  # 1. create a plan for this machine
  lanbridge init && nano lanbridge.toml

  # 2. check the machine against that plan
  lanbridge doctor

  # 3. generate profiles
  lanbridge gen --transport softether --role both -o out

  # 4. dashboard (binds 127.0.0.1 only)
  lanbridge dashboard --port 8770

Headless VM? Reach the dashboard through SSH:
  ssh -L 8770:127.0.0.1:8770 user@vm   then open http://127.0.0.1:8770

Transport daemons are NOT started by this installer. Apply the generated
profiles yourself; see docs/RUNBOOK.md for which machine does what.
EOF
