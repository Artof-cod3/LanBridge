# Runbook: which machine runs what

Every command below starts with a tag telling you **where it runs** and **with
what privileges**. If a command has no tag, it is wrong for this project.

## The tags

| Tag | Machine | Notes |
|---|---|---|
| `[WIN-HOST]` | Your Windows 11 laptop, normal shell | Generation, dashboard, hypervisor settings. No admin needed. |
| `[WIN-HOST·ADMIN]` | Same laptop, PowerShell as Administrator | Only for SoftEther-on-Windows or firewall rules. |
| `[VM-AP]` | Linux VM on your partner's side, bridged to his AP | Runs the **host** profile. Root. |
| `[VM-YOU]` | Linux VM on your side | Runs the **client** profile. Root. |
| `[EITHER]` | Any of the above | Pure file generation, no privileges. |

Two rules that prevent almost every mistake:

1. **The CLI generates; it does not connect.** `lanbridge gen/doctor/plan` can
   run anywhere. The generated `*.sh` files must run **inside the VM they were
   written for** — not on Windows, where `ip`, `bridge` and `wg` do not exist.
2. **Never run a bring-up script on `[WIN-HOST]`** unless the transport is
   SoftEther and you deliberately chose the Windows-host variant (below).

---

## 0. Get the code to each machine

```bash
# [WIN-HOST]
cd "C:\Users\kirui\Documents\The Hustle\LanBridge"
python -m lanbridge gen --transport softether --role both -o out
```

Then copy each folder to the machine that owns it:

| Folder | Goes to |
|---|---|
| `out\softether-host\` | `[VM-AP]` |
| `out\softether-client\` | `[VM-YOU]` (or `[WIN-HOST]` if your client is Windows) |

Copy with a shared folder, `scp`, or drag-and-drop. If you copy into a Linux
VM, keep the `INSTALL.md` next to the scripts — it names the paths.

---

## 1. Pick the host runtime first

This decides the column you follow for the rest of the runbook.

| AP-side runtime | Transports | Why |
|---|---|---|
| **Linux VM, bridged NIC** | softether, openvpn, vxlan | Full L2 control; `br0` is yours. |
| **Windows host directly** | softether only | Works over Wi-Fi without any VM NIC bridging (Hyper-V cannot bridge Wi-Fi, and Windows cannot bridge a Wi-Fi adapter to a TAP). |

If your partner's laptop is on Wi-Fi and you are using VirtualBox/VMware, the
Linux VM path works but the VM's NIC must be **Bridged**, not NAT. On Hyper-V,
use the Windows-host variant instead.

---

## 2. Host side

### 2a. AP side = Linux VM (`[VM-AP]`)

```bash
# [VM-AP]  root
cd /path/to/softether-host        # or openvpn-host / vxlan-host
sudo ./host.sh                    # softether: creates hub + local bridge
# or
sudo ./bridge-up.sh               # openvpn: creates br0, enslaves tap0
sudo openvpn --config server.ovpn
# or
sudo ./up.sh                      # vxlan: wg0 + vxlan42 + br0
```

Firewall — same machine, once:

```bash
# [VM-AP]  root
sudo nft -f firewall-host.nft
```

Check before touching the client:

```bash
# [VM-AP]  root
ip -br addr show                    # br0 carries 192.168.50.240/24
bridge link show                    # tap0 (or vxlan42) is a member
```

### 2b. AP side = the Windows machine itself (`[WIN-HOST·ADMIN]`)

SoftEther only, no VM involved:

```powershell
# [WIN-HOST·ADMIN]
cd "C:\Users\kirui\Documents\The Hustle\LanBridge"
powershell -ExecutionPolicy Bypass -File out\softether-host\host.ps1
powershell -ExecutionPolicy Bypass -File out\softether-host\firewall-host.ps1
```

The bridge device must be the adapter facing the AP: `Wi-Fi`, `Ethernet`,
exactly as `Get-NetAdapter` prints it.

---

## 3. Client side

```bash
# [VM-YOU]  root
cd /path/to/softether-client
sudo ./client.sh
```

```powershell
# [WIN-HOST·ADMIN]  if your client is Windows instead
powershell -ExecutionPolicy Bypass -File out\softether-client\client.ps1
```

OpenVPN client, either platform:

```bash
# [VM-YOU]  root
sudo openvpn --config client.ovpn
```
```powershell
# [WIN-HOST]  OpenVPN GUI → import client.ovpn
```

VXLAN client:

```bash
# [VM-YOU]  root
sudo ./up.sh
```

---

## 4. Dashboard: run it on the machine you want to watch

```bash
# [VM-YOU]  (or [WIN-HOST], or [VM-AP]) — no root
python -m lanbridge dashboard --port 8770
```

**It reports the adapters of the machine it runs on.** If the tunnel lives in
`[VM-YOU]` but you launch the dashboard on `[WIN-HOST]`, the link panel will say
*Disconnected* — that is correct, not a bug. Watch the link where the link is.

It binds `127.0.0.1` only, so:

| Where you want the UI | Where to run it |
|---|---|
| Windows browser, tunnel in the VM | run it **in the VM**, then `ssh -L 8770:127.0.0.1:8770 user@vm` and open `http://127.0.0.1:8770` on Windows |
| Windows browser, tunnel on Windows | run it on `[WIN-HOST]` directly |
| Headless | skip the dashboard, use `lanbridge status` |

