/* ══════════════════════════════════════════════════════════════════════
   LeadAI Admin Control Center — frontend application
   Vanilla JS SPA. Every number comes from the real backend; every control
   writes through the admin API and the backend enforces it.
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

function statusBadge(status) {
  const map = {
    running: ["blue", "⟳ running"], completed: ["green", "✓ completed"],
    error: ["red", "✕ error"], cancelled: ["amber", "◌ cancelled"],
    queued: ["violet", "◌ queued"], skipped: ["cyan", "⊘ skipped"],
    empty: ["amber", "○ empty"], pending: ["violet", "○ pending"],
    not_started: ["cyan", "· not started"], started: ["blue", "⟳ started"],
  };
  const [cls, label] = map[status] || ["", status || "—"];
  return `<span class="adm-badge ${cls}">${label}</span>`;
}

function platformBadge(p) {
  const map = {
    facebook: "blue", instagram: "violet", linkedin: "cyan", youtube: "red",
  };
  return `<span class="adm-badge ${map[p] || ""}">${esc(p || "unknown")}</span>`;
}

function qualityBadge(q) {
  if (!q) return `<span class="adm-badge">—</span>`;
  const cls = q === "hot" ? "red" : q === "warm" ? "amber" : "cyan";
  return `<span class="adm-badge ${cls}">${esc(q)}</span>`;
}

function leadStatusBadge(s) {
  const map = { new: "blue", contacted: "violet", qualified: "cyan", converted: "green", ignored: "amber" };
  return `<span class="adm-badge ${map[s] || ""}">${esc(s || "—")}</span>`;
}

function scorePill(score) {
  const n = Number(score);
  if (isNaN(n)) return `<span class="adm-badge">—</span>`;
  const cls = n >= 80 ? "red" : n >= 50 ? "amber" : n >= 1 ? "cyan" : "";
  return `<span class="adm-badge ${cls}">${n}</span>`;
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

function emptyState(icon, text) {
  return `<div class="adm-empty"><div class="adm-empty-ico">${icon}</div>${esc(text)}</div>`;
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

function pageHead(title, sub, actions = "") {
  return `
    <div class="adm-page-head">
      <div>
        <h1 class="adm-page-title">${esc(title)}</h1>
        <div class="adm-page-sub">${sub}</div>
      </div>
      <div class="adm-flex">${actions}</div>
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

/* ──────────────────────────────── Auth / shell ────────────────────── */
async function boot() {
  let authErr = null;
  try {
    const res = await api("/api/auth/me");
    state.user = res.user;
  } catch (err) {
    // api() redirects to /login on 401. Any other failure (server down,
    // 500, timeout) must NOT leave a dead page: the shell renders and the
    // content area shows a retryable error.
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
  // Profile dropdown (top-right): Open App / Users / Security / Sign out
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
}

function navigate(view, params = null) {
  // view: "jobs", "jobs/details:URL123…", "leads/details:…", "platforms/details:…",
  //       "pages", "posts"; anything else falls through to the 404 page.
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
  // Param routes: jobs/details:ID, leads/details:ID, platforms/details:NAME
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
  const viewEl = $("#view");
  viewEl.innerHTML = skeleton();
  target().catch((err) => {
    // A failed view API must never blank the page: the sidebar, topbar and
    // the rest of the shell stay intact; only the content area shows the
    // error with a Retry action.
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

/* ──────────────────────────────── DASHBOARD ───────────────────────── */
async function viewDashboard() {
  const data = await api("/api/admin/dashboard");
  const c = data.counts;
  const s = data.status;
  const root = $("#view");
  const stat = (label, value, hint, nav, params, cls) => `
    <div class="adm-stat clickable" data-nav="${nav}" data-nav-params='${params ? JSON.stringify(params) : ""}'>
      <div class="adm-stat-label">${label}</div><div class="adm-stat-value ${cls || ""}">${value.toLocaleString()}</div>
      ${hint ? `<div class="adm-stat-hint">${hint}</div>` : ""}
    </div>`;
  root.innerHTML = `
    ${pageHead("Dashboard", "Live overview of the LeadAI platform — every number is real data from the database.", `
      <a class="adm-btn primary" href="#/jobs">View Jobs</a>
      <a class="adm-btn" href="#/health">Health Check</a>`)}
    <div class="adm-stats">
      ${stat("Searches (total)", c.jobs_total, `${c.jobs_today} today`, "jobs")}
      ${stat("Running", c.jobs_running, "currently running", "jobs", { status: "running" }, c.jobs_running ? "ok" : "")}
      ${stat("Failed", c.jobs_failed, "ended in error", "jobs", { status: "error" }, c.jobs_failed ? "err" : "ok")}
      ${stat("Pages", c.pages, "collected pages", "pages")}
      ${stat("Posts", c.posts, "collected posts", "posts")}
      ${stat("Comments", c.comments, "collected comments", "ci")}
      ${stat("AI Analyzed", c.analyzed, "records processed by AI", "ci")}
      ${stat("Leads", c.leads, `${c.leads_contact} with phone/email`, "leads")}
    </div>
    <div class="adm-grid-2">
      <div class="adm-card">
        <div class="adm-card-title">Platforms <span class="adm-hint">(click a row for details)</span></div>
        <div class="adm-table-wrap"><table class="adm-table">
          <thead><tr><th>Platform</th><th>Enabled</th><th>Pages</th><th>Posts</th><th>Comments</th><th>Leads</th></tr></thead>
          <tbody>
            ${data.platforms.map((p) => `
              <tr class="adm-row-link" data-nav="platforms/details:${esc(p.platform)}">
                <td>${platformBadge(p.platform)}</td>
                <td>${p.enabled ? `<span class="adm-badge green">on</span>` : `<span class="adm-badge red">off</span>`}</td>
                <td>${p.pages}</td><td>${p.posts}</td><td>${p.comments}</td><td>${p.leads}</td>
              </tr>`).join("")}
          </tbody>
        </table></div>
      </div>
      <div class="adm-card">
        <div class="adm-card-title">System Status <span class="adm-hint">(click a row to open its page)</span></div>
        <div class="adm-kv">
          <div class="adm-kv-row" data-nav="database"><dt>Database</dt><dd><span class="adm-badge ${s.database ? "green" : "red"}">${s.database ? "connected" : "down"}</span></dd></div>
          <div class="adm-kv-row" data-nav="apify"><dt>Apify token</dt><dd>${s.apify_token ? `<span class="adm-badge green">${esc(s.apify_token_hint)}</span>` : `<span class="adm-badge red">not configured</span>`}</dd></div>
          <div class="adm-kv-row" data-nav="ai"><dt>Gemini key</dt><dd>${s.gemini_key ? `<span class="adm-badge green">configured</span>` : `<span class="adm-badge amber">not set (rule fallback)</span>`}</dd></div>
          <div class="adm-kv-row" data-nav="features"><dt>URL search</dt><dd>${s.url_search_enabled ? `<span class="adm-badge green">enabled</span>` : `<span class="adm-badge red">disabled</span>`}</dd></div>
          <div class="adm-kv-row" data-nav="maintenance"><dt>Maintenance</dt><dd>${s.maintenance ? `<span class="adm-badge amber">active</span>` : `<span class="adm-badge">off</span>`}</dd></div>
        </div>
      </div>
    </div>
    <div class="adm-card">
      <div class="adm-card-title">Recent Jobs <span class="adm-hint">(click a row for details)</span></div>
      ${data.recent_jobs.length ? `
        <div class="adm-table-wrap"><table class="adm-table">
          <thead><tr><th>Run ID</th><th>Query</th><th>Platform</th><th>Status</th><th>Phase</th><th>Created</th></tr></thead>
          <tbody>
            ${data.recent_jobs.map((j) => `
              <tr class="adm-row-link" data-nav="jobs/details:${esc(j.run_id)}">
                <td><span class="adm-code">${esc(j.run_id)}</span></td>
                <td><div class="adm-cell-main">${esc((j.query || "").slice(0, 60))}</div></td>
                <td>${platformBadge(j.platform || (j.intent || {}).platform)}</td>
                <td>${statusBadge(j.status)}</td>
                <td>${esc(j.phase || "—")}</td>
                <td>${relativeTime(j.created_at)}</td>
              </tr>`).join("")}
          </tbody>
        </table></div>`
      : emptyState("⇶", "No searches yet — run one from the app.")}
    </div>`;
  bindNav(root);
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

/* ──────────────────────────────── JOBS ────────────────────────────── */
const JOB_FILTERS = { status: "", platform: "", q: "", from: "", to: "" };

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
      <a class="adm-btn" href="/api/admin/export/jobs.csv" ${role === "viewer" ? "onclick='return false'" : ""}>⇩ Export jobs CSV</a>`)}
    <div class="adm-card">
      <div class="adm-filters">
        <input class="adm-input" id="fStatus" placeholder="Status" list="statusOpts" value="${esc(f.status)}">
        <datalist id="statusOpts">
          <option>running</option><option>completed</option><option>error</option>
          <option>cancelled</option><option>queued</option>
        </datalist>
        <input class="adm-input" id="fPlatform" placeholder="Platform" list="platOpts" value="${esc(f.platform)}">
        <datalist id="platOpts">
          <option>facebook</option><option>instagram</option><option>linkedin</option><option>youtube</option>
        </datalist>
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
                  <button class="adm-btn small danger" data-act="del" data-id="${esc(j.run_id)}" ${j.status === "running" || role !== "super_admin" ? "disabled" : ""}>✕</button>
                </div>
              </td>
            </tr>`).join("")
          : `<tr><td colspan="8">${emptyState("⇶", "No jobs match these filters.")}</td></tr>`}
        </tbody>
      </table></div>
      ${pagerHtml(data.total, data.offset, data.limit, (dir) => {
        const off = data.offset + dir * data.limit;
        state.filters.jobsOffset = Math.max(0, off);
        viewJobs();
      })}
    </div>`;
  const apply = () => {
    state.filters.jobsOffset = 0;
    f.status = $("#fStatus").value.trim();
    f.platform = $("#fPlatform").value.trim();
    f.q = $("#fQ").value.trim();
    f.from = $("#fFrom").value;
    f.to = $("#fTo").value;
    viewJobs();
  };
  $("#applyFilters").onclick = apply;
  $("#clearFilters").onclick = () => {
    Object.assign(f, { status: "", platform: "", q: "", from: "", to: "" });
    state.filters.jobsOffset = 0;
    viewJobs();
  };
  $$("[data-job]", root).forEach((row) => {
    row.onclick = (e) => {
      if (e.target.closest("button")) return;
      navigate(`jobs/details:${row.dataset.job}`);
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
  const counts = data.counts || {};
  const intent = j.intent || {};
  const platform = j.platform || intent.platform;
  const started = j.started_at || j.updated_at || j.created_at;
  const duration = (() => {
    if (!j.completed_at || !j.created_at) return "—";
    const ms = new Date(j.completed_at).getTime() - new Date(j.created_at).getTime();
    if (isNaN(ms) || ms < 0) return "—";
    if (ms < 60000) return `${Math.round(ms / 1000)}s`;
    if (ms < 3600000) return `${Math.round(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`;
    return `${Math.floor(ms / 3600000)}h ${Math.round((ms % 3600000) / 60000)}m`;
  })();
  const running = j.status === "running" || j.status === "queued";
  const url = intent.canonical_url || j.query;
  root.innerHTML = `
    ${pageHead(`Job ${esc(runId)}`, "Full run report from the real search history record.", `
      <a class="adm-btn" href="#/jobs">← All Jobs</a>
      <button class="adm-btn" id="jdRefresh">⟳ Refresh</button>
      <button class="adm-btn primary" id="jdRetry" ${!running && role !== "viewer" ? "" : "disabled"} data-act="retry">↻ Retry</button>
      <button class="adm-btn" id="jdCancel" ${running && role !== "viewer" ? "" : "disabled"} data-act="cancel">◼ Cancel</button>
      <button class="adm-btn danger" id="jdDelete" ${!running && role === "super_admin" ? "" : "disabled"} data-act="del">✕ Delete</button>`)}
    <div class="adm-grid-2">
      <div class="adm-card">
        <div class="adm-card-title">Run info</div>
        <div class="adm-kv">
          <dt>Run ID</dt><dd><span class="adm-code">${esc(runId)}</span></dd>
          ${j.retried_from ? `<dt>Retry of</dt><dd><a class="adm-link" href="#/jobs/details:${esc(j.retried_from)}">${esc(j.retried_from)}</a></dd>` : ""}
          <dt>Query / URL</dt><dd>${url ? `<a class="adm-link" href="${esc(url)}" target="_blank" rel="noopener">${esc(String(url).slice(0, 90))}</a>` : "—"}</dd>
          <dt>Platform</dt><dd>${platformBadge(platform)}</dd>
          <dt>Status</dt><dd>${statusBadge(j.status)}${j.status === "error" ? `<div class="adm-cell-sub" style="color:var(--red)">${esc((j.message || j.error || "Failed").slice(0, 300))}</div>` : ""}</dd>
          <dt>Phase</dt><dd>${esc(j.phase || "—")}${j.message ? `<div class="adm-cell-sub">${esc(String(j.message).slice(0, 300))}</div>` : ""}</dd>
          <dt>Provider</dt><dd>${esc(j.provider || "—")}</dd>
          <dt>Created</dt><dd>${fmtTime(j.created_at)}</dd>
          <dt>Last update</dt><dd>${fmtTime(j.updated_at)}</dd>
          <dt>Completed</dt><dd>${j.completed_at ? fmtTime(j.completed_at) : running ? `<span class="adm-badge blue">in progress</span>` : "—"}</dd>
          <dt>Duration</dt><dd>${duration}</dd>
        </div>
      </div>
      <div class="adm-card">
        <div class="adm-card-title">Collected data <span class="adm-hint">(counted live from pages/posts/comments/leads)</span></div>
        <div class="adm-stats">
          <div class="adm-stat"><div class="adm-stat-label">Pages</div><div class="adm-stat-value">${counts.pages}</div></div>
          <div class="adm-stat"><div class="adm-stat-label">Posts</div><div class="adm-stat-value">${counts.posts}</div></div>
          <div class="adm-stat"><div class="adm-stat-label">Comments</div><div class="adm-stat-value">${counts.comments}</div></div>
          <div class="adm-stat"><div class="adm-stat-label">AI analyzed</div><div class="adm-stat-value">${counts.analyzed}</div></div>
          <div class="adm-stat"><div class="adm-stat-label">Leads</div><div class="adm-stat-value ${counts.leads ? "ok" : ""}">${counts.leads}</div></div>
        </div>
        <div class="adm-kv" style="margin-top:14px">
          <dt>Pages found (run record)</dt><dd>${esc(j.pages_found ?? "—")}</dd>
          <dt>Pages stored (run record)</dt><dd>${esc(j.pages_stored ?? "—")}</dd>
          ${j.limit !== undefined ? `<dt>Post limit</dt><dd>${esc(j.limit)}</dd>` : ""}
          ${intent.max_comments_per_post ? `<dt>Comments per post</dt><dd>${esc(intent.max_comments_per_post)}</dd>` : ""}
          ${j.usageUsd !== undefined && j.usageUsd !== null ? `<dt>Apify cost (USD)</dt><dd>$${money(j.usageUsd)}</dd>` : ""}
          ${j.actorRunId ? `<dt>Apify run</dt><dd><span class="adm-code">${esc(j.actorRunId)}</span></dd>` : ""}
        </div>
        <div class="adm-btn-row" style="margin-top:14px">
          <a class="adm-btn" href="#/pages?run=${encodeURIComponent(runId)}">Pages of this run</a>
          <a class="adm-btn" href="#/logs?q=${encodeURIComponent(runId)}">View logs</a>
        </div>
      </div>
    </div>
    <div class="adm-card">
      <div class="adm-card-title">Raw run record</div>
      <pre class="adm-log" style="margin:0;padding:14px;font-family:'JetBrains Mono',ui-monospace,monospace;font-size:12px;line-height:1.55;color:var(--text-2);max-height:360px;overflow:auto;white-space:pre-wrap;word-break:break-word">${esc(JSON.stringify({ ...j, intent }, null, 2).slice(0, 6000))}</pre>
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
  root.innerHTML = `
    ${pageHead("Failed Jobs", "Runs that ended in an error, with the failure reason and a one-click retry.", `
      <span class="adm-badge red">${data.total} failed</span>`)}
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
          : `<tr><td colspan="6">${emptyState("✓", "No failed jobs. Everything is healthy.")}</td></tr>`}
        </tbody>
      </table></div>
    </div>`;
  $$("[data-job]", root).forEach((row) => {
    row.onclick = (e) => {
      if (e.target.closest("button")) return;
      navigate(`jobs/details:${row.dataset.job}`);
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
  root.innerHTML = `
    ${pageHead("Leads", "AI-analyzed comments. Update their pipeline status, bulk-mark, export or delete.")}
    <div class="adm-card">
      <div class="adm-filters">
        <input class="adm-input" id="lPlat" placeholder="Platform" list="platOpts" value="${esc(f.platform)}">
        <input class="adm-input" id="lQuality" placeholder="Quality" list="qualityOpts" value="${esc(f.quality)}">
        <datalist id="qualityOpts"><option>hot</option><option>warm</option><option>cold</option></datalist>
        <select class="adm-select" id="lStatus">
          <option value="">Any status</option>
          ${statuses.map((s) => `<option value="${s}" ${f.status === s ? "selected" : ""}>${s}</option>`).join("")}
        </select>
        <input class="adm-input" id="lQ" placeholder="Text / name / phone / email…" value="${esc(f.q)}">
        <button class="adm-btn primary" id="applyFilters">Filter</button>
        <button class="adm-btn" id="clearFilters">Clear</button>
      </div>
      <div class="adm-flex" style="margin-bottom:12px">
        <select class="adm-select" id="bulkStatus" ${role === "viewer" ? "disabled" : ""}>
          ${statuses.map((s) => `<option value="${s}">Mark selected → ${s}</option>`).join("")}
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
                : `<span class="adm-badge">no contact</span>`}</td>
              <td>${relativeTime(l.analyzed_at)}</td>
            </tr>`).join("")
          : `<tr><td colspan="${role !== "viewer" ? 9 : 8}">${emptyState("◎", "No leads match these filters.")}</td></tr>`}
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
    return `<button class="adm-btn small" data-copy="${esc(String(v))}">⇪ Copy</button>
      <a class="adm-btn small" href="${href}">Open</a>`;
  };
  openModal(`Lead — ${esc(lead.commenter_name || "Unknown")}`, `
    <div class="adm-kv">
      <dt>Commenter</dt><dd><div class="adm-cell-main">${esc(lead.commenter_name || "—")}</div></dd>
      <dt>Page</dt><dd>${esc(lead.page_name || "—")}</dd>
      <dt>Platform</dt><dd>${platformBadge(lead.platform)}</dd>
      <dt>Comment</dt><dd><div style="max-width:520px;font-size:13px;line-height:1.5">${esc((lead.comment_text || "").slice(0, 600))}</div></dd>
      <dt>Post</dt><dd>${lead.post_url ? `<a class="adm-link" href="${esc(lead.post_url)}" target="_blank" rel="noopener">open post ↗</a>` : "—"}</dd>
      <dt>Lead score</dt><dd>${scorePill(lead.lead_score)}${lead.signal_score !== undefined ? ` <span class="adm-hint">signal ${esc(lead.signal_score)}</span>` : ""}</dd>
      <dt>Quality</dt><dd>${qualityBadge(lead.lead_quality)}</dd>
      <dt>Priority</dt><dd><span class="adm-badge">${esc(lead.priority || "—")}</span></dd>
      <dt>Confidence</dt><dd>${lead.confidence !== undefined ? `${(Number(lead.confidence) * 100).toFixed(0)}%` : "—"}</dd>
      <dt>Intent</dt><dd>${esc(lead.intent || "—")}</dd>
      ${lead.budget ? `<dt>Budget</dt><dd>${esc(lead.budget)}</dd>` : ""}
      ${lead.requirement ? `<dt>Requirement</dt><dd>${esc(lead.requirement)}</dd>` : ""}
      ${lead.urgency ? `<dt>Urgency</dt><dd>${esc(lead.urgency)}</dd>` : ""}
      ${lead.location ? `<dt>Location</dt><dd>${esc(lead.location)}</dd>` : ""}
      ${lead.website ? `<dt>Website</dt><dd><a class="adm-link" href="${esc(lead.website)}" target="_blank" rel="noopener">${esc(lead.website)}</a></dd>` : ""}
      <dt>Reason</dt><dd><div class="adm-cell-sub">${esc((lead.reason || "").slice(0, 300))}</div></dd>
      <dt>Analyzed</dt><dd>${fmtTime(lead.analyzed_at)} by <span class="adm-badge ${lead.analyzed_by === "gemini" ? "violet" : "cyan"}">${esc(lead.analyzed_by || "—")}</span></dd>
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
  const maxJobs = Math.max(1, ...data.jobs_series.map((d) => d.jobs));
  const maxLeads = Math.max(1, ...data.leads_series.map((d) => d.leads));
  const palette = ["#4f8cff", "#8b5cf6", "#22d3ee", "#34d399", "#fbbf24", "#f87171", "#c084fc"];
  const qualityTotal = Object.values(data.quality).reduce((a, b) => a + b, 0) || 1;
  const platTotal = Object.values(data.leads_by_platform).reduce((a, b) => a + b, 0) || 1;
  const t = data.totals || {};
  const rangeBtn = (days, label) =>
    `<button class="adm-btn small ${r.days === days && !r.from ? "primary" : ""}" data-range="${days}">${label}</button>`;
  root.innerHTML = `
    ${pageHead("Analytics", "Aggregations over real database records for the selected range.", `
      <button class="adm-btn small" data-range="today">Today</button>
      ${rangeBtn(7, "7 days")}${rangeBtn(14, "14 days")}${rangeBtn(30, "30 days")}${rangeBtn(90, "90 days")}
      <input class="adm-input" type="date" id="anFrom" title="From" value="${esc(r.from)}" style="max-width:150px">
      <input class="adm-input" type="date" id="anTo" title="To" value="${esc(r.to)}" style="max-width:150px">
      <button class="adm-btn primary small" id="anApply">Apply</button>`)}
    <div class="adm-stats">
      <div class="adm-stat"><div class="adm-stat-label">Jobs</div><div class="adm-stat-value">${t.jobs ?? "—"}</div><div class="adm-stat-hint">${t.completed ?? 0} completed</div></div>
      <div class="adm-stat"><div class="adm-stat-label">Success rate</div><div class="adm-stat-value">${t.success_rate !== null && t.success_rate !== undefined ? `${t.success_rate}%` : "—"}</div><div class="adm-stat-hint">${t.failed ?? 0} failed</div></div>
      <div class="adm-stat"><div class="adm-stat-label">Pages</div><div class="adm-stat-value">${(t.pages ?? 0).toLocaleString()}</div></div>
      <div class="adm-stat"><div class="adm-stat-label">Posts</div><div class="adm-stat-value">${(t.posts ?? 0).toLocaleString()}</div></div>
      <div class="adm-stat"><div class="adm-stat-label">Comments</div><div class="adm-stat-value">${(t.comments ?? 0).toLocaleString()}</div></div>
      <div class="adm-stat"><div class="adm-stat-label">AI analyzed</div><div class="adm-stat-value">${(t.analyzed ?? 0).toLocaleString()}</div></div>
      <div class="adm-stat"><div class="adm-stat-label">Leads</div><div class="adm-stat-value">${(t.leads ?? 0).toLocaleString()}</div></div>
    </div>
    <div class="adm-grid-2">
      <div class="adm-card">
        <div class="adm-card-title">Jobs per day</div>
        <div class="adm-bars">
          ${data.jobs_series.length ? data.jobs_series.map((d) => `
            <div class="adm-bar-col" title="${esc(d.date)}">
              <div class="adm-bar-value">${d.jobs}</div>
              <div class="adm-bar ${d.failed ? "failed" : ""}" style="height:${Math.round((d.jobs / maxJobs) * 100)}%"></div>
              <div class="adm-bar-label">${esc(d.date.slice(5))}</div>
            </div>`).join("") : emptyState("◔", "No jobs in range.")}
        </div>
      </div>
      <div class="adm-card">
        <div class="adm-card-title">Leads per day</div>
        <div class="adm-bars">
          ${data.leads_series.length ? data.leads_series.map((d) => `
            <div class="adm-bar-col" title="${esc(d.date)}">
              <div class="adm-bar-value">${d.leads}</div>
              <div class="adm-bar" style="height:${Math.round((d.leads / maxLeads) * 100)}%"></div>
              <div class="adm-bar-label">${esc(d.date.slice(5))}</div>
            </div>`).join("") : emptyState("◎", "No leads analyzed in range.")}
        </div>
      </div>
      <div class="adm-card">
        <div class="adm-card-title">Job statuses</div>
        ${Object.keys(data.job_statuses).length ? `
          <div class="adm-donut-legend">
            ${Object.entries(data.job_statuses).map(([k, v], i) => `
              <div class="adm-legend-row">
                <span class="adm-legend-swatch" style="background:${palette[i % palette.length]}"></span>
                ${statusBadge(k)} <span>${v}</span>
              </div>`).join("")}
          </div>` : emptyState("◔", "No jobs yet.")}
      </div>
      <div class="adm-card">
        <div class="adm-card-title">Leads by platform</div>
        ${Object.keys(data.leads_by_platform).length ? `
          <div class="adm-donut-legend">
            ${Object.entries(data.leads_by_platform).map(([k, v], i) => `
              <div class="adm-legend-row">
                <span class="adm-legend-swatch" style="background:${palette[i % palette.length]}"></span>
                ${platformBadge(k)} <span>${v} (${Math.round((v / platTotal) * 100)}%)</span>
              </div>`).join("")}
          </div>` : emptyState("◎", "No leads yet.")}
      </div>
      <div class="adm-card">
        <div class="adm-card-title">Lead quality distribution</div>
        ${Object.keys(data.quality).length ? `
          <div class="adm-donut-legend">
            ${Object.entries(data.quality).map(([k, v], i) => `
              <div class="adm-legend-row">
                <span class="adm-legend-swatch" style="background:${palette[i % palette.length]}"></span>
                ${qualityBadge(k)} <span>${v} (${Math.round((v / qualityTotal) * 100)}%)</span>
              </div>`).join("")}
          </div>` : emptyState("≈", "No analyzed comments yet.")}
      </div>
      <div class="adm-card">
        <div class="adm-card-title">Lead score distribution</div>
        ${Object.keys(data.score_distribution || {}).length ? `
          <div class="adm-donut-legend">
            ${Object.entries(data.score_distribution).map(([k, v], i) => `
              <div class="adm-legend-row">
                <span class="adm-legend-swatch" style="background:${palette[i % palette.length]}"></span>
                <span class="adm-code">${esc(k)}</span> <span>${v}</span>
              </div>`).join("")}
          </div>` : emptyState("≈", "No scored leads in range.")}
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
        </table></div>` : emptyState("⬡", "No platform data in range.")}
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
          </table></div>` : emptyState("◎", "No lead data yet.")}
      </div>
      <div class="adm-card">
        <div class="adm-card-title">Lead pipeline status</div>
        <div class="adm-donut-legend">
          ${Object.entries(data.lead_statuses || {}).map(([k, v]) => `
            <div class="adm-legend-row">${leadStatusBadge(k)} <span>${v}</span></div>`).join("")}
        </div>
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
      <span class="adm-badge">${data.platforms.filter((p) => p.enabled).length}/${data.platforms.length} enabled</span>`)}
    <div class="adm-grid-2">
      ${data.platforms.map((p) => `
        <div class="adm-card">
          <div class="adm-flex">
            <h3 class="adm-card-title" style="margin:0">${platformBadge(p.platform)}</h3>
            <span class="adm-spacer"></span>
            <label class="adm-toggle" title="Enable/disable ${esc(p.platform)}">
              <input type="checkbox" data-toggle data-p="${esc(p.platform)}" ${p.enabled ? "checked" : ""} ${role === "viewer" ? "disabled" : ""}>
              <span class="adm-toggle-slider"></span>
            </label>
          </div>
          <div class="adm-kv" style="margin-top:12px">
            <dt>Pages</dt><dd>${p.stats.pages}</dd>
            <dt>Posts</dt><dd>${p.stats.posts}</dd>
            <dt>Comments</dt><dd>${p.stats.comments}</dd>
            <dt>Leads</dt><dd>${p.stats.leads}</dd>
          </div>
          <div class="adm-card-title" style="margin:16px 0 10px">Actors</div>
          ${p.actors.map((a) => `
            <div class="adm-flex" style="margin-bottom:8px">
              <span class="adm-badge violet">${esc(a.kind)}</span>
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
    root.innerHTML = emptyState("⬡", `Platform "${esc(platform)}" is not in the configured list.`);
    return;
  }
  root.innerHTML = `
    ${pageHead(`${p.platform[0].toUpperCase()}${p.platform.slice(1)}`, "Platform detail — live statistics, actors and recent runs.", `
      <a class="adm-btn" href="#/platforms">← All Platforms</a>`)}
    <div class="adm-grid-2">
      <div class="adm-card">
        <div class="adm-flex">
          <h3 class="adm-card-title" style="margin:0">${platformBadge(p.platform)}</h3>
          <span class="adm-spacer"></span>
          <label class="adm-toggle" title="Enable/disable ${esc(p.platform)}">
            <input type="checkbox" id="pToggle" ${p.enabled ? "checked" : ""} ${role === "viewer" ? "disabled" : ""}>
            <span class="adm-toggle-slider"></span>
          </label>
        </div>
        <div class="adm-kv" style="margin-top:12px">
          <dt>Status</dt><dd>${p.enabled ? `<span class="adm-badge green">enabled</span>` : `<span class="adm-badge red">disabled</span>`}</dd>
          <dt>Pages</dt><dd>${p.stats.pages}</dd>
          <dt>Posts</dt><dd>${p.stats.posts}</dd>
          <dt>Comments</dt><dd>${p.stats.comments}</dd>
          <dt>Leads</dt><dd>${p.stats.leads}</dd>
        </div>
      </div>
      <div class="adm-card">
        <div class="adm-card-title">Actors used by this platform</div>
        ${p.actors.map((a) => `
          <div class="adm-flex" style="margin-bottom:8px">
            <span class="adm-badge violet">${esc(a.kind)}</span>
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
              <tr class="adm-row-link" data-nav="jobs/details:${esc(j.run_id)}">
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
      : emptyState("⇶", `No runs on ${esc(platform)} yet.`)}
    </div>`;
  const toggle = $("#pToggle");
  if (toggle) toggle.onchange = async () => {
    try {
      await api(`/api/admin/platforms/${encodeURIComponent(platform)}/toggle`, { method: "POST" });
      toast(`${platform} ${toggle.checked ? "enabled" : "disabled"}`, toggle.checked ? "ok" : "warn");
      viewPlatformDetail(platform);
    } catch (err) { toast(err.message, "error"); toggle.checked = !toggle.checked; }
  };
  bindNav(root);
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
      <a class="adm-btn" href="/api/admin/export/pages.csv" ${state.user.role === "viewer" ? "onclick='return false'" : ""}>⇩ Export CSV</a>`)}
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
                <div class="adm-cell-sub"><a class="adm-link" href="${esc(p.facebook_url || "#")}" target="_blank" rel="noopener">open profile ↗</a></div></td>
              <td>${platformBadge(p.platform)}</td>
              <td>${esc(p.category || "—")}</td>
              <td>${esc(p.city || "—")}</td>
              <td>${(p.followers || 0).toLocaleString()}</td>
              <td>${p.phone || p.email || p.whatsapp || p.website
                ? `<div class="adm-cell-sub">${[p.phone, p.whatsapp, p.email].filter(Boolean).map((v) => esc(String(v))).join(" · ")}</div>`
                : `<span class="adm-badge">no contact</span>`}</td>
              <td>${relativeTime(p.collected_at)}</td>
            </tr>`).join("")
          : `<tr><td colspan="7">${emptyState("▥", "No pages match these filters.")}</td></tr>`}
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
      <dt>URL</dt><dd>${page.facebook_url ? `<a class="adm-link" href="${esc(page.facebook_url)}" target="_blank" rel="noopener">${esc(page.facebook_url)}</a>` : "—"}</dd>
      <dt>Category</dt><dd>${esc(page.category || "—")}</dd>
      <dt>City / State</dt><dd>${[page.city, page.state].filter(Boolean).map(esc).join(", ") || "—"}</dd>
      <dt>Followers</dt><dd>${(page.followers || 0).toLocaleString()}</dd>
      <dt>Verified</dt><dd>${page.verified ? "✓" : "—"}</dd>
      <dt>Phone</dt><dd>${esc(page.phone || "—")}</dd>
      <dt>Email</dt><dd>${esc(page.email || "—")}</dd>
      <dt>Website</dt><dd>${page.website ? `<a class="adm-link" href="${esc(page.website)}" target="_blank" rel="noopener">${esc(page.website)}</a>` : "—"}</dd>
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
      <a class="adm-btn" href="/api/admin/export/posts.csv" ${state.user.role === "viewer" ? "onclick='return false'" : ""}>⇩ Export CSV</a>`)}
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
          : `<tr><td colspan="7">${emptyState("▤", "No posts match these filters.")}</td></tr>`}
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
      <dt>URL</dt><dd>${post.post_url ? `<a class="adm-link" href="${esc(post.post_url)}" target="_blank" rel="noopener">open post ↗</a>` : "—"}</dd>
      <dt>Page</dt><dd>${esc(post.page_name || "—")}</dd>
      <dt>Platform</dt><dd>${platformBadge(post.platform)}</dd>
      <dt>Caption</dt><dd><div style="max-width:520px;font-size:13px;line-height:1.5;white-space:pre-wrap">${esc((post.caption || post.description || "").slice(0, 1200))}</div></dd>
      ${post.hashtags && post.hashtags.length ? `<dt>Hashtags</dt><dd>${post.hashtags.map((h) => `<span class="adm-badge cyan">${esc(h)}</span>`).join(" ")}</dd>` : ""}
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
  // Note: /api/admin/apify/test is a POST that performs a real Apify API
  // probe, so it must NOT run on page load (wrong method + wasted call).
  // Load status only; the probe runs on demand via the "Run live test" button.
  const status = await api("/api/admin/apify");
  const role = state.user.role;
  root.innerHTML = `
    ${pageHead("Apify", "Connection status, connection test and token management. Tokens are never displayed — only a masked hint.", `
      <a class="adm-btn" href="#/usage">Usage & Cost</a>`)}
    <div class="adm-grid-2">
      <div class="adm-card">
        <div class="adm-card-title">Connection</div>
        <div class="adm-kv">
          <dt>Token</dt><dd>${status.token_configured ? `<span class="adm-badge green">${esc(status.token_hint)}</span>` : `<span class="adm-badge red">not configured</span>`}</dd>
          <dt>Source</dt><dd>${status.env_token_configured && status.token_hint ? "env override active" : status.env_token_configured ? "environment (.env)" : "—"}</dd>
          <dt>Last test</dt><dd>${status.last_test_at ? `${status.last_test_ok ? "✓ ok" : "✕ failed"} · ${fmtTime(new Date(status.last_test_at * 1000))}` : "never"}</dd>
        </div>
        <div class="adm-btn-row" style="margin-top:14px">
          <button class="adm-btn primary" id="probeBtn" ${role === "viewer" ? "disabled" : ""}>⟳ Run live test</button>
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
    <div class="adm-card" id="probeResult" hidden></div>`;
  const probeBtn = $("#probeBtn");
  if (probeBtn) probeBtn.onclick = async () => {
    probeBtn.disabled = true;
    probeBtn.textContent = "⟳ Testing…";
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
      probeBtn.textContent = "⟳ Run live test";
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
      <button class="adm-btn" id="refreshActors">↻ Refresh</button>`)}
    <div class="adm-card">
      <div class="adm-table-wrap"><table class="adm-table">
        <thead><tr><th>Key</th><th>Platform</th><th>Current actor id</th><th>Source</th><th></th></tr></thead>
        <tbody>
          ${data.actors.map((a) => `
            <tr>
              <td><span class="adm-code">${esc(a.key)}</span></td>
              <td>${platformBadge(a.platform)}</td>
              <td><div class="adm-cell-main">${esc(a.value)}</div></td>
              <td>${a.overridden ? `<span class="adm-badge amber">override</span> <span class="adm-hint">default: ${esc(a.default)}</span>` : `<span class="adm-badge">default</span>`}</td>
              <td><button class="adm-btn small" data-test data-key="${esc(a.key)}" ${role === "viewer" ? "disabled" : ""}>Test</button></td>
            </tr>`).join("")}
        </tbody>
      </table></div>
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
      <span class="adm-badge ${data.total_cost > 0 ? "amber" : ""}">$${money(data.total_cost)} total (${data.runs_with_usage} runs with usage data)</span>`)}
    <div class="adm-note">${esc(data.note)}</div>
    <div class="adm-card">
      <div class="adm-bars">
        ${data.actors.length ? data.actors.map((a) => `
          <div class="adm-bar-col" title="${esc(a.actor)}">
            <div class="adm-bar-value">${a.runs}</div>
            <div class="adm-bar" style="height:${Math.round((a.runs / maxRuns) * 100)}%"></div>
            <div class="adm-bar-label">${esc(a.actor.split("/").pop().slice(0, 14))}</div>
          </div>`).join("") : emptyState("¥", "No Apify runs with usage data in the last 30 days.")}
      </div>
      <div class="adm-table-wrap" style="margin-top:16px"><table class="adm-table">
        <thead><tr><th>Actor</th><th>Runs</th><th>Cost (USD)</th></tr></thead>
        <tbody>
          ${data.actors.map((a) => `
            <tr>
              <td><div class="adm-cell-main">${esc(a.actor)}</div></td>
              <td>${a.runs}</td>
              <td>${a.cost > 0 ? `$${money(a.cost)}` : `<span class="adm-badge">not reported</span>`}</td>
            </tr>`).join("")}
        </tbody>
      </table></div>
    </div>`;
}

/* ──────────────────────────────── ENVIRONMENT ──────────────────────── */
async function viewEnvironment() {
  const root = $("#view");
  let lock;
  try {
    lock = await api("/api/admin/env/lock-status");
  } catch (err) {
    throw err;
  }
  if (lock.locked) {
    renderEnvLock(root);
    return;
  }
  let data;
  try {
    data = await api("/api/admin/env");
  } catch (err) {
    if (err.message.includes("locked")) {
      renderEnvLock(root);
      return;
    }
    throw err;
  }
  const vars = data.vars;
  const canWrite = state.user.role !== "viewer";
  const groups = [...new Set(vars.map((v) => v.group))];
  const sourceBadge = (v) => v.source === "override"
    ? `<span class="adm-badge green">override</span>`
    : v.source === "env"
      ? `<span class="adm-badge cyan">.env</span>`
      : `<span class="adm-badge">default</span>`;
  const valueCell = (v) => {
    if (v.secret) return `<span class="adm-code">${v.masked ? esc(v.masked) : "—"}</span>`;
    const raw = String(v.value === undefined || v.value === null ? "" : v.value);
    return `<span class="adm-code">${esc(raw) || "—"}</span>`;
  };
  root.innerHTML = `
    ${pageHead("Environment", "Live environment variables. An override row wins; otherwise the real .env value applies; otherwise the documented default. Changes take effect immediately unless marked 'needs restart'.", `
      <button class="adm-btn" id="envLockBtn">🔒 Lock now</button>`)}
    <div class="adm-note">Secrets are never displayed — only a masked hint. Every value below is editable (manager+; the guard password is the protection). Overrides are stored in the database and survive restarts; they do not rewrite your .env file. The section stays open for ${lock.unlock_minutes} minutes after unlocking.</div>
    ${canWrite ? `
    <div class="adm-card" style="margin-bottom:16px">
      <div class="adm-card-title">Recovery admin password</div>
      <div class="adm-field" style="margin-bottom:12px">
        <label>New password</label>
        <input class="adm-input" id="envPass" type="password" placeholder="min 8 characters">
        <span class="adm-hint">Hashed (sha256) server-side and stored as an ADMIN_PASSWORD_HASH override. Effective on the next login.</span>
      </div>
      <button class="adm-btn primary" id="envPassBtn">Change password</button>
    </div>` : ""}
    ${groups.map((group) => `
      <div class="adm-card" style="margin-bottom:16px">
        <div class="adm-card-title">${esc(group)}</div>
        <div class="adm-table-wrap"><table class="adm-table">
          <thead><tr><th>Variable</th><th>Value</th><th>Source</th><th>Notes</th><th style="width:150px"></th></tr></thead>
          <tbody>
            ${vars.filter((v) => v.group === group).map((v) => `
              <tr>
                <td><div class="adm-cell-main">${esc(v.name)}</div>
                  <div class="adm-cell-sub">${esc(v.description)}</div></td>
                <td>${valueCell(v)}</td>
                <td>${sourceBadge(v)}${v.overridden ? `<div class="adm-cell-sub">${v.updated_by || "admin"} · ${v.updated_at ? fmtTime(new Date(v.updated_at * 1000)) : ""}</div>` : ""}</td>
                <td>${v.restart ? `<span class="adm-badge amber">needs restart</span>` : `<span class="adm-badge green">applies now</span>`}${v.secret ? `<span class="adm-badge">secret</span>` : ""}</td>
                <td>${canWrite
                  ? `<div class="adm-btn-row" style="gap:6px">
                      <button class="adm-btn" data-env-edit="${esc(v.name)}">Edit</button>
                      ${v.overridden ? `<button class="adm-btn danger" data-env-reset="${esc(v.name)}">Reset</button>` : ""}
                    </div>`
                  : `<span class="adm-badge">read only</span>`}</td>
              </tr>`).join("")}
          </tbody>
        </table></div>
      </div>`).join("")}`;
  const lockBtn = $("#envLockBtn");
  if (lockBtn) lockBtn.onclick = async () => {
    await api("/api/admin/env/unlock", { method: "DELETE" });
    toast("Environment panel locked", "ok");
    viewEnvironment();
  };
  if (canWrite) {
    $$("[data-env-edit]", root).forEach((btn) => {
      btn.onclick = () => {
        const entry = vars.find((v) => v.name === btn.dataset.envEdit);
        if (!entry) return;
        openModal(`Edit ${entry.name}`, `
          <div class="adm-field">
            <label>Value</label>
            <input class="adm-input" id="envInput" type="${entry.secret ? "password" : "text"}" value="${esc(entry.secret ? "" : entry.value)}" placeholder="${entry.kind === "int" ? "number" : entry.kind === "bool" ? "true / false" : "value"}">
            <span class="adm-hint">${esc(entry.description)}${entry.secret ? " Never displayed again — only a masked hint." : ""}</span>
          </div>`, `
          <button class="adm-btn" data-close>Cancel</button>
          <button class="adm-btn primary" id="envSaveBtn">Save</button>`);
        $("#modalBackdrop").onclick = (e) => { if (e.target.id === "modalBackdrop") closeModal(); };
        $("[data-close]", $("#modalBox")).onclick = closeModal;
        $("#envSaveBtn").onclick = async () => {
          const value = $("#envInput").value.trim();
          if (!value) { toast("A value is required", "error"); return; }
          try {
            await api(`/api/admin/env/${encodeURIComponent(entry.name)}`, { method: "PUT", body: { value } });
            toast(`${entry.name} saved`, "ok");
            closeModal();
            viewEnvironment();
          } catch (err) { toast(err.message, "error"); }
        };
      };
    });
    $$("[data-env-reset]", root).forEach((btn) => {
      btn.onclick = () => {
        const name = btn.dataset.envReset;
        confirmModal(`Reset ${name}`, `The database override will be removed; the real .env value (or default) becomes active again.`, async () => {
          await api(`/api/admin/env/${encodeURIComponent(name)}`, { method: "DELETE" });
          toast(`${name} reset`, "ok");
          viewEnvironment();
        }, "Reset");
      };
    });
    const passBtn = $("#envPassBtn");
    if (passBtn) passBtn.onclick = async () => {
      const password = $("#envPass").value;
      if (password.length < 8) { toast("Password must be at least 8 characters", "error"); return; }
      try {
        await api("/api/admin/env/password", { method: "POST", body: { new_password: password } });
        toast("Admin password changed", "ok");
        $("#envPass").value = "";
        viewEnvironment();
      } catch (err) { toast(err.message, "error"); }
    };
  }
}

function renderEnvLock(root) {
  root.innerHTML = `
    ${pageHead("Environment", "Environment variables are protected — unlock with the guard password to view and edit them.", "")}
    <div class="adm-card" style="max-width:520px">
      <div class="adm-empty">
        <div class="adm-empty-ico">🔒</div>
        <div style="font-size:14px;color:var(--text);margin-bottom:6px">Environment panel locked</div>
        <div style="font-size:12.5px;margin-bottom:16px">Every environment variable is hidden until you enter the guard password. The unlock lasts 15 minutes.</div>
        <input class="adm-input" id="envUnlockInput" type="password" placeholder="Guard password" style="max-width:280px;margin:0 auto 12px">
        <div class="adm-btn-row" style="justify-content:center">
          <button class="adm-btn primary" id="envUnlockBtn">Unlock</button>
        </div>
      </div>
    </div>`;
  const btn = $("#envUnlockBtn");
  btn.onclick = async () => {
    const password = $("#envUnlockInput").value;
    if (!password) { toast("Enter the guard password", "error"); return; }
    btn.disabled = true;
    btn.textContent = "Unlocking…";
    try {
      await api("/api/admin/env/unlock", { method: "POST", body: { password } });
      toast("Environment unlocked", "ok");
      viewEnvironment();
    } catch (err) {
      toast(err.message, "error");
      btn.disabled = false;
      btn.textContent = "Unlock";
    }
  };
  const enter = (e) => { if (e.key === "Enter") btn.onclick(); };
  $("#envUnlockInput").onkeydown = enter;
}

/* ──────────────────────────────── LIMITS ──────────────────────────── */
const LIMIT_LABELS = {
  "limits.min_comments": "Min comments to qualify a post",
  "limits.max_posts_default": "Default posts per scrape",
  "limits.max_posts_cap": "Hard cap: posts per scrape",
  "limits.max_comments_per_post_default": "Default comments per post",
  "limits.max_comments_per_post_cap": "Hard cap: comments per post",
  "limits.global_max_comments": "Global cap: total comments per run",
  "cost.stop_on_limit": "Stop collecting when the global cap is hit",
  "cost.warn_before_expensive": "Warn before an expensive scrape",
};
const LIMIT_HINTS = {
  "limits.min_comments": "Posts with fewer comments are skipped (0 disables).",
  "limits.global_max_comments": "Hard ceiling for one URL-search run.",
};

async function viewLimits() {
  const root = $("#view");
  const data = await api("/api/admin/limits");
  const keys = Object.keys(LIMIT_LABELS);
  const values = data.settings;
  root.innerHTML = `
    ${pageHead("Scraping & Global Limits", "Server-enforced limits. The backend clamps every request to these values — the frontend cannot bypass them.")}
    <div class="adm-card">
      ${settingsForm(keys, LIMIT_LABELS, values, { hints: LIMIT_HINTS })}
    </div>`;
  bindSettingsSave(root, "/api/admin/limits", viewLimits);
}

/* ──────────────────────────────── AI ──────────────────────────────── */
const AI_LABELS = {
  "ai.enabled": "AI analysis enabled",
  "ai.rule_fallback": "Rule-based fallback when Gemini fails",
  "ai.max_calls_per_job": "Max Gemini calls per job",
  "ai.temperature": "Temperature",
  "ai.model": "Gemini model",
};
const AI_HINTS = {
  "ai.temperature": "0.0–1.0; lower is more deterministic.",
  "ai.model": "e.g. gemini-2.5-flash",
};

async function viewAI() {
  const root = $("#view");
  const data = await api("/api/admin/ai");
  root.innerHTML = `
    ${pageHead("AI / Gemini", "Tune the comment-analysis model. Changes apply on the next analysis; existing results are untouched.", `
      <span class="adm-badge ${data.gemini_key_configured ? "green" : "amber"}">${data.gemini_key_configured ? "Gemini key configured" : "No Gemini key — rule-based only"}</span>`)}
    <div class="adm-grid-2">
      <div class="adm-card">
        <div class="adm-card-title">Settings</div>
        ${settingsForm(Object.keys(AI_LABELS), AI_LABELS, data.settings, { hints: AI_HINTS })}
      </div>
      <div class="adm-card">
        <div class="adm-card-title">Test the pipeline</div>
        <div class="adm-field" style="margin-bottom:12px">
          <label>Sample comment</label>
          <textarea class="adm-textarea" id="aiSample">Hi, I want to buy a 2BHK flat near Hitech City within 45 lakhs. Please call me at 9876543210.</textarea>
        </div>
        <button class="adm-btn primary" id="aiTestBtn">Run real analysis</button>
        <div id="aiResult" style="margin-top:14px"></div>
      </div>
    </div>`;
  bindSettingsSave(root, "/api/admin/ai", viewAI);
  $("#aiTestBtn").onclick = async () => {
    const btn = $("#aiTestBtn");
    btn.disabled = true;
    try {
      const res = await api("/api/admin/ai/test", { method: "POST", body: { text: $("#aiSample").value } });
      const contact = res.contact || {};
      const buyer = res.buyer || {};
      $("#aiResult").innerHTML = `
        <div class="adm-kv">
          <dt>Engine</dt><dd><span class="adm-badge ${res.analyzed_by === "gemini" ? "violet" : "cyan"}">${esc(res.analyzed_by)}</span></dd>
          <dt>Useful</dt><dd>${res.is_useful ? "✓" : "✕"} — ${esc(res.reason || "")}</dd>
          <dt>Quality</dt><dd>${qualityBadge(res.lead_quality)}</dd>
          <dt>Priority</dt><dd><span class="adm-badge">${esc(res.priority)}</span></dd>
          <dt>Lead score</dt><dd>${scorePill(res.lead_score)}</dd>
          <dt>Signal score</dt><dd>${scorePill(res.signal_score)}</dd>
          <dt>Signals</dt><dd>${res.signals.length ? res.signals.map((s) => `<span class="adm-badge cyan">${esc(s)}</span>`).join(" ") : "—"}</dd>
          <dt>Phone</dt><dd>${esc(contact.phone || contact.mobile || "—")}</dd>
          <dt>Email</dt><dd>${esc(contact.email || "—")}</dd>
          <dt>Budget</dt><dd>${esc(buyer.budget || "—")}</dd>
          <dt>Intent</dt><dd>${esc(buyer.intent || "—")}</dd>
        </div>`;
    } catch (err) { toast(err.message, "error"); }
    finally { btn.disabled = false; }
  };
}

/* ──────────────────────────────── SCORING ─────────────────────────── */
const SCORE_LABELS = {
  "scoring.confidence_weight": "Confidence weight (× score)",
  "scoring.priority_weight": "Priority weight (max)",
  "scoring.quality_weight": "Quality weight (max)",
  "scoring.contact_phone": "Contact points: phone",
  "scoring.contact_email": "Contact points: email",
  "scoring.spam_penalty": "Spam penalty (× spam score)",
  "scoring.phone": "Signal score: phone",
  "scoring.email": "Signal score: email",
  "scoring.budget": "Signal score: budget",
  "scoring.urgency": "Signal score: urgency",
  "scoring.location": "Signal score: location",
  "scoring.buying_intent": "Signal score: buying intent",
  "scoring.hot_min": "Hot threshold (signal score)",
  "scoring.warm_min": "Warm threshold (signal score)",
  "scoring.derive_quality": "Derive quality from signal score",
};
const SCORE_HINTS = {
  "scoring.confidence_weight": "The base formula: confidence × weight + priority + quality + contact − spam.",
  "scoring.derive_quality": "When on, comments without an AI quality get hot/warm/cold from the signal score.",
};

async function viewScoring() {
  const root = $("#view");
  const data = await api("/api/admin/scoring");
  root.innerHTML = `
    ${pageHead("Lead Scoring", "Deterministic 0–100 lead score plus the additive signal score. Weights apply to every new analysis.")}
    <div class="adm-card">
      ${settingsForm(Object.keys(SCORE_LABELS), SCORE_LABELS, data.settings, { hints: SCORE_HINTS })}
    </div>`;
  bindSettingsSave(root, "/api/admin/scoring", viewScoring);
}

/* ──────────────────────────────── COMMENT INTELLIGENCE ────────────── */
const CI_LABELS = {
  "ci.detect_phone": "Detect phone numbers",
  "ci.detect_email": "Detect emails",
  "ci.detect_budget": "Detect budgets",
  "ci.detect_location": "Detect locations",
  "ci.detect_urgency": "Detect urgency",
  "ci.detect_buying_intent": "Detect buying intent",
  "ci.detect_selling_intent": "Detect selling intent",
  "ci.ignore_emoji_only": "Filter emoji-only comments",
  "ci.ignore_spam": "Filter spam patterns",
  "ci.ignore_low_value": "Filter low-value comments",
  "ci.min_lead_score": "Min lead score to count as lead",
};
const CI_HINTS = {
  "ci.min_lead_score": "Comments scoring below this are not marked is_lead (0 = no threshold).",
};
const CI_FILTERS = { platform: "", intent: "", isLead: "", contact: false, minConf: "", q: "" };

async function viewCI() {
  const root = $("#view");
  const f = CI_FILTERS;
  const qs = qsOf({
    platform: f.platform, intent: f.intent,
    is_lead: f.isLead, q: f.q,
    contact: f.contact ? "true" : "",
    min_confidence: f.minConf,
    offset: state.filters.ciOffset || 0, limit: 25,
  });
  const [settings, data] = await Promise.all([
    api("/api/admin/comment-intelligence"),
    api(`/api/admin/comments?${qs}`),
  ]);
  const role = state.user.role;
  const sm = data.summary || {};
  const intentOpts = Object.keys(sm.intents || {});
  const chip = (label, value) => `
    <div class="adm-stat"><div class="adm-stat-label">${label}</div><div class="adm-stat-value">${value.toLocaleString()}</div></div>`;
  root.innerHTML = `
    ${pageHead("Comment Intelligence", "Real analyzed comments with the signals the pipeline extracted. Filters hit the live ai_comments collection.")}
    <div class="adm-stats">
      ${chip("Total comments", sm.total ?? 0)}
      ${chip("Analyzed", sm.analyzed ?? 0)}
      ${chip("With contact info", sm.contacts ?? 0)}
      ${chip("Potential leads", sm.leads ?? 0)}
      ${chip("High value (score ≥ 80)", sm.high_value ?? 0)}
      <div class="adm-stat"><div class="adm-stat-label">Avg confidence</div><div class="adm-stat-value">${sm.avg_confidence !== null && sm.avg_confidence !== undefined ? `${(sm.avg_confidence * 100).toFixed(0)}%` : "—"}</div></div>
      <div class="adm-stat"><div class="adm-stat-label">Avg lead score</div><div class="adm-stat-value">${sm.avg_score !== null && sm.avg_score !== undefined ? sm.avg_score : "—"}</div></div>
    </div>
    <div class="adm-card" style="margin-bottom:16px">
      <div class="adm-card-title">Signal detection settings</div>
      ${settingsForm(Object.keys(CI_LABELS), CI_LABELS, settings.settings, { hints: CI_HINTS })}
    </div>
    <div class="adm-card">
      <div class="adm-filters">
        <input class="adm-input" id="cPlat" placeholder="Platform" list="platOpts" value="${esc(f.platform)}">
        <input class="adm-input" id="cIntent" placeholder="Intent" list="intentOpts" value="${esc(f.intent)}">
        <datalist id="intentOpts">${intentOpts.map((i) => `<option>${esc(i)}</option>`).join("")}</datalist>
        <select class="adm-select" id="cLead">
          <option value="">Any lead status</option>
          <option value="true" ${f.isLead === "true" ? "selected" : ""}>Only leads</option>
          <option value="false" ${f.isLead === "false" ? "selected" : ""}>Non-leads only</option>
        </select>
        <input class="adm-input" id="cConf" placeholder="Min confidence (0–1)" value="${esc(f.minConf)}" style="max-width:150px">
        <label class="adm-flex" style="gap:8px;align-items:center">
          <input type="checkbox" id="cContact" ${f.contact ? "checked" : ""}>
          <span class="adm-hint">with contact info</span>
        </label>
        <input class="adm-input" id="cQ" placeholder="Text / name / phone / email…" value="${esc(f.q)}">
        <button class="adm-btn primary" id="applyCI">Filter</button>
        <button class="adm-btn" id="clearCI">Clear</button>
      </div>
      <div class="adm-note">${data.total.toLocaleString()} comment(s) match the filters.</div>
      <div class="adm-table-wrap"><table class="adm-table">
        <thead><tr><th>Commenter</th><th>Comment</th><th>Platform</th><th>Intent</th><th>Score</th><th>Confidence</th><th>Contact</th><th>Lead</th><th>Analyzed</th></tr></thead>
        <tbody>
          ${data.items.length ? data.items.map((c) => `
            <tr class="adm-row-link" data-comment="${esc(c._id)}">
              <td><div class="adm-cell-main">${esc(c.commenter_name || "—")}</div>
                <div class="adm-cell-sub">${esc(c.page_name || "")}</div></td>
              <td><div class="adm-cell-sub" style="max-width:300px">${esc((c.comment_text || "").slice(0, 130))}</div></td>
              <td>${platformBadge(c.platform)}</td>
              <td>${c.intent ? `<span class="adm-badge cyan">${esc(c.intent)}</span>` : "—"}</td>
              <td>${scorePill(c.lead_score)}</td>
              <td>${c.confidence !== undefined && c.confidence !== null ? `${(Number(c.confidence) * 100).toFixed(0)}%` : "—"}</td>
              <td>${c.phone || c.email || c.whatsapp
                ? `<div class="adm-cell-sub">${[c.phone, c.whatsapp, c.email].filter(Boolean).map((v) => esc(String(v))).join(" · ")}</div>`
                : `<span class="adm-badge">none</span>`}</td>
              <td>${c.is_lead ? `<span class="adm-badge green">lead</span>` : `<span class="adm-badge">no</span>`}</td>
              <td>${relativeTime(c.analyzed_at)}</td>
            </tr>`).join("")
          : `<tr><td colspan="9">${emptyState("♜", "No comments match these filters.")}</td></tr>`}
        </tbody>
      </table></div>
      ${pagerHtml(data.total, data.offset, data.limit, (dir) => {
        const off = data.offset + dir * data.limit;
        state.filters.ciOffset = Math.max(0, off);
        viewCI();
      })}
    </div>`;
  bindSettingsSave(root, "/api/admin/comment-intelligence", viewCI);
  const apply = () => {
    state.filters.ciOffset = 0;
    f.platform = $("#cPlat").value.trim();
    f.intent = $("#cIntent").value.trim();
    f.isLead = $("#cLead").value;
    f.minConf = $("#cConf").value.trim();
    f.contact = $("#cContact").checked;
    f.q = $("#cQ").value.trim();
    viewCI();
  };
  $("#applyCI").onclick = apply;
  $("#clearCI").onclick = () => {
    Object.assign(f, { platform: "", intent: "", isLead: "", contact: false, minConf: "", q: "" });
    state.filters.ciOffset = 0;
    viewCI();
  };
  $$("[data-comment]", root).forEach((row) => {
    row.onclick = () => openCommentDetail(
      data.items.find((c) => c._id === row.dataset.comment));
  });
}

function openCommentDetail(c) {
  const contacts = [c.phone, c.whatsapp, c.email].filter(Boolean);
  const href = (v) => String(v).includes("@") ? `mailto:${encodeURIComponent(String(v))}` : `tel:${encodeURIComponent(String(v))}`;
  openModal(`Comment — ${esc(c.commenter_name || "Unknown")}`, `
    <div class="adm-kv">
      <dt>Commenter</dt><dd><div class="adm-cell-main">${esc(c.commenter_name || "—")}</div></dd>
      <dt>Page</dt><dd>${esc(c.page_name || "—")}</dd>
      <dt>Platform</dt><dd>${platformBadge(c.platform)}</dd>
      <dt>Comment</dt><dd><div style="max-width:520px;font-size:13px;line-height:1.5">${esc((c.comment_text || "").slice(0, 1200))}</div></dd>
      <dt>Post</dt><dd>${c.post_url ? `<a class="adm-link" href="${esc(c.post_url)}" target="_blank" rel="noopener">open post ↗</a>` : "—"}</dd>
      <dt>Lead score</dt><dd>${scorePill(c.lead_score)}${c.signal_score !== undefined ? ` <span class="adm-hint">signal ${esc(c.signal_score)}</span>` : ""}</dd>
      <dt>Quality</dt><dd>${qualityBadge(c.lead_quality)}</dd>
      <dt>Priority</dt><dd><span class="adm-badge">${esc(c.priority || "—")}</span></dd>
      <dt>Confidence</dt><dd>${c.confidence !== undefined && c.confidence !== null ? `${(Number(c.confidence) * 100).toFixed(0)}%` : "—"}</dd>
      <dt>Intent</dt><dd>${esc(c.intent || "—")}</dd>
      <dt>Is lead</dt><dd>${c.is_lead ? `<span class="adm-badge green">yes</span>` : `<span class="adm-badge">no</span>`}</dd>
      ${c.budget ? `<dt>Budget</dt><dd>${esc(c.budget)}</dd>` : ""}
      ${c.requirement ? `<dt>Requirement</dt><dd>${esc(c.requirement)}</dd>` : ""}
      ${c.urgency ? `<dt>Urgency</dt><dd>${esc(c.urgency)}</dd>` : ""}
      ${c.location ? `<dt>Location</dt><dd>${esc(c.location)}</dd>` : ""}
      <dt>Analyzed</dt><dd>${fmtTime(c.analyzed_at)} by <span class="adm-badge ${c.analyzed_by === "gemini" ? "violet" : "cyan"}">${esc(c.analyzed_by || "—")}</span></dd>
      ${contacts.length ? `<dt>Contact</dt><dd>${contacts.map((v) => `
        <div style="margin-bottom:4px;display:flex;gap:8px;align-items:center">
          <span class="adm-code">${esc(String(v))}</span>
          <a class="adm-btn small" href="${href(v)}">Open</a>
        </div>`).join("")}</dd>` : ""}
    </div>`, `
    <button class="adm-btn" data-close>Close</button>
    <a class="adm-btn primary" href="#/leads/details:${esc(c._id)}">Open as lead</a>`);
  $("#modalBackdrop").onclick = (e) => { if (e.target.id === "modalBackdrop") closeModal(); };
  $("[data-close]", $("#modalBox")).onclick = closeModal;
}

/* ──────────────────────────────── DATABASE ────────────────────────── */
async function viewDatabase() {
  const root = $("#view");
  const data = await api("/api/admin/database");
  const fmtBytes = (b) => {
    const n = Number(b);
    if (isNaN(n)) return "—";
    if (n > 1e9) return `${(n / 1e9).toFixed(2)} GB`;
    if (n > 1e6) return `${(n / 1e6).toFixed(1)} MB`;
    if (n > 1e3) return `${(n / 1e3).toFixed(1)} KB`;
    return `${n} B`;
  };
  root.innerHTML = `
    ${pageHead("Database", "Live MongoDB statistics for the LeadAI database.")}
    ${data.db_stats.error ? `<div class="adm-note">dbStats unavailable: ${esc(data.db_stats.error)}</div>` : `
    <div class="adm-stats">
      <div class="adm-stat"><div class="adm-stat-label">Database</div><div class="adm-stat-value">${esc(data.db_stats.db || "—")}</div></div>
      <div class="adm-stat"><div class="adm-stat-label">Documents</div><div class="adm-stat-value">${Number(data.db_stats.objects || 0).toLocaleString()}</div></div>
      <div class="adm-stat"><div class="adm-stat-label">Data size</div><div class="adm-stat-value">${fmtBytes(data.db_stats.dataSize)}</div></div>
      <div class="adm-stat"><div class="adm-stat-label">Storage size</div><div class="adm-stat-value">${fmtBytes(data.db_stats.storageSize)}</div></div>
      <div class="adm-stat"><div class="adm-stat-label">Indexes</div><div class="adm-stat-value">${Number(data.db_stats.indexes || 0)}</div></div>
    </div>`}
    <div class="adm-card">
      <div class="adm-card-title">Collections</div>
      <div class="adm-table-wrap"><table class="adm-table">
        <thead><tr><th>Collection</th><th>Documents</th><th>Indexes</th></tr></thead>
        <tbody>
          ${data.collections.map((c) => `
            <tr>
              <td><span class="adm-code">${esc(c.name)}</span></td>
              <td>${c.documents.toLocaleString()}</td>
              <td>${c.indexes.length ? c.indexes.map((i) => `
                <span class="adm-badge" title="${esc(i.name)} (${i.keys.join(", ")})">${esc(i.name)}${i.unique ? " · unique" : ""}</span>`).join(" ") : "—"}</td>
            </tr>`).join("")}
        </tbody>
      </table></div>
    </div>`;
  $("#view").innerHTML = root.innerHTML;
}

/* ──────────────────────────────── LOGS ────────────────────────────── */
const LOG_FILTERS = { level: "", q: "" };

async function viewLogs() {
  const root = $("#view");
  const f = LOG_FILTERS;
  const hashParams = new URLSearchParams(location.hash.split("?")[1] || "");
  if (hashParams.has("q")) f.q = hashParams.get("q") || "";
  const qs = new URLSearchParams({ level: f.level, q: f.q, lines: 2000, offset: 0 });
  const data = await api(`/api/admin/logs?${qs}`);
  const role = state.user.role;
  root.innerHTML = `
    ${pageHead("Logs", "Tail of the server log (logs/app.log), DEBUG level and up.", `
      ${role !== "viewer" ? `<a class="adm-btn" href="/api/admin/logs/download">⇩ Download full log</a>` : ""}`)}
    <div class="adm-card">
      <div class="adm-filters">
        <select class="adm-select" id="logLevel">
          <option value="">All levels</option>
          ${["DEBUG", "INFO", "WARNING", "ERROR"].map((l) => `<option value="${l}" ${f.level === l ? "selected" : ""}>${l}</option>`).join("")}
        </select>
        <input class="adm-input" id="logQ" placeholder="Filter text…" value="${esc(f.q)}">
        <button class="adm-btn primary" id="applyLogs">Filter</button>
      </div>
      <div class="adm-note">${data.total.toLocaleString()} matching line(s)</div>
      <div class="adm-table-wrap"><pre class="adm-log" id="logPre" style="
        margin:0; padding:14px; font-family:'JetBrains Mono',ui-monospace,monospace; font-size:12px;
        line-height:1.55; color:var(--text-2); max-height:560px; overflow:auto; white-space:pre-wrap; word-break:break-word;
      ">${data.lines.map((ln) => esc(ln)).join("") || "— no log lines match —"}</pre></div>
    </div>`;
  const apply = () => {
    f.level = $("#logLevel").value;
    f.q = $("#logQ").value.trim();
    viewLogs();
  };
  $("#applyLogs").onclick = apply;
  $("#logLevel").onchange = apply;
  const pre = $("#logPre");
  if (pre) {
    // Make run ids inside log lines clickable links to the job report.
    pre.innerHTML = data.lines.map((ln) => {
      let out = esc(ln);
      out = out.replace(/\b((?:URL|FB|IG|LI|YT)\d{10,})\b/g,
        (m) => `<a href="#/jobs/details:${m}" class="adm-link">${m}</a>`);
      return out;
    }).join("") || "— no log lines match —";
  }
}

/* ──────────────────────────────── EXPORTS ─────────────────────────── */
async function viewExports() {
  const root = $("#view");
  const role = state.user.role;
  const csvUrl = (scope, extra = "") => `/api/admin/export/${scope}.csv?${extra}`;
  const history = await api("/api/admin/audit-logs?category=exports&limit=10");
  const rows = history.items || [];
  root.innerHTML = `
    ${pageHead("Exports", role === "viewer" ? "CSV exports require the manager role." : "Download filtered CSV exports generated live from the real database. Every download is recorded in the audit log.")}
    <div class="adm-grid-2">
      <div class="adm-card">
        <div class="adm-card-title">Pages</div>
        <p style="color:var(--text-2);font-size:13px;margin:0 0 12px">All stored pages across every run.</p>
        <div class="adm-btn-row">
          <a class="adm-btn primary" href="${csvUrl("pages")}" ${role === "viewer" ? "onclick='return false'" : ""}>⇩ Export Pages CSV</a>
        </div>
      </div>
      <div class="adm-card">
        <div class="adm-card-title">Posts</div>
        <p style="color:var(--text-2);font-size:13px;margin:0 0 12px">All stored posts across every run.</p>
        <div class="adm-btn-row">
          <a class="adm-btn primary" href="${csvUrl("posts")}" ${role === "viewer" ? "onclick='return false'" : ""}>⇩ Export Posts CSV</a>
        </div>
      </div>
      <div class="adm-card">
        <div class="adm-card-title">Jobs</div>
        <p style="color:var(--text-2);font-size:13px;margin:0 0 12px">All search runs with their status, phase and page counts.</p>
        <div class="adm-btn-row">
          <a class="adm-btn primary" href="${csvUrl("jobs")}" ${role === "viewer" ? "onclick='return false'" : ""}>⇩ Export Jobs CSV</a>
        </div>
      </div>
      <div class="adm-card">
        <div class="adm-card-title">Leads</div>
        <div class="adm-field" style="margin-bottom:10px">
          <label>Platform filter (optional)</label>
          <input class="adm-input" id="leadPlat" placeholder="facebook / instagram / linkedin / youtube">
        </div>
        <div class="adm-btn-row">
          <a class="adm-btn primary" id="leadCsvLink" href="${csvUrl("leads")}" ${role === "viewer" ? "onclick='return false'" : ""}>⇩ Export Leads CSV</a>
        </div>
      </div>
    </div>
    <div class="adm-card">
      <div class="adm-card-title">Recent exports <span class="adm-hint">(from the audit log — every download is recorded)</span></div>
      ${rows.length ? `
        <div class="adm-table-wrap"><table class="adm-table">
          <thead><tr><th>When</th><th>User</th><th>Scope</th><th>Format</th><th>Rows</th></tr></thead>
          <tbody>
            ${rows.map((e) => `
              <tr>
                <td>${fmtTime(new Date(e.at * 1000))}</td>
                <td>${esc(e.user || "—")}</td>
                <td><span class="adm-code">${esc((e.details || {}).scope || "—")}</span></td>
                <td><span class="adm-badge cyan">${esc((e.details || {}).format || "csv")}</span></td>
                <td>${(e.details || {}).rows ?? "—"}</td>
              </tr>`).join("")}
          </tbody>
        </table></div>`
      : emptyState("⇩", "No exports yet — the first download appears here.")}
    </div>`;
  const leadLink = $("#leadCsvLink");
  if (leadLink) {
    $("#leadPlat").onchange = () => {
      const p = $("#leadPlat").value.trim();
      leadLink.href = csvUrl("leads", p ? `platform=${encodeURIComponent(p)}` : "");
    };
  }
}

/* ──────────────────────────────── USERS ───────────────────────────── */
async function viewUsers() {
  const root = $("#view");
  const data = await api("/api/admin/users");
  root.innerHTML = `
    ${pageHead("Users", "Admin accounts and roles. Viewer = read-only, Manager = operations, Super Admin = everything.", `
      <button class="adm-btn primary" id="addUserBtn">+ Add User</button>`)}
    <div class="adm-card">
      <div class="adm-table-wrap"><table class="adm-table">
        <thead><tr><th>Email</th><th>Name</th><th>Role</th><th>Enabled</th><th>Last login</th><th></th></tr></thead>
        <tbody>
          ${data.users.map((u) => `
            <tr>
              <td><div class="adm-cell-main">${esc(u.email)} ${u.env_account ? `<span class="adm-badge amber">env</span>` : ""}</div></td>
              <td>${esc(u.name || "—")}</td>
              <td><span class="adm-badge ${u.role === "super_admin" ? "violet" : u.role === "manager" ? "blue" : ""}">${esc(u.role)}</span></td>
              <td>${u.enabled ? `<span class="adm-badge green">enabled</span>` : `<span class="adm-badge red">disabled</span>`}</td>
              <td>${u.last_login ? relativeTime(u.last_login) : "—"}</td>
              <td>
                <div class="adm-btn-row">
                  <button class="adm-btn small" data-edit data-id="${esc(u._id || "")}" data-email="${esc(u.email)}" data-name="${esc(u.name || "")}" data-role="${esc(u.role)}" data-enabled="${u.enabled ? 1 : 0}" ${u.env_account ? "disabled" : ""}>Edit</button>
                  <button class="adm-btn small danger" data-del data-id="${esc(u._id || "")}" data-email="${esc(u.email)}" ${u.env_account ? "disabled" : ""}>✕</button>
                </div>
              </td>
            </tr>`).join("")}
        </tbody>
      </table></div>
    </div>`;
  const userForm = (u, isEdit) => openModal(
    isEdit ? `Edit user — ${esc(u.email)}` : "Add admin user",
    `
      <div class="adm-form-grid">
        <div class="adm-field"><label>Email</label><input class="adm-input" id="uEmail" value="${esc(u.email)}" ${isEdit ? "disabled" : ""}></div>
        <div class="adm-field"><label>Name</label><input class="adm-input" id="uName" value="${esc(u.name || "")}"></div>
        <div class="adm-field"><label>Role</label>
          <select class="adm-select" id="uRole">
            ${["viewer", "manager", "super_admin"].map((r) => `<option value="${r}" ${u.role === r ? "selected" : ""}>${r}</option>`).join("")}
          </select></div>
        <div class="adm-field"><label>${isEdit ? "New password (leave empty to keep)" : "Password"}</label>
          <input class="adm-input" type="password" id="uPass" placeholder="min 8 characters"></div>
      </div>`,
    `<button class="adm-btn" data-close>Cancel</button>
     <button class="adm-btn primary" id="uSave">${isEdit ? "Save" : "Create"}</button>`);
  const doSave = async (isEdit) => {
    const email = $("#uEmail").value.trim();
    const name = $("#uName").value.trim();
    const role = $("#uRole").value;
    const password = $("#uPass").value;
    const body = { name, role };
    if (!isEdit) {
      body.email = email;
      body.password = password;
    } else if (password) {
      body.password = password;
    }
    try {
      const id = $("#uSave").dataset.id;
      if (isEdit) {
        await api(`/api/admin/users/${id}`, { method: "PATCH", body });
      } else {
        await api("/api/admin/users", { method: "POST", body });
      }
      closeModal();
      toast(isEdit ? "User updated" : "User created", "ok");
      viewUsers();
    } catch (err) { toast(err.message, "error"); }
  };
  $("#addUserBtn").onclick = () => {
    userForm({ email: "", name: "", role: "viewer" }, false);
    $("#uSave").onclick = () => doSave(false);
    $("#modalBackdrop").onclick = (e) => { if (e.target.id === "modalBackdrop") closeModal(); };
    $("[data-close]", $("#modalBox")).onclick = closeModal;
  };
  $$("[data-edit]", root).forEach((btn) => {
    btn.onclick = () => {
      userForm({
        email: btn.dataset.email, name: btn.dataset.name,
        role: btn.dataset.role, enabled: btn.dataset.enabled === "1",
      }, true);
      $("#uSave").dataset.id = btn.dataset.id;
      $("#uSave").onclick = () => doSave(true);
      $("#modalBackdrop").onclick = (e) => { if (e.target.id === "modalBackdrop") closeModal(); };
      $("[data-close]", $("#modalBox")).onclick = closeModal;
    };
  });
  $$("[data-del]", root).forEach((btn) => {
    btn.onclick = () => confirmModal(
      "Delete user", `Remove <b>${esc(btn.dataset.email)}</b>? Their sessions stop working immediately.`,
      async () => {
        await api(`/api/admin/users/${btn.dataset.id}`, { method: "DELETE" });
        toast("User deleted", "ok");
        viewUsers();
      }, "Delete User");
  });
}

/* ──────────────────────────────── SECURITY ────────────────────────── */
async function viewSecurity() {
  const root = $("#view");
  const data = await api("/api/admin/security");
  const set = data.settings;
  const timeoutHours = set["security.session_timeout_hours"];
  root.innerHTML = `
    ${pageHead("Security", "Session policy, brute-force protection and password management.", `
      <span class="adm-badge green">signed in as ${esc(data.me.email)} (${esc(data.me.role)})</span>`)}
    <div class="adm-grid-2">
      <div class="adm-card">
        <div class="adm-card-title">Session policy</div>
        <div class="adm-form-grid">
          <div class="adm-field">
            <label>Idle timeout (hours)</label>
            <input class="adm-input" type="number" id="secTimeout" value="${esc(timeoutHours ?? "")}" min="1">
            <span class="adm-hint">Env default: ${data.session_timeout_hours_env}h (cookie lifetime). Applies to admin-panel sessions.</span>
          </div>
          <div class="adm-field">
            <label>Brute-force login protection</label>
            <div class="adm-flex">
              <label class="adm-toggle">
                <input type="checkbox" id="secLoginProtect" ${set["security.login_protection"] ? "checked" : ""}>
                <span class="adm-toggle-slider"></span>
              </label>
              <span class="adm-hint">${data.login_protection_active ? "active (5 fails / 60s per IP)" : "disabled"}</span>
            </div>
          </div>
          <div class="adm-field">
            <label>Audit logging</label>
            <div class="adm-flex">
              <label class="adm-toggle">
                <input type="checkbox" id="secAudit" ${set["security.audit_logging"] ? "checked" : ""}>
                <span class="adm-toggle-slider"></span>
              </label>
              <span class="adm-hint">Records every admin action to the audit log.</span>
            </div>
          </div>
        </div>
        <div class="adm-btn-row" style="margin-top:16px">
          <button class="adm-btn primary" id="saveSec">Save</button>
          <button class="adm-btn danger" id="revokeAll">Revoke all sessions</button>
        </div>
      </div>
      <div class="adm-card">
        <div class="adm-card-title">Change password</div>
        <div class="adm-field" style="margin-bottom:12px">
          <label>New password</label>
          <input class="adm-input" type="password" id="newPass" placeholder="min 8 characters">
          <span class="adm-hint">If you are the .env admin, this creates a managed override — the .env password stays valid as a recovery fallback.</span>
        </div>
        <button class="adm-btn primary" id="changePass">Update Password</button>
      </div>
    </div>`;
  $("#saveSec").onclick = async () => {
    const body = {
      "security.session_timeout_hours": Number($("#secTimeout").value) || 0,
      "security.login_protection": $("#secLoginProtect").checked,
      "security.audit_logging": $("#secAudit").checked,
    };
    try {
      await api("/api/admin/security", { method: "PUT", body });
      toast("Security settings saved", "ok");
      viewSecurity();
    } catch (err) { toast(err.message, "error"); }
  };
  $("#revokeAll").onclick = () => confirmModal(
    "Revoke all sessions", "Every signed-in session (including your other browsers) is invalidated immediately. You stay signed in.",
    async () => {
      await api("/api/admin/security/revoke-sessions", { method: "POST" });
      toast("All sessions revoked", "ok");
      viewSecurity();
    }, "Revoke All");
  $("#changePass").onclick = async () => {
    const password = $("#newPass").value;
    try {
      await api("/api/admin/security/change-password", { method: "POST", body: { password } });
      toast("Password updated — sign in again", "ok");
      setTimeout(() => { location.href = "/login"; }, 1200);
    } catch (err) { toast(err.message, "error"); }
  };
}

/* ──────────────────────────────── FEATURES ────────────────────────── */
const FEATURE_LABELS = {
  "features.url_search.enabled": "URL search (user app)",
  "features.exports.enabled": "CSV exports",
  "platform.facebook.enabled": "Facebook",
  "platform.instagram.enabled": "Instagram",
  "platform.linkedin.enabled": "LinkedIn",
  "platform.youtube.enabled": "YouTube",
};

async function viewFeatures() {
  const root = $("#view");
  const data = await api("/api/admin/features");
  root.innerHTML = `
    ${pageHead("Features", "Global feature toggles. Disabling URL search blocks new searches in the user app; disabling exports blocks every CSV download.")}
    <div class="adm-card">
      ${settingsForm(Object.keys(FEATURE_LABELS), FEATURE_LABELS, data.settings)}
    </div>`;
  bindSettingsSave(root, "/api/admin/features", viewFeatures);
}

/* ──────────────────────────────── MAINTENANCE ─────────────────────── */
async function viewMaintenance() {
  const root = $("#view");
  const [status, maint] = await Promise.all([
    api("/api/admin/dashboard"),
    api("/api/admin/maintenance"),
  ]);
  const enabled = status.status.maintenance;
  root.innerHTML = `
    ${pageHead("Maintenance", "Maintenance mode blocks the user app with a 503 while the admin panel keeps working. Signed-in admins always pass.")}
    <div class="adm-card">
      <div class="adm-flex">
        <label class="adm-toggle">
          <input type="checkbox" id="maintEnabled" ${enabled ? "checked" : ""}>
          <span class="adm-toggle-slider"></span>
        </label>
        <span class="adm-badge ${enabled ? "amber" : ""}">${enabled ? "Maintenance ACTIVE — user app blocked" : "Maintenance off — user app live"}</span>
      </div>
      <div class="adm-field" style="margin-top:16px">
        <label>Maintenance message</label>
        <textarea class="adm-textarea" id="maintMsg">${esc(maint.message || "")}</textarea>
        <span class="adm-hint">Shown to users when maintenance mode is on.</span>
      </div>
      <div class="adm-btn-row" style="margin-top:14px">
        <button class="adm-btn primary" id="maintSave">${enabled ? "Update" : "Enable Maintenance Mode"}</button>
      </div>
    </div>`;
  $("#maintSave").onclick = async () => {
    try {
      await api("/api/admin/maintenance", {
        method: "POST",
        body: { enabled: $("#maintEnabled").checked, message: $("#maintMsg").value },
      });
      toast($("#maintEnabled").checked ? "Maintenance mode enabled" : "Maintenance mode disabled", "ok");
      viewMaintenance();
    } catch (err) { toast(err.message, "error"); }
  };
}

/* ──────────────────────────────── HEALTH ──────────────────────────── */
async function viewHealth() {
  const root = $("#view");
  const data = await api("/api/admin/health");
  const c = data.checks;
  root.innerHTML = `
    ${pageHead("Health", "Live component checks.", `
      <span class="adm-badge ${data.overall === "ok" ? "green" : "red"}">${data.overall}</span>
      <button class="adm-btn" id="recheck">↻ Re-check</button>`)}
    ${data.checked_at ? `<div class="adm-note">Last checked ${fmtTime(new Date(data.checked_at * 1000))}</div>` : ""}
    <div class="adm-grid-2">
      <div class="adm-card">
        <div class="adm-card-title">MongoDB</div>
        <div class="adm-kv">
          <dt>Status</dt><dd>${c.mongo.ok ? `<span class="adm-badge green">connected</span>` : `<span class="adm-badge red">${esc(c.mongo.error || "down")}</span>`}</dd>
          <dt>Ping latency</dt><dd>${c.mongo.latency_ms !== undefined && c.mongo.latency_ms !== null ? `${c.mongo.latency_ms} ms` : "—"}</dd>
        </div>
      </div>
      <div class="adm-card">
        <div class="adm-card-title">Apify</div>
        <div class="adm-kv">
          <dt>Token</dt><dd>${c.apify.ok ? `<span class="adm-badge green">${esc(c.apify.token_hint)}</span>` : `<span class="adm-badge red">not configured</span>`}</dd>
          <dt>Last test</dt><dd>${c.apify.last_test_ok === null ? "never run" : c.apify.last_test_ok ? "✓ ok" : "✕ failed"}</dd>
        </div>
      </div>
      <div class="adm-card">
        <div class="adm-card-title">Gemini</div>
        <div class="adm-kv">
          <dt>API key</dt><dd>${c.gemini.ok ? `<span class="adm-badge green">configured</span>` : `<span class="adm-badge amber">not set — rule fallback active</span>`}</dd>
        </div>
      </div>
      <div class="adm-card">
        <div class="adm-card-title">Log file</div>
        <div class="adm-kv">
          <dt>Present</dt><dd>${c.logs.ok ? `<span class="adm-badge green">${esc(c.logs.path)}</span>` : `<span class="adm-badge amber">missing</span>`}</dd>
          <dt>Maintenance</dt><dd>${c.maintenance.enabled ? `<span class="adm-badge amber">active</span>` : `<span class="adm-badge">off</span>`}</dd>
        </div>
      </div>
    </div>`;
  $("#recheck").onclick = viewHealth;
}

/* ──────────────────────────────── AUDIT LOG ───────────────────────── */
const AUDIT_FILTERS = { category: "", q: "" };

async function viewAudit() {
  const root = $("#view");
  const f = AUDIT_FILTERS;
  const qs = new URLSearchParams({
    category: f.category, q: f.q,
    offset: state.filters.auditOffset || 0, limit: 50,
  });
  const data = await api(`/api/admin/audit-logs?${qs}`);
  root.innerHTML = `
    ${pageHead("Audit Log", "Every admin action, in chronological order. Secrets are never logged.")}
    <div class="adm-card">
      <div class="adm-filters">
        <select class="adm-select" id="auditCat">
          <option value="">All categories</option>
          ${data.categories.map((c) => `<option value="${esc(c)}" ${f.category === c ? "selected" : ""}>${esc(c)}</option>`).join("")}
        </select>
        <input class="adm-input" id="auditQ" placeholder="Action / user…" value="${esc(f.q)}">
        <button class="adm-btn primary" id="applyAudit">Filter</button>
      </div>
      <div class="adm-table-wrap"><table class="adm-table">
        <thead><tr><th>When</th><th>Action</th><th>Category</th><th>User</th><th>Result</th><th>Details</th></tr></thead>
        <tbody>
          ${data.items.length ? data.items.map((e) => `
            <tr>
              <td>${fmtTime(new Date(e.at * 1000))}</td>
              <td><span class="adm-code">${esc(e.action)}</span></td>
              <td><span class="adm-badge violet">${esc(e.category)}</span></td>
              <td>${esc(e.user || "—")}${e.ip ? `<div class="adm-cell-sub">${esc(e.ip)}</div>` : ""}</td>
              <td>${e.success ? `<span class="adm-badge green">ok</span>` : `<span class="adm-badge red">failed</span>`}</td>
              <td><div class="adm-cell-sub">${esc(JSON.stringify(e.details || {}).slice(0, 160))}</div></td>
            </tr>`).join("")
          : `<tr><td colspan="6">${emptyState("◈", "No audit entries.")}</td></tr>`}
        </tbody>
      </table></div>
      ${pagerHtml(data.total, data.offset, data.limit, (dir) => {
        const off = data.offset + dir * data.limit;
        state.filters.auditOffset = Math.max(0, off);
        viewAudit();
      })}
    </div>`;
  const apply = () => {
    state.filters.auditOffset = 0;
    f.category = $("#auditCat").value;
    f.q = $("#auditQ").value.trim();
    viewAudit();
  };
  $("#applyAudit").onclick = apply;
  $("#auditCat").onchange = apply;
}

/* ──────────────────────────────── Boot ────────────────────────────── */
document.addEventListener("DOMContentLoaded", boot);
