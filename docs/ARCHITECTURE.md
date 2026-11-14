# LanBridge architecture

Practical notes for the report. Everything here maps to a file the generator
writes, so each claim can be pointed at a config.

---

## 1. Why layer 2, not layer 3

Your app is bound to a static address on the AP's subnet, for example
`192.168.50.77/24`, and it may also depend on discovery that never crosses a
router:

| Traffic | Crosses an L3 tunnel? | Crosses an L2 segment? |
|---|---|---|
| TCP/UDP to a known address | yes, with a route | yes |
| ARP ("who has 192.168.50.1") | **no** | yes (broadcast) |
| DHCP | no | yes (broadcast) |
| mDNS / SSDP / NetBIOS / WSD | no | yes (multicast/broadcast) |
| Wake-on-LAN, L2 discovery | no | yes |

A `tun` device is a routed point-to-point link: it carries IP packets, so it
has no broadcast domain. A `tap` device carries Ethernet frames, which is what
lets one machine's `/24` be the same `/24` as the AP's.

So the design constraint is: **the far machine's frame must arrive on the AP's
wire with the far machine's MAC address intact.** Anything that rewrites
addresses (NAT) or terminates at L3 (tun, plain WireGuard) breaks that.

---

## 2. Interface types used here

| Device | Layer | What it is | Broadcast |
|---|---|---|---|
| `tun0` | L3 | point-to-point IP tunnel | no |
| `tap0` | L2 | virtual Ethernet NIC, frames to userspace | yes |
| `br0` | L2 | software switch; members share one segment | yes |
| `vxlan42` | L2 over UDP | Ethernet-in-UDP, needs a VTEP peer | yes (flooded to peers) |
| `wg0` | L3 | encrypted UDP tunnel | no |

The three transports compose these differently:

### SoftEther (recommended on Wi-Fi / Windows)

```
app -> virtual NIC (SoftEther client) -> TLS/UDP -> vpnserver
                                                     |
                                          virtual hub = L2 switch
                                                     |
                                       local bridge -> "Wi-Fi" adapter
```

The hub is a software switch. `BridgeCreate ... /DEVICE:"Wi-Fi"` attaches the
physical adapter to it, so the client's frames reach the AP's segment. SoftEther
handles the 802.11 three-address problem internally, which is why this works
where a Linux bridge over Wi-Fi does not.

### OpenVPN TAP (cleanest for a write-up, needs Ethernet on the host)

```
app -> tap0 (client) -> TLS/UDP -> openvpn server -> tap0
                                                       |
                                                     br0  = LAN NIC + tap0
```

`br0` is a real Linux bridge. The LAN NIC loses its address; the address moves
to `br0`, which now owns the segment. No routing entry is involved at all: the
whole `/24` is on-link on `br0`, reached by ARP through the bridge.

### WireGuard + VXLAN (lightest, Linux only)

```
eth frame
  -> vxlan42 (adds VXLAN + UDP + outer IP)
     -> wg0 (encrypts, adds 60 bytes)
        -> internet -> wg0 -> vxlan42 -> br0 -> LAN NIC
```

Two encapsulations. WireGuard gives confidentiality and a stable underlay
address (`10.99.0.0/30`); VXLAN gives back the Ethernet semantics WireGuard
lacks. This is the lowest-overhead option that still provides broadcast, and it
needs no kernel module beyond `vxlan` (in-tree since 3.7).

---

## 3. Routing tables, concretely

### Bridged (SoftEther, OpenVPN TAP)

There is **nothing to route**. After bridging, `ip route` shows the LAN prefix
as a device route on the bridge:

```
$ ip route
default via 192.168.50.1 dev br0 proto dhcp src 192.168.50.240 metric 100
192.168.50.0/24 dev br0 proto kernel scope link src 192.168.50.240
```

`scope link` means "directly reachable, ARP for it". The client sees the same
thing on its TAP adapter. That is why `192.168.50.77` keeps working unchanged:
the kernel never asks a router where it is.

