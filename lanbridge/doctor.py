"""Environment checks with concrete fixes.

Each result is (id, level, title, detail, fix). Levels are ok / warn / fail.
`doctor` is deliberately read-only: it never changes the machine.
"""

from __future__ import annotations

import ipaddress
import platform
import shutil
import sys
from dataclasses import asdict, dataclass
from typing import Any

from . import sysinfo
from .settings import Settings

WINDOWS = sys.platform.startswith("win")
LINUX = sys.platform.startswith("linux")


@dataclass
class Check:
    id: str
    level: str          # ok | warn | fail
    title: str
    detail: str
    fix: str = ""


def _in_network(ip: str, cfg: Settings) -> bool:
    try:
        return ipaddress.IPv4Address(ip) in cfg.network
    except ValueError:
        return False


def run_checks(cfg: Settings) -> list[Check]:
    checks: list[Check] = []
    add = checks.append

    # 1. Interpreter -------------------------------------------------------
    if sys.version_info >= (3, 11):
        add(Check("python", "ok", "Python interpreter", f"{platform.python_version()} (tomllib available)"))
    else:
        add(
            Check(
                "python",
                "fail",
                "Python interpreter",
                f"{platform.python_version()} is too old",
                "Install Python 3.11 or newer; tomllib and dataclass slots are required.",
            )
        )

    # 2. Privileges --------------------------------------------------------
    if sysinfo.is_admin():
        add(Check("privileges", "ok", "Privileges", "elevated (can create adapters and edit routes)"))
    else:
        add(
            Check(
                "privileges",
                "warn",
                "Privileges",
                "not elevated - generation works, applying configs will not",
                "Run an Administrator PowerShell (Windows) or `sudo` (Linux) when you apply.",
            )
        )

    # 3. Platform fit ------------------------------------------------------
    os_name = platform.system()
    if LINUX:
        add(Check("platform", "ok", "Platform", f"{sysinfo.os_label()} - full TAP/VXLAN support"))
    elif WINDOWS:
        add(
            Check(
                "platform",
                "ok",
                "Platform",
                f"{sysinfo.os_label()} - recommend SoftEther for Wi-Fi bridging",
            )
        )
    else:
        add(
            Check(
                "platform",
                "warn",
                "Platform",
                f"{os_name} is not a primary target",
                "Use a Linux VM for the bridge node if possible.",
            )
        )

    # 4. Tooling -----------------------------------------------------------
    found = sysinfo.tools()
    if found.get("openvpn"):
        add(Check("openvpn", "ok", "OpenVPN", "openvpn binary on PATH"))
    else:
        add(
            Check(
                "openvpn",
                "warn",
                "OpenVPN",
                "not on PATH (only needed for the openvpn transport)",
                "Windows: https://openvpn.net/community-downloads/  Linux: `apt install openvpn`",
            )
        )

    if found.get("vpncmd") or (WINDOWS and shutil.which("vpncmd")):
        add(Check("softether", "ok", "SoftEther", "vpncmd on PATH"))
    else:
        add(
            Check(
                "softether",
                "warn",
                "SoftEther",
                "vpncmd not on PATH (only needed for the softether transport)",
                "Install SoftEther VPN Server/Client; vpncmd lives in its install directory.",
            )
        )

    if LINUX:
        if found.get("wg"):
            add(Check("wireguard", "ok", "WireGuard tools", "wg on PATH"))
        else:
            add(
                Check(
                    "wireguard",
                    "warn",
                    "WireGuard tools",
                    "wg not on PATH (only needed for the vxlan transport)",
                    "`apt install wireguard-tools` or `dnf install wireguard-tools`",
                )
            )
        if not found.get("ip"):
            add(Check("iproute2", "fail", "iproute2", "the `ip` command is missing",
                      "`apt install iproute2` - required to create bridges and VXLAN devices"))
        forward = sysinfo.sysctl_value("net.ipv4.ip_forward")
        if forward == "1":
            add(Check("forwarding", "ok", "IPv4 forwarding", "net.ipv4.ip_forward=1"))
        else:
            add(
                Check(
                    "forwarding",
                    "warn",
                    "IPv4 forwarding",
                    f"net.ipv4.ip_forward={forward}",
                    "Only needed for the VXLAN path: `sysctl -w net.ipv4.ip_forward=1`",
                )
            )

    # 5. Adapters ----------------------------------------------------------
    ifaces = sysinfo.interfaces()
    device_names = [i.name.lower() for i in ifaces]
    virtual = sysinfo.virtual_devices(ifaces)
    if virtual:
        add(Check("virtual-device", "ok", "Virtual adapter", "present: " + ", ".join(virtual)))
    else:
        add(
            Check(
                "virtual-device",
                "warn",
                "Virtual adapter",
                "no TAP/TUN/WireGuard device present yet",
                "Create it with the generated script; it disappears after a reboot unless persisted.",
            )
        )

    lan = cfg.host.lan_iface
    match = [i for i in ifaces if i.name.lower() == lan.lower() or lan.lower() in i.description.lower()]
    if match:
        picked = match[0]
        wireless = sysinfo.looks_wireless(picked.name, picked.description)
        if wireless and cfg.tunnel.transport == "openvpn":
            add(
                Check(
                    "lan-iface",
                    "warn",
                    f"Bridge target: {picked.name}",
                    "wireless adapter selected for an OpenVPN bridge",
                    "802.11 uses three MAC addresses, so a Wi-Fi NIC cannot sit in a Linux "
                    "bridge with a client MAC. Use softether, or wire the bridge node to Ethernet.",
                )
            )
        else:
            add(Check("lan-iface", "ok", f"Bridge target: {picked.name}",
                      f"status={picked.status} addresses={', '.join(picked.addresses) or 'none'}"))
    else:
        add(
            Check(
                "lan-iface",
                "warn",
                f"Bridge target: {lan}",
                "no adapter matches host.lan_iface",
                "List adapters with `ip -br addr` (Linux) or `Get-NetAdapter` (Windows) and update the config.",
            )
        )

    # 6. Address plan ------------------------------------------------------
    if not _in_network(cfg.client.static_ip, cfg):
        add(
            Check(
                "static-ip",
                "fail",
                "Client static address",
                f"{cfg.client.static_ip} is outside {cfg.site.subnet}",
                "Both ends must share the AP prefix; fix site.subnet or client.static_ip.",
            )
        )
    else:
        try:
            start = ipaddress.IPv4Address(cfg.site.dhcp_start)
            end = ipaddress.IPv4Address(cfg.site.dhcp_end)
            client_ip = ipaddress.IPv4Address(cfg.client.static_ip)
            in_pool = start <= client_ip <= end
        except ValueError:
            in_pool = False
            add(Check("dhcp-pool", "fail", "DHCP pool", "dhcp_start/dhcp_end are not valid IPv4 addresses"))
        if in_pool:
            add(
                Check(
                    "static-ip",
                    "fail",
                    "Client static address",
                    f"{cfg.client.static_ip} is inside the DHCP pool {cfg.site.dhcp_start}-{cfg.site.dhcp_end}",
                    "Move it outside the pool, or reserve it on the AP; otherwise a lease can take it.",
                )
            )
        elif cfg.client.static_ip in (cfg.site.gateway, cfg.site.bridge_address):
            add(Check("static-ip", "fail", "Client static address",
                      "address collides with the gateway or bridge address",
                      "Pick a different static address."))
        else:
            add(Check("static-ip", "ok", "Client static address",
                      f"{cfg.client.static_ip}/{cfg.prefixlen} is free of the DHCP pool and gateway"))

    if _in_network(cfg.site.gateway, cfg):
        add(Check("gateway", "ok", "Gateway", f"{cfg.site.gateway} inside {cfg.site.subnet}"))
    else:
        add(Check("gateway", "fail", "Gateway", f"{cfg.site.gateway} is outside {cfg.site.subnet}",
                  "Correct site.gateway; route-gateway pushes will otherwise be ignored."))

    bridge_ip = cfg.site.bridge_address
    if _in_network(bridge_ip, cfg):
        add(Check("bridge-address", "ok", "Bridge address",
                  f"{bridge_ip}/{cfg.prefixlen} (the AP node's identity on the LAN)"))
    else:
        add(Check("bridge-address", "fail", "Bridge address", f"{bridge_ip} is outside the subnet",
                  "Set site.bridge_address to a free address in the AP subnet."))

    # 7. Reachability hint -------------------------------------------------
    if not cfg.tunnel.remote_host or cfg.tunnel.remote_host.startswith(("203.0.113", "example")):
        add(
            Check(
                "remote",
                "warn",
                "Remote endpoint",
                "tunnel.remote_host is a placeholder (RFC 5737 documentation range)",
                "Set it to the host node's public IP, or use SoftEther NAT traversal "
                "and skip inbound port forwarding.",
            )
        )
    else:
        add(Check("remote", "ok", "Remote endpoint", f"{cfg.tunnel.remote_host}:{cfg.tunnel.remote_port}"))

    return checks


def summary(checks: list[Check]) -> dict[str, Any]:
    levels = [c.level for c in checks]
    verdict = "fail" if "fail" in levels else "warn" if "warn" in levels else "ok"
    return {
        "verdict": verdict,
        "counts": {level: levels.count(level) for level in ("ok", "warn", "fail")},
        "checks": [asdict(c) for c in checks],
    }
