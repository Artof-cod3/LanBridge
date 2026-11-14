"""OpenVPN TAP-mode bridge configuration.

Why TAP: a TAP device hands Ethernet frames to userspace, so the tunnel can be
enslaved to a Linux bridge next to the AP-facing NIC. Both sides end up in one
broadcast domain: ARP, DHCP, mDNS and static addresses all behave exactly as
if the cable were plugged in.

The trap: a *wireless* NIC cannot join a Linux bridge (802.11 uses three MAC
addresses, so the client MAC never appears on the air). On Wi-Fi, use the
SoftEther transport instead - see softether.py.
"""

from __future__ import annotations

from ..settings import Settings
from . import firewall
from .common import banner

NAME = "openvpn"
TITLE = "OpenVPN TAP bridge"


def server_config(cfg: Settings) -> str:
    return f"""{banner("LanBridge - OpenVPN TAP server", cfg, "host")}
#
# Run this on the node that sits on the AP's LAN. The tap device is enslaved
# to {cfg.host.bridge_iface}; the LAN address moves onto the bridge.

dev {cfg.host.tap_iface}
mode server
tls-server
proto udp
port {cfg.tunnel.listen_port}
local 0.0.0.0

ca   pki/ca.crt
cert pki/issued/host.crt
key  pki/private/host.key
dh   pki/dh.pem

# LAN settings. server-bridge only provides a DHCP pool for clients that do
# not pin an address; a client with `ifconfig` in its profile keeps that
# address, which is the whole point of this project.
server-bridge {cfg.site.gateway} {cfg.netmask} {cfg.site.dhcp_start} {cfg.site.dhcp_end}
push "route-gateway {cfg.site.gateway}"
push "route {cfg.network_address} {cfg.netmask}"

# Clients may talk to each other even if bridge filtering interferes.
client-to-client

# Keep the default route alone. We only want the LAN prefix on-link.
# If you *do* want full-tunnel, uncomment the next line instead:
# push "redirect-gateway def1 bypass-dhcp"

tun-mtu {cfg.tunnel.mtu}
keepalive 10 60
persist-key
persist-tun
explicit-exit-notify 0
verb 3

# Linux only: drop privileges after the tap device is configured.
# user nobody
# group nogroup
"""


def client_config(cfg: Settings) -> str:
    return f"""{banner("LanBridge - OpenVPN TAP client", cfg, "client")}
#
# The `ifconfig` line below is what preserves the pre-configured static IP.
# Do not let anything else (DHCP renew, NetworkManager) own this adapter.

client
dev {cfg.client.tap_iface}
proto udp
remote {cfg.tunnel.remote_host} {cfg.tunnel.remote_port}
nobind
resolv-retry infinite
persist-key
persist-tun

ca   pki/ca.crt
cert pki/issued/client.crt
key  pki/private/client.key
remote-cert-tls server

# Keep the address your app was configured against.
ifconfig {cfg.client.static_ip} {cfg.netmask}
route-gateway {cfg.site.gateway}

# Windows: pin the virtual adapter by name so the right TAP device is used.
# dev-node "{cfg.client.vpn_adapter_name}"

tun-mtu {cfg.tunnel.mtu}
verb 3
"""


def bridge_script(cfg: Settings) -> str:
    return f"""#!/usr/bin/env bash
# LanBridge - create the L2 bridge on the AP node (Linux).
# Idempotent: safe to re-run after a reboot.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || {{ echo "run as root" >&2; exit 1; }}

LAN={cfg.host.lan_iface}
TAP={cfg.host.tap_iface}
BR={cfg.host.bridge_iface}

# 1. The bridge itself.
ip link show "$BR" >/dev/null 2>&1 || ip link add "$BR" type bridge

# 2. Move the LAN address off the NIC and onto the bridge, then enslave it.
LAN_ADDR="$(ip -4 -o addr show dev "$LAN" | awk '{{print $4}}' | head -n1)"
[ -n "$LAN_ADDR" ] && ip addr del "$LAN_ADDR" dev "$LAN" 2>/dev/null || true
ip link set "$LAN" master "$BR" || true
ip addr add {cfg.site.bridge_address}/{cfg.prefixlen} dev "$BR" 2>/dev/null || true
ip link set "$LAN" up
ip link set "$BR" up

# 3. Create the persistent tap device and put it in the bridge.
openvpn --mktun --dev "$TAP" >/dev/null 2>&1 || true
ip link set "$TAP" master "$BR" || true
ip link set "$TAP" up

# 4. Bridging does not need routing; turn off the netfilter hooks that
#    silently drop cross-bridge frames on some distros.
sysctl -qw net.bridge.bridge-nf-call-iptables=0 || true
sysctl -qw net.bridge.bridge-nf-call-ip6tables=0 || true

echo "bridge $BR up:"
bridge link show
ip -4 -br addr show "$BR"
"""


