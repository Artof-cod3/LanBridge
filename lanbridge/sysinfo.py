"""Cross-platform probes: interfaces, addresses, routes, tunnel processes.

Everything here degrades gracefully. A probe that cannot run returns an empty
list plus an error string instead of raising, because the dashboard must keep
working on a machine with no tunnel installed.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from typing import Any

WINDOWS = sys.platform.startswith("win")
LINUX = sys.platform.startswith("linux")
MACOS = sys.platform == "darwin"


def run(cmd: list[str], timeout: int = 8) -> tuple[int, str, str]:
    """Run a command, never raise. Returns (returncode, stdout, stderr)."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            errors="replace",
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError:
        return 127, "", f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{cmd[0]}: timed out"
    except Exception as exc:  # pragma: no cover - defensive
        return 1, "", str(exc)


def powershell(script: str) -> tuple[int, str, str]:
    """Run a PowerShell snippet with UTF-8 output forced on."""
    prefix = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
    return run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            prefix + script,
        ],
        timeout=25,
    )


def is_admin() -> bool:
    if WINDOWS:
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        except Exception:
            return False
    try:
        return os.geteuid() == 0  # type: ignore[attr-defined]
    except Exception:
        return False


def os_label() -> str:
    return f"{platform.system()} {platform.release()} ({platform.machine()})"


def _json_list(payload: str) -> list[dict[str, Any]]:
    """PowerShell ConvertTo-Json returns an object for a single row."""
    payload = payload.strip()
    if not payload:
        return []
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    return []


# ---------------------------------------------------------------------------
# Interfaces
# ---------------------------------------------------------------------------

@dataclass
class Interface:
    name: str
    status: str = "unknown"
    description: str = ""
    mac: str = ""
    addresses: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.addresses is None:
            self.addresses = []


def interfaces() -> list[Interface]:
    if LINUX:
        return _interfaces_linux()
    if WINDOWS:
        return _interfaces_windows()
    return _interfaces_generic()


def _interfaces_linux() -> list[Interface]:
    rc, out, _ = run(["ip", "-j", "addr", "show"])
    if rc != 0:
        return _interfaces_generic()
    try:
        rows = json.loads(out)
    except json.JSONDecodeError:
        return _interfaces_generic()

    result: list[Interface] = []
    for row in rows:
        addrs = [
            f"{a.get('local')}/{a.get('prefixlen')}"
            for a in row.get("addr_info", [])
            if a.get("family") == "inet"
        ]
        result.append(
            Interface(
                name=row.get("ifname", "?"),
                status="up" if "UP" in (row.get("flags") or []) else "down",
                description=row.get("ifname", ""),
                mac=row.get("address", ""),
                addresses=addrs,
            )
        )
    return result


def _interfaces_windows() -> list[Interface]:
    rc, out, _ = powershell(
        "Get-NetAdapter | Select-Object Name,InterfaceDescription,Status,MacAddress"
        " | ConvertTo-Json -Compress"
    )
    adapters = _json_list(out) if rc == 0 else []
    rc2, out2, _ = powershell(
        "Get-NetIPAddress -AddressFamily IPv4 | Select-Object InterfaceAlias,IPAddress,PrefixLength"
        " | ConvertTo-Json -Compress"
    )
    addresses = _json_list(out2) if rc2 == 0 else []

    by_name: dict[str, list[str]] = {}
    for item in addresses:
        alias = str(item.get("InterfaceAlias", ""))
        ip = item.get("IPAddress")
        if alias and ip:
            by_name.setdefault(alias, []).append(f"{ip}/{item.get('PrefixLength')}")

    return [
        Interface(
            name=str(a.get("Name", "?")),
            status=str(a.get("Status", "unknown")).lower(),
            description=str(a.get("InterfaceDescription", "")),
            mac=str(a.get("MacAddress", "")),
            addresses=by_name.get(str(a.get("Name", "")), []),
        )
        for a in adapters
    ]


