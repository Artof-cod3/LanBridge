"""WireGuard + VXLAN: L2 over an L3 tunnel, Linux only.

WireGuard is a layer-3 tunnel, so it cannot carry broadcast on its own. VXLAN
puts Ethernet frames inside UDP and VXLAN rides *inside* the WireGuard tunnel:

    [ eth frame | VXLAN | UDP | outer IP ]  ->  encrypted by wg0  ->  internet

The VXLAN device is then enslaved to a bridge with the AP-facing NIC, exactly
like the TAP case. Overheads:

    1500 underlay
     -60  WireGuard (IPv4 + UDP + wg header)
    = 1440  -> use 1420 for safety
     -50  VXLAN + outer IP + UDP + inner Ethernet
    = 1370  inner MTU
"""

from __future__ import annotations

from ..settings import Settings
from . import firewall
from .common import banner

NAME = "vxlan"
TITLE = "WireGuard + VXLAN"


def wg_host(cfg: Settings) -> str:
    return f"""{banner("LanBridge - WireGuard host config", cfg, "host")}
# Place in /etc/wireguard/{cfg.host.wg_iface}.conf on the AP node.
# Replace <HOST_PRIVKEY> and <CLIENT_PUBKEY> with `wg genkey` output.

[Interface]
Address = {cfg.tunnel.wg_host_ip}/30
ListenPort = {cfg.tunnel.wg_port}
PrivateKey = <HOST_PRIVKEY>
MTU = {cfg.wg_mtu}

[Peer]
PublicKey = <CLIENT_PUBKEY>
AllowedIPs = {cfg.tunnel.wg_client_ip}/32
PersistentKeepalive = 25
"""


def wg_client(cfg: Settings) -> str:
    return f"""{banner("LanBridge - WireGuard client config", cfg, "client")}
# Place in /etc/wireguard/{cfg.host.wg_iface}.conf on the client.

[Interface]
Address = {cfg.tunnel.wg_client_ip}/30
PrivateKey = <CLIENT_PRIVKEY>
MTU = {cfg.wg_mtu}

[Peer]
PublicKey = <HOST_PUBKEY>
Endpoint = {cfg.tunnel.remote_host}:{cfg.tunnel.wg_port}
AllowedIPs = {cfg.tunnel.wg_host_ip}/32
PersistentKeepalive = 25
"""


def up_script(cfg: Settings, role: str) -> str:
    is_host = role == "host"
    local_ip = cfg.tunnel.wg_host_ip if is_host else cfg.tunnel.wg_client_ip
    peer_ip = cfg.tunnel.wg_client_ip if is_host else cfg.tunnel.wg_host_ip
    bridge_section = ""
    if is_host:
        bridge_section = f"""
# --- L2 segment -----------------------------------------------------------
# The bridge holds the AP-facing NIC plus the VXLAN device.
ip link show "$BR" >/dev/null 2>&1 || ip link add "$BR" type bridge
ip link set "$WG" mtu {cfg.wg_mtu} up
ip link set "$LAN" master "$BR" 2>/dev/null || true
ip addr flush dev "$LAN" 2>/dev/null || true
ip addr add {cfg.site.bridge_address}/{cfg.prefixlen} dev "$BR" 2>/dev/null || true
ip link set "$LAN" up
ip link set "$BR" up

# nolearning: we have exactly one VTEP peer, so replaying every unknown
# frame to it is correct and avoids flooding loops.
ip link show "$VX" >/dev/null 2>&1 || \\
  ip link add "$VX" type vxlan id {cfg.tunnel.vxlan_id} \\
      local $LOCAL_IP remote {peer_ip} dstport {cfg.tunnel.vxlan_port} \\
      dev "$WG" nolearning
ip link set "$VX" mtu {cfg.vxlan_mtu}
ip link set "$VX" master "$BR"
ip link set "$VX" up
bridge fdb append 00:00:00:00:00:00 dev "$VX" dst {peer_ip}

sysctl -qw net.bridge.bridge-nf-call-iptables=0 || true
echo "bridge $BR / $VX up (MTU {cfg.vxlan_mtu})"
bridge link show
"""
    else:
        bridge_section = f"""
# --- L2 segment -----------------------------------------------------------
# No physical bridge on the client: the VXLAN device IS the virtual NIC, so
# the pre-configured static address lives directly on it.
ip link set "$WG" mtu {cfg.wg_mtu} up
ip link show "$VX" >/dev/null 2>&1 || \\
  ip link add "$VX" type vxlan id {cfg.tunnel.vxlan_id} \\
      local $LOCAL_IP remote {peer_ip} dstport {cfg.tunnel.vxlan_port} \\
      dev "$WG" nolearning
ip link set "$VX" mtu {cfg.vxlan_mtu}
ip addr replace {cfg.client.static_ip}/{cfg.prefixlen} dev "$VX"
ip link set "$VX" up

# Static FDB entry: broadcast/unknown-unicast is replayed to the single VTEP.
bridge fdb append 00:00:00:00:00:00 dev "$VX" dst {peer_ip} 2>/dev/null || true

# The AP prefix is on-link on the VXLAN device; no gateway route needed.
ip route replace {cfg.network_address}/{cfg.prefixlen} dev "$VX" 2>/dev/null || true
echo "$VX up: {cfg.client.static_ip}/{cfg.prefixlen} over {local_ip} -> {peer_ip} (MTU {cfg.vxlan_mtu})"
"""

    return f"""#!/usr/bin/env bash
{banner("LanBridge - bring up WireGuard + VXLAN", cfg, role)}
set -euo pipefail
[ "$(id -u)" -eq 0 ] || {{ echo "run as root" >&2; exit 1; }}

WG={cfg.host.wg_iface}
VX={cfg.host.vxlan_iface}
BR={cfg.host.bridge_iface}
LAN={cfg.host.lan_iface}
LOCAL_IP={local_ip}

# 1. WireGuard first: VXLAN rides on top of it.
ip link show "$WG" >/dev/null 2>&1 || ip link add "$WG" type wireguard
wg setconf "$WG" /etc/wireguard/"$WG".conf
ip addr replace {local_ip}/30 dev "$WG"
{bridge_section}
"""


