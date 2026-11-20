"""Loopback-only dashboard server (standard library only).

Routes
    GET  /                 the UI shell
    GET  /static/*         css / js
    GET  /api/state        machine snapshot, link, traffic, activity, doctor
    GET  /api/probe        ICMP reachability to gateway + bridge node
    GET  /api/file         read-only preview of a generated file
    POST /api/preflight    what still blocks a transport, and the command
    POST /api/generate     write transport files
    PUT  /api/settings     validate and save lanbridge.toml

Safety: binds 127.0.0.1 by default, rejects non-loopback Host headers and
cross-origin mutations, never executes tunnel commands on behalf of the client.
Generation only writes text files under ./out.
"""

from __future__ import annotations

import ipaddress
import json
import mimetypes
import threading
import time
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .. import __version__, doctor, gen, guide, preflight, sysinfo
from ..settings import CONFIG_NAME, Settings, dumps, find_config

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_BODY = 256 * 1024
SAMPLE_INTERVAL = 2.0          # seconds between traffic samples
SAMPLE_LIMIT = 360             # ~12 minutes of history
EVENT_LIMIT = 50


class State:
    """Process-wide state shared with request handlers."""

    def __init__(self, cfg: Settings, outdir: Path, config_path: Path, port: int) -> None:
        self.cfg = cfg
        self.outdir = outdir
        self.config_path = config_path
        self.port = port
        self.lock = threading.Lock()

        self.samples: deque[dict[str, float]] = deque(maxlen=SAMPLE_LIMIT)
        self.last_sample = 0.0
        self.events: deque[dict[str, Any]] = deque(maxlen=EVENT_LIMIT)
        self.last_link_state: str | None = None
        self.linked_since: float | None = None
        self.sampling_since: float | None = None
        self.device: str | None = None

    # -- helpers -------------------------------------------------------
    def log(self, text: str, level: str = "info") -> None:
        self.events.appendleft({"t": time.time(), "level": level, "text": text})

    def sample(self, device: str | None) -> None:
        """Append one counter sample, rate-limited so polling does not fill it."""
        if not device:
            self.device = None
            return
        now = time.time()
        self.device = device
        if now - self.last_sample < SAMPLE_INTERVAL:
            return
        reading = sysinfo.counters(device)
        if not reading:
            return
        self.last_sample = now
        if self.sampling_since is None:
            self.sampling_since = now
        self.samples.append({"t": now, "rx": float(reading["rx"]), "tx": float(reading["tx"])})

    def traffic(self) -> dict[str, Any]:
        if not self.samples:
            return {
                "available": False,
                "device": self.device,
                "reason": "no tunnel counters yet",
                "samples": [],
            }
        samples = list(self.samples)
        rx = samples[-1]["rx"]
        tx = samples[-1]["tx"]
        rx_rate = tx_rate = 0.0
        if len(samples) >= 2:
            prev, last = samples[-2], samples[-1]
            dt = max(1e-6, last["t"] - prev["t"])
            rx_rate = max(0.0, (last["rx"] - prev["rx"]) / dt)
            tx_rate = max(0.0, (last["tx"] - prev["tx"]) / dt)
        return {
            "available": True,
            "device": self.device,
            "rx_bytes": int(rx),
            "tx_bytes": int(tx),
            "rx_rate": round(rx_rate, 1),
            "tx_rate": round(tx_rate, 1),
            "sampling_since": self.sampling_since,
            "samples": samples,
        }


def _link(cfg: Settings, snap: dict[str, Any]) -> dict[str, Any]:
    """Derive one honest link state from facts, not from optimism."""
    virtual = set(snap["virtual_devices"])
    on_segment = None
    for iface in snap["interfaces"]:
        if iface["name"] not in virtual:
            continue
        for addr in iface["addresses"]:
            try:
                ip = ipaddress.IPv4Interface(addr)
            except ValueError:
                continue
            if ip.ip in cfg.network:
                on_segment = {"device": iface["name"], "address": addr}
                break

    if on_segment:
        state_name = "linked"
    elif virtual:
        state_name = "adapter"
    else:
        state_name = "offline"

    return {
        "state": state_name,
        "device": on_segment["device"] if on_segment else (sorted(virtual)[0] if virtual else None),
        "address": on_segment["address"] if on_segment else None,
        "processes": snap["processes"],
        "mtu": cfg.vxlan_mtu if cfg.tunnel.transport == "vxlan" else cfg.tunnel.mtu,
        "transport": cfg.tunnel.transport,
        "static_ip": cfg.client.static_ip,
    }


