"""SoftEther VPN: virtual L2 switch + local bridge.

This is the practical answer for Windows and for Wi-Fi links:

* The SoftEther virtual hub is a real Ethernet switch.
* `BridgeCreate ... /DEVICE:"Wi-Fi"` attaches the physical adapter to that
  switch. SoftEther inserts its own 802.11 shim, so the three-MAC-address
  limitation of Wi-Fi does not break the bridge.
* Clients get a virtual NIC that receives the LAN's broadcast domain, so a
  static IP from the AP's plan works untouched.

Downside: it is a bigger daemon than a raw tunnel, and the hub is only as
private as its user list - always set a password and never leave the default
"anonymous" hub reachable from the internet.
"""

from __future__ import annotations

from ..settings import Settings
from . import firewall
from .common import banner

NAME = "softether"
TITLE = "SoftEther L2 bridge"


def vpncmd_unix(cfg: Settings) -> str:
    """vpncmd command list for the Linux server build."""
    return f"""# LanBridge - SoftEther server setup (Linux, /usr/local/vpnserver)
# Run as root, after `vpnserver start`.
# vpncmd asks "Are you sure?" - answer "yes", or pipe `yes |`.

CMD=/usr/local/vpnserver/vpncmd
SERVER="$CMD localhost /SERVER"

$SERVER /CMD ServerPasswordSet
$SERVER /CMD HubCreate {cfg.site.name} /PASSWORD:
$SERVER /CMD Hub {cfg.site.name}
$SERVER /CMD UserCreate client /GROUP:none /REALNAME:none /NOTE:lanbridge
$SERVER /CMD UserPasswordSet client /PASSWORD:

# The local bridge: virtual hub <-> physical NIC facing the AP.
# /TAP:no means "bridge to a real adapter" rather than a tap device.
$SERVER /CMD BridgeCreate {cfg.site.name} /DEVICE:{cfg.host.lan_iface} /TAP:no

# Keep only what you need listening. SoftEther NAT traversal uses the
# built-in relay when the host has no inbound port forward.
$SERVER /CMD ListenerDelete 992
$SERVER /CMD ListenerCreate {cfg.tunnel.listen_port}

$SERVER /CMD BridgeList
$SERVER /CMD ListenerList
"""


def host_ps1(cfg: Settings) -> str:
    return f"""{banner("LanBridge - SoftEther server setup (Windows)", cfg, "host")}
# Run this in an elevated PowerShell.
$ErrorActionPreference = "Stop"

$vpncmd = "C:\\Program Files\\SoftEther VPN Server\\vpncmd.exe"
if (-not (Test-Path $vpncmd)) {{
    throw "Install SoftEther VPN Server first: https://www.softether-download.com/"
}}

# Interactive password prompts are unavoidable here; set them once.
& $vpncmd localhost /SERVER /CMD ServerPasswordSet
& $vpncmd localhost /SERVER /CMD HubCreate {cfg.site.name} /PASSWORD:
& $vpncmd localhost /SERVER /CMD UserCreate client /GROUP:none /REALNAME:none /NOTE:lanbridge
& $vpncmd localhost /SERVER /CMD UserPasswordSet client /PASSWORD:

# Bridge the virtual hub onto the physical adapter that faces the AP.
# Use the exact name from Get-NetAdapter, e.g. "Wi-Fi" or "Ethernet".
& $vpncmd localhost /SERVER /CMD BridgeCreate {cfg.site.name} /DEVICE:"{cfg.host.lan_iface}" /TAP:no

& $vpncmd localhost /SERVER /CMD ListenerCreate {cfg.tunnel.listen_port}
& $vpncmd localhost /SERVER /CMD BridgeList

Write-Host ""
Write-Host "Firewall: UDP {cfg.tunnel.listen_port} is not required when SoftEther"
Write-Host "NAT traversal is used, but open it for a direct connection:"
Write-Host "  netsh advfirewall firewall add rule name=`"LanBridge SoftEther`" dir=in action=allow protocol=TCP localport={cfg.tunnel.listen_port}"
"""


def client_ps1(cfg: Settings) -> str:
    return f"""{banner("LanBridge - SoftEther client (Windows)", cfg, "client")}
# Run elevated. Assumes SoftEther VPN Client is installed and its service runs.
$vpncmd = "C:\\Program Files\\SoftEther VPN Client\\vpncmd.exe"

& $vpncmd localhost /CLIENT /CMD AccountCreate {cfg.site.name} `
    /SERVER:{cfg.tunnel.remote_host}:{cfg.tunnel.remote_port} `
    /HUB:{cfg.site.name} /USERNAME:client /NICNAME:"{cfg.client.vpn_adapter_name}"
& $vpncmd localhost /CLIENT /CMD AccountPasswordSet {cfg.site.name} /PASSWORD: /TYPE:standard
& $vpncmd localhost /CLIENT /CMD AccountConnect {cfg.site.name}

# Give the virtual NIC the static identity the app expects. The tunnel is a
# bridge, so the gateway is the AP's router, not a tunnel endpoint.
Start-Sleep -Seconds 2
netsh interface ipv4 set address name="{cfg.client.vpn_adapter_name}" `
    static {cfg.client.static_ip} {cfg.netmask} {cfg.site.gateway}
netsh interface ipv4 set dnsservers name="{cfg.client.vpn_adapter_name}" `
    static {cfg.site.gateway} primary

& $vpncmd localhost /CLIENT /CMD AccountStatusGet {cfg.site.name}
Get-NetIPAddress -InterfaceAlias "{cfg.client.vpn_adapter_name}" -AddressFamily IPv4
"""


