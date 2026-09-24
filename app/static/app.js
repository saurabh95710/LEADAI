/* LeadAI — User Portal (private, per-user product workspace).
   Flow: URL search (platform auto-detected) → Pages → Posts → Comments → Leads.
   Portal: dashboard, leads (incl. "Assigned to me"), search history, exports,
   usage & tokens, plans & billing, settings, notifications.
   All data comes from real Apify actor output — never fabricated. The backend
   scopes every /api call to the signed-in user; UI permission checks are
   cosmetic only. */

"use strict";

// ── Agent memory (workflow state survives refreshes) ─────────────────────
const memory = {
  runId: localStorage.getItem("leadai_run_id") || "",
  pageId: localStorage.getItem("leadai_page_id") || "",
  postId: localStorage.getItem("leadai_post_id") || "",
  commentsPerPost: parseInt(localStorage.getItem("leadai_commentsPerPost") || "20", 10) || 20,
};
const saveMemory = (key, value) => {
  memory[key] = value;
  try {
    if (value) localStorage.setItem("leadai_" + key, value);
    else localStorage.removeItem("leadai_" + key);
  } catch (_) { /* storage unavailable — memory still works for this tab */ }
};

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function esc(text) {
  if (text === null || text === undefined) return "";
  return String(text)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/** Only http(s) URLs may become an href (blocks javascript: etc.). */
function safeUrl(url) {
  const s = String(url || "").trim();
  return /^https?:\/\//i.test(s) ? s : "";
}

// ── Portal state (user, permissions, dashboard summary) ──────────────────
const portal = {
  user: null,
  permissions: new Set(),
  summary: null,        // GET /api/me/summary
  plans: [],            // GET /api/billing/plans (cached)
  subscription: null,   // GET /api/billing/subscription (billing viewers)
};

function can(perm) { return portal.permissions.has(perm); }
function canManageBilling() { return can("org_billing.manage"); }

// fetch with same-origin credentials (every call in this file goes through it)
function apiFetch(url, init = {}) {
  return fetch(url, Object.assign({ credentials: "same-origin" }, init));
}

/** JSON API helper: never throws; resolves {ok, status, data, networkError}. */
async function api(url, opts = {}) {
  const init = { method: opts.method || "GET", headers: { Accept: "application/json" } };
  if (opts.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  let res;
  try {
    res = await apiFetch(url, init);
  } catch (_) {
    return { ok: false, status: 0, data: {}, networkError: true };
  }
  let data = {};
  try { data = (await res.json()) || {}; } catch (_) { data = {}; }
  if (res.status === 401 && !opts.allow401) verifySession();
  return { ok: res.ok, status: res.status, data, networkError: false };
}

/** A 401 from some endpoint → confirm the session really ended before
    sending the user to the sign-in page. */
let _sessionCheck = null;
function verifySession() {
  if (_sessionCheck) return _sessionCheck;
  _sessionCheck = apiFetch("/api/auth/me", { headers: { Accept: "application/json" } })
    .then((r) => {
      if (r.status === 401) location.href = "/login?next=" + encodeURIComponent(location.pathname + location.hash);
    })
    .catch(() => {})
    .finally(() => { setTimeout(() => { _sessionCheck = null; }, 5000); });
  return _sessionCheck;
}

/** Readable sentence from a FastAPI error body. */
function errText(data, fallback) {
  const d = data && (data.detail !== undefined ? data.detail : (data.message || data.error));
  if (typeof d === "string" && d.trim()) return d;
  if (d && typeof d === "object" && !Array.isArray(d) && typeof d.message === "string") return d.message;
  if (Array.isArray(d) && d.length) {
    const first = d[0] || {};
    return String(first.msg || fallback || "Invalid input").replace(/^Value error,\s*/i, "");
  }
  return fallback || "Something went wrong. Please try again.";
}
function errCode(data) {
  const d = data && data.detail;
  return d && typeof d === "object" && !Array.isArray(d) && (d.code || d.errorType) ? String(d.code || d.errorType) : "";
}
function apiErrorMessage(err, fallback) { return errText(err, fallback); }

function fmtTime(value) {
  const d = parseDate(value);
  if (!d) return value ? esc(String(value)) : "—";
  const opts = { year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" };
  return esc(d.toLocaleString(undefined, opts));
}

// a "running" status that started more than 30 minutes ago is stale
// (server restart / crash) — the agent allows retry in that case
function isStale(startedAt) {
  if (!startedAt) return false;
  const d = parseDate(startedAt);
  if (!d) return false;
  return Date.now() - d.getTime() > 30 * 60 * 1000;
}

function fmt(n) {
  if (n === null || n === undefined) return "—";
  const num = Number(n);
  if (isNaN(num)) return "—";
  if (num >= 1e9) return "∞";
  if (num >= 10000000) return (num / 10000000).toFixed(1).replace(/\.0$/, "") + " Cr";
  if (num >= 100000) return (num / 100000).toFixed(1).replace(/\.0$/, "") + " L";
  if (num >= 1000) return (num / 1000).toFixed(1).replace(/\.0$/, "") + "K";
  return String(num);
}

function toast(msg, type = "info") {
  const box = $("toastContainer");
  if (!box) return;
  const el = document.createElement("div");
  el.className = "toast toast-" + type;
  el.setAttribute("role", type === "error" ? "alert" : "status");
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => el.classList.add("show"), 10);
  setTimeout(() => { el.classList.remove("show"); setTimeout(() => el.remove(), 400); }, type === "error" ? 6000 : 4200);
}

// ── Status badge (top-right) ─────────────────────────────────────────────
function setBadge(text, cls) {
  const badge = $("pipelineStatusBadge");
  if (!badge) return;
  badge.textContent = "⬤ " + text;
  badge.className = "status-badge " + (cls || "status-idle");
}

// ── Shared UI states (skeleton / empty / error / permission denied) ──────
const RETRY = {};
function skeletonRows(n = 5) {
  let rows = "";
  for (let i = 0; i < n; i++) {
    rows += `<div class="sk-row"><span class="skeleton skeleton-circle"></span><span class="sk-lines"><span class="skeleton skeleton-line w-60"></span><span class="skeleton skeleton-line w-40"></span></span><span class="skeleton skeleton-pill"></span></div>`;
  }
  return `<div class="sk-list" aria-busy="true" aria-label="Loading">${rows}</div>`;
}
/** Inline icon from the page's SVG sprite (index.html <symbol id="i-…">). */
function ico(name, cls) {
  return `<svg class="ico ${cls || "ico-inline"}" aria-hidden="true" focusable="false"><use href="#i-${esc(name)}"/></svg>`;
}
function emptyState({ icon = "sparkles", title = "Nothing here yet", sub = "", cta = "", ctaNav = "", ctaAction = "" } = {}) {
  const btn = cta
    ? `<button type="button" class="btn-primary btn-mini-cta" ${ctaNav ? `data-nav="${esc(ctaNav)}"` : ""} ${ctaAction ? `data-action="${esc(ctaAction)}"` : ""}>${esc(cta)}</button>`
    : "";
  return `<div class="empty-state"><div class="empty-icon" aria-hidden="true">${ico(icon, "ico-empty")}</div><div class="empty-title">${esc(title)}</div>${sub ? `<div class="empty-sub">${esc(sub)}</div>` : ""}${btn}</div>`;
}
function errorState(message, retryKey) {
  return `<div class="state-box state-error" role="alert"><div class="state-title">Couldn't load this</div><div class="state-sub">${esc(message || "Please check your connection and try again.")}</div>${retryKey ? `<button type="button" class="btn-secondary" data-retry="${esc(retryKey)}">↻ Try again</button>` : ""}</div>`;
}
function deniedState(message) {
  return `<div class="state-box state-denied" role="status"><div class="state-title">${ico("lock")} No access</div><div class="state-sub">${esc(message || "Your role doesn't include this section. Ask your workspace admin for access.")}</div></div>`;
}
/** Standard failure → state html (permission-denied vs error). */
function failureState(res, retryKey, what) {
  if (res.status === 403 && errCode(res.data) === "permission_denied") {
    return deniedState(`Your role doesn't include ${what || "this section"}. Ask your workspace admin for access.`);
  }
  return errorState(res.networkError ? "You appear to be offline." : errText(res.data, "The server returned an error."), retryKey);
}

function renderPager(el, meta, key) {
  if (!el) return;
  const total = meta && meta.total ? meta.total : 0;
  const pages = meta && meta.total_pages ? meta.total_pages : 0;
  if (!total) { el.innerHTML = ""; return; }
  const page = meta.page || 1;
  if (pages <= 1) { el.innerHTML = `<span class="pager-info">${total} result${total === 1 ? "" : "s"}</span>`; return; }
  const size = meta.page_size || 20;
  const from = (page - 1) * size + 1;
  const to = Math.min(total, page * size);
  const nums = [];
  for (let p = Math.max(1, page - 2); p <= Math.min(pages, page + 2); p++) nums.push(p);
  const btn = (p, label, aria, disabled) => {
    const cur = disabled === undefined && p === page;
    return `<button type="button" class="pager-btn${cur ? " is-current" : ""}" data-page-key="${esc(key)}" data-page="${p}" aria-label="${esc(aria)}"${cur ? ' aria-current="page"' : ""}${disabled ? " disabled" : ""}>${label}</button>`;
  };
  const gap = '<span class="pager-gap" aria-hidden="true">…</span>';
  const last = nums[nums.length - 1];
  el.innerHTML = `<span class="pager-info">${from}–${to} of ${total}</span>
    <nav class="pager-btns" aria-label="Pagination">
      ${btn(page - 1, "‹", "Previous page", page <= 1)}
      ${nums[0] > 1 ? btn(1, "1", "Page 1") + (nums[0] > 2 ? gap : "") : ""}
      ${nums.map((p) => btn(p, String(p), "Page " + p)).join("")}
      ${last < pages ? (last < pages - 1 ? gap : "") + btn(pages, String(pages), "Page " + pages) : ""}
      ${btn(page + 1, "›", "Next page", page >= pages)}
    </nav>`;
}
const PAGERS = {};

// ── Data tables: sticky header, sortable columns, column visibility, CSV ──
// cfg: { key, el, label, columns: [{ id, label, cls, required, sort: {desc, asc}, html(row), text(row) }],
//        rows, sort (current sort value), onSort(value), rowAttrs(row), actions(row), fetchAll() }
const TABLES = {};
function hiddenCols(key) {
  try { return new Set(JSON.parse(localStorage.getItem("leadai_cols_" + key) || "[]")); } catch (_) { return new Set(); }
}
function setHiddenCols(key, set) {
  try { localStorage.setItem("leadai_cols_" + key, JSON.stringify(Array.from(set))); } catch (_) { /* per-tab only */ }
}
function renderTable(cfg) {
  TABLES[cfg.key] = cfg;
  const hidden = hiddenCols(cfg.key);
  const cols = cfg.columns.filter((c) => c.required || !hidden.has(c.id));
  const head = cols.map((c) => {
    if (!c.sort || !cfg.onSort) return `<th scope="col" class="${c.cls || ""}">${esc(c.label)}</th>`;
    const dir = cfg.sort === c.sort.desc ? "descending" : cfg.sort === c.sort.asc ? "ascending" : "none";
    const next = dir === "descending" && c.sort.asc ? c.sort.asc : c.sort.desc;
    const arrow = dir === "ascending" ? "▲" : dir === "descending" ? "▼" : "↕";
    return `<th scope="col" class="${c.cls || ""}" aria-sort="${dir}"><button type="button" class="th-sort${dir !== "none" ? " is-sorted" : ""}" data-table-sort="${esc(cfg.key)}" data-sort="${esc(next)}" title="Sort by ${esc(c.label.toLowerCase())}">${esc(c.label)}<span class="th-arrow" aria-hidden="true">${arrow}</span></button></th>`;
  }).join("") + (cfg.actions ? `<th scope="col" class="th-actions"><span class="sr-only">Actions</span></th>` : "");
  const body = cfg.rows.map((r) => `<tr ${cfg.rowAttrs ? cfg.rowAttrs(r) : ""}>${cols.map((c) =>
      `<td class="${c.cls || ""}" data-label="${esc(c.label)}">${c.html(r)}</td>`).join("")}${cfg.actions ? `<td class="td-actions">${cfg.actions(r)}</td>` : ""}</tr>`).join("");
  cfg.el.innerHTML = `<div class="ut-wrap"><table class="ut-table ut-${esc(cfg.key)}"><caption class="sr-only">${esc(cfg.label)}</caption><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  renderColMenu(cfg.key);
  setTableTools(cfg.key, true);
}
/** Enable/disable a table's Columns + CSV tools (disabled while the list is empty). */
function setTableTools(key, enabled) {
  document.querySelectorAll(`[data-col-toggle="${key}"], [data-table-export="${key}"], [data-server-export="${key}"]`).forEach((b) => { b.disabled = !enabled; });
}
function clearTable(key) { TABLES[key] = null; setTableTools(key, false); }
function renderColMenu(key) {
  const panel = $("colPanel-" + key);
  const cfg = TABLES[key];
  if (!panel || !cfg) return;
  const hidden = hiddenCols(key);
  panel.innerHTML = `<div class="menu-panel-head"><strong>Visible columns</strong><button type="button" class="link-btn" data-cols-reset="${esc(key)}">Reset</button></div>` +
    cfg.columns.filter((c) => !c.required).map((c) => `<label class="col-opt"><input type="checkbox" data-col-key="${esc(key)}" value="${esc(c.id)}" ${hidden.has(c.id) ? "" : "checked"}><span>${esc(c.label)}</span></label>`).join("");
}
function csvCell(v) {
  let s = v === null || v === undefined ? "" : String(v);
  if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;           // no spreadsheet formula injection
  return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}
async function exportTableCsv(key, btn) {
  const cfg = TABLES[key];
  if (!cfg || !cfg.rows.length) { toast("Nothing to export yet", "info"); return; }
  if (btn) { btn.disabled = true; btn.setAttribute("aria-busy", "true"); }
  try {
    const rows = cfg.fetchAll ? await cfg.fetchAll() : cfg.rows;
    if (!rows) { toast("Couldn't prepare the download — please try again", "error"); return; }
    const hidden = hiddenCols(key);
    const cols = cfg.columns.filter((c) => c.text && (c.required || !hidden.has(c.id)));
    const lines = [cols.map((c) => csvCell(c.label)).join(",")]
      .concat(rows.map((r) => cols.map((c) => csvCell(c.text(r))).join(",")));
    const blob = new Blob(["\ufeff" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${key}_${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1500);
    toast(`Downloaded ${rows.length} row${rows.length === 1 ? "" : "s"}`, "success");
    logClientExport(key, rows.length, cols.map((c) => c.label), cfg.filters ? cfg.filters() : {});
  } finally {
    if (btn) { btn.disabled = false; btn.removeAttribute("aria-busy"); }
  }
}
/** Audit trail for an in-browser table download (fire-and-forget: a failed
    log never blocks or undoes the download the user already has). */
function logClientExport(table, rows, columns, filters) {
  if (!CLIENT_LOG_TABLES.has(table)) return;
  const clean = {};
  Object.entries(filters || {}).forEach(([k, v]) => { if (v !== "" && v !== null && v !== undefined) clean[k] = String(v).slice(0, 200); });
  api("/api/me/exports/client-log", { method: "POST", body: { table, rows, columns: columns.slice(0, 50), filters: clean } })
    .then((res) => { if (res.ok && currentView === "exports") loadExports(); })
    .catch(() => {});
}
const CLIENT_LOG_TABLES = new Set(["history", "exports", "ledger"]);
/** Plain {name: value} of a URLSearchParams without paging keys (for the export audit). */
function filtersOf(params) {
  const out = {};
  params.forEach((v, k) => { if (k !== "page" && k !== "page_size" && v) out[k] = v; });
  return out;
}
/** Every page of a paged /api/me/* list (max 1,000 rows) for CSV downloads. */
async function fetchAllPages(buildUrl) {
  const out = [];
  for (let page = 1; page <= 10; page++) {
    const res = await api(buildUrl(page, 100));
    if (!res.ok) return null;
    out.push(...(res.data.items || []));
    if (page >= (res.data.total_pages || 1)) break;
  }
  return out;
}

// ── Confirm dialog (replaces window.confirm for destructive actions) ──────
let _confirmResolve = null;
function confirmAction({ title = "Are you sure?", message = "", confirmLabel = "Confirm", danger = false } = {}) {
  const modal = $("confirmModal");
  if (!modal) return Promise.resolve(window.confirm(message || title));
  if (_confirmResolve) _confirmResolve(false);
  const prevFocus = document.activeElement;
  $("confirmTitle").textContent = title;
  $("confirmMsg").textContent = message;
  const ok = $("confirmOk");
  ok.textContent = confirmLabel;
  ok.className = danger ? "btn-danger-solid" : "btn-primary";
  const icon = $("confirmIcon");
  icon.className = "confirm-icon" + (danger ? " is-danger" : "");
  icon.textContent = danger ? "!" : "?";
  modal.classList.remove("hidden");
  setTimeout(() => (danger ? $("confirmCancel") : ok).focus(), 20);
  return new Promise((resolve) => {
    _confirmResolve = (v) => {
      _confirmResolve = null;
      modal.classList.add("hidden");
      if (prevFocus && document.contains(prevFocus)) { try { prevFocus.focus(); } catch (_) {} }
      resolve(Boolean(v));
    };
  });
}

/** Labelled, accessible usage meter (token colours; warn >= 80%, danger at 100%). */
function meterBar(pct, label, valueText) {
  const p = Math.max(0, Math.min(100, Math.round(pct || 0)));
  return `<div class="quota-bar" role="progressbar" aria-label="${esc(label)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${p}"${valueText ? ` aria-valuetext="${esc(valueText)}"` : ""}><div class="quota-fill ${meterClass(p)}" style="width:${p}%"></div></div>`;
}

function debounce(fn, ms) {
  let t = null;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

// ── Navigation / router ──────────────────────────────────────────────────
const PORTAL_VIEWS = ["dashboard", "search", "pages", "posts", "comments", "leads",
                      "history", "exports", "usage", "billing", "settings"];
const WORKFLOW_VIEWS = ["search", "pages", "posts", "comments"];
const VIEWS = WORKFLOW_VIEWS; // legacy name kept for the workflow breadcrumb
const VIEW_TITLES = {
  dashboard: "Dashboard", search: "New search", pages: "Pages", posts: "Posts",
  comments: "Comments & leads", leads: "Leads", history: "Search history",
  exports: "Exports", usage: "Usage & tokens", billing: "Plans & billing", settings: "Settings",
};
let currentView = null;
const VIEW_GROUPS = { search: "Workspace", leads: "Intelligence", history: "Intelligence", exports: "Intelligence",
                      usage: "Account", billing: "Account", settings: "Account" };

/** Consistent breadcrumb trail above every view. */
function renderCrumbs(view) {
  const el = $("pageCrumbs");
  if (!el) return;
  const link = (nav, label) => `<li><button type="button" class="crumb-link" data-nav="${nav}">${esc(label)}</button></li>`;
  const items = [view === "dashboard" ? `<li aria-current="page"><span>Home</span></li>` : link("dashboard", "Home")];
  if (["pages", "posts", "comments"].includes(view)) {
    items.push(link("history", "Search history"));
    if (view !== "pages") items.push(link("pages", "Pages"));
    if (view === "comments") items.push(link("posts", "Posts"));
  } else if (VIEW_GROUPS[view]) {
    items.push(`<li><span class="crumb-group">${esc(VIEW_GROUPS[view])}</span></li>`);
  }
  if (view !== "dashboard") items.push(`<li aria-current="page"><span>${esc(VIEW_TITLES[view] || view)}</span></li>`);
  el.innerHTML = `<ol>${items.join("")}</ol>`;
}

function viewAllowed(view) {
  if (view === "pages") return Boolean(memory.runId);
  if (view === "posts") return Boolean(memory.pageId);
  if (view === "comments") return Boolean(memory.postId);
  return PORTAL_VIEWS.includes(view);
}

function parseHash() {
  const raw = (location.hash || "").replace(/^#/, "");
  const [view, query] = raw.split("?");
  const params = new URLSearchParams(query || "");
  return { view: view || "", params };
}

function navigateToView(view, opts = {}) {
  if (view === "results") {
    if (!memory.runId) {
      toast("Open a search from your history to browse its results", "info");
      view = "history";
    } else {
      view = memory.postId ? "comments" : memory.pageId ? "posts" : "pages";
    }
  }
  if (!PORTAL_VIEWS.includes(view)) view = "dashboard";
  if (!viewAllowed(view)) view = WORKFLOW_VIEWS.includes(view) ? "search" : "dashboard";

  PORTAL_VIEWS.forEach((v) => { const el = $("view-" + v); if (el) el.classList.toggle("hidden", v !== view); });
  const crumb = $("workflowBreadcrumb");
  if (crumb) crumb.classList.toggle("hidden", !WORKFLOW_VIEWS.includes(view) || !memory.runId);
  document.querySelectorAll(".breadcrumb-item").forEach((btn) => {
    const active = btn.dataset.view === view;
    btn.classList.toggle("active", active);
    btn.disabled = !viewAllowed(btn.dataset.view);
    if (active) btn.setAttribute("aria-current", "step"); else btn.removeAttribute("aria-current");
  });
  const navKey = ["pages", "posts", "comments"].includes(view) ? "results" : view;
  document.querySelectorAll(".side-link[data-nav]").forEach((a) => {
    const on = a.dataset.nav === navKey;
    a.classList.toggle("active", on);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  const navResults = $("navResults");
  if (navResults) {
    navResults.classList.toggle("is-disabled", !memory.runId);
    navResults.title = memory.runId ? "Results of your current search" : "Open a search to browse results";
  }
  const hash = "#" + view + (opts.query ? "?" + opts.query : "");
  if (location.hash !== hash) {
    try { history.pushState(null, "", hash); } catch (_) { location.hash = hash; }
  }
  document.title = (VIEW_TITLES[view] || "LeadAI") + " · " + ((window.AppConfig && AppConfig.config && AppConfig.config.app && AppConfig.config.app.name) || "LeadAI");
  renderCrumbs(view);
  // the header pipeline badge describes the results screens / a live run only
  if (!_searchInFlight && !["pages", "posts", "comments"].includes(view)) {
    const badge = $("pipelineStatusBadge");
    if (badge && /Viewing/.test(badge.textContent)) setBadge("Idle", "status-idle");
  }
  closeSidebar();
  closeMenus();
  const changed = currentView !== view;
  currentView = view;
  if (changed && opts.focus !== false) {
    const main = $("main");
    if (main && opts.userInitiated) main.focus({ preventScroll: true });
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  if (view === "dashboard") loadDashboard();
  if (view === "search") { applySearchState(); renderRecentSearches(); }
  if (view === "pages") renderPagesScreen();
  if (view === "posts") renderPostsScreen();
  if (view === "comments") renderCommentsScreen();
  if (view === "leads") { if (opts.leadsView) setLeadsView(opts.leadsView, false); loadLeads(); }
  if (view === "history") loadHistory();
  if (view === "exports") loadExports();
  if (view === "usage") loadUsage();
  if (view === "billing") loadBillingData();
  if (view === "settings") showSettingsTab(opts.tab || currentSettingsTab);
}

// ── URL SEARCH — paste a social media link, platform auto-detected ──────
const URL_LABELS = {
  facebook: ["Facebook", ico("facebook")], instagram: ["Instagram", ico("instagram")],
  youtube: ["YouTube", ico("youtube")], linkedin: ["LinkedIn", ico("linkedin")],
};

// platform display names for every screen — "unknown" renders as a plain
// link with no platform name, never as Facebook
const PLATFORMS = {
  facebook: "Facebook", instagram: "Instagram",
  youtube: "YouTube", linkedin: "LinkedIn",
};
function platformInfo(p) {
  return { name: PLATFORMS[p] || null, icon: (URL_LABELS[p] || [])[1] || null };
}
const PLATFORM_HOSTS = [
  [/(^|\.)facebook\.com$|(^|\.)fb\.com$|(^|\.)fb\.me$/i, "facebook"],
  [/(^|\.)instagram\.com$/i, "instagram"],
  [/(^|\.)youtube\.com$|(^|\.)youtu\.be$/i, "youtube"],
  [/(^|\.)linkedin\.com$/i, "linkedin"],
];
/** Client-side hint only — the server does the authoritative detection. */
function detectPlatform(url) {
  try {
    const u = new URL(/^https?:\/\//i.test(url) ? url : "https://" + url);
    for (const [rx, p] of PLATFORM_HOSTS) if (rx.test(u.hostname)) return p;
  } catch (_) { /* not a URL */ }
  return null;
}

// replaced with the real handler by handleUrlSearch while a run is active —
// exists so the inline onclick never throws
function cancelCurrentSearch() {}

// ── Live pipeline indicator (driven by run.status / run.phase) ───────────
// URL → Agent → Apify → Page info → Posts → Comments → Comment qualification
// → AI/rules → Lead intelligence → Lead scoring → Results
const PIPELINE_STEPS = ["url", "agent", "apify", "page", "posts", "comments",
                        "qualify", "ai", "intel", "score", "results"];
// done = steps completed before the active one; live = steps that run per
// post while comments are being collected (qualification → scoring)
const PHASE_STATE = {
  queued:   { done: 1, active: 1, pct: 8 },
  page:     { done: 3, active: 3, pct: 25 },
  posts:    { done: 4, active: 4, pct: 50 },
  comments: { done: 5, active: 5, live: [6, 7, 8, 9], pct: 72 },
};
const URL_PHASE_PCT = { url_invalid: 5, platform_disabled: 10, queued: 8, page: 25, posts: 50,
                        comments: 72, completed: 100, failed: 100, error: 100, cancelled: 100 };
let lastActiveStep = 0;

function setAnalysisSteps(phase, status) {
  const list = $("urlSearchSteps");
  if (!list) return;
  const items = Array.from(list.querySelectorAll("li[data-step]"));
  let done = 0, active = -1, live = [], errorAt = -1, cancelAt = -1;
  if (status === "completed" || phase === "completed") {
    done = items.length;
  } else if (PHASE_STATE[phase]) {
    ({ done, active } = PHASE_STATE[phase]);
    live = PHASE_STATE[phase].live || [];
    lastActiveStep = active;
  }
  if (phase === "url_invalid") errorAt = 0;
  else if (phase === "platform_disabled") errorAt = 1;
  if (status === "error" && errorAt < 0) errorAt = lastActiveStep;
  if (status === "cancelled") cancelAt = lastActiveStep;
  if (errorAt >= 0) { done = errorAt; active = -1; live = []; }
  if (cancelAt >= 0) { done = cancelAt; active = -1; live = []; }
  const counter = $("urlSearchStepCount");
  if (counter) counter.textContent = items.length ? `Step ${Math.min(items.length, Math.max(1, (active >= 0 ? active : done) + 1))} of ${items.length}` : "";
  items.forEach((li, i) => {
    li.classList.remove("done", "active", "error", "live", "cancelled");
    li.removeAttribute("aria-current");
    if (i === errorAt) li.classList.add("error");
    else if (i === cancelAt) li.classList.add("cancelled");
    else if (i < done) li.classList.add("done");
    else if (i === active && status === "running") { li.classList.add("active"); li.setAttribute("aria-current", "step"); }
    else if (live.includes(i) && status === "running") li.classList.add("live");
  });
}

function setProgress(pct) {
  const fill = $("urlSearchProgressFill");
  const bar = $("urlSearchProgressBar");
  if (fill) fill.style.width = pct + "%";
  if (bar) bar.setAttribute("aria-valuenow", String(pct));
}

function renderPipelineMeta(data) {
  const meta = $("urlSearchMeta");
  if (!meta || !data) return;
  const pages = data.pages || [];
  const posts = pages.reduce((s, p) => s + (p.total_posts_found != null ? p.total_posts_found : (p.posts_count || 0)), 0);
  const qualifying = pages.reduce((s, p) => s + (p.qualifying_posts_count || 0), 0);
  const cf = (data.search || {}).comment_filter_summary;
  const parts = [];
  if (pages.length) parts.push(`${pages.length} page${pages.length === 1 ? "" : "s"}`);
  if (posts) parts.push(`${posts} posts found`);
  if (qualifying) parts.push(`${qualifying} qualifying`);
  if (cf && cf.total) parts.push(`${cf.matched || 0}/${cf.total} comments qualified for AI`);
  meta.textContent = parts.join(" · ");
}

async function pollSearchRun(runId, onCancelRequested) {
  let cancelled = false;
  let failures = 0;
  if (onCancelRequested) onCancelRequested(() => { cancelled = true; });
  while (true) {
    const res = await api(`/api/search/${encodeURIComponent(runId)}`);
    if (!res.ok) {
      if (res.status === 404 || ++failures >= 4) {
        toast("Lost track of the search status — check Search history", "error");
        return { status: "error", error: "Could not read the search status" };
      }
      await sleep(2000);
      continue;
    }
    failures = 0;
    const run = res.data.search || {};
    const label = $("urlSearchProgressLabel");
    if (label) label.textContent = run.message || run.error || run.status || "Working…";
    const pct = URL_PHASE_PCT[run.phase];
    if (pct !== undefined) setProgress(pct);
    setAnalysisSteps(run.phase, run.status);
    renderPipelineMeta(res.data);

    if (run.status !== "running") {
      if (run.status === "error") toast(run.error || "Search failed", "error");
      return run;
    }
    if (cancelled) {
      // the user clicked Cancel — the backend stops the Apify run; stop
      // polling once it flips to a terminal status
      for (let i = 0; i < 80; i++) {
        const c = await api(`/api/search/${encodeURIComponent(runId)}`);
        if (c.ok) {
          const s = c.data.search || {};
          setAnalysisSteps(s.phase, s.status);
          if (s.status !== "running") return s;
        }
        await sleep(1500);
      }
      return { status: "cancelled" };
    }
    await sleep(1500);
  }
}

let recentData = [];
let recentFilter = "all";
let recentLimit = 8;
let _recentRefreshTimer = null;

function parseDate(ts) {
  if (!ts) return null;
  if (ts instanceof Date) return isNaN(ts.getTime()) ? null : ts;
  let s = String(ts).trim();
  if (!s) return null;
  if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(s)) {
    s = s.replace(" ", "T") + "Z";
  }
  const d = new Date(s);
  return isNaN(d.getTime()) ? null : d;
}

function relativeTime(ts) {
  const d = parseDate(ts);
  if (!d) return "";
  const diff = Date.now() - d.getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return m + "m ago";
  const h = Math.floor(m / 60);
  if (h < 24) return h + "h ago";
  const days = Math.floor(h / 24);
  if (days < 30) return days + "d ago";
  return d.toLocaleDateString();
}

function formatDate(ts) {
  const d = parseDate(ts);
  if (!d) return ts ? String(ts) : "";
  return d.toLocaleString(undefined, {
    day: "numeric", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

/** Consistent status pill (Active, Suspended, Pending, Failed, Completed, Running, Expired, Cancelled, Demo). */
/* Canonical status → colour mapping (shared by every portal):
   success: active/completed/success/paid/confirmed · info (animated dot): running/processing/in_progress
   warning: pending/queued/draft/invited/payment states · danger: failed/error/rejected/refund_due
   neutral: cancelled/expired/archived/deactivated/revoked (suspended = danger-soft) · violet: demo/trial */
const STATUS_PILL = {
  completed: ["completed", "Completed"], success: ["completed", "Success"], confirmed: ["completed", "Confirmed"],
  paid: ["completed", "Paid"], active: ["active", "Active"],
  running: ["running", "Running"], processing: ["running", "Processing"], in_progress: ["running", "In progress"],
  queued: ["pending", "Queued"], pending: ["pending", "Pending"], draft: ["pending", "Draft"], invited: ["pending", "Invited"],
  pending_payment: ["pending", "Awaiting payment"], payment_received: ["pending", "Payment received"],
  pending_admin_confirmation: ["pending", "Pending confirmation"], open: ["pending", "Open"],
  error: ["failed", "Failed"], failed: ["failed", "Failed"], rejected: ["failed", "Rejected"], refund_due: ["failed", "Refund due"],
  suspended: ["suspended", "Suspended"], cancelled: ["cancelled", "Cancelled"], expired: ["expired", "Expired"],
  archived: ["cancelled", "Archived"], deactivated: ["cancelled", "Deactivated"], revoked: ["cancelled", "Revoked"],
  void: ["cancelled", "Void"],
  demo: ["demo", "Demo"], trial: ["demo", "Trial"], trialing: ["demo", "Trial"],
};
function statusPill(status, labelOverride) {
  const [cls, label] = STATUS_PILL[status] || ["neutral", String(status || "—").replace(/_/g, " ")];
  return `<span class="status-pill sp-${cls}" data-status="${esc(status || "")}">${esc(labelOverride || label)}</span>`;
}

async function fetchRecentSearches() {
  const res = await api(`/api/search/history?limit=${recentLimit}`);
  if (!res.ok) { recentData = []; return false; }
  recentData = res.data.searches || [];
  return true;
}

function renderRecentRows() {
  const list = $("recentSearchesList");
  const empty = $("recentSearchesEmpty");
  if (!list || !empty) return;
  const rows = recentData.filter((s) => recentFilter === "all" || (s.platform || (s.intent || {}).platform) === recentFilter);
  if (!rows.length) {
    list.innerHTML = "";
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");
  const canDelete = can("search.cancel");
  list.innerHTML = rows.map((s) => {
    const platform = s.platform || (s.intent || {}).platform;
    const [label, icon] = URL_LABELS[platform] || [platform || "URL", ico("link")];
    const count = s.pages_stored || 0;
    return `
      <div class="rs-row" role="button" tabindex="0" data-open-run="${esc(s.run_id)}" aria-label="Open search for ${esc(s.query)}">
        <span class="rs-icon" aria-hidden="true">${icon}</span>
        <span class="rs-main">
          <span class="rs-title">${esc(s.query)}</span>
          <span class="rs-sub">${esc(label)} · ${count} page${count === 1 ? "" : "s"}</span>
        </span>
        <span class="rs-right">
          ${statusPill(s.status)}
          <span class="rs-time" title="${esc(formatDate(s.created_at))}">${esc(relativeTime(s.created_at))}</span>
          ${canDelete ? `<button class="rs-del" type="button" title="Delete this search and its data" aria-label="Delete this search" data-delete-run="${esc(s.run_id)}">✕</button>` : ""}
          <span class="rs-open" aria-hidden="true">Open →</span>
        </span>
      </div>`;
  }).join("");
}

async function deleteSearch(runId, ev) {
  if (ev) ev.stopPropagation();
  if (!(await confirmAction({ title: "Delete this search?", message: "Its pages, posts, comments and leads are deleted too. This cannot be undone.", confirmLabel: "Delete search", danger: true }))) return;
  const res = await api(`/api/search/${encodeURIComponent(runId)}`, { method: "DELETE" });
  if (!res.ok) { toast("Could not delete search: " + errText(res.data, "Delete failed"), "error"); return; }
  toast("Search deleted", "success");
  if (memory.runId === runId) { saveMemory("runId", ""); saveMemory("pageId", ""); saveMemory("postId", ""); }
  await fetchRecentSearches();
  renderRecentRows();
  renderSessionStats();
  if (currentView === "history") loadHistory();
}

function renderSessionStats() {
  const strip = $("sessionStats");
  if (!strip) return;
  if (!recentData.length) { strip.classList.add("hidden"); return; }
  const completed = recentData.filter((s) => s.status === "completed").length;
  const pages = recentData.reduce((sum, s) => sum + (s.pages_stored || 0), 0);
  $("statSearches").textContent = recentData.length;
  $("statCompleted").textContent = completed;
  $("statPosts").textContent = pages;
  strip.classList.remove("hidden");
}

function setRecentFilter(btn) {
  document.querySelectorAll("#recentFilters .filter-chip").forEach((c) => {
    c.classList.toggle("active", c === btn);
    c.setAttribute("aria-pressed", c === btn ? "true" : "false");
  });
  recentFilter = btn.dataset.f;
  renderRecentRows();
}

async function recentViewAll() { navigateToView("history", { userInitiated: true }); }

async function renderRecentSearches() {
  const list = $("recentSearchesList");
  if (list && !recentData.length) list.innerHTML = skeletonRows(3);
  const ok = await fetchRecentSearches();
  if (!ok && list) {
    $("recentSearchesEmpty").classList.add("hidden");
    RETRY.recent = renderRecentSearches;
    list.innerHTML = errorState("Recent searches are unavailable right now.", "recent");
    return;
  }
  renderRecentRows();
  renderSessionStats();
}

async function reopenSearch(runId) {
  const res = await api(`/api/search/${encodeURIComponent(runId)}`);
  if (!res.ok) { toast(res.status === 404 ? "That search no longer exists" : "Could not open that search", "error"); return; }
  if (memory.runId !== runId) { saveMemory("pageId", ""); saveMemory("postId", ""); }
  saveMemory("runId", runId);
  toast("Opened search: " + ((res.data.search || {}).query || runId), "info");
  navigateToView("pages", { userInitiated: true });  // renderPagesScreen auto-fires collection if needed
}

// ── Search caps / blockers (validated BEFORE running) ────────────────────
function searchCaps() { return (portal.summary && portal.summary.caps) || {}; }
function searchBlockers() { return (portal.summary && portal.summary.blockers) || []; }

function clampToCap(input, cap) {
  if (!input) return { value: 0, clamped: false };
  let v = parseInt(input.value, 10);
  if (isNaN(v) || v < 1) v = 1;
  let clamped = false;
  if (cap && v > cap) { v = cap; clamped = true; }
  input.value = v;
  return { value: v, clamped };
}

let _capsApplied = false;
function applySearchState() {
  const caps = searchCaps();
  const postsIn = $("urlSearchLimit");
  const commentsIn = $("urlSearchCommentsPerPost");
  const postsHint = $("capPostsHint");
  const commentsHint = $("capCommentsHint");
  if (postsIn && caps.posts_per_search) {
    postsIn.max = caps.posts_per_search;
    if (!_capsApplied && caps.posts_default) postsIn.value = caps.posts_default;
    if (parseInt(postsIn.value, 10) > caps.posts_per_search) postsIn.value = caps.posts_per_search;
    if (postsHint) postsHint.textContent = `1 – ${caps.posts_per_search}${caps.plan_posts_per_search ? " on your plan" : ""}`;
  }
  if (commentsIn && caps.comments_per_post) {
    commentsIn.max = caps.comments_per_post;
    if (parseInt(commentsIn.value, 10) > caps.comments_per_post) commentsIn.value = caps.comments_per_post;
    if (commentsHint) commentsHint.textContent = `1 – ${caps.comments_per_post}${caps.plan_comments_per_post ? " on your plan" : ""}`;
  }
  const cpp = $("commentsPerPostInput");
  if (cpp && caps.comments_per_post) {
    cpp.max = caps.comments_per_post;
    if (parseInt(cpp.value, 10) > caps.comments_per_post) cpp.value = caps.comments_per_post;
  }
  if (portal.summary) _capsApplied = true;

  // token cost line
  const costRow = $("searchCostRow");
  const usage = (portal.summary && portal.summary.usage) || {};
  const costs = (portal.summary && portal.summary.token_costs) || {};
  if (costRow) {
    const t = usage.tokens;
    costRow.innerHTML = t && costs.search
      ? `<span class="cost-chip">${ico("coins")} Uses <b>${esc(costs.search)}</b> tokens</span><span class="cost-chip">${esc(fmt(t.remaining))} of ${esc(fmt(t.allocated))} left</span>`
      : "";
  }

  // permission + blockers
  const btn = $("urlSearchBtn");
  const permBox = $("searchPermission");
  const blockBox = $("searchBlocker");
  const canSearch = !portal.user || can("search.create");
  if (permBox) {
    permBox.classList.toggle("hidden", canSearch);
    permBox.innerHTML = canSearch ? "" : deniedState("You have view-only access, so you can't start searches. Ask your workspace admin for search access.");
  }
  const blockers = searchBlockers();
  if (blockBox) {
    if (blockers.length && canSearch) {
      const b = blockers[0];
      const copy = ENTITLEMENT_COPY[b.code] || {};
      blockBox.className = "inline-alert alert-danger";
      blockBox.innerHTML = `<div><strong>${esc(copy.title || "Searching is paused")}</strong><div>${esc(b.message)}</div></div>${upgradeButtonHtml(b.code)}`;
    } else {
      blockBox.className = "hidden";
      blockBox.innerHTML = "";
    }
  }
  if (btn && !btn.classList.contains("is-loading")) {
    const blocked = !canSearch || blockers.length > 0;
    btn.disabled = blocked;
    btn.setAttribute("aria-disabled", blocked ? "true" : "false");
  }
}

/** Guided flow: 1 link → 2 limits → 3 qualification (optional) → 4 run. */
function updateSearchGuide(platform, hasValue) {
  document.querySelectorAll("#platformHints [data-p]").forEach((el) => el.classList.toggle("on", el.dataset.p === platform));
  const guide = $("searchGuide");
  if (!guide) return;
  const steps = Array.from(guide.querySelectorAll("li[data-g]"));
  const ready = Boolean(platform);
  const state = { url: ready ? "done" : "current", limits: ready ? "done" : "", filter: ready ? "done" : "", run: ready ? "current" : "" };
  if (hasValue && !platform) state.url = "error";
  steps.forEach((li) => {
    li.classList.remove("done", "current", "error");
    if (state[li.dataset.g]) li.classList.add(state[li.dataset.g]);
    if (state[li.dataset.g] === "current") li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
  });
}

let _elapsedTimer = null;
function startElapsed() {
  const el = $("urlSearchElapsed");
  clearInterval(_elapsedTimer);
  if (!el) return;
  const t0 = Date.now();
  const tick = () => {
    const sec = Math.floor((Date.now() - t0) / 1000);
    el.textContent = `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
  };
  tick();
  _elapsedTimer = setInterval(tick, 1000);
}
function stopElapsed() { clearInterval(_elapsedTimer); _elapsedTimer = null; }

function setUrlError(msg) {
  const el = $("urlSearchError");
  const input = $("urlSearchInput");
  if (el) el.textContent = msg || "";
  if (input) { if (msg) input.setAttribute("aria-invalid", "true"); else input.removeAttribute("aria-invalid"); }
}

let _searchInFlight = false;
async function handleUrlSearch(event) {
  event.preventDefault();
  if (_searchInFlight) return;
  const url = $("urlSearchInput").value.trim();
  setUrlError("");
  if (!url) { setUrlError("Paste a Facebook, Instagram, YouTube or LinkedIn URL."); $("urlSearchInput").focus(); return; }
  if (!detectPlatform(url)) {
    setUrlError("That doesn't look like a Facebook, Instagram, YouTube or LinkedIn link.");
    $("urlSearchInput").focus();
    return;
  }
  if (portal.user && !can("search.create")) { toast("Your role can't start searches.", "error"); return; }
  const blockers = searchBlockers();
  if (blockers.length) { showQuotaExceededModal(blockers[0]); return; }

  const caps = searchCaps();
  const posts = clampToCap($("urlSearchLimit"), caps.posts_per_search);
  const comments = clampToCap($("urlSearchCommentsPerPost"), caps.comments_per_post);
  if (posts.clamped || comments.clamped) {
    toast("Adjusted to your plan's per-search limits", "info");
  }
  const maxPosts = posts.value || 20;
  const maxCommentsPerPost = comments.value || 30;
  saveMemory("commentsPerPost", maxCommentsPerPost);

  const btn = $("urlSearchBtn");
  const btnText = btn.querySelector(".btn-text");
  const cancelBtn = $("urlSearchCancelBtn");
  const doneActions = $("urlSearchDoneActions");
  _searchInFlight = true;
  btn.disabled = true;
  btn.classList.add("is-loading");
  btn.setAttribute("aria-busy", "true");
  if (btnText) btnText.textContent = "Starting…";
  const prog = $("urlSearchProgress");
  prog.classList.remove("hidden");
  if (doneActions) { doneActions.classList.add("hidden"); doneActions.innerHTML = ""; }
  $("urlSearchSpinner").classList.remove("hidden");
  $("urlSearchMeta").textContent = "";
  $("urlSearchProgressLabel").textContent = "Validating URL and plan limits…";
  setProgress(3);
  lastActiveStep = 0;
  setAnalysisSteps("queued", "running");
  startElapsed();
  setBadge("URL search", "status-running");

  const params = new URLSearchParams({
    url,
    max_posts: String(maxPosts),
    max_comments_per_post: String(maxCommentsPerPost),
  });
  const mode = (document.querySelector('input[name="filterMode"]:checked') || {}).value || "all";
  if (mode === "preset") {
    const preset = $("urlFilterPreset") ? $("urlFilterPreset").value : "";
    if (preset) { params.set("filter_mode", "preset"); params.set("preset", preset); }
  } else if (mode === "custom") {
    const inc = ($("urlFilterInclude").value || "").trim();
    const exc = ($("urlFilterExclude").value || "").trim();
    const cats = Array.from(document.querySelectorAll("#urlFilterCategories input:checked"))
      .map((c) => c.value).join(",");
    if (inc || exc || cats) {
      params.set("filter_mode", "custom");
      if (inc) params.set("include_keywords", inc);
      if (exc) params.set("exclude_keywords", exc);
      if (cats) params.set("categories", cats);
      const mm = $("urlFilterMatchMode");
      if (mm) params.set("match_mode", mm.value);
    }
  }

  let runId = null;
  try {
    const res = await api(`/api/url/search?${params.toString()}`, { method: "POST" });
    if (!res.ok) {
      prog.classList.add("hidden");
      if (handleEntitlementError(res.status, res.data)) { setBadge("Idle", "status-idle"); refreshSummary(); return; }
      if (res.status === 422) { setUrlError(errText(res.data, "That URL isn't supported.")); setBadge("Idle", "status-idle"); return; }
      throw new Error(res.networkError ? "You appear to be offline." : errText(res.data, "URL search failed"));
    }
    const data = res.data;
    runId = data.run_id;
    const [label, icon] = URL_LABELS[data.platform] || [data.platform, ico("link")];
    const chip = $("urlPlatformChip");
    chip.innerHTML = `<span class="intent-chip"><b>Platform:</b> ${icon} ${esc(label)}</span><span class="intent-chip"><b>URL:</b> ${esc(data.canonical_url)}</span>`;
    chip.classList.remove("hidden");
    if (memory.runId !== runId) { saveMemory("pageId", ""); saveMemory("postId", ""); }
    saveMemory("runId", runId);
    if (btnText) btnText.textContent = "Search running…";
    refreshSummary();  // tokens were charged — update the meter right away
    cancelBtn.classList.remove("hidden");
    cancelBtn.disabled = false;
    cancelBtn.textContent = "✕ Cancel search";

    const run = await pollSearchRun(runId, (setCancelFlag) => {
      cancelBtn.onclick = () => {
        setCancelFlag(true);
        cancelBtn.disabled = true;
        cancelBtn.textContent = "Cancelling…";
        api(`/api/search/${encodeURIComponent(runId)}/cancel`, { method: "POST" });
        toast("Cancelling search…", "info");
      };
    });
    cancelBtn.classList.add("hidden");
    chip.classList.add("hidden");
    $("urlSearchSpinner").classList.add("hidden");
    renderDoneActions(runId, run.status);

    if (run.status === "cancelled") {
      setBadge("Cancelled", "status-warn");
      $("urlSearchProgressLabel").textContent = "Search cancelled";
      return;
    }
    setBadge(run.status === "completed" ? "Completed" : "Error",
             run.status === "completed" ? "status-success" : "status-error");
    setProgress(100);
    $("urlSearchProgressLabel").textContent = run.message || run.error || "Completed";

    if (run.status === "error") {
      toast(run.error || "URL search failed — is the URL correct?", "error");
    } else {
      toast("Search complete — results are ready", "success");
      if (currentView === "search") navigateToView("pages");
    }
  } catch (err) {
    toast(err.message || "URL search failed", "error");
    setBadge("Idle", "status-idle");
    $("urlPlatformChip").classList.add("hidden");
    if (!runId) prog.classList.add("hidden");
  } finally {
    stopElapsed();
    _searchInFlight = false;
    btn.classList.remove("is-loading");
    btn.removeAttribute("aria-busy");
    if (btnText) btnText.textContent = "Start search";
    cancelBtn.classList.add("hidden");
    applySearchState();
    renderRecentSearches();
    refreshSummary();
  }
}

function renderDoneActions(runId, status) {
  const box = $("urlSearchDoneActions");
  if (!box) return;
  const report = `/static/url_report.html?run_id=${encodeURIComponent(runId)}`;
  box.innerHTML = status === "completed"
    ? `<button type="button" class="btn-primary btn-sm" data-open-run="${esc(runId)}">View results →</button> <a class="btn-ghost btn-sm" href="${esc(report)}" target="_blank" rel="noopener">Open report ↗</a>`
    : status === "error"
      ? `<button type="button" class="btn-secondary btn-sm" data-action="retry-search">↻ Try again</button> <button type="button" class="btn-ghost btn-sm" data-nav="history">Search history</button>`
      : `<button type="button" class="btn-ghost btn-sm" data-nav="history">Search history</button>`;
  box.classList.remove("hidden");
}

/** Re-attach the live pipeline to a search that is still running (page reload). */
async function resumeRunningSearch() {
  if (_searchInFlight || !memory.runId) return;
  const run = recentData.find((s) => s.run_id === memory.runId && s.status === "running");
  if (!run || isStale(run.created_at)) return;
  _searchInFlight = true;
  const prog = $("urlSearchProgress");
  const btn = $("urlSearchBtn");
  const cancelBtn = $("urlSearchCancelBtn");
  prog.classList.remove("hidden");
  $("urlSearchSpinner").classList.remove("hidden");
  btn.disabled = true;
  btn.classList.add("is-loading");
  setBadge("URL search", "status-running");
  if (can("search.cancel")) { cancelBtn.classList.remove("hidden"); cancelBtn.disabled = false; }
  startElapsed();
  try {
    const result = await pollSearchRun(run.run_id, (setCancelFlag) => {
      cancelBtn.onclick = () => {
        setCancelFlag(true);
        cancelBtn.disabled = true;
        cancelBtn.textContent = "Cancelling…";
        api(`/api/search/${encodeURIComponent(run.run_id)}/cancel`, { method: "POST" });
      };
    });
    $("urlSearchSpinner").classList.add("hidden");
    renderDoneActions(run.run_id, result.status);
    setBadge(result.status === "completed" ? "Completed" : result.status === "cancelled" ? "Cancelled" : "Error",
             result.status === "completed" ? "status-success" : result.status === "cancelled" ? "status-warn" : "status-error");
  } finally {
    stopElapsed();
    _searchInFlight = false;
    btn.classList.remove("is-loading");
    cancelBtn.classList.add("hidden");
    applySearchState();
    renderRecentSearches();
    refreshSummary();
  }
}

// ── Comment Filter (user URL search) ────────────────────────────────────

let urlFilterTouched = false;

function urlFilterModeChanged() {
  const radio = document.querySelector('input[name="filterMode"]:checked');
  const mode = radio ? radio.value : "all";
  const presetField = $("urlFilterPresetField");
  const customField = $("urlFilterCustomField");
  if (presetField) presetField.classList.toggle("hidden", mode !== "preset");
  if (customField) customField.classList.toggle("hidden", mode !== "custom");
}

// Apply the admin-configured default mode/preset on first visit
async function applyUrlFilterDefaults() {
  if (!window.AppConfig) return;
  try {
    const cfg = await AppConfig.load();
    const mode = cfg.defaults.comment_filter_mode;
    if (!mode || !["all", "preset", "custom"].includes(mode)) return;
    const radio = document.querySelector(`input[name="filterMode"][value="${mode}"]`);
    if (radio && !urlFilterTouched && !radio.checked) {
      radio.checked = true;
      if (mode === "preset" && cfg.defaults.keyword_preset) {
        const presetSel = $("urlFilterPreset");
        if (presetSel && [...presetSel.options].some((o) => o.value === cfg.defaults.keyword_preset)) {
          presetSel.value = cfg.defaults.keyword_preset;
        }
      }
      urlFilterModeChanged();
    }
  } catch (err) { /* defaults stay untouched on config failure */ }
}

async function loadUrlFilterCatalog() {
  const presetSel = $("urlFilterPreset");
  const catsWrap = $("urlFilterCategoriesWrap");
  const catsBox = $("urlFilterCategories");
  const res = await api("/api/comment-filters/catalog");
  if (!res.ok) {
    if (presetSel) presetSel.innerHTML = `<option value="">Filtering unavailable</option>`;
    return;
  }
  const cat = res.data;
  const opts = (cat.presets || []).map((p) =>
    `<option value="${esc(p.key)}">Preset · ${esc(p.name)}</option>`);
  // Saved rules (/api/comment-filters/rules) are platform-staff only (admin
  // session) — workspace users get the presets; the admin's ACTIVE rule still
  // applies automatically through "Default".
  if (presetSel) {
    presetSel.innerHTML = `<option value="">Default (admin rule or all)</option>` + opts.join("");
  }
  const cats = [...(cat.categories || []), ...(cat.custom_categories || [])];
  if (cats.length && catsWrap && catsBox) {
    catsWrap.classList.remove("hidden");
    catsBox.innerHTML = cats.map((c) =>
      `<label class="filter-cat"><input type="checkbox" value="${esc(c.key || c.id || c._id)}"><span>${esc(c.icon || "")} ${esc(c.name)}</span></label>`).join("");
  }
}

// ── PAGES ────────────────────────────────────────────────────────────────
let _pagesTimer = null;
async function renderPagesScreen() {
  clearTimeout(_pagesTimer);
  if (!memory.runId) { $("pagesGrid").innerHTML = ""; $("pagesListEmpty").classList.remove("hidden"); return; }
  setBadge("Viewing pages", "status-idle");
  const category = $("pagesCategoryFilter").value;
  const contact = $("pagesContactOnly").checked;
  const params = new URLSearchParams({ run_id: memory.runId, limit: "200" });
  if (category) params.set("category", category);
  if (contact) params.set("contact", "true");
  const tbody = $("pagesGrid");
  const empty = $("pagesListEmpty");
  const statusBox = $("pagesScreenStatus");
  if (!tbody.children.length) { empty.classList.add("hidden"); tbody.innerHTML = skeletonRows(2); }

  const res = await api("/api/pages?" + params.toString());
  if (!res.ok) {
    RETRY.pages = renderPagesScreen;
    empty.classList.add("hidden");
    statusBox.classList.add("hidden");
    tbody.innerHTML = failureState(res, "pages", "search results");
    return;
  }
  const data = res.data;
  if (!data.pages.length) {
    tbody.innerHTML = "";
    empty.classList.remove("hidden");
    statusBox.classList.add("hidden");
    return;
  }
  empty.classList.add("hidden");
  $("view-pages").querySelectorAll(".btn-export").forEach((b) => b.classList.toggle("hidden", !can("exports.create")));

  // populate category filter once
  const sel = $("pagesCategoryFilter");
  if (sel.options.length <= 1) {
    const cats = [...new Set(data.pages.map((p) => p.category).filter(Boolean))].sort();
    cats.forEach((c) => {
      const opt = document.createElement("option");
      opt.value = c; opt.textContent = c;
      sel.appendChild(opt);
    });
  }

  let anyRunning = false;
  const needsCollect = data.pages.some((p) => !p.posts_status || p.posts_status === "not_started");
  const collectNote = needsCollect
    ? (data.pages.some((p) => p.posts_status === "running")
        ? " · analyzing posts…"
        : " · open a page to analyze its posts")
    : "";
  const qualified = data.pages.filter((p) => p.has_qualifying_posts).length;
  statusBox.innerHTML = `<div class="summary-line">Found <b>${data.pages.length}</b> page(s)/channel(s) · <b>${qualified} with qualifying high-intent posts</b>${collectNote}</div>`;
  statusBox.classList.remove("hidden");

  tbody.innerHTML = data.pages.map((p) => {
    const pid = esc(p.id);
    const postStatus = p.posts_status || "not_started";
    if (postStatus === "running") anyRunning = true;
    const stale = isStale(p.posts_started_at);
    const found = p.total_posts_found != null ? p.total_posts_found : (p.posts_count || 0);
    const qualifying = p.qualifying_posts_count || 0;
    const err = p.posts_error || "";

    let actionBtn;
    if (postStatus === "running" && !stale) {
      actionBtn = `<span class="btn-secondary" style="cursor:default"><span class="mini-spinner"></span> Analyzing posts (${found} found)</span>`;
    } else if (postStatus === "completed" && found > 0) {
      actionBtn = `<button class="btn-primary" data-open-page="${pid}">${ico("file")} View ${found} posts${qualifying > 0 ? ` (${qualifying} qualifying)` : ""}</button>`;
    } else if (postStatus === "empty" || found === 0) {
      actionBtn = `<button class="btn-secondary" data-open-page="${pid}">↻ Re-analyze posts</button>`;
    } else {
      actionBtn = `<button class="btn-primary" data-open-page="${pid}">${ico("search")} Analyze posts</button>`;
    }

    const pic = safeUrl(p.profile_picture);
    const avatar = pic
      ? `<img class="pc-avatar" src="${esc(pic)}" alt="" loading="lazy" onerror="this.style.display='none'">`
      : `<span class="pc-avatar pc-avatar-fallback">${esc((p.page_name || "?").charAt(0).toUpperCase())}</span>`;

    const pageInfo = platformInfo(p.platform);
    const platformCls = PLATFORMS[p.platform] ? p.platform : "unknown";

    const contactChips = [];
    if (p.phone) contactChips.push(`<a class="pc-chip" href="tel:${esc(p.phone)}" data-stop>${ico("phone")} <b>${esc(p.phone)}</b></a>`);
    if (p.email) contactChips.push(`<a class="pc-chip" href="mailto:${esc(p.email)}" data-stop>${ico("mail")} <b>${esc(p.email)}</b></a>`);
    if (p.whatsapp) contactChips.push(`<a class="pc-chip" href="https://wa.me/${esc(String(p.whatsapp).replace(/[^\d]/g, ''))}" target="_blank" rel="noopener" data-stop>${ico("message")} WhatsApp</a>`);
    if (safeUrl(p.website)) contactChips.push(`<a class="pc-chip pc-chip-link" href="${esc(safeUrl(p.website))}" target="_blank" rel="noopener" data-stop>${ico("globe")} ${esc(p.website.replace(/^https?:\/\//, '').replace(/\/$/, ''))}</a>`);
    if (p.address || p.country) contactChips.push(`<span class="pc-chip pc-chip-addr" title="${esc(p.address || p.country)}">${ico("map-pin")} ${esc(p.address || p.country)}</span>`);
    if (p.category) contactChips.push(`<span class="pc-chip">${ico("tag")} ${esc(p.category)}</span>`);
    const srcUrl = safeUrl(p.facebook_url);

    return `<article class="page-card clickable-card" data-open-page="${pid}" tabindex="0" aria-label="${esc(p.page_name || "Page")}">
      <div class="pc-head">
        ${avatar}
        <div class="pc-body">
          <div class="pc-name">
            ${esc(p.page_name || "Unnamed page")}
            <span class="platform-badge ${platformCls}">${pageInfo.icon ? pageInfo.icon + " " : ""}${esc(pageInfo.name || p.platform || "Platform")}</span>
            ${p.verified ? '<span class="verified-badge" title="Verified">✓ Verified</span>' : ""}
            ${p.source_type === "group" ? '<span class="src-badge">GROUP</span>' : ""}
          </div>
          ${srcUrl ? `<a class="pc-link" href="${esc(srcUrl)}" target="_blank" rel="noopener" data-stop>Open on ${esc(pageInfo.name || "platform")} ↗</a>` : ""}
          ${p.about ? `<div class="pc-about-snippet" title="${esc(p.about)}">${esc(p.about.slice(0, 160))}${p.about.length > 160 ? "…" : ""}</div>` : ""}
        </div>
      </div>

      <div class="pc-stats">
        <div class="pc-stat"><div class="pc-stat-value">${fmt(p.followers)}</div><div class="pc-stat-label">Followers / subs</div></div>
        <div class="pc-stat"><div class="pc-stat-value">${fmt(p.likes)}</div><div class="pc-stat-label">Likes</div></div>
        <div class="pc-stat"><div class="pc-stat-value">${found}</div><div class="pc-stat-label">Posts found</div></div>
        <div class="pc-stat"><div class="pc-stat-value">${qualifying}</div><div class="pc-stat-label">Qualifying posts</div></div>
        <div class="pc-stat"><div class="pc-stat-value">${fmt(p.total_comments_on_qualifying_posts)}</div><div class="pc-stat-label">Total comments</div></div>
      </div>

      <div class="pc-contact">
        ${contactChips.length ? contactChips.join("") : `<span class="muted" style="font-size:0.75rem">No public contact info extracted</span>`}
      </div>

      <div class="pc-foot">
        ${actionBtn}
        ${err ? `<div class="row-error" title="${esc(err)}">${esc(err.slice(0, 90))}</div>` : ""}
      </div>
    </article>`;
  }).join("");

  if (anyRunning && currentView === "pages") {
    _pagesTimer = setTimeout(renderPagesScreen, 4000);
  }
}

async function openPage(pageId, event) {
  if (event) event.stopPropagation();
  const res = await api(`/api/pages/${encodeURIComponent(pageId)}`);
  if (!res.ok) { toast(errText(res.data, "Could not open that page"), "error"); return; }
  const page = res.data;
  const status = page.posts_status || "not_started";
  if (status === "running" && !isStale(page.posts_started_at)) {
    toast("Posts are already being collected for this page", "info");
    saveMemory("pageId", pageId);
    await waitForPosts(pageId);
    navigateToView("posts");
    return;
  }
  if (status === "completed" && (page.posts_count || 0) > 0) {
    saveMemory("pageId", pageId);
    saveMemory("postId", "");
    navigateToView("posts", { userInitiated: true });
    return;
  }
  if (!can("search.create")) { toast("You have view-only access — ask your admin to collect posts.", "error"); return; }
  const caps = searchCaps();
  const maxPosts = Math.min(20, caps.posts_per_search || 20);
  toast("Analyzing real posts for " + (page.page_name || "this page") + "…", "info");
  const postRes = await api(`/api/pages/${encodeURIComponent(pageId)}/posts?max_posts=${maxPosts}`, { method: "POST" });
  if (!postRes.ok) {
    if (handleEntitlementError(postRes.status, postRes.data)) return;
    toast(errText(postRes.data, "Collection failed"), "error");
    return;
  }
  refreshSummary();
  saveMemory("pageId", pageId);
  saveMemory("postId", "");
  await waitForPosts(pageId);
  navigateToView("posts");
}

async function waitForPosts(pageId) {
  for (let i = 0; i < 400; i++) {
    const res = await api(`/api/pages/${encodeURIComponent(pageId)}/posts`);
    if (res.ok && res.data.posts_status !== "running") { setBadge("Idle", "status-idle"); return res.data; }
    setBadge("Collecting posts", "status-running");
    await sleep(1500);
  }
  return null;
}

function exportPagesCsv() {
  if (!memory.runId) return;
  downloadCsv(`/api/export/pages.csv?run_id=${encodeURIComponent(memory.runId)}`, "pages.csv");
}

// ── POSTS ────────────────────────────────────────────────────────────────
let _postsTimer = null;
async function renderPostsScreen() {
  clearTimeout(_postsTimer);
  if (!memory.pageId) { $("postsGrid").innerHTML = ""; $("postsListEmpty").classList.remove("hidden"); return; }
  const tbody = $("postsGrid");
  const empty = $("postsListEmpty");
  if (!tbody.children.length) { empty.classList.add("hidden"); tbody.innerHTML = skeletonRows(3); }
  const res = await api(`/api/pages/${encodeURIComponent(memory.pageId)}/posts`);
  if (!res.ok) {
    RETRY.posts = renderPostsScreen;
    empty.classList.add("hidden");
    tbody.innerHTML = failureState(res, "posts", "these results");
    return;
  }
  const data = res.data;
  $("postsPageName").textContent = data.page.page_name || "this page";
  $("view-posts").querySelectorAll(".btn-export").forEach((b) => b.classList.toggle("hidden", !can("exports.create")));

  const statusBox = $("postsScreenStatus");
  const page = data.page;
  if (page.posts_status === "running") {
    statusBox.innerHTML = `<div class="summary-line status-running-text"><span class="mini-spinner"></span> Analyzing posts... ${data.posts_count || 0} found so far</div>`;
    statusBox.classList.remove("hidden");
  } else if (page.posts_status === "empty" || (page.posts_status === "completed" && !data.posts.length)) {
    statusBox.innerHTML = `<div class="summary-line status-error-text">${esc(page.posts_error || "No posts returned for this page.")}</div>`;
    statusBox.classList.remove("hidden");
  } else {
    const qual = data.qualifyingPosts || 0;
    const min = data.minComments || 10;
    const note = qual > 0
      ? `<span class="status-success-text">${qual} qualifying post(s) (relevant & ≥ ${min} comments)</span>`
      : `<span class="muted">No posts with ≥ ${min} comments</span>`;
    statusBox.innerHTML = `<div class="summary-line">Total posts: <b>${esc(data.totalPosts)}</b> · Relevant: <b>${esc(data.relevantPosts)}</b> · Qualifying: <b>${qual}</b> · Comments on qualifying: <b>${fmt(data.commentsOnQualifying)}</b> · Latest post: <b>${esc(data.latestPostDate || "N/A")}</b><br>${note}</div>`;
    statusBox.classList.remove("hidden");
  }

  if (!data.posts.length) {
    tbody.innerHTML = "";
    empty.classList.remove("hidden");
    if (page.posts_status === "running" && currentView === "posts") _postsTimer = setTimeout(renderPostsScreen, 2000);
    return;
  }
  empty.classList.add("hidden");
  let anyRunning = false;
  const min = data.minComments || 10;

  tbody.innerHTML = data.posts.map((post) => {
    const id = esc(post.id);
    const cStatus = post.comments_status || "not_started";
    if (cStatus === "running") anyRunning = true;
    const total = post.total_comment_count || 0;
    const scraped = post.scraped_comment_count || 0;
    const qualifying = !!post.is_qualifying;
    const cErr = post.comments_error || "";

    let actionBtn;
    if (cStatus === "running") {
      actionBtn = `<span class="btn-secondary" style="cursor:default"><span class="mini-spinner"></span> Collecting (${scraped} found)</span>`;
    } else if (scraped > 0) {
      actionBtn = `<button class="btn-primary" data-open-post="${id}">${ico("message")} View ${scraped} comments & leads</button>`;
    } else if (qualifying) {
      actionBtn = `<button class="btn-primary" data-open-post="${id}">${ico("message")} Collect comments (${total} available)</button>`;
    } else {
      actionBtn = `<button class="btn-secondary" data-open-post="${id}">${ico("message")} Collect comments (${total} available)</button>`;
    }

    const relBadge = post.is_relevant === true
      ? `<span class="badge badge-ok">✓ Relevant</span>`
      : post.is_relevant === false
        ? `<span class="badge badge-muted">✗ Low relevance</span>`
        : "";

    const qualBadge = qualifying
      ? `<span class="badge badge-lead">★ Qualifying (≥ ${min} comments)</span>`
      : `<span class="badge badge-muted">&lt;${min} comments</span>`;
    const kf = post.keyword_filter;
    const kfBadge = kf && kf.total ? `<span class="badge badge-ai" title="Comment qualification">${ico("filter")} ${esc(kf.matched || 0)}/${esc(kf.total)} qualified</span>` : "";

    const img = post.images && post.images.length ? safeUrl(post.images[0]) : "";
    const thumb = img
      ? `<img class="pt-thumb" src="${esc(img)}" alt="" loading="lazy" onerror="this.style.display='none'">`
      : (post.videos && post.videos.length ? `<span class="pt-thumb pt-thumb-video">${ico("film", "ico-thumb")}</span>` : `<span class="pt-thumb pt-thumb-fallback">${ico("file", "ico-thumb")}</span>`);

    const captionText = post.caption || post.description || post.text || "No post caption text available.";
    const postUrl = safeUrl(post.post_url);

    return `<article class="post-tile clickable-card" data-open-post="${id}" tabindex="0">
      <div class="pt-main">
        ${thumb}
        <div class="pt-body">
          <div class="pt-caption" title="${esc(captionText)}">${esc(captionText)}</div>
          <div class="pt-meta">
            <span>${ico("calendar")} ${esc(post.published_date || "Date unknown")}</span>
            ${relBadge}
            ${qualBadge}
            ${kfBadge}
            ${postUrl ? `<a class="pc-link" href="${esc(postUrl)}" target="_blank" rel="noopener" data-stop>View original post ↗</a>` : ""}
          </div>
        </div>
      </div>

      <div class="pt-stats">
        <div class="pc-stat"><div class="pc-stat-value">${fmt(post.likes_count)}</div><div class="pc-stat-label">Likes</div></div>
        <div class="pc-stat"><div class="pc-stat-value">${fmt(total)}</div><div class="pc-stat-label">Total comments</div></div>
        <div class="pc-stat"><div class="pc-stat-value">${scraped}</div><div class="pc-stat-label">Scraped & analyzed</div></div>
        <div class="pc-stat"><div class="pc-stat-value">${fmt(post.shares_count)}</div><div class="pc-stat-label">Shares</div></div>
      </div>

      <div class="pt-foot">
        ${actionBtn}
        ${cErr ? `<div class="row-error" title="${esc(cErr)}">${esc(cErr.slice(0, 90))}</div>` : ""}
      </div>
    </article>`;
  }).join("");

  if ((anyRunning || page.posts_status === "running") && currentView === "posts") {
    _postsTimer = setTimeout(renderPostsScreen, 3000);
  }
}

async function openPost(postId, event) {
  if (event) event.stopPropagation();
  const res = await api(`/api/posts/${encodeURIComponent(postId)}`);
  if (!res.ok) { toast(errText(res.data, "Could not open that post"), "error"); return; }
  const post = res.data;
  const status = post.comments_status || "not_started";
  const scraped = post.scraped_comment_count || 0;
  if (status === "running") {
    toast("Comments are already being collected for this post", "info");
    saveMemory("postId", postId);
    await waitForComments(postId);
    navigateToView("comments");
    return;
  }
  if ((status === "completed" && scraped > 0) || status === "skipped") {
    saveMemory("postId", postId);
    navigateToView("comments", { userInitiated: true });
    return;
  }
  if (!can("search.create")) {
    saveMemory("postId", postId);
    navigateToView("comments", { userInitiated: true });
    return;
  }
  const caps = searchCaps();
  const maxComments = Math.min(memory.commentsPerPost || 20, caps.comments_per_post || 500);
  toast("Collecting real comments + AI analysis…", "info");
  const postRes = await api(`/api/posts/${encodeURIComponent(postId)}/comments?max_comments=${maxComments}`, { method: "POST" });
  if (!postRes.ok) {
    if (handleEntitlementError(postRes.status, postRes.data)) return;
    toast(errText(postRes.data, "Collection failed"), "error");
    return;
  }
  refreshSummary();
  if (postRes.data.status === "skipped") {
    toast(postRes.data.message || "Not scraped — below the comment threshold", "info");
  }
  saveMemory("postId", postId);
  await waitForComments(postId);
  navigateToView("comments");
}

async function waitForComments(postId) {
  for (let i = 0; i < 400; i++) {
    const res = await api(`/api/posts/${encodeURIComponent(postId)}/comments?limit=1`);
    if (res.ok && res.data.comments_status !== "running") { setBadge("Idle", "status-idle"); return res.data; }
    setBadge("Collecting + analyzing", "status-running");
    await sleep(1500);
  }
  return null;
}

function showCommentsScrapeProgress(scraped, total, indeterminate = false) {
  const el = $("commentsScrapeProgress");
  const label = $("commentsScrapeLabel");
  const fill = $("commentsScrapeFill");
  const bar = $("commentsScrapeBar");
  el.classList.remove("hidden");
  if (indeterminate || !(total > 0)) {
    fill.classList.add("indeterminate");
    fill.style.width = "30%";
    bar.setAttribute("aria-valuenow", 0);
    label.textContent = "Loading comments…";
  } else {
    fill.classList.remove("indeterminate");
    const pct = Math.min(100, Math.round((scraped / total) * 100));
    fill.style.width = pct + "%";
    bar.setAttribute("aria-valuenow", pct);
    label.textContent = `Scraping comments… ${scraped} of ${total} (${pct}%)`;
  }
}

function exportPostsCsv() {
  if (!memory.pageId) return;
  downloadCsv(`/api/export/posts.csv?page_id=${encodeURIComponent(memory.pageId)}`, "posts.csv");
}

// ── COMMENTS / LEADS ─────────────────────────────────────────────────────

let activeCommentFilter = "all";
let searchDebounceTimer = null;
let _commentsTimer = null;

function setCommentsPerPost(el) {
  const cap = searchCaps().comments_per_post || 500;
  const n = Math.min(cap, Math.max(1, parseInt(el.value, 10) || 50));
  el.value = n;
  saveMemory("commentsPerPost", n);
  const other = el.id === "commentsPerPostInput" ? "urlSearchCommentsPerPost" : "commentsPerPostInput";
  const otherEl = $(other);
  if (otherEl) otherEl.value = n;
}

function setCommentFilterType(type, btn) {
  activeCommentFilter = type;
  const pills = document.querySelectorAll("#commentFilterPills .filter-pill");
  pills.forEach((p) => { p.classList.remove("active"); p.setAttribute("aria-pressed", "false"); });
  if (btn) { btn.classList.add("active"); btn.setAttribute("aria-pressed", "true"); }
  renderCommentsScreen();
}

function onCommentsSearchInput() {
  const input = $("commentsSearchInput");
  const clearBtn = $("commentsSearchClear");
  if (input && clearBtn) {
    clearBtn.classList.toggle("hidden", !input.value.trim());
  }
  if (searchDebounceTimer) clearTimeout(searchDebounceTimer);
  searchDebounceTimer = setTimeout(() => {
    renderCommentsScreen();
  }, 250);
}

function clearCommentsSearch() {
  const input = $("commentsSearchInput");
  const clearBtn = $("commentsSearchClear");
  if (input) input.value = "";
  if (clearBtn) clearBtn.classList.add("hidden");
  renderCommentsScreen();
}

function scoreBar(score) {
  const s = Math.max(0, Math.min(100, Number(score) || 0));
  const tier = s >= 80 ? "hot" : s >= 50 ? "warm" : "cold";
  return `<span class="score-meter score-${tier}" title="Lead score ${s}/100" aria-label="Lead score ${s} out of 100"><span class="score-meter-fill" style="width:${s}%"></span></span>`;
}

/** Score bar (tier-coloured: hot ≥ 80, warm ≥ 50, cold) + number. */
function scoreCell(score) {
  const s = Math.max(0, Math.min(100, Number(score) || 0));
  return `<span class="dt-score">${scoreBar(s)}<b>${s}</b></span>`;
}

async function renderCommentsScreen() {
  clearTimeout(_commentsTimer);
  if (!memory.postId) { $("commentsGrid").innerHTML = ""; $("commentsListEmpty").classList.remove("hidden"); return; }
  showCommentsScrapeProgress(0, 0, true);

  const searchVal = $("commentsSearchInput") ? $("commentsSearchInput").value.trim() : "";
  const qualityVal = $("commentsQualityFilter") ? $("commentsQualityFilter").value : "";
  const sortByVal = $("commentsSortBy") ? $("commentsSortBy").value : "score";
  const limitVal = Math.max(memory.commentsPerPost || 50, 200);

  const params = new URLSearchParams({
    filter_type: activeCommentFilter,
    sort_by: sortByVal,
    limit: String(limitVal)
  });
  if (searchVal) params.set("q", searchVal);
  if (qualityVal) params.set("quality", qualityVal);

  const res = await api(`/api/posts/${encodeURIComponent(memory.postId)}/comments?${params.toString()}`);
  const tbody = $("commentsGrid");
  const empty = $("commentsListEmpty");
  if (!res.ok) {
    $("commentsScrapeProgress").classList.add("hidden");
    RETRY.comments = renderCommentsScreen;
    empty.classList.add("hidden");
    tbody.innerHTML = failureState(res, "comments", "these results");
    return;
  }
  const data = res.data;
  $("commentsPostName").textContent = (data.post && data.post.caption ? data.post.caption.slice(0, 90) : "this post") || "this post";
  $("view-comments").querySelectorAll(".btn-export").forEach((b) => b.classList.toggle("hidden", !can("exports.create")));

  // Update pill badge counts
  if (data.counts) {
    if ($("countAll")) $("countAll").textContent = data.counts.all || 0;
    if ($("countLeads")) $("countLeads").textContent = data.counts.leads || 0;
    if ($("countContact")) $("countContact").textContent = data.counts.contact || 0;
    if ($("countHot")) $("countHot").textContent = data.counts.hot || 0;
    if ($("countPricing")) $("countPricing").textContent = data.counts.pricing || 0;
    if ($("countInquiry")) $("countInquiry").textContent = data.counts.inquiry || 0;
  }

  const statusBox = $("commentsScreenStatus");
  const total = data.total_comment_count || 0;
  const scraped = data.scraped_comment_count || 0;
  const hasComments = data.comments && data.comments.length;
  const scrapeProgress = $("commentsScrapeProgress");

  if (data.comments_status === "running") {
    showCommentsScrapeProgress(scraped, total);
  } else {
    scrapeProgress.classList.add("hidden");
  }

  if (data.comments_status === "running" && !hasComments) {
    statusBox.innerHTML = `<div class="summary-line status-running-text"><span class="mini-spinner"></span> Collecting and analyzing comments in real time…</div>`;
    statusBox.classList.remove("hidden");
    tbody.innerHTML = "";
    if (currentView === "comments") _commentsTimer = setTimeout(renderCommentsScreen, 2000);
    return;
  }

  const filterLabels = {
    all: "Showing all collected comments",
    leads: "Showing qualified, high-intent leads",
    contact: "Showing comments with direct phone or email contact",
    hot: "Showing high-priority hot prospects",
    pricing: "Showing pricing, cost & budget inquiries",
    inquiry: "Showing customer questions & inquiries"
  };
  const activeLabel = filterLabels[activeCommentFilter] || "Showing comments";
  const searchNote = searchVal ? ` matching "<b>${esc(searchVal)}</b>"` : "";
  const kf = data.post && data.post.keyword_filter;
  const qualNote = kf && kf.total
    ? `<div class="summary-line qual-line">${ico("filter")} Comment qualification: <b>${esc(kf.matched || 0)}</b> of <b>${esc(kf.total)}</b> comments matched the filter and went to AI analysis${kf.not_matched ? ` · ${esc(kf.not_matched)} skipped` : ""}</div>`
    : "";
  statusBox.innerHTML = `<div class="summary-line">${activeLabel}${searchNote} · <b>${esc(data.total)}</b> of <b>${esc(data.all_count || 0)}</b> comments displayed</div>${qualNote}`;
  statusBox.classList.remove("hidden");

  if (!hasComments) {
    tbody.innerHTML = "";
    empty.classList.remove("hidden");
    empty.querySelector(".empty-sub").textContent =
      searchVal ? `No comments found matching "${searchVal}" under this filter`
      : activeCommentFilter !== "all" ? `No comments under this filter — choose "All comments" to see everything`
      : "No comments on this post yet — collect comments from the Posts screen";
    return;
  }
  empty.classList.add("hidden");

  tbody.innerHTML = data.comments.map((c) => {
    const cid = esc(c.id);
    const priorityClass = { high: "badge-hot", medium: "badge-warm", low: "badge-cold" }[c.priority] || "badge-cold";
    const intent = c.intent ? String(c.intent).replace(/_/g, " ") : "";
    const commentInfo = platformInfo(c.platform);
    const platformCls = PLATFORMS[c.platform] ? c.platform : "unknown";

    const contactChips = [];
    if (c.phone) contactChips.push(`<a class="lc-chip" href="tel:${esc(c.phone)}" data-stop>${ico("phone")} <b>${esc(c.phone)}</b></a>`);
    if (c.email) contactChips.push(`<a class="lc-chip" href="mailto:${esc(c.email)}" data-stop>${ico("mail")} <b>${esc(c.email)}</b></a>`);
    if (c.whatsapp) contactChips.push(`<a class="lc-chip" href="https://wa.me/${esc(String(c.whatsapp).replace(/[^\d]/g, ''))}" target="_blank" rel="noopener" data-stop>${ico("message")} WhatsApp</a>`);
    if (c.budget) contactChips.push(`<span class="lc-chip">${ico("wallet")} Budget: <b>${esc(c.budget)}</b></span>`);
    if (c.requirement) contactChips.push(`<span class="lc-chip">${ico("clipboard")} Req: <b>${esc(c.requirement)}</b></span>`);
    if (c.location) contactChips.push(`<span class="lc-chip">${ico("map-pin")} ${esc(c.location)}</span>`);
    if (intent) contactChips.push(`<span class="lc-chip lc-chip-intent">${ico("target")} ${esc(intent)}</span>`);
    if (c.sentiment && c.sentiment !== "neutral") contactChips.push(`<span class="lc-chip" style="opacity:0.85">${ico("message")} ${esc(c.sentiment)}</span>`);
    const cUrl = safeUrl(c.comment_url);
    const score = Number(c.lead_score) || 0;

    return `<article class="lead-card clickable-card${c.has_contact ? " lead-card-highlight" : ""}" data-open-lead="${cid}" tabindex="0">
      <div class="lc-head">
        <div class="lc-author-wrap">
          <span class="lc-avatar">${esc((c.commenter_name || "?").trim().charAt(0).toUpperCase())}</span>
          <div class="lc-author-info">
            <div class="lc-name">
              ${esc(c.commenter_name || "Commenter")}
              <span class="platform-badge ${platformCls}">${esc(commentInfo.name || c.platform || "Social")}</span>
              ${c.has_contact ? `<span class="badge badge-lead">${ico("phone")} Contact ready</span>` : ""}
            </div>
            <div class="lc-meta">
              ${c.published_date ? `<span>${ico("clock")} ${esc(formatDate(c.published_date))}</span>` : ""}
              ${cUrl ? `<a class="pc-link" href="${esc(cUrl)}" target="_blank" rel="noopener" data-stop>View on ${esc(commentInfo.name || "platform")} ↗</a>` : ""}
            </div>
          </div>
        </div>

        <div class="lc-score-wrap">
          <div class="score-pill" title="AI lead score: ${score}/100">${score}</div>
          ${c.priority ? `<span class="badge ${priorityClass}">${esc(String(c.priority).toUpperCase())}</span>` : ""}
        </div>
      </div>
      ${scoreBar(score)}

      <div class="lc-text-box">
        <p class="lc-full-text">${esc(c.comment_text || "No comment text")}</p>
        ${c.reason ? `<div class="lc-ai-reason">${ico("sparkles")} <b>AI intelligence:</b> ${esc(c.reason)}</div>` : ""}
      </div>

      <div class="lc-chips">
        ${contactChips.length ? contactChips.join("") : `<span class="muted" style="font-size:0.75rem">General comment</span>`}
      </div>

      <div class="lc-foot">
        <button class="btn-ghost" style="font-size:0.76rem;padding:5px 12px" data-open-lead="${cid}">${ico("eye")} View full dossier</button>
      </div>
    </article>`;
  }).join("");

  if (data.comments_status === "running" && currentView === "comments") {
    _commentsTimer = setTimeout(renderCommentsScreen, 3000);
  }
}

function exportLeadsCsv() {
  if (!memory.postId) return;
  const isLeadOnly = activeCommentFilter === "leads";
  downloadCsv(`/api/export/comments.csv?post_id=${encodeURIComponent(memory.postId)}&only_leads=${isLeadOnly}`,
              isLeadOnly ? "leads.csv" : "comments.csv");
}

/** Download a CSV via fetch so errors (402/403/429) are shown nicely. */
async function downloadCsv(url, fallbackName) {
  if (portal.user && !can("exports.create")) {
    toast("Your role can't create exports. Ask your workspace admin.", "error");
    return false;
  }
  toast("Preparing export…", "info");
  let res;
  try {
    res = await apiFetch(url);
  } catch (_) {
    toast("You appear to be offline — export not downloaded", "error");
    return false;
  }
  if (!res.ok) {
    let data = {};
    try { data = await res.json(); } catch (_) { data = {}; }
    if (handleEntitlementError(res.status, data)) return false;
    toast(errText(data, "Export failed"), "error");
    return false;
  }
  const blob = await res.blob();
  const cd = res.headers.get("Content-Disposition") || "";
  const m = /filename="?([^";]+)"?/i.exec(cd);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = m ? m[1] : fallbackName;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1500);
  if (res.headers.get("X-Export-Truncated")) {
    toast(`Export truncated — ${res.headers.get("X-Export-Total")} rows exist; the file holds the first 50,000`, "warn");
  } else {
    toast("Export downloaded", "success");
  }
  refreshSummary();
  if (currentView === "exports") loadExports();
  return true;
}

