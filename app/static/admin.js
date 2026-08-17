/* ══════════════════════════════════════════════════════════════════════
   LeadAI Admin Control Center — frontend application
   Vanilla JS SPA. Every number comes from the real backend; every control
   writes through the admin API and the backend enforces it.
   Design system: "leads turned to gold" — gold = value moments only,
   violet = AI / Gemini only.
   ══════════════════════════════════════════════════════════════════════ */
"use strict";

/* ──────────────────────────────── Core state ──────────────────────── */
const state = {
  user: null,
  view: "dashboard",
  filters: {},
};

const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

/* ──────────────────────────────── API client ──────────────────────── */
async function api(path, opts = {}) {
  let res;
  try {
    res = await fetch(path, {
      method: opts.method || "GET",
      headers: opts.body ? { "Content-Type": "application/json" } : undefined,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
  } catch (err) {
    throw new Error("Network error — is the server running?");
  }
  if (res.status === 401) {
    location.href = "/login";
    throw new Error("Sign in required");
  }
  let data = null;
  try { data = await res.json(); } catch (err) { /* non-JSON */ }
  if (!res.ok) {
    const detail = data && data.detail;
    const message = typeof detail === "string"
      ? detail
      : (detail && detail.message) || `Request failed (${res.status})`;
    throw new Error(message);
  }
  return data;
}

/* ──────────────────────────────── UI helpers ──────────────────────── */
// URLSearchParams that drops empty values: empty contact=/min_confidence=/
// is_lead= parameters would 422 on FastAPI's bool/float query coercion.
function qsOf(params) {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== "" && v !== null && v !== undefined) qs.set(k, v);
  }
  return qs;
}

function esc(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function fmtTime(value) {
  if (!value) return "—";
  const d = new Date(value);
  if (isNaN(d.getTime())) return esc(value);
  return d.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

function relativeTime(value) {
  if (!value) return "—";
  const d = new Date(value);
  if (isNaN(d.getTime())) return "—";
  const secs = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

function money(value) {
  const n = Number(value);
  if (isNaN(n)) return "—";
  return n.toFixed(4);
}

function fmtNum(value) {
  const n = Number(value);
  if (value === undefined || value === null || isNaN(n)) return "—";
  return n.toLocaleString();
}

function fmtBytes(value) {
  const n = Number(value);
  if (value === undefined || value === null || isNaN(n)) return "—";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let i = -1;
  let v = n;
  do { v /= 1024; i++; } while (v >= 1024 && i < units.length - 1);
  return `${v.toFixed(v >= 10 ? 1 : 2)} ${units[i]}`;
}

/* Inline SVG icons (24×24, stroke = currentColor) */
const ICONS = {
  dashboard: '<path d="M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z"/>',
  jobs: '<circle cx="12" cy="12" r="9"/><path d="M10 8.5l6 3.5-6 3.5z" fill="currentColor" stroke="none"/>',
  failed: '<path d="M12 3l10 18H2z"/><path d="M12 10v5"/><circle cx="12" cy="17.5" r=".6" fill="currentColor" stroke="none"/>',
  leads: '<path d="M12 3l2.4 6.2L21 9.7l-4.9 4.4 1.4 6.4L12 17.2l-5.5 3.3 1.4-6.4L3 9.7l6.6-.5z"/>',
  analytics: '<path d="M4 20V4M4 20h16"/><path d="M8 16v-5M12 16V8M16 16v-3M20 16V6"/>',
  platforms: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a15 15 0 010 18 15 15 0 010-18z"/>',
  apify: '<rect x="4" y="8" width="16" height="11" rx="2"/><path d="M12 8V5M8 5h8"/><path d="M9 14h.01M12 14h.01M15 14h.01"/>',
  actors: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  usage: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/><path d="M7 6.5C5.6 7.6 5 9.6 5 12s.6 4.4 2 5.5"/>',
  environment: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3"/><path d="M2 14h4M10 8h4M18 16h4"/>',
  ai: '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 15l.9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9z"/>',
  scoring: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none"/>',
  ci: '<path d="M21 12a8 8 0 01-8 8H4l2.5-2.5A8 8 0 1121 12z"/><path d="M8.5 11h.01M12 11h.01M15.5 11h.01"/>',
  limits: '<path d="M12 3l8 3v6c0 4.5-3.2 7.7-8 9-4.8-1.3-8-4.5-8-9V6z"/><path d="M12 8v5M12 15.5h.01"/>',
  database: '<ellipse cx="12" cy="5.5" rx="8" ry="2.5"/><path d="M4 5.5v13c0 1.4 3.6 2.5 8 2.5s8-1.1 8-2.5v-13"/><path d="M4 12c0 1.4 3.6 2.5 8 2.5s8-1.1 8-2.5"/>',
  logs: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9l3 3-3 3M13 15h4"/>',
  health: '<path d="M3 12h4l2-6 4 12 2-6h6"/>',
  exports: '<path d="M12 4v11M8 11l4 4 4-4"/><path d="M4 19h16"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.8-3.2 3.3-5 6.5-5s5.7 1.8 6.5 5"/><circle cx="17.5" cy="9" r="2.5"/><path d="M17 15c2.6.3 4.2 2 4.5 5"/>',
  security: '<rect x="5" y="10" width="14" height="10" rx="2"/><path d="M8 10V7a4 4 0 018 0v3"/><circle cx="12" cy="15" r="1.2"/>',
  features: '<path d="M4 8h10M18 8h2M4 16h2M10 16h10"/><circle cx="16" cy="8" r="2"/><circle cx="8" cy="16" r="2"/>',
  maintenance: '<path d="M14.5 6.5a5 5 0 106.9 6.9c-.4 2.6-2.4 4.6-5 5L4 21l2.6-12.4a5 5 0 015-5c-.8 2.2-.6 3.9.9 2.9z"/>',
  audit: '<path d="M5 4h14v16H5z"/><path d="M9 8h6M9 12h6M9 16h3"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M16.5 16.5L21 21"/>',
  bell: '<path d="M6 9a6 6 0 0112 0c0 5 2 6 2 6H4s2-1 2-6z"/><path d="M10 19a2 2 0 004 0"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 012-2h10"/>',
  external: '<path d="M14 4h6v6M20 4L10 14"/><path d="M20 14v5a2 2 0 01-2 2H6a2 2 0 01-2-2V7a2 2 0 012-2h5"/>',
  eye: '<path d="M2 12s3.5-6.5 10-6.5S22 12 22 12s-3.5 6.5-10 6.5S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  refresh: '<path d="M20 12a8 8 0 11-2.3-5.7M20 4v4h-4"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  run: '<path d="M8 5v14l11-7z" fill="currentColor" stroke="none"/>',
};

function icon(name, size = 16) {
  const p = ICONS[name] || ICONS.dashboard;
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${p}</svg>`;
}

function initials(name) {
  const clean = String(name || "?").trim();
  const parts = clean.split(/[\s@.]+/).filter(Boolean);
  const a = (parts[0] || "?")[0] || "?";
  const b = parts.length > 1 ? (parts[1] || "")[0] : "";
  return (a + b).toUpperCase() || "?";
}

/* ──────────────────────────────── Badges ──────────────────────────── */
function statusBadge(status) {
  const map = {
    running: ["green", "running"], started: ["green", "started"],
    completed: ["green", "completed"], error: ["red", "error"],
    cancelled: ["gray", "cancelled"], queued: ["amber", "queued"],
    skipped: ["gray", "skipped"], empty: ["gray", "empty"],
    pending: ["amber", "pending"], not_started: ["gray", "not started"],
  };
  const live = status === "running" || status === "started";
  const [cls, label] = map[status] || ["gray", status || "—"];
  return `<span class="adm-badge ${cls}${live ? " pulse" : ""}">${label}</span>`;
}

function platformBadge(p) {
  return `<span class="adm-badge gray plain">${esc(p || "unknown")}</span>`;
}

function qualityBadge(q) {
  if (!q) return `<span class="adm-badge gray plain">—</span>`;
  const cls = q === "hot" ? "red" : q === "warm" ? "amber" : "gray";
  return `<span class="adm-badge ${cls}">${esc(q)}</span>`;
}

function leadStatusBadge(s) {
  const map = { new: "amber", contacted: "gold", qualified: "green", converted: "green", ignored: "gray" };
  return `<span class="adm-badge ${map[s] || "gray"}">${esc(s || "—")}</span>`;
}

function scorePill(score) {
  const n = Number(score);
  if (isNaN(n)) return `<span class="adm-badge gray plain">—</span>`;
  return `<span class="adm-badge gold">${n}</span>`;
}

function engineBadge(engine) {
  return `<span class="adm-badge ${engine === "gemini" ? "violet" : "gray"}">${esc(engine || "rule")}</span>`;
}

function toast(message, kind = "info") {
  const el = document.createElement("div");
  el.className = `adm-toast ${kind === "ok" ? "ok" : kind === "error" ? "err" : kind === "warn" ? "warn" : ""}`;
  el.textContent = message;
  $("#toasts").appendChild(el);
  setTimeout(() => {
    el.classList.add("out");
    setTimeout(() => el.remove(), 320);
  }, 3800);
}

function openModal(title, bodyHtml, actionsHtml) {
  const box = $("#modalBox");
  box.innerHTML = `
    <h3>${esc(title)}</h3>
    <div class="adm-modal-body">${bodyHtml}</div>
    <div class="adm-modal-actions">${actionsHtml || ""}</div>`;
  $("#modalBackdrop").hidden = false;
  return box;
}

function closeModal() {
  $("#modalBackdrop").hidden = true;
  $("#modalBox").innerHTML = "";
}

function confirmModal(title, bodyHtml, onConfirm, confirmLabel = "Confirm") {
  openModal(title, bodyHtml, `
    <button class="adm-btn" data-close>Cancel</button>
    <button class="adm-btn danger" id="confirmBtn">${esc(confirmLabel)}</button>`);
  $("#modalBackdrop").onclick = (e) => { if (e.target.id === "modalBackdrop") closeModal(); };
  $("[data-close]", $("#modalBox")).onclick = closeModal;
  $("#confirmBtn").onclick = async () => {
    const btn = $("#confirmBtn");
    btn.disabled = true;
    try {
      await onConfirm();
      closeModal();
    } catch (err) {
      toast(err.message, "error");
      btn.disabled = false;
    }
  };
}

function skeleton() {
  return `<div class="adm-skeleton-block"></div><div class="adm-skeleton-block"></div><div class="adm-skeleton-block"></div>`;
}

function errorState(message, retryFn, context = "dashboard") {
  const root = $("#view");
  root.innerHTML = `
    <div class="adm-card" style="margin-top:8px">
      <div class="adm-empty">
        <div class="adm-empty-ico">⚠</div>
        <div style="font-size:14px;color:var(--text);margin-bottom:6px">Unable to load ${esc(context)} data</div>
        <div style="font-size:12.5px;margin-bottom:16px">${esc(message)}</div>
        <button class="adm-btn primary" id="retryBtn">↻ Retry</button>
      </div>
    </div>`;
  const btn = $("#retryBtn", root);
  if (btn && retryFn) btn.onclick = retryFn;
}

function emptyState(iconName, text) {
  return `<div class="adm-empty"><div class="adm-empty-ico">${icon(iconName, 22)}</div>${esc(text)}</div>`;
}

function pagerHtml(total, offset, limit, cb) {
  const pages = Math.max(1, Math.ceil(total / limit));
  const current = Math.floor(offset / limit) + 1;
  return `
    <div class="adm-pager">
      <span>${total.toLocaleString()} total · page ${current} / ${pages}</span>
      <button class="adm-btn small" data-pg="prev" ${current <= 1 ? "disabled" : ""}>‹ Prev</button>
      <button class="adm-btn small" data-pg="next" ${current >= pages ? "disabled" : ""}>Next ›</button>
    </div>`;
}

function bindPager(root, cb) {
  $$("[data-pg]", root).forEach((btn) => {
    btn.onclick = () => cb(btn.dataset.pg === "prev" ? -1 : 1);
  });
}

const CRUMBS = {
  dashboard: "Main", jobs: "Main", failed: "Main", leads: "Main", analytics: "Main",
  platforms: "Platforms & Data Sources", apify: "Platforms & Data Sources",
  actors: "Platforms & Data Sources", usage: "Platforms & Data Sources",
  environment: "Platforms & Data Sources", ai: "AI & Lead Engine",
  scoring: "AI & Lead Engine", ci: "AI & Lead Engine",
  limits: "Operations", database: "Operations", logs: "Operations",
  health: "Operations", exports: "Operations",
  users: "Administration", security: "Administration", features: "Administration",
  maintenance: "Administration", audit: "Administration",
  pages: "Main", posts: "Main",
};

function pageHead(title, sub, actions = "") {
  const crumb = CRUMBS[state.view] || "Admin";
  return `
    <div class="adm-head">
      <div class="adm-crumbline">${esc(crumb)}</div>
      <div class="adm-head-row">
        <div class="adm-head-main">
          <h1 class="adm-title">${esc(title)}</h1>
          <p class="adm-desc">${sub}</p>
        </div>
        ${actions ? `<div class="adm-head-actions">${actions}</div>` : ""}
      </div>
    </div>`;
}

function settingsForm(keys, labels, values, opts = {}) {
  let html = `<div class="adm-form-grid">`;
  keys.forEach((key) => {
    const v = values[key];
    const label = labels[key] || key;
    const hint = opts.hints ? (opts.hints[key] || "") : "";
    const type = typeof v === "boolean"
      ? "bool"
      : typeof v === "number" ? "number" : "text";
    if (type === "bool") {
      html += `
        <div class="adm-field">
          <label>${esc(label)}</label>
          <div class="adm-flex">
            <label class="adm-toggle">
              <input type="checkbox" data-k="${esc(key)}" ${v ? "checked" : ""}>
              <span class="adm-toggle-slider"></span>
            </label>
            <span class="adm-hint">${esc(hint)}</span>
          </div>
        </div>`;
    } else {
      html += `
        <div class="adm-field">
          <label>${esc(label)}</label>
          <input class="adm-input" type="${type}" data-k="${esc(key)}" value="${esc(v ?? "")}">
          ${hint ? `<span class="adm-hint">${esc(hint)}</span>` : ""}
        </div>`;
    }
  });
  html += `</div>`;
  if (opts.saveLabel !== false) {
    html += `<div class="adm-btn-row" style="margin-top:16px">
      <button class="adm-btn primary" id="saveSettingsBtn">${esc(opts.saveLabel || "Save Changes")}</button>
    </div>`;
  }
  return html;
}

function collectSettings(root) {
  const out = {};
  $$("[data-k]", root).forEach((el) => {
    const key = el.dataset.k;
    const type = el.getAttribute("type");
    if (type === "checkbox") out[key] = el.checked;
    else if (type === "number") out[key] = Number(el.value);
    else out[key] = el.value;
  });
  return out;
}

function bindSettingsSave(root, endpoint, cb) {
  const btn = $("#saveSettingsBtn", root);
  if (!btn) return;
  btn.onclick = async () => {
    btn.disabled = true;
    try {
      const body = collectSettings(root);
      const res = await api(endpoint, { method: "PUT", body });
      toast(`Saved ${res.changed.length} setting(s)`, "ok");
      if (cb) cb();
    } catch (err) {
      toast(err.message, "error");
    } finally {
      btn.disabled = false;
    }
  };
}

/* ──────────────────────────────── Charts ──────────────────────────── */
// Gold-first chart primitives. Everything is real data; only the drawing
// is ours. Multi-series charts use gold for the primary series and muted
// gray for secondary ones (opacity stagger, no rainbow).

function sparkSvg(points, w = 110, h = 30) {
  const vals = points.map(Number);
  if (!vals.length || vals.every((v) => v === 0)) {
    return `<svg class="adm-kpi-spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"></svg>`;
  }
  const max = Math.max(...vals, 1);
  const min = Math.min(...vals, 0);
  const span = max - min || 1;
  const step = w / (vals.length - 1 || 1);
  const pts = vals.map((v, i) => `${(i * step).toFixed(1)},${(h - 3 - ((v - min) / span) * (h - 8)).toFixed(1)}`);
  return `<svg class="adm-kpi-spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">
    <polyline points="${pts.join(" ")}" fill="none" stroke="#e3b25c" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" opacity="0.9"/>
  </svg>`;
}

function areaSvg(labels, values, opts = {}) {
  const { w = 860, h = 210, color = "#e3b25c", id = "goldArea" } = opts;
  const vals = values.map(Number);
  const n = vals.length;
  const padL = 10, padR = 10, padT = 12, padB = 24;
  const iw = w - padL - padR, ih = h - padT - padB;
  const max = Math.max(...vals, 1);
  const x = (i) => padL + (n <= 1 ? iw / 2 : (i / (n - 1)) * iw);
  const y = (v) => padT + ih - (v / max) * ih;
  const line = vals.map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const area = `${line} L${x(n - 1).toFixed(1)},${padT + ih} L${x(0).toFixed(1)},${padT + ih} Z`;
  const ticks = n > 1 ? [0, Math.floor(n / 2), n - 1] : [0];
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="trend chart">
    <defs>
      <linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="${color}" stop-opacity="0.28"/>
        <stop offset="100%" stop-color="${color}" stop-opacity="0.02"/>
      </linearGradient>
    </defs>
    <g stroke="rgba(111,106,120,0.25)" stroke-width="1">
      ${[0.25, 0.5, 0.75].map((f) => `<line x1="${padL}" y1="${(padT + ih * f).toFixed(1)}" x2="${w - padR}" y2="${(padT + ih * f).toFixed(1)}"/>`).join("")}
    </g>
    <path d="${area}" fill="url(#${id})"/>
    <path d="${line}" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
    ${ticks.map((i) => `
      <text x="${x(i).toFixed(1)}" y="${h - 7}" text-anchor="${i === 0 ? "start" : i === n - 1 ? "end" : "middle"}" fill="#5f5b68" font-size="10" font-family="JetBrains Mono, monospace">${esc(labels[i] || "")}</text>`).join("")}
  </svg>`;
}

function dualAreaSvg(labels, primary, secondary, opts = {}) {
  // primary → gold, secondary → muted gray
  const { w = 860, h = 210, pColor = "#e3b25c", sColor = "#6f6a78", id = "dualGoldArea" } = opts;
  const n = primary.length;
  const padL = 10, padR = 10, padT = 12, padB = 24;
  const iw = w - padL - padR, ih = h - padT - padB;
  const max = Math.max(1, ...primary.map(Number), ...secondary.map(Number));
  const x = (i) => padL + (n <= 1 ? iw / 2 : (i / (n - 1)) * iw);
  const y = (v) => padT + ih - (v / max) * ih;
  const path = (vals) => vals.map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(Number(v)).toFixed(1)}`).join(" ");
  const ticks = n > 1 ? [0, Math.floor(n / 2), n - 1] : [0];
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="dual trend chart">
    <defs>
      <linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="${pColor}" stop-opacity="0.28"/>
        <stop offset="100%" stop-color="${pColor}" stop-opacity="0.02"/>
      </linearGradient>
    </defs>
    <g stroke="rgba(111,106,120,0.25)" stroke-width="1">
      ${[0.25, 0.5, 0.75].map((f) => `<line x1="${padL}" y1="${(padT + ih * f).toFixed(1)}" x2="${w - padR}" y2="${(padT + ih * f).toFixed(1)}"/>`).join("")}
    </g>
    <path d="${path(secondary)}" fill="none" stroke="${sColor}" stroke-width="1.6" stroke-dasharray="4 4" stroke-linecap="round"/>
    <path d="${path(primary)}" fill="none" stroke="${pColor}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
    ${ticks.map((i) => `
      <text x="${x(i).toFixed(1)}" y="${h - 7}" text-anchor="${i === 0 ? "start" : i === n - 1 ? "end" : "middle"}" fill="#5f5b68" font-size="10" font-family="JetBrains Mono, monospace">${esc(labels[i] || "")}</text>`).join("")}
  </svg>`;
}

function vbarsSvg(labels, values, opts = {}) {
  // Vertical gold bars with per-bar optional second (red) overlay.
  const { w = 860, h = 200, id = "vbarGrad", overlay = null } = opts;
  const vals = values.map(Number);
  const n = vals.length;
  const padL = 10, padR = 10, padT = 12, padB = 24;
  const iw = w - padL - padR, ih = h - padT - padB;
  const max = Math.max(...vals, 1);
  const bw = Math.max(2, (iw / n) * 0.62);
  const step = n <= 1 ? iw : iw / n;
  const ov = overlay ? overlay.map(Number) : null;
  const ticks = n > 1 ? [0, Math.floor(n / 2), n - 1] : [0];
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="bar chart">
    <defs>
      <linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#f2cd86"/>
        <stop offset="100%" stop-color="#b8853a"/>
      </linearGradient>
    </defs>
    <g stroke="rgba(111,106,120,0.25)" stroke-width="1">
      ${[0.25, 0.5, 0.75].map((f) => `<line x1="${padL}" y1="${(padT + ih * f).toFixed(1)}" x2="${w - padR}" y2="${(padT + ih * f).toFixed(1)}"/>`).join("")}
    </g>
    ${vals.map((v, i) => {
      const cx = padL + i * step + step / 2;
      const bh = (v / max) * ih;
      const bhO = ov && ov[i] ? (ov[i] / max) * ih : 0;
      return `
        <rect x="${(cx - bw / 2).toFixed(1)}" y="${(padT + ih - bh).toFixed(1)}" width="${bw.toFixed(1)}" height="${bh.toFixed(1)}" rx="2" fill="url(#${id})"/>
        ${ov && ov[i] ? `<rect x="${(cx - bw / 2).toFixed(1)}" y="${(padT + ih - bhO).toFixed(1)}" width="${bw.toFixed(1)}" height="${bhO.toFixed(1)}" rx="2" fill="#e8664f" opacity="0.85"/>` : ""}`;
    }).join("")}
    ${ticks.map((i) => `
      <text x="${(padL + i * step + step / 2).toFixed(1)}" y="${h - 7}" text-anchor="middle" fill="#5f5b68" font-size="10" font-family="JetBrains Mono, monospace">${esc(labels[i] || "")}</text>`).join("")}
  </svg>`;
}

/* ──────────────────────────────── Drawer ──────────────────────────── */
function openDrawer(html) {
  const backdrop = $("#drawerBackdrop");
  const drawer = $("#drawer");
  drawer.innerHTML = html;
  drawer.hidden = false;
  backdrop.hidden = false;
  document.body.style.overflow = "hidden";
}

function closeDrawer() {
  const backdrop = $("#drawerBackdrop");
  const drawer = $("#drawer");
  drawer.hidden = true;
  backdrop.hidden = true;
  drawer.innerHTML = "";
  document.body.style.overflow = "";
}

/* Shared job report markup (drawer + details page) */
function jobReportHtml(data, runId) {
  const j = data.job;
  const counts = data.counts || {};
  const intent = j.intent || {};
  const platform = j.platform || intent.platform;
  const running = j.status === "running" || j.status === "queued";
  const url = intent.canonical_url || j.query;
  const duration = (() => {
    if (!j.completed_at || !j.created_at) return "—";
    const ms = new Date(j.completed_at).getTime() - new Date(j.created_at).getTime();
    if (isNaN(ms) || ms < 0) return "—";
    if (ms < 60000) return `${Math.round(ms / 1000)}s`;
    if (ms < 3600000) return `${Math.round(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`;
    return `${Math.floor(ms / 3600000)}h ${Math.round((ms % 3600000) / 60000)}m`;
  })();
  return `
    <div class="adm-drawer-head">
      <div>
        <div class="adm-crumbline">Run report</div>
        <h2 class="adm-title">${esc(runId)}</h2>
        <div style="margin-top:6px">${statusBadge(j.status)} ${platformBadge(platform)}</div>
      </div>
      <button class="adm-drawer-close" id="drawerClose" aria-label="Close">${icon("close")}</button>
    </div>
    <div class="adm-grid-2" style="grid-template-columns:1fr 1fr">
      <div class="adm-card" style="margin-bottom:14px">
        <div class="adm-card-title">Run info</div>
        <div class="adm-kv">
          <div class="adm-kv-row"><dt>Run ID</dt><dd><span class="adm-code">${esc(runId)}</span></dd></div>
          ${j.retried_from ? `<div class="adm-kv-row" data-nav="jobs/details:${esc(j.retried_from)}"><dt>Retry of</dt><dd><span class="adm-code">${esc(j.retried_from)}</span></dd></div>` : ""}
          <div class="adm-kv-row"><dt>Query / URL</dt><dd>${url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(String(url).slice(0, 70))}</a>` : "—"}</dd></div>
          <div class="adm-kv-row"><dt>Phase</dt><dd>${esc(j.phase || "—")}</dd></div>
          <div class="adm-kv-row"><dt>Provider</dt><dd>${esc(j.provider || "—")}</dd></div>
          <div class="adm-kv-row"><dt>Created</dt><dd>${fmtTime(j.created_at)}</dd></div>
          <div class="adm-kv-row"><dt>Completed</dt><dd>${j.completed_at ? fmtTime(j.completed_at) : running ? `<span class="adm-badge green pulse">in progress</span>` : "—"}</dd></div>
          <div class="adm-kv-row"><dt>Duration</dt><dd>${duration}</dd></div>
        </div>
        ${j.status === "error" ? `<div class="adm-note" style="color:var(--red)">${esc((j.message || j.error || "Failed").slice(0, 300))}</div>` : ""}
        ${j.message ? `<div class="adm-cell-sub" style="margin-top:8px">${esc(String(j.message).slice(0, 300))}</div>` : ""}
      </div>
      <div class="adm-card" style="margin-bottom:14px">
        <div class="adm-card-title">Collected data <span class="adm-hint">(live counts)</span></div>
        <div class="adm-stats cols-3">
          <div class="adm-stat"><div class="adm-stat-label">Pages</div><div class="adm-stat-value">${counts.pages ?? 0}</div></div>
          <div class="adm-stat"><div class="adm-stat-label">Posts</div><div class="adm-stat-value">${counts.posts ?? 0}</div></div>
          <div class="adm-stat"><div class="adm-stat-label">Comments</div><div class="adm-stat-value">${counts.comments ?? 0}</div></div>
          <div class="adm-stat"><div class="adm-stat-label">AI analyzed</div><div class="adm-stat-value">${counts.analyzed ?? 0}</div></div>
          <div class="adm-stat"><div class="adm-stat-label">Leads</div><div class="adm-stat-value ${counts.leads ? "gold" : ""}">${counts.leads ?? 0}</div></div>
        </div>
        <div class="adm-kv" style="margin-top:10px">
          ${j.pages_found !== undefined && j.pages_found !== null ? `<div class="adm-kv-row"><dt>Pages found</dt><dd>${esc(j.pages_found)}</dd></div>` : ""}
          ${j.pages_stored !== undefined && j.pages_stored !== null ? `<div class="adm-kv-row"><dt>Pages stored</dt><dd>${esc(j.pages_stored)}</dd></div>` : ""}
          ${j.limit !== undefined ? `<div class="adm-kv-row"><dt>Post limit</dt><dd>${esc(j.limit)}</dd></div>` : ""}
          ${intent.max_comments_per_post ? `<div class="adm-kv-row"><dt>Comments / post</dt><dd>${esc(intent.max_comments_per_post)}</dd></div>` : ""}
          ${j.usageUsd !== undefined && j.usageUsd !== null ? `<div class="adm-kv-row"><dt>Apify cost</dt><dd>$${money(j.usageUsd)}</dd></div>` : ""}
          ${j.actorRunId ? `<div class="adm-kv-row"><dt>Apify run</dt><dd><span class="adm-code">${esc(j.actorRunId)}</span></dd></div>` : ""}
        </div>
      </div>
    </div>
    <div class="adm-btn-row">
      <a class="adm-btn" href="#/pages?run=${encodeURIComponent(runId)}">Pages of this run</a>
      <a class="adm-btn" href="#/logs?q=${encodeURIComponent(runId)}">View logs</a>
      <a class="adm-btn" href="#/jobs/details:${esc(runId)}">Open full report</a>
    </div>`;
}

/* ──────────────────────────────── Auth / shell ────────────────────── */
async function boot() {
  let authErr = null;
  try {
    const res = await api("/api/auth/me");
    state.user = res.user;
  } catch (err) {
    authErr = err;
  }
  renderShell();
  window.addEventListener("hashchange", () => {
    const view = location.hash.replace("#/", "").split("?")[0] || "dashboard";
    navigate(view);
  });
  if (state.user) {
    const name = state.user.name || state.user.email;
    $("#admUser").textContent = name;
    const roleEl = $("#admRole");
    roleEl.textContent = state.user.role || "viewer";
    $("#admAvatar").textContent = initials(name);
    navigate(location.hash.replace("#/", "").split("?")[0] || "dashboard");
  } else {
    $("#admUser").textContent = "—";
    $("#crumb").textContent = "Connection problem";
    errorState(
      authErr ? authErr.message : "Sign in required",
      () => { $("#view").innerHTML = skeleton(); boot(); },
      "admin");
  }
}

function renderShell() {
  const role = state.user.role;
  $$("#nav .adm-nav-item").forEach((el) => {
    const view = el.dataset.view;
    if (!view) return;
    if (view === "users" || view === "security") {
      el.style.display = role === "super_admin" ? "" : "none";
    }
  });
  const doLogout = async () => {
    try { await api("/api/auth/logout", { method: "POST" }); } catch (err) {}
    location.href = "/login";
  };
  $("#logoutBtn").onclick = doLogout;
  $("#burger").onclick = () => $("#sidebar").classList.toggle("open");

  // Sidebar collapse (persisted; collapses to an icon rail on wide screens)
  const collapseBtn = $("#sidebarCollapse");
  if (collapseBtn) {
    if (localStorage.getItem("admSidebar") === "1") {
      document.body.classList.add("sidebar-collapsed");
    }
    collapseBtn.onclick = () => {
      const collapsed = document.body.classList.toggle("sidebar-collapsed");
      localStorage.setItem("admSidebar", collapsed ? "1" : "0");
    };
  }

  // Profile dropdown (top-right)
  const profileBtn = $("#admProfileBtn");
  const dropdown = $("#admDropdown");
  if (profileBtn) {
    const ddUsers = $("#ddUsers");
    if (ddUsers) ddUsers.hidden = role !== "super_admin";
    profileBtn.onclick = (e) => {
      e.stopPropagation();
      dropdown.hidden = !dropdown.hidden;
    };
    document.addEventListener("click", (e) => {
      if (!e.target.closest("#admProfile")) dropdown.hidden = true;
    });
    $$(".adm-dropdown-item", dropdown).forEach((item) => {
      item.onclick = () => { dropdown.hidden = true; };
    });
    $("#ddLogout").onclick = doLogout;
  }

  // Global search → /api/admin/search
  const searchInput = $("#admSearchInput");
  const searchBox = $("#admSearchResults");
  let searchTimer = null;
  if (searchInput) {
    const runSearch = async (q) => {
      if (q.length < 2) { searchBox.hidden = true; searchBox.innerHTML = ""; return; }
      try {
        const data = await api(`/api/admin/search?q=${encodeURIComponent(q)}`);
        const groups = [
          ["jobs", "Jobs"], ["leads", "Leads"], ["pages", "Pages"],
          ["posts", "Posts"], ["comments", "Comments"], ["platforms", "Platforms"],
        ];
        const any = groups.some(([g]) => (data[g] || []).length);
        searchBox.innerHTML = any ? groups.map(([g, label]) => {
          const items = data[g] || [];
          if (!items.length) return "";
          return `
            <div class="adm-search-group">${label}</div>
            ${items.map((it) => {
              const sub = it.query || it.commenter_name || it.page_name || it.platform || it.run_id || it._id || "";
              const nav = g === "jobs" ? `jobs/details:${esc(it.run_id || it._id)}`
                : g === "leads" ? `leads/details:${esc(it._id)}`
                : g === "platforms" ? `platforms/details:${esc(it.platform)}`
                : g === "comments" ? "ci" : g;
              return `<div class="adm-search-item" data-nav="${nav}">
                ${icon(g === "comments" ? "ci" : g === "platforms" ? "platforms" : g, 14)}
                <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(String(sub).slice(0, 60))}</span>
                <span class="adm-hint">${esc(g)}</span>
              </div>`;
            }).join("")}`;
        }).join("") : `<div class="adm-search-empty">No results for “${esc(q)}”</div>`;
        searchBox.hidden = false;
      } catch (err) {
        searchBox.innerHTML = `<div class="adm-search-empty">${esc(err.message)}</div>`;
        searchBox.hidden = false;
      }
    };
    searchInput.addEventListener("input", () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => runSearch(searchInput.value.trim()), 250);
    });
    searchInput.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { searchBox.hidden = true; searchInput.blur(); }
    });
    document.addEventListener("click", (e) => {
      if (!e.target.closest(".adm-search")) searchBox.hidden = true;
    });
    searchBox.addEventListener("click", (e) => {
      const item = e.target.closest("[data-nav]");
      if (!item) return;
      searchBox.hidden = true;
      searchInput.value = "";
      navigate(item.dataset.nav);
    });
  }

  // Bell → /api/admin/alerts (last 24h)
  const bell = $("#admBell");
  const bellCount = $("#admBellCount");
  const dropdown2 = $("#admDropdown");
  let bellOpen = false;
  if (bell) {
    const loadAlerts = async () => {
      try {
        const data = await api("/api/admin/alerts");
        const alerts = data.alerts || [];
        bellCount.textContent = alerts.length;
        bellCount.hidden = alerts.length === 0;
        bell.dataset.alerts = JSON.stringify(alerts);
      } catch (err) { /* health pill covers this */ }
    };
    loadAlerts();
    setInterval(loadAlerts, 120000);
    bell.onclick = (e) => {
      e.stopPropagation();
      bellOpen = !bellOpen;
      if (!bellOpen) { dropdown2.hidden = true; return; }
      dropdown2.hidden = false;
      let alerts = [];
      try { alerts = JSON.parse(bell.dataset.alerts || "[]"); } catch (err) {}
      dropdown2.className = "adm-dropdown wide";
      dropdown2.innerHTML = alerts.length ? `
        <div class="adm-notif-head"><span>Notifications</span><span class="adm-hint">24h</span></div>
        ${alerts.map((a) => `
          <div class="adm-notif-item" data-alert-nav="${esc(a.route || "")}">
            <span class="adm-notif-dot ${a.severity === "ok" ? "ok" : a.severity === "warn" ? "warn" : a.severity === "info" ? "info" : "err"}"></span>
            <div>
              <div class="adm-notif-title">${esc(a.title)}</div>
              <div class="adm-notif-msg">${esc(a.message)}</div>
            </div>
          </div>`).join("")}`
        : `<div class="adm-notif-head"><span>Notifications</span></div>
           <div class="adm-search-empty">All quiet — nothing in the last 24h.</div>`;
      $$("[data-alert-nav]", dropdown2).forEach((item) => {
        item.onclick = () => {
          dropdown2.hidden = true;
          bellOpen = false;
          if (item.dataset.alertNav) navigate(item.dataset.alertNav);
        };
      });
    };
    document.addEventListener("click", (e) => {
      if (!e.target.closest("#admBell") && !e.target.closest("#admDropdown")) {
        dropdown2.hidden = true;
        bellOpen = false;
      }
    });
  }

  // Health pill → /api/admin/health
  const pill = $("#healthPill");
  const dot = $("#healthDot");
  const label = $("#healthLabel");
  const setHealth = (overall) => {
    const cls = overall === "ok" ? "ok" : overall === "warn" ? "warn" : "err";
    dot.className = `adm-health-dot ${cls}`;
    label.textContent = overall === "ok" ? "Healthy" : overall === "warn" ? "Degraded" : "Down";
  };
  const probeHealth = async () => {
    try {
      const h = await api("/api/admin/health");
      setHealth(h.overall || "ok");
    } catch (err) { setHealth("err"); }
  };
  probeHealth();
  setInterval(probeHealth, 60000);
  if (pill) pill.onclick = () => navigate("health");

  // Drawer / modal close wiring
  $("#drawerBackdrop").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (!$("#drawer").hidden) closeDrawer();
      if (!$("#modalBackdrop").hidden) closeModal();
    }
  });
}

