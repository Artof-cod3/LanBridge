# LanBridge

A small L2 virtual LAN bridge so two machines in different places can share one
subnet and keep their pre-configured static IP addresses.

No dependencies beyond Python 3.11. Nothing is installed, no service is
registered: the tool produces config files and setup scripts you run yourself.

```
lanbridge doctor                         # what this machine is missing
lanbridge plan                           # topology + recommended transport
lanbridge gen -t softether -r both       # write out/softether-{host,client}/
lanbridge gen -t openvpn  -r client --static-ip 192.168.50.77
lanbridge status                         # interfaces, routes, tunnel processes
lanbridge dashboard                      # local UI on 127.0.0.1:8770
```

---

## Which transport

| | SoftEther | OpenVPN TAP | WireGuard + VXLAN |
|---|---|---|---|
| Layer 2 / broadcast | yes | yes | yes |
| Works over Wi-Fi | **yes** | no (Linux bridge limitation) | no |
| Windows host | **yes** | only with a wired NIC | no |
| Needs TAP driver | no | yes | no |
| Overhead | ~70 B | ~70 B | ~110 B |
| Config transparency | medium | **high** | medium |
| Port forward required | no (NAT traversal) | yes | yes |

Default recommendation: **SoftEther** for laptops and Wi-Fi, **OpenVPN TAP**
when the AP node is wired and you want readable config files for the report,
**WireGuard + VXLAN** when both ends are Linux and you want the lowest overhead.

`lanbridge plan` picks one from the detected adapters and explains why.

---

## The address plan

Everything derives from three numbers:

* `site.subnet` - the AP's LAN, e.g. `192.168.50.0/24`
* `site.gateway` - the AP's router, e.g. `192.168.50.1`
* `client.static_ip` - the address your app already expects, e.g. `192.168.50.77`

`doctor` fails the plan if the static address is inside the DHCP pool, equals
the gateway or the bridge address, or falls outside the subnet.

The host keeps `site.bridge_address` (`192.168.50.240` by default) on the LAN so
you have a stable far end to ping.

---

## Install

**Debian 11/12, Ubuntu 22.04/24.04**

```bash
sudo ./install.sh                 # CLI + dashboard
sudo ./install.sh --with openvpn  # or: --with vxlan
```

It installs `python3`, `iproute2`, `nftables`, the `tomli` backport when Python
is older than 3.11, copies the package to `/opt/lanbridge`, and writes a
`/usr/local/bin/lanbridge` launcher. Re-running upgrades in place. It does not
start any tunnel daemon.

**Windows** - nothing to install beyond Python 3.9+:

```powershell
cd LanBridge
python -m lanbridge doctor
```

**From a checkout, no install** - use `python -m lanbridge ...` with the folder
on `PYTHONPATH`; everything works the same.

No third-party runtime packages are required. `tomli` is the only conditional
dependency (Python < 3.11).

## Usage

**Every command is tagged with the machine it runs on.** `[WIN-HOST]` is your
Windows laptop, `[VM-AP]` is the Linux VM on your partner's side, `[VM-YOU]` is
the VM on yours. The full sequence - including hypervisor settings, teardown
order and a symptom-to-machine table - is in [`docs/RUNBOOK.md`](docs/RUNBOOK.md).

The CLI only writes files; it never connects. Generation and inspection can run
anywhere, but the generated `*.sh` must run **inside the VM** it was written for,
because `ip`, `bridge` and `wg` do not exist on Windows.

```bash
# [EITHER] - no privileges needed
lanbridge init                       # writes ./lanbridge.toml
# edit site.subnet, site.gateway, client.static_ip, host.lan_iface
lanbridge doctor
lanbridge plan
lanbridge gen --transport softether --role both -o out
```

```bash
# [VM-AP] root - the machine on the AP
sudo ./host.sh            # or ./bridge-up.sh + openvpn, or ./up.sh for vxlan
sudo nft -f firewall-host.nft
```

```bash
# [VM-YOU] root - your machine
sudo ./client.sh          # or openvpn --config client.ovpn
```

```bash
# [VM-YOU] - dashboard watches the adapters of the machine it runs on
python -m lanbridge dashboard --port 8770
```

Copy `out/<transport>-host/` to the machine on the AP and
`out/<transport>-client/` to yours; each folder's `INSTALL.md` covers its own
paths. Every script is idempotent: re-running it after a reboot rebuilds the
same state.

Ports used: `udp/1194` (OpenVPN), `tcp/1194` (SoftEther), `udp/51820`
(WireGuard). VXLAN `udp/4789` stays inside the WireGuard tunnel and must never
be exposed.

### SSH over the segment

