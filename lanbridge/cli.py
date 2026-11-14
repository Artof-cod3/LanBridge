"""LanBridge command line interface.

    lanbridge doctor                 environment + address-plan checks
    lanbridge plan                   topology and transport recommendation
    lanbridge gen -t softether -r host
    lanbridge status                  interfaces, routes, tunnel processes
    lanbridge dashboard              local web UI (127.0.0.1 only)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, doctor, gen, sysinfo
from .settings import CONFIG_NAME, Settings, find_config

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


# ---------------------------------------------------------------------------
# Formatting helpers (plain text, no ANSI colour: it has to read well in a
# report appendix and in redirected output).
# ---------------------------------------------------------------------------

MARK = {"ok": "[ok]  ", "warn": "[warn]", "fail": "[fail]", "info": "[info]"}


def _hr(width: int = 68) -> str:
    return "-" * width


def _load(args) -> Settings:
    if getattr(args, "config", None):
        return Settings.load(Path(args.config))
    return Settings.load(find_config(Path.cwd()))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_doctor(args) -> int:
    cfg = _load(args)
    result = doctor.summary(doctor.run_checks(cfg))

    if args.json:
        print(json.dumps(result, indent=2))
        return 1 if result["verdict"] == "fail" else 0

    print(f"LanBridge {__version__}  |  {sysinfo.os_label()}")
    print(f"config      {find_config(Path.cwd())}")
    print(f"site        {cfg.site.name}  {cfg.network_address}/{cfg.prefixlen}  gw {cfg.site.gateway}")
    print(f"client ip   {cfg.client.static_ip}   transport {cfg.tunnel.transport}")
    print(_hr())
    for check in doctor.run_checks(cfg):
        print(f"{MARK.get(check.level, '[----]')} {check.title}")
        if check.detail:
            print(f"         {check.detail}")
        if check.fix:
            print(f"         -> {check.fix}")
    counts = result["counts"]
    print(_hr())
    print(f"verdict {result['verdict'].upper()}   ok={counts['ok']} warn={counts['warn']} fail={counts['fail']}")
    return 1 if result["verdict"] == "fail" else 0


def _recommend(cfg: Settings) -> tuple[str, str]:
    """Pick a transport from platform + adapter facts."""
    ifaces = sysinfo.interfaces()
    lan = cfg.host.lan_iface.lower()
    match = [i for i in ifaces if i.name.lower() == lan or lan in i.description.lower()]
    wireless = bool(match) and sysinfo.looks_wireless(match[0].name, match[0].description)

    if sysinfo.WINDOWS:
        return "softether", "Windows + virtual adapters: SoftEther's L2 switch needs no TAP driver."
    if wireless:
        return "softether", (
            f"{cfg.host.lan_iface} looks wireless; 802.11 cannot join a Linux bridge "
            "with a foreign MAC, so SoftEther's 802.11 shim is the reliable path."
        )
    if sysinfo.LINUX:
        return "vxlan", "Linux host with a wired NIC: WireGuard + VXLAN is the lightest option."
    return "openvpn", "Portable default: widely documented TAP bridge."


def cmd_plan(args) -> int:
    cfg = _load(args)
    transport, why = _recommend(cfg)
    proto = "tcp" if cfg.tunnel.transport == "softether" else "udp"
    print(f"LanBridge topology - {cfg.site.name}")
    print(_hr())
    print(f"""
  client {cfg.client.static_ip}/{cfg.prefixlen}
      |  {cfg.tunnel.transport} tunnel ({proto}/{cfg.tunnel.listen_port})
      v
  host / bridge node {cfg.site.bridge_address}/{cfg.prefixlen}
      |  L2 segment: {cfg.host.bridge_iface} = {cfg.host.lan_iface}
      v
  AP LAN {cfg.network_address}/{cfg.prefixlen}   gw {cfg.site.gateway}
      DHCP pool {cfg.site.dhcp_start} - {cfg.site.dhcp_end}