def windows_note(cfg: Settings) -> str:
    return f"""# Windows bridging, honestly

Windows has no supported command-line "make a bridge" cmdlet, and bridging a
Wi-Fi adapter with a TAP adapter usually fails: the Wi-Fi miniport refuses to
join a bridge ("An unexpected error occurred while configuring the network
bridge"). Windows also cannot pass frames with two MAC addresses over 802.11.

So on Windows, treat this file as reference only and use SoftEther:

    lanbridge gen --transport softether --role host

If the AP-side machine *is* wired, this still works:

  1. Open ncpa.cpl, select the Ethernet NIC and the TAP-Windows adapter.
  2. Right click -> Bridge Connections.
  3. Give the resulting "Network Bridge" adapter
     {cfg.site.bridge_address}/{cfg.prefixlen} and gateway {cfg.site.gateway}.
  4. Disable "Internet Connection Sharing" if it was auto-enabled.
"""


def install_md(cfg: Settings) -> str:
    return f"""# OpenVPN TAP bridge - install

Two nodes: **host** (on the AP) and **client** (your machine).

## 1. Certificates (either machine, then copy `pki/` to both)

```bash
git clone https://github.com/OpenVPN/easy-rsa.git
cd easy-rsa/easyrsa3
./easyrsa init-pki
./easyrsa --batch build-ca nopass
./easyrsa --batch build-server-full host nopass
./easyrsa --batch build-client-full client nopass
./easyrsa gen-dh
```

Windows: `C:\\Program Files\\OpenVPN\\easy-rsa\\EasyRSA-Start.bat`, same commands.
Copy the resulting `pki/` next to the `.ovpn` files.

## 2. Host: bridge the tap device

```bash
sudo ./bridge-up.sh
sudo openvpn --config server.ovpn
```

Check before connecting the client:

```bash
bridge link show
ip -4 -br addr show {cfg.host.bridge_iface}     # must carry {cfg.site.bridge_address}/{cfg.prefixlen}
```

## 3. Host: firewall

```bash
sudo nft -f firewall-host.nft  # or apply the ufw lines noted in that file
```

## 4. Client

```bash
sudo openvpn --config client.ovpn
```

Windows: import `client.ovpn` in the OpenVPN GUI. Install the TAP-Windows
driver during OpenVPN setup; the adapter must be named `{cfg.client.vpn_adapter_name}`
if you uncomment `dev-node`.

## 5. Verify

```bash
ping {cfg.site.gateway}                       # AP router
ping {cfg.site.bridge_address}                # host node over the tunnel
arping -I {cfg.client.tap_iface} {cfg.site.gateway}
```

If `ping` works but `arping` does not, the bridge is not passing broadcast:
re-check that the LAN NIC is enslaved and that its address moved to the bridge.
"""


def generate(cfg: Settings, role: str) -> dict[str, str]:
    files = {
        "INSTALL.md": install_md(cfg),
    }
    if role == "host":
        files["server.ovpn"] = server_config(cfg)
        files["bridge-up.sh"] = bridge_script(cfg)
        files["WINDOWS-BRIDGE.md"] = windows_note(cfg)
        files["firewall-host.nft"] = firewall.linux_nft(cfg, "openvpn")
        files["firewall-host.ps1"] = firewall.windows_ps1(cfg, "openvpn")
    else:
        files["client.ovpn"] = client_config(cfg)
    return files