// ── LEAD DETAIL MODAL ────────────────────────────────────────────────────
const LEAD_STATUS_LABELS = {
  new: "New", contacted: "Contacted", qualified: "Qualified",
  follow_up: "Follow-up", converted: "Converted", lost: "Lost",
  disqualified: "Disqualified", archived: "Archived"
};
const LEAD_STATUS_CLASS = {
  new: "sp-info", contacted: "sp-pending", qualified: "sp-completed", follow_up: "sp-demo",
  converted: "sp-active", lost: "sp-cancelled", disqualified: "sp-failed", archived: "sp-cancelled"
};
function leadStatusPill(s) {
  const st = s || "new";
  return `<span class="status-pill ${LEAD_STATUS_CLASS[st] || "sp-neutral"}">${esc(LEAD_STATUS_LABELS[st] || st)}</span>`;
}

let _lastFocus = null;
async function openLeadDetail(commentId, event) {
  if (event) event.stopPropagation();
  _lastFocus = document.activeElement;
  let res = await api(`/api/comments/${encodeURIComponent(commentId)}`);
  if (!res.ok) { toast(res.status === 404 ? "That lead is no longer available" : "Could not load lead details", "error"); return; }
  // The comments view lists RAW comment ids; /api/comments/{id} then returns
  // the raw comment only. Resolve its AI analysis (scores, lifecycle, notes).
  if (res.data.comment && res.data.comment.id === res.data.id && res.data.lead_quality === "none") {
    const ai = await api(`/api/me/leads?comment_ref=${encodeURIComponent(commentId)}&page_size=1`);
    const hit = ai.ok && (ai.data.items || [])[0];
    if (hit && hit.id && hit.id !== commentId) {
      const full = await api(`/api/comments/${encodeURIComponent(hit.id)}`);
      if (full.ok) res = full;
    }
  }
  const d = res.data;
  const page = d.page || {};
  const post = d.post || {};
  const comment = d.comment || {};
  const leadId = esc(d.id || d._id || commentId);
  const isLead = d.is_lead !== undefined ? d.is_lead : true;
  // lifecycle/notes live on the AI analysis doc (ai_comments), never on a raw comment
  const isRawComment = Boolean(d.comment && d.comment.id === d.id);
  const canManage = can("leads.manage") && !isRawComment;
  const quality = { hot: `${ico("flame")} Hot`, warm: `${ico("zap")} Warm`, cold: `${ico("snowflake")} Cold`, none: "—" }[d.lead_quality] || "—";
  const intent = d.intent ? String(d.intent).replace(/_/g, " ") : "—";

  const currentStatus = d.lead_status || "new";
  const priorityLabels = { high: '<span class="prio-dot prio-high" aria-hidden="true"></span>High', medium: '<span class="prio-dot prio-medium" aria-hidden="true"></span>Medium', low: '<span class="prio-dot prio-low" aria-hidden="true"></span>Low' };
  const currentPriority = d.lead_priority || d.priority || "low";
  const priorityLabel = priorityLabels[currentPriority] || currentPriority;

  const validTransitions = {
    new: ["contacted", "qualified", "follow_up", "disqualified", "lost", "archived"],
    contacted: ["qualified", "follow_up", "lost", "archived"],
    qualified: ["follow_up", "converted", "lost", "archived"],
    follow_up: ["contacted", "qualified", "converted", "lost", "archived"],
    converted: ["archived"],
    lost: ["archived"],
    disqualified: ["archived"],
    archived: []
  };
  const nextStatuses = validTransitions[currentStatus] || [];
  const statusOptions = nextStatuses.map((s) =>
    `<option value="${s}">${esc(LEAD_STATUS_LABELS[s] || s)}</option>`).join("");

  // Lifecycle: the status path new → … with the current step highlighted
  const LIFECYCLE = ["new", "contacted", "qualified", "follow_up", "converted"];
  const lcIndex = LIFECYCLE.indexOf(currentStatus);
  const lifecycleHtml = `<ol class="lifecycle" aria-label="Lead lifecycle">${LIFECYCLE.map((s, i) =>
    `<li class="${i < lcIndex ? "done" : i === lcIndex ? "current" : ""}"${i === lcIndex ? ' aria-current="step"' : ""}>${esc(LEAD_STATUS_LABELS[s])}</li>`).join("")}</ol>${lcIndex < 0 ? `<div class="lifecycle-terminal">Current: ${leadStatusPill(currentStatus)}</div>` : ""}`;

  // Notes section
  const notes = (d.notes || []).filter(Boolean);
  const notesHtml = notes.length > 0
    ? notes.map((n, i) => `
      <div class="note-item">
        <div class="note-head">
          <div class="note-meta">${esc(n.author || "user")} · ${fmtTime(n.created_at)}</div>
          ${canManage ? `<button class="btn-ghost btn-xs btn-danger-text" data-lead-note-del="${leadId}" data-index="${i}">Delete</button>` : ""}
        </div>
        <div class="note-text">${esc(n.text)}</div>
      </div>`).join("")
    : '<div class="muted-line">No notes yet</div>';

  // Follow-ups section
  const followUps = d.follow_ups || [];
  const followUpsHtml = followUps.length > 0
    ? followUps.map((fu, i) => {
      const fuStatus = fu.status || "pending";
      const canUpdate = fuStatus === "pending" && canManage;
      return `
        <div class="note-item">
          <div class="note-head">
            <b>${esc(fu.title)}</b>
            ${statusPill(fuStatus === "pending" ? "pending" : fuStatus === "completed" ? "completed" : "cancelled", fuStatus)}
          </div>
          ${fu.due_at ? `<div class="note-meta">Due: ${fmtTime(fu.due_at)}</div>` : ""}
          ${fu.notes ? `<div class="note-text">${esc(fu.notes)}</div>` : ""}
          ${canUpdate ? `<div class="note-actions">
            <button class="btn-ghost btn-xs" data-fu-update="${leadId}" data-index="${i}" data-status="completed">Complete</button>
            <button class="btn-ghost btn-xs btn-danger-text" data-fu-update="${leadId}" data-index="${i}" data-status="cancelled">Cancel</button>
          </div>` : ""}
        </div>`;
    }).join("")
    : '<div class="muted-line">No follow-ups</div>';

  // Status history
  const hist = d.status_history || [];
  const historyHtml = hist.length > 0
    ? hist.slice(-10).reverse().map((h) => `
      <div class="history-row">
        ${leadStatusPill(h.from_status)} → ${leadStatusPill(h.to_status)}
        <span class="note-meta">${fmtTime(h.changed_at)}${h.changed_by ? ` by ${esc(h.changed_by)}` : ""}</span>
        ${h.reason ? `<div class="note-meta">Reason: ${esc(h.reason)}</div>` : ""}
      </div>`).join("")
    : '<div class="muted-line">No status changes yet</div>';

  // Score visualisation
  const score = Math.max(0, Math.min(100, Number(d.lead_score) || 0));
  const conf = d.confidence != null ? Math.round(Number(d.confidence) * 100) : null;
  const tier = score >= 80 ? "hot" : score >= 50 ? "warm" : "cold";
  const scoreHtml = `<div class="score-visual">
      <div class="score-ring score-${tier}" style="--p:${score}" role="img" aria-label="Lead score ${score} out of 100"><span>${score}</span></div>
      <div class="score-facts">
        <div><span class="detail-label">Quality</span> ${quality}</div>
        <div><span class="detail-label">Priority</span> ${priorityLabels[currentPriority] ? priorityLabel : esc(priorityLabel)}</div>
        <div><span class="detail-label">AI confidence</span> ${conf != null ? conf + "%" : "—"}</div>
        ${conf != null ? `<span class="score-meter score-${tier}"><span class="score-meter-fill" style="width:${conf}%"></span></span>` : ""}
      </div>
    </div>`;

  $("leadDetailTitle").textContent = "Lead intelligence — " + (d.commenter_name || "Prospect");
  const detailInfo = platformInfo(d.platform);
  const cUrl = safeUrl(d.comment_url);
  const web = safeUrl(d.website);
  const srcPage = safeUrl(page.facebook_url);
  const srcPost = safeUrl(post.post_url);
  const author = safeUrl(comment.author_profile_url);
  $("leadDetailContent").innerHTML = `
    <div class="lead-detail-grid">
      <div class="detail-block detail-block-wide">
        <div class="detail-label">Original comment</div>
        <div class="detail-value quote-box">${esc(d.comment_text || "—")}</div>
        ${cUrl ? `<div class="mt-8"><a class="pc-link" href="${esc(cUrl)}" target="_blank" rel="noopener">Open original comment on ${esc(detailInfo.name || "platform")} ↗</a></div>` : ""}
        ${d.reason ? `<div class="detail-reason">${ico("sparkles")} <b>AI rationale:</b> ${esc(d.reason)} ${d.analyzed_by ? `(${esc(d.analyzed_by)})` : ""}</div>` : ""}
        ${!isLead ? `<div class="detail-reason">This comment was not qualified as a lead.</div>` : ""}
      </div>

      <div class="detail-block detail-block-wide">
        <div class="detail-label">Lead score</div>
        ${scoreHtml}
      </div>

      <div class="detail-block">
        <div class="detail-label">Phone number</div>
        <div class="detail-value">${d.phone ? `<a href="tel:${esc(d.phone)}" class="contact-pill">${ico("phone")} ${esc(d.phone)}</a>` : "—"}</div>
        <div class="detail-label">WhatsApp</div>
        <div class="detail-value">${d.whatsapp ? `<a href="https://wa.me/${esc(String(d.whatsapp).replace(/[^\d]/g, ""))}" target="_blank" rel="noopener" class="contact-pill">${ico("message")} Direct chat</a>` : "—"}</div>
        <div class="detail-label">Email</div>
        <div class="detail-value">${d.email ? `<a href="mailto:${esc(d.email)}" class="contact-pill">${ico("mail")} ${esc(d.email)}</a>` : "—"}</div>
        <div class="detail-label">Website</div>
        <div class="detail-value">${web ? `<a href="${esc(web)}" target="_blank" rel="noopener" class="pc-link">${esc(web)} ↗</a>` : "—"}</div>
      </div>

      <div class="detail-block">
        <div class="detail-label">Budget</div>
        <div class="detail-value">${esc(d.budget || "—")}</div>
        <div class="detail-label">Requirement / inquiry</div>
        <div class="detail-value">${esc(d.requirement || "—")}</div>
        <div class="detail-label">Location</div>
        <div class="detail-value">${esc(d.location || "—")}</div>
        <div class="detail-label">Buying intent</div>
        <div class="detail-value"><span class="badge badge-ai">${esc(intent)}</span></div>
        <div class="detail-label">Urgency</div>
        <div class="detail-value">${esc(d.urgency || "—")}</div>
      </div>

      <div class="detail-block">
        <div class="detail-label">Platform</div>
        <div class="detail-value">${detailInfo.icon ? detailInfo.icon + " " : ""}${esc(detailInfo.name || "—")}</div>
        <div class="detail-label">Assigned to</div>
        <div class="detail-value">${d.assigned_user_id && portal.user && d.assigned_user_id === portal.user.user_id ? '<span class="badge badge-lead">You</span>' : esc(d.assigned_to || "—")}</div>
      </div>

      <div class="detail-block detail-block-wide">
        <div class="detail-label">Lifecycle</div>
        ${lifecycleHtml}
        ${canManage && nextStatuses.length > 0 ? `
          <div class="inline-form">
            <label class="sr-only" for="leadStatusSelect">Change status</label>
            <select id="leadStatusSelect" class="form-input form-input-sm">
              <option value="">Change status…</option>
              ${statusOptions}
            </select>
            <button class="btn-ghost" data-lead-status="${leadId}">Update</button>
          </div>` : canManage ? '<div class="muted-line">Terminal state — no transitions available</div>' : ""}
      </div>

      <div class="detail-block detail-block-wide">
        <div class="detail-label">Notes</div>
        <div class="detail-value">
          <div id="leadNotesContainer">${notesHtml}</div>
          ${canManage ? `<div class="inline-form">
            <label class="sr-only" for="leadNoteInput">Add a note</label>
            <input type="text" id="leadNoteInput" class="form-input form-input-sm" placeholder="Add a note…" maxlength="2000">
            <button class="btn-ghost" data-lead-note-add="${leadId}">Add</button>
          </div>` : ""}
        </div>
      </div>

      <div class="detail-block detail-block-wide">
        <div class="detail-label">Follow-ups</div>
        <div class="detail-value">
          <div id="leadFollowUpsContainer">${followUpsHtml}</div>
          ${canManage ? `<div class="inline-form">
            <label class="sr-only" for="leadFUTitle">Follow-up title</label>
            <input type="text" id="leadFUTitle" class="form-input form-input-sm" placeholder="Follow-up title…" maxlength="200">
            <label class="sr-only" for="leadFUDue">Due date</label>
            <input type="datetime-local" id="leadFUDue" class="form-input form-input-sm">
            <button class="btn-ghost" data-lead-fu-add="${leadId}">Add</button>
          </div>` : ""}
        </div>
      </div>

      <div class="detail-block detail-block-wide">
        <div class="detail-label">Status history</div>
        <div class="detail-value history-box">${historyHtml}</div>
      </div>

      <div class="detail-block detail-block-wide">
        <div class="detail-label">Source — page / profile</div>
        <div class="detail-value">${esc(page.page_name || "—")}${srcPage ? ` · <a class="pc-link" href="${esc(srcPage)}" target="_blank" rel="noopener">Open source page ↗</a>` : ""}</div>
        <div class="detail-label">Source — post</div>
        <div class="detail-value muted-line">${esc(post.caption || "—")}${srcPost ? ` · <a class="pc-link" href="${esc(srcPost)}" target="_blank" rel="noopener">Open post ↗</a>` : ""}</div>
        <div class="detail-label">Commenter profile</div>
        <div class="detail-value">${author ? `<a class="pc-link" href="${esc(author)}" target="_blank" rel="noopener">${esc(author)} ↗</a>` : "—"}</div>
      </div>
    </div>`;
  const modal = $("leadDetailModal");
  modal.classList.remove("hidden");
  const closeBtn = modal.querySelector(".modal-close");
  if (closeBtn) closeBtn.focus();
}

