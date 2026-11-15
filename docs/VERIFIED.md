# What has actually been run

Claims in the README are backed by the runs below or they are marked as
unverified. Everything here was executed on 2026-11-14.

## Installer + runtime matrix

`install.sh` was executed as root inside clean container images, with the
repository mounted read-only, exactly as a user would run it:

```bash
docker run --rm -v "$PWD:/src:ro" -w /src <image> bash ./install.sh
```

| Image | Distro | Python | Result |
|---|---|---|---|
| `debian:bullseye-slim` | Debian 11 | 3.9.2 (from apt) | install + self-test pass |
| `python:3.9-slim-bullseye` | Debian 11 | 3.9.23 | install + self-test pass |
| `ubuntu:22.04` | Ubuntu 22.04 | 3.10.12 (from apt) | install + self-test pass |
| `debian:bookworm-slim` | Debian 12 | 3.11.2 | install + self-test pass |
| Git Bash on Windows 11 | - | 3.14.0 | `./install.sh --self-test` pass |

Debian 13 and Ubuntu 24.04 were not run. They are the same package set as
Debian 12 / Ubuntu 22.04, so they are expected to work, not proven to.

Each run covers: `apt` dependency install, the `tomli` backport path on every
interpreter below 3.11, copying to `/opt/lanbridge`, writing
`/usr/local/bin/lanbridge`, and the full self-test below.

## What the self-test asserts

`./install.sh --self-test` runs the same checks without root and without
touching the system:

1. all 14 modules import, including `lanbridge.dashboard.server`
2. `lanbridge --version` answers through the installed launcher
3. `init` writes a config that round-trips, with the right netmask,
   broadcast and gateway derived from the subnet
4. `doctor --json` completes and emits valid JSON (exit 1 is accepted: it
   means "the plan has findings", which is a result, not a crash)
5. `gen` produces profiles for **all three** transports for both roles, with
   no empty files
6. installed mode only: the launcher still works under `env -i`, so an
   inherited `PYTHONPATH` cannot mask a broken install

Any failure aborts with a non-zero exit and names the step. The installer never
prints "Done" over a broken tree.

## The bug this found

The `>=3.9` claim was false. `lanbridge/gen/common.py` called
`Path.write_text(..., newline="\n")`, and the `newline` parameter was added in
Python 3.10. On 3.9 every `lanbridge gen` died with `TypeError`. The static
audit had passed because it only checked *grammar*: the call is valid syntax on
3.9, it just fails at runtime. Fixed by opening the file explicitly.

`scripts/check_pycompat.py` now also scans for 3.10+ API additions
(`write_text(newline=)`, `read_text(newline=)`, `dataclass(slots=)`,
`ParamSpec`, `TypeAlias`, `Path.hardlink_to`) and strips comments first so
prose about an API is not mistaken for its use. It is still a heuristic; the
container matrix is the real evidence.

## Debian 11 package note

The `debian:bullseye-slim` image ships a `bullseye-security` index that
advertises `python3 3.9.2-1+deb11u7` while the pool only serves other versions,
so a plain `apt-get install python3` returns 404. This is a stale mirror, not a
LanBridge problem, and a stock Debian 11 system already has `python3` from its
base install. `install_pkgs` now retries with `--fix-missing` and then falls
back to the release pocket (`-t bullseye`) so the install completes instead of
dying on the 404.

## End-to-end tunnel proof (single machine)

`scripts/e2e_namespaces.py` proves the WireGuard + VXLAN path without a second
machine. It runs inside `debian:bookworm-slim` with `--privileged`, creates
three Linux network namespaces, and executes the **generated** host and client
`up.sh` scripts unchanged (with real `wg genkey` pairs injected):

| Namespace | Role |
|---|---|
| `lb-e2e-ap` | the AP's LAN (`192.168.50.1/24` on `va`) |
| `lb-e2e-host` | AP-side bridge node, runs `vxlan-host/up.sh` |
| `lb-e2e-cli` | remote client, runs `vxlan-client/up.sh`, keeps `192.168.50.77/24` |

`ap0` (host) and `va` (AP) are a veth pair standing in for the AP's physical
segment. `vh`/`vc` (10.255.0.0/30) are the "internet" underlay that WireGuard
rides on; VXLAN rides inside WireGuard. There is no packet simulation.

Result: **25 passed, 0 failed** (kernel 6.6.114.1-microsoft-standard-WSL2).

| Assertion | Result |
|---|---|
| without a tunnel the static IP cannot reach the AP | pass (control) |
| generated `up.sh` exits 0 on host and client | pass |
| `vxlan42` id 42, pinned to the peer over `wg0`, enslaved to `br0` on host | pass |
| client keeps `192.168.50.77/24` on `vxlan42`; AP subnet on-link via `vxlan42` | pass |
| client pings `192.168.50.1`; ARP resolves to the AP's real MAC | pass |
| AP and host bridge ping `192.168.50.77`; AP learns the client's MAC | pass |
| broadcast/ARP for an unknown host is delivered across the tunnel | pass |
| `ping -M do -s 1342` (full inner MTU 1370) succeeds | pass |
| both sides complete a WireGuard handshake and carry real bytes | pass |

The harness itself found a generator bug: the emitted WireGuard configs
included `Address`/`MTU` (wg-quick keys) while `up.sh` applies them with
`ip addr` and calls `wg setconf`, which rejects those keys with "Configuration
parsing error". Fixed in `lanbridge/gen/vxlan.py` so the configs are valid for
both `wg setconf` and the script-driven path.

## Not verified

The namespace proof is real L2/L3 traffic, but it is not the whole product:

* **Only the WireGuard + VXLAN transport** was exercised end to end. The
  recommended **SoftEther** path and **OpenVPN TAP** were not started and have
  not carried a packet. (Their configs are generated and syntactically checked;
  their daemons are not.)
* **No real internet/NAT path.** The "internet" was a local veth pair; real
  deployment still depends on reachability/NAT traversal, firewall rules, and
  endpoint stability.
* **No real Wi-Fi / physical AP segment.** The AP side was a namespace and a
  veth, not an 802.11 link. The Wi-Fi bridging limitations the docs warn about
  (foreign MACs over a station interface, Hyper-V vs Wi-Fi) remain real-world
  constraints, not disproven here.
* **No Windows runtime.** Everything above ran on Linux.
* **No partner machine / two physical hosts.** This is a single-laptop proof.
* `doctor` reports `verdict=fail` inside a container because containers have no
  physical adapters - that is the tool correctly finding no LAN.
* The dashboard was not opened in a browser during these runs; only its module
  import and HTTP routes were exercised.