function navigate(view, params = null) {
  const match = /^([a-z-]+)(?:\/([a-zA-Z0-9]+):(.+))?$/.exec(view) ||
    /^([a-z-]+)(?:\/([a-zA-Z0-9]+)\/(.+))?$/.exec(view);
  const base = (match && match[1]) || view;
  const paramType = match && match[2];
  const paramValue = match && match[3];
  const renderers = {
    dashboard: viewDashboard, jobs: viewJobs, failed: viewFailed, leads: viewLeads,
    analytics: viewAnalytics, platforms: viewPlatforms, apify: viewApify,
    actors: viewActors, usage: viewUsage, environment: viewEnvironment,
    limits: viewLimits, ai: viewAI,
    scoring: viewScoring, ci: viewCI, database: viewDatabase, logs: viewLogs,
    exports: viewExports, users: viewUsers, security: viewSecurity,
    features: viewFeatures, maintenance: viewMaintenance, health: viewHealth,
    audit: viewAudit, pages: viewPages, posts: viewPosts,
  };
  const labels = {
    dashboard: "Dashboard", jobs: "Jobs", failed: "Failed Jobs", leads: "Leads",
    analytics: "Analytics", platforms: "Platforms", apify: "Apify",
    actors: "Actors", usage: "Usage & Cost", environment: "Environment",
    limits: "Scraping & Global Limits",
    ai: "AI / Gemini", scoring: "Lead Scoring", ci: "Comment Intelligence",
    database: "Database", logs: "Logs", exports: "Exports", users: "Users",
    security: "Security", features: "Features", maintenance: "Maintenance",
    health: "Health", audit: "Audit Log", pages: "Pages", posts: "Posts",
  };
  let target = null;
  if (paramType) {
    if (base === "jobs" && paramType === "details") target = () => viewJobDetail(paramValue);
    else if (base === "leads" && paramType === "details") target = () => viewLeadDetail(paramValue);
    else if (base === "platforms" && paramType === "details") target = () => viewPlatformDetail(paramValue);
    else target = viewNotFound;
  } else {
    target = renderers[base];
  }
  if (!target) {
    target = viewNotFound;
    state.view = "notfound";
    location.hash = `#/${view}`;
    $$("#nav .adm-nav-item").forEach((el) => el.classList.remove("active"));
    $("#crumb").textContent = "Not found";
    $("#crumbSub").textContent = "";
    const viewEl = $("#view");
    viewEl.innerHTML = skeleton();
    target().catch((err) => errorState(err.message, () => navigate(state.view), "404"));
    return;
  }
  state.view = base;
  if (params) {
    const qs = new URLSearchParams(params);
    location.hash = `#/${base}${qs.toString() ? "?" + qs.toString() : ""}`;
  } else if (!location.hash.startsWith(`#/${view}`)) {
    location.hash = `#/${view}`;
  }
  $$("#nav .adm-nav-item").forEach((el) => el.classList.toggle("active", el.dataset.view === base));
  $("#crumb").textContent = labels[base] || base;
  $("#crumbSub").textContent = "LeadAI · AI Lead Intelligence Platform";
  const viewEl = $("#view");
  viewEl.innerHTML = skeleton();
  target().catch((err) => {
    errorState(err.message, () => navigate(state.view), labels[base] || base);
  });
}

function viewNotFound() {
  $("#view").innerHTML = `
    <div class="adm-card" style="margin-top:8px;max-width:560px">
      <div class="adm-empty">
        <div class="adm-empty-ico">🕳</div>
        <div style="font-size:15px;color:var(--text);margin-bottom:6px">404 — page not found</div>
        <div style="font-size:12.5px;margin-bottom:16px">The route <span class="adm-code">#/${esc(location.hash.replace("#/", ""))}</span> does not exist in the admin panel.</div>
        <a class="adm-btn primary" href="#/dashboard">← Back to Dashboard</a>
      </div>
    </div>`;
}

function bindNav(root) {
  $$("[data-nav]", root).forEach((el) => {
    el.onclick = () => {
      let params = null;
      if (el.dataset.navParams) {
        try { params = JSON.parse(el.dataset.navParams); } catch (err) {}
      }
      navigate(el.dataset.nav, params);
    };
  });
}

/* ──────────────────────────────── DASHBOARD ───────────────────────── */
let DASH_RANGE = { days: 30, from: "", to: "" };