async function leadMutation(url, method, body, okMsg, leadId) {
  const res = await api(url, { method, body });
  if (!res.ok) { toast(errText(res.data, "Update failed"), "error"); return; }
  toast(okMsg, "success");
  openLeadDetail(leadId);
  if (currentView === "leads") loadLeads();
}
async function updateLeadStatus(leadId) {
  const select = $("leadStatusSelect");
  if (!select || !select.value) { toast("Choose a status first", "info"); return; }
  if (["lost", "disqualified", "archived"].includes(select.value) &&
      !(await confirmAction({ title: `Mark this lead as ${LEAD_STATUS_LABELS[select.value].toLowerCase()}?`, message: "This is a closing status — the lead leaves your active pipeline.", confirmLabel: "Update status", danger: true }))) return;
  await leadMutation(`/api/leads/${encodeURIComponent(leadId)}`, "PATCH", { lead_status: select.value }, "Status updated", leadId);
}
async function addLeadNote(leadId) {
  const input = $("leadNoteInput");
  if (!input || !input.value.trim()) { if (input) input.focus(); return; }
  await leadMutation(`/api/leads/${encodeURIComponent(leadId)}/notes`, "POST", { text: input.value.trim() }, "Note added", leadId);
}
async function addLeadFollowUp(leadId) {
  const title = $("leadFUTitle");
  const due = $("leadFUDue");
  if (!title || !title.value.trim()) { if (title) title.focus(); return; }
  const body = { title: title.value.trim() };
  if (due && due.value) body.due_at = new Date(due.value).toISOString();
  await leadMutation(`/api/leads/${encodeURIComponent(leadId)}/follow-ups`, "POST", body, "Follow-up added", leadId);
}
async function deleteLeadNote(leadId, noteIndex) {
  if (!(await confirmAction({ title: "Delete this note?", message: "The note is removed from this lead for everyone.", confirmLabel: "Delete note", danger: true }))) return;
  await leadMutation(`/api/leads/${encodeURIComponent(leadId)}/notes/${noteIndex}`, "DELETE", undefined, "Note deleted", leadId);
}
async function updateFollowUpStatus(leadId, fuIndex, newStatus) {
  if (newStatus === "cancelled" && !(await confirmAction({ title: "Cancel this follow-up?", message: "It will be marked as cancelled.", confirmLabel: "Cancel follow-up", danger: true }))) return;
  await leadMutation(`/api/leads/${encodeURIComponent(leadId)}/follow-ups/${fuIndex}`, "PATCH", { status: newStatus }, `Follow-up ${newStatus}`, leadId);
}