---

## 5. Verification matrix

Run each line on the machine named. Together they prove the segment.

| # | Where | Command | Proves |
|---|---|---|---|
| 1 | `[VM-AP]` root | `ip -br addr show tap0` | tunnel device exists |
| 2 | `[VM-AP]` root | `bridge link show` | device is in the bridge |
| 3 | `[VM-YOU]` root | `ip -4 addr show dev tap0` | **static IP applied** |
| 4 | `[VM-YOU]` root | `ping -c3 192.168.50.1` | AP router reachable |
| 5 | `[VM-YOU]` root | `arping -I tap0 192.168.50.1` | **broadcast crosses** (the real test) |
| 6 | `[VM-YOU]` root | `ip neigh show dev tap0` | ARP entry learned |
| 7 | `[VM-YOU]` root | `ping -M do -s 1372 192.168.50.1` | MTU is sufficient |
| 8 | `[VM-AP]` root | `ping -c3 192.168.50.77` | the far side can reach you |

Windows client equivalents: `Get-NetIPAddress`, `arp -a | findstr 192.168.50`,
`ping -l 1372 -f 192.168.50.1`.

If 4 passes and 5 fails, the bridge is not passing broadcast — look at rows 1–2
on `[VM-AP]`, not at the client.

---

## 6. Hypervisor settings (`[WIN-HOST]`, before any of the above)

| Hypervisor | Where | Setting |
|---|---|---|
| VMware Workstation | VM settings → Network Adapter | **Bridged (Automatic)**, and set the virtual network editor to *Allow all* promiscuous |
| VirtualBox | VM settings → Network → Adapter 1 | **Bridged Adapter**, Promiscuous Mode = **Allow All** |
| Hyper-V | Virtual Switch Manager | External switch binds **wired only** — use the Windows-host variant for Wi-Fi |

NAT mode hides the guest behind the host address: L2 is lost, and nothing in
this runbook will work.

---

## 7. Teardown (order matters)

```bash
# [VM-YOU] root — first
sudo pkill -f "openvpn --config client.ovpn"   # or AccountDisconnect / wg-quick down
```

```bash
# [VM-AP] root — second
sudo pkill -f "openvpn --config server.ovpn"
sudo ip link del br0          # drops tap0 and the vxlan member with it
```

Client first: tearing down the host bridge while a client is attached leaves
the client ARPing into a segment that no longer exists.

---

## 8. Symptom → which machine to look at

| Symptom | Look here first |
|---|---|
| Dashboard says Disconnected but you started a tunnel | you are watching the wrong machine (§4) |
| `ping` works, `arping` fails | `[VM-AP]`: is the tap a bridge member? |
| Client gets no address | `[VM-YOU]`: did the script find the device? |
| Works, then dies after reboot | `[VM-AP]`: `tap0`/`br0` are runtime-only; re-run bring-up |
| Large transfers hang, small ones fine | `[VM-YOU]`: MTU row 7, lower `tun-mtu` |
| `eth0` not found in the VM | `[VM-AP]`: interface name differs (`ens33`, `enp0s3`); fix `host.lan_iface` under Settings |
| Nothing at all on Wi-Fi | `[WIN-HOST]`: bridged-vs-NAT (§6), or switch to the Windows-host SoftEther variant |