async function viewDashboard() {
  const root = $("#view");
  const r = DASH_RANGE;
  const params = new URLSearchParams();
  if (r.from || r.to) {
    if (r.from) params.set("from_date", r.from);
    if (r.to) params.set("to_date", r.to);
  } else {
    params.set("days", r.days);
  }
  const data = await api(`/api/admin/dashboard?${params}`);
  const kpiOf = (key) => data.kpis.find((k) => k.key === key) || { key, label: key, value: 0, change: null, series: [] };
  const leads = kpiOf("leads");
  const searches = kpiOf("searches");
  const act = data.activity || {};
  const counts = data.counts || {};
  const delta = (k) => {
    if (k.change === null || k.change === undefined) return "";
    const up = k.change >= 0;
    return `<span class="adm-kpi-delta ${k.change === 0 ? "flat" : up ? "up" : "down"}">
      ${up ? "▲" : "▼"} ${Math.abs(k.change).toFixed(1)}% vs prev window</span>`;
  };
  const kpiNav = {
    searches: ["jobs", {}], running: ["jobs", { status: "running" }],
    failed: ["jobs", { status: "error" }], pages: ["pages", {}],
    posts: ["posts", {}], comments: ["ci", {}], analyzed: ["ci", {}],
    leads: ["leads", {}],
  };
  const kpiIco = {
    searches: ["jobs", "gray"], running: ["run", "green"], failed: ["failed", "red"],
    pages: ["platforms", "gray"], posts: ["ci", "gray"], comments: ["ci", "gray"],
    analyzed: ["ai", "violet"], leads: ["leads", "gold"],
  };
  const rangeBtn = (days, label) =>
    `<button class="adm-range-btn ${r.days === days && !r.from ? "active" : ""}" data-days="${days}">${label}</button>`;
  const alerts = data.alerts || [];
  const maxPlat = Math.max(1, ...data.leads_by_platform.map((p) => p.count));
  root.innerHTML = `
    ${pageHead("Dashboard", "Live overview of the LeadAI platform — every number is real data from the database.", `
      <a class="adm-btn primary" href="/" target="_blank">${icon("plus", 14)} New Search</a>`)}
    <div class="adm-hero">
      <div class="adm-hero-num">${Number(leads.value).toLocaleString()}</div>
      <div class="adm-hero-main">
        <div class="adm-hero-label">Leads in range</div>
        <div class="adm-hero-sub"><strong>${Number(counts.leads_contact || 0).toLocaleString()}</strong> with verified phone/email · ${esc(data.range.label)}</div>
        ${delta(leads)}
      </div>
      <div class="adm-hero-stat">
        <div>
          <div class="adm-stat-label">Searches</div>
          <div class="adm-stat-value">${Number(searches.value).toLocaleString()}</div>
          <div class="adm-kpi-delta ${(searches.change || 0) >= 0 ? "up" : "down"}">${searches.change !== null && searches.change !== undefined ? `${searches.change >= 0 ? "▲" : "▼"} ${Math.abs(searches.change).toFixed(1)}%` : ""}</div>
        </div>
        <div>
          <div class="adm-stat-label">Running now</div>
          <div class="adm-stat-value ${kpiOf("running").value ? "ok" : ""}">${kpiOf("running").value}</div>
        </div>
        <div>
          <div class="adm-stat-label">Success rate</div>
          <div class="adm-stat-value">${data.performance.success_rate !== null && data.performance.success_rate !== undefined ? `${data.performance.success_rate}%` : "—"}</div>
        </div>
      </div>
    </div>
    <div class="adm-dash-head">
      <div class="adm-hint">Daily activity for the selected window — click a KPI to open its module.</div>
      <div class="adm-dash-range">
        ${rangeBtn(7, "7d")}${rangeBtn(30, "30d")}${rangeBtn(90, "90d")}
        <input class="adm-input" type="date" id="dashFrom" title="From" value="${esc(r.from)}" style="max-width:140px">
        <input class="adm-input" type="date" id="dashTo" title="To" value="${esc(r.to)}" style="max-width:140px">
        <button class="adm-range-btn" id="dashApply">Apply</button>
      </div>
    </div>
    <div class="adm-kpi-grid">
      ${data.kpis.map((k) => `
        <div class="adm-kpi" data-nav="${kpiNav[k.key][0]}" data-nav-params='${JSON.stringify(kpiNav[k.key][1])}'>
          <div class="adm-kpi-top">
            <span class="adm-kpi-label">${esc(k.label)}</span>
            <span class="adm-kpi-ico ${kpiIco[k.key][1]}">${icon(kpiIco[k.key][0], 13)}</span>
          </div>
          <div class="adm-kpi-value">${Number(k.value).toLocaleString()}</div>
          ${delta(k)}
          ${sparkSvg(k.series)}
        </div>`).join("")}
    </div>
    <div class="adm-grid-2">
      <div class="adm-card">
        <div class="adm-card-title">Activity <span class="adm-hint">(leads gold · searches muted)</span></div>
        <div class="adm-chart">
          ${dualAreaSvg(act.labels || [], act.leads || [], act.searches || [])}
        </div>
        <div class="adm-legend">
          <span class="adm-legend-item"><span class="adm-legend-dot" style="background:var(--gold)"></span>Leads</span>
          <span class="adm-legend-item"><span class="adm-legend-dot" style="background:var(--gray)"></span>Searches</span>
        </div>
      </div>
      <div class="adm-card">
        <div class="adm-card-title">Leads by platform <span class="adm-hint">(click a row)</span></div>
        ${data.leads_by_platform.length ? data.leads_by_platform.map((p, i) => `
          <div class="adm-bar-row" data-nav="platforms/details:${esc(p.platform)}">
            <div class="adm-bar-top">
              <span class="adm-cell-main">${platformBadge(p.platform)}</span>
              <span class="adm-cell-sub">${p.count.toLocaleString()} ${p.count === 1 ? "lead" : "leads"}</span>
            </div>
            <div class="adm-bar-track"><div class="adm-bar-fill" style="width:${Math.max(2, (p.count / maxPlat) * 100).toFixed(1)}%;opacity:${(1 - i * 0.12).toFixed(2)}"></div></div>
          </div>`).join("") : emptyState("leads", "No leads analyzed in this window.")}
      </div>
    </div>
    <div class="adm-grid-2">
      <div class="adm-card">
        <div class="adm-card-title">Alerts <span class="adm-hint">(click to open)</span></div>
        ${alerts.length ? alerts.map((a) => `
          <div class="adm-alert-row" data-nav="${esc(a.route || "health")}">
            <span class="adm-alert-dot ${a.severity === "ok" ? "ok" : a.severity === "warn" ? "warn" : a.severity === "info" ? "info" : "err"}"></span>
            <div>
              <div class="adm-alert-title">${esc(a.title)}</div>
              <div class="adm-alert-msg">${esc(a.message)}</div>
            </div>
          </div>`).join("") : emptyState("health", "All quiet — no alerts for this window.")}
      </div>
      <div class="adm-card">
        <div class="adm-card-title">System status <span class="adm-hint">(click a row to open its page)</span></div>
        <div class="adm-kv">
          <div class="adm-kv-row" data-nav="database"><dt>Database</dt><dd><span class="adm-badge ${data.status.database ? "green" : "red"}">${data.status.database ? "connected" : "down"}</span></dd></div>
          <div class="adm-kv-row" data-nav="apify"><dt>Apify token</dt><dd>${data.status.apify_token ? `<span class="adm-badge green">${esc(data.status.apify_token_hint)}</span>` : `<span class="adm-badge red">not configured</span>`}</dd></div>
          <div class="adm-kv-row" data-nav="ai"><dt>Gemini key</dt><dd>${data.status.gemini_key ? `<span class="adm-badge green">configured</span>` : `<span class="adm-badge amber">not set (rule fallback)</span>`}</dd></div>
          <div class="adm-kv-row" data-nav="features"><dt>URL search</dt><dd>${data.status.url_search_enabled ? `<span class="adm-badge green">enabled</span>` : `<span class="adm-badge red">disabled</span>`}</dd></div>
          <div class="adm-kv-row" data-nav="maintenance"><dt>Maintenance</dt><dd>${data.status.maintenance ? `<span class="adm-badge amber">active</span>` : `<span class="adm-badge gray plain">off</span>`}</dd></div>
        </div>
        <div class="adm-quick-grid" style="margin-top:14px">
          <button class="adm-quick" data-nav="failed">${icon("failed", 15)} Failed jobs</button>
          <button class="adm-quick" data-nav="health">${icon("health", 15)} Health check</button>
          <button class="adm-quick" data-nav="usage">${icon("usage", 15)} Usage & cost</button>
          <button class="adm-quick" data-nav="exports">${icon("exports", 15)} Exports</button>
        </div>
      </div>
    </div>
    <div class="adm-card">
      <div class="adm-card-title">Recent jobs <span class="adm-hint">(click a row for the full report)</span></div>
      ${data.recent_jobs.length ? `
        <div class="adm-table-wrap"><table class="adm-table">
          <thead><tr><th>Run ID</th><th>Query</th><th>Platform</th><th>Status</th><th>Phase</th><th>Created</th></tr></thead>
          <tbody>
            ${data.recent_jobs.map((j) => `
              <tr class="adm-row-link" data-job="${esc(j.run_id)}">
                <td><span class="adm-code">${esc(j.run_id)}</span></td>
                <td><div class="adm-cell-main">${esc((j.query || "").slice(0, 60))}</div></td>
                <td>${platformBadge(j.platform || (j.intent || {}).platform)}</td>
                <td>${statusBadge(j.status)}</td>
                <td>${esc(j.phase || "—")}</td>
                <td>${relativeTime(j.created_at)}</td>
              </tr>`).join("")}
          </tbody>
        </table></div>`
      : emptyState("jobs", "No searches yet — run one from the app.")}
    </div>`;
  bindNav(root);
  $$("[data-job]", root).forEach((row) => {
    row.onclick = () => openJobDrawer(row.dataset.job);
  });
  $$("[data-days]", root).forEach((btn) => {
    btn.onclick = () => {
      DASH_RANGE = { days: Number(btn.dataset.days), from: "", to: "" };
      viewDashboard();
    };
  });
  $("#dashApply").onclick = () => {
    const from = $("#dashFrom").value;
    const to = $("#dashTo").value;
    if (!from && !to) { DASH_RANGE = { days: 30, from: "", to: "" }; }
    else { DASH_RANGE = { days: 0, from, to }; }
    viewDashboard();
  };
}

async function openJobDrawer(runId) {
  openDrawer(`<div class="adm-card" style="margin-bottom:14px">${skeleton()}</div>`);
  try {
    const data = await api(`/api/admin/jobs/${encodeURIComponent(runId)}`);
    const html = jobReportHtml(data, runId);
    $("#drawer").innerHTML = html;
    const closeBtn = $("#drawerClose");
    if (closeBtn) closeBtn.onclick = closeDrawer;
    bindNav($("#drawer"));
  } catch (err) {
    $("#drawer").innerHTML = `
      <div class="adm-drawer-head">
        <div><div class="adm-crumbline">Run report</div><h2 class="adm-title">${esc(runId)}</h2></div>
        <button class="adm-drawer-close" id="drawerClose">${icon("close")}</button>
      </div>
      <div class="adm-empty">${esc(err.message)}</div>`;
    $("#drawerClose").onclick = closeDrawer;
  }
}

/* ──────────────────────────────── JOBS ────────────────────────────── */
const JOB_FILTERS = { status: "", platform: "", q: "", from: "", to: "" };
const JOB_PILLS = [
  ["", "All"], ["running", "Running"], ["completed", "Completed"],
  ["error", "Failed"], ["cancelled", "Cancelled"], ["queued", "Queued"],
];