function closeLeadDetails() {
  $("leadDetailModal").classList.add("hidden");
  if (_lastFocus && document.contains(_lastFocus)) { try { _lastFocus.focus(); } catch (_) {} }
}

// ═════════════════════════════════════════════════════════════════════════
// ENTITLEMENTS — friendly, specific 402/403 states with an Upgrade action
// ═════════════════════════════════════════════════════════════════════════
const METRIC_LABELS = {
  monthly_searches: "searches", monthly_ai_analyses: "AI analyses", monthly_exports: "exports",
  monthly_posts: "posts", monthly_comments: "comments", team_members: "team members",
  posts_per_search: "posts per search", comments_per_post: "comments per post", max_leads: "leads",
  tokens: "tokens",
};
const FEATURE_NAMES = {
  csv_export: "CSV exports", ai_analysis: "AI lead analysis", lead_scoring: "Lead scoring",
  url_search: "URL search", facebook: "Facebook search", instagram: "Instagram search",
  youtube: "YouTube search", linkedin: "LinkedIn search", custom_branding: "Custom branding",
};
const ENTITLEMENT_COPY = {
  DEMO_EXPIRED: {
    icon: "clock", title: "Your demo has ended",
    msg: () => "Your free demo period is over. Choose a plan to keep finding leads — your searches and leads are kept safe.",
  },
  TOKENS_EXHAUSTED: {
    icon: "coins", title: "You're out of tokens",
    msg: (d) => d.needed != null
      ? `This action needs ${d.needed} token${d.needed === 1 ? "" : "s"} and your workspace has ${d.remaining || 0} left.`
      : "Your workspace has used all of its tokens.",
  },
  TOKENS_EXPIRED: {
    icon: "clock", title: "Your tokens have expired",
    msg: () => "The tokens on your workspace have expired. Choose a plan to get a fresh allowance.",
  },
  QUOTA_EXCEEDED: {
    icon: "chart", title: "Monthly limit reached",
    msg: (d) => d.limit != null
      ? `You've used ${d.used} of ${d.limit} ${METRIC_LABELS[d.metric] || String(d.metric || "units").replace(/_/g, " ")} this month.`
      : (d.message || "You've reached your plan's monthly limit."),
  },
  PLAN_LIMIT: {
    icon: "alert", title: "Above your plan's limit",
    msg: (d) => d.message || `Your plan allows up to ${d.limit} ${METRIC_LABELS[d.metric] || "units"}.`,
  },
  FEATURE_NOT_AVAILABLE: {
    icon: "lock", title: "Not included in your plan",
    msg: (d) => `${FEATURE_NAMES[d.feature] || String(d.feature || "This feature").replace(/_/g, " ")} isn't included in ${d.plan || "your current plan"}.`,
  },
  ORGANIZATION_INACTIVE: {
    icon: "pause", title: "Your workspace is inactive",
    msg: (d) => (d.message ? d.message + " " : "") + "Reactivate a plan or contact support to continue.",
  },
};