The generated OpenVPN server pushes `route-gateway 192.168.50.1` and
`route 192.168.50.0 255.255.255.0`; those exist for clients whose TAP adapter
was not given the LAN prefix, and they are harmless when it was.

`redirect-gateway def1` is deliberately **not** pushed. It would install
`0.0.0.0/1` and `128.0.0.0/1` via the tunnel and send all internet traffic
through the AP node - slower, and unnecessary for LAN access. Enable it only if
you actually want the AP's internet egress.

### VXLAN over WireGuard

Here there *is* routing, on the underlay only:

```
$ ip route
10.99.0.0/30 dev wg0 proto kernel scope link src 10.99.0.1
192.168.50.0/24 dev br0 proto kernel scope link src 192.168.50.240
```

The `10.99.0.0/30` route is how the VXLAN outer packets find the peer. The LAN
prefix still resolves by ARP on `br0`, because `vxlan42` is a bridge member.

WireGuard's `AllowedIPs = 10.99.0.2/32` is both a cryptokey routing entry and
the peer's permitted source address: a peer spoofing `192.168.50.0/24` on the
underlay is dropped. Keep it at `/32`.

### Windows

Same model with different commands:

```powershell
Get-NetRoute -AddressFamily IPv4 | Sort-Object RouteMetric | Select-Object -First 10
route print -4
```

Bridged tunnels show the LAN prefix bound to the virtual adapter with no
gateway - that is the "on-link" case, and it is correct.

---

## 4. Virtual adapters, per platform

**Linux** - created at runtime, gone after reboot:

```bash
ip tuntap add dev tap0 mode tap      # or: openvpn --mktun --dev tap0
ip link set tap0 up
```

**Windows** - the adapter is a driver-level device:

* OpenVPN installs TAP-Windows6 (`tap0901`). A second adapter can be added with
  `Add-TapWindowsAdapter` from `tapctl.exe`.
* SoftEther installs its own virtual NIC driver, one per configured `NICNAME`.
* The adapter must be named identically to `client.vpn_adapter_name` when
  `dev-node` is used, otherwise OpenVPN picks the first TAP device it finds.

**VM specifics** - this matters because you are testing in VMs:

| Hypervisor | Setting to use | Note |
|---|---|---|
| VirtualBox | Adapter 1 = **Bridged Adapter**, Promiscuous = **Allow All** | Bridged over host Wi-Fi uses MAC-NAT; broadcast works, but inbound ARP to the guest can be unreliable. |
| VMware Workstation | **Bridged (Automatic)** | Set `Promiscuous mode` to *Allow all* in the virtual network editor. |
| Hyper-V | **External Virtual Switch** | Not allowed to bind a Wi-Fi adapter. Use a wired NIC, or run SoftEther on the host. |
| KVM/libvirt | `<interface type='bridge'>` or `macvtap` in bridge mode | `macvtap` in *bridge* mode cannot talk to its own host; use a real bridge. |

If the bridge node runs inside a VM, the VM's NIC must be in a bridged/macvtap
mode - NAT mode hides the guest behind the host's address and L2 is lost.
Prefer two NICs: one NAT for management, one bridged for the LAN segment.

Also disable the host firewall's "public network" default for the bridged
adapter, and turn off **MAC address spoofing** filters in Hyper-V
(`Set-VMNetworkAdapter -MacAddressSpoofing On`) or the guest's frames are
dropped when the MAC does not match.

---

## 5. Firewall rules

The only rule that truly matters is one inbound UDP/TCP port on the host. The
generated scripts do exactly that and nothing else.

**Inbound (host, internet-facing):**

| Transport | Rule |
|---|---|
| OpenVPN | `udp/1194` |
| SoftEther | `tcp/1194` (or 443 to look like HTTPS and survive restrictive networks) |
| WireGuard | `udp/51820` |

**Never expose:** VXLAN `udp/4789`. It is carried inside `wg0`; opening it on
the uplink turns your L2 segment into a public one.

**Bridging and netfilter.** A Linux bridge is transparent, but if
`br_netfilter` is loaded, `iptables` sees bridged frames and can drop them:

