"""Firewall rule generation.

Minimal exposure: one inbound UDP/TCP port for the tunnel, and a forwarding
path on the *bridge* interface only. Nothing else is touched.

The nft script uses `add table` + `flush table`, which is idempotent: applying
it twice does not fail and does not leave stale rules behind.
"""

from __future__ import annotations

from ..settings import Settings

PORTS = {
    "openvpn": ("udp", "listen_port"),
    "softether": ("tcp", "listen_port"),
    "vxlan": ("udp", "wg_port"),
}


def _port(cfg: Settings, transport: str) -> tuple[str, int]:
    proto, attr = PORTS.get(transport, ("udp", "listen_port"))
    return proto, getattr(cfg.tunnel, attr)


def linux_nft(cfg: Settings, transport: str) -> str:
    proto, port = _port(cfg, transport)
    vxlan_guard = ""
    if transport == "vxlan":
        vxlan_guard = (
            f'    # VXLAN is carried inside {cfg.host.wg_iface}; it must NOT be\n'
            f"    # reachable on the uplink.\n"
            f'    iifname != "{cfg.host.wg_iface}" udp dport {cfg.tunnel.vxlan_port} drop\n'
        )

    return f"""#!/usr/sbin/nft -f
# LanBridge firewall - host node.
# Apply with: sudo nft -f firewall-host.nft

add table inet lanbridge
flush table inet lanbridge

table inet lanbridge {{
  chain input {{
    type filter hook input priority 0; policy accept;

    iif lo accept
    ct state established,related accept

    # The tunnel endpoint: the only port that must face the internet.
    {proto} dport {port} accept
{vxlan_guard}
    # ICMP is needed for path-MTU discovery.
    ip protocol icmp accept
  }}

  chain forward {{
    type filter hook forward priority 0; policy accept;

    # Frames crossing an L2 bridge are not routed, so these rules only
    # matter for the VXLAN-under-WireGuard path.
    iifname "{cfg.host.bridge_iface}" accept
    oifname "{cfg.host.bridge_iface}" accept
  }}
}}

# Equivalent with ufw instead of nftables:
#   sudo ufw allow {port}/{proto}
#   sudo ufw route allow in on {cfg.host.bridge_iface}
#   sudo ufw route allow out on {cfg.host.bridge_iface}
"""


def windows_ps1(cfg: Settings, transport: str) -> str:
    proto, port = _port(cfg, transport)
    return f"""# LanBridge firewall - host node (Windows, elevated).
# Creates an inbound allow rule for the tunnel and permits the virtual adapter.

$ErrorActionPreference = "Stop"
$name = "LanBridge {transport} {port}"

Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule

New-NetFirewallRule `
    -DisplayName $name `
    -Direction Inbound `
    -Action Allow `
    -Protocol {proto.upper()} `
    -LocalPort {port} `
    -Profile Any

# Let the tunnel adapter's traffic through without per-app prompts.
$adapter = "{cfg.client.vpn_adapter_name}"
New-NetFirewallRule -DisplayName "LanBridge $adapter allow" `
    -Direction Inbound -Action Allow -InterfaceAlias $adapter -Profile Any `
    -ErrorAction SilentlyContinue

Write-Host "Rules:"
Get-NetFirewallRule -DisplayName "LanBridge*" |
    Select-Object DisplayName,Enabled,Direction,Action | Format-Table
"""


def generate(cfg: Settings, transport: str) -> dict[str, str]:
    return {
        "firewall-host.nft": linux_nft(cfg, transport),
        "firewall-host.ps1": windows_ps1(cfg, transport),
    }