function upgradeButtonHtml(code) {
  if (canManageBilling()) return `<button type="button" class="btn-primary btn-sm" data-action="upgrade">Upgrade plan</button>`;
  return `<span class="ask-admin">Ask your workspace admin to upgrade</span>`;
}

/** Show the specific entitlement modal. Returns true when handled. */
function showQuotaExceededModal(detail) {
  const d = (detail && typeof detail === "object") ? detail : {};
  const code = d.code || d.error || "QUOTA_EXCEEDED";
  const copy = ENTITLEMENT_COPY[code] || ENTITLEMENT_COPY.QUOTA_EXCEEDED;
  const modal = $("quotaExceededModal");
  if (!modal) return false;
  $("quotaExceededIcon").innerHTML = ico(copy.icon, "ico-entitlement");
  $("quotaExceededTitle").textContent = copy.title;
  $("quotaExceededMsg").textContent = copy.msg(d);
  const stats = $("quotaExceededStats");
  const statItems = [];
  if (d.used != null && d.limit != null) statItems.push([d.used, "used"], [d.limit, "limit"]);
  if (d.remaining != null && code.startsWith("TOKENS")) statItems.push([d.remaining, "tokens left"]);
  if (d.needed != null) statItems.push([d.needed, "needed"]);
  if (d.requested != null && d.limit != null && code === "PLAN_LIMIT") statItems.splice(0, statItems.length, [d.requested, "requested"], [d.limit, "your plan"]);
  stats.innerHTML = statItems.map(([v, l]) => `<div class="quota-stat-item"><span class="quota-stat-val">${esc(fmt(v))}</span><span class="quota-stat-lbl">${esc(l)}</span></div>`).join("");
  stats.classList.toggle("hidden", !statItems.length);
  const cta = $("quotaExceededCta");
  const hint = $("quotaExceededHint");
  if (canManageBilling()) {
    cta.textContent = code === "PLAN_LIMIT" ? "See plans with higher limits →" : "View plans & upgrade →";
    hint.textContent = code === "PLAN_LIMIT" ? "Or lower the numbers and try again." : "";
  } else {
    cta.textContent = "See plans";
    hint.textContent = "Only your workspace owner or billing admin can change the plan — ask them to upgrade.";
  }
  modal.classList.remove("hidden");
  cta.focus();
  return true;
}

function closeQuotaModal() {
  const modal = $("quotaExceededModal");
  if (modal) modal.classList.add("hidden");
}

/** Handle a structured entitlement / limit error. Returns true when shown. */
function handleEntitlementError(status, data) {
  const code = errCode(data);
  const detail = (data && data.detail) || {};
  if (ENTITLEMENT_COPY[code] && (status === 402 || status === 403)) {
    showQuotaExceededModal(Object.assign({ code }, detail));
    refreshSummary();
    return true;
  }
  if (status === 402) { showQuotaExceededModal(Object.assign({ code: "QUOTA_EXCEEDED" }, typeof detail === "object" ? detail : { message: String(detail) })); return true; }
  if (status === 429) { toast(errText(data, "Too many requests — please wait a moment."), "error"); return true; }
  if (status === 403 && (code === "feature_disabled" || code === "platform_disabled" || code === "export_limit")) {
    toast(errText(data, "This is currently disabled by the administrator."), "error");
    return true;
  }
  if (status === 403 && code === "permission_denied") {
    toast("Your role doesn't allow this action. Ask your workspace admin.", "error");
    return true;
  }
  return false;
}

// ═════════════════════════════════════════════════════════════════════════
// WORKSPACE, TEAM (workspace admins) & BILLING
// ═════════════════════════════════════════════════════════════════════════

let currentTenantOrg = null;
let currentTenantSub = null;

// ── Workspace Modal & Settings ───────────────────────────────────────────
function openWorkspaceModal() {
  const modal = $("workspaceModal");
  if (!modal) return;
  closeMenus();
  modal.classList.remove("hidden");
  loadWorkspaceData();
  setTimeout(() => { const i = $("wsNameInput"); if (i) i.focus(); }, 30);
}

function closeWorkspaceModal() {
  const modal = $("workspaceModal");
  if (modal) modal.classList.add("hidden");
}

async function loadWorkspaceData() {
  const res = await api("/api/organizations/current");
  if (!res.ok || !res.data.organization) return;
  const org = res.data.organization;
  currentTenantOrg = org;
  const wsName = $("topbarWorkspaceName");
  if (wsName) wsName.textContent = org.display_name || org.name || "Workspace";
  if ($("wsNameInput")) $("wsNameInput").value = org.name || "";
  if ($("wsWebsiteInput")) $("wsWebsiteInput").value = org.website || "";
  if ($("wsTimezoneInput")) $("wsTimezoneInput").value = org.timezone || "UTC";
  if ($("wsCurrencyInput")) $("wsCurrencyInput").value = org.currency || "USD";
  if ($("wsCompanyName")) $("wsCompanyName").value = (org.branding && org.branding.company_name) || org.display_name || "";
}

async function saveWorkspaceSettings(event) {
  event.preventDefault();
  const btn = $("btnSaveWorkspace");
  if (btn) { btn.disabled = true; btn.textContent = "Saving…"; }
  const body = {
    name: $("wsNameInput") ? $("wsNameInput").value.trim() : "",
    website: $("wsWebsiteInput") ? $("wsWebsiteInput").value.trim() : "",
    timezone: $("wsTimezoneInput") ? $("wsTimezoneInput").value : "UTC",
    currency: $("wsCurrencyInput") ? $("wsCurrencyInput").value : "USD",
    company_name: $("wsCompanyName") ? $("wsCompanyName").value.trim() : "",
  };
  const res = await api("/api/organizations/current", { method: "PATCH", body });
  if (btn) { btn.disabled = false; btn.textContent = "Save changes"; }
  if (!res.ok) { toast(errText(res.data, "Failed to update workspace"), "error"); return; }
  toast("Workspace settings saved", "success");
  closeWorkspaceModal();
  loadWorkspaceData();
}

// ── Team Management Modal ────────────────────────────────────────────────
function openTeamModal() {
  const modal = $("teamModal");
  if (!modal) return;
  closeMenus();
  modal.classList.remove("hidden");
  const invite = $("teamInviteCard");
  if (invite) invite.classList.toggle("hidden", !can("members.invite"));
  loadTeamData();
}

function closeTeamModal() {
  const modal = $("teamModal");
  if (modal) modal.classList.add("hidden");
}

const ROLE_LABELS = { owner: "Owner", admin: "Admin", manager: "Manager", member: "User", viewer: "Viewer" };

async function loadTeamData() {
  const list = $("teamMembersList");
  const countEl = $("teamMemberCount");
  if (list) list.innerHTML = `<tr><td colspan="4" class="td-empty">Loading team…</td></tr>`;
  const res = await api("/api/organizations/current/team");
  if (!res.ok) {
    if (list) list.innerHTML = `<tr><td colspan="4" class="td-empty">${res.status === 403 ? "Your role can't view the team." : "Failed to load the team."}</td></tr>`;
    return;
  }
  const members = res.data.members || [];
  const invitations = res.data.invitations || [];
  if (countEl) countEl.textContent = String(members.length);
  const canUpdate = can("members.update");
  const canDelete = can("members.delete");
  const me = portal.user ? portal.user.user_id : "";

  if (list) {
    if (!members.length) {
      list.innerHTML = `<tr><td colspan="4" class="td-empty">No team members found.</td></tr>`;
    } else {
      list.innerHTML = members.map((m) => {
        const isOwner = m.role === "owner";
        const isMe = String(m.user_id) === String(me);
        const uid = esc(m.user_id);
        const roleCell = (!canUpdate || isOwner || isMe)
          ? `<span class="badge badge-muted">${esc(ROLE_LABELS[m.role] || m.role)}</span>`
          : `<select class="saas-select" data-member-role="${uid}" aria-label="Role for ${esc(m.email)}">
              ${["admin", "manager", "member", "viewer"].map((r) => `<option value="${r}" ${m.role === r ? "selected" : ""}>${ROLE_LABELS[r]}</option>`).join("")}
            </select>`;
        return `
          <tr>
            <td>
              <div class="cell-strong">${esc(m.name || m.email)}${isMe ? " (you)" : ""}</div>
              <div class="cell-mono">${esc(m.email)}</div>
            </td>
            <td>${roleCell}</td>
            <td>${statusPill(m.status === "active" ? "active" : m.status === "suspended" ? "suspended" : "cancelled", m.status)}</td>
            <td class="ta-right">
              ${isOwner ? '<span class="cell-mono">Primary owner</span>' : (canDelete && !isMe ? `<button type="button" class="saas-btn saas-btn-danger saas-btn-sm" data-member-remove="${uid}">Remove</button>` : "")}
            </td>
          </tr>`;
      }).join("");
    }
  }

  const pendingWrap = $("pendingInvitationsWrap");
  const pendingList = $("pendingInvitationsList");
  if (pendingWrap && pendingList) {
    if (invitations.length) {
      const canInvite = can("members.invite");
      pendingWrap.classList.remove("hidden");
      pendingList.innerHTML = invitations.map((inv) => `
        <tr>
          <td><span class="cell-strong">${esc(inv.email)}</span> <span class="badge badge-muted">${esc(ROLE_LABELS[inv.role] || inv.role)}</span></td>
          <td class="cell-mono">Expires in ${Math.max(0, Math.round((new Date(inv.expires_at) - Date.now()) / 86400000))}d</td>
          <td class="ta-right">
            ${canInvite ? `<button type="button" class="saas-btn saas-btn-secondary saas-btn-sm" data-invite-resend="${esc(inv.id)}">Resend</button>
            <button type="button" class="saas-btn saas-btn-danger saas-btn-sm" data-invite-revoke="${esc(inv.id)}">Revoke</button>` : ""}
          </td>
        </tr>`).join("");
    } else {
      pendingWrap.classList.add("hidden");
      pendingList.innerHTML = "";
    }
  }
}

async function handleSendInvitation(event) {
  event.preventDefault();
  const emailInput = $("inviteEmailInput");
  const roleSelect = $("inviteRoleSelect");
  const btn = $("btnSendInvite");
  if (!emailInput || !emailInput.value.trim()) return;
  const email = emailInput.value.trim();
  const role = roleSelect ? roleSelect.value : "member";
  if (btn) { btn.disabled = true; btn.textContent = "Inviting…"; }
  const res = await api("/api/organizations/current/invitations", { method: "POST", body: { email, role } });
  if (btn) { btn.disabled = false; btn.textContent = "Send invite →"; }
  if (!res.ok) {
    if (handleEntitlementError(res.status, res.data)) return;
    toast(errText(res.data, "Failed to send invitation"), "error");
    return;
  }
  toast(`Invitation sent to ${email}`, "success");
  emailInput.value = "";
  loadTeamData();
}

async function handleRevokeInvitation(invitationId) {
  if (!(await confirmAction({ title: "Revoke this invitation?", message: "The recipient will no longer be able to join.", confirmLabel: "Revoke", danger: true }))) return;
  const res = await api(`/api/organizations/current/invitations/${encodeURIComponent(invitationId)}`, { method: "DELETE" });
  if (!res.ok) { toast(errText(res.data, "Could not revoke invitation"), "error"); return; }
  toast("Invitation revoked", "info");
  loadTeamData();
}

async function handleResendInvitation(invitationId) {
  const res = await api(`/api/organizations/current/invitations/${encodeURIComponent(invitationId)}/resend`, { method: "POST" });
  if (!res.ok) { toast(errText(res.data, "Could not resend invitation"), "error"); return; }
  toast("Invitation resent", "success");
  loadTeamData();
}

async function handleUpdateMemberRole(userId, newRole) {
  const res = await api(`/api/organizations/current/members/${encodeURIComponent(userId)}`, { method: "PATCH", body: { role: newRole } });
  if (!res.ok) toast(errText(res.data, "Could not update role"), "error");
  else toast("Member role updated", "success");
  loadTeamData();
}

async function handleRemoveMember(userId) {
  if (!(await confirmAction({ title: "Remove this member?", message: "They lose access to this workspace immediately.", confirmLabel: "Remove member", danger: true }))) return;
  const res = await api(`/api/organizations/current/members/${encodeURIComponent(userId)}`, { method: "DELETE" });
  if (!res.ok) { toast(errText(res.data, "Could not remove member"), "error"); return; }
  toast("Member removed", "info");
  loadTeamData();
}

// ── Plans & billing (view) ───────────────────────────────────────────────
function openBillingModal() { navigateToView("billing", { userInitiated: true }); }
function closeBillingModal() { navigateToView("dashboard"); }

const SUB_STATUS_LABELS = {
  demo: "Demo",
  active: "Active",
  trialing: "Trial",
  pending_payment: "Awaiting payment",
  payment_received: "Payment received",
  pending_admin_confirmation: "Pending confirmation",
  suspended: "Suspended",
  expired: "Expired",
  cancelled: "Cancelled",
};

function formatMoney(amount, currency) {
  const value = Number(amount || 0);
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency: currency || "USD",
      maximumFractionDigits: value % 1 ? 2 : 0 }).format(value);
  } catch (_) {
    return `${value} ${currency || ""}`.trim();
  }
}

function meterClass(pct) { return pct >= 100 ? "danger" : pct >= 80 ? "warn" : ""; }

function setMeter(valId, fillId, m) {
  if (!m) return;
  const used = m.used || 0;
  const limit = m.limit || 0;
  const pct = limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  if ($(valId)) $(valId).textContent = `${fmt(used)} / ${limit >= 1e9 ? "∞" : limit ? fmt(limit) : "—"}`;
  if ($(fillId)) {
    $(fillId).style.width = `${pct}%`; $(fillId).className = "quota-fill " + meterClass(pct);
    if ($(fillId).parentElement) $(fillId).parentElement.setAttribute("aria-valuenow", String(pct));
  }
}

async function ensurePlans() {
  if (portal.plans.length) return portal.plans;
  const res = await api("/api/billing/plans");
  if (res.ok) portal.plans = res.data.plans || [];
  return portal.plans;
}

let _billingLoading = false;
async function loadBillingData() {
  if (_billingLoading) return;
  _billingLoading = true;
  try {
    const canView = can("org_billing.view");
    const canManage = canManageBilling();
    const [usageRes, subRes, invRes] = await Promise.all([
      api("/api/billing/usage"),
      canView ? api("/api/billing/subscription") : Promise.resolve(null),
      canView ? api("/api/billing/invoices") : Promise.resolve(null),
    ]);
    const plans = await ensurePlans();
    const usage = usageRes && usageRes.ok ? usageRes.data.usage : null;
    const sub = subRes && subRes.ok ? subRes.data.subscription : null;
    currentTenantSub = sub;
    portal.subscription = sub;
    const summarySub = (portal.summary && portal.summary.subscription) || null;

    // Plan identity (plan data comes only from the plan system)
    const plan = (sub && sub.plan) || (usage && usage.plan) || {};
    const status = sub ? (sub.is_demo ? "demo" : sub.status) : (summarySub ? (summarySub.is_demo ? "demo" : summarySub.status) : (plan.is_demo ? "demo" : ""));
    const isActive = status === "active";
    const planName = plan.name || (summarySub && summarySub.plan_name) || "No plan";
    const badge = $("topbarPlanBadge");
    if (badge) { badge.textContent = planName; badge.className = `plan-badge-pill ${esc(String(plan.slug || "").toLowerCase())}`; }

    $("billingPlanName").textContent = plan.is_demo ? "Demo" : planName;
    const statusEl = $("billingStatusBadge");
    const [pillCls, pillLabel] = STATUS_PILL[status] || ["neutral", SUB_STATUS_LABELS[status] || (status ? String(status).replace(/_/g, " ") : "No active plan")];
    statusEl.className = "status-pill sp-" + pillCls;
    statusEl.textContent = SUB_STATUS_LABELS[status] || pillLabel;

    const cycle = sub && sub.billing_cycle === "yearly" ? "year" : "month";
    const amount = sub && sub.amount != null ? sub.amount : plan.price_monthly;
    const price = plan.is_demo ? "Free demo" : (amount ? formatMoney(amount, (sub && sub.currency) || plan.currency) : "Free");
    $("billingPlanPrice").innerHTML = `${esc(price)} <span class="price-period">${amount && !plan.is_demo ? "/ " + cycle : ""}</span>`;

    let details = "";
    if (sub) {
      const endStr = sub.current_period_end ? new Date(sub.current_period_end).toLocaleDateString() : "";
      if (isActive && endStr) details = `Next billing on ${endStr}`;
      if (isActive && sub.cancel_at_period_end && endStr) details = `Cancels on ${endStr}`;
      if (!isActive && status !== "demo") details = "This plan is not active.";
    }
    const demo = usage && usage.demo;
    if (demo) details = demo.expired ? "Your demo has ended." : `Demo ends ${new Date(demo.expires_at).toLocaleString()}`;
    if (!canView) details = (details ? details + " · " : "") + "Billing details are visible to your workspace admins.";
    $("billingSubDetails").textContent = details;

    // Pending confirmation state (never claims the plan is active)
    const pending = (sub && sub.pending) || (summarySub && summarySub.pending) || null;
    const pendingBox = $("billingPending");
    const pendingPlanId = pending ? (pending.plan_id || (pending.plan && pending.plan.slug)) : null;
    if (pending) {
      const pn = (pending.plan && pending.plan.name) || pending.plan_name || pending.plan_id || "Your new plan";
      const st = SUB_STATUS_LABELS[pending.status] || String(pending.status || "pending").replace(/_/g, " ");
      const statusLink = pending.checkout_session_id && canView
        ? `<a class="btn-ghost btn-sm" href="/billing/status?session=${encodeURIComponent(pending.checkout_session_id)}">Payment status</a>` : "";
      pendingBox.className = "inline-alert alert-info";
      pendingBox.innerHTML = `<div><strong>Pending confirmation — ${esc(pn)}</strong><div>${esc(st)}. Your current plan stays in effect until our team confirms the payment; you'll be notified when it's active.</div></div>${statusLink}`;
    } else {
      pendingBox.className = "hidden";
      pendingBox.innerHTML = "";
    }

    // Meters
    const q = (usage && (usage.metrics || usage.quotas)) || {};
    setMeter("meterSearchesVal", "meterSearchesFill", q.monthly_searches);
    setMeter("meterAiVal", "meterAiFill", q.monthly_ai_analyses);
    setMeter("meterTeamVal", "meterTeamFill", q.team_members);
    renderTokenMeter(usage);

    // Plans grid
    const notice = $("billingPlanNotice");
    if (notice) {
      notice.className = canManage ? "hidden" : "inline-alert alert-info";
      notice.innerHTML = canManage ? "" : "<div><strong>Want more?</strong><div>Only your workspace owner or billing admin can change the plan. Ask your admin to upgrade.</div></div>";
    }
    const activeSlug = isActive ? (plan.slug || (sub && sub.plan_id) || "") : "";
    const plansGrid = $("plansCatalogGrid");
    if (plansGrid) {
      if (!plans.length) {
        plansGrid.innerHTML = emptyState({ icon: "card", title: "No plans available", sub: "Plans will appear here once they are published." });
      } else {
        plansGrid.innerHTML = plans.map((p) => {
          const isCurrent = activeSlug && p.slug === activeSlug;
          const isPending = pendingPlanId && (p.slug === pendingPlanId || p.id === pendingPlanId);
          const lim = p.limits || {};
          const has = (f) => Array.isArray(p.features) && p.features.includes(f);
          const features = [
            { name: `${fmt(lim.monthly_tokens || 0)} tokens / mo`, active: Boolean(lim.monthly_tokens) },
            { name: `${fmt(lim.monthly_searches || 0)} searches / mo`, active: Boolean(lim.monthly_searches) },
            { name: `${fmt(lim.posts_per_search || 0)} posts per search`, active: Boolean(lim.posts_per_search) },
            { name: `${fmt(lim.comments_per_post || 0)} comments per post`, active: Boolean(lim.comments_per_post) },
            { name: `${fmt(lim.monthly_ai_analyses || 0)} AI analyses`, active: has("ai_analysis") },
            { name: `Up to ${fmt(lim.team_members || 0)} team members`, active: Boolean(lim.team_members) },
            { name: "CSV & report exports", active: has("csv_export") },
          ];
          let action;
          if (isCurrent) action = `<button type="button" class="saas-btn saas-btn-secondary btn-block" disabled>Current plan</button>`;
          else if (isPending) action = `<button type="button" class="saas-btn saas-btn-secondary btn-block" disabled>Pending confirmation</button>`;
          else if (canManage && !pending) action = `<button type="button" class="saas-btn saas-btn-primary btn-block" data-checkout="${esc(p.slug)}">Choose ${esc(p.name)} →</button>`;
          else if (canManage) action = `<button type="button" class="saas-btn saas-btn-secondary btn-block" disabled title="Finish the pending checkout first">Checkout pending</button>`;
          else action = `<button type="button" class="saas-btn saas-btn-secondary btn-block" disabled>Ask your admin to upgrade</button>`;
          return `
            <div class="plan-card ${isCurrent ? "current" : ""}">
              <div class="plan-card-name">${esc(p.name)}</div>
              <div class="plan-card-desc">${esc(p.description || "")}</div>
              <div class="plan-card-price">
                <span class="amount">${esc(formatMoney(p.price_monthly, p.currency))}</span>
                <span class="period">/ month</span>
              </div>
              <ul class="plan-card-features">
                ${features.map((f) => `<li class="${f.active ? "included" : "excluded"}">${esc(f.name)}</li>`).join("")}
              </ul>
              ${action}
            </div>`;
        }).join("");
      }
    }

    // Invoices
    const invList = $("invoicesList");
    if (invList) {
      if (!canView) {
        invList.innerHTML = `<tr><td colspan="4" class="td-empty">${ico("lock")} Invoices are visible to your workspace admins.</td></tr>`;
      } else if (invRes && !invRes.ok) {
        invList.innerHTML = `<tr><td colspan="4" class="td-empty">Couldn't load invoices. <button type="button" class="link-btn" data-retry="billing">Try again</button></td></tr>`;
        RETRY.billing = loadBillingData;
      } else {
        const invoices = (invRes && invRes.data.invoices) || [];
        invList.innerHTML = invoices.length
          ? invoices.map((inv) => `
            <tr>
              <td class="cell-mono">${esc(inv.number)}</td>
              <td>${esc(formatDate(inv.created_at))}</td>
              <td class="cell-strong">${esc(formatMoney(inv.total, inv.currency))}</td>
              <td>${statusPill(inv.status)}</td>
            </tr>`).join("")
          : `<tr><td colspan="4" class="td-empty">No invoices yet.</td></tr>`;
      }
    }

    // Cancel / undo-cancel (owner only — the server enforces it)
    const cancelArea = $("billingCancelArea");
    if (cancelArea) {
      const owner = portal.user && portal.user.org_role === "owner";
      if (sub && isActive && canManage && owner && !sub.cancel_at_period_end) {
        cancelArea.innerHTML = `<button class="btn-ghost btn-danger-text" data-action="cancel-sub">Cancel subscription</button>`;
      } else if (sub && isActive && canManage && owner && sub.cancel_at_period_end) {
        cancelArea.innerHTML = `<button class="btn-primary" data-action="reactivate-sub">Keep my subscription</button>`;
      } else {
        cancelArea.innerHTML = "";
      }
    }
    if (!usageRes.ok && currentView === "billing") toast("Some billing data couldn't be loaded", "error");
  } finally {
    _billingLoading = false;
  }
}

