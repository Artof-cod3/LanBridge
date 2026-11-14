"""The runbook as data, so the dashboard can show it.

Every step carries the machine it must run on: "win-host" (your Windows
laptop), "vm-ap" (Linux VM on the partner's side), "vm-you" (your VM), or
"either". That tag is the whole point - a command shown without it is how
people end up running `ip link` on Windows.
"""

from __future__ import annotations

from typing import Any

MACHINES: list[dict[str, str]] = [
    {
        "id": "win-host",
        "label": "Windows host",
        "detail": "Your Windows laptop. Generation, dashboard, hypervisor settings. No admin.",
    },
    {
        "id": "vm-ap",
        "label": "AP-side VM",
        "detail": "Linux VM on your partner's side, NIC bridged to his AP. Runs the host profile as root.",
    },
    {
        "id": "vm-you",
        "label": "Your VM",
        "detail": "Linux VM on your side. Runs the client profile as root.",
    },
    {
        "id": "either",
        "label": "Any machine",
        "detail": "Pure file generation or inspection. No privileges required.",
    },
    {
        "id": "win-host-admin",
        "label": "Windows host (admin)",
        "detail": "Elevated PowerShell. Only for SoftEther-on-Windows or firewall rules.",
    },
]

STEPS: list[dict[str, Any]] = [
    {
        "id": "generate",
        "title": "Generate the profiles",
        "machine": "either",
        "body": "Writes both roles under out/. The CLI only writes files; it never connects.",
        "commands": ["python -m lanbridge gen --transport softether --role both -o out"],
    },
    {
        "id": "copy",
        "title": "Copy each folder to its machine",
        "machine": "either",
        "body": "out/softether-host/ goes to the AP-side VM, out/softether-client/ to yours. "
                "A shared folder, scp or drag-and-drop all work.",
        "commands": [],
    },
    {
        "id": "host",
        "title": "Bring up the host side",
        "machine": "vm-ap",
        "body": "Creates the hub and local bridge to the AP-facing NIC. Use the exact adapter "
                "name from `ip -br addr` (it is usually eth0, ens33 or enp0s3).",
        "commands": [
            "cd /path/to/softether-host",
            "sudo ./host.sh              # openvpn: sudo ./bridge-up.sh && sudo openvpn --config server.ovpn",
            "sudo nft -f firewall-host.nft",
        ],
    },
    {
        "id": "host-check",
        "title": "Check the bridge before touching the client",
        "machine": "vm-ap",
        "body": "If the tap device is not a bridge member, nothing on the client side can fix it.",
        "commands": ["ip -br addr show", "bridge link show"],
    },
    {
        "id": "client",
        "title": "Connect the client",
        "machine": "vm-you",
        "body": "Pins the static address from your plan onto the virtual NIC.",
        "commands": ["cd /path/to/softether-client", "sudo ./client.sh"],
    },
    {
        "id": "verify",
        "title": "Prove the segment",
        "machine": "vm-you",
        "body": "ping proves routing. arping proves broadcast - that is the one that matters; "
                "if ping works and arping fails, the bridge is not passing frames.",
        "commands": [
            "ip -4 addr show dev tap0",
            "ping -c3 192.168.50.1",
            "arping -I tap0 192.168.50.1",
            "ping -M do -s 1372 192.168.50.1",
        ],
    },
    {
        "id": "teardown",
        "title": "Tear down, client first",
        "machine": "vm-you",
        "body": "Drop the client before the host bridge, or the client ARPs into a segment "
                "that no longer exists.",
        "commands": ["sudo ip link del tap0 2>/dev/null || true"],
    },
]

NOTES: list[dict[str, str]] = [
    {
        "title": "The dashboard sees only its own machine",
        "body": "It reports the adapters of wherever it runs. Watching the link from your "
                "Windows host while the tunnel lives in a VM will show Disconnected - that is "
                "correct. Run it in the VM (loopback-only, so use ssh -L 8770:127.0.0.1:8770).",
    },
    {
        "title": "Wi-Fi changes the answer",
        "body": "A Linux bridge cannot carry a foreign MAC over 802.11, and Hyper-V cannot bind "
                "a Wi-Fi adapter. On Wi-Fi, run SoftEther on the Windows host itself and skip "
                "VM NIC bridging.",
    },
    {
        "title": "The .sh files are not Windows files",
        "body": "ip, bridge and wg do not exist on Windows. A generated .sh belongs in the VM it "
                "was written for.",
    },
]

TROUBLESHOOT: list[dict[str, str]] = [
    {"symptom": "Dashboard says Disconnected while a tunnel is running",
     "where": "win-host", "fix": "You are watching the wrong machine. Run the dashboard where the link is."},
    {"symptom": "ping works, arping fails",
     "where": "vm-ap", "fix": "The tap device is not a bridge member. Check `bridge link show`."},
    {"symptom": "Client never receives the static address",
     "where": "vm-you", "fix": "The connect script could not find the virtual NIC; check the account status."},
    {"symptom": "Works, then dies after reboot",
     "where": "vm-ap", "fix": "br0/tap0 are runtime-only. Re-run the bring-up script."},
    {"symptom": "Small transfers fine, large ones hang",
     "where": "vm-you", "fix": "MTU. Lower tun-mtu to 1400 and retry the -M do probe."},
    {"symptom": "Adapter name not found in the VM",
     "where": "vm-ap", "fix": "Fix host.lan_iface under Settings (eth0 vs ens33 vs enp0s3)."},
]


def payload() -> dict[str, Any]:
    return {
        "machines": MACHINES,
        "steps": STEPS,
        "notes": NOTES,
        "troubleshoot": TROUBLESHOOT,
        "verified": False,
        "verified_note": (
            "These steps come from the generated profiles. The tunnel itself has not been "
            "exercised on this machine: no SoftEther/OpenVPN install, no elevation, no "
            "second node. Treat the sequence as the plan to follow, not as a tested result."
        ),
    }