def install_md(cfg: Settings) -> str:
    return f"""# WireGuard + VXLAN - install (Linux only)

Windows has no usable VXLAN stack; use softether there.

## 1. Keys

```bash
wg genkey | tee host.key       | wg pubkey > host.pub
wg genkey | tee client.key     | wg pubkey > client.pub
```

Paste the private keys and the opposite public key into `wg-host.conf` and
`wg-client.conf`.

## 2. Host

```bash
sudo cp wg-host.conf /etc/wireguard/{cfg.host.wg_iface}.conf
sudo chmod 600 /etc/wireguard/{cfg.host.wg_iface}.conf
sudo ./up.sh
```

## 3. Client

```bash
sudo cp wg-client.conf /etc/wireguard/{cfg.host.wg_iface}.conf
sudo chmod 600 /etc/wireguard/{cfg.host.wg_iface}.conf
sudo ./up.sh
```

The client's `up.sh` creates `{cfg.host.vxlan_iface}` on top of `wg0` and puts
`{cfg.client.static_ip}/{cfg.prefixlen}` straight on it - no bridge, because
this machine has no physical NIC on the AP's segment. ARP then crosses the
VXLAN segment to the host's bridge, where the AP's devices answer.

## 4. Firewall (host)

```bash
sudo nft -f firewall-host.nft
```

Only the WireGuard port ({cfg.tunnel.wg_port}/udp) needs to be reachable from
the internet. VXLAN's {cfg.tunnel.vxlan_port}/udp stays inside `wg0` and must
not be exposed.

## 5. Verify

```bash
wg show                                    # latest handshake, transfer counters
ip -d link show {cfg.host.vxlan_iface}     # vxlan id {cfg.tunnel.vxlan_id}
bridge fdb show dev {cfg.host.vxlan_iface} # the 00:00:00:00:00:00 -> peer entry
ping -M do -s {cfg.vxlan_mtu - 28} {cfg.site.gateway}   # MTU probe
```

If VXLAN shows up but ARP does not resolve, check
`bridge fdb show dev {cfg.host.vxlan_iface}` for the all-zero MAC entry and
confirm `ip -d link show` reports `dev {cfg.host.wg_iface}`.
"""


def generate(cfg: Settings, role: str) -> dict[str, str]:
    files: dict[str, str] = {
        "INSTALL.md": install_md(cfg),
        "up.sh": up_script(cfg, role),
    }
    if role == "host":
        files["wg-host.conf"] = wg_host(cfg)
        files["firewall-host.nft"] = firewall.linux_nft(cfg, "vxlan")
    else:
        files["wg-client.conf"] = wg_client(cfg)
    return files