function renderTokenMeter(usage) {
  const t = usage && usage.tokens;
  const demo = usage && usage.demo;
  const box = $("tokenMeter");
  if (!box) return;
  if (!t) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  const pct = t.allocated > 0 ? Math.min(100, Math.round((t.used / t.allocated) * 100)) : 100;
  const valEl = $("tokenMeterVal");
  if (valEl) valEl.textContent = `${fmt(t.remaining)} left of ${fmt(t.allocated)}`;
  const fill = $("tokenMeterFill");
  if (fill) {
    fill.style.width = `${pct}%`; fill.className = "quota-fill " + meterClass(pct);
    if (fill.parentElement) fill.parentElement.setAttribute("aria-valuenow", String(pct));
  }
  const note = $("tokenMeterNote");
  if (note) {
    if (t.expired || (demo && demo.expired)) note.textContent = "Your demo has ended — choose a plan to continue.";
    else if (demo) note.textContent = `Demo ends in ${countdownText(demo.expires_at)}.`;
    else note.textContent = t.expires_at ? `Renews ${new Date(t.expires_at).toLocaleDateString()}` : "";
  }
}

async function handlePlanCheckout(planSlug) {
  if (!canManageBilling()) { toast("Only your workspace owner or billing admin can change the plan.", "error"); return; }
  const plan = portal.plans.find((p) => p.slug === planSlug) || { name: planSlug };
  if (!(await confirmAction({ title: `Continue to payment for ${plan.name}?`, message: "The plan activates after the payment is confirmed by our team. Your current plan stays in effect until then.", confirmLabel: "Continue to payment" }))) return;
  const res = await api("/api/billing/checkout", { method: "POST", body: { plan_slug: planSlug, billing_cycle: "monthly" } });
  if (!res.ok) {
    if (handleEntitlementError(res.status, res.data)) return;
    toast(errText(res.data, "Checkout failed"), "error");
    return;
  }
  // Checkout never activates anything: the plan becomes active only after
  // the payment is verified AND our team confirms it.
  const co = res.data.checkout || {};
  let target = "";
  try {
    const u = co.redirect_url ? new URL(String(co.redirect_url), location.href) : null;
    // provider checkout (https) or our own status page (same origin) only
    if (u && (u.protocol === "https:" || u.origin === location.origin)) target = u.href;
  } catch (_) { target = ""; }
  if (target) { location.href = target; return; }
  toast("Payment started — your plan activates after confirmation.", "info");
  await refreshSummary();
  loadBillingData();
}

async function handleCancelSubscription() {
  if (!(await confirmAction({ title: "Cancel your subscription?", message: "You keep access until the end of the current billing period.", confirmLabel: "Cancel subscription", danger: true }))) return;
  const res = await api("/api/billing/cancel", { method: "POST" });
  if (!res.ok) { toast(errText(res.data, "Cancellation failed"), "error"); return; }
  toast("Subscription cancelled. Access continues until period end.", "info");
  loadBillingData();
}

async function handleReactivateSubscription() {
  const res = await api("/api/billing/reactivate", { method: "POST" });
  if (!res.ok) { toast(errText(res.data, "Reactivation failed"), "error"); return; }
  toast("Scheduled cancellation removed", "success");
  loadBillingData();
}

// ═════════════════════════════════════════════════════════════════════════
// DASHBOARD, TOKEN METER, BANNER
// ═════════════════════════════════════════════════════════════════════════