Yes - once the link is up, the other side is an ordinary host on the same
broadcast domain, so `ssh user@192.168.50.77` works with no extra tunnelling.
The only requirements are on the target: `sshd` running (Debian:
`apt install openssh-server`; Windows: enable the *OpenSSH Server* feature) and
`tcp/22` allowed by its firewall. If `ping` answers and `ssh` hangs, the fault
is at the target, not in the tunnel.

---

## Dashboard

`lanbridge dashboard` serves the app on `127.0.0.1:8770`, polling state every
5 seconds:

| View | Contents |
|---|---|
| Dashboard | hero link state, traffic chart, connection details, quick actions, host card, segment checks, activity |
| Servers | transport picker, host/client role, generate, inline preflight |
| Activity | the session event log |
| Diagnostics | full `doctor` list, routes, adapters |
| Files | generated output with a read-only preview |
| Settings | editable address plan + validation |

What is actually live:

* **Traffic** - cumulative `rx`/`tx` counters read from the tunnel device
  (`Get-NetAdapterStatistics` on Windows, `ip -s -j link` on Linux), sampled
every 2 s into a 360-point ring buffer. The chart plots bytes/second derived
  from consecutive samples; before a device exists it says so rather than
  drawing a decorative line.
* **Duration** - measured from the moment the link was first seen with an
  address inside `site.subnet`.
* **Activity** - link transitions, generations, plan saves and preflights, with
  timestamps; it resets when the dashboard restarts.
* **Preflight** - `Connect` never pretends to connect. It checks privileges,
  binaries, endpoint and generated profiles, then prints the exact command to
  run, with a copy button.

Link state is derived from facts, not optimism: `linked` requires a virtual
device that actually holds an address inside `site.subnet`; `adapter` means the
device exists without one; `offline` means neither. Gateway RTT is a real ICMP
echo to `site.gateway` with an 800 ms timeout, and shows `no reply` when the
segment is not reachable — that is the honest answer, not an error.

Read-only against the OS. The only writes are `lanbridge.toml` and files under
`out/`. File previews are confined to the output directory. It refuses to bind
a non-loopback address, rejects cross-origin mutations, and rejects any request
whose `Host` header is not loopback.

### Appearance

One token layer in `static/app.css`, one controller in `static/theme.js`:

* **Themes** - Dark (charcoal), Midnight (graphite), Ocean (blue-grey), Light.
* **Follow system** - a real mode, not a fallback. It reads
  `prefers-color-scheme` and re-applies when the OS changes while the page is
  open.
* **Accent** - slate / azure / sage / clay / steel. Azure is the reference
  layout's blue, kept slightly desaturated; the rest are quieter. Status dots
  keep their own semantic colours, so no accent choice can make *offline* look
  healthy.
* **Density** - compact (24 px rows, 12 px text) and comfortable (30 px, 13 px).
* **Persistence** - `localStorage["lanbridge.appearance"]`, applied before body
  parse so there is no flash of the wrong theme on reload.
* **Transitions** - 140 ms on colour properties only, disabled entirely under
  `prefers-reduced-motion`.
* **Consistency** - every rule reads variables; no view can drift from the
  palette.

---

## Layout

```
lanbridge/
  cli.py            doctor | plan | gen | status | init | dashboard
  settings.py       typed config + TOML round-trip
  sysinfo.py        interfaces, routes, processes (Windows + Linux)
  doctor.py         checks with fixes
  gen/
    openvpn.py      TAP server/client + bridge script + firewall
    softether.py    hub, local bridge, client account scripts
    vxlan.py        WireGuard + VXLAN scripts and configs
    firewall.py     nftables and Windows firewall rules
  dashboard/        stdlib HTTP server + static UI
docs/ARCHITECTURE.md  interfaces, routing tables, firewall, MTU, VM notes
docs/RUNBOOK.md       every command labelled with its machine and privilege
```

Read `docs/ARCHITECTURE.md` before your demo: it covers why TAP, what actually
appears in `ip route`, why Wi-Fi cannot join a Linux bridge, MTU budgets, and
the VM NIC modes (VirtualBox/VMware/Hyper-V/libvirt).

---

## Limits worth stating in the report

* L2 means everyone on the tunnel is on the AP's segment. There is no
  per-host firewall between tunnel members; the tunnel is the trust boundary.
* Bridging over Wi-Fi only works through SoftEther's 802.11 shim. A Linux
  bridge cannot carry a foreign MAC over 802.11, and Hyper-V cannot bind a
  Wi-Fi adapter at all.
* Without a public endpoint you need a relay (SoftEther's NAT traversal or a
  small VPS). Both laptops dialing out to a VPS is the most reliable demo setup.
