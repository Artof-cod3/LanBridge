"""Transport registry."""

from __future__ import annotations

from pathlib import Path

from ..settings import Settings
from . import common, firewall, openvpn, softether, vxlan

MODULES = {
    "softether": softether,
    "openvpn": openvpn,
    "vxlan": vxlan,
}

# Human-facing ordering: recommended first.
ORDER = ["softether", "openvpn", "vxlan"]

DESCRIPTIONS = {
    "softether": "L2 hub + local bridge. Works over Wi-Fi and on Windows. Recommended.",
    "openvpn": "TAP bridge with TLS. Cleanest config files for a write-up. Needs Ethernet on the host.",
    "vxlan": "WireGuard + VXLAN. Linux only, lowest overhead, no TAP driver.",
}


def describe() -> list[dict[str, str]]:
    return [
        {
            "id": name,
            "title": MODULES[name].TITLE,
            "description": DESCRIPTIONS[name],
            "platforms": "Linux" if name == "vxlan" else "Windows, Linux",
        }
        for name in ORDER
    ]


def generate(cfg: Settings, transport: str, role: str) -> dict[str, str]:
    if transport not in MODULES:
        raise ValueError(f"unknown transport {transport!r}")
    if role not in ("host", "client"):
        raise ValueError(f"unknown role {role!r}")
    return MODULES[transport].generate(cfg, role)


def write(cfg: Settings, transport: str, role: str, outdir: Path) -> list[Path]:
    files = generate(cfg, transport, role)
    return common.write_files(outdir / f"{transport}-{role}", files)