function countdownText(iso) {
  const d = parseDate(iso);
  if (!d) return "—";
  let s = Math.max(0, Math.floor((d.getTime() - Date.now()) / 1000));
  const days = Math.floor(s / 86400); s -= days * 86400;
  const hours = Math.floor(s / 3600); s -= hours * 3600;
  const mins = Math.floor(s / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${mins}m`;
  return `${mins}m`;
}

let _summaryPromise = null;
/** Refresh GET /api/me/summary (dashboard data, caps, blockers, tokens). */
function refreshSummary() {
  if (_summaryPromise) return _summaryPromise;
  _summaryPromise = (async () => {
    const res = await api("/api/me/summary");
    if (res.ok) {
      portal.summary = res.data;
      renderTokenWidgets();
      renderPortalBanner();
      applySearchState();
      const badge = $("navAssignedBadge");
      const n = (res.data.counts || {}).assigned_to_me || 0;
      if (badge) { badge.textContent = String(n); badge.classList.toggle("hidden", !n); }
      const planBadge = $("topbarPlanBadge");
      const plan = ((res.data.usage || {}).plan) || {};
      if (planBadge && plan.name) { planBadge.textContent = plan.name; planBadge.className = `plan-badge-pill ${esc(String(plan.slug || "").toLowerCase())}`; }
    }
    return res;
  })().finally(() => { setTimeout(() => { _summaryPromise = null; }, 0); });
  return _summaryPromise;
}

function tokenState() {
  const usage = (portal.summary && portal.summary.usage) || {};
  return { t: usage.tokens || null, demo: usage.demo || null };
}

function renderTokenWidgets() {
  const { t, demo } = tokenState();
  const chip = $("tokenChip");
  const card = $("sidebarTokenCard");
  const upgradeBtn = $("stcUpgrade");
  const pct = t && t.allocated > 0 ? Math.min(100, Math.round((t.used / t.allocated) * 100)) : 0;
  const remainingPct = 100 - pct;
  const lvl = t && (t.expired || t.remaining <= 0) ? "danger" : remainingPct <= 20 ? "warn" : "";
  if (chip) {
    if (t) {
      chip.classList.remove("hidden");
      $("tokenChipText").textContent = t.expired ? "Expired" : `${fmt(t.remaining)} tokens`;
      const f = $("tokenChipFill");
      f.style.width = remainingPct + "%";
      chip.className = "token-chip " + (lvl ? "tc-" + lvl : "");
      chip.title = `${fmt(t.remaining)} of ${fmt(t.allocated)} tokens left${demo && !demo.expired ? " · demo ends in " + countdownText(demo.expires_at) : ""}`;
    } else {
      chip.classList.add("hidden");
    }
  }
  if (card) {
    const val = $("stcValue");
    const note = $("stcNote");
    const bar = $("stcBar");
    const fill = $("stcFill");
    if (!t) {
      val.textContent = "Not metered";
      fill.style.width = "0%";
      bar.setAttribute("aria-valuenow", "0");
      const planName = (((portal.summary || {}).usage || {}).plan || {}).name;
      note.textContent = planName ? `${planName} plan` : "";
    } else {
      val.textContent = `${fmt(t.remaining)} / ${fmt(t.allocated)}`;
      fill.style.width = pct + "%";
      fill.className = "quota-fill " + meterClass(pct);
      bar.setAttribute("aria-valuenow", String(pct));
      bar.setAttribute("aria-valuetext", `${t.used} of ${t.allocated} tokens used`);
      if (t.expired || (demo && demo.expired)) note.textContent = demo ? "Demo ended — choose a plan" : "Tokens expired";
      else if (demo) note.innerHTML = `Demo ends in <b data-countdown="${esc(demo.expires_at)}">${esc(countdownText(demo.expires_at))}</b>`;
      else note.textContent = t.expires_at ? `Resets ${new Date(t.expires_at).toLocaleDateString()}` : `${fmt(t.used)} used`;
    }
    const showUpgrade = Boolean(demo) || lvl !== "";
    if (upgradeBtn) {
      upgradeBtn.classList.toggle("hidden", !showUpgrade);
      upgradeBtn.textContent = canManageBilling() ? "Upgrade plan" : "See plans";
    }
  }
  renderTokenMeter((portal.summary || {}).usage);
}

function renderPortalBanner() {
  const box = $("portalBanner");
  if (!box || !portal.summary) return;
  const s = portal.summary;
  let dismissed = "";
  try { dismissed = sessionStorage.getItem("leadai_banner_dismissed") || ""; } catch (_) {}
  let item = null;
  if ((s.blockers || []).length) {
    const b = s.blockers[0];
    item = { level: "danger", code: b.code, title: (ENTITLEMENT_COPY[b.code] || {}).title || "Action needed", msg: b.message, action: "upgrade" };
  } else if (s.subscription && s.subscription.pending) {
    item = { level: "info", code: "PENDING", title: "Pending confirmation", msg: `${s.subscription.pending.plan_name || "Your new plan"} is awaiting confirmation by our team. Your current plan stays in effect until then.`, action: "billing" };
  } else if (s.usage && s.usage.demo && !s.usage.demo.expired && (s.usage.demo.days_remaining || 0) <= 2) {
    item = { level: "warning", code: "DEMO_ENDING", title: "Your demo ends soon", msg: `Demo ends in ${countdownText(s.usage.demo.expires_at)}. Choose a plan to keep your workspace running.`, action: "upgrade" };
  }
  // the dashboard shows the same information as alerts — no duplicate banner there
  if (!item || dismissed === item.code || currentView === "dashboard") { box.classList.add("hidden"); box.innerHTML = ""; return; }
  const action = item.action === "billing"
    ? `<button type="button" class="btn-ghost btn-sm" data-nav="billing">View status</button>`
    : upgradeButtonHtml(item.code);
  box.className = `portal-banner banner-${item.level}`;
  box.innerHTML = `<div class="banner-text"><strong>${esc(item.title)}</strong> <span>${esc(item.msg)}</span></div><div class="banner-actions">${action}${item.level !== "danger" ? `<button type="button" class="icon-btn icon-btn-sm" data-dismiss-banner="${esc(item.code)}" aria-label="Dismiss">✕</button>` : ""}</div>`;
}

const ALERT_ACTIONS = {
  upgrade: () => upgradeButtonHtml(""),
  billing: () => `<button type="button" class="btn-ghost btn-sm" data-nav="billing">View</button>`,
  assigned: () => `<button type="button" class="btn-ghost btn-sm" data-nav="leads" data-leads-view-link="assigned">Open</button>`,
  history: () => `<button type="button" class="btn-ghost btn-sm" data-nav="history">Review</button>`,
};

let _dashLoading = false;
async function loadDashboard() {
  if (_dashLoading) return;
  _dashLoading = true;
  const errBox = $("dashError");
  try {
    const res = await refreshSummary();
    if (!res.ok) {
      RETRY.dashboard = loadDashboard;
      errBox.classList.remove("hidden");
      errBox.innerHTML = failureState(res, "dashboard", "the dashboard");
      return;
    }
    errBox.classList.add("hidden");
    errBox.innerHTML = "";
    renderDashboard(res.data);
  } finally {
    _dashLoading = false;
  }
}

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

function kpi(id, label, value, foot, extraCls) {
  const el = $(id);
  if (!el) return;
  el.className = "kpi-card" + (extraCls ? " " + extraCls : "");
  el.innerHTML = `<div class="kpi-label">${esc(label)}</div><div class="kpi-value">${value}</div><div class="kpi-foot">${foot}</div>`;
}

function renderDashboard(s) {
  const first = ((s.user || {}).name || "").split(" ")[0];
  $("dashTitle").textContent = `${greeting()}${first ? ", " + first : ""}`;
  $("dashSub").textContent = `${(s.organization || {}).name || "Your workspace"} · ${(s.user || {}).role_label || ""} — only your own searches, leads and usage are shown here.`;
  const newSearch = $("dashNewSearch");
  if (newSearch) newSearch.classList.toggle("hidden", !can("search.create"));

  // alerts
  const alerts = s.alerts || [];
  $("dashAlerts").innerHTML = alerts.map((a) => {
    const lvl = a.level === "danger" ? "alert-danger" : a.level === "warning" ? "alert-warning" : "alert-info";
    const title = (ENTITLEMENT_COPY[a.code] || {}).title || "";
    const actionHtml = a.action === "upgrade" ? upgradeButtonHtml(a.code) : (ALERT_ACTIONS[a.action] ? ALERT_ACTIONS[a.action]() : "");
    return `<div class="inline-alert ${lvl}"><div>${title ? `<strong>${esc(title)}</strong> ` : ""}<span>${esc(a.message)}</span>${a.code === "DEMO_ACTIVE" && a.expires_at ? ` <span class="countdown" data-countdown="${esc(a.expires_at)}">(${esc(countdownText(a.expires_at))} left)</span>` : ""}</div>${actionHtml}</div>`;
  }).join("");

  const c = s.counts || {};
  const usage = s.usage || {};
  const t = usage.tokens;
  const demo = usage.demo;
  if (t) {
    const pct = t.allocated > 0 ? Math.min(100, Math.round((t.used / t.allocated) * 100)) : 100;
    kpi("kpiTokens", "Tokens left", `${esc(fmt(t.remaining))}<span class="kpi-of"> / ${esc(fmt(t.allocated))}</span>`,
      `${meterBar(pct, "Workspace tokens used", `${t.used} of ${t.allocated} tokens used`)}
       <div class="kpi-sub">${demo ? (demo.expired ? "Demo ended" : `Demo ends in <b data-countdown="${esc(demo.expires_at)}">${esc(countdownText(demo.expires_at))}</b>`) : t.expires_at ? "Resets " + esc(new Date(t.expires_at).toLocaleDateString()) : esc(fmt(c.tokens_consumed || 0)) + " used by you"}</div>`,
      "kpi-token");
  } else {
    kpi("kpiTokens", "Plan", esc((usage.plan || {}).name || "—"), `<div class="kpi-sub">Not token-metered</div>`, "kpi-token");
  }
  const ms = (usage.metrics || {}).monthly_searches || {};
  kpi("kpiSearches", "Searches this period", esc(fmt(c.searches_period != null ? c.searches_period : c.searches)),
    `<div class="kpi-sub">${esc(fmt(c.searches))} total · ${c.searches_running ? `<b>${esc(c.searches_running)} running</b> · ` : ""}${esc(c.searches_failed || 0)} failed${ms.limit ? ` · workspace ${esc(fmt(ms.used))}/${esc(fmt(ms.limit))}` : ""}</div>`);
  kpi("kpiLeads", "My leads", esc(fmt(c.leads)),
    `<div class="kpi-sub">${esc(fmt(c.hot_leads || 0))} hot · ${esc(fmt(c.new_leads || 0))} new</div>`);
  kpi("kpiAssigned", "Assigned to me", esc(fmt(c.assigned_to_me || 0)),
    `<button type="button" class="link-btn" data-nav="leads" data-leads-view-link="assigned">Open assigned leads →</button>`,
    c.assigned_to_me ? "kpi-accent" : "");

  // recent searches
  const rs = s.recent_searches || [];
  $("dashRecentSearches").innerHTML = rs.length
    ? `<ul class="mini-list">${rs.map((r) => {
        const [label, icon] = URL_LABELS[r.platform] || [r.platform || "URL", ico("link")];
        return `<li><button type="button" class="mini-row" data-open-run="${esc(r.run_id)}">
          <span class="mini-icon" aria-hidden="true">${icon}</span>
          <span class="mini-main"><span class="mini-title">${esc(r.query)}</span><span class="mini-sub">${esc(label)} · ${esc(relativeTime(r.created_at))}</span></span>
          ${statusPill(r.status)}
        </button></li>`;
      }).join("")}</ul>`
    : emptyState({ icon: "search", title: "No searches yet", sub: "Start with a Facebook, Instagram, YouTube or LinkedIn URL.", cta: can("search.create") ? "Start a search" : "", ctaNav: "search" });

  // recent leads
  const rl = s.recent_leads || [];
  $("dashRecentLeads").innerHTML = rl.length
    ? `<ul class="mini-list">${rl.map((l) => `<li><button type="button" class="mini-row" data-open-lead="${esc(l.id)}">
          <span class="mini-avatar" aria-hidden="true">${esc((l.commenter_name || "?").trim().charAt(0).toUpperCase())}</span>
          <span class="mini-main"><span class="mini-title">${esc(l.commenter_name || "Prospect")}${l.assigned_to_me ? ' <span class="badge badge-lead">Assigned</span>' : ""}</span><span class="mini-sub">${esc((l.comment_text || "").slice(0, 80))}</span></span>
          <span class="mini-score">${scoreBar(l.lead_score)}<b>${esc(l.lead_score || 0)}</b></span>
        </button></li>`).join("")}</ul>`
    : emptyState({ icon: "target", title: "No leads yet", sub: "Leads appear here as the AI qualifies comments from your searches." });

  // plan & usage
  const plan = usage.plan || {};
  const caps = s.caps || {};
  const sub = s.subscription || {};
  const status = sub.is_demo ? "demo" : sub.status;
  const meters = [["monthly_searches", "Searches"], ["monthly_ai_analyses", "AI analyses"], ["monthly_exports", "Exports"], ["team_members", "Team members"]];
  $("dashPlan").innerHTML = `
    <div class="plan-summary">
      <div class="plan-summary-head">
        <div><div class="plan-summary-name">${esc(plan.is_demo ? "Demo" : (plan.name || "No plan"))}</div>
        <div class="kpi-sub">${caps.posts_per_search ? `Up to ${esc(caps.posts_per_search)} posts per search` : ""}${caps.comments_per_post ? ` · ${esc(caps.comments_per_post)} comments per post` : ""}</div></div>
        ${status ? statusPill(status) : ""}
      </div>
      <div class="meter-grid">${meters.map(([k, label]) => {
        const m = (usage.metrics || {})[k];
        if (!m) return "";
        const pct = m.limit > 0 ? Math.min(100, Math.round((m.used / m.limit) * 100)) : 0;
        const val = `${fmt(m.used)} / ${m.limit >= 1e9 ? "∞" : m.limit ? fmt(m.limit) : "—"}`;
        return `<div class="meter"><div class="meter-head"><span>${esc(label)}</span><span class="cell-mono">${esc(val)}</span></div>${meterBar(pct, label + " used", val)}</div>`;
      }).join("")}</div>
    </div>`;
}

// live countdowns (demo expiry) — refreshed every 30s
function tickCountdowns() {
  document.querySelectorAll("[data-countdown]").forEach((el) => {
    const txt = countdownText(el.getAttribute("data-countdown"));
    el.textContent = el.classList.contains("countdown") ? `(${txt} left)` : txt;
  });
}

// ═════════════════════════════════════════════════════════════════════════
// LEADS (own + assigned to me)
// ═════════════════════════════════════════════════════════════════════════
const leadsState = { view: "all", page: 1 };

function setLeadsView(view, reload = true) {
  leadsState.view = ["all", "mine", "assigned"].includes(view) ? view : "all";
  leadsState.page = 1;
  document.querySelectorAll("#leadsTabs .seg-tab").forEach((b) => {
    const on = b.dataset.leadsView === leadsState.view;
    b.classList.toggle("active", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
  });
  if (reload) loadLeads();
}

/** Query string of the Leads table's current view + filters (shared by the
    list and its CSV export so the download matches what is on screen). */
function leadsQuery(extra) {
  const f = leadsFilters();
  const params = new URLSearchParams(Object.assign({ view: leadsState.view, sort: f.sort || "score" }, extra || {}));
  ["q", "status", "quality", "min_score", "platform"].forEach((k) => { if (f[k]) params.set(k, f[k]); });
  return params;
}
async function exportMyLeadsCsv(btn) {
  if (btn) { btn.disabled = true; btn.setAttribute("aria-busy", "true"); }
  try {
    await downloadCsv("/api/me/leads.csv?" + leadsQuery().toString(), "my_leads.csv");
  } finally {
    if (btn) { btn.removeAttribute("aria-busy"); btn.disabled = !TABLES.leads; }
  }
}
function leadsFilters() {
  return {
    q: $("leadsSearch").value.trim(), status: $("leadsStatus").value, quality: $("leadsQuality").value,
    min_score: $("leadsMinScore").value, platform: $("leadsPlatform").value, sort: $("leadsSort").value,
  };
}

async function loadLeads() {
  const list = $("leadsList");
  if (!can("leads.view") && portal.user) { list.innerHTML = deniedState("Your role can't view leads."); $("leadsPager").innerHTML = ""; clearTable("leads"); return; }
  list.innerHTML = skeletonRows(6);
  const f = leadsFilters();
  const exportBtn = $("leadsExport");
  if (exportBtn) exportBtn.classList.toggle("hidden", Boolean(portal.user) && !(can("exports.create") && can("leads.export")));
  const params = leadsQuery({ page: String(leadsState.page), page_size: "20" });
  const res = await api("/api/me/leads?" + params.toString());
  if (!res.ok) {
    RETRY.leads = loadLeads;
    clearTable("leads");
    list.innerHTML = failureState(res, "leads", "leads");
    $("leadsPager").innerHTML = "";
    return;
  }
  const d = res.data;
  const vc = d.view_counts || {};
  $("leadsCountAll").textContent = fmt(vc.all || 0);
  $("leadsCountMine").textContent = fmt(vc.mine || 0);
  $("leadsCountAssigned").textContent = fmt(vc.assigned || 0);
  const badge = $("navAssignedBadge");
  if (badge) { badge.textContent = String(vc.assigned || 0); badge.classList.toggle("hidden", !vc.assigned); }

  const items = d.items || [];
  const filtered = Boolean(f.q || f.status || f.quality || f.min_score || f.platform);
  $("leadsClear").classList.toggle("hidden", !filtered);
  if (!items.length) {
    clearTable("leads");
    list.innerHTML = filtered
      ? emptyState({ icon: "search", title: "No leads match these filters", sub: "Try a broader search or clear the filters.", cta: "Clear filters", ctaAction: "clear-lead-filters" })
      : leadsState.view === "assigned"
        ? emptyState({ icon: "users", title: "Nothing assigned to you yet", sub: "When an admin assigns a lead to you, it shows up here." })
        : emptyState({ icon: "target", title: "No leads yet", sub: "Run a search — the AI qualifies comments and scores every lead.", cta: can("search.create") ? "Start a search" : "", ctaNav: "search" });
    renderPager($("leadsPager"), d, "leads");
    return;
  }
  const quality = (q) => q && q !== "none"
    ? `<span class="badge badge-${q === "hot" ? "hot" : q === "warm" ? "warm" : "cold"}">${esc(q)}</span>` : `<span class="muted">—</span>`;
  renderTable({
    key: "leads", el: list, label: "Leads", rows: items, sort: f.sort || "score",
    onSort: (v) => { $("leadsSort").value = v; leadsState.page = 1; loadLeads(); },
    rowAttrs: (l) => `class="dt-click" tabindex="0" data-open-lead="${esc(l.id)}" aria-label="Open lead ${esc(l.commenter_name || "")}"`,
    columns: [
      { id: "lead", label: "Lead", required: true, cls: "col-main", text: (l) => l.commenter_name || "Prospect",
        html: (l) => {
          const pi = platformInfo(l.platform);
          return `<span class="dt-main">
            <span class="mini-avatar" aria-hidden="true">${esc((l.commenter_name || "?").trim().charAt(0).toUpperCase())}</span>
            <span class="mini-main">
              <span class="mini-title">${esc(l.commenter_name || "Prospect")}
                ${pi.name ? `<span class="platform-badge ${esc(l.platform)}">${esc(pi.name)}</span>` : ""}
                ${l.assigned_to_me ? '<span class="badge badge-lead">Assigned to me</span>' : ""}
              </span>
              <span class="mini-sub">${esc((l.comment_text || "").slice(0, 140))}</span>
            </span>
          </span>`;
        } },
      { id: "score", label: "Score", cls: "col-score", sort: { desc: "score" }, text: (l) => l.lead_score || 0,
        html: (l) => scoreCell(l.lead_score) },
      { id: "quality", label: "Quality", cls: "col-tag", text: (l) => l.lead_quality || "", html: (l) => quality(l.lead_quality) },
      { id: "status", label: "Status", cls: "col-tag", text: (l) => LEAD_STATUS_LABELS[l.lead_status || "new"] || l.lead_status,
        html: (l) => leadStatusPill(l.lead_status) },
      { id: "contact", label: "Contact", cls: "col-contact", text: (l) => [l.phone, l.email].filter(Boolean).join(" / "),
        html: (l) => l.phone || l.email
          ? `<span class="contact-links">${l.phone ? `<a class="contact-mini" href="tel:${esc(l.phone)}" data-stop title="Call ${esc(l.phone)}">${ico("phone")} <span>${esc(l.phone)}</span></a>` : ""}${l.email ? `<a class="contact-mini" href="mailto:${esc(l.email)}" data-stop title="Email ${esc(l.email)}">${ico("mail")} <span>${esc(l.email)}</span></a>` : ""}</span>`
          : `<span class="muted">—</span>` },
      { id: "found", label: "Found", cls: "col-date", sort: { desc: "newest", asc: "oldest" },
        text: (l) => l.lead_created_at || l.created_at || "",
        html: (l) => `<span class="cell-mono" title="${esc(formatDate(l.lead_created_at || l.created_at))}">${esc(relativeTime(l.lead_created_at || l.created_at)) || "—"}</span>` },
    ],
    actions: (l) => `<button type="button" class="btn-ghost btn-sm" data-open-lead="${esc(l.id)}" aria-label="Open lead ${esc(l.commenter_name || "")}">Open</button>`,
  });
  renderPager($("leadsPager"), d, "leads");
}
PAGERS.leads = (p) => { leadsState.page = p; loadLeads(); };

// ═════════════════════════════════════════════════════════════════════════
// SEARCH HISTORY
// ═════════════════════════════════════════════════════════════════════════
const historyState = { page: 1 };
function historyParams(page, size) {
  const params = new URLSearchParams({ page: String(page), page_size: String(size), sort: $("historySort").value || "newest" });
  const q = $("historySearch").value.trim();
  if (q) params.set("q", q);
  if ($("historyStatus").value) params.set("status", $("historyStatus").value);
  if ($("historyPlatform").value) params.set("platform", $("historyPlatform").value);
  return params;
}
async function loadHistory() {
  const list = $("historyList");
  list.innerHTML = skeletonRows(6);
  const res = await api("/api/me/searches?" + historyParams(historyState.page, 20).toString());
  if (!res.ok) {
    RETRY.history = loadHistory;
    clearTable("history");
    list.innerHTML = failureState(res, "history", "search history");
    $("historyPager").innerHTML = "";
    return;
  }
  const items = res.data.items || [];
  const filtered = Boolean($("historySearch").value.trim() || $("historyStatus").value || $("historyPlatform").value);
  $("historyClear").classList.toggle("hidden", !filtered);
  if (!items.length) {
    clearTable("history");
    list.innerHTML = filtered
      ? emptyState({ icon: "search", title: "No searches match", sub: "Try different filters.", cta: "Clear filters", ctaAction: "clear-history-filters" })
      : emptyState({ icon: "clock", title: "No searches yet", sub: "Your search runs will be listed here.", cta: can("search.create") ? "Start a search" : "", ctaNav: "search" });
    renderPager($("historyPager"), res.data, "history");
    return;
  }
  const canDelete = can("search.cancel");
  renderTable({
    key: "history", el: list, label: "Search history", rows: items, sort: $("historySort").value || "newest",
    onSort: (v) => { $("historySort").value = v; historyState.page = 1; loadHistory(); },
    fetchAll: () => fetchAllPages((p, n) => "/api/me/searches?" + historyParams(p, n).toString()),
    filters: () => filtersOf(historyParams(1, 20)),
    columns: [
      { id: "url", label: "URL", required: true, cls: "col-main", text: (s) => s.query,
        html: (s) => {
          const [label, icon] = URL_LABELS[s.platform] || [s.platform || "URL", ico("link")];
          return `<span class="dt-main"><span class="mini-icon" aria-hidden="true">${icon}</span>
            <span class="mini-main"><span class="mini-title cell-url">${esc(s.query)}</span>
            <span class="mini-sub">${esc(label)}${s.limit ? ` · ${esc(s.limit)} posts` : ""}${s.max_comments_per_post ? ` · ${esc(s.max_comments_per_post)} comments/post` : ""}${s.error ? ` · <span class="text-danger">${esc(String(s.error).slice(0, 80))}</span>` : ""}</span></span></span>`;
        } },
      { id: "platform", label: "Platform", cls: "col-tag", text: (s) => PLATFORMS[s.platform] || s.platform || "",
        html: (s) => PLATFORMS[s.platform] ? `<span class="platform-badge ${esc(s.platform)}">${esc(PLATFORMS[s.platform])}</span>` : `<span class="muted">—</span>` },
      { id: "status", label: "Status", cls: "col-tag", sort: { desc: "status" }, text: (s) => s.status || "", html: (s) => statusPill(s.status) },
      { id: "pages", label: "Pages", cls: "col-num", text: (s) => s.pages_stored || 0, html: (s) => `<span class="cell-mono">${esc(s.pages_stored || 0)}</span>` },
      { id: "started", label: "Started", cls: "col-date", sort: { desc: "newest", asc: "oldest" }, text: (s) => s.created_at || "",
        html: (s) => `<span class="cell-mono" title="${esc(formatDate(s.created_at))}">${esc(relativeTime(s.created_at))}</span>` },
    ],
    actions: (s) => {
      const report = `/static/url_report.html?run_id=${encodeURIComponent(s.run_id)}`;
      return `<button type="button" class="btn-ghost btn-sm" data-open-run="${esc(s.run_id)}">Open</button>
        <a class="btn-ghost btn-sm" href="${esc(report)}" target="_blank" rel="noopener" aria-label="Open report in a new tab">Report ↗</a>
        ${canDelete && s.status !== "running" ? `<button type="button" class="btn-ghost btn-sm btn-danger-text" data-delete-run="${esc(s.run_id)}" aria-label="Delete search ${esc(s.query)}">Delete</button>` : ""}`;
    },
  });
  renderPager($("historyPager"), res.data, "history");
}
PAGERS.history = (p) => { historyState.page = p; loadHistory(); };

// ═════════════════════════════════════════════════════════════════════════
// EXPORTS
// ═════════════════════════════════════════════════════════════════════════
const exportsState = { page: 1 };
const EXPORT_LABELS = { pages: "Pages", posts: "Posts", comments: "Comments", leads: "My leads", history: "Search history", exports: "Export history", ledger: "Token activity" };
function exportUrl(e) {
  if (e.source === "user_portal_client") return "";   // in-browser table download — nothing to re-run
  if (e.scope === "leads") {
    const params = new URLSearchParams();
    Object.entries(e.filters || {}).forEach(([k, v]) => { if (v !== null && v !== undefined && v !== "") params.set(k, String(v)); });
    return "/api/me/leads.csv" + (params.toString() ? "?" + params.toString() : "");
  }
  if (e.scope === "pages") return e.run_id ? `/api/export/pages.csv?run_id=${encodeURIComponent(e.run_id)}` : "/api/export/pages.csv";
  if (e.scope === "posts" && e.page_id) return `/api/export/posts.csv?page_id=${encodeURIComponent(e.page_id)}`;
  if (e.scope === "comments" && e.post_id) return `/api/export/comments.csv?post_id=${encodeURIComponent(e.post_id)}&only_leads=${e.only_leads ? "true" : "false"}`;
  return "";
}
function exportsParams(page, size) {
  const params = new URLSearchParams({ page: String(page), page_size: String(size), sort: $("exportsSort").value || "newest" });
  if ($("exportsScope").value) params.set("scope", $("exportsScope").value);
  return params;
}
async function loadExports() {
  const list = $("exportsList");
  if (portal.user && !can("exports.view")) { list.innerHTML = deniedState("Your role can't view exports."); $("exportsPager").innerHTML = ""; clearTable("exports"); return; }
  list.innerHTML = skeletonRows(5);
  const res = await api("/api/me/exports?" + exportsParams(exportsState.page, 20).toString());
  if (!res.ok) {
    RETRY.exports = loadExports;
    clearTable("exports");
    list.innerHTML = failureState(res, "exports", "exports");
    $("exportsPager").innerHTML = "";
    return;
  }
  const items = res.data.items || [];
  if (!items.length) {
    clearTable("exports");
    list.innerHTML = $("exportsScope").value
      ? emptyState({ icon: "search", title: "No exports of this type", sub: "Choose “All types” to see everything." })
      : emptyState({ icon: "file", title: "No exports yet", sub: "Use “Export CSV” on a page, post or comments screen — your downloads are listed here." });
    renderPager($("exportsPager"), res.data, "exports");
    return;
  }
  const canCreate = can("exports.create");
  const labelOf = (e) => e.scope === "comments" && e.only_leads ? "Leads" : (EXPORT_LABELS[e.scope] || e.scope);
  const srcOf = (e) => e.source === "user_portal_client" ? "Table download" : e.scope === "leads" ? (e.filters && e.filters.view === "assigned" ? "Assigned to me" : e.filters && e.filters.view === "mine" ? "Found by me" : "All my leads") : e.run_id ? `Search ${String(e.run_id).slice(-8)}` : e.page_id ? `Page …${String(e.page_id).slice(-6)}` : e.post_id ? `Post …${String(e.post_id).slice(-6)}` : "All";
  renderTable({
    key: "exports", el: list, label: "Exports", rows: items, sort: $("exportsSort").value || "newest",
    onSort: (v) => { $("exportsSort").value = v; exportsState.page = 1; loadExports(); },
    fetchAll: () => fetchAllPages((p, n) => "/api/me/exports?" + exportsParams(p, n).toString()),
    filters: () => filtersOf(exportsParams(1, 20)),
    columns: [
      { id: "export", label: "Export", required: true, cls: "col-main", text: (e) => labelOf(e) + " export",
        html: (e) => `<span class="dt-main"><span class="mini-icon" aria-hidden="true">${ico("file", "ico-sm")}</span><span class="mini-main"><span class="mini-title">${esc(labelOf(e))} export</span><span class="mini-sub cell-mono">${esc(srcOf(e))}</span></span></span>` },
      { id: "source", label: "Source", cls: "col-tag", text: (e) => srcOf(e), html: (e) => `<span class="cell-mono">${esc(srcOf(e))}</span>` },
      { id: "format", label: "Format", cls: "col-num", text: (e) => String(e.format || "csv").toUpperCase(), html: (e) => `<span class="cell-mono">${esc(String(e.format || "csv").toUpperCase())}</span>` },
      { id: "status", label: "Status", cls: "col-tag", text: (e) => e.status || "completed", html: (e) => statusPill(e.status || "completed") },
      { id: "created", label: "Created", cls: "col-date", sort: { desc: "newest", asc: "oldest" }, text: (e) => e.created_at || "",
        html: (e) => `<span class="cell-mono" title="${esc(formatDate(e.created_at))}">${esc(relativeTime(e.created_at))}</span>` },
    ],
    actions: (e) => {
      const url = exportUrl(e);
      return canCreate && url ? `<button type="button" class="btn-ghost btn-sm" data-reexport="${esc(url)}" data-name="${esc(labelOf(e).toLowerCase())}.csv">Export again</button>` : "";
    },
  });
  renderPager($("exportsPager"), res.data, "exports");
}
PAGERS.exports = (p) => { exportsState.page = p; loadExports(); };

// ═════════════════════════════════════════════════════════════════════════
// USAGE
// ═════════════════════════════════════════════════════════════════════════
const ledgerState = { page: 1 };
const REASON_LABELS = { search: "URL search", collect: "Collect posts / comments", ai_call: "AI analysis", export: "CSV export" };

async function loadUsage() {
  const body = $("usageBody");
  body.innerHTML = `<div class="kpi-grid">${'<div class="kpi-card"><span class="skeleton skeleton-line w-40"></span><span class="skeleton skeleton-line w-60"></span></div>'.repeat(4)}</div>`;
  loadLedger();
  const res = await api("/api/me/usage");
  if (!res.ok) {
    RETRY.usage = loadUsage;
    body.innerHTML = failureState(res, "usage", "usage");
    return;
  }
  const d = res.data;
  const me = d.me || {};
  const org = d.organization || {};
  const t = org.tokens;
  const demo = org.demo;
  const costs = d.token_costs || {};
  const caps = d.caps || {};
  const reasons = Object.entries(me.tokens_by_reason || {}).sort((a, b) => b[1] - a[1]);
  const maxReason = reasons.length ? reasons[0][1] : 0;
  const metrics = [["monthly_searches", "Searches"], ["monthly_ai_analyses", "AI analyses"], ["monthly_exports", "Exports"], ["monthly_posts", "Posts"], ["monthly_comments", "Comments"], ["team_members", "Team members"]];
  const tokPct = t && t.allocated > 0 ? Math.min(100, Math.round((t.used / t.allocated) * 100)) : 0;
  body.innerHTML = `
    <h2 class="section-title">You, this period</h2>
    <div class="kpi-grid">
      <div class="kpi-card"><div class="kpi-label">Searches</div><div class="kpi-value">${esc(fmt(me.searches_period))}</div><div class="kpi-foot"><div class="kpi-sub">${esc(fmt(me.searches))} all time</div></div></div>
      <div class="kpi-card"><div class="kpi-label">Leads</div><div class="kpi-value">${esc(fmt(me.leads))}</div><div class="kpi-foot"><div class="kpi-sub">${esc(fmt(me.assigned_to_me || 0))} assigned to you</div></div></div>
      <div class="kpi-card"><div class="kpi-label">Exports</div><div class="kpi-value">${esc(fmt(me.exports_period != null ? me.exports_period : me.exports))}</div><div class="kpi-foot"><div class="kpi-sub">${esc(fmt(me.exports))} all time</div></div></div>
      <div class="kpi-card kpi-token"><div class="kpi-label">Tokens you used</div><div class="kpi-value">${esc(fmt(me.tokens_consumed_period))}</div><div class="kpi-foot"><div class="kpi-sub">${esc(fmt(me.tokens_consumed))} all time</div></div></div>
    </div>
    <div class="dash-grid">
      <section class="panel">
        <div class="panel-head"><h2 class="panel-title">Workspace tokens</h2></div>
        <div class="panel-body">
          ${t ? `<div class="meter-big"><span class="meter-big-val">${esc(fmt(t.remaining))}</span><span class="kpi-of"> of ${esc(fmt(t.allocated))} left</span></div>
            <div class="quota-bar" role="progressbar" aria-label="Workspace tokens used" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${tokPct}"><div class="quota-fill ${meterClass(tokPct)}" style="width:${tokPct}%"></div></div>
            <div class="kpi-sub mt-8">${demo ? (demo.expired ? "Demo ended — choose a plan to continue." : `Demo ends in <b data-countdown="${esc(demo.expires_at)}">${esc(countdownText(demo.expires_at))}</b>`) : t.expires_at ? "Resets " + esc(new Date(t.expires_at).toLocaleDateString()) : ""}</div>`
          : `<div class="kpi-sub">Your workspace isn't token-metered.</div>`}
          ${Object.keys(costs).length ? `<div class="cost-list">${Object.entries(costs).map(([k, v]) => `<span class="cost-chip">${esc(REASON_LABELS[k] || k)}: <b>${esc(v)}</b> token${v === 1 ? "" : "s"}</span>`).join("")}</div>` : ""}
          ${caps.posts_per_search || caps.comments_per_post ? `<div class="kpi-sub mt-8">Per search: up to ${esc(caps.posts_per_search || "—")} posts · ${esc(caps.comments_per_post || "—")} comments per post</div>` : ""}
        </div>
      </section>
      <section class="panel">
        <div class="panel-head"><h2 class="panel-title">Where your tokens went</h2></div>
        <div class="panel-body">
          ${reasons.length ? `<ul class="bar-list" aria-label="Tokens used by activity">${reasons.map(([k, v], i) => `<li class="bar-row"><span class="bar-label"><span class="bar-key" style="background:var(--chart-${(i % 8) + 1})" aria-hidden="true"></span>${esc(REASON_LABELS[k] || k)}</span><span class="bar-track" aria-hidden="true"><span class="bar-fill" style="width:${maxReason ? Math.max(4, Math.round(v / maxReason * 100)) : 0}%;background:var(--chart-${(i % 8) + 1})"></span></span><span class="bar-val cell-mono">${esc(fmt(v))}<span class="sr-only"> tokens</span></span></li>`).join("")}</ul>`
          : emptyState({ icon: "coins", title: "No tokens used yet", sub: "Searches, collections and exports use tokens." })}
        </div>
      </section>
    </div>
    <section class="panel">
      <div class="panel-head"><h2 class="panel-title">Workspace plan meters</h2><span class="kpi-sub">${esc((org.plan || {}).name || "")}</span></div>
      <div class="panel-body"><div class="meter-grid">${metrics.map(([k, label]) => {
        const m = (org.metrics || {})[k];
        if (!m) return "";
        const pct = m.limit > 0 ? Math.min(100, Math.round((m.used / m.limit) * 100)) : 0;
        const val = `${fmt(m.used)} / ${m.limit >= 1e9 ? "∞" : m.limit ? fmt(m.limit) : "—"}`;
        return `<div class="meter"><div class="meter-head"><span>${esc(label)}</span><span class="cell-mono">${esc(val)}</span></div>${meterBar(pct, label + " used", val)}</div>`;
      }).join("")}</div></div>
    </section>`;
}

function ledgerUrl(page, size) {
  const params = new URLSearchParams({ page: String(page), page_size: String(size) });
  const reason = $("ledgerReason") ? $("ledgerReason").value : "";
  if (reason) params.set("reason", reason);
  return "/api/me/usage/ledger?" + params.toString();
}
async function loadLedger() {
  const list = $("ledgerList");
  list.innerHTML = skeletonRows(4);
  const res = await api(ledgerUrl(ledgerState.page, 15));
  if (!res.ok) {
    RETRY.ledger = loadLedger;
    clearTable("ledger");
    list.innerHTML = failureState(res, "ledger", "token activity");
    $("ledgerPager").innerHTML = "";
    return;
  }
  const items = res.data.items || [];
  if (!items.length) {
    clearTable("ledger");
    list.innerHTML = $("ledgerReason").value
      ? emptyState({ icon: "search", title: "No activity of this type", sub: "Choose “All activity” to see everything." })
      : emptyState({ icon: "file", title: "No token activity yet", sub: "Every token you spend is listed here." });
    $("ledgerPager").innerHTML = "";
    return;
  }
  const sign = (r) => r.type === "consume" ? "−" : r.type === "refund" || r.type === "allocate" ? "+" : "";
  renderTable({
    key: "ledger", el: list, label: "Token activity", rows: items,
    fetchAll: () => fetchAllPages(ledgerUrl),
    filters: () => filtersOf(new URLSearchParams(ledgerUrl(1, 15).split("?")[1] || "")),
    columns: [
      { id: "activity", label: "Activity", required: true, cls: "col-main", text: (r) => REASON_LABELS[r.reason] || r.reason || r.type,
        html: (r) => `<span class="dt-main"><span class="mini-main"><span class="mini-title">${esc(REASON_LABELS[r.reason] || r.reason || r.type)}</span><span class="mini-sub cell-mono">${esc(r.reference || "")}</span></span></span>` },
      { id: "reference", label: "Reference", cls: "col-tag", text: (r) => r.reference || "", html: (r) => `<span class="cell-mono">${esc(r.reference || "—")}</span>` },
      { id: "tokens", label: "Tokens", cls: "col-num", text: (r) => (r.type === "consume" ? -1 : 1) * (Number(r.amount) || 0),
        html: (r) => `<span class="cell-mono ${r.type === "consume" ? "text-danger" : "text-success"}">${sign(r)}${esc(r.amount)}</span>` },
      { id: "balance", label: "Balance after", cls: "col-num", text: (r) => r.balance_after != null ? r.balance_after : "",
        html: (r) => `<span class="cell-mono">${r.balance_after != null ? esc(fmt(r.balance_after)) : "—"}</span>` },
      { id: "when", label: "When", cls: "col-date", text: (r) => r.created_at || "",
        html: (r) => `<span class="cell-mono" title="${esc(formatDate(r.created_at))}">${esc(relativeTime(r.created_at))}</span>` },
    ],
  });
  renderPager($("ledgerPager"), res.data, "ledger");
}
PAGERS.ledger = (p) => { ledgerState.page = p; loadLedger(); };

// ═════════════════════════════════════════════════════════════════════════
// SETTINGS — profile, notification preferences, security
// ═════════════════════════════════════════════════════════════════════════
let currentSettingsTab = "profile";
let profileCache = null;

function showSettingsTab(tab) {
  currentSettingsTab = ["profile", "notifications", "security"].includes(tab) ? tab : "profile";
  document.querySelectorAll("#settingsTabs .seg-tab").forEach((b) => {
    const on = b.dataset.settingsTab === currentSettingsTab;
    b.classList.toggle("active", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
  });
  $("settingsProfile").classList.toggle("hidden", currentSettingsTab !== "profile");
  $("settingsNotifications").classList.toggle("hidden", currentSettingsTab !== "notifications");
  $("settingsSecurity").classList.toggle("hidden", currentSettingsTab !== "security");
  if (currentSettingsTab === "security") loadSessions();
  else loadProfile();
}

function fieldError(inputId, msg) {
  const input = $(inputId);
  if (!input) return;
  const el = $(input.getAttribute("data-error") || "");
  if (msg) { input.setAttribute("aria-invalid", "true"); if (el) el.textContent = msg; }
  else { input.removeAttribute("aria-invalid"); if (el) el.textContent = ""; }
}

function setBusy(btn, busy, label) {
  if (!btn) return;
  const l = btn.querySelector(".btn-label");
  if (busy) {
    if (l && btn.dataset.idle === undefined) btn.dataset.idle = l.textContent;
    if (l && label) l.textContent = label;
    btn.disabled = true; btn.classList.add("is-loading"); btn.setAttribute("aria-busy", "true");
  } else {
    if (l && btn.dataset.idle !== undefined) { l.textContent = btn.dataset.idle; delete btn.dataset.idle; }
    btn.disabled = false; btn.classList.remove("is-loading"); btn.removeAttribute("aria-busy");
  }
}

async function loadProfile() {
  const prefsBody = $("prefsBody");
  if (!profileCache) prefsBody.innerHTML = skeletonRows(4);
  const res = await api("/api/me/profile");
  if (!res.ok) {
    RETRY.profile = loadProfile;
    prefsBody.innerHTML = failureState(res, "profile", "your profile");
    $("profileStatus").textContent = "Couldn't load your profile.";
    return;
  }
  const p = res.data.profile;
  profileCache = p;
  $("profName").value = p.name || "";
  $("profEmail").value = p.email || "";
  $("profPhone").value = p.phone || "";
  $("profOrgRole").textContent = `${(p.organization || {}).name || "—"} · ${p.role_label || p.role || ""}`;
  $("profileStatus").textContent = p.impersonated ? "Read-only during a support session." : "";
  ["profName", "profPhone"].forEach((id) => { $(id).disabled = Boolean(p.impersonated); });
  $("profileSave").disabled = Boolean(p.impersonated);
  $("prefsSave").disabled = Boolean(p.impersonated);

  const options = p.notification_options || [];
  const prefs = p.notification_preferences || {};
  prefsBody.innerHTML = `<p class="field-hint prefs-intro">Shared with the Admin portal — changes apply everywhere you sign in.</p>
  <div class="prefs-table" role="list" aria-label="Notification preferences">
    ${options.map((o) => `<div class="prefs-row" role="listitem">
      <span><span class="prefs-label" id="pref-${esc(o.key)}">${esc(o.label)}</span>${o.hint ? `<span class="field-hint prefs-hint">${esc(o.hint)}</span>` : ""}</span>
      <label class="switch"><input type="checkbox" data-pref="${esc(o.key)}" aria-labelledby="pref-${esc(o.key)}" ${prefs[o.key] ? "checked" : ""}><span class="switch-ui" aria-hidden="true"></span></label>
    </div>`).join("")}
    <div class="prefs-row" role="listitem"><span><span class="prefs-label" id="pref-security">Security alerts</span><span class="field-hint prefs-hint">Always on — sign-ins, password changes, suspicious activity</span></span>
      <label class="switch"><input type="checkbox" checked disabled aria-labelledby="pref-security"><span class="switch-ui" aria-hidden="true"></span></label></div>
  </div>`;
}

async function saveProfile(ev) {
  ev.preventDefault();
  const name = $("profName").value.trim();
  const phone = $("profPhone").value.trim();
  fieldError("profName", ""); fieldError("profPhone", "");
  let bad = false;
  if (!name) { fieldError("profName", "Enter your name."); bad = true; }
  else if (/[<>]/.test(name)) { fieldError("profName", "Name can't contain < or >."); bad = true; }
  if (phone && !/^[+0-9 ()\-.]{5,25}$/.test(phone)) { fieldError("profPhone", "Use digits, spaces and + ( ) - . only."); bad = true; }
  if (bad) return;
  const btn = $("profileSave");
  setBusy(btn, true, "Saving…");
  const res = await api("/api/me/profile", { method: "PATCH", body: { name, phone } });
  setBusy(btn, false);
  if (!res.ok) {
    const d = (res.data || {}).detail || {};
    if (d.field === "name") fieldError("profName", d.message);
    else if (d.field === "phone") fieldError("profPhone", d.message);
    else toast(errText(res.data, "Could not save your profile"), "error");
    return;
  }
  $("profileStatus").textContent = (res.data.changed || []).length ? "Saved." : "No changes.";
  toast("Profile saved", "success");
  if (portal.user) {
    portal.user.name = res.data.profile.name;
    $("userMenuName").textContent = res.data.profile.name;
    $("userAvatar").textContent = (res.data.profile.name || "?").charAt(0).toUpperCase();
  }
}

async function savePrefs(ev) {
  ev.preventDefault();
  const prefs = {};
  document.querySelectorAll("#prefsBody input[data-pref]").forEach((cb) => { prefs[cb.dataset.pref] = cb.checked; });
  const btn = $("prefsSave");
  setBusy(btn, true, "Saving…");
  const res = await api("/api/me/profile", { method: "PATCH", body: { notification_preferences: prefs } });
  setBusy(btn, false);
  if (!res.ok) { toast(errText(res.data, "Could not save preferences"), "error"); return; }
  $("prefsStatus").textContent = "Saved.";
  toast("Notification preferences saved", "success");
}

function passwordProblem(pw) {
  if (window.LeadAIUI && LeadAIUI.passwordProblem) return LeadAIUI.passwordProblem(pw);
  if (!pw) return "Choose a password.";
  if (pw.length < 8) return "Use at least 8 characters.";
  if (pw === pw.toLowerCase()) return "Add at least one uppercase letter.";
  if (!/\d/.test(pw)) return "Add at least one number.";
  return "";
}

async function changePassword(ev) {
  ev.preventDefault();
  const cur = $("pwCurrent").value;
  const nw = $("pwNew").value;
  const conf = $("pwConfirm").value;
  ["pwCurrent", "pwNew", "pwConfirm"].forEach((id) => fieldError(id, ""));
  let bad = false;
  if (!cur) { fieldError("pwCurrent", "Enter your current password."); bad = true; }
  const prob = passwordProblem(nw);
  if (prob) { fieldError("pwNew", prob); bad = true; }
  else if (nw === cur) { fieldError("pwNew", "Choose a password different from the current one."); bad = true; }
  if (!bad && nw !== conf) { fieldError("pwConfirm", "Passwords don't match."); bad = true; }
  if (bad) return;
  const btn = $("passwordSave");
  setBusy(btn, true, "Updating…");
  const res = await api("/api/auth/password/change", { method: "POST", body: { current_password: cur, new_password: nw } });
  setBusy(btn, false);
  if (!res.ok) {
    const msg = errText(res.data, "Could not change the password");
    if (res.status === 400 && /current/i.test(msg)) fieldError("pwCurrent", msg);
    else fieldError("pwNew", msg);
    return;
  }
  ["pwCurrent", "pwNew", "pwConfirm"].forEach((id) => { $(id).value = ""; });
  $("passwordStatus").textContent = res.data.message || "Password updated.";
  toast("Password updated — other sessions were signed out", "success");
  loadSessions();
}

function deviceName(ua) {
  const s = String(ua || "");
  const browser = /Edg\//.test(s) ? "Edge" : /Chrome\//.test(s) ? "Chrome" : /Firefox\//.test(s) ? "Firefox" : /Safari\//.test(s) ? "Safari" : "Browser";
  const os = /Windows/.test(s) ? "Windows" : /Mac OS X/.test(s) ? "macOS" : /Android/.test(s) ? "Android" : /iPhone|iPad/.test(s) ? "iOS" : /Linux/.test(s) ? "Linux" : "";
  return s && s !== "unknown" ? `${browser}${os ? " on " + os : ""}` : "Unknown device";
}

async function loadSessions() {
  const list = $("sessionsList");
  list.innerHTML = skeletonRows(3);
  const res = await api("/api/auth/sessions");
  if (!res.ok) {
    RETRY.sessions = loadSessions;
    list.innerHTML = failureState(res, "sessions", "your sessions");
    return;
  }
  const items = res.data.sessions || [];
  const others = items.filter((s) => !s.current).length;
  $("revokeOthersBtn").disabled = !others;
  if (!items.length) { list.innerHTML = emptyState({ icon: "lock", title: "No active sessions", sub: "" }); return; }
  list.innerHTML = `<ul class="session-list">${items.map((s) => `<li class="session-row">
      <span class="mini-main"><span class="mini-title">${esc(deviceName(s.user_agent))} ${s.current ? '<span class="badge badge-ok">This device</span>' : ""}${s.impersonated_by ? ' <span class="badge badge-warm">Support session</span>' : ""}</span>
      <span class="mini-sub">Signed in ${esc(formatDate(s.created_at))}${s.expires_at ? " · expires " + esc(formatDate(s.expires_at)) : ""}</span></span>
      ${s.current ? "" : `<button type="button" class="btn-ghost btn-sm btn-danger-text" data-revoke-session="${esc(s.id)}">Sign out</button>`}
    </li>`).join("")}</ul>`;
}

async function revokeSession(id) {
  const all = id === "others";
  if (!(await confirmAction({ title: all ? "Sign out every other session?" : "Sign out this session?", message: "Those devices will need to sign in again.", confirmLabel: "Sign out", danger: true }))) return;
  const res = await api(`/api/auth/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!res.ok) { toast(errText(res.data, "Could not sign out the session"), "error"); return; }
  toast(all ? `Signed out ${res.data.revoked || 0} session(s)` : "Session signed out", "success");
  loadSessions();
}