def _interfaces_generic() -> list[Interface]:
    """Best-effort fallback (macOS / missing tooling)."""
    rc, out, _ = run(["ifconfig"])
    if rc != 0:
        return []
    result: list[Interface] = []
    current: Interface | None = None
    for line in out.splitlines():
        if line and not line[0].isspace():
            name = line.split(":")[0]
            current = Interface(name=name, description=name, status="up" if "<UP" in line else "down")
            result.append(current)
        elif current is not None and "inet " in line:
            parts = line.split()
            if "inet" in parts:
                idx = parts.index("inet")
                if idx + 1 < len(parts):
                    current.addresses.append(parts[idx + 1])
    return result


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@dataclass
class Route:
    destination: str
    gateway: str = ""
    interface: str = ""
    metric: str = ""


def routes() -> list[Route]:
    if LINUX:
        rc, out, _ = run(["ip", "-j", "route", "show"])
        if rc != 0:
            return []
        try:
            rows = json.loads(out)
        except json.JSONDecodeError:
            return []
        return [
            Route(
                destination=str(r.get("dst", "")),
                gateway=str(r.get("gateway", "")),
                interface=str(r.get("dev", "")),
                metric=str(r.get("metric", "")),
            )
            for r in rows
        ]
    if WINDOWS:
        rc, out, _ = powershell(
            "Get-NetRoute -AddressFamily IPv4 | Select-Object DestinationPrefix,NextHop,"
            "InterfaceAlias,RouteMetric | ConvertTo-Json -Compress"
        )
        if rc != 0:
            return []
        return [
            Route(
                destination=str(r.get("DestinationPrefix", "")),
                gateway=str(r.get("NextHop", "")),
                interface=str(r.get("InterfaceAlias", "")),
                metric=str(r.get("RouteMetric", "")),
            )
            for r in _json_list(out)
        ]
    return []


# ---------------------------------------------------------------------------
# Tunnel processes / devices
# ---------------------------------------------------------------------------

KNOWN_PROCESSES = {
    "openvpn": "OpenVPN",
    "vpnserver": "SoftEther server",
    "vpnclient": "SoftEther client",
    "vpnbridge": "SoftEther bridge",
    "wireguard": "WireGuard (GUI)",
    "wg-quick": "wg-quick",
}

VIRTUAL_PREFIXES = ("tap", "tun", "wg", "vxlan", "utun", "lanbridge")


def processes() -> list[dict[str, str]]:
    """Return running tunnel-ish processes as {name, pid, label}."""
    if WINDOWS:
        rc, out, _ = powershell(
            "Get-Process | Where-Object { $_.ProcessName -match 'openvpn|vpnserver|vpnclient|"
            "vpnbridge|wireguard|wg' } | Select-Object ProcessName,Id | ConvertTo-Json -Compress"
        )
        if rc != 0:
            return []
        return [
            {
                "name": str(p.get("ProcessName", "")),
                "pid": str(p.get("Id", "")),
                "label": KNOWN_PROCESSES.get(str(p.get("ProcessName", "")).lower(), "tunnel"),
            }
            for p in _json_list(out)
        ]

    rc, out, _ = run(["ps", "-eo", "pid=,comm="])
    if rc != 0:
        return []
    found: list[dict[str, str]] = []
    for line in out.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        comm = parts[1].strip().rsplit("/", 1)[-1]
        if comm.lower() in KNOWN_PROCESSES:
            found.append(
                {
                    "name": comm,
                    "pid": parts[0],
                    "label": KNOWN_PROCESSES[comm.lower()],
                }
            )
    return found


def virtual_devices(ifaces: list[Interface] | None = None) -> list[str]:
    ifaces = ifaces if ifaces is not None else interfaces()
    return [i.name for i in ifaces if i.name.lower().startswith(VIRTUAL_PREFIXES)]


def tools() -> dict[str, bool]:
    names = ["openvpn", "wg", "ip", "nft", "ufw", "vpncmd", "iptables", "tcpdump"]
    return {n: shutil.which(n) is not None for n in names}


