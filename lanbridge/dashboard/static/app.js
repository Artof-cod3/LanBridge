/* LanBridge dashboard - vanilla JS, no build step, no dependencies. */

const $ = (id) => document.getElementById(id);

let state = null;
let probe = null;
let selectedTransport = null;
let selectedRole = "both";
let activeFile = null;
let trafficWindow = 0;   // 0 = whole session, otherwise seconds

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const LINK_LABEL = { linked: "Connected", adapter: "Adapter only", offline: "Disconnected" };
const LINK_SUB = {
  linked: "On the AP segment with your static address",
  adapter: "Virtual device exists, no address on the segment",
  offline: "No virtual adapter present",
};

/* -------------------------------------------------------------------- util */

function fmtBytes(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = n / 1024;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toFixed(value >= 10 ? 1 : 2)} ${units[i]}`;
}

function fmtRate(bytesPerSecond) {
  if (!bytesPerSecond) return "0 B/s";
  return `${fmtBytes(bytesPerSecond)}/s`;
}

function fmtClock(epochSeconds) {
  if (!epochSeconds) return "—";
  return new Date(epochSeconds * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function fmtDuration(since) {
  if (!since) return "00:00:00";
  const total = Math.max(0, Math.floor(Date.now() / 1000 - since));
  const h = String(Math.floor(total / 3600)).padStart(2, "0");
  const m = String(Math.floor((total % 3600) / 60)).padStart(2, "0");
  const s = String(total % 60).padStart(2, "0");
  return `${h}:${m}:${s}`;
}

/* -------------------------------------------------------------------- data */

async function refresh() {
  try {
    const res = await fetch("/api/state", { headers: { Accept: "application/json" } });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state = await res.json();
    render();
    $("rail-updated").textContent = "refreshed " + new Date().toLocaleTimeString();
    refreshProbe();
  } catch (err) {
    $("link-text").textContent = "dashboard unreachable";
    $("link-dot").className = "dot fail";
  }
}

async function refreshProbe() {
  try {
    const res = await fetch("/api/probe", { headers: { Accept: "application/json" } });
    if (!res.ok) return;
    probe = await res.json();
    renderProbe();
  } catch {
    /* best effort */
  }
}

async function postJSON(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

/* ------------------------------------------------------------------ render */

function render() {
  if (!state) return;
  renderChrome();
  renderHero();
  renderTraffic();
  renderDetails();
  renderHost();
  renderMiniChecks();
  renderFeed();
  renderGuide();
  renderTransports();
  renderDiagnostics();
  renderFiles();
  renderSettings();
}

function renderChrome() {
  const s = state.settings;
  const link = state.link;

  $("greet").textContent = "Welcome back";
  $("greet-sub").textContent = `${s.site.name} · ${link.state === "linked" ? "segment live" : "ready when you are"}`;

  $("link-dot").className = "dot " + link.state;
  $("link-text").textContent = LINK_LABEL[link.state] || link.state;

  $("side-dot").className = "dot " + link.state;
  $("side-state").textContent = LINK_LABEL[link.state] || link.state;
  $("side-network").textContent = `${state.derived.network}/${state.derived.prefixlen}`;
  $("side-sub").textContent = `${s.tunnel.transport} · gw ${s.site.gateway}`;

  $("rail-config").textContent = state.config_path.split(/[\\/]/).pop();

  const live = $("link-live");
  if (live && live.dataset.state !== link.state) {
    live.dataset.state = link.state;
    live.textContent = `Link ${LINK_LABEL[link.state] || link.state}`;
  }
}

function renderHero() {
  const link = state.link;
  $("ring").className = "status-ring " + link.state;
  $("link-state").textContent = LINK_LABEL[link.state] || link.state;
  $("link-sub").textContent = link.address
    ? `${LINK_SUB[link.state]} · ${link.address}`
    : LINK_SUB[link.state];
  $("hero-note").textContent =
    link.state === "linked" ? `${link.device} · MTU ${link.mtu}` : "";
  $("connect-label").textContent = link.state === "linked" ? "Check connection" : "Connect";
}

function renderProbe() {
  if (!probe) return;
  const gw = probe.gateway;
  $("host-rtt").textContent = !gw
    ? "—"
    : gw.ok
      ? (gw.rtt_ms !== null ? `${gw.rtt_ms} ms` : "reply")
      : "no reply";

  const rtt = gw && gw.ok ? gw.rtt_ms : null;
  const level = !gw || !gw.ok ? 0 : rtt === null ? 2 : rtt < 40 ? 4 : rtt < 90 ? 3 : 2;
  $("host-bars").dataset.level = String(level);

  if (!state) return;
  $("act-probe-sub").textContent = gw && gw.ok
    ? `gateway replies in ${gw.rtt_ms ?? "?"} ms`
    : "gateway does not answer yet";
}

function renderTraffic() {
  const t = state.traffic;
  if (!t || !t.available) {
    $("rx-total").textContent = "—";
    $("tx-total").textContent = "—";
    $("traffic-hint").textContent = "waiting for a tunnel device";
    $("axis-from").textContent = "";
    $("axis-to").textContent = "";
  } else {
    $("rx-total").textContent = fmtBytes(t.rx_bytes);
    $("tx-total").textContent = fmtBytes(t.tx_bytes);
    $("traffic-hint").textContent = `${t.device} · ${fmtRate(t.rx_rate)}`;
    const first = t.samples[0];
    const last = t.samples[t.samples.length - 1];
    $("axis-from").textContent = fmtClock(first && first.t);
    $("axis-to").textContent = fmtClock(last && last.t);
  }
  drawChart(t);
}

function chartRates(t, windowSeconds) {
  if (!t || !t.available || t.samples.length < 3) return [];
  let samples = t.samples;
  if (windowSeconds > 0) {
    const cutoff = samples[samples.length - 1].t - windowSeconds;
    samples = samples.filter((s) => s.t >= cutoff);
  }
  const rates = [];
  for (let i = 1; i < samples.length; i += 1) {
    const dt = Math.max(1, samples[i].t - samples[i - 1].t);
    rates.push({
      rx: Math.max(0, samples[i].rx - samples[i - 1].rx) / dt,
      tx: Math.max(0, samples[i].tx - samples[i - 1].tx) / dt,
    });
  }
  return rates;
}

function drawChart(t) {
  const svg = $("chart");
  const W = 600, H = 152, padX = 6, padTop = 12, padBottom = 10;
  const empty = `<text class="empty-text" x="${W / 2}" y="${H / 2}" text-anchor="middle">collecting samples…</text>`;

  const rates = chartRates(t, trafficWindow);
  if (rates.length < 2) {
    svg.innerHTML = empty;
    return;
  }

  const peak = Math.max(1e-6, ...rates.map((r) => Math.max(r.rx, r.tx)));
  const n = rates.length;
  const x = (i) => padX + (i * (W - padX * 2)) / (n - 1);
  const y = (v) => H - padBottom - (v / peak) * (H - padBottom - padTop);

  /* Catmull-Rom style tangents keep the curve smooth without overshooting. */
  const curve = (key) => {
    const pts = rates.map((r, i) => ({ x: x(i), y: y(r[key]) }));
    if (pts.length < 2) return "";
    let d = `M${pts[0].x.toFixed(1)},${pts[0].y.toFixed(1)}`;
    for (let i = 0; i < pts.length - 1; i += 1) {
      const p0 = pts[i - 1] || pts[i];
      const p1 = pts[i];
      const p2 = pts[i + 1];
      const p3 = pts[i + 2] || p2;
      const k = 0.16;
      const c1x = p1.x + (p2.x - p0.x) * k;
      const c1y = p1.y + (p2.y - p0.y) * k;
      const c2x = p2.x - (p3.x - p1.x) * k;
      const c2y = p2.y - (p3.y - p1.y) * k;
      d += ` C${c1x.toFixed(1)},${c1y.toFixed(1)} ${c2x.toFixed(1)},${c2y.toFixed(1)} ${p2.x.toFixed(1)},${p2.y.toFixed(1)}`;
    }
    return d;
  };

  const area = (d) =>
    `${d} L${x(n - 1).toFixed(1)},${H - padBottom} L${padX},${H - padBottom} Z`;

  const grid = [0.3, 0.6, 0.9]
    .map((f) => `<line class="grid-line" x1="${padX}" x2="${W - padX}" y1="${(padTop + (H - padTop - padBottom) * f).toFixed(1)}" y2="${(padTop + (H - padTop - padBottom) * f).toFixed(1)}"/>`)
    .join("");

  const rxPath = curve("rx");
  const txPath = curve("tx");

  svg.innerHTML = `<defs>
      <linearGradient id="rxFill" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="var(--accent)" stop-opacity="0.32"/>
        <stop offset="100%" stop-color="var(--accent)" stop-opacity="0"/>
      </linearGradient>
      <linearGradient id="txFill" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="var(--series-2)" stop-opacity="0.20"/>
        <stop offset="100%" stop-color="var(--series-2)" stop-opacity="0"/>
      </linearGradient>
    </defs>
    ${grid}
    <path class="series-area-b" d="${area(txPath)}"/>
    <path class="series-area-a" d="${area(rxPath)}"/>
    <path class="series-b" d="${txPath}"/>
    <path class="series-a" d="${rxPath}"/>`;
}

/* ------------------------------------------------------------- hero map */
/* Dotted world map drawn from soft landmass ellipses in lon/lat. Reads as a
 * map at a glance while staying a few hundred DOM nodes, not an image. */
const LANDMASSES = [
  [-152, 64, 14, 7], [-105, 59, 31, 12], [-99, 38, 24, 11], [-103, 22, 10, 7],
  [-72, 17, 9, 3], [-42, 73, 12, 8], [-61, -4, 17, 12], [-66, -31, 9, 14],
  [4, 50, 14, 9], [18, 62, 10, 6], [16, 22, 26, 12], [22, -12, 14, 14],
  [45, 28, 12, 9], [78, 22, 10, 9], [62, 60, 30, 9], [120, 62, 32, 9],
  [105, 33, 18, 10], [105, 13, 10, 8], [115, -2, 16, 4], [138, 37, 4, 6],
  [134, -25, 17, 10], [172, -42, 4, 4], [47, -19, 3, 6], [-2, 54, 4, 4],
];

function onLand(lon, lat) {
  for (const [cx, cy, rx, ry] of LANDMASSES) {
    const wobble = 1 + 0.07 * Math.sin(lon * 0.8) * Math.cos(lat * 0.6);
    const dx = (lon - cx) / (rx * wobble);
    const dy = (lat - cy) / (ry * wobble);
    if (dx * dx + dy * dy <= 1) return true;
  }
  return false;
}

function buildHeroMap() {
  const svg = $("hero-map");
  if (!svg || svg.dataset.built === "1") return;
  const W = 720, H = 300;
  const dots = [];
  for (let lat = 76; lat >= -56; lat -= 3.1) {
    const step = 3.1 / Math.max(0.45, Math.cos((lat * Math.PI) / 180));
    for (let lon = -178; lon <= 178; lon += step) {
      if (!onLand(lon, lat)) continue;
      const px = ((lon + 180) / 360) * W;
      const py = ((76 - lat) / 132) * H;
      dots.push(`<circle cx="${px.toFixed(1)}" cy="${py.toFixed(1)}" r="1.45"/>`);
    }
  }
  svg.innerHTML = dots.join("");
  svg.dataset.built = "1";
}

function renderDetails() {
  const link = state.link;
  $("d-duration").textContent = fmtDuration(state.session.linked_since);
  $("d-address").textContent = link.address || "—";
  $("d-device").textContent = link.device || "none";
  $("d-mtu").textContent = String(link.mtu);
  $("session-hint").textContent = state.session.linked_since ? "live" : "not linked";
}

function renderHost() {
  const s = state.settings;
  $("host-name").textContent = s.site.name;
  $("host-sub").textContent = `${s.tunnel.transport} · ${state.machine.interfaces.length} adapters`;
  $("h-bridge").textContent = `${s.site.bridge_address}/${state.derived.prefixlen}`;
  $("h-remote").textContent = `${s.tunnel.remote_host}:${s.tunnel.remote_port}`;

  const select = $("h-transport");
  if (select.dataset.built !== "1") {
    select.innerHTML = state.transports
      .map((t) => `<option value="${esc(t.id)}">${esc(t.title)}</option>`)
      .join("");
    select.dataset.built = "1";
    select.addEventListener("change", async () => {
      try {
        await fetch("/api/settings", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ tunnel: { transport: select.value } }),
        });
        await refresh();
      } catch {
        /* the plan editor will surface a real error */
      }
    });
  }
  select.value = s.tunnel.transport;
}

function renderMiniChecks() {
  const d = state.doctor;
  $("checks-hint").textContent = `${d.counts.fail} blocking · ${d.counts.warn} warnings`;

  const priority = ["static-ip", "gateway", "bridge-address", "lan-iface", "virtual-device", "remote", "privileges"];
  const byId = new Map(d.checks.map((c) => [c.id, c]));
  const picked = priority.map((id) => byId.get(id)).filter(Boolean).slice(0, 4);

  $("mini-checks").innerHTML = picked
    .map(
      (c) => `<li>
        <span class="c-dot ${esc(c.level)}"></span>
        <span class="mc-text">${esc(c.title)}</span>
        <span class="pill ${esc(c.level)}">${esc(c.level)}</span>
      </li>`
    )
    .join("");
}

function renderFeed() {
  const events = state.activity || [];
  const row = (e) => `<li>
      <span class="c-dot ${esc(e.level === "ok" ? "ok" : e.level === "warn" ? "warn" : "info")}"></span>
      <span class="feed-text">${esc(e.text)}</span>
      <span class="feed-time">${esc(fmtClock(e.t))}</span>
    </li>`;

  $("feed").innerHTML = events.length
    ? events.slice(0, 4).map(row).join("")
    : `<li class="feed-empty">No events yet.</li>`;

  $("feed-full").innerHTML = events.length
    ? events.map(row).join("")
    : `<li class="feed-empty">No events yet. Generate a profile or save the plan.</li>`;
}

const BADGE_CLASS = {
  "win-host": "host",
  "win-host-admin": "host",
  "vm-ap": "ap",
  "vm-you": "you",
  "either": "any",
};

function renderGuide() {
  const g = state.guide;
  if (!g) return;

  const label = (id) => (g.machines.find((m) => m.id === id) || {}).label || id;
  const badge = (id) => `<span class="badge ${BADGE_CLASS[id] || "any"}">${esc(label(id))}</span>`;

  $("guide-legend").innerHTML = g.machines
    .filter((m) => m.id !== "win-host-admin")
    .map(
      (m) => `<div class="legend-item">${badge(m.id)}<span class="legend-detail">${esc(m.detail)}</span></div>`
    )
    .join("");

  $("guide-verified").textContent = g.verified_note;
  $("guide-count").textContent = `${g.steps.length} steps`;

  $("guide-steps").innerHTML = g.steps
    .map(
      (s, i) => `<div class="step">
        <div class="step-head">
          <span class="step-num">${i + 1}</span>
          <span class="step-title">${esc(s.title)}</span>
          ${badge(s.machine)}
        </div>
        ${s.body ? `<div class="step-body">${esc(s.body)}</div>` : ""}
        ${(s.commands || [])
          .map(
            (cmd) => `<div class="cmd">
              <pre>${esc(cmd)}</pre>
              <button class="btn small" type="button" data-copy>Copy</button>
            </div>`
          )
          .join("")}
      </div>`
    )
    .join("");

  $("guide-notes").innerHTML = g.notes
    .map(
      (n) => `<li><div class="gn-title">${esc(n.title)}</div><div class="gn-body">${esc(n.body)}</div></li>`
    )
    .join("");

  $("trouble-table").querySelector("tbody").innerHTML = g.troubleshoot
    .map(
      (t) => `<tr>
        <td>${esc(t.symptom)}</td>
        <td>${badge(t.where)}</td>
        <td>${esc(t.fix)}</td>
      </tr>`
    )
    .join("");
}

function renderTransports() {
  const box = $("transports");
  const current = selectedTransport || state.settings.tunnel.transport;
  selectedTransport = current;

  box.innerHTML = state.transports
    .map(
      (t) => `<label class="transport ${t.id === current ? "is-selected" : ""}" data-id="${esc(t.id)}">
        <input type="radio" name="transport" value="${esc(t.id)}" ${t.id === current ? "checked" : ""}>
        <span class="t-body">
          <span class="t-name">${esc(t.title)}</span>
          <span class="t-desc">${esc(t.description)}</span>
          <span class="t-meta">${esc(t.platforms)}</span>
        </span>
      </label>`
    )
    .join("");

  box.querySelectorAll(".transport").forEach((el) => {
    el.addEventListener("click", () => {
      selectedTransport = el.dataset.id;
      box.querySelectorAll(".transport").forEach((n) =>
        n.classList.toggle("is-selected", n.dataset.id === selectedTransport)
      );
      loadPreflight(selectedTransport, $("preflight-inline"));
    });
  });

  $("output-count").textContent = state.outputs.length ? `${state.outputs.length} files` : "empty";
  $("outputs").innerHTML = state.outputs.length
    ? state.outputs
        .map((o) => `<div><span>${esc(o.path)}</span><span>${esc(o.size)} B</span></div>`)
        .join("")
    : `<div class="empty">Nothing generated yet.</div>`;
}

function renderDiagnostics() {
  const d = state.doctor;
  $("check-count").textContent = `${d.counts.ok} ok · ${d.counts.warn} warn · ${d.counts.fail} fail`;

  const ordered = d.checks
    .filter((c) => c.level !== "ok")
    .concat(d.checks.filter((c) => c.level === "ok"));
  $("checks").innerHTML = ordered
    .map(
      (c) => `<li>
        <span class="c-dot ${esc(c.level)}"></span>
        <div>
          <div class="c-title">${esc(c.title)}</div>
          <div class="c-detail">${esc(c.detail)}</div>
          ${c.fix ? `<div class="c-fix">${esc(c.fix)}</div>` : ""}
        </div>
      </li>`
    )
    .join("");

  $("adapter-count").textContent = `${state.machine.interfaces.length} detected`;
  $("iface-table").querySelector("tbody").innerHTML = state.machine.interfaces
    .map(
      (i) => `<tr><td>${esc(i.name)}</td><td>${esc(i.status)}</td><td>${esc(i.addresses.join(", ") || "—")}</td></tr>`
    )
    .join("");

  $("route-table").querySelector("tbody").innerHTML = state.machine.routes
    .slice(0, 14)
    .map(
      (r) => `<tr><td>${esc(r.destination || "default")}</td><td>${esc(r.gateway || "—")}</td><td>${esc(r.interface || "—")}</td></tr>`
    )
    .join("");
}

function renderFiles() {
  const list = $("file-list");
  if (!state.outputs.length) {
    list.innerHTML = `<li class="empty">nothing generated</li>`;
    return;
  }
  list.innerHTML = state.outputs
    .map(
      (o) => `<li data-path="${esc(o.path)}" class="${o.path === activeFile ? "is-active" : ""}">${esc(o.path)}</li>`
    )
    .join("");
  list.querySelectorAll("li[data-path]").forEach((li) => {
    li.addEventListener("click", () => openFile(li.dataset.path));
  });
}

async function openFile(path) {
  activeFile = path;
  renderFiles();
  $("file-name").textContent = path;
  try {
    const res = await fetch(`/api/file?path=${encodeURIComponent(path)}`);
    const body = await res.json();
    if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
    $("file-content").textContent = body.content;
  } catch (err) {
    $("file-content").textContent = String(err.message || err);
  }
}

function renderSettings() {
  const s = state.settings;
  $("site-config-path").textContent = state.config_path;

  const bind = {
    "f-name": s.site.name,
    "f-subnet": s.site.subnet,
    "f-gateway": s.site.gateway,
    "f-bridge": s.site.bridge_address,
    "f-dhcp-start": s.site.dhcp_start,
    "f-dhcp-end": s.site.dhcp_end,
    "f-client": s.client.static_ip,
    "f-lan": s.host.lan_iface,
    "f-port": s.tunnel.listen_port,
    "f-remote": s.tunnel.remote_host,
  };
  for (const [id, value] of Object.entries(bind)) {
    const el = $(id);
    if (el && document.activeElement !== el) el.value = value;
  }

  const d = state.doctor;
  $("plan-verdict").textContent = `${d.counts.ok} ok · ${d.counts.warn} warn · ${d.counts.fail} fail`;
  $("plan-checks").innerHTML = d.checks
    .filter((c) => ["static-ip", "gateway", "bridge-address", "dhcp-pool", "lan-iface"].includes(c.id))
    .map(
      (c) => `<li>
        <span class="c-dot ${esc(c.level)}"></span>
        <div>
          <div class="c-title">${esc(c.title)}</div>
          <div class="c-detail">${esc(c.detail)}</div>
          ${c.fix ? `<div class="c-fix">${esc(c.fix)}</div>` : ""}
        </div>
      </li>`
    )
    .join("");
}

/* --------------------------------------------------------------- preflight */

const PREFLIGHT_CHECKING = `<li><span class="c-dot info"></span><div>Checking…</div></li>`;

const preflightRows = (steps) =>
  steps
    .map(
      (s) => `<li>
        <span class="c-dot ${s.ok ? "ok" : "fail"}"></span>
        <div>${esc(s.text)}${s.fix ? `<div class="p-fix">${esc(s.fix)}</div>` : ""}</div>
      </li>`
    )
    .join("");

async function loadPreflight(transport, target) {
  if (!target) return;
  target.innerHTML = PREFLIGHT_CHECKING;
  try {
    const data = await postJSON("/api/preflight", { transport });
    target.innerHTML = preflightRows(data.steps);
  } catch (err) {
    target.innerHTML = `<li><span class="c-dot fail"></span><div>${esc(err.message || err)}</div></li>`;
  }
}

async function openPreflight(transport) {
  $("modal").classList.remove("is-hidden");
  $("preflight-steps").innerHTML = PREFLIGHT_CHECKING;
  $("preflight-command").textContent = "";
  $("preflight-notes").textContent = "";

  try {
    const data = await postJSON("/api/preflight", { transport });
    $("modal-title").textContent = data.ready ? "Ready to connect" : "Blocked before connecting";
    $("preflight-steps").innerHTML = preflightRows(data.steps);
    $("preflight-command").textContent = data.command;
    $("preflight-notes").textContent = data.notes;
  } catch (err) {
    $("preflight-steps").innerHTML = `<li><span class="c-dot fail"></span><div>${esc(err.message || err)}</div></li>`;
  }
}

/* ------------------------------------------------------------------- views */

function showView(name) {
  document.querySelectorAll(".view").forEach((v) =>
    v.classList.toggle("is-hidden", v.id !== `view-${name}`)
  );
  document.querySelectorAll(".nav-item").forEach((b) =>
    b.classList.toggle("is-active", b.dataset.view === name)
  );
  if (name === "servers" && !$("preflight-inline").dataset.loaded) {
    $("preflight-inline").dataset.loaded = "1";
    loadPreflight(selectedTransport, $("preflight-inline"));
  }
}

document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => showView(btn.dataset.view));
});
document.querySelectorAll("[data-goto]").forEach((btn) => {
  btn.addEventListener("click", () => showView(btn.dataset.goto));
});

/* ------------------------------------------------------------------ events */

$("refresh").addEventListener("click", refresh);

$("connect").addEventListener("click", () => openPreflight(selectedTransport || state.settings.tunnel.transport));
$("modal-close").addEventListener("click", () => $("modal").classList.add("is-hidden"));
$("modal").addEventListener("click", (ev) => {
  if (ev.target === $("modal")) $("modal").classList.add("is-hidden");
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") $("modal").classList.add("is-hidden");
});

/* Any Copy button inside a .cmd block copies its sibling <pre>. */
document.addEventListener("click", async (ev) => {
  const btn = ev.target.closest("[data-copy]");
  if (!btn) return;
  const pre = btn.parentElement.querySelector("pre");
  if (!pre) return;
  try {
    await navigator.clipboard.writeText(pre.textContent);
    btn.textContent = "Copied";
    setTimeout(() => { btn.textContent = "Copy"; }, 1200);
  } catch {
    btn.textContent = "Select manually";
  }
});

$("copy-command").addEventListener("click", async () => {
  const text = $("preflight-command").textContent;
  try {
    await navigator.clipboard.writeText(text);
    $("copy-command").textContent = "Copied";
    setTimeout(() => { $("copy-command").textContent = "Copy"; }, 1200);
  } catch {
    $("copy-command").textContent = "Select manually";
  }
});

$("act-probe").addEventListener("click", () => {
  $("act-probe-sub").textContent = "checking…";
  refreshProbe();
});

$("act-generate").addEventListener("click", async () => {
  const sub = $("act-generate-sub");
  sub.textContent = "writing…";
  try {
    const data = await postJSON("/api/generate", {
      transport: selectedTransport || state.settings.tunnel.transport,
      role: "both",
    });
    state = data.state;
    render();
    sub.textContent = `${data.written.length} files written`;
  } catch (err) {
    sub.textContent = String(err.message || err);
  }
});

$("site-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const payload = {
    site: {
      name: $("f-name").value.trim(),
      subnet: $("f-subnet").value.trim(),
      gateway: $("f-gateway").value.trim(),
      bridge_address: $("f-bridge").value.trim(),
      dhcp_start: $("f-dhcp-start").value.trim(),
      dhcp_end: $("f-dhcp-end").value.trim(),
    },
    client: { static_ip: $("f-client").value.trim() },
    host: { lan_iface: $("f-lan").value.trim() },
    tunnel: {
      listen_port: Number($("f-port").value) || 1194,
      remote_host: $("f-remote").value.trim(),
    },
  };
  const note = $("save-note");
  note.textContent = "saving…";
  try {
    const res = await fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await res.json();
    if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
    state = body;
    render();
    note.textContent = "saved";
  } catch (err) {
    note.textContent = String(err.message || err);
  }
});

document.querySelectorAll("#role-seg .seg").forEach((btn) => {
  btn.addEventListener("click", () => {
    selectedRole = btn.dataset.role;
    document.querySelectorAll("#role-seg .seg").forEach((b) =>
      b.classList.toggle("is-active", b === btn)
    );
  });
});

$("traffic-range").addEventListener("click", (ev) => {
  const chip = ev.target.closest("[data-window]");
  if (!chip) return;
  trafficWindow = Number(chip.dataset.window) || 0;
  document.querySelectorAll("#traffic-range .chip").forEach((c) =>
    c.classList.toggle("is-active", c === chip)
  );
  if (state) drawChart(state.traffic);
});

$("generate").addEventListener("click", async () => {
  const btn = $("generate");
  btn.disabled = true;
  $("generate-note").textContent = "writing…";
  try {
    const data = await postJSON("/api/generate", {
      transport: selectedTransport || state.settings.tunnel.transport,
      role: selectedRole,
    });
    state = data.state;
    render();
    $("generate-note").textContent = `${data.written.length} files`;
  } catch (err) {
    $("generate-note").textContent = String(err.message || err);
  } finally {
    btn.disabled = false;
  }
});

/* Duration ticks locally so the timer is smooth between polls. */
setInterval(() => {
  if (state && !document.hidden) $("d-duration").textContent = fmtDuration(state.session.linked_since);
}, 1000);

setInterval(() => {
  if (!document.hidden) refresh();
}, 5000);

buildHeroMap();
refresh();