async function viewJobs() {
  const root = $("#view");
  const f = JOB_FILTERS;
  const hashParams = new URLSearchParams(location.hash.split("?")[1] || "");
  if (hashParams.has("status") || hashParams.has("platform") || hashParams.has("q")) {
    f.status = hashParams.get("status") || "";
    f.platform = hashParams.get("platform") || "";
    f.q = hashParams.get("q") || "";
  }
  const qs = new URLSearchParams({
    status: f.status, platform: f.platform, q: f.q,
    from_date: f.from, to_date: f.to,
    offset: state.filters.jobsOffset || 0, limit: 20,
  });
  const data = await api(`/api/admin/jobs?${qs}`);
  const role = state.user.role;
  root.innerHTML = `
    ${pageHead("Jobs", "All search runs. Retry recreates a failed run with the same URL and limits; deletion is permanent. Click a row for the full run report.", `
      <a class="adm-btn primary" href="/" target="_blank">${icon("plus", 14)} New Search</a>
      <a class="adm-btn" href="/api/admin/export/jobs.csv" ${role === "viewer" ? "onclick='return false'" : ""}>${icon("exports", 14)} Export CSV</a>`)}
    <div class="adm-card">
      <div class="adm-filters">
        <div class="adm-pills">
          ${JOB_PILLS.map(([val, label]) => `
            <button class="adm-pill ${f.status === val ? (val === "error" ? "red" : val === "running" ? "green" : "active") : ""}" data-pill="${val}">
              ${val === "running" ? `<span class="adm-status-dot ok pulse"></span>` : ""}${label}
            </button>`).join("")}
        </div>
        <select class="adm-select" id="fPlatform">
          <option value="">All platforms</option>
          ${["facebook", "instagram", "linkedin", "youtube"].map((p) => `<option value="${p}" ${f.platform === p ? "selected" : ""}>${p}</option>`).join("")}
        </select>
        <input class="adm-input" id="fQ" placeholder="Search query / run id…" value="${esc(f.q)}">
        <input class="adm-input" type="date" id="fFrom" title="From date" value="${esc(f.from)}">
        <input class="adm-input" type="date" id="fTo" title="To date" value="${esc(f.to)}">
        <button class="adm-btn primary" id="applyFilters">Filter</button>
        <button class="adm-btn" id="clearFilters">Clear</button>
      </div>
      <div class="adm-table-wrap"><table class="adm-table">
        <thead><tr><th>Run ID</th><th>Query</th><th>Platform</th><th>Status</th><th>Phase</th><th>Message</th><th>Created</th><th></th></tr></thead>
        <tbody>
          ${data.items.length ? data.items.map((j) => `
            <tr class="adm-row-link" data-job="${esc(j.run_id)}">
              <td><span class="adm-code">${esc(j.run_id)}</span>
                ${j.retried_from ? `<div class="adm-cell-sub">retry of ${esc(j.retried_from)}</div>` : ""}</td>
              <td><div class="adm-cell-main">${esc((j.query || "").slice(0, 70))}</div></td>
              <td>${platformBadge(j.platform || (j.intent || {}).platform)}</td>
              <td>${statusBadge(j.status)}</td>
              <td>${esc(j.phase || "—")}</td>
              <td><div class="adm-cell-sub">${esc((j.message || j.error || "").slice(0, 90))}</div></td>
              <td>${relativeTime(j.created_at)}</td>
              <td>
                <div class="adm-btn-row">
                  <button class="adm-btn small" data-act="cancel" data-id="${esc(j.run_id)}" ${j.status !== "running" || role === "viewer" ? "disabled" : ""}>◼ Cancel</button>
                  <button class="adm-btn small" data-act="retry" data-id="${esc(j.run_id)}" ${j.status === "running" || role === "viewer" ? "disabled" : ""}>↻ Retry</button>
                  <button class="adm-btn small danger" data-act="del" data-id="${esc(j.run_id)}" ${j.status === "running" || role !== "super_admin" ? "disabled" : ""}>${icon("close", 12)}</button>
                </div>
              </td>
            </tr>`).join("")
          : `<tr><td colspan="8">${emptyState("jobs", "No jobs match these filters.")}</td></tr>`}
        </tbody>
      </table></div>
      ${pagerHtml(data.total, data.offset, data.limit, (dir) => {
        const off = data.offset + dir * data.limit;
        state.filters.jobsOffset = Math.max(0, off);
        viewJobs();
      })}
    </div>`;
  $$("[data-pill]", root).forEach((btn) => {
    btn.onclick = () => {
      JOB_FILTERS.status = btn.dataset.pill;
      state.filters.jobsOffset = 0;
      viewJobs();
    };
  });
  $("#applyFilters").onclick = () => {
    state.filters.jobsOffset = 0;
    f.platform = $("#fPlatform").value;
    f.q = $("#fQ").value.trim();
    f.from = $("#fFrom").value;
    f.to = $("#fTo").value;
    viewJobs();
  };
  $("#clearFilters").onclick = () => {
    Object.assign(f, { status: "", platform: "", q: "", from: "", to: "" });
    state.filters.jobsOffset = 0;
    viewJobs();
  };
  $$("[data-job]", root).forEach((row) => {
    row.onclick = (e) => {
      if (e.target.closest("button")) return;
      openJobDrawer(row.dataset.job);
    };
  });
  $$("[data-act]", root).forEach((btn) => {
    btn.onclick = (e) => {
      e.stopPropagation();
      const id = btn.dataset.id;
      if (btn.dataset.act === "retry") {
        confirmModal("Retry search run", `Re-run <span class="adm-code">${esc(id)}</span> with its original URL and limits? A new run id is created.`,
          async () => {
            await api(`/api/admin/jobs/${encodeURIComponent(id)}/retry`, { method: "POST" });
            toast("Retry started", "ok");
            viewJobs();
          }, "Retry");
      } else if (btn.dataset.act === "cancel") {
        confirmModal("Cancel search run", `Stop <span class="adm-code">${esc(id)}</span>? The run is marked cancelled and the scrape thread stops.`,
          async () => {
            await api(`/api/admin/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" });
            toast("Cancellation requested", "ok");
            viewJobs();
          }, "Cancel Run");
      } else {
        confirmModal("Delete search run", `<b>Permanent.</b> The run and all its pages, posts, comments and AI leads will be removed. This cannot be undone.`,
          async () => {
            await api(`/api/admin/jobs/${encodeURIComponent(id)}`, { method: "DELETE" });
            toast("Search run deleted", "ok");
            viewJobs();
          }, "Delete Forever");
      }
    };
  });
}

/* ──────────────────────────────── JOB DETAIL ──────────────────────── */
let jobPollTimer = null;

async function viewJobDetail(runId) {
  if (jobPollTimer) { clearInterval(jobPollTimer); jobPollTimer = null; }
  const root = $("#view");
  const role = state.user.role;
  const data = await api(`/api/admin/jobs/${encodeURIComponent(runId)}`);
  const j = data.job;
  const running = j.status === "running" || j.status === "queued";
  root.innerHTML = `
    ${pageHead(`Job ${esc(runId)}`, "Full run report from the real search history record.", `
      <a class="adm-btn" href="#/jobs">← All Jobs</a>
      <button class="adm-btn" id="jdRefresh">${icon("refresh", 14)} Refresh</button>
      <button class="adm-btn primary" id="jdRetry" ${!running && role !== "viewer" ? "" : "disabled"} data-act="retry">↻ Retry</button>
      <button class="adm-btn" id="jdCancel" ${running && role !== "viewer" ? "" : "disabled"} data-act="cancel">◼ Cancel</button>
      <button class="adm-btn danger" id="jdDelete" ${!running && role === "super_admin" ? "" : "disabled"} data-act="del">${icon("close", 14)} Delete</button>`)}
    ${jobReportHtml(data, runId)}
    <div class="adm-card">
      <div class="adm-card-title">Raw run record</div>
      <pre class="adm-code" style="display:block;padding:14px;font-size:12px;line-height:1.55;color:var(--text-dim);max-height:360px;overflow:auto;white-space:pre-wrap;word-break:break-word;background:var(--surface-2);border-radius:var(--radius-sm)">${esc(JSON.stringify({ ...j, intent: j.intent || {} }, null, 2).slice(0, 6000))}</pre>
    </div>`;
  $$("[data-act]", root).forEach((btn) => {
    btn.onclick = () => {
      if (btn.dataset.act === "retry") {
        confirmModal("Retry search run", `Re-run <span class="adm-code">${esc(runId)}</span> with its original URL and limits?`,
          async () => {
            await api(`/api/admin/jobs/${encodeURIComponent(runId)}/retry`, { method: "POST" });
            toast("Retry started", "ok");
            viewJobDetail(runId);
          }, "Retry");
      } else if (btn.dataset.act === "cancel") {
        confirmModal("Cancel search run", `Stop <span class="adm-code">${esc(runId)}</span>?`,
          async () => {
            await api(`/api/admin/jobs/${encodeURIComponent(runId)}/cancel`, { method: "POST" });
            toast("Cancellation requested", "ok");
            viewJobDetail(runId);
          }, "Cancel Run");
      } else {
        confirmModal("Delete search run", `<b>Permanent.</b> The run and all its pages, posts, comments and AI leads will be removed.`,
          async () => {
            await api(`/api/admin/jobs/${encodeURIComponent(runId)}`, { method: "DELETE" });
            toast("Search run deleted", "ok");
            navigate("jobs");
          }, "Delete Forever");
      }
    };
  });
  $("#jdRefresh").onclick = () => viewJobDetail(runId);
  if (running) {
    jobPollTimer = setInterval(() => {
      if (document.hidden) return;
      viewJobDetail(runId);
    }, 5000);
  }
}

window.addEventListener("hashchange", () => {
  if (jobPollTimer) { clearInterval(jobPollTimer); jobPollTimer = null; }
});

/* ──────────────────────────────── FAILED JOBS ─────────────────────── */
async function viewFailed() {
  const root = $("#view");
  const data = await api(`/api/admin/failed-jobs?offset=0&limit=50`);
  const role = state.user.role;
  const s = data.summary || {};
  const topErr = s.top_error;
  root.innerHTML = `
    ${pageHead("Failed Jobs", "Runs that ended in an error, with the failure reason and a one-click retry.", `
      <button class="adm-btn primary" id="retryAll" ${!data.items.length || role === "viewer" ? "disabled" : ""}>↻ Retry All</button>`)}
    <div class="adm-hero red">
      <div class="adm-hero-num">${Number(s.total || 0).toLocaleString()}</div>
      <div class="adm-hero-main">
        <div class="adm-hero-label">Failed runs (all time)</div>
        <div class="adm-hero-sub"><strong>${s.today ?? 0}</strong> today · <strong>${s.this_week ?? 0}</strong> this week</div>
      </div>
      ${topErr ? `
      <div class="adm-hero-stat" style="margin-left:0">
        <div>
          <div class="adm-stat-label">Most common error</div>
          <div class="adm-cell-sub" style="max-width:340px;color:var(--text-dim)">${esc(topErr.message)}</div>
          <div class="adm-stat-hint">${topErr.count} run(s)</div>
        </div>
      </div>` : ""}
    </div>
    <div class="adm-card">
      <div class="adm-table-wrap"><table class="adm-table">
        <thead><tr><th>Run ID</th><th>Query</th><th>Platform</th><th>Error</th><th>Created</th><th></th></tr></thead>
        <tbody>
          ${data.items.length ? data.items.map((j) => `
            <tr class="adm-row-link" data-job="${esc(j.run_id)}">
              <td><span class="adm-code">${esc(j.run_id)}</span></td>
              <td><div class="adm-cell-main">${esc((j.query || "").slice(0, 60))}</div></td>
              <td>${platformBadge(j.platform || (j.intent || {}).platform)}</td>
              <td><div class="adm-cell-sub" style="color:var(--red)">${esc((j.error || j.message || "Unknown error").slice(0, 160))}</div></td>
              <td>${relativeTime(j.created_at)}</td>
              <td>
                <div class="adm-btn-row">
                  <button class="adm-btn small" data-act="retry" data-id="${esc(j.run_id)}" ${role === "viewer" ? "disabled" : ""}>↻ Retry</button>
                </div>
              </td>
            </tr>`).join("")
          : `<tr><td colspan="6">${emptyState("health", "No failed jobs. Everything is healthy.")}</td></tr>`}
        </tbody>
      </table></div>
    </div>`;
  $$("[data-job]", root).forEach((row) => {
    row.onclick = (e) => {
      if (e.target.closest("button")) return;
      openJobDrawer(row.dataset.job);
    };
  });
  $$("[data-act]", root).forEach((btn) => {
    btn.onclick = (e) => {
      e.stopPropagation();
      const id = btn.dataset.id;
      confirmModal("Retry failed job", `Re-run <span class="adm-code">${esc(id)}</span>?`,
        async () => {
          await api(`/api/admin/jobs/${encodeURIComponent(id)}/retry`, { method: "POST" });
          toast("Retry started", "ok");
          viewFailed();
        }, "Retry");
    };
  });
  const retryAll = $("#retryAll");
  if (retryAll) retryAll.onclick = () => confirmModal(
    "Retry all failed runs", `Re-run all <b>${data.items.length}</b> failed runs shown here, one by one, with their original URLs and limits?`,
    async () => {
      const btn = $("#retryAll");
      btn.disabled = true;
      let ok = 0;
      for (const j of data.items) {
        try {
          await api(`/api/admin/jobs/${encodeURIComponent(j.run_id)}/retry`, { method: "POST" });
          ok += 1;
        } catch (err) { /* keep going */ }
      }
      toast(`Retried ${ok} of ${data.items.length} runs`, ok === data.items.length ? "ok" : "warn");
      viewFailed();
    }, "Retry All");
}

/* ──────────────────────────────── LEADS ───────────────────────────── */
const LEAD_FILTERS = { platform: "", quality: "", status: "", q: "" };

async function viewLeads() {
  const root = $("#view");
  const f = LEAD_FILTERS;
  const qs = new URLSearchParams({
    platform: f.platform, quality: f.quality, status: f.status, q: f.q,
    offset: state.filters.leadsOffset || 0, limit: 25,
  });
  const data = await api(`/api/admin/leads?${qs}`);
  const role = state.user.role;
  const statuses = ["new", "contacted", "qualified", "converted", "ignored"];
  const s = data.summary || {};
  const platEntries = Object.entries(s.by_platform || {}).slice(0, 3);
  root.innerHTML = `
    ${pageHead("Leads", "AI-analyzed comments. Update their pipeline status, bulk-mark, export or delete.", `
      <a class="adm-btn" href="/api/admin/export/leads.csv" ${role === "viewer" ? "onclick='return false'" : ""}>${icon("exports", 14)} Export CSV</a>`)}
    <div class="adm-hero">
      <div class="adm-hero-num">${Number(s.total || 0).toLocaleString()}</div>
      <div class="adm-hero-main">
        <div class="adm-hero-label">Total leads</div>
        <div class="adm-hero-sub"><strong>${Number(s.with_contact || 0).toLocaleString()}</strong> with verified phone or email</div>
      </div>
      <div class="adm-hero-stat">
        <div>
          <div class="adm-stat-label">Found this week</div>
          <div class="adm-stat-value">${Number(s.this_week || 0).toLocaleString()}</div>
        </div>
        <div>
          <div class="adm-stat-label">Avg score</div>
          <div class="adm-stat-value">${s.avg_score !== null && s.avg_score !== undefined ? s.avg_score : "—"}</div>
        </div>
        ${platEntries.map(([p, c]) => `
        <div>
          <div class="adm-stat-label">${esc(p)}</div>
          <div class="adm-stat-value">${Number(c).toLocaleString()}</div>
        </div>`).join("")}
      </div>
    </div>
    <div class="adm-card">
      <div class="adm-filters">
        <input class="adm-input" id="lPlat" placeholder="Platform" list="platOpts" value="${esc(f.platform)}">
        <input class="adm-input" id="lQuality" placeholder="Quality" list="qualityOpts" value="${esc(f.quality)}">
        <datalist id="qualityOpts"><option>hot</option><option>warm</option><option>cold</option></datalist>
        <select class="adm-select" id="lStatus">
          <option value="">Any status</option>
          ${statuses.map((s2) => `<option value="${s2}" ${f.status === s2 ? "selected" : ""}>${s2}</option>`).join("")}
        </select>
        <input class="adm-input" id="lQ" placeholder="Text / name / phone / email…" value="${esc(f.q)}">
        <button class="adm-btn primary" id="applyFilters">Filter</button>
        <button class="adm-btn" id="clearFilters">Clear</button>
      </div>
      <div class="adm-flex" style="margin-bottom:12px">
        <select class="adm-select" id="bulkStatus" ${role === "viewer" ? "disabled" : ""}>
          ${statuses.map((s2) => `<option value="${s2}">Mark selected → ${s2}</option>`).join("")}
        </select>
        <button class="adm-btn" id="bulkApply" ${role === "viewer" ? "disabled" : ""}>Apply</button>
        <button class="adm-btn danger" id="bulkDelete" ${role !== "super_admin" ? "disabled" : ""}>Delete selected</button>
        <span class="adm-hint" id="selCount"></span>
      </div>
      <div class="adm-table-wrap"><table class="adm-table">
        <thead><tr>
          ${role !== "viewer" ? `<th><input type="checkbox" id="selAll"></th>` : ""}
          <th>Commenter</th><th>Text</th><th>Platform</th><th>Score</th><th>Quality</th><th>Status</th><th>Contact</th><th>Analyzed</th>
        </tr></thead>
        <tbody>
          ${data.items.length ? data.items.map((l) => `
            <tr class="adm-row-link" data-lead="${esc(l._id)}">
              ${role !== "viewer" ? `<td><input type="checkbox" class="row-sel" value="${esc(l._id)}"></td>` : ""}
              <td><div class="adm-cell-main">${esc(l.commenter_name || "—")}</div>
                <div class="adm-cell-sub">${esc(l.page_name || "")}</div></td>
              <td><div class="adm-cell-sub" style="max-width:320px">${esc((l.comment_text || "").slice(0, 140))}</div></td>
              <td>${platformBadge(l.platform)}</td>
              <td>${scorePill(l.lead_score)}</td>
              <td>${qualityBadge(l.lead_quality)}</td>
              <td>${leadStatusBadge(l.lead_status)}</td>
              <td>${l.phone || l.email || l.whatsapp ? `
                <div class="adm-cell-sub">${[l.phone, l.whatsapp, l.email].filter(Boolean).map((v) => esc(String(v))).join(" · ")}</div>`
                : `<span class="adm-badge gray plain">no contact</span>`}</td>
              <td>${relativeTime(l.analyzed_at)}</td>
            </tr>`).join("")
          : `<tr><td colspan="${role !== "viewer" ? 9 : 8}">${emptyState("leads", "No leads match these filters.")}</td></tr>`}
        </tbody>
      </table></div>
      ${pagerHtml(data.total, data.offset, data.limit, (dir) => {
        const off = data.offset + dir * data.limit;
        state.filters.leadsOffset = Math.max(0, off);
        viewLeads();
      })}
    </div>`;
  const selected = () => $$(".row-sel", root).filter((el) => el.checked).map((el) => el.value);
  const updateSel = () => { $("#selCount").textContent = `${selected().length} selected`; };
  updateSel();
  const apply = () => {
    state.filters.leadsOffset = 0;
    f.platform = $("#lPlat").value.trim();
    f.quality = $("#lQuality").value.trim();
    f.status = $("#lStatus").value;
    f.q = $("#lQ").value.trim();
    viewLeads();
  };
  $("#applyFilters").onclick = apply;
  $("#clearFilters").onclick = () => {
    Object.assign(f, { platform: "", quality: "", status: "", q: "" });
    state.filters.leadsOffset = 0;
    viewLeads();
  };
  $$(".row-sel", root).forEach((el) => el.onchange = updateSel);
  $$("[data-lead]", root).forEach((row) => {
    row.onclick = (e) => {
      if (e.target.closest("button") || e.target.closest("input")) return;
      navigate(`leads/details:${row.dataset.lead}`);
    };
  });
  const all = $("#selAll");
  if (all) all.onchange = () => $$(".row-sel", root).forEach((el) => { el.checked = all.checked; updateSel(); });
  const bulk = async (action, value) => {
    const ids = selected();
    if (!ids.length) { toast("Select at least one lead", "warn"); return; }
    try {
      const res = await api("/api/admin/leads/bulk", { method: "POST", body: { ids, action, value } });
      toast(`Updated ${res.affected} lead(s)`, "ok");
      viewLeads();
    } catch (err) { toast(err.message, "error"); }
  };
  $("#bulkApply").onclick = () => bulk("set_status", $("#bulkStatus").value);
  $("#bulkDelete").onclick = () => confirmModal(
    "Delete selected leads", `<b>Permanent.</b> ${selected().length} AI lead record(s) will be removed.`,
    () => bulk("delete"), "Delete Forever");
}

/* ──────────────────────────────── LEAD DETAIL ─────────────────────── */
async function viewLeadDetail(leadId) {
  const data = await api(`/api/admin/leads/${encodeURIComponent(leadId)}`);
  const lead = data.lead;
  const role = state.user.role;
  const statuses = ["new", "contacted", "qualified", "converted", "ignored"];
  const contactValues = [lead.phone, lead.whatsapp, lead.email].filter(Boolean);
  const contactLinks = (v, kind) => {
    const href = kind === "phone"
      ? `tel:${encodeURIComponent(String(v))}`
      : `mailto:${encodeURIComponent(String(v))}`;
    return `<button class="adm-btn small" data-copy="${esc(String(v))}">${icon("copy", 12)} Copy</button>
      <a class="adm-btn small" href="${href}">Open</a>`;
  };
  openModal(`Lead — ${esc(lead.commenter_name || "Unknown")}`, `
    <div class="adm-kv">
      <dt>Commenter</dt><dd><div class="adm-cell-main">${esc(lead.commenter_name || "—")}</div></dd>
      <dt>Page</dt><dd>${esc(lead.page_name || "—")}</dd>
      <dt>Platform</dt><dd>${platformBadge(lead.platform)}</dd>
      <dt>Comment</dt><dd><div style="max-width:520px;font-size:13px;line-height:1.5">${esc((lead.comment_text || "").slice(0, 600))}</div></dd>
      <dt>Post</dt><dd>${lead.post_url ? `<a href="${esc(lead.post_url)}" target="_blank" rel="noopener">open post ↗</a>` : "—"}</dd>
      <dt>Lead score</dt><dd>${scorePill(lead.lead_score)}${lead.signal_score !== undefined ? ` <span class="adm-hint">signal ${esc(lead.signal_score)}</span>` : ""}</dd>
      <dt>Quality</dt><dd>${qualityBadge(lead.lead_quality)}</dd>
      <dt>Priority</dt><dd><span class="adm-badge gray plain">${esc(lead.priority || "—")}</span></dd>
      <dt>Confidence</dt><dd>${lead.confidence !== undefined ? `${(Number(lead.confidence) * 100).toFixed(0)}%` : "—"}</dd>
      <dt>Intent</dt><dd>${esc(lead.intent || "—")}</dd>
      ${lead.budget ? `<dt>Budget</dt><dd>${esc(lead.budget)}</dd>` : ""}
      ${lead.requirement ? `<dt>Requirement</dt><dd>${esc(lead.requirement)}</dd>` : ""}
      ${lead.urgency ? `<dt>Urgency</dt><dd>${esc(lead.urgency)}</dd>` : ""}
      ${lead.location ? `<dt>Location</dt><dd>${esc(lead.location)}</dd>` : ""}
      ${lead.website ? `<dt>Website</dt><dd><a href="${esc(lead.website)}" target="_blank" rel="noopener">${esc(lead.website)}</a></dd>` : ""}
      <dt>Reason</dt><dd><div class="adm-cell-sub">${esc((lead.reason || "").slice(0, 300))}</div></dd>
      <dt>Analyzed</dt><dd>${fmtTime(lead.analyzed_at)} by ${engineBadge(lead.analyzed_by)}</dd>
      <dt>Status</dt><dd>
        <select class="adm-select" id="leadStatus" ${role === "viewer" ? "disabled" : ""}>
          ${statuses.map((s) => `<option value="${s}" ${lead.lead_status === s ? "selected" : ""}>${s}</option>`).join("")}
        </select>
      </dd>
    </div>
    ${contactValues.length ? `
    <div class="adm-kv" style="margin-top:10px">
      <dt>Contact</dt><dd>
        ${contactValues.map((v) => `
          <div style="margin-bottom:6px;display:flex;gap:8px;align-items:center">
            <span class="adm-code">${esc(String(v))}</span>
            ${contactLinks(v, String(v).includes("@") ? "email" : "phone")}
          </div>`).join("")}
      </dd>
    </div>` : ""}`, `
    <button class="adm-btn" data-close>Close</button>
    ${role !== "viewer" ? `<button class="adm-btn primary" id="leadStatusSave">Save status</button>` : ""}
    ${role === "super_admin" ? `<button class="adm-btn danger" id="leadDelete">Delete</button>` : ""}`);
  $("#modalBackdrop").onclick = (e) => { if (e.target.id === "modalBackdrop") closeModal(); };
  $("[data-close]", $("#modalBox")).onclick = closeModal;
  $$("[data-copy]", $("#modalBox")).forEach((btn) => {
    btn.onclick = async () => {
      try {
        await navigator.clipboard.writeText(btn.dataset.copy);
        toast("Copied", "ok");
      } catch (err) {
        toast("Copy failed — select manually", "error");
      }
    };
  });
  const saveBtn = $("#leadStatusSave");
  if (saveBtn) saveBtn.onclick = async () => {
    try {
      await api(`/api/admin/leads/${encodeURIComponent(leadId)}`, {
        method: "PATCH", body: { status: $("#leadStatus").value },
      });
      toast("Lead status saved", "ok");
      closeModal();
    } catch (err) { toast(err.message, "error"); }
  };
  const delBtn = $("#leadDelete");
  if (delBtn) delBtn.onclick = () => confirmModal(
    "Delete lead", `<b>Permanent.</b> This AI lead record will be removed.`,
    async () => {
      await api("/api/admin/leads/bulk", { method: "POST", body: { ids: [leadId], action: "delete" } });
      toast("Lead deleted", "ok");
      closeModal();
      viewLeads();
    }, "Delete Forever");
}

/* ──────────────────────────────── ANALYTICS ───────────────────────── */
let ANALYTICS_RANGE = { days: 14, from: "", to: "" };

async function viewAnalytics() {
  const root = $("#view");
  const r = ANALYTICS_RANGE;
  let qs = "";
  if (r.from || r.to) {
    qs = `?from_date=${encodeURIComponent(r.from)}&to_date=${encodeURIComponent(r.to)}`;
  } else {
    qs = `?days=${r.days}`;
  }
  const data = await api(`/api/admin/analytics${qs}`);
  // Week-over-week delta for the hero (real: same window on the dashboard).
  let wotw = null;
  try {
    const dash = await api(`/api/admin/dashboard?days=${r.days || 30}`);
    const lk = dash.kpis.find((k) => k.key === "leads");
    wotw = lk && lk.change !== null && lk.change !== undefined ? lk.change : null;
  } catch (err) { /* non-fatal */ }
  const t = data.totals || {};
  const rangeBtn = (days, label) =>
    `<button class="adm-range-btn ${r.days === days && !r.from ? "active" : ""}" data-range="${days}">${label}</button>`;
  const distBars = (obj, badgeFn, emptyIco, emptyTxt) => {
    const entries = Object.entries(obj || {});
    if (!entries.length) return emptyState(emptyIco, emptyTxt);
    const total = entries.reduce((a, [, v]) => a + v, 0) || 1;
    const max = Math.max(...entries.map(([, v]) => v), 1);
    return entries.map(([k, v], i) => `
      <div class="adm-bar-row">
        <div class="adm-bar-top">
          <span class="adm-cell-main">${badgeFn(k)}</span>
          <span class="adm-cell-sub">${v} · ${Math.round((v / total) * 100)}%</span>
        </div>
        <div class="adm-bar-track"><div class="adm-bar-fill" style="width:${Math.max(2, (v / max) * 100).toFixed(1)}%;opacity:${(1 - i * 0.13).toFixed(2)}"></div></div>
      </div>`).join("");
  };
  const jobLabels = data.jobs_series.map((d) => d.date.slice(5));
  const jobFailed = data.jobs_series.map((d) => d.failed || 0);
  root.innerHTML = `
    ${pageHead("Analytics", "Aggregations over real database records for the selected range.", `
      <button class="adm-range-btn" data-range="today">Today</button>
      ${rangeBtn(7, "7 days")}${rangeBtn(14, "14 days")}${rangeBtn(30, "30 days")}${rangeBtn(90, "90 days")}
      <input class="adm-input" type="date" id="anFrom" title="From" value="${esc(r.from)}" style="max-width:150px">
      <input class="adm-input" type="date" id="anTo" title="To" value="${esc(r.to)}" style="max-width:150px">
      <button class="adm-btn primary small" id="anApply">Apply</button>`)}
    <div class="adm-hero">
      <div class="adm-hero-num small">${(t.leads ?? 0).toLocaleString()}</div>
      <div class="adm-hero-main">
        <div class="adm-hero-label">Leads in range</div>
        <div class="adm-hero-sub">${wotw !== null ? `<strong>${wotw >= 0 ? "▲" : "▼"} ${Math.abs(wotw).toFixed(1)}%</strong> week-over-week` : `${data.range ? `from ${esc(data.range.from)} to ${esc(data.range.to)}` : ""}`}</div>
      </div>
      <div class="adm-hero-stat">
        <div>
          <div class="adm-stat-label">Success rate</div>
          <div class="adm-stat-value">${t.success_rate !== null && t.success_rate !== undefined ? `${t.success_rate}%` : "—"}</div>
          <div class="adm-stat-hint">${t.failed ?? 0} failed of ${t.jobs ?? 0}</div>
        </div>
        <div>
          <div class="adm-stat-label">Pages</div>
          <div class="adm-stat-value">${(t.pages ?? 0).toLocaleString()}</div>
        </div>
        <div>
          <div class="adm-stat-label">Comments</div>
          <div class="adm-stat-value">${(t.comments ?? 0).toLocaleString()}</div>
        </div>
      </div>
    </div>
    <div class="adm-grid-2">
      <div class="adm-card">
        <div class="adm-card-title">Jobs per day <span class="adm-hint">(red = failed share)</span></div>
        <div class="adm-chart">
          ${data.jobs_series.length ? vbarsSvg(jobLabels, data.jobs_series.map((d) => d.jobs), { overlay: jobFailed }) : emptyState("jobs", "No jobs in range.")}
        </div>
      </div>
      <div class="adm-card">
        <div class="adm-card-title">Leads per day</div>
        <div class="adm-chart">
          ${data.leads_series.length ? areaSvg(data.leads_series.map((d) => d.date.slice(5)), data.leads_series.map((d) => d.leads)) : emptyState("leads", "No leads analyzed in range.")}
        </div>
      </div>
      <div class="adm-card">
        <div class="adm-card-title">Job statuses</div>
        ${distBars(data.job_statuses, statusBadge, "jobs", "No jobs yet.")}
      </div>
      <div class="adm-card">
        <div class="adm-card-title">Leads by platform</div>
        ${distBars(data.leads_by_platform, platformBadge, "leads", "No leads yet.")}
      </div>
      <div class="adm-card">
        <div class="adm-card-title">Lead quality distribution</div>
        ${distBars(data.quality, qualityBadge, "ci", "No analyzed comments yet.")}
      </div>
      <div class="adm-card">
        <div class="adm-card-title">Lead score distribution</div>
        ${Object.keys(data.score_distribution || {}).length ? Object.entries(data.score_distribution).map(([k, v], i) => `
          <div class="adm-bar-row">
            <div class="adm-bar-top">
              <span class="adm-cell-main"><span class="adm-code">${esc(k)}</span></span>
              <span class="adm-cell-sub">${v}</span>
            </div>
            <div class="adm-bar-track"><div class="adm-bar-fill violet" style="width:${Math.max(2, (v / Math.max(1, ...Object.values(data.score_distribution))) * 100).toFixed(1)}%;opacity:${(1 - i * 0.13).toFixed(2)}"></div></div>
          </div>`).join("") : emptyState("scoring", "No scored leads in range.")}
      </div>
    </div>
    <div class="adm-card">
      <div class="adm-card-title">Platform performance</div>
      ${data.platform_perf && data.platform_perf.length ? `
        <div class="adm-table-wrap"><table class="adm-table">
          <thead><tr><th>Platform</th><th>Runs</th><th>Completed</th><th>Failed</th><th>Success</th><th>Leads</th></tr></thead>
          <tbody>
            ${data.platform_perf.map((p) => `
              <tr class="adm-row-link" data-nav="platforms/details:${esc(p.platform)}">
                <td>${platformBadge(p.platform)}</td>
                <td>${p.runs}</td>
                <td>${p.completed}</td>
                <td>${p.failed}</td>
                <td>${p.success_rate !== null && p.success_rate !== undefined ? `${p.success_rate}%` : "—"}</td>
                <td>${p.leads}</td>
              </tr>`).join("")}
          </tbody>
        </table></div>` : emptyState("platforms", "No platform data in range.")}
    </div>
    <div class="adm-grid-2">
      <div class="adm-card">
        <div class="adm-card-title">Top pages by leads</div>
        ${data.top_pages.length ? `
          <div class="adm-table-wrap"><table class="adm-table">
            <thead><tr><th>Page</th><th>Leads</th></tr></thead>
            <tbody>
              ${data.top_pages.map((p) => `
                <tr><td><div class="adm-cell-main">${esc(p.page)}</div></td><td>${p.leads}</td></tr>`).join("")}
            </tbody>
          </table></div>` : emptyState("leads", "No lead data yet.")}
      </div>
      <div class="adm-card">
        <div class="adm-card-title">Lead pipeline status</div>
        ${distBars(data.lead_statuses || {}, leadStatusBadge, "leads", "No leads in range.")}
      </div>
    </div>`;
  const setRange = (days) => {
    ANALYTICS_RANGE = { days, from: "", to: "" };
    viewAnalytics();
  };
  $$("[data-range]", root).forEach((btn) => {
    btn.onclick = () => {
      if (btn.dataset.range === "today") {
        const now = new Date();
        const iso = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
        ANALYTICS_RANGE = { days: 0, from: iso, to: iso };
        viewAnalytics();
      } else {
        setRange(Number(btn.dataset.range));
      }
    };
  });
  $("#anApply").onclick = () => {
    const from = $("#anFrom").value;
    const to = $("#anTo").value;
    if (!from && !to) { setRange(14); return; }
    ANALYTICS_RANGE = { days: 0, from, to };
    viewAnalytics();
  };
  bindNav(root);
}

/* ──────────────────────────────── PLATFORMS ───────────────────────── */
async function viewPlatforms() {
  const root = $("#view");
  const data = await api("/api/admin/platforms");
  const role = state.user.role;
  root.innerHTML = `
    ${pageHead("Platforms", "Enable/disable platforms server-side and manage the Apify actors each platform scrapes with. Disabling blocks every scrape entry point — including the user app.", `
      <span class="adm-badge gold">${data.platforms.filter((p) => p.enabled).length}/${data.platforms.length} enabled</span>`)}
    <div class="adm-grid-2">
      ${data.platforms.map((p) => `
        <div class="adm-card">
          <div class="adm-flex">
            <h3 class="adm-card-title" style="margin:0">${platformBadge(p.platform)}</h3>
            <span style="flex:1"></span>
            <label class="adm-toggle" title="Enable/disable ${esc(p.platform)}">
              <input type="checkbox" data-toggle data-p="${esc(p.platform)}" ${p.enabled ? "checked" : ""} ${role === "viewer" ? "disabled" : ""}>
              <span class="adm-toggle-slider"></span>
            </label>
          </div>
          <div class="adm-kv" style="margin-top:12px">
            <div class="adm-kv-row"><dt>Pages</dt><dd>${p.stats.pages}</dd></div>
            <div class="adm-kv-row"><dt>Posts</dt><dd>${p.stats.posts}</dd></div>
            <div class="adm-kv-row"><dt>Comments</dt><dd>${p.stats.comments}</dd></div>
            <div class="adm-kv-row"><dt>Leads</dt><dd>${p.stats.leads}</dd></div>
          </div>
          <div class="adm-card-title" style="margin:16px 0 10px">Actors</div>
          ${p.actors.map((a) => `
            <div class="adm-flex" style="margin-bottom:8px">
              <span class="adm-badge gray plain">${esc(a.kind)}</span>
              <input class="adm-input" data-actor data-key="${esc(a.key)}" value="${esc(a.value)}" ${role === "viewer" ? "disabled" : ""}>
              <button class="adm-btn small" data-test data-key="${esc(a.key)}" ${role === "viewer" ? "disabled" : ""}>Test</button>
            </div>
            ${a.overridden ? `<div class="adm-hint" style="margin:-4px 0 8px">override of ${esc(a.default)}</div>` : ""}`).join("")}
          <div class="adm-btn-row" style="margin-top:10px">
            <button class="adm-btn primary small" data-save-actors data-p="${esc(p.platform)}" ${role === "viewer" ? "disabled" : ""}>Save Actors</button>
          </div>
        </div>`).join("")}
    </div>`;
  $$("[data-toggle]", root).forEach((el) => {
    el.onchange = async () => {
      try {
        await api(`/api/admin/platforms/${encodeURIComponent(el.dataset.p)}/toggle`, { method: "POST" });
        toast(`${el.dataset.p} ${el.checked ? "enabled" : "disabled"}`, el.checked ? "ok" : "warn");
      } catch (err) { toast(err.message, "error"); el.checked = !el.checked; }
    };
  });
  $$("[data-test]", root).forEach((el) => {
    el.onclick = async () => {
      el.disabled = true;
      try {
        const res = await api("/api/admin/actors/test", { method: "POST", body: { key: el.dataset.key } });
        toast(res.ok ? `Actor ${res.actor_id} reachable` : `Test failed: ${res.error}`, res.ok ? "ok" : "error");
      } catch (err) { toast(err.message, "error"); }
      finally { el.disabled = false; }
    };
  });
  $$("[data-save-actors]", root).forEach((btn) => {
    btn.onclick = async () => {
      const card = btn.closest(".adm-card");
      const updates = $$("[data-actor]", card).map((el) => ({
        key: el.dataset.key, value: el.value.trim(),
      }));
      try {
        for (const u of updates) {
          if (!u.value) { toast(`Actor ${u.key} cannot be empty`, "error"); return; }
          await api(`/api/admin/platforms/${encodeURIComponent(btn.dataset.p)}/actor`,
            { method: "POST", body: { key: u.key, actor_id: u.value } });
        }
        toast("Actors updated — next scrape uses the new actor ids", "ok");
        viewPlatforms();
      } catch (err) { toast(err.message, "error"); }
    };
  });
}

/* ──────────────────────────────── PLATFORM DETAIL ─────────────────── */
async function viewPlatformDetail(platform) {
  const root = $("#view");
  const role = state.user.role;
  const [plats, jobs] = await Promise.all([
    api("/api/admin/platforms"),
    api(`/api/admin/jobs?platform=${encodeURIComponent(platform)}&limit=8`),
  ]);
  const p = plats.platforms.find((x) => x.platform === platform);
  if (!p) {
    root.innerHTML = emptyState("platforms", `Platform "${esc(platform)}" is not in the configured list.`);
    return;
  }
  root.innerHTML = `
    ${pageHead(`${p.platform[0].toUpperCase()}${p.platform.slice(1)}`, "Platform detail — live statistics, actors and recent runs.", `
      <a class="adm-btn" href="#/platforms">← All Platforms</a>`)}
    <div class="adm-grid-2">
      <div class="adm-card">
        <div class="adm-flex">
          <h3 class="adm-card-title" style="margin:0">${platformBadge(p.platform)}</h3>
          <span style="flex:1"></span>
          <label class="adm-toggle" title="Enable/disable ${esc(p.platform)}">
            <input type="checkbox" id="pToggle" ${p.enabled ? "checked" : ""} ${role === "viewer" ? "disabled" : ""}>
            <span class="adm-toggle-slider"></span>
          </label>
        </div>
        <div class="adm-kv" style="margin-top:12px">
          <div class="adm-kv-row"><dt>Status</dt><dd>${p.enabled ? `<span class="adm-badge green">enabled</span>` : `<span class="adm-badge red">disabled</span>`}</dd></div>
          <div class="adm-kv-row"><dt>Pages</dt><dd>${p.stats.pages}</dd></div>
          <div class="adm-kv-row"><dt>Posts</dt><dd>${p.stats.posts}</dd></div>
          <div class="adm-kv-row"><dt>Comments</dt><dd>${p.stats.comments}</dd></div>
          <div class="adm-kv-row"><dt>Leads</dt><dd>${p.stats.leads}</dd></div>
        </div>
      </div>
      <div class="adm-card">
        <div class="adm-card-title">Actors used by this platform</div>
        ${p.actors.map((a) => `
          <div class="adm-flex" style="margin-bottom:8px">
            <span class="adm-badge gray plain">${esc(a.kind)}</span>
            <span class="adm-code" style="flex:1">${esc(a.value)}</span>
            <span class="adm-hint">${a.overridden ? "override" : "default"}</span>
          </div>`).join("")}
        <div class="adm-hint" style="margin-top:6px">Manage actors from the Platforms page.</div>
      </div>
    </div>
    <div class="adm-card">
      <div class="adm-card-title">Recent runs on ${esc(p.platform)}</div>
      ${jobs.items.length ? `
        <div class="adm-table-wrap"><table class="adm-table">
          <thead><tr><th>Run ID</th><th>Query</th><th>Status</th><th>Phase</th><th>Created</th></tr></thead>
          <tbody>
            ${jobs.items.map((j) => `
              <tr class="adm-row-link" data-job="${esc(j.run_id)}">
                <td><span class="adm-code">${esc(j.run_id)}</span></td>
                <td><div class="adm-cell-main">${esc((j.query || "").slice(0, 60))}</div></td>
                <td>${statusBadge(j.status)}</td>
                <td>${esc(j.phase || "—")}</td>
                <td>${relativeTime(j.created_at)}</td>
              </tr>`).join("")}
          </tbody>
        </table></div>
        <div class="adm-btn-row" style="margin-top:10px">
          <a class="adm-btn" href="#/jobs?platform=${encodeURIComponent(platform)}">All ${esc(p.platform)} jobs →</a>
        </div>`
      : emptyState("jobs", `No runs on ${esc(platform)} yet.`)}
    </div>`;
  $$("[data-job]", root).forEach((row) => {
    row.onclick = () => openJobDrawer(row.dataset.job);
  });
  const toggle = $("#pToggle");
  if (toggle) toggle.onchange = async () => {
    try {
      await api(`/api/admin/platforms/${encodeURIComponent(platform)}/toggle`, { method: "POST" });
      toast(`${platform} ${toggle.checked ? "enabled" : "disabled"}`, toggle.checked ? "ok" : "warn");
      viewPlatformDetail(platform);
    } catch (err) { toast(err.message, "error"); toggle.checked = !toggle.checked; }
  };
}

/* ──────────────────────────────── PAGES / POSTS BROWSER ───────────── */
const PAGE_FILTERS = { platform: "", q: "", run: "", contact: false };
const POST_FILTERS = { platform: "", q: "", run: "" };

async function viewPages() {
  const root = $("#view");
  const f = PAGE_FILTERS;
  const hashParams = new URLSearchParams(location.hash.split("?")[1] || "");
  if (hashParams.has("run")) f.run = hashParams.get("run") || "";
  const qs = qsOf({
    platform: f.platform, q: f.q, run: f.run,
    contact: f.contact ? "true" : "",
    offset: state.filters.pagesOffset || 0, limit: 25,
  });
  const data = await api(`/api/admin/pages?${qs}`);
  root.innerHTML = `
    ${pageHead("Pages", "Every collected page from the real database.", `
      <a class="adm-btn" href="/api/admin/export/pages.csv" ${state.user.role === "viewer" ? "onclick='return false'" : ""}>${icon("exports", 14)} Export CSV</a>`)}
    <div class="adm-card">
      <div class="adm-filters">
        <input class="adm-input" id="pPlat" placeholder="Platform" list="platOpts" value="${esc(f.platform)}">
        <input class="adm-input" id="pQ" placeholder="Name / category / city…" value="${esc(f.q)}">
        <label class="adm-flex" style="gap:8px;align-items:center">
          <input type="checkbox" id="pContact" ${f.contact ? "checked" : ""}>
          <span class="adm-hint">with contact info</span>
        </label>
        <button class="adm-btn primary" id="applyPages">Filter</button>
        <button class="adm-btn" id="clearPages">Clear</button>
      </div>
      <div class="adm-table-wrap"><table class="adm-table">
        <thead><tr><th>Page</th><th>Platform</th><th>Category</th><th>City</th><th>Followers</th><th>Contact</th><th>Collected</th></tr></thead>
        <tbody>
          ${data.items.length ? data.items.map((p) => `
            <tr class="adm-row-link" data-page="${esc(p._id)}">
              <td><div class="adm-cell-main">${esc(p.page_name || "—")}</div>
                <div class="adm-cell-sub"><a href="${esc(p.facebook_url || "#")}" target="_blank" rel="noopener">open profile ↗</a></div></td>
              <td>${platformBadge(p.platform)}</td>
              <td>${esc(p.category || "—")}</td>
              <td>${esc(p.city || "—")}</td>
              <td>${(p.followers || 0).toLocaleString()}</td>
              <td>${p.phone || p.email || p.whatsapp || p.website
                ? `<div class="adm-cell-sub">${[p.phone, p.whatsapp, p.email].filter(Boolean).map((v) => esc(String(v))).join(" · ")}</div>`
                : `<span class="adm-badge gray plain">no contact</span>`}</td>
              <td>${relativeTime(p.collected_at)}</td>
            </tr>`).join("")
          : `<tr><td colspan="7">${emptyState("platforms", "No pages match these filters.")}</td></tr>`}
        </tbody>
      </table></div>
      ${pagerHtml(data.total, data.offset, data.limit, (dir) => {
        const off = data.offset + dir * data.limit;
        state.filters.pagesOffset = Math.max(0, off);
        viewPages();
      })}
    </div>`;
  const apply = () => {
    state.filters.pagesOffset = 0;
    f.platform = $("#pPlat").value.trim();
    f.q = $("#pQ").value.trim();
    f.contact = $("#pContact").checked;
    viewPages();
  };
  $("#applyPages").onclick = apply;
  $("#clearPages").onclick = () => {
    Object.assign(f, { platform: "", q: "", contact: false });
    state.filters.pagesOffset = 0;
    viewPages();
  };
  $$("[data-page]", root).forEach((row) => {
    row.onclick = () => openPageDetail(row.dataset.page);
  });
}

function openPageDetail(page) {
  openModal(`Page — ${esc(page.page_name || "Unknown")}`, `
    <div class="adm-kv">
      <dt>Name</dt><dd><div class="adm-cell-main">${esc(page.page_name || "—")}</div></dd>
      <dt>URL</dt><dd>${page.facebook_url ? `<a href="${esc(page.facebook_url)}" target="_blank" rel="noopener">${esc(page.facebook_url)}</a>` : "—"}</dd>
      <dt>Category</dt><dd>${esc(page.category || "—")}</dd>
      <dt>City / State</dt><dd>${[page.city, page.state].filter(Boolean).map(esc).join(", ") || "—"}</dd>
      <dt>Followers</dt><dd>${(page.followers || 0).toLocaleString()}</dd>
      <dt>Verified</dt><dd>${page.verified ? "✓" : "—"}</dd>
      <dt>Phone</dt><dd>${esc(page.phone || "—")}</dd>
      <dt>Email</dt><dd>${esc(page.email || "—")}</dd>
      <dt>Website</dt><dd>${page.website ? `<a href="${esc(page.website)}" target="_blank" rel="noopener">${esc(page.website)}</a>` : "—"}</dd>
      <dt>Description</dt><dd><div style="max-width:520px;font-size:13px;line-height:1.5">${esc((page.description || "").slice(0, 600))}</div></dd>
      <dt>Collected</dt><dd>${fmtTime(page.collected_at)}</dd>
    </div>`, `
    <button class="adm-btn" data-close>Close</button>`);
  $("#modalBackdrop").onclick = (e) => { if (e.target.id === "modalBackdrop") closeModal(); };
  $("[data-close]", $("#modalBox")).onclick = closeModal;
}

async function viewPosts() {
  const root = $("#view");
  const f = POST_FILTERS;
  const hashParams = new URLSearchParams(location.hash.split("?")[1] || "");
  if (hashParams.has("run")) f.run = hashParams.get("run") || "";
  const qs = qsOf({
    platform: f.platform, q: f.q, run: f.run,
    offset: state.filters.postsOffset || 0, limit: 25,
  });
  const data = await api(`/api/admin/posts?${qs}`);
  root.innerHTML = `
    ${pageHead("Posts", "Every collected post from the real database.", `
      <a class="adm-btn" href="/api/admin/export/posts.csv" ${state.user.role === "viewer" ? "onclick='return false'" : ""}>${icon("exports", 14)} Export CSV</a>`)}
    <div class="adm-card">
      <div class="adm-filters">
        <input class="adm-input" id="oPlat" placeholder="Platform" list="platOpts" value="${esc(f.platform)}">
        <input class="adm-input" id="oQ" placeholder="Caption / page…" value="${esc(f.q)}">
        <button class="adm-btn primary" id="applyPosts">Filter</button>
        <button class="adm-btn" id="clearPosts">Clear</button>
      </div>
      <div class="adm-table-wrap"><table class="adm-table">
        <thead><tr><th>Post</th><th>Platform</th><th>Page</th><th>Reactions</th><th>Comments</th><th>Shares</th><th>Published</th></tr></thead>
        <tbody>
          ${data.items.length ? data.items.map((o) => `
            <tr class="adm-row-link" data-post="${esc(o._id)}">
              <td><div class="adm-cell-sub" style="max-width:340px">${esc((o.caption || o.description || "").slice(0, 110))}</div></td>
              <td>${platformBadge(o.platform)}</td>
              <td>${esc(o.page_name || "—")}</td>
              <td>${(o.reaction_count || o.likes_count || 0).toLocaleString()}</td>
              <td>${(o.total_comment_count || 0).toLocaleString()}</td>
              <td>${(o.shares_count || 0).toLocaleString()}</td>
              <td>${o.published_date ? relativeTime(o.published_date) : "—"}</td>
            </tr>`).join("")
          : `<tr><td colspan="7">${emptyState("ci", "No posts match these filters.")}</td></tr>`}
        </tbody>
      </table></div>
      ${pagerHtml(data.total, data.offset, data.limit, (dir) => {
        const off = data.offset + dir * data.limit;
        state.filters.postsOffset = Math.max(0, off);
        viewPosts();
      })}
    </div>`;
  const apply = () => {
    state.filters.postsOffset = 0;
    f.platform = $("#oPlat").value.trim();
    f.q = $("#oQ").value.trim();
    viewPosts();
  };
  $("#applyPosts").onclick = apply;
  $("#clearPosts").onclick = () => {
    Object.assign(f, { platform: "", q: "" });
    state.filters.postsOffset = 0;
    viewPosts();
  };
  $$("[data-post]", root).forEach((row) => {
    row.onclick = () => openPostDetail(row.dataset.post);
  });
}

function openPostDetail(post) {
  const likes = post.reaction_count || post.likes_count || 0;
  const comments = post.total_comment_count || post.comments_count || 0;
  openModal(`Post — ${esc(post.page_name || "Unknown page")}`, `
    <div class="adm-kv">
      <dt>URL</dt><dd>${post.post_url ? `<a href="${esc(post.post_url)}" target="_blank" rel="noopener">open post ↗</a>` : "—"}</dd>
      <dt>Page</dt><dd>${esc(post.page_name || "—")}</dd>
      <dt>Platform</dt><dd>${platformBadge(post.platform)}</dd>
      <dt>Caption</dt><dd><div style="max-width:520px;font-size:13px;line-height:1.5;white-space:pre-wrap">${esc((post.caption || post.description || "").slice(0, 1200))}</div></dd>
      ${post.hashtags && post.hashtags.length ? `<dt>Hashtags</dt><dd>${post.hashtags.map((h) => `<span class="adm-badge gray plain">${esc(h)}</span>`).join(" ")}</dd>` : ""}
      <dt>Reactions</dt><dd>${likes.toLocaleString()}</dd>
      <dt>Comments</dt><dd>${comments.toLocaleString()}</dd>
      <dt>Shares</dt><dd>${(post.shares_count || 0).toLocaleString()}</dd>
      <dt>Published</dt><dd>${fmtTime(post.published_date)}</dd>
      <dt>Collected</dt><dd>${fmtTime(post.collected_at)}</dd>
    </div>`, `
    <button class="adm-btn" data-close>Close</button>`);
  $("#modalBackdrop").onclick = (e) => { if (e.target.id === "modalBackdrop") closeModal(); };
  $("[data-close]", $("#modalBox")).onclick = closeModal;
}

/* ──────────────────────────────── APIFY ───────────────────────────── */
async function viewApify() {
  const root = $("#view");
  // /api/admin/apify/test is a POST that performs a real Apify API probe,
  // so it must NOT run on page load. Load status only; the probe runs on
  // demand via the "Run live test" button.
  const [status, usage] = await Promise.all([
    api("/api/admin/apify"),
    api("/api/admin/usage?days=30").catch(() => null),
  ]);
  const role = state.user.role;
  const u = usage || {};
  const maxActor = Math.max(1, ...(u.actors || []).map((a) => a.runs));
  root.innerHTML = `
    ${pageHead("Apify", "Connection status, connection test and token management. Tokens are never displayed — only a masked hint.", `
      <a class="adm-btn" href="#/usage">${icon("usage", 14)} Usage & Cost</a>`)}
    <div class="adm-grid-2">
      <div class="adm-card">
        <div class="adm-card-title">Connection</div>
        <div class="adm-kv">
          <div class="adm-kv-row"><dt>Token</dt><dd>${status.token_configured ? `<span class="adm-badge green">${esc(status.token_hint)}</span>` : `<span class="adm-badge red">not configured</span>`}</dd></div>
          <div class="adm-kv-row"><dt>Source</dt><dd>${status.env_token_configured && status.token_hint ? "env override active" : status.env_token_configured ? "environment (.env)" : "—"}</dd></div>
          <div class="adm-kv-row"><dt>Last test</dt><dd>${status.last_test_at ? `${status.last_test_ok ? "✓ ok" : "✕ failed"} · ${fmtTime(new Date(status.last_test_at * 1000))}` : "never"}</dd></div>
        </div>
        <div class="adm-btn-row" style="margin-top:14px">
          <button class="adm-btn primary" id="probeBtn" ${role === "viewer" ? "disabled" : ""}>${icon("refresh", 14)} Run live test</button>
        </div>
      </div>
      <div class="adm-card">
        <div class="adm-card-title">Token</div>
        ${role === "viewer" ? `<div class="adm-note">Viewer role cannot change the token.</div>` : `
          <div class="adm-field" style="margin-bottom:12px">
            <label>New token (or edit token)</label>
            <input class="adm-input" id="tokenInput" type="password" placeholder="apify_api_…">
            <span class="adm-hint">Stored as a DB override; the .env token is the fallback. The new token is tested immediately.</span>
          </div>
          <div class="adm-btn-row">
            <button class="adm-btn primary" id="saveToken">Save & Test</button>
            ${role === "super_admin" ? `<button class="adm-btn danger" id="clearToken">Remove override</button>` : ""}
          </div>`}
      </div>
    </div>
    <div class="adm-card" id="probeResult" hidden></div>
    <div class="adm-card">
      <div class="adm-card-title">Usage this month <span class="adm-hint">(real usageUsd from run records, last 30 days)</span></div>
      ${u && (u.actors || []).length ? `
        <div class="adm-stats cols-4">
          <div class="adm-stat"><div class="adm-stat-label">Total cost</div><div class="adm-stat-value gold">$${money(u.total_cost)}</div></div>
          <div class="adm-stat"><div class="adm-stat-label">Runs with usage</div><div class="adm-stat-value">${u.runs_with_usage}</div></div>
          <div class="adm-stat"><div class="adm-stat-label">Actors used</div><div class="adm-stat-value">${u.actors.length}</div></div>
        </div>
        ${u.actors.map((a, i) => `
          <div class="adm-bar-row">
            <div class="adm-bar-top">
              <span class="adm-cell-main">${esc(a.actor)}</span>
              <span class="adm-cell-sub">${a.runs} runs · $${money(a.cost)}</span>
            </div>
            <div class="adm-bar-track"><div class="adm-bar-fill" style="width:${Math.max(2, (a.runs / maxActor) * 100).toFixed(1)}%;opacity:${(1 - i * 0.12).toFixed(2)}"></div></div>
          </div>`).join("")}`
      : `<div class="adm-note">No Apify runs with usage data in the last 30 days — costs appear here as they are reported.</div>`}
    </div>`;
  const probeBtn = $("#probeBtn");
  if (probeBtn) probeBtn.onclick = async () => {
    probeBtn.disabled = true;
    probeBtn.textContent = "Testing…";
    try {
      const result = await api("/api/admin/apify/test", { method: "POST" });
      const box = $("#probeResult");
      box.hidden = false;
      box.innerHTML = result.ok
        ? `<span class="adm-badge green">actor reachable (${esc(result.actor)})</span>`
        : `<span class="adm-badge red">${esc(result.error || "failed")}</span>`;
      toast(result.ok ? "Apify connection OK" : "Apify test failed", result.ok ? "ok" : "error");
    } catch (err) {
      toast(err.message, "error");
    } finally {
      probeBtn.disabled = false;
      probeBtn.textContent = "Run live test";
    }
  };
  if (role !== "viewer") {
    $("#saveToken").onclick = async () => {
      const token = $("#tokenInput").value.trim();
      if (token.length < 10) { toast("Token looks too short", "error"); return; }
      try {
        const res = await api("/api/admin/apify/token", { method: "POST", body: { token } });
        toast(res.test.ok ? "Token saved and verified" : `Saved, but test failed: ${res.test.error}`, res.test.ok ? "ok" : "warn");
        viewApify();
      } catch (err) { toast(err.message, "error"); }
    };
    const clearBtn = $("#clearToken");
    if (clearBtn) clearBtn.onclick = () => confirmModal(
      "Remove token override", "The DB token override will be deleted; the .env token becomes active again.",
      async () => { await api("/api/admin/apify/token", { method: "DELETE" }); toast("Override removed", "ok"); viewApify(); },
      "Remove");
  }
}

/* ──────────────────────────────── ACTORS ──────────────────────────── */
async function viewActors() {
  const root = $("#view");
  const data = await api("/api/admin/actors");
  const role = state.user.role;
  root.innerHTML = `
    ${pageHead("Actors", "Every Apify actor the scrapers call, with its current value and whether it overrides the default.", `
      <button class="adm-btn" id="refreshActors">${icon("refresh", 14)} Refresh</button>`)}
    <div class="adm-actor-grid">
      ${data.actors.map((a) => `
        <div class="adm-actor-card">
          <div class="adm-actor-top">
            <span class="adm-actor-ico">${icon("actors", 16)}</span>
            <div style="min-width:0">
              <div class="adm-actor-name">${esc(a.key)}</div>
              <div class="adm-actor-kind">${platformBadge(a.platform)}</div>
            </div>
          </div>
          <div class="adm-actor-meta">
            <span class="adm-code" style="align-self:flex-start">${esc(a.value)}</span>
            <span>${a.overridden ? `<span class="adm-badge amber">override</span> <span class="adm-hint">default: ${esc(a.default)}</span>` : `<span class="adm-badge gray plain">default</span>`}</span>
          </div>
          <div class="adm-actor-foot">
            <button class="adm-btn small" data-test data-key="${esc(a.key)}" ${role === "viewer" ? "disabled" : ""}>Test</button>
            <a class="adm-btn small ghost" href="https://apify.com/${esc(a.value)}" target="_blank" rel="noopener">${icon("external", 12)} Open in Apify</a>
          </div>
        </div>`).join("")}
    </div>`;
  $("#refreshActors").onclick = viewActors;
  $$("[data-test]", root).forEach((el) => {
    el.onclick = async () => {
      el.disabled = true;
      try {
        const res = await api("/api/admin/actors/test", { method: "POST", body: { key: el.dataset.key } });
        toast(res.ok ? `Actor ${res.actor_id} reachable` : `Test failed: ${res.error}`, res.ok ? "ok" : "error");
      } catch (err) { toast(err.message, "error"); }
      finally { el.disabled = false; }
    };
  });
}

/* ──────────────────────────────── USAGE ───────────────────────────── */
async function viewUsage() {
  const root = $("#view");
  const data = await api("/api/admin/usage?days=30");
  const maxRuns = Math.max(1, ...data.actors.map((a) => a.runs));
  root.innerHTML = `
    ${pageHead("Usage & Cost", "Apify usage aggregated from real run metadata stored on each search run (usageUsd). Nothing is estimated.", `
      <span class="adm-badge ${data.total_cost > 0 ? "gold" : "gray plain"}">$${money(data.total_cost)} total · ${data.runs_with_usage} runs with usage</span>`)}
    <div class="adm-note">${esc(data.note)}</div>
    <div class="adm-card">
      <div class="adm-card-title">Cost by actor <span class="adm-hint">(30 days)</span></div>
      ${data.actors.length ? data.actors.map((a, i) => `
        <div class="adm-bar-row">
          <div class="adm-bar-top">
            <span class="adm-cell-main">${esc(a.actor)}</span>
            <span class="adm-cell-sub">${a.runs} runs · $${money(a.cost)}</span>
          </div>
          <div class="adm-bar-track"><div class="adm-bar-fill" style="width:${Math.max(2, (a.runs / maxRuns) * 100).toFixed(1)}%;opacity:${(1 - i * 0.12).toFixed(2)}"></div></div>
        </div>`).join("") : emptyState("usage", "No Apify runs with usage data in the last 30 days.")}
      <div class="adm-table-wrap" style="margin-top:16px"><table class="adm-table">
        <thead><tr><th>Actor</th><th>Runs</th><th>Cost (USD)</th></tr></thead>
        <tbody>
          ${data.actors.map((a) => `
            <tr>
              <td><div class="adm-cell-main">${esc(a.actor)}</div></td>
              <td>${a.runs}</td>
              <td>${a.cost > 0 ? `$${money(a.cost)}` : `<span class="adm-badge gray plain">not reported</span>`}</td>
            </tr>`).join("")}
        </tbody>
      </table></div>
    </div>`;
}

/* ────────────────────────────── ENVIRONMENT ──────────────────────── */
const ENV_LOCK_COOKIE = "admin_env_unlock";

async function viewEnvironment() {
  const root = $("#view");
  const role = state.user.role;
  const lock = await api("/api/admin/env/lock-status");
  let data = null;
  if (!lock.locked) {
    try {
      data = await api("/api/admin/env");
    } catch (err) {
      if (err.status === 403) { lock.locked = true; }
      else throw err;
    }
  }
  const locked = !!lock.locked;
  const rows = data ? data.vars : [];
  const byCategory = {};
  rows.forEach((v) => {
    const g = v.group || "Other";
    (byCategory[g] = byCategory[g] || []).push(v);
  });
  root.innerHTML = `
    ${pageHead("Environment", "Runtime variables: masked secrets, overrides, sources and the section lock. Editing takes effect on the next scrape.", `
      ${role === "viewer" ? `<span class="adm-badge gray plain">viewer — read only</span>` : `
        <button class="adm-btn" id="envLockNow" ${locked ? "disabled" : ""}>${icon("lock", 14)} Lock</button>`}`)}
    <div class="adm-note ${locked ? "adm-note-warn" : ""}">${locked
      ? `<b>The environment is locked.</b> Enter the guard password to unlock and edit for ${esc(lock.unlock_minutes || "a short")} minutes.`
      : `<b>Unlocked.</b> Edits are saved to the DB override table; the .env value stays the fallback.`}</div>
    ${locked ? `
      <div class="adm-card" style="max-width:440px">
        <div class="adm-card-title">Unlock environment</div>
        <div class="adm-field"><label>Guard password</label>
          <input class="adm-input" id="envPassword" type="password" placeholder="••••••••">
        </div>
        <div class="adm-btn-row">
          <button class="adm-btn primary" id="envUnlockBtn">${icon("unlock", 14)} Unlock</button>
          <button class="adm-btn" id="envReload">${icon("refresh", 14)} Reload</button>
        </div>
        <div class="adm-hint" id="envUnlockErr"></div>
      </div>` : `
      ${Object.entries(byCategory).map(([cat, vars]) => `
        <div class="adm-card">
          <div class="adm-card-title">${esc(cat)}</div>
          <div class="adm-env-list">
            ${vars.map((v) => `
              <div class="adm-env-row" data-key="${esc(v.name)}">
                <div class="adm-env-head">
                  <div class="adm-cell-main">${esc(v.name)}</div>
                  ${v.secret ? `<span class="adm-badge gray plain">secret</span>` : ""}
                  ${v.overridden ? `<span class="adm-badge amber">override</span>` : `<span class="adm-badge gray plain">${esc(v.source || "default")}</span>`}
                  ${v.restart ? `<span class="adm-badge gold">restart needed</span>` : ""}
                </div>
                <span class="adm-code">${v.secret ? `••••${(v.masked && v.masked.length > 4 ? esc(v.masked.slice(-4)) : "")}` : esc(String(v.value ?? "—").slice(0, 60))}</span>
                <div class="adm-cell-sub">${esc(v.description || "")}</div>
                <div class="adm-env-actions">
                  ${v.secret ? `<span class="adm-hint">value never exposed</span>` : ""}
                  <button class="adm-btn small" data-edit data-key="${esc(v.name)}" data-secret="${v.secret}" ${role === "viewer" ? "disabled" : ""}>${icon("edit", 12)} Edit</button>
                  <button class="adm-btn small danger" data-reset data-key="${esc(v.name)}" ${v.overridden && role !== "viewer" ? "" : "disabled"}>Reset</button>
                </div>
              </div>`).join("")}
          </div>
        </div>`).join("")}
      <div class="adm-card">
        <div class="adm-card-title">Guard password</div>
        <p class="adm-hint">Change the password protecting this section. It is hashed server-side (SHA-256) and stored as an override.</p>
        <div class="adm-field" style="margin-bottom:12px">
          <label>New password</label>
          <input class="adm-input" id="envNewPassword" type="password" placeholder="8+ characters" ${role === "viewer" ? "disabled" : ""}>
        </div>
        ${role !== "viewer" ? `<button class="adm-btn primary" id="envSavePassword">${icon("key", 14)} Change password</button>` : ""}
      </div>`}`;

  const unlockBtn = $("#envUnlockBtn");
  if (unlockBtn) unlockBtn.onclick = async () => {
    const pw = $("#envPassword").value.trim();
    if (!pw) { $("#envUnlockErr").textContent = "Password required"; return; }
    unlockBtn.disabled = true;
    try {
      await api("/api/admin/env/unlock", { method: "POST", body: { password: pw } });
      toast("Unlocked", "ok");
      viewEnvironment();
    } catch (err) {
      $("#envUnlockErr").textContent = err.message || "Wrong password";
      toast(err.message, "error");
    } finally {
      unlockBtn.disabled = false;
    }
  };
  const reloadBtn = $("#envReload");
  if (reloadBtn) reloadBtn.onclick = () => viewEnvironment();
  if (!locked) {
    const lockBtn = $("#envLockNow");
    if (lockBtn) lockBtn.onclick = async () => {
      try {
        await api("/api/admin/env/unlock", { method: "DELETE" });
        toast("Environment locked", "ok");
        viewEnvironment();
      } catch (err) { toast(err.message, "error"); }
    };
    const savePwBtn = $("#envSavePassword");
    if (savePwBtn) savePwBtn.onclick = async () => {
      const pw = $("#envNewPassword").value.trim();
      if (pw.length < 8) { toast("Password must be at least 8 characters", "warn"); return; }
      try {
        await api("/api/admin/env/password", { method: "POST", body: { new_password: pw } });
        toast("Password changed", "ok");
        $("#envNewPassword").value = "";
      } catch (err) { toast(err.message, "error"); }
    };
    $$("[data-edit]", root).forEach((el) => el.onclick = () => {
      const box = el.closest(".adm-env-row");
      const editRow = document.createElement("div");
      editRow.className = "adm-env-edit";
      editRow.innerHTML = `
        <input class="adm-input" type="${el.dataset.secret === "true" ? "password" : "text"}" value="" placeholder="${el.dataset.secret === "true" ? "••••••••" : "new value"}">
        <div class="adm-btn-row">
          <button class="adm-btn small primary">Save</button>
          <button class="adm-btn small ghost">Cancel</button>
        </div>`;
      box.after(editRow);
      box.style.display = "none";
      const input = editRow.querySelector("input");
      editRow.querySelector("button.primary").onclick = async () => {
        const value = input.value.trim();
        if (!value) { toast("Value cannot be empty", "warn"); return; }
        try {
          await api(`/api/admin/env/${encodeURIComponent(el.dataset.key)}`, { method: "PUT", body: { value } });
          toast("Saved", "ok");
          viewEnvironment();
        } catch (err) { toast(err.message, "error"); }
      };
      editRow.querySelector("button.ghost").onclick = () => { editRow.remove(); box.style.display = ""; };
    });
    $$("[data-reset]", root).forEach((el) => el.onclick = () => {
      if (el.disabled) return;
      confirmModal("Reset variable", `Remove the DB override for ${el.dataset.key}? The .env value becomes active again.`,
        async () => { await api(`/api/admin/env/${encodeURIComponent(el.dataset.key)}`, { method: "DELETE" }); toast("Reset", "ok"); viewEnvironment(); },
        "Reset");
    });
  }
}

/* ──────────────────────────────── LIMITS ──────────────────────────── */
const LIMIT_META = {
  "limits.min_comments": { label: "Min comments", hint: "Ignore comments shorter than this many characters.", min: 0, max: 500, step: 1 },
  "limits.max_posts_default": { label: "Max posts (default)", hint: "Post URLs collected per query by default.", min: 1, max: 100, step: 1 },
  "limits.max_posts_cap": { label: "Max posts (cap)", hint: "Hard ceiling for the post limit on any query.", min: 1, max: 500, step: 1 },
  "limits.max_comments_per_post_default": { label: "Comments / post (default)", hint: "Comments collected per post by default.", min: 1, max: 500, step: 1 },
  "limits.max_comments_per_post_cap": { label: "Comments / post (cap)", hint: "Hard ceiling per post.", min: 1, max: 2000, step: 1 },
  "limits.global_max_comments": { label: "Global comment budget", hint: "Total comments collected per query.", min: 1, max: 20000, step: 10 },
};
const COST_META = {
  "cost.stop_on_limit": { label: "Stop on limit", hint: "Abort a run when the comment budget is reached instead of over-collecting." },
  "cost.warn_before_expensive": { label: "Warn before expensive runs", hint: "Show a cost warning before starting very large scrapes." },
};

async function viewLimits() {
  const root = $("#view");
  const data = await api("/api/admin/limits");
  const settings = data.settings || {};
  const usage = data.usage || {};
  const role = state.user.role;
  root.innerHTML = `
    ${pageHead("Limits", "Scrape quotas and cost protection. Saved instantly to the DB — the running scrapers respect them on the next query.", "")}
    <div class="adm-card">
      <div class="adm-card-title">Scrape limits <span class="adm-hint">(usage = documents currently stored)</span></div>
      ${Object.entries(LIMIT_META).map(([key, m]) => {
        const val = settings[key];
        const usageKey = key === "limits.global_max_comments" ? "comments" : null;
        const used = usageKey ? usage[usageKey] || 0 : null;
        return `
        <div class="adm-settings-row" data-key="${key}">
          <div class="adm-settings-text">
            <div class="adm-cell-main">${esc(m.label)}</div>
            <div class="adm-hint">${esc(m.hint)}</div>
            ${used !== null ? `<div class="adm-hint">currently stored: <b>${fmtNum(used)}</b></div>` : ""}
          </div>
          <input class="adm-input" type="number" min="${m.min}" max="${m.max}" step="${m.step}" value="${val ?? m.min}" data-role="edit" ${role === "viewer" ? "disabled" : ""}>
        </div>`;
      }).join("")}
    </div>
    <div class="adm-card">
      <div class="adm-card-title">Cost protection</div>
      ${Object.entries(COST_META).map(([key, m]) => `
        <div class="adm-toggle-row">
          <div><div class="adm-cell-main">${esc(m.label)}</div><div class="adm-hint">${esc(m.hint)}</div></div>
          <label class="adm-switch"><input type="checkbox" data-key="${key}" ${settings[key] ? "checked" : ""} ${role === "viewer" ? "disabled" : ""}><span></span></label>
        </div>`).join("")}
    </div>`;
  $$("[data-role=edit]", root).forEach((input) => {
    let timer;
    const commit = async () => {
      const key = input.closest(".adm-settings-row").dataset.key;
      const m = LIMIT_META[key];
      const val = parseInt(input.value, 10);
      if (isNaN(val) || val < m.min || val > m.max) { toast(`${m.label} out of range (${m.min}–${m.max})`, "warn"); return; }
      try {
        await api("/api/admin/limits", { method: "PUT", body: { [key]: val } });
        toast(`${m.label} → ${val}`, "ok");
      } catch (err) { toast(err.message, "error"); }
    };
    input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(commit, 600); });
  });
  $$(".adm-switch input", root).forEach((el) => el.addEventListener("change", async () => {
    try {
      await api("/api/admin/limits", { method: "PUT", body: { [el.dataset.key]: el.checked } });
      toast(`${COST_META[el.dataset.key].label} → ${el.checked ? "on" : "off"}`, "ok");
    } catch (err) { toast(err.message, "error"); el.checked = !el.checked; }
  }));
}

/* ───────────────────────────────── AI ─────────────────────────────── */
const AI_TOGGLES = {
  "ai.enabled": { label: "AI analysis", hint: "Run the Gemini stage on collected comments. When off, rules-only scoring applies." },
  "ai.rule_fallback": { label: "Rule fallback", hint: "If the model is unreachable, fall back to deterministic rules instead of failing the run." },
};
const AI_NUMBERS = {
  "ai.max_calls_per_job": { label: "Max calls per job", hint: "Gemini calls budgeted for a single search run.", min: 0, max: 10000, step: 10 },
  "ai.temperature": { label: "Temperature", hint: "Model sampling temperature (0–1).", min: 0, max: 1, step: 0.05 },
};

async function viewAI() {
  const root = $("#view");
  const data = await api("/api/admin/ai");
  const settings = data.settings || {};
  const role = state.user.role;
  root.innerHTML = `
    ${pageHead("AI & Analysis", "Gemini analysis of collected content — lead scoring, intent detection, classification. Everything below uses the real pipeline.", "", "violet")}
    <div class="adm-grid-2">
      <div class="adm-card">
        <div class="adm-card-title">Status</div>
        <div class="adm-kv">
          <div class="adm-kv-row"><dt>Model</dt><dd><span class="adm-badge violet">${esc(settings["ai.model"] || "—")}</span></dd></div>
          <div class="adm-kv-row"><dt>API key</dt><dd>${data.gemini_key_configured ? `<span class="adm-badge green">configured</span>` : `<span class="adm-badge red">missing</span>`}</dd></div>
          <div class="adm-kv-row"><dt>Analyzed (all time)</dt><dd>${fmtNum(data.counts.analyzed)}</dd></div>
          <div class="adm-kv-row"><dt>This month</dt><dd>${fmtNum(data.counts.this_month)}</dd></div>
          <div class="adm-kv-row"><dt>Leads saved</dt><dd>${fmtNum(data.counts.leads)}</dd></div>
        </div>
      </div>
      <div class="adm-card">
        <div class="adm-card-title">Settings <span class="adm-hint">(saved instantly)</span></div>
        ${Object.entries(AI_TOGGLES).map(([key, m]) => `
          <div class="adm-toggle-row">
            <div><div class="adm-cell-main">${esc(m.label)}</div><div class="adm-hint">${esc(m.hint)}</div></div>
            <label class="adm-switch"><input type="checkbox" data-setting="${key}" ${settings[key] ? "checked" : ""} ${role === "viewer" ? "disabled" : ""}><span></span></label>
          </div>`).join("")}
        ${Object.entries(AI_NUMBERS).map(([key, m]) => `
          <div class="adm-settings-row" data-key="${key}">
            <div class="adm-settings-text">
              <div class="adm-cell-main">${esc(m.label)}</div>
              <div class="adm-hint">${esc(m.hint)}</div>
            </div>
            <input class="adm-input" type="number" min="${m.min}" max="${m.max}" step="${m.step}" value="${settings[key] ?? m.min}" data-setting="${key}" ${role === "viewer" ? "disabled" : ""}>
          </div>`).join("")}
      </div>
    </div>
    <div class="adm-card">
      <div class="adm-card-title">Live test <span class="adm-hint">(runs the real analysis on your text)</span></div>
      <div class="adm-field">
        <label>Sample comment</label>
        <textarea class="adm-input" id="aiSample" rows="3">Need a website redesign for my salon, budget around 5-7k, can share more details on WhatsApp 9812345678.</textarea>
      </div>
      <div class="adm-field">
        <label>Author</label>
        <input class="adm-input" id="aiAuthor" value="Test User">
      </div>
      <div class="adm-btn-row">
        <button class="adm-btn primary" id="aiTest" ${role === "viewer" ? "disabled" : ""}>${icon("sparkles", 14)} Run live test</button>
      </div>
    </div>
    <div class="adm-card" id="aiResult" hidden></div>`;
  const testBtn = $("#aiTest");
  if (testBtn) testBtn.onclick = async () => {
    const text = $("#aiSample").value.trim();
    if (!text) { toast("Sample comment required", "warn"); return; }
    testBtn.disabled = true;
    testBtn.textContent = "Analyzing…";
    try {
      const res = await api("/api/admin/ai/test", { method: "POST", body: { text, author: $("#aiAuthor").value || "Test User" } });
      const box = $("#aiResult");
      box.hidden = false;
      box.innerHTML = `
        <div class="adm-card-title">Test result <span class="adm-badge violet">${esc(res.analyzed_by || "gemini")}</span></div>
        <div class="adm-kv">
          <div class="adm-kv-row"><dt>Lead?</dt><dd><span class="adm-badge ${res.is_useful ? "gold" : "gray plain"}">${res.is_useful ? "yes" : "no"}</span></dd></div>
          <div class="adm-kv-row"><dt>Reason</dt><dd>${esc(res.reason || "—")}</dd></div>
          <div class="adm-kv-row"><dt>Quality</dt><dd>${esc(res.lead_quality || "—")}</dd></div>
          <div class="adm-kv-row"><dt>Priority</dt><dd>${esc(res.priority || "—")}</dd></div>
          <div class="adm-kv-row"><dt>Buyer</dt><dd>${esc(res.buyer || "—")}</dd></div>
          ${res.contact ? Object.entries(res.contact).filter(([, v]) => v).map(([k, v]) => `<div class="adm-kv-row"><dt>${esc(k)}</dt><dd>${esc(String(v))}</dd></div>`).join("") : ""}
          <div class="adm-kv-row"><dt>Lead score</dt><dd><span class="adm-score-pill">${res.lead_score ?? 0}</span></dd></div>
          <div class="adm-kv-row"><dt>Signal score</dt><dd>${res.signal_score ?? 0}</dd></div>
          ${res.signals && res.signals.length ? `<div class="adm-kv-row"><dt>Signals</dt><dd>${res.signals.map((s) => `<span class="adm-badge gray plain">${esc(s)}</span>`).join(" ")}</dd></div>` : ""}
        </div>`;
      toast("AI test complete", "ok");
    } catch (err) { toast(err.message, "error"); }
    finally { testBtn.disabled = false; testBtn.textContent = "Run live test"; }
  };
  if (role !== "viewer") {
    $$(".adm-switch input", root).forEach((el) => el.addEventListener("change", async () => {
      try {
        await api("/api/admin/ai", { method: "PUT", body: { [el.dataset.setting]: el.checked } });
        toast(`${AI_TOGGLES[el.dataset.setting].label} → ${el.checked ? "on" : "off"}`, "ok");
      } catch (err) { toast(err.message, "error"); el.checked = !el.checked; }
    }));
    $$("input[data-setting]", root).forEach((input) => {
      let timer;
      const commit = async () => {
        const key = input.dataset.setting;
        const m = AI_NUMBERS[key];
        const val = parseFloat(input.value);
        if (isNaN(val) || val < m.min || val > m.max) { toast(`${m.label} out of range`, "warn"); return; }
        try {
          await api("/api/admin/ai", { method: "PUT", body: { [key]: val } });
          toast(`${m.label} → ${val}`, "ok");
        } catch (err) { toast(err.message, "error"); }
      };
      input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(commit, 600); });
    });
  }
}

/* ─────────────────────────────── SCORING ──────────────────────────── */
const SCORING_GROUPS = [
  ["Weights", [
    ["scoring.confidence_weight", { label: "Confidence", hint: "AI confidence in the analysis.", min: 0, max: 100, step: 1 }],
    ["scoring.priority_weight", { label: "Priority", hint: "Priority detected in the comment.", min: 0, max: 100, step: 1 }],
    ["scoring.quality_weight", { label: "Quality", hint: "Overall comment quality.", min: 0, max: 100, step: 1 }],
    ["scoring.contact_phone", { label: "Phone", hint: "Points when a phone number is present.", min: 0, max: 50, step: 1 }],
    ["scoring.contact_email", { label: "Email", hint: "Points when an email is present.", min: 0, max: 50, step: 1 }],
    ["scoring.spam_penalty", { label: "Spam penalty", hint: "Points subtracted for spam signals.", min: 0, max: 100, step: 1 }],
  ]],
  ["Signals", [
    ["scoring.phone", { label: "Phone signal", hint: "Signal points for a phone.", min: 0, max: 50, step: 1 }],
    ["scoring.email", { label: "Email signal", hint: "Signal points for an email.", min: 0, max: 50, step: 1 }],
    ["scoring.budget", { label: "Budget", hint: "Signal points for a stated budget.", min: 0, max: 50, step: 1 }],
    ["scoring.urgency", { label: "Urgency", hint: "Signal points for urgency.", min: 0, max: 50, step: 1 }],
    ["scoring.location", { label: "Location", hint: "Signal points for a location.", min: 0, max: 50, step: 1 }],
    ["scoring.buying_intent", { label: "Buying intent", hint: "Signal points for buying intent.", min: 0, max: 50, step: 1 }],
  ]],
  ["Thresholds", [
    ["scoring.hot_min", { label: "Hot minimum", hint: "Score at or above this is a hot lead.", min: 0, max: 100, step: 1 }],
    ["scoring.warm_min", { label: "Warm minimum", hint: "Score at or above this is a warm lead.", min: 0, max: 100, step: 1 }],
  ]],
];
const SCORING_TOGGLES = {
  "scoring.derive_quality": { label: "Derive quality", hint: "Infer quality from the signal score when no explicit quality is reported." },
};

async function viewScoring() {
  const root = $("#view");
  const data = await api("/api/admin/scoring");
  const settings = data.settings || {};
  const role = state.user.role;
  root.innerHTML = `
    ${pageHead("Lead scoring", "Weights, signals and thresholds of the deterministic score. Changes apply to the next analysis — collected leads are untouched.", "")}
    ${SCORING_GROUPS.map(([group, keys]) => `
      <div class="adm-card">
        <div class="adm-card-title">${esc(group)}</div>
        ${keys.map(([key, m]) => `
          <div class="adm-settings-row" data-key="${key}">
            <div class="adm-settings-text">
              <div class="adm-cell-main">${esc(m.label)}</div>
              <div class="adm-hint">${esc(m.hint)}</div>
            </div>
            <input class="adm-input" type="number" min="${m.min}" max="${m.max}" step="${m.step}" value="${settings[key] ?? m.min}" data-setting="${key}" ${role === "viewer" ? "disabled" : ""}>
          </div>`).join("")}
      </div>`).join("")}
    <div class="adm-card">
      <div class="adm-card-title">Behavior</div>
      ${Object.entries(SCORING_TOGGLES).map(([key, m]) => `
        <div class="adm-toggle-row">
          <div><div class="adm-cell-main">${esc(m.label)}</div><div class="adm-hint">${esc(m.hint)}</div></div>
          <label class="adm-switch"><input type="checkbox" data-setting="${key}" ${settings[key] ? "checked" : ""} ${role === "viewer" ? "disabled" : ""}><span></span></label>
        </div>`).join("")}
    </div>`;
  if (role !== "viewer") {
    const all = [...SCORING_GROUPS.flatMap(([, keys]) => keys), ...Object.entries(SCORING_TOGGLES)];
    const metaOf = (key) => Object.fromEntries(all)[key];
    $$("input[data-setting]", root).forEach((input) => {
      let timer;
      const commit = async () => {
        const key = input.dataset.setting;
        const m = metaOf(key);
        const val = parseFloat(input.value);
        if (isNaN(val) || val < m.min || val > m.max) { toast(`${m.label} out of range`, "warn"); return; }
        try {
          await api("/api/admin/scoring", { method: "PUT", body: { [key]: val } });
          toast(`${m.label} → ${val}`, "ok");
        } catch (err) { toast(err.message, "error"); }
      };
      input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(commit, 600); });
    });
    $$(".adm-switch input", root).forEach((el) => el.addEventListener("change", async () => {
      try {
        await api("/api/admin/scoring", { method: "PUT", body: { [el.dataset.setting]: el.checked } });
        toast(`${metaOf(el.dataset.setting).label} → ${el.checked ? "on" : "off"}`, "ok");
      } catch (err) { toast(err.message, "error"); el.checked = !el.checked; }
    }));
  }
}

/* ───────────────────────────────── CI ─────────────────────────────── */
const CI_TOGGLES = {
  "ci.detect_phone": { label: "Phone", hint: "Extract phone numbers." },
  "ci.detect_email": { label: "Email", hint: "Extract email addresses." },
  "ci.detect_budget": { label: "Budget", hint: "Detect budget mentions." },
  "ci.detect_location": { label: "Location", hint: "Detect location mentions." },
  "ci.detect_urgency": { label: "Urgency", hint: "Detect urgency." },
  "ci.detect_buying_intent": { label: "Buying intent", hint: "Detect buying intent." },
  "ci.detect_selling_intent": { label: "Selling intent", hint: "Detect selling intent (off by default to preserve lead filtering)." },
  "ci.ignore_emoji_only": { label: "Ignore emoji-only", hint: "Skip comments that are only emoji." },
  "ci.ignore_spam": { label: "Ignore spam", hint: "Skip spam-looking comments." },
  "ci.ignore_low_value": { label: "Ignore low value", hint: "Skip low-value comments." },
};

async function viewCI() {
  const root = $("#view");
  const role = state.user.role;
  const hashParams = new URLSearchParams(location.hash.split("?")[1] || "");
  const qs = new URLSearchParams({
    platform: hashParams.get("platform") || "",
    intent: hashParams.get("intent") || "",
    quality: hashParams.get("quality") || "",
    limit: 40,
  });
  const [data, ci] = await Promise.all([
    api(`/api/admin/comments?${qs}`),
    api("/api/admin/comment-intelligence"),
  ]);
  const items = data.items || [];
  const summary = data.summary || {};
  const settings = ci.settings || {};
  const badge = (text, cls) => text ? `<span class="adm-badge ${cls || "gray plain"}">${esc(text)}</span>` : "";
  root.innerHTML = `
    ${pageHead("Contact Intelligence", "What the engine decided about real comments — intent, quality, confidence, contact details.", `
      <span class="adm-badge violet">${fmtNum(summary.analyzed ?? 0)} analyzed</span>
      <span class="adm-badge gold">${fmtNum(summary.leads ?? 0)} leads</span>`)}
    <div class="adm-grid-2">
      <div class="adm-card">
        <div class="adm-card-title">Pipeline <span class="adm-hint">(all-time)</span></div>
        <div class="adm-stats cols-3">
          <div class="adm-stat"><div class="adm-stat-label">Total</div><div class="adm-stat-value">${fmtNum(summary.total ?? 0)}</div></div>
          <div class="adm-stat"><div class="adm-stat-label">Analyzed</div><div class="adm-stat-value">${fmtNum(summary.analyzed ?? 0)}</div></div>
          <div class="adm-stat"><div class="adm-stat-label">Leads</div><div class="adm-stat-value gold">${fmtNum(summary.leads ?? 0)}</div></div>
          <div class="adm-stat"><div class="adm-stat-label">Contacts</div><div class="adm-stat-value">${fmtNum(summary.contacts ?? 0)}</div></div>
          <div class="adm-stat"><div class="adm-stat-label">High value</div><div class="adm-stat-value">${fmtNum(summary.high_value ?? 0)}</div></div>
          <div class="adm-stat"><div class="adm-stat-label">Avg confidence</div><div class="adm-stat-value">${summary.avg_confidence !== null && summary.avg_confidence !== undefined ? (summary.avg_confidence * 100).toFixed(0) + "%" : "—"}</div></div>
        </div>
        ${summary.intents && Object.keys(summary.intents).length ? `
          <div class="adm-bar-list" style="margin-top:12px">
            ${Object.entries(summary.intents).sort((a, b) => b[1] - a[1]).map(([intent, count]) => `
              <div class="adm-bar-row">
                <div class="adm-bar-top"><span class="adm-cell-main">${badge(intent, "violet")}</span><span class="adm-cell-sub">${fmtNum(count)}</span></div>
                <div class="adm-bar-track"><div class="adm-bar-fill" style="width:${Math.max(2, (count / Math.max(1, ...Object.values(summary.intents))) * 100).toFixed(1)}%"></div></div>
              </div>`).join("")}
          </div>` : ""}
      </div>
      <div class="adm-card">
        <div class="adm-card-title">Detection settings <span class="adm-hint">(instant save)</span></div>
        ${Object.entries(CI_TOGGLES).map(([key, m]) => `
          <div class="adm-toggle-row">
            <div><div class="adm-cell-main">${esc(m.label)}</div><div class="adm-hint">${esc(m.hint)}</div></div>
            <label class="adm-switch"><input type="checkbox" data-setting="${key}" ${settings[key] ? "checked" : ""} ${role === "viewer" ? "disabled" : ""}><span></span></label>
          </div>`).join("")}
        <div class="adm-settings-row" data-key="ci.min_lead_score">
          <div class="adm-settings-text">
            <div class="adm-cell-main">Minimum lead score</div>
            <div class="adm-hint">Comments below this score are not treated as leads.</div>
          </div>
          <input class="adm-input" type="number" min="0" max="100" step="1" value="${settings["ci.min_lead_score"] ?? 0}" data-setting="ci.min_lead_score" ${role === "viewer" ? "disabled" : ""}>
        </div>
      </div>
    </div>
    <div class="adm-card">
      <div class="adm-card-title">Feed <span class="adm-hint">(${items.length} shown · click a row for the lead detail)</span></div>
      <div class="adm-feed-filters" id="ciFilters">
        <select class="adm-input" data-filter="platform">
          <option value="">all platforms</option>
          ${["facebook", "instagram", "linkedin", "youtube"].map((p) => `<option value="${p}" ${hashParams.get("platform") === p ? "selected" : ""}>${p}</option>`).join("")}
        </select>
        <select class="adm-input" data-filter="intent">
          <option value="">all intents</option>
          ${Object.keys(summary.intents || {}).map((i) => `<option value="${esc(i)}" ${hashParams.get("intent") === i ? "selected" : ""}>${esc(i)}</option>`).join("")}
        </select>
        <select class="adm-input" data-filter="quality">
          <option value="">all qualities</option>
          ${["high", "medium", "low"].map((q) => `<option value="${q}" ${hashParams.get("quality") === q ? "selected" : ""}>${q}</option>`).join("")}
        </select>
      </div>
      <div class="adm-feed">
        ${items.length ? items.map((it) => `
          <div class="adm-feed-item" data-nav="leads/details:${esc(it._id)}">
            <div class="adm-feed-head">
              <span class="adm-cell-main">${esc(it.commenter_name || "—")}</span>
              ${badge(it.platform)}
              ${it.is_lead ? `<span class="adm-badge gold">lead</span>` : ""}
              <span class="adm-feed-time">${it.analyzed_at ? fmtTime(new Date(it.analyzed_at)) : ""}</span>
            </div>
            <div class="adm-feed-body">
              ${badge(it.intent, "violet")}
              ${badge(it.lead_quality, "gray plain")}
              <span class="adm-score-pill">${it.lead_score ?? 0}</span>
              ${it.confidence !== undefined && it.confidence !== null ? `<span class="adm-cell-sub">conf ${(it.confidence * 100).toFixed(0)}%</span>` : ""}
            </div>
            <div class="adm-feed-reason">${esc(String(it.comment_text || "").slice(0, 240))}</div>
            ${(it.phone || it.email) ? `<div class="adm-feed-contacts"><span class="adm-badge green plain">✆ ${esc(it.phone || "")}</span>${it.email ? `<span class="adm-badge green plain">@ ${esc(it.email)}</span>` : ""}</div>` : ""}
          </div>`).join("") : emptyState("ci", "No comments match these filters.")}
      </div>
    </div>`;
  $$("#ciFilters select", root).forEach((sel) => sel.addEventListener("change", () => {
    const params = new URLSearchParams();
    $$("#ciFilters select", root).forEach((s) => { if (s.value) params.set(s.dataset.filter, s.value); });
    navigate(`#/ci${params.toString() ? "?" + params.toString() : ""}`);
  }));
  bindNav(root);
  if (role !== "viewer") {
    $$(".adm-switch input", root).forEach((el) => el.addEventListener("change", async () => {
      try {
        await api("/api/admin/comment-intelligence", { method: "PUT", body: { [el.dataset.setting]: el.checked } });
        toast(`${CI_TOGGLES[el.dataset.setting].label} → ${el.checked ? "on" : "off"}`, "ok");
      } catch (err) { toast(err.message, "error"); el.checked = !el.checked; }
    }));
    const minInput = root.querySelector('[data-setting="ci.min_lead_score"]');
    if (minInput) {
      let timer;
      minInput.addEventListener("input", () => {
        clearTimeout(timer);
        timer = setTimeout(async () => {
          const val = parseInt(minInput.value, 10);
          if (isNaN(val) || val < 0 || val > 100) { toast("Range 0–100", "warn"); return; }
          try {
            await api("/api/admin/comment-intelligence", { method: "PUT", body: { "ci.min_lead_score": val } });
            toast(`Minimum lead score → ${val}`, "ok");
          } catch (err) { toast(err.message, "error"); }
        }, 600);
      });
    }
  }
}

/* ─────────────────────────────── DATABASE ────────────────────────── */
async function viewDatabase() {
  const root = $("#view");
  const data = await api("/api/admin/database");
  const stats = data.db_stats || {};
  const collections = data.collections || [];
  const maxBytes = Math.max(1, ...collections.map((c) => c.size || 0));
  const ok = !stats.error;
  root.innerHTML = `
    ${pageHead("Database", "MongoDB state behind the scrapers — documents, indexes and size. Nothing here is deletable from the UI.", "")}
    <div class="adm-card">
      <div class="adm-card-title">Connection <span class="adm-hint">(dbStats command)</span></div>
      ${stats.error ? `<div class="adm-note adm-note-warn">${esc(stats.error)}</div>` : `
      <div class="adm-kv">
        <div class="adm-kv-row"><dt>Status</dt><dd><span class="adm-status-word ${ok ? "ok" : "error"}">${ok ? "connected" : "error"}</span></dd></div>
        <div class="adm-kv-row"><dt>Database</dt><dd><span class="adm-code">${esc(stats.db || "—")}</span></dd></div>
        <div class="adm-kv-row"><dt>Collections</dt><dd>${fmtNum(stats.collections ?? collections.length)}</dd></div>
        <div class="adm-kv-row"><dt>Documents</dt><dd>${fmtNum(stats.objects)}</dd></div>
        <div class="adm-kv-row"><dt>Data size</dt><dd>${fmtBytes(stats.dataSize)}</dd></div>
        <div class="adm-kv-row"><dt>Storage size</dt><dd>${fmtBytes(stats.storageSize)}</dd></div>
        <div class="adm-kv-row"><dt>Indexes</dt><dd>${fmtNum(stats.indexes)} (${fmtBytes(stats.indexSize)})</dd></div>
        <div class="adm-kv-row"><dt>Avg object</dt><dd>${fmtBytes(stats.avgObjSize)}</dd></div>
      </div>`}
    </div>
    <div class="adm-card">
      <div class="adm-card-title">Collections</div>
      <div class="adm-table-wrap"><table class="adm-table">
        <thead><tr><th>Collection</th><th>Documents</th><th>Size</th><th>Indexes</th></tr></thead>
        <tbody>
          ${collections.map((c) => `
            <tr>
              <td><div class="adm-cell-main">${esc(c.name)}</div></td>
              <td>${fmtNum(c.documents)}</td>
              <td>
                <div class="adm-bar-row" style="min-width:160px">
                  <div class="adm-bar-top" style="gap:8px"><span class="adm-cell-sub">${fmtBytes(c.size)}</span></div>
                  <div class="adm-bar-track" style="height:6px"><div class="adm-bar-fill" style="width:${Math.max(2, ((c.size || 0) / maxBytes) * 100).toFixed(1)}%"></div></div>
                </div>
              </td>
              <td>${(c.indexes || []).length}</td>
            </tr>`).join("")}
        </tbody>
      </table></div>
    </div>`;
}

/* ──────────────────────────────── LOGS ────────────────────────────── */
async function viewLogs() {
  const root = $("#view");
  const hashParams = new URLSearchParams(location.hash.split("?")[1] || "");
  const q = hashParams.get("q") || "";
  const lvl = hashParams.get("level") || "";
  const qs = new URLSearchParams({ lines: 250, q, level: lvl });
  const data = await api(`/api/admin/logs?${qs}`);
  const entries = data.lines || [];
  const levelClass = (lv) => ({ ERROR: "error", WARNING: "warn", INFO: "info", DEBUG: "debug" }[lv] || "info");
  const lineHtml = (l) => `
    <div class="adm-log-line" data-level="${esc(l.level || "")}">
      <span class="adm-log-level ${levelClass(l.level)}">${esc(l.level || "—")}</span>
      <span class="adm-log-time">${esc(String(l).slice(0, 23))}</span>
      <span class="adm-log-msg">${esc(String(l).slice(24))}</span>
    </div>`;
  root.innerHTML = `
    ${pageHead("Logs", "Raw application log lines (UTC). Error and warning lines are highlighted; every line stays copyable.", `
      <label class="adm-check"><input type="checkbox" id="logAuto" checked> autoscroll</label>
      <button class="adm-btn" id="logRefresh">${icon("refresh", 14)} Refresh</button>`)}
    <div class="adm-log-filter">
      <input class="adm-input" id="logQ" placeholder="Filter text…" value="${esc(q)}">
      <select class="adm-input" id="logLevel">
        <option value="">all levels</option>
        ${["ERROR", "WARNING", "INFO", "DEBUG"].map((lv) => `<option value="${lv}" ${lvl === lv ? "selected" : ""}>${lv}</option>`).join("")}
      </select>
      <button class="adm-btn primary" id="logApply">Apply</button>
    </div>
    <div class="adm-terminal" id="logBox">
      ${entries.length ? entries.map((l) => lineHtml(l)).join("") : `<div class="adm-empty">No log lines match.</div>`}
    </div>`;
  const box = $("#logBox");
  const load = async () => {
    const params = new URLSearchParams({ lines: 250, q: $("#logQ").value.trim(), level: $("#logLevel").value });
    try {
      const d = await api(`/api/admin/logs?${params}`);
      box.innerHTML = (d.lines || []).length ? d.lines.map((l) => lineHtml(l)).join("") : `<div class="adm-empty">No log lines match.</div>`;
      if ($("#logAuto").checked) box.scrollTop = box.scrollHeight;
    } catch (err) { toast(err.message, "error"); }
  };
  $("#logRefresh").onclick = load;
  $("#logApply").onclick = () => {
    navigate(`#/logs?q=${encodeURIComponent($("#logQ").value.trim())}&level=${encodeURIComponent($("#logLevel").value)}`);
  };
  $("#logQ").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#logApply").onclick(); });
  if ($("#logAuto").checked) box.scrollTop = box.scrollHeight;
}

/* ─────────────────────────────── EXPORTS ──────────────────────────── */
const EXPORT_SCOPES = [
  { scope: "jobs", label: "Jobs", hint: "Search runs: run id, query, platform, status, phases and timestamps." },
  { scope: "pages", label: "Pages", hint: "Collected pages, sorted by followers.", platform: true },
  { scope: "posts", label: "Posts", hint: "Collected posts, sorted by publish date.", platform: true },
  { scope: "leads", label: "Leads", hint: "AI-analyzed comments that are leads, sorted by score. Includes phone/email when extracted." },
];

async function viewExports() {
  const root = $("#view");
  const role = state.user.role;
  root.innerHTML = `
    ${pageHead("Exports", "Live CSV downloads generated from the current database on every request. Nothing is stored or queued.", "")}
    <div class="adm-grid-2">
      ${EXPORT_SCOPES.map((e) => `
        <div class="adm-card">
          <div class="adm-card-title">${esc(e.label)} <span class="adm-hint">(.csv)</span></div>
          <p class="adm-hint">${esc(e.hint)}</p>
          <div class="adm-btn-row" style="margin-top:10px">
            <a class="adm-btn primary" href="/api/admin/export/${e.scope}.csv" ${role === "viewer" ? "onclick='return false'" : ""}>${icon("exports", 14)} Download ${esc(e.label)} CSV</a>
          </div>
        </div>`).join("")}
      <div class="adm-card">
        <div class="adm-card-title">Application log</div>
        <p class="adm-hint">The raw app.log file (plain text).</p>
        <div class="adm-btn-row" style="margin-top:10px">
          <a class="adm-btn" href="/api/admin/logs/download" ${role === "viewer" ? "onclick='return false'" : ""}>${icon("logs", 14)} Download app.log</a>
        </div>
      </div>
    </div>`;
}

/* ──────────────────────────────── USERS ───────────────────────────── */
async function viewUsers() {
  const root = $("#view");
  const data = await api("/api/admin/users");
  const users = data.users || [];
  const role = state.user.role;
  const roleBadge = (r) => r === "super_admin" ? `<span class="adm-badge gold">super admin</span>` : r === "manager" ? `<span class="adm-badge green">manager</span>` : `<span class="adm-badge gray plain">${esc(r)}</span>`;
  root.innerHTML = `
    ${pageHead("Users", "Admin panel accounts and their roles.", `
      <button class="adm-btn primary" id="addUser" ${role === "super_admin" ? "" : "disabled"}>${icon("plus", 14)} Create user</button>`)}
    <div class="adm-card">
      <div class="adm-table-wrap"><table class="adm-table">
        <thead><tr><th>User</th><th>Email</th><th>Role</th><th>Status</th><th></th></tr></thead>
        <tbody>
          ${users.map((u) => `
            <tr>
              <td><span class="adm-avatar">${esc(initials(u.name))}</span> <span class="adm-cell-main">${esc(u.name)}</span> ${u.env_account ? `<span class="adm-badge gray plain">env</span>` : ""}</td>
              <td>${esc(u.email)}</td>
              <td>${roleBadge(u.role)}</td>
              <td><span class="adm-badge ${u.enabled ? "green" : "gray plain"}">${u.enabled ? "active" : "disabled"}</span></td>
              <td class="adm-cell-actions">
                ${u.email !== state.user.email ? (role === "super_admin" ? `
                  <button class="adm-btn small ghost" data-edit data-id="${esc(u._id)}" data-name="${esc(u.name)}" data-email="${esc(u.email)}" data-role="${esc(u.role)}" data-enabled="${u.enabled}">${icon("edit", 12)} Edit</button>
                  <button class="adm-btn small danger" data-delete data-id="${esc(u._id)}" data-email="${esc(u.email)}">Delete</button>` : "") : `<span class="adm-badge gray plain">you</span>`}
              </td>
            </tr>`).join("")}
        </tbody>
      </table></div>
    </div>`;
  const openUserModal = (u) => openModal(
    `<div class="adm-card-title">${u ? "Edit user" : "Create user"}</div>
     <div class="adm-field"><label>Name</label><input class="adm-input" id="userName" value="${u ? esc(u.name) : ""}"></div>
     <div class="adm-field"><label>Email</label><input class="adm-input" id="userEmail" value="${u ? esc(u.email) : ""}" ${u ? "readonly" : ""}></div>
     <div class="adm-field"><label>Role</label>
       <select class="adm-input" id="userRole">
         <option value="viewer" ${u && u.role === "viewer" ? "selected" : ""}>viewer</option>
         <option value="manager" ${u && u.role === "manager" ? "selected" : ""}>manager</option>
         <option value="super_admin" ${u && u.role === "super_admin" ? "selected" : ""}>super_admin</option>
       </select>
     </div>
     ${u ? `
       <label class="adm-check"><input type="checkbox" id="userEnabled" ${u.enabled ? "checked" : ""}> enabled</label>
       <div class="adm-field" style="margin-top:10px"><label>New password <span class="adm-hint">(optional)</span></label><input class="adm-input" id="userPassword" type="password" placeholder="8+ characters"></div>` : `
       <div class="adm-field"><label>Password</label><input class="adm-input" id="userPassword" type="password" placeholder="8+ characters"></div>`}`,
    [
      { label: "Cancel", cls: "", close: true },
      { label: u ? "Save" : "Create", cls: "primary", action: async (box) => {
        const name = box.querySelector("#userName").value;
        const roleVal = box.querySelector("#userRole").value;
        const password = box.querySelector("#userPassword").value;
        try {
          if (u) {
            const body = { name, role: roleVal, enabled: box.querySelector("#userEnabled").checked };
            if (password) body.password = password;
            await api(`/api/admin/users/${encodeURIComponent(u._id)}`, { method: "PATCH", body });
            toast("User updated", "ok");
          } else {
            if (password.length < 8) { toast("Password must be at least 8 characters", "warn"); return; }
            await api("/api/admin/users", { method: "POST", body: { name, email: box.querySelector("#userEmail").value, role: roleVal, password } });
            toast("User created", "ok");
          }
          closeModal();
          viewUsers();
        } catch (err) { toast(err.message, "error"); }
      } },
    ]);
  $("#addUser").onclick = () => openUserModal(null);
  $$("[data-edit]", root).forEach((el) => el.onclick = () => openUserModal({ _id: el.dataset.id, name: el.dataset.name, email: el.dataset.email, role: el.dataset.role, enabled: el.dataset.enabled === "true" }));
  $$("[data-delete]", root).forEach((el) => el.onclick = () => confirmModal(
    "Delete user", `${el.dataset.email} will lose access immediately.`,
    async () => { await api(`/api/admin/users/${encodeURIComponent(el.dataset.id)}`, { method: "DELETE" }); toast("User deleted", "ok"); viewUsers(); },
    "Delete"));
}

/* ─────────────────────────────── SECURITY ────────────────────────── */
const SECURITY_TOGGLES = {
  "security.login_protection": { label: "Login protection", hint: "Rate-limit and lock out failing logins." },
  "security.audit_logging": { label: "Audit logging", hint: "Record every admin action to the audit log." },
};

async function viewSecurity() {
  const root = $("#view");
  const data = await api("/api/admin/security");
  const settings = data.settings || {};
  const role = state.user.role;
  const isSuper = role === "super_admin";
  root.innerHTML = `
    ${pageHead("Security", "Session lifetime, login protection and account controls.", `
      ${isSuper ? `<button class="adm-btn danger" id="revokeSessions">${icon("refresh", 14)} Revoke all sessions</button>` : ""}`)}
    <div class="adm-card">
      <div class="adm-card-title">Session & authentication</div>
      <div class="adm-settings-row" data-key="security.session_timeout_hours">
        <div class="adm-settings-text">
          <div class="adm-cell-main">Session timeout (hours)</div>
          <div class="adm-hint">Idle sessions are invalidated after this many hours. Env default: ${esc(data.session_timeout_hours_env ?? "—")}h.</div>
        </div>
        <input class="adm-input" type="number" min="1" max="720" step="1" value="${settings["security.session_timeout_hours"] ?? 24}" data-setting="security.session_timeout_hours" ${role === "viewer" ? "disabled" : ""}>
      </div>
      ${Object.entries(SECURITY_TOGGLES).map(([key, m]) => `
        <div class="adm-toggle-row">
          <div><div class="adm-cell-main">${esc(m.label)}</div><div class="adm-hint">${esc(m.hint)}</div></div>
          <label class="adm-switch"><input type="checkbox" data-setting="${key}" ${settings[key] ? "checked" : ""} ${role === "viewer" ? "disabled" : ""}><span></span></label>
        </div>`).join("")}
      <div class="adm-kv" style="margin-top:14px">
        <div class="adm-kv-row"><dt>Login protection</dt><dd><span class="adm-badge ${data.login_protection_active ? "green" : "amber"}">${data.login_protection_active ? "active" : "disabled"}</span></dd></div>
        <div class="adm-kv-row"><dt>Session epoch</dt><dd><span class="adm-code">${esc(String(settings["security.session_epoch"] ?? 0))}</span> <span class="adm-hint">(bumped when sessions are revoked)</span></dd></div>
      </div>
    </div>
    <div class="adm-card">
      <div class="adm-card-title">Account</div>
      <div class="adm-kv">
        <div class="adm-kv-row"><dt>Signed in as</dt><dd>${esc(data.me.email)} <span class="adm-badge ${data.me.role === "super_admin" ? "gold" : "green"}">${esc(data.me.role)}</span></dd></div>
      </div>
      <div class="adm-field" style="margin-bottom:12px;max-width:420px">
        <label>New password for your account</label>
        <input class="adm-input" id="secPassword" type="password" placeholder="8+ characters" ${isSuper ? "" : "disabled"}>
      </div>
      <button class="adm-btn primary" id="changePassword" ${isSuper ? "" : "disabled"}>${icon("key", 14)} Change password</button>
    </div>`;
  if (role !== "viewer") {
    $$(".adm-switch input", root).forEach((el) => el.addEventListener("change", async () => {
      try {
        await api("/api/admin/security", { method: "PUT", body: { [el.dataset.setting]: el.checked } });
        toast(`${SECURITY_TOGGLES[el.dataset.setting].label} → ${el.checked ? "on" : "off"}`, "ok");
      } catch (err) { toast(err.message, "error"); el.checked = !el.checked; }
    }));
    const hoursInput = root.querySelector('[data-setting="security.session_timeout_hours"]');
    if (hoursInput) {
      let timer;
      hoursInput.addEventListener("input", () => {
        clearTimeout(timer);
        timer = setTimeout(async () => {
          const val = parseInt(hoursInput.value, 10);
          if (isNaN(val) || val < 1 || val > 720) { toast("Range 1–720 hours", "warn"); return; }
          try {
            await api("/api/admin/security", { method: "PUT", body: { "security.session_timeout_hours": val } });
            toast(`Session timeout → ${val}h`, "ok");
          } catch (err) { toast(err.message, "error"); }
        }, 600);
      });
    }
  }
  if (isSuper) {
    $("#revokeSessions").onclick = () => confirmModal(
      "Revoke all sessions", "Every admin cookie becomes invalid. You will need to sign in again.",
      async () => { await api("/api/admin/security/revoke-sessions", { method: "POST" }); toast("All sessions revoked", "ok"); },
      "Revoke");
    $("#changePassword").onclick = async () => {
      const pw = $("#secPassword").value.trim();
      if (pw.length < 8) { toast("Password must be at least 8 characters", "warn"); return; }
      try {
        await api("/api/admin/security/change-password", { method: "POST", body: { password: pw } });
        toast("Password updated — sign in again with the new password", "ok");
        $("#secPassword").value = "";
      } catch (err) { toast(err.message, "error"); }
    };
  }
}

/* ─────────────────────────────── FEATURES ─────────────────────────── */
const FEATURE_META = {
  "features.url_search.enabled": { label: "URL search", hint: "Allow searching by direct URL in the user app." },
  "features.exports.enabled": { label: "Exports", hint: "Allow CSV export endpoints." },
};
const PLATFORM_META = {
  facebook: "Facebook",
  instagram: "Instagram",
  linkedin: "LinkedIn",
  youtube: "YouTube",
};

async function viewFeatures() {
  const root = $("#view");
  const data = await api("/api/admin/features");
  const settings = data.settings || {};
  const role = state.user.role;
  const platformKeys = Object.keys(settings).filter((k) => k.startsWith("platform."));
  root.innerHTML = `
    ${pageHead("Features", "Feature switches and platform availability. Toggling is instant and persisted.", "")}
    <div class="adm-card">
      <div class="adm-card-title">Features</div>
      ${Object.entries(FEATURE_META).map(([key, m]) => `
        <div class="adm-toggle-row">
          <div><div class="adm-cell-main">${esc(m.label)}</div><div class="adm-hint">${esc(m.hint)}</div></div>
          <label class="adm-switch"><input type="checkbox" data-setting="${key}" ${settings[key] ? "checked" : ""} ${role === "viewer" ? "disabled" : ""}><span></span></label>
        </div>`).join("")}
    </div>
    <div class="adm-card">
      <div class="adm-card-title">Platforms</div>
      ${platformKeys.map((key) => {
        const platform = key.split(".")[1];
        const name = PLATFORM_META[platform] || platform;
        return `
        <div class="adm-toggle-row">
          <div><div class="adm-cell-main">${esc(name)}</div><div class="adm-hint">Scraping on ${esc(platform)} is ${settings[key] ? "enabled" : "disabled"}.</div></div>
          <label class="adm-switch"><input type="checkbox" data-setting="${key}" ${settings[key] ? "checked" : ""} ${role === "viewer" ? "disabled" : ""}><span></span></label>
        </div>`;
      }).join("")}
    </div>`;
  if (role !== "viewer") {
    $$(".adm-switch input", root).forEach((el) => el.addEventListener("change", async () => {
      try {
        await api("/api/admin/features", { method: "PUT", body: { [el.dataset.setting]: el.checked } });
        toast(`${el.dataset.setting} → ${el.checked ? "on" : "off"}`, "ok");
      } catch (err) { toast(err.message, "error"); el.checked = !el.checked; }
    }));
  }
}

/* ────────────────────────────── MAINTENANCE ───────────────────────── */
async function viewMaintenance() {
  const root = $("#view");
  const data = await api("/api/admin/maintenance");
  const role = state.user.role;
  root.innerHTML = `
    ${pageHead("Maintenance", "Planned downtime control. The notice is shown in the user app while maintenance is active.", "")}
    <div class="adm-card">
      <div class="adm-card-title">Maintenance mode</div>
      <div class="adm-toggle-row">
        <div>
          <div class="adm-cell-main">Maintenance active</div>
          <div class="adm-hint">${data.enabled ? "Search requests are rejected and users see the notice." : "Search and login behave normally."}</div>
        </div>
        <label class="adm-switch"><input type="checkbox" id="maintenanceToggle" ${data.enabled ? "checked" : ""} ${role === "viewer" ? "disabled" : ""}><span></span></label>
      </div>
      ${data.enabled ? `
        <div class="adm-note adm-note-warn" style="margin-top:12px">${esc(data.message || "Maintenance mode is on.")}</div>
        <div class="adm-field" style="margin-top:12px"><label>Notice</label><input class="adm-input" id="maintenanceMessage" value="${esc(data.message || "")}"><span class="adm-hint">Shown to users while maintenance is active.</span></div>
        <button class="adm-btn primary" id="saveMessage" ${role === "viewer" ? "disabled" : ""}>Save notice</button>` : ""}
    </div>`;
  if (role !== "viewer") {
    $("#maintenanceToggle").addEventListener("change", async () => {
      try {
        await api("/api/admin/maintenance", { method: "POST", body: { enabled: $("#maintenanceToggle").checked } });
        toast("Maintenance " + ($("#maintenanceToggle").checked ? "enabled" : "disabled"), "ok");
        viewMaintenance();
      } catch (err) { toast(err.message, "error"); }
    });
    const saveBtn = $("#saveMessage");
    if (saveBtn) saveBtn.onclick = async () => {
      try {
        await api("/api/admin/maintenance", { method: "POST", body: { enabled: true, message: $("#maintenanceMessage").value } });
        toast("Notice saved", "ok");
      } catch (err) { toast(err.message, "error"); }
    };
  }
}

/* ─────────────────────────────── HEALTH ───────────────────────────── */
async function viewHealth() {
  const root = $("#view");
  const data = await api("/api/admin/health");
  const checks = data.checks || {};
  const entries = Object.entries(checks);
  const isOk = (c) => c.ok !== undefined ? c.ok : true;
  const allOk = entries.filter(([, c]) => c.ok !== undefined).every(([, c]) => c.ok);
  const checkedAt = data.checked_at ? new Date(data.checked_at * 1000) : new Date();
  root.innerHTML = `
    ${pageHead("Health", "Live health checks of every subsystem behind LeadAI.", "")}
    <div class="adm-health-hero">
      <span class="adm-status-word ${allOk ? "ok" : "error"}" style="font-size:2.2em">${esc(data.overall || (allOk ? "ok" : "degraded"))}</span>
      <span class="adm-hint">checked ${fmtTime(checkedAt)}</span>
    </div>
    <div class="adm-grid-2">
      ${entries.map(([name, c]) => `
        <div class="adm-card">
          <div class="adm-card-title"><span class="adm-status-dot ${isOk(c) ? "ok" : "error"}"></span> ${esc(name)}</div>
          <div class="adm-kv">
            ${c.ok !== undefined ? `<div class="adm-kv-row"><dt>Status</dt><dd><span class="adm-badge ${c.ok ? "green" : "red"}">${c.ok ? "ok" : "failed"}</span></dd></div>` : ""}
            ${c.latency_ms !== undefined ? `<div class="adm-kv-row"><dt>Latency</dt><dd>${c.latency_ms} ms</dd></div>` : ""}
            ${c.token_hint ? `<div class="adm-kv-row"><dt>Token</dt><dd><span class="adm-code">${esc(c.token_hint)}</span></dd></div>` : ""}
            ${c.last_test_ok !== undefined && c.last_test_ok !== null ? `<div class="adm-kv-row"><dt>Last test</dt><dd><span class="adm-badge ${c.last_test_ok ? "green" : "red"}">${c.last_test_ok ? "passed" : "failed"}</span></dd></div>` : ""}
            ${c.enabled !== undefined ? `<div class="adm-kv-row"><dt>Maintenance</dt><dd><span class="adm-badge ${c.enabled ? "amber" : "gray plain"}">${c.enabled ? "active" : "off"}</span></dd></div>` : ""}
            ${c.path ? `<div class="adm-kv-row"><dt>Path</dt><dd><span class="adm-code">${esc(c.path)}</span></dd></div>` : ""}
            ${c.error ? `<div class="adm-kv-row"><dt>Error</dt><dd class="adm-cell-sub" style="color:var(--red)">${esc(c.error)}</dd></div>` : ""}
          </div>
        </div>`).join("")}
    </div>`;
}

/* ──────────────────────────────── AUDIT ───────────────────────────── */
async function viewAudit() {
  const root = $("#view");
  const data = await api("/api/admin/audit-logs?limit=60");
  const items = data.items || [];
  const detailText = (e) => {
    const d = e.details;
    if (!d || typeof d !== "object" || !Object.keys(d).length) return "";
    const parts = Object.entries(d).map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`);
    return parts.join(" · ");
  };
  root.innerHTML = `
    ${pageHead("Audit log", "Every admin action, chronologically. Immutable — this list cannot be cleared.", `
      <span class="adm-badge gray plain">${fmtNum(data.total || items.length)} events</span>`)}
    <div class="adm-feed">
      ${items.length ? items.map((e) => `
        <div class="adm-feed-item">
          <div class="adm-feed-head">
            <span class="adm-avatar" style="width:26px;height:26px">${esc(initials(e.user || "system"))}</span>
            <span class="adm-cell-main">${esc(e.user || "system")}</span>
            <span class="adm-badge gray plain">${esc(e.category || "")}</span>
            <span class="adm-badge ${e.success === false ? "red" : "gray plain"}">${esc(e.action)}</span>
            <span class="adm-feed-time" title="${new Date(e.at * 1000).toISOString()}">${fmtTime(new Date(e.at * 1000))}</span>
          </div>
          ${detailText(e) ? `<div class="adm-feed-reason">${esc(detailText(e))}</div>` : ""}
        </div>`).join("") : emptyState("audit", "No audit events recorded yet.")}
    </div>`;
}

/* ──────────────────────────────── BOOT ────────────────────────────── */
document.addEventListener("DOMContentLoaded", boot);
boot();