""")
    print("Transports")
    print(_hr())
    for item in gen.describe():
        print(f"  {item['id']:<10} {item['title']:<22} {item['platforms']}")
        print(f"             {item['description']}")
    print()
    print(f"Recommended for this machine: {transport}")
    print(f"  {why}")
    print()
    print("Next")
    print(_hr())
    print(f"  lanbridge gen --transport {transport} --role host")
    print(f"  lanbridge gen --transport {transport} --role client")
    print(f"  lanbridge doctor --json")
    return 0


def cmd_gen(args) -> int:
    cfg = _load(args)
    if args.transport:
        cfg.tunnel.transport = args.transport
    if args.remote:
        cfg.tunnel.remote_host = args.remote
    if args.static_ip:
        cfg.client.static_ip = args.static_ip
    if args.lan_iface:
        cfg.host.lan_iface = args.lan_iface

    outdir = Path(args.out)
    roles = ["host", "client"] if args.role == "both" else [args.role]
    for role in roles:
        written = gen.write(cfg, cfg.tunnel.transport, role, outdir)
        print(f"{cfg.tunnel.transport}/{role} -> {outdir / (cfg.tunnel.transport + '-' + role)}")
        for path in written:
            print(f"  {path.relative_to(outdir)}  ({path.stat().st_size} bytes)")
    return 0


def cmd_status(args) -> int:
    snap = sysinfo.snapshot()
    if args.json:
        print(json.dumps(snap, indent=2))
        return 0

    print(f"{snap['platform']}   admin={snap['admin']}")
    print(_hr())
    print("Interfaces")
    for iface in snap["interfaces"]:
        addrs = ", ".join(iface["addresses"]) or "-"
        print(f"  {iface['name']:<18} {iface['status']:<8} {addrs}")
    print()
    print("Virtual devices")
    print("  " + (", ".join(snap["virtual_devices"]) or "none"))
    print()
    print("Tunnel processes")
    if snap["processes"]:
        for proc in snap["processes"]:
            print(f"  {proc['label']:<20} pid {proc['pid']}  ({proc['name']})")
    else:
        print("  none running")
    print()
    print("Routes (first 40)")
    for route in snap["routes"]:
        gw = route["gateway"] or "-"
        print(f"  {route['destination']:<20} via {gw:<16} dev {route['interface']}")
    return 0


def cmd_init(args) -> int:
    target = Path(args.path)
    if target.exists() and not args.force:
        print(f"{target} already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    cfg = Settings.load(EXAMPLES / CONFIG_NAME) if (EXAMPLES / CONFIG_NAME).exists() else Settings()
    cfg.save(target)
    print(f"wrote {target}")
    print("edit site.subnet / site.gateway / client.static_ip, then: lanbridge doctor")
    return 0


def cmd_dashboard(args) -> int:
    from .dashboard.server import serve

    cfg = _load(args)
    outdir = Path(args.out)
    serve(cfg, host=args.host, port=args.port, outdir=outdir,
          config_path=Path(args.config) if args.config else find_config(Path.cwd()))
    return 0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lanbridge",
        description="L2 virtual LAN bridge for preserving static IPs across sites.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--version", action="version", version=f"lanbridge {__version__}")
    parser.add_argument("-c", "--config", help=f"path to {CONFIG_NAME}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="check environment and address plan")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("plan", help="show topology and recommended transport")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("gen", help="write configuration files")
    p.add_argument("-t", "--transport", choices=gen.ORDER)
    p.add_argument("-r", "--role", choices=["host", "client", "both"], default="both")
    p.add_argument("-o", "--out", default="out")
    p.add_argument("--remote", help="public hostname/IP of the host node")
    p.add_argument("--static-ip", help="static LAN address for the client")
    p.add_argument("--lan-iface", help="AP-facing adapter on the host node")
    p.set_defaults(func=cmd_gen)

    p = sub.add_parser("status", help="interfaces, routes, tunnel processes")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("init", help="write a starter lanbridge.toml")
    p.add_argument("path", nargs="?", default=CONFIG_NAME)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("dashboard", help="local web dashboard")
    p.add_argument("--host", default="127.0.0.1",
                   help="bind address (loopback only, by design)")
    p.add_argument("--port", type=int, default=8770)
    p.add_argument("-o", "--out", default="out")
    p.set_defaults(func=cmd_dashboard)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