// ═════════════════════════════════════════════════════════════════════════
// NOTIFICATIONS (bell)
// ═════════════════════════════════════════════════════════════════════════
let notifItems = [];
async function loadNotifications(renderList = false) {
  const res = await api("/api/notifications?limit=15");
  const count = $("notifCount");
  const btn = $("notifBtn");
  if (!res.ok) {
    if (renderList) {
      RETRY.notifications = () => loadNotifications(true);
      $("notifList").innerHTML = errorState(errText(res.data, "Notifications are unavailable."), "notifications");
    }
    return;
  }
  notifItems = res.data.items || [];
  const unread = res.data.unread || 0;
  count.textContent = unread > 99 ? "99+" : String(unread);
  count.classList.toggle("hidden", !unread);
  btn.setAttribute("aria-label", unread ? `Notifications (${unread} unread)` : "Notifications");
  if (renderList || !$("notifPanel").classList.contains("hidden")) renderNotifications();
}

const SEVERITY_ICON = { success: "✓", warning: "!", danger: "!", info: "i" };
function renderNotifications() {
  const list = $("notifList");
  if (!notifItems.length) {
    list.innerHTML = `<div class="notif-empty">You're all caught up.</div>`;
    return;
  }
  list.innerHTML = notifItems.map((n) => `<button type="button" class="notif-item${n.read ? "" : " unread"}" data-notif="${esc(n.id)}">
      <span class="notif-sev sev-${esc(n.severity || "info")}" aria-hidden="true">${SEVERITY_ICON[n.severity] || "i"}</span>
      <span class="notif-body"><span class="notif-title">${esc(n.title)}</span>${n.message ? `<span class="notif-msg">${esc(n.message)}</span>` : ""}<span class="notif-time">${esc(relativeTime(n.created_at))}</span></span>
      ${n.read ? "" : '<span class="sr-only">(unread)</span>'}
    </button>`).join("");
}

async function openNotification(id) {
  const n = notifItems.find((x) => x.id === id);
  if (n && !n.read) { await api("/api/notifications/read", { method: "POST", body: { id } }); }
  closeMenus();
  loadNotifications();
  if (!n) return;
  const data = n.data || {};
  if (data.search_run_id) { reopenSearch(data.search_run_id); return; }
  if (n.type === "lead_assigned") { setLeadsView("assigned", false); navigateToView("leads", { userInitiated: true }); return; }
  if (/demo|subscription|payment|token/.test(n.type || "")) { navigateToView("billing", { userInitiated: true }); return; }
  if (n.link && typeof n.link === "string" && n.link.startsWith("/") && !n.link.startsWith("//")) location.href = n.link;
}

async function markAllNotificationsRead() {
  const res = await api("/api/notifications/read", { method: "POST", body: {} });
  if (!res.ok) { toast("Couldn't mark notifications as read", "error"); return; }
  loadNotifications(true);
}

// ═════════════════════════════════════════════════════════════════════════
// SHELL — menus, sidebar, delegated events, auth, boot
// ═════════════════════════════════════════════════════════════════════════
function closeMenus(except) {
  document.querySelectorAll(".col-panel").forEach((p) => p.classList.add("hidden"));
  document.querySelectorAll("[data-col-toggle]").forEach((b) => b.setAttribute("aria-expanded", "false"));
  [["notifPanel", "notifBtn"], ["userMenu", "userChip"]].forEach(([panel, btn]) => {
    if (panel === except) return;
    const p = $(panel);
    const b = $(btn);
    if (p) p.classList.add("hidden");
    if (b) b.setAttribute("aria-expanded", "false");
  });
}
function toggleMenu(panelId, btnId) {
  const p = $(panelId);
  const open = p.classList.contains("hidden");
  closeMenus(panelId);
  p.classList.toggle("hidden", !open);
  $(btnId).setAttribute("aria-expanded", open ? "true" : "false");
  return open;
}
function openSidebar() {
  document.body.classList.add("sidebar-open");
  $("sidebarScrim").classList.remove("hidden");
  $("sidebarToggle").setAttribute("aria-expanded", "true");
}
function closeSidebar() {
  document.body.classList.remove("sidebar-open");
  const scrim = $("sidebarScrim");
  if (scrim) scrim.classList.add("hidden");
  const t = $("sidebarToggle");
  if (t) t.setAttribute("aria-expanded", "false");
}

function onDocumentClick(e) {
  const t = e.target;
  if (!t || !t.closest) return;
  // links inside clickable cards must not trigger the card
  if (t.closest("[data-stop]")) return;

  if (t.closest("#confirmOk")) { if (_confirmResolve) _confirmResolve(true); return; }
  if (t.closest("#confirmCancel") || t.id === "confirmModal") { if (_confirmResolve) _confirmResolve(false); return; }
  const tsort = t.closest("[data-table-sort]");
  if (tsort) { const cfg = TABLES[tsort.dataset.tableSort]; if (cfg && cfg.onSort) cfg.onSort(tsort.dataset.sort); return; }
  const texp = t.closest("[data-table-export]");
  if (texp) { exportTableCsv(texp.dataset.tableExport, texp); return; }
  const ctog = t.closest("[data-col-toggle]");
  if (ctog) {
    const key = ctog.dataset.colToggle;
    const panel = $("colPanel-" + key);
    const open = panel.classList.contains("hidden");
    closeMenus();
    if (open) renderColMenu(key);
    panel.classList.toggle("hidden", !open);
    ctog.setAttribute("aria-expanded", open ? "true" : "false");
    return;
  }
  const creset = t.closest("[data-cols-reset]");
  if (creset) { setHiddenCols(creset.dataset.colsReset, new Set()); if (TABLES[creset.dataset.colsReset]) renderTable(TABLES[creset.dataset.colsReset]); renderColMenu(creset.dataset.colsReset); return; }

  const retry = t.closest("[data-retry]");
  if (retry) { const fn = RETRY[retry.dataset.retry]; if (fn) fn(); return; }
  const pager = t.closest("[data-page-key]");
  if (pager) { const fn = PAGERS[pager.dataset.pageKey]; if (fn && !pager.disabled) fn(parseInt(pager.dataset.page, 10) || 1); return; }

  const nav = t.closest("[data-nav]");
  if (nav) {
    e.preventDefault();
    const view = nav.dataset.nav;
    if (nav.dataset.leadsViewLink) setLeadsView(nav.dataset.leadsViewLink, false);
    navigateToView(view, { tab: nav.dataset.tab, userInitiated: true });
    return;
  }
  const act = t.closest("[data-action]");
  if (act) {
    const a = act.dataset.action;
    if (a === "upgrade") { closeQuotaModal(); navigateToView("billing", { userInitiated: true }); }
    else if (a === "retry-search") { $("urlSearchInput").focus(); $("urlSearchProgress").classList.add("hidden"); }
    else if (a === "export-leads") exportMyLeadsCsv(act);
    else if (a === "clear-lead-filters") { ["leadsSearch", "leadsStatus", "leadsQuality", "leadsMinScore", "leadsPlatform"].forEach((id) => { $(id).value = ""; }); leadsState.page = 1; loadLeads(); }
    else if (a === "clear-history-filters") { ["historySearch", "historyStatus", "historyPlatform"].forEach((id) => { $(id).value = ""; }); historyState.page = 1; loadHistory(); }
    else if (a === "focus-url") { if (currentView !== "search") navigateToView("search", { userInitiated: true }); setTimeout(() => { const i = $("urlSearchInput"); if (i) i.focus(); }, 30); }
    else if (a === "cancel-sub") handleCancelSubscription();
    else if (a === "reactivate-sub") handleReactivateSubscription();
    return;
  }
  const del = t.closest("[data-delete-run]");
  if (del) { e.stopPropagation(); deleteSearch(del.dataset.deleteRun, e); return; }
  const run = t.closest("[data-open-run]");
  if (run) { reopenSearch(run.dataset.openRun); return; }
  const lead = t.closest("[data-open-lead]");
  if (lead) { openLeadDetail(lead.dataset.openLead); return; }
  const pg = t.closest("[data-open-page]");
  if (pg) { openPage(pg.dataset.openPage); return; }
  const po = t.closest("[data-open-post]");
  if (po) { openPost(po.dataset.openPost); return; }
  const ls = t.closest("[data-lead-status]");
  if (ls) { updateLeadStatus(ls.dataset.leadStatus); return; }
  const na = t.closest("[data-lead-note-add]");
  if (na) { addLeadNote(na.dataset.leadNoteAdd); return; }
  const nd = t.closest("[data-lead-note-del]");
  if (nd) { deleteLeadNote(nd.dataset.leadNoteDel, parseInt(nd.dataset.index, 10)); return; }
  const fa = t.closest("[data-lead-fu-add]");
  if (fa) { addLeadFollowUp(fa.dataset.leadFuAdd); return; }
  const fu = t.closest("[data-fu-update]");
  if (fu) { updateFollowUpStatus(fu.dataset.fuUpdate, parseInt(fu.dataset.index, 10), fu.dataset.status); return; }
  const co = t.closest("[data-checkout]");
  if (co) { handlePlanCheckout(co.dataset.checkout); return; }
  const rx = t.closest("[data-reexport]");
  if (rx) {
    confirmAction({ title: "Export this data again?", message: "A new export uses your plan's export allowance.", confirmLabel: "Export again" })
      .then((ok) => { if (ok) downloadCsv(rx.dataset.reexport, rx.dataset.name || "export.csv"); });
    return;
  }
  const rs = t.closest("[data-revoke-session]");
  if (rs) { revokeSession(rs.dataset.revokeSession); return; }
  const nt = t.closest("[data-notif]");
  if (nt) { openNotification(nt.dataset.notif); return; }
  const dismiss = t.closest("[data-dismiss-banner]");
  if (dismiss) { try { sessionStorage.setItem("leadai_banner_dismissed", dismiss.dataset.dismissBanner); } catch (_) {} renderPortalBanner(); return; }
  const mr = t.closest("[data-member-remove]");
  if (mr) { handleRemoveMember(mr.dataset.memberRemove); return; }
  const ir = t.closest("[data-invite-resend]");
  if (ir) { handleResendInvitation(ir.dataset.inviteResend); return; }
  const iv = t.closest("[data-invite-revoke]");
  if (iv) { handleRevokeInvitation(iv.dataset.inviteRevoke); return; }

  // click outside open menus closes them
  if (!t.closest(".menu-wrap")) closeMenus();
}

function openDialog() {
  return Array.from(document.querySelectorAll(".modal-backdrop")).reverse().find((m) => !m.classList.contains("hidden"));
}
function onDocumentKeydown(e) {
  if (e.key === "Tab") {
    const dlg = openDialog();
    if (dlg) {
      const f = Array.from(dlg.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'))
        .filter((el) => el.offsetParent !== null);
      if (f.length) {
        const first = f[0], last = f[f.length - 1];
        if (!dlg.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
        else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    }
    return;
  }
  if (e.key === "Escape" && _confirmResolve) { _confirmResolve(false); return; }
  if (e.key === "Escape") {
    const openModal = Array.from(document.querySelectorAll(".modal-backdrop")).find((m) => !m.classList.contains("hidden"));
    if (openModal) {
      if (openModal.id === "leadDetailModal") closeLeadDetails();
      else openModal.classList.add("hidden");
      return;
    }
    closeMenus();
    closeSidebar();
    return;
  }
  if (e.key === "Enter" || e.key === " ") {
    const t = e.target;
    if (!t || !t.matches) return;
    if (t.matches(".rs-row, .clickable-card, .dt-click") && !t.matches("button, a, input, select")) {
      e.preventDefault();
      t.click();
    }
  }
}

// ── Support session (Super Admin impersonation) ─────────────────────────
// Always visible while a Super Admin is viewing this workspace as a user;
// "Exit" ends it server-side and returns to the Super Admin portal.
function showImpersonationBanner(user) {
  if (document.getElementById("impersonationBanner")) return;
  const bar = document.createElement("div");
  bar.id = "impersonationBanner";
  bar.setAttribute("role", "status");
  bar.style.cssText = "position:sticky;top:0;z-index:1000;display:flex;flex-wrap:wrap;gap:8px 14px;" +
    "align-items:center;justify-content:center;padding:8px 16px;font:500 13px var(--font-ui);" +
    "background:var(--amber-soft);color:var(--text);border-bottom:1px solid var(--amber)";
  const expires = parseDate(user.impersonation_expires_at);
  bar.innerHTML = `<span>Support session: you are viewing <strong>${esc(user.organization_name || "this workspace")}</strong>
    as <strong>${esc(user.email || "")}</strong> (started by ${esc(user.impersonated_by)}${expires ? ", ends " + fmtTime(user.impersonation_expires_at) : ""}).</span>
    <button type="button" class="btn btn-sm" id="impersonationExit">Exit support session</button>`;
  document.body.prepend(bar);
  bar.querySelector("#impersonationExit").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    const res = await api("/api/super-admin/impersonate/exit", { method: "POST", allow401: true });
    if (res.ok) { location.href = "/superadmin"; return; }
    btn.disabled = false;
    toast(errText(res.data, "Couldn't end the support session."), "error");
  });
}

// ── Sign-in ──────────────────────────────────────────────────────────────
// The backend enforces a session for every /api call; signed-out visitors
// get 401 → send them to /login.
async function checkAuth() {
  const res = await api("/api/auth/me", { allow401: true });
  if (res.status === 401) { location.href = "/login?next=" + encodeURIComponent(location.pathname + location.hash); return false; }
  if (res.status === 403) {
    const code = errCode(res.data);
    if (code === "demo_pending") { location.href = "/demo-pending"; return false; }
    location.href = "/403"; return false;
  }
  if (!res.ok) {
    // backend offline — keep the shell usable with a clear message
    toast(res.networkError ? "You appear to be offline." : "Couldn't verify your session.", "error");
    return false;
  }
  const user = res.data.user;
  if (user && (user.is_platform_admin || user.platform_role) && !user.organization_id) {
    location.href = user.platform_role === "super_admin" ? "/superadmin" : "/admin"; return false;
  }
  if (!user) { location.href = "/login"; return false; }
  portal.user = user;
  portal.permissions = new Set(user.permissions || []);
  if (user.impersonated_by) showImpersonationBanner(user);

  const display = user.name || user.email || "?";
  $("userAvatar").textContent = display.charAt(0).toUpperCase();
  $("userName").textContent = user.name || user.email;
  $("userMenuName").textContent = user.name || "—";
  $("userMenuEmail").textContent = user.email || "";
  $("userMenuRole").textContent = `${ROLE_LABELS[user.org_role] || user.org_role || ""}${user.organization_name ? " · " + user.organization_name : ""}`;
  $("userChip").classList.remove("hidden");
  $("topbarWorkspaceName").textContent = user.organization_name || "Workspace";
  $("sidebarRole").textContent = ROLE_LABELS[user.org_role] || user.org_role || "";

  const isAdmin = user.org_role === "owner" || user.org_role === "admin";
  const showAdminPortal = Boolean(user.admin_portal_enabled) && isAdmin;
  $("navAdminPortal").classList.toggle("hidden", !showAdminPortal);
  $("menuAdminPortal").classList.toggle("hidden", !showAdminPortal);
  $("menuWorkspace").classList.toggle("hidden", !can("settings.manage"));
  $("menuTeam").classList.toggle("hidden", !can("members.view"));
  document.body.classList.toggle("can-search", can("search.create"));
  return true;
}

async function logout() {
  await api("/api/auth/logout", { method: "POST", allow401: true });
  location.href = "/login";
}

function bindFilters() {
  const reloadLeads = () => { leadsState.page = 1; loadLeads(); };
  $("leadsSearch").addEventListener("input", debounce(reloadLeads, 300));
  ["leadsStatus", "leadsQuality", "leadsMinScore", "leadsPlatform", "leadsSort"].forEach((id) => $(id).addEventListener("change", reloadLeads));
  document.querySelectorAll("#leadsTabs .seg-tab").forEach((b) => b.addEventListener("click", () => setLeadsView(b.dataset.leadsView)));

  const reloadHistory = () => { historyState.page = 1; loadHistory(); };
  $("historySearch").addEventListener("input", debounce(reloadHistory, 300));
  ["historyStatus", "historyPlatform", "historySort"].forEach((id) => $(id).addEventListener("change", reloadHistory));

  const reloadExports = () => { exportsState.page = 1; loadExports(); };
  ["exportsScope", "exportsSort"].forEach((id) => $(id).addEventListener("change", reloadExports));

  document.querySelectorAll("#settingsTabs .seg-tab").forEach((b) => b.addEventListener("click", () => showSettingsTab(b.dataset.settingsTab)));
  $("profileForm").addEventListener("submit", saveProfile);
  $("prefsForm").addEventListener("submit", savePrefs);
  $("passwordForm").addEventListener("submit", changePassword);
  $("revokeOthersBtn").addEventListener("click", () => revokeSession("others"));

  $("notifBtn").addEventListener("click", (e) => { e.stopPropagation(); if (toggleMenu("notifPanel", "notifBtn")) { $("notifList").innerHTML = skeletonRows(3); loadNotifications(true); } });
  $("notifMarkAll").addEventListener("click", (e) => { e.stopPropagation(); markAllNotificationsRead(); });
  $("userChip").addEventListener("click", (e) => { e.stopPropagation(); toggleMenu("userMenu", "userChip"); });
  $("sidebarToggle").addEventListener("click", () => (document.body.classList.contains("sidebar-open") ? closeSidebar() : openSidebar()));
  $("sidebarScrim").addEventListener("click", closeSidebar);
  $("themeToggle").addEventListener("click", () => {
    if (window.LeadAITheme && LeadAITheme.toggle) LeadAITheme.toggle();
    else {
      const cur = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", cur);
      try { localStorage.setItem("leadai_color_mode", cur); } catch (_) {}
    }
    syncThemeToggle();
  });
  document.addEventListener("change", (e) => {
    const sel = e.target && e.target.closest ? e.target.closest("[data-member-role]") : null;
    if (sel) handleUpdateMemberRole(sel.dataset.memberRole, sel.value);
    const col = e.target && e.target.matches && e.target.matches("[data-col-key]") ? e.target : null;
    if (col) {
      const key = col.dataset.colKey;
      const hidden = hiddenCols(key);
      if (col.checked) hidden.delete(col.value); else hidden.add(col.value);
      setHiddenCols(key, hidden);
      if (TABLES[key]) renderTable(TABLES[key]);
      const again = document.querySelector(`#colPanel-${key} input[value="${col.value}"]`);
      if (again) again.focus();
    }
  });
  $("ledgerReason").addEventListener("change", () => { ledgerState.page = 1; loadLedger(); });
}

function syncThemeToggle() {
  const btn = $("themeToggle");
  if (!btn) return;
  const attr = document.documentElement.getAttribute("data-theme");
  const mode = attr === "light" || attr === "dark" ? attr
    : (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  btn.setAttribute("data-mode", mode);
  btn.setAttribute("aria-label", mode === "dark" ? "Switch to light theme" : "Switch to dark theme");
  btn.title = btn.getAttribute("aria-label");
}

// ── Boot ─────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", async () => {
  document.addEventListener("click", onDocumentClick);
  document.addEventListener("keydown", onDocumentKeydown);
  document.addEventListener("leadai:themechange", syncThemeToggle);
  syncThemeToggle();

  const input = $("urlSearchInput");
  if (input) {
    input.addEventListener("input", () => {
      const hint = $("urlDetectionHint");
      const v = input.value.trim();
      setUrlError("");
      const p = v ? detectPlatform(v) : null;
      if (hint) {
        hint.textContent = !v ? "Waiting for URL…" : p ? `${(URL_LABELS[p] || [p])[0]} detected` : "Unsupported link";
        hint.classList.toggle("hint-ok", Boolean(p));
        hint.classList.toggle("hint-bad", Boolean(v) && !p);
      }
      updateSearchGuide(p, Boolean(v));
    });
  }
  // one shared "comments per post" number across the comment section and URL search
  const cpp = $("commentsPerPostInput");
  if (cpp) cpp.value = memory.commentsPerPost;
  const urlCpp = $("urlSearchCommentsPerPost");
  if (urlCpp) {
    urlCpp.value = memory.commentsPerPost;
    urlCpp.addEventListener("change", () => setCommentsPerPost(urlCpp));
  }
  const postsIn = $("urlSearchLimit");
  if (postsIn) postsIn.addEventListener("change", () => clampToCap(postsIn, searchCaps().posts_per_search));
  const modeRadios = document.querySelectorAll('input[name="filterMode"]');
  if (modeRadios.length) {
    modeRadios.forEach((r) => r.addEventListener("change", () => {
      urlFilterTouched = true;
      urlFilterModeChanged();
    }));
    urlFilterModeChanged();
  }
  bindFilters();
  if (window.AppConfig) AppConfig.load();

  const ok = await checkAuth();
  if (!ok) return;

  // initial route (#view, default dashboard; checkout cancel returns to #billing)
  const { view, params } = parseHash();
  const leadsView = view === "leads" ? params.get("view") : null;
  if (leadsView) setLeadsView(leadsView, false);
  navigateToView(view || "dashboard", { leadsView, tab: params.get("tab") || undefined, focus: false });
  window.addEventListener("popstate", () => {
    const h = parseHash();
    navigateToView(h.view || "dashboard", { focus: false, tab: h.params.get("tab") || undefined });
  });

  refreshSummary();
  applyUrlFilterDefaults();
  loadUrlFilterCatalog();
  loadWorkspaceData();
  loadNotifications();
  await fetchRecentSearches();
  resumeRunningSearch();

  // Background refresh: running searches, notifications, summary, countdowns
  _recentRefreshTimer = setInterval(() => {
    if (document.hidden) return;
    if (recentData.some((s) => s.status === "running")) {
      fetchRecentSearches().then(() => { renderRecentRows(); renderSessionStats(); });
    }
  }, 5000);
  setInterval(() => { if (!document.hidden) loadNotifications(); }, 60000);
  setInterval(() => { if (!document.hidden) { refreshSummary(); if (currentView === "dashboard") loadDashboard(); } }, 120000);
  setInterval(tickCountdowns, 30000);
});
