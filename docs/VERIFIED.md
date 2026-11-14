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

## Not verified

These need real hardware and a second node; no claim is made that they work:

* No SoftEther, OpenVPN or WireGuard process has ever been started.
* No tunnel has carried a packet. Broadcast, ARP and static-IP behaviour over
  the bridge is designed for, not measured.
* `doctor` reports `verdict=fail` inside a container because containers have no
  physical adapters - that is the tool correctly finding no LAN.
* The dashboard was not opened in a browser during these runs; only its module
  import and HTTP routes were exercised.