```bash
sysctl -w net.bridge.bridge-nf-call-iptables=0
sysctl -w net.bridge.bridge-nf-call-ip6tables=0
```

The generated `bridge-up.sh` sets both. If ARP resolves but TCP hangs, check
these first.

**Forwarding** (`net.ipv4.ip_forward=1`) is needed only for the VXLAN path.
Bridging does not route, so a pure bridged setup works with forwarding off.

**Windows:**

```powershell
New-NetFirewallRule -DisplayName "LanBridge" -Direction Inbound -Action Allow `
  -Protocol UDP -LocalPort 1194 -Profile Any
```

Also confirm the virtual adapter's network profile is **Private**; on Public,
inbound SMB/ICMP is blocked by default even when the tunnel works:

```powershell
Get-NetConnectionProfile
Set-NetConnectionProfile -InterfaceAlias "LanBridge" -NetworkCategory Private
```

---

## 6. MTU

Encapsulation costs bytes, and an L2 segment cannot fragment for you.

| Path | Overhead | Safe inner MTU |
|---|---|---|
| OpenVPN over UDP/IPv4 | ~69 | 1400 (1441 is the hard figure) |
| SoftEther over TLS | ~70 | 1400 |
| WireGuard | 60 | 1420 |
| VXLAN on top of WireGuard | 60 + 50 | **1370** |

Symptom of a wrong MTU: `ping` works, `ssh` connects, then large transfers
stall or hang. Diagnosis:

```bash
ping -M do -s 1372 192.168.50.1        # 1400 - 28
ping -M do -s 1342 192.168.50.1        # 1370 - 28
```

Lower `tun-mtu` (OpenVPN) or the bridge MTU until the large probe passes.
`mssfix` (OpenVPN) clamps TCP MSS so you do not have to lower MTU everywhere;
it does nothing for UDP.

---

## 7. Verifying the segment

Run these in order; each failure has exactly one meaning.

```bash
# 1. tunnel established?
wg show                       | tail -n 5     # vxlan path
ip -br addr show tap0                        # openvpn path
vpncmd localhost /CLIENT /CMD AccountStatusGet lab

# 2. frames crossing the bridge?
bridge link show
bridge fdb show | head

# 3. is the far side ARP-visible?
arping -I tap0 192.168.50.1                  # both directions
ip neigh show dev tap0

# 4. is it the AP or the tunnel?
ping -c3 192.168.50.1
tcpdump -ni tap0 -c 20 arp or icmp
```

`arping` failing while `ping` succeeds means the bridge is not passing
broadcast - a bridge membership problem, not a tunnel problem.

---

## 8. Security notes

An L2 bridge is a shared wire. Anything on the tunnel is on the AP's LAN:

* Never bridge a hub with no user password (`HubCreate` without `/PASSWORD`).
* SoftEther: delete the default `VPN` hub or leave it password-protected; the
  hub user list is the only access control.
* OpenVPN: `remote-cert-tls server` on the client prevents server impersonation;
  ignore `verify-x509-name` and you lose that.
* VXLAN has no authentication of its own - that is WireGuard's job. Do not run
  VXLAN over the open internet.
* Treat the client as an untrusted host: it can ARP-spoof the AP's segment.
  For a school project this is acceptable; for anything real, add
  `ebtables`/`nft` bridge filtering per MAC.
* Revoking a client means revoking its certificate / deleting its hub user.
  Rotating the tunnel key alone does not remove frames already on the bridge.

---

## 9. Reachability

| Situation | Option |
|---|---|
| Host has a public IPv4 | forward one UDP port to the host |
| Host is behind CGNAT | SoftEther NAT traversal, or a $0 VPS relay |
| IPv6 available | WireGuard endpoint `[2001:db8::1]:51820` needs no NAT at all |
| No public anything | Tailscale/ZeroTier style relay, or a VPS with a cascade |

For the school demo, a small VPS running SoftEther (cascade) or `wg` as a relay
is the most reliable: both laptops dial out, neither needs a port forward.