def make_handler(state: State):
    class Handler(BaseHTTPRequestHandler):
        server_version = "LanBridge"
        protocol_version = "HTTP/1.1"

        # -- plumbing ---------------------------------------------------
        def log_message(self, fmt: str, *args) -> None:
            return

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: Any, status: int = 200) -> None:
            self._send(status, json.dumps(payload).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost", "[::1]", "::1")

        def _same_origin(self) -> bool:
            origin = self.headers.get("Origin")
            if origin is None:
                return True
            return origin in (
                f"http://127.0.0.1:{state.port}",
                f"http://localhost:{state.port}",
            )

        # -- GET --------------------------------------------------------
        def do_GET(self) -> None:  # noqa: N802
            if not self._host_ok():
                return self._json({"error": "invalid host"}, 400)
            path = urlparse(self.path).path

            if path in ("/", "/index.html"):
                return self._serve_file(STATIC_DIR / "index.html")
            if path.startswith("/static/"):
                target = (STATIC_DIR / path[len("/static/"):]).resolve()
                if STATIC_DIR.resolve() not in target.parents:
                    return self._json({"error": "forbidden"}, 403)
                return self._serve_file(target)
            if path == "/api/state":
                return self._json(self._state())
            if path == "/api/probe":
                return self._json(self._probe())
            if path == "/api/file":
                return self._file()
            if path == "/api/health":
                return self._json({"ok": True, "version": __version__})
            self._json({"error": "not found"}, 404)

        def _serve_file(self, target: Path) -> None:
            if not target.is_file():
                return self._json({"error": "not found"}, 404)
            ctype, _ = mimetypes.guess_type(str(target))
            self._send(HTTPStatus.OK, target.read_bytes(), ctype or "application/octet-stream")

        def _probe(self) -> dict[str, Any]:
            cfg = state.cfg
            return {
                "gateway": sysinfo.ping(cfg.site.gateway),
                "bridge": sysinfo.ping(cfg.site.bridge_address),
                "at": time.time(),
            }

        def _file(self) -> None:
            raw = parse_qs(urlparse(self.path).query).get("path", [""])[0]
            root = state.outdir.resolve()
            target = (root / raw).resolve()
            if not raw or root not in target.parents:
                return self._json({"error": "path outside output directory"}, 403)
            if not target.is_file():
                return self._json({"error": "not found"}, 404)
            if target.stat().st_size > 128 * 1024:
                return self._json({"error": "file too large to preview"}, 413)
            return self._json({
                "path": raw,
                "content": target.read_text(encoding="utf-8", errors="replace"),
            })

        # -- POST/PUT ---------------------------------------------------
        def do_PUT(self) -> None:  # noqa: N802
            self._mutate("settings")

        def do_POST(self) -> None:  # noqa: N802
            self._mutate(urlparse(self.path).path.rsplit("/", 1)[-1])

        def _read_json(self) -> dict[str, Any] | None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return None
            if length <= 0 or length > MAX_BODY:
                return None
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return None
            return payload if isinstance(payload, dict) else None

        def _mutate(self, kind: str) -> None:
            if not self._host_ok() or not self._same_origin():
                return self._json({"error": "cross-origin request rejected"}, 403)
            payload = self._read_json()
            if payload is None:
                return self._json({"error": "invalid JSON body"}, 400)

            if kind == "settings":
                return self._save_settings(payload)
            if kind == "generate":
                return self._generate(payload)
            if kind == "preflight":
                transport = str(payload.get("transport") or state.cfg.tunnel.transport)
                if transport not in gen.MODULES:
                    return self._json({"error": f"unknown transport {transport}"}, 400)
                state.log(f"Preflight for {transport}", "info")
                return self._json(preflight.run(state.cfg, transport, state.outdir))
            return self._json({"error": "not found"}, 404)

        def _save_settings(self, payload: dict[str, Any]) -> None:
            merged = state.cfg.to_dict()
            for section, values in payload.items():
                if section in merged and isinstance(values, dict):
                    merged[section].update(values)
            new_cfg = Settings.from_dict(merged)
            checks = doctor.run_checks(new_cfg)
            if any(c.id == "static-ip" and c.level == "fail" for c in checks):
                return self._json({"error": "address plan is invalid",
                                   "checks": [c.__dict__ for c in checks]}, 422)
            with state.lock:
                state.cfg = new_cfg
                new_cfg.save(state.config_path)
            state.log(f"Address plan saved ({new_cfg.site.subnet})", "info")
            return self._json(self._state())

        def _generate(self, payload: dict[str, Any]) -> None:
            transport = str(payload.get("transport") or state.cfg.tunnel.transport)
            role = str(payload.get("role") or "both")
            if transport not in gen.MODULES:
                return self._json({"error": f"unknown transport {transport}"}, 400)
            if role not in ("host", "client", "both"):
                return self._json({"error": f"unknown role {role}"}, 400)
            roles = ["host", "client"] if role == "both" else [role]
            written: list[dict[str, str]] = []
            for r in roles:
                for path in gen.write(state.cfg, transport, r, state.outdir):
                    written.append({
                        "path": path.relative_to(state.outdir).as_posix(),
                        "size": str(path.stat().st_size),
                    })
            state.log(f"Generated {len(written)} {transport}/{role} files", "ok")
            return self._json({"written": written, "state": self._state()})

        # -- state assembly ---------------------------------------------
        def _state(self) -> dict[str, Any]:
            cfg = state.cfg
            snap = sysinfo.snapshot()
            link = _link(cfg, snap)

            # Traffic sampling needs a device that actually carries the segment.
            state.sample(link["device"])

            if link["state"] != state.last_link_state:
                state.last_link_state = link["state"]
                if link["state"] == "linked":
                    state.linked_since = time.time()
                    state.log("Link up on the AP segment", "ok")
                elif link["state"] == "adapter":
                    state.linked_since = None
                    state.log("Virtual adapter up without an AP address", "warn")
                else:
                    state.linked_since = None
                    state.log("Link offline", "warn")

            doctor_result = doctor.summary(doctor.run_checks(cfg, snap))
            outputs: list[dict[str, str]] = []
            if state.outdir.exists():
                for path in sorted(state.outdir.rglob("*")):
                    if path.is_file():
                        outputs.append({
                            "path": path.relative_to(state.outdir).as_posix(),
                            "size": str(path.stat().st_size),
                        })

            return {
                "version": __version__,
                "config_path": str(state.config_path),
                "link": link,
                "traffic": state.traffic(),
                "activity": list(state.events),
                "session": {
                    "linked_since": state.linked_since,
                    "sampling_since": state.sampling_since,
                    "now": time.time(),
                },
                "settings": cfg.to_dict(),
                "derived": {
                    "network": cfg.network_address,
                    "prefixlen": cfg.prefixlen,
                    "netmask": cfg.netmask,
                    "broadcast": cfg.broadcast,
                    "wg_mtu": cfg.wg_mtu,
                    "vxlan_mtu": cfg.vxlan_mtu,
                },
                "machine": snap,
                "doctor": doctor_result,
                "transports": gen.describe(),
                "guide": guide.payload(),
                "outputs": outputs,
            }

    return Handler


def serve(cfg: Settings, host: str = "127.0.0.1", port: int = 8770,
          outdir: Path | None = None, config_path: Path | None = None) -> None:
    outdir = outdir or Path("out")
    outdir.mkdir(parents=True, exist_ok=True)
    config_path = config_path or find_config(Path.cwd())

    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit(
            "refusing to bind a non-loopback address: the dashboard exposes local "
            "interface data. Use an SSH tunnel instead."
        )

    state = State(cfg, outdir, config_path, port)
    state.log(f"Dashboard started on port {port}", "info")

    httpd = ThreadingHTTPServer((host, port), make_handler(state))
    httpd.daemon_threads = True

    print(f"""
LanBridge {__version__}  -  dashboard
  http://{host}:{port}
  config  {config_path}
  output  {outdir.resolve()}
  Ctrl+C to stop
""", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        httpd.server_close()


__all__ = ["serve", "CONFIG_NAME", "dumps"]
