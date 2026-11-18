/* LanBridge appearance system.
 *
 * Four themes, four muted accents, two densities. Everything is a CSS custom
 * property, so every view inherits the same palette by construction - there is
 * no per-page colour to drift.
 *
 * Persistence: one localStorage key. "system" is a real mode, not a fallback:
 * it follows prefers-color-scheme and reacts to OS changes while the page is
 * open.
 */
(function () {
  "use strict";

  const STORAGE_KEY = "lanbridge.appearance";

  const THEMES = [
    { id: "dark", name: "Dark", note: "Deep navy charcoal", swatch: ["#080a0f", "#0f131b", "#222b3b"] },
    { id: "midnight", name: "Midnight", note: "Near black", swatch: ["#050608", "#0b0d12", "#1e232e"] },
    { id: "ocean", name: "Ocean", note: "Blue-grey", swatch: ["#070d12", "#0d151d", "#1e2e3b"] },
    { id: "light", name: "Light", note: "Soft light grey", swatch: ["#eef1f7", "#ffffff", "#dde4ef"] },
  ];

  const ACCENTS = [
    { id: "slate", name: "Slate", hex: "#7c8ba1" },
    { id: "azure", name: "Azure", hex: "#3b82f6" },
    { id: "sage", name: "Sage", hex: "#22a06b" },
    { id: "clay", name: "Clay", hex: "#c4784f" },
    { id: "steel", name: "Steel", hex: "#5f7d8c" },
  ];

  const DEFAULTS = { theme: "dark", accent: "azure", density: "comfortable" };

  const mq = window.matchMedia("(prefers-color-scheme: light)");

  function validTheme(id) {
    return id === "system" || THEMES.some((t) => t.id === id);
  }

  function load() {
    let stored = {};
    try {
      stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}") || {};
    } catch {
      stored = {};
    }
    const prefs = { ...DEFAULTS };
    if (validTheme(stored.theme)) prefs.theme = stored.theme;
    if (ACCENTS.some((a) => a.id === stored.accent)) prefs.accent = stored.accent;
    if (["compact", "comfortable"].includes(stored.density)) prefs.density = stored.density;

    // First visit: respect the OS instead of forcing dark.
    if (!stored.theme && mq.matches) prefs.theme = "system";
    return prefs;
  }

  let prefs = load();

  function resolvedTheme() {
    if (prefs.theme !== "system") return prefs.theme;
    return mq.matches ? "light" : "dark";
  }

  function persist() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
    } catch {
      /* private mode: the session still looks right, it just will not persist */
    }
  }

  function applyTheme() {
    const root = document.documentElement;
    const theme = resolvedTheme();
    root.dataset.theme = theme;
    root.dataset.accent = prefs.accent;
    root.dataset.density = prefs.density;
    root.style.colorScheme = theme === "light" ? "light" : "dark";
  }

  function apply() {
    applyTheme();
    renderControls();
  }

  // Paint the right theme before the body exists: no flash on reload.
  applyTheme();

  function set(patch) {
    prefs = { ...prefs, ...patch };
    persist();
    apply();
  }

  /* ---------------------------------------------------------------- controls */

  function themeLabel() {
    const active = THEMES.find((t) => t.id === resolvedTheme());
    const name = active ? active.name : "Dark";
    return prefs.theme === "system" ? `System · ${name}` : name;
  }

  function renderControls() {
    const label = document.getElementById("theme-label");
    if (label) label.textContent = themeLabel();

    const grid = document.getElementById("theme-grid");
    if (grid) {
      grid.querySelectorAll("[data-theme-id]").forEach((btn) => {
        const id = btn.dataset.themeId;
        const on = id === "system" ? prefs.theme === "system" : prefs.theme === id;
        btn.classList.toggle("is-selected", on);
        btn.setAttribute("aria-pressed", on ? "true" : "false");
      });
    }

    const accents = document.getElementById("accent-row");
    if (accents) {
      accents.querySelectorAll("[data-accent-id]").forEach((btn) => {
        const on = btn.dataset.accentId === prefs.accent;
        btn.classList.toggle("is-selected", on);
        btn.setAttribute("aria-pressed", on ? "true" : "false");
      });
    }

    const dens = document.getElementById("density-seg");
    if (dens) {
      dens.querySelectorAll("[data-density]").forEach((btn) => {
        const on = btn.dataset.density === prefs.density;
        btn.classList.toggle("is-active", on);
        btn.setAttribute("aria-pressed", on ? "true" : "false");
      });
    }
  }

  function buildControls() {
    const grid = document.getElementById("theme-grid");
    if (grid) {
      grid.innerHTML = THEMES.map(
        (t) => `<button class="theme-card" type="button" data-theme-id="${t.id}"
            aria-pressed="false" title="${t.note}">
            <span class="theme-chip" style="--c1:${t.swatch[0]};--c2:${t.swatch[1]};--c3:${t.swatch[2]}"></span>
            <span class="theme-name">${t.name}</span>
            <span class="theme-note">${t.note}</span>
          </button>`
      ).join("");
      grid.addEventListener("click", (ev) => {
        const btn = ev.target.closest("[data-theme-id]");
        if (btn) set({ theme: btn.dataset.themeId });
      });
    }

    const system = document.getElementById("theme-system");
    if (system) {
      system.addEventListener("click", () =>
        set({ theme: prefs.theme === "system" ? resolvedTheme() : "system" })
      );
    }

    const accents = document.getElementById("accent-row");
    if (accents) {
      accents.innerHTML = ACCENTS.map(
        (a) => `<button class="accent-dot" type="button" data-accent-id="${a.id}"
            style="--swatch:${a.hex}" aria-pressed="false" title="${a.name}">
            <span class="sr-only">${a.name}</span>
          </button>`
      ).join("");
      accents.addEventListener("click", (ev) => {
        const btn = ev.target.closest("[data-accent-id]");
        if (btn) set({ accent: btn.dataset.accentId });
      });
    }

    const dens = document.getElementById("density-seg");
    if (dens) {
      dens.addEventListener("click", (ev) => {
        const btn = ev.target.closest("[data-density]");
        if (btn) set({ density: btn.dataset.density });
      });
    }
  }

  /* ---------------------------------------------------------------- popover */

  function popover() {
    return document.getElementById("theme-pop");
  }

  function setOpen(open) {
    const pop = popover();
    const btn = document.getElementById("theme-btn");
    if (!pop || !btn) return;
    pop.classList.toggle("is-hidden", !open);
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) renderControls();
  }

  function toggle() {
    const pop = popover();
    if (pop) setOpen(pop.classList.contains("is-hidden"));
  }

  document.addEventListener("click", (ev) => {
    const pop = popover();
    const btn = document.getElementById("theme-btn");
    if (!pop || !btn) return;
    if (pop.classList.contains("is-hidden")) return;
    if (pop.contains(ev.target) || btn.contains(ev.target)) return;
    setOpen(false);
  });

  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") setOpen(false);
  });

  /* ------------------------------------------------------------------- init */

  function init() {
    buildControls();
    apply();

    const btn = document.getElementById("theme-btn");
    if (btn) btn.addEventListener("click", toggle);

    const onChange = () => {
      if (prefs.theme === "system") apply();
    };
    if (mq.addEventListener) mq.addEventListener("change", onChange);
    else if (mq.addListener) mq.addListener(onChange);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.LBAppearance = {
    get prefs() {
      return { ...prefs };
    },
    set,
    reset() {
      prefs = { ...DEFAULTS };
      persist();
      apply();
    },
  };
})();
