"""Preflight: what is still missing before a transport can come up, and the
exact command that would bring it up.

Deliberately does not execute anything. A VPN tool that quietly runs privileged
scripts from a web page is a worse tool than one that prints the command and
lets you read it first.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import sysinfo
from .settings import Settings

WINDOWS = sysinfo.WINDOWS
LINUX = sysinfo.LINUX


@dataclass
class Step:
    ok: bool
    text: str
    fix: str = ""


def _tools() -> dict[str, bool]:
    return sysinfo.tools()


def _device_present(cfg: Settings, snap: dict[str, Any]) -> bool:
    return bool(snap["virtual_devices"])


def _is_placeholder(host: str) -> bool:
    return not host or host.startswith(("203.0.113", "192.0.2", "example", "198.51.100"))


def _configs_written(cfg: Settings, outdir: Path) -> bool:
    target = outdir / f"{cfg.tunnel.transport}-client"
    return target.exists() and any(target.iterdir())


def run(cfg: Settings, transport: str, outdir: Path) -> dict[str, Any]:
    snap = sysinfo.snapshot()
    tools = _tools()
    steps: list[Step] = []
    add = steps.append

    admin = snap["admin"]
    add(Step(admin, "Administrator / root privileges",
             "" if admin else "Re-run your terminal elevated; adapters cannot be created otherwise."))

    if transport == "softether":
        have = bool(tools.get("vpncmd"))
        add(Step(have, "SoftEther vpncmd available",
                 "" if have else "Install SoftEther VPN Server/Client; vpncmd ships with it."))
        add(Step(not _is_placeholder(cfg.tunnel.remote_host),
                 f"Remote endpoint {cfg.tunnel.remote_host}",
                 "" if not _is_placeholder(cfg.tunnel.remote_host)
                 else "Set tunnel.remote_host, or use SoftEther NAT traversal."))
        add(Step(_configs_written(cfg, outdir),
                 "Client profile generated",
                 "" if _configs_written(cfg, outdir) else "Generate the softether client files first."))
        command = (
            "powershell -ExecutionPolicy Bypass -File out\\softether-client\\client.ps1"
            if WINDOWS
            else "sudo ./out/softether-client/client.sh"
        )
        notes = "Bridges to the AP through the host's local bridge; the client NIC gets the static IP."

    elif transport == "openvpn":
        have = bool(tools.get("openvpn"))
        add(Step(have, "OpenVPN binary on PATH",
                 "" if have else "Install OpenVPN (it also installs the TAP-Windows driver)."))
        add(Step(_device_present(cfg, snap), "TAP adapter present",
                 "" if _device_present(cfg, snap) else "Run the bridge-up script; it creates tap0."))
        add(Step(not _is_placeholder(cfg.tunnel.remote_host),
                 f"Remote endpoint {cfg.tunnel.remote_host}",
                 "" if not _is_placeholder(cfg.tunnel.remote_host)
                 else "Set tunnel.remote_host to the host node's public address."))
        command = (
            'openvpn --config "out\\openvpn-client\\client.ovpn"'
            if WINDOWS
            else "sudo openvpn --config out/openvpn-client/client.ovpn"
        )
        notes = "Requires pki/ next to the .ovpn files, and a server that is already bridged."

    else:
        add(Step(LINUX, "Linux platform", "" if LINUX else "WireGuard + VXLAN needs a Linux host."))
        add(Step(bool(tools.get("wg")), "WireGuard tools on PATH",
                 "" if tools.get("wg") else "Install wireguard-tools."))
        add(Step(not _is_placeholder(cfg.tunnel.remote_host),
                 f"Remote endpoint {cfg.tunnel.remote_host}",
                 "" if not _is_placeholder(cfg.tunnel.remote_host)
                 else "Set tunnel.remote_host to the host node's public address."))
        command = "sudo ./out/vxlan-client/up.sh"
        notes = "Creates vxlan42 over wg0 and puts the static IP on it."

    ready = all(step.ok for step in steps)
    return {
        "transport": transport,
        "ready": ready,
        "steps": [asdict(s) for s in steps],
        "command": command,
        "notes": notes,
    }
