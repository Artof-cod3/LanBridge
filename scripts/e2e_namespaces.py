#!/usr/bin/env python3
"""End-to-end proof of the WireGuard + VXLAN path on a single machine.

Three network namespaces stand in for three machines:

    [ lb-e2e-ap ]        [ lb-e2e-host ]        [ lb-e2e-cli ]
     192.168.50.1  --va-- ap0 ... br0 ... vxlan42 ==wg0== vxlan42
                                   |
                                10.255.0.1 --vh-- 10.255.0.2

The AP namespace is the Wi-Fi router's LAN. The host namespace runs the
generated `vxlan-host/up.sh` verbatim; the client namespace runs
`vxlan-client/up.sh` verbatim. The veth pair between host and client is the
"internet": WireGuard rides on it, VXLAN rides inside WireGuard.

Nothing here is simulated at the L2 level - these are real veth, vxlan and
wireguard devices in the running kernel. What it cannot prove is anything
about the real internet path (NAT, a public endpoint, Wi-Fi).

Usage:
    sudo python3 scripts/e2e_namespaces.py [--keep] [--verbose]

Requires: Linux, root, iproute2, wireguard-tools, iputils-ping.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NS_AP = "lb-e2e-ap"
NS_HOST = "lb-e2e-host"
NS_CLI = "lb-e2e-cli"

# Underlay: the stand-in for the public internet between the two sites.
UNDERLAY_HOST = "10.255.0.1"
UNDERLAY_CLI = "10.255.0.2"

VERBOSE = False
results: list[tuple[bool, str, str]] = []


# --------------------------------------------------------------------- shell
def run(args: list[str], ns: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    argv = ["ip", "netns", "exec", ns, *args] if ns else args
    proc = subprocess.run(argv, capture_output=True, text=True)
    if VERBOSE:
        print(f"    $ {' '.join(argv)} -> {proc.returncode}")
        if proc.stdout.strip():
            print("      " + proc.stdout.strip().replace("\n", "\n      "))
        if proc.stderr.strip():
            print("      ! " + proc.stderr.strip().replace("\n", "\n      "))
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(argv)} exited {proc.returncode}\n{proc.stderr.strip()}")
    return proc


def out(args: list[str], ns: str | None = None) -> str:
    return run(args, ns=ns).stdout.strip()


def step(title: str) -> None:
    print(f"\n  [....] {title}")


def record(ok: bool, title: str, detail: str = "") -> None:
    results.append((ok, title, detail))
    mark = "ok  " if ok else "FAIL"
    print(f"  [{mark}] {title}" + (f" - {detail}" if detail else ""))


def require(ok: bool, title: str, detail: str = "") -> None:
    """A check whose failure makes the rest of the run meaningless."""
    if not ok:
        record(False, title, detail)
        raise SystemExit(1)
    record(True, title, detail)


# ------------------------------------------------------------------- helpers
def link_mac(ns: str, iface: str) -> str:
    text = out(["ip", "-d", "link", "show", iface], ns=ns)
    match = re.search(r"link/ether ([0-9a-f:]{17})", text)
    return match.group(1) if match else ""


def counters(ns: str, iface: str) -> tuple[int, int]:
    """(rx_packets, tx_packets) from `ip -s link`."""
    text = out(["ip", "-s", "link", "show", iface], ns=ns)
    blocks = {}
    for name in ("RX", "TX"):
        match = re.search(rf"{name}:\s*bytes\s+packets.*\n\s*(\d+)\s+(\d+)", text)
        if match:
            blocks[name] = int(match.group(2))
    return blocks.get("RX", 0), blocks.get("TX", 0)


def ping(ns: str, target: str, count: int = 2, size: int | None = None, wait: int = 2) -> bool:
    args = ["ping", "-c", str(count), "-W", str(wait), "-n"]
    if size:
        args += ["-M", "do", "-s", str(size)]
    args.append(target)
    return run(args, ns=ns, check=False).returncode == 0


def neigh_mac(ns: str, iface: str, ip: str) -> tuple[str, str]:
    text = out(["ip", "neigh", "show", ip, "dev", iface], ns=ns)
    mac = re.search(r"lladdr ([0-9a-f:]{17})", text)
    state = re.search(r"\b(REACHABLE|STALE|DELAY|PROBE|FAILED|INCOMPLETE)\b", text)
    return (mac.group(1) if mac else ""), (state.group(1) if state else "NONE")


def wg_keypair() -> tuple[str, str]:
    priv = out(["wg", "genkey"])
    pub = subprocess.run(
        ["wg", "pubkey"], input=priv, capture_output=True, text=True, check=True
    ).stdout.strip()
    return priv, pub


# ------------------------------------------------------------------- scenario
def build_networks() -> None:
    for ns in (NS_AP, NS_HOST, NS_CLI):
        run(["ip", "netns", "delete", ns], check=False)
        run(["ip", "netns", "add", ns])
        run(["ip", "link", "set", "lo", "up"], ns=ns)

    # Underlay "internet" between the two sites.
    run(["ip", "link", "add", "vh", "type", "veth", "peer", "name", "vc"])
    run(["ip", "link", "set", "vh", "netns", NS_HOST])
    run(["ip", "link", "set", "vc", "netns", NS_CLI])
    run(["ip", "addr", "add", f"{UNDERLAY_HOST}/30", "dev", "vh"], ns=NS_HOST)
    run(["ip", "link", "set", "vh", "up"], ns=NS_HOST)
    run(["ip", "addr", "add", f"{UNDERLAY_CLI}/30", "dev", "vc"], ns=NS_CLI)
    run(["ip", "link", "set", "vc", "up"], ns=NS_CLI)

    # The AP's LAN: a veth whose host end is named ap0, matching host.lan_iface.
    run(["ip", "link", "add", "va", "type", "veth", "peer", "name", "ap0"])
    run(["ip", "link", "set", "va", "netns", NS_AP])
    run(["ip", "link", "set", "ap0", "netns", NS_HOST])
    run(["ip", "addr", "add", "192.168.50.1/24", "dev", "va"], ns=NS_AP)
    run(["ip", "link", "set", "va", "up"], ns=NS_AP)
    run(["ip", "link", "set", "ap0", "up"], ns=NS_HOST)


def write_config(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "# Generated by scripts/e2e_namespaces.py - single-machine proof",
                "[site]",
                'name = "e2e-lab"',
                'subnet = "192.168.50.0/24"',
                'gateway = "192.168.50.1"',
                'dhcp_start = "192.168.50.200"',
                'dhcp_end = "192.168.50.219"',
                'bridge_address = "192.168.50.240"',
                "",
                "[tunnel]",
                'transport = "vxlan"',
                f'remote_host = "{UNDERLAY_HOST}"',
                "listen_port = 1194",
                "remote_port = 1194",
                "mtu = 1500",
                "wg_port = 51820",
                'wg_host_ip = "10.99.0.1"',
                'wg_client_ip = "10.99.0.2"',
                "vxlan_id = 42",
                "vxlan_port = 4789",
                "",
                "[host]",
                'lan_iface = "ap0"',
                'tap_iface = "tap0"',
                'bridge_iface = "br0"',
                'wg_iface = "wg0"',
                'vxlan_iface = "vxlan42"',
                "",
                "[client]",
                'static_ip = "192.168.50.77"',
                'tap_iface = "tap0"',
                'vpn_adapter_name = "LanBridge"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def patch_conf(text: str, priv: str, peer_pub: str) -> str:
    return (
        text.replace("<HOST_PRIVKEY>", priv)
        .replace("<CLIENT_PRIVKEY>", priv)
        .replace("<HOST_PUBKEY>", peer_pub)
        .replace("<CLIENT_PUBKEY>", peer_pub)
    )


def run_up(ns: str, script: Path, conf: Path) -> subprocess.CompletedProcess:
    """Run a generated up.sh with /etc/wireguard bind-mounted to a private dir."""
    confdir = conf.parent
    inner = (
        f"mount --bind {shlex.quote(str(confdir))} /etc/wireguard && "
        f"exec bash {shlex.quote(str(script))}"
    )
    return run(
        ["unshare", "-m", "--propagation", "private", "sh", "-c", inner],
        ns=ns,
        check=False,
    )


def main() -> int:
    global VERBOSE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="leave the namespaces in place")
    parser.add_argument("--verbose", action="store_true", help="print every command")
    args = parser.parse_args()
    VERBOSE = args.verbose

    if sys.platform != "linux":
        print("This harness needs Linux network namespaces.", file=sys.stderr)
        return 2
    if os.geteuid() != 0:
        print("Run as root: sudo python3 scripts/e2e_namespaces.py", file=sys.stderr)
        return 2
    missing = [tool for tool in ("ip", "wg", "ping", "unshare") if not shutil.which(tool)]
    if missing:
        print(f"missing tools: {', '.join(missing)}", file=sys.stderr)
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="lanbridge-e2e-"))
    print(f"Workspace: {tmp}")
    try:
        # ---------------------------------------------------------- generate
        step("generate the VXLAN profiles from a test plan")
        cfg = tmp / "lanbridge.toml"
        write_config(cfg)
        gen = subprocess.run(
            [sys.executable, "-m", "lanbridge", "-c", str(cfg),
             "gen", "-t", "vxlan", "-r", "both", "-o", str(tmp / "out")],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            capture_output=True,
            text=True,
        )
        require(
            gen.returncode == 0,
            "lanbridge gen -t vxlan -r both",
            gen.stderr.strip()[:200],
        )
        host_dir = tmp / "out" / "vxlan-host"
        cli_dir = tmp / "out" / "vxlan-client"
        require(
            (host_dir / "up.sh").exists() and (cli_dir / "up.sh").exists(),
            "generated up.sh for both roles",
        )

        # ------------------------------------------------------------- keys
        step("real WireGuard keys in place of the placeholders")
        host_priv, host_pub = wg_keypair()
        cli_priv, cli_pub = wg_keypair()
        wgdir = Path("/etc/wireguard")
        wgdir.mkdir(parents=True, exist_ok=True)
        for role_dir, priv, peer in ((host_dir, host_priv, cli_pub), (cli_dir, cli_priv, host_pub)):
            conf = next(role_dir.glob("wg-*.conf"))
            patched = patch_conf(conf.read_text(encoding="utf-8"), priv, peer)
            require("<" not in patched, f"{conf.name}: placeholders replaced")
            confdir = role_dir / "etc-wireguard"
            confdir.mkdir(exist_ok=True)
            (confdir / "wg0.conf").write_text(patched, encoding="utf-8")
            (confdir / "wg0.conf").chmod(0o600)

        # -------------------------------------------------------- namespaces
        step("build the three namespaces")
        build_networks()
        require(ping(NS_CLI, "192.168.50.1", count=1, wait=1) is False,
                "control: without a tunnel the static IP cannot reach the AP")

        # -------------------------------------------------------- bring up
        step("run the generated host up.sh in lb-e2e-host")
        host_up = run_up(NS_HOST, host_dir / "up.sh", host_dir / "etc-wireguard" / "wg0.conf")
        require(host_up.returncode == 0, "host up.sh exited 0",
                (host_up.stderr.strip().splitlines() or [""])[-1][:200])

        step("run the generated client up.sh in lb-e2e-cli")
        cli_up = run_up(NS_CLI, cli_dir / "up.sh", cli_dir / "etc-wireguard" / "wg0.conf")
        require(cli_up.returncode == 0, "client up.sh exited 0",
                (cli_up.stderr.strip().splitlines() or [""])[-1][:200])

        # ------------------------------------------------------- assertions
        step("device shape")
        vx_host = out(["ip", "-d", "link", "show", "vxlan42"], ns=NS_HOST)
        vx_cli = out(["ip", "-d", "link", "show", "vxlan42"], ns=NS_CLI)
        record("vxlan id 42" in vx_host, "host vxlan42 has vxlan id 42")
        record("vxlan id 42" in vx_cli, "client vxlan42 has vxlan id 42")
        record("10.99.0.2" in vx_host and "dev wg0" in vx_host,
               "host vxlan42 is pinned to peer 10.99.0.2 over wg0")
        record("10.99.0.1" in vx_cli and "dev wg0" in vx_cli,
               "client vxlan42 is pinned to peer 10.99.0.1 over wg0")
        record("master br0" in out(["ip", "-d", "link", "show", "vxlan42"], ns=NS_HOST),
               "host vxlan42 is enslaved to br0")

        step("the client keeps its pre-configured static address")
        addr = out(["ip", "-4", "-o", "addr", "show", "vxlan42"], ns=NS_CLI)
        record("192.168.50.77/24" in addr, "client vxlan42 carries 192.168.50.77/24",
               addr.split("inet ")[-1].split(" ")[0] if "inet " in addr else addr[:60])
        route = out(["ip", "route", "get", "192.168.50.1"], ns=NS_CLI)
        record("dev vxlan42" in route, "AP subnet is on-link via vxlan42", route.split(" via ")[0][:70])

        step("ARP across the tunnel")
        ap_mac = link_mac(NS_AP, "va")
        record(ping(NS_CLI, "192.168.50.1", count=3), "client ping 192.168.50.1 (the AP)")
        mac, state = neigh_mac(NS_CLI, "vxlan42", "192.168.50.1")
        record(state == "REACHABLE" and mac == ap_mac,
               "client resolved the AP by ARP", f"{mac or 'no entry'} ({state})")
        record(mac == ap_mac, "that MAC belongs to the AP's interface, not a proxy",
               f"expected {ap_mac}")

        step("the return path: AP reaches the static IP directly")
        record(ping(NS_AP, "192.168.50.77", count=3), "AP ping 192.168.50.77 (static IP)")
        record(ping(NS_HOST, "192.168.50.77", count=2), "host bridge (192.168.50.240) ping 192.168.50.77")
        cli_ll, _ = neigh_mac(NS_AP, "va", "192.168.50.77")
        record(bool(cli_ll), "AP learned the client's MAC over the segment", cli_ll)

        step("broadcast is carried, not dropped")
        rx_before, _ = counters(NS_CLI, "vxlan42")
        # .99 does not exist, so this is an ARP who-has broadcast from the AP.
        # Run it a few times and give the tunnel a moment to deliver.
        for _ in range(3):
            ping(NS_AP, "192.168.50.99", count=1, wait=1)
        run(["sleep", "0.3"], ns=NS_CLI, check=False)
        rx_after, _ = counters(NS_CLI, "vxlan42")
        record(rx_after > rx_before, "client received the broadcast frames",
               f"rx {rx_before} -> {rx_after}")

        step("MTU 1370 inside the tunnel")
        size = 1370 - 28
        record(ping(NS_CLI, "192.168.50.1", count=2, size=size),
               f"ping -M do -s {size} (full inner MTU)")

        step("WireGuard actually carried the traffic")
        hs = out(["wg", "show", "wg0", "latest-handshakes"], ns=NS_CLI).split()
        record(len(hs) >= 2 and hs[1] != "0", "client completed a handshake",
               f"epoch {hs[1]}" if len(hs) >= 2 else "no peer")
        transfer = out(["wg", "show", "wg0", "transfer"], ns=NS_CLI).split()
        rx = int(transfer[1]) if len(transfer) >= 2 else 0
        tx = int(transfer[2]) if len(transfer) >= 3 else 0
        record(rx > 0 and tx > 0, "client encrypted and decrypted real bytes",
               f"rx {rx} B / tx {tx} B")
        host_hs = out(["wg", "show", "wg0", "latest-handshakes"], ns=NS_HOST).split()
        record(len(host_hs) >= 2 and host_hs[1] != "0", "host completed a handshake")

    except RuntimeError as err:
        record(False, "harness error", str(err)[:300])
    finally:
        if not args.keep:
            for ns in (NS_AP, NS_HOST, NS_CLI):
                subprocess.run(["ip", "netns", "delete", ns], capture_output=True)
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print(f"\nKept: {', '.join((NS_AP, NS_HOST, NS_CLI))} and {tmp}")

    failed = [r for r in results if not r[0]]
    print("\n" + "-" * 68)
    print(f"{len(results) - len(failed)} passed, {len(failed)} failed")
    if failed:
        for _, title, detail in failed:
            print(f"  FAIL {title}" + (f" - {detail}" if detail else ""))
        return 1
    print("The generated WireGuard + VXLAN profiles carried ARP and a static IP")
    print("across two real namespaces on this machine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