def client_sh(cfg: Settings) -> str:
    return f"""#!/usr/bin/env bash
# LanBridge - SoftEther client (Linux)
set -euo pipefail
[ "$(id -u)" -eq 0 ] || {{ echo "run as root" >&2; exit 1; }}

CMD=/usr/local/vpnclient/vpncmd
SERVER="$CMD localhost /CLIENT"

# A tap device is created by the SoftEther client for each virtual NIC.
$SERVER /CMD AccountCreate {cfg.site.name} \\
    /SERVER:{cfg.tunnel.remote_host}:{cfg.tunnel.remote_port} \\
    /HUB:{cfg.site.name} /USERNAME:client /NICNAME:{cfg.client.vpn_adapter_name}
$SERVER /CMD AccountPasswordSet {cfg.site.name} /PASSWORD: /TYPE:standard
$SERVER /CMD AccountConnect {cfg.site.name}
sleep 2
$SERVER /CMD AccountStatusGet {cfg.site.name}

# Find the tap device the client attached, then pin the static address.
DEV="$(ip -o link show | awk -F': ' '{{print $2}}' | grep -iE '^vpn_{cfg.client.vpn_adapter_name.lower()}' | head -n1)"
[ -n "$DEV" ] || {{ echo "virtual NIC not found; check AccountStatusGet" >&2; exit 1; }}
ip addr flush dev "$DEV"
ip addr add {cfg.client.static_ip}/{cfg.prefixlen} dev "$DEV"
# Leave the default route alone: the bridge only adds the AP prefix, and
# hijacking the default would push all internet traffic through the AP.
ip link set "$DEV" up
ip route add {cfg.network_address}/{cfg.prefixlen} dev "$DEV" 2>/dev/null || true
ip -4 -br addr show "$DEV"
"""


def install_md(cfg: Settings) -> str:
    return f"""# SoftEther L2 bridge - install

## Host (on the AP)

Windows: install **SoftEther VPN Server**, then run `host.ps1` elevated.
Linux: install the server tarball, then `host.sh` as root.

```bash
# Linux, from the SoftEther download page
tar xzf softether-vpnserver-*.tar.gz -C /usr/local
cd /usr/local/vpnserver && make && ./vpnserver start
sudo ./host.sh
```

Check the bridge exists before touching the client:

```bash
vpncmd localhost /SERVER /CMD BridgeList      # State: Operating
```

The bridge device must be the adapter that faces the AP:
`{cfg.host.lan_iface}`. If frames do not flow, the bridge shows `Error` -
that means the NIC name is wrong or the adapter is down.

## Client

Windows: `client.ps1` (edit the static IP first if needed, it is
`{cfg.client.static_ip}`). Linux: `client.sh`.

## Reaching the host without a port forward

SoftEther's default listener is reachable directly only if the host's router
forwards {cfg.tunnel.listen_port}. If the AP-side machine is behind CGNAT, use
one of:

* **SoftEther's own NAT traversal** - enable *VPN NAT Traversal* in the server
  manager; the client connects through the relay on port 443/992.
* A **cheap VPS** running SoftEther with a cascade to the AP node.
* A **WireGuard relay** (see the vxlan transport) if you prefer owning both ends.

## Verify

```bash
ping {cfg.site.gateway}
ping {cfg.site.bridge_address}
# Windows
arp -a | findstr {cfg.site.gateway.split(".")[0]}.{cfg.site.gateway.split(".")[1]}
```

`arp -a` must show `{cfg.site.gateway}` on the virtual adapter. If it does not,
the hub is not bridged to the physical NIC.
"""


def generate(cfg: Settings, role: str) -> dict[str, str]:
    files: dict[str, str] = {"INSTALL.md": install_md(cfg)}
    if role == "host":
        files["host.ps1"] = host_ps1(cfg)
        files["host.sh"] = vpncmd_unix(cfg)
        files["firewall-host.ps1"] = firewall.windows_ps1(cfg, "softether")
        files["firewall-host.nft"] = firewall.linux_nft(cfg, "softether")
    else:
        files["client.ps1"] = client_ps1(cfg)
        files["client.sh"] = client_sh(cfg)
    return files