def sysctl_value(key: str) -> str | None:
    if not LINUX:
        return None
    path = "/proc/sys/" + key.replace(".", "/")
    try:
        return open(path, encoding="utf-8").read().strip()
    except OSError:
        return None


def kernel_release() -> str:
    return platform.release()


def _prefix_length(route: Route) -> int:
    tail = route.destination.rsplit("/", 1)[-1] if "/" in route.destination else "0"
    return int(tail) if tail.isdigit() else 32


def snapshot() -> dict[str, Any]:
    """One JSON-serialisable view of the machine, used by the dashboard.

    Each probe shells out - on Windows four separate PowerShell processes - so
    they run together: a poll costs the slowest probe instead of their sum.
    """
    with ThreadPoolExecutor(max_workers=4) as pool:
        ifaces_future = pool.submit(interfaces)
        routes_future = pool.submit(routes)
        processes_future = pool.submit(processes)
        tools_future = pool.submit(tools)
        ifaces = ifaces_future.result()
        route_list = routes_future.result()
        running = processes_future.result()
        found = tools_future.result()

    ordered = sorted(route_list, key=_prefix_length)  # 0.0.0.0/0 first
    return {
        "platform": os_label(),
        "os": platform.system().lower(),
        "admin": is_admin(),
        "python": platform.python_version(),
        "interfaces": [asdict(i) for i in ifaces],
        "virtual_devices": virtual_devices(ifaces),
        "routes": [asdict(r) for r in ordered[:40]],
        "processes": running,
        "tools": found,
        "forwarding": sysctl_value("net.ipv4.ip_forward"),
        "kernel": kernel_release(),
    }


def counters(device: str) -> dict[str, int] | None:
    """Cumulative rx/tx bytes for one interface, or None if unavailable."""
    if not device:
        return None
    if WINDOWS:
        rc, out, _ = powershell(
            f'Get-NetAdapterStatistics -Name \"{device}\" | '
            "Select-Object ReceivedBytes,SentBytes | ConvertTo-Json -Compress"
        )
        if rc != 0:
            return None
        rows = _json_list(out)
        if not rows:
            return None
        return {
            "rx": int(rows[0].get("ReceivedBytes") or 0),
            "tx": int(rows[0].get("SentBytes") or 0),
        }

    if LINUX:
        rc, out, _ = run(["ip", "-s", "-j", "link", "show", "dev", device])
        if rc != 0:
            return None
        try:
            rows = json.loads(out)
        except json.JSONDecodeError:
            return None
        if not rows:
            return None
        stats = rows[0].get("stats64") or rows[0].get("stats") or {}
        rx = stats.get("rx") or {}
        tx = stats.get("tx") or {}
        return {"rx": int(rx.get("bytes") or 0), "tx": int(tx.get("bytes") or 0)}

    return None


def ping(host: str, timeout_ms: int = 800) -> dict[str, Any]:
    """One ICMP echo. Read-only; used by the dashboard link panel."""
    if not host:
        return {"host": host, "ok": False, "rtt_ms": None}
    if WINDOWS:
        cmd = ["ping", "-n", "1", "-w", str(timeout_ms), host]
    elif MACOS:
        cmd = ["ping", "-c", "1", "-t", str(max(1, timeout_ms // 1000)), host]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, timeout_ms // 1000)), host]
    rc, out, _ = run(cmd, timeout=max(3, timeout_ms // 1000 + 2))
    match = re.search(r"[=<]\s*(\d+(?:\.\d+)?)\s*ms", out)
    return {
        "host": host,
        "ok": rc == 0,
        "rtt_ms": float(match.group(1)) if match else None,
    }


def looks_wireless(name: str, description: str = "") -> bool:
    blob = f"{name} {description}".lower()
    return bool(re.search(r"\b(wi-?fi|wlan|wireless|802\.11|airport)\b", blob))
