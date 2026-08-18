/* LeadAI — AI-orchestrated social lead intelligence dashboard.
   Flow: URL search (platform auto-detected) → Pages → Posts → Comments.
   All data comes from real Apify actor output — never fabricated. */

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
  if (value) localStorage.setItem("leadai_" + key, value);
  else localStorage.removeItem("leadai_" + key);
};

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function esc(text) {
  if (text === null || text === undefined) return "";
  return String(text)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// a "running" status that started more than 30 minutes ago is stale
// (server restart / crash) — the agent allows retry in that case
function isStale(startedAt) {
  if (!startedAt) return false;
  const t = new Date(startedAt.replace(" ", "T") + (startedAt.includes("Z") ? "" : "Z")).getTime();
  if (isNaN(t)) return false;
  return Date.now() - t > 30 * 60 * 1000;
}

function fmt(n) {
  if (n === null || n === undefined) return "—";
  const num = Number(n);
  if (isNaN(num)) return "—";
  if (num >= 10000000) return (num / 10000000).toFixed(1).replace(/\.0$/, "") + " Cr";
  if (num >= 100000) return (num / 100000).toFixed(1).replace(/\.0$/, "") + " L";
  if (num >= 1000) return (num / 1000).toFixed(1).replace(/\.0$/, "") + "K";
  return String(num);
}

function toast(msg, type = "info") {
  const box = $("toastContainer");
  const el = document.createElement("div");
  el.className = "toast toast-" + type;
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => el.classList.add("show"), 10);
  setTimeout(() => { el.classList.remove("show"); setTimeout(() => el.remove(), 400); }, 4200);
}

// ── Status badge (top-right) ─────────────────────────────────────────────
function setBadge(text, cls) {
  const badge = $("pipelineStatusBadge");
  badge.textContent = "⬤ " + text;
  badge.className = "status-badge " + (cls || "status-idle");
}

// ── Navigation ───────────────────────────────────────────────────────────
const VIEWS = ["search", "pages", "posts", "comments"];

function viewAllowed(view) {
  if (view === "search") return true;
  if (view === "pages") return Boolean(memory.runId);
  if (view === "posts") return Boolean(memory.pageId);
  if (view === "comments") return Boolean(memory.postId);
  return false;
}

function navigateToView(view) {
  if (!viewAllowed(view)) return;
  VIEWS.forEach((v) => $("view-" + v).classList.toggle("hidden", v !== view));
  document.querySelectorAll(".breadcrumb-item").forEach((btn) => {
    const active = btn.dataset.view === view;
    btn.classList.toggle("active", active);
    btn.disabled = !viewAllowed(btn.dataset.view);
  });
  if (view === "pages") renderPagesScreen();
  if (view === "posts") renderPostsScreen();
  if (view === "comments") renderCommentsScreen();
}

// ── URL SEARCH — paste a social media link, platform auto-detected ──────
const URL_LABELS = {
  facebook: ["Facebook", "🏠"], instagram: ["Instagram", "📸"],
  youtube: ["YouTube", "▶️"], linkedin: ["LinkedIn", "💼"],
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

// replaced with the real handler by handleUrlSearch while a run is active —
// exists so the inline onclick never throws
function cancelCurrentSearch() {}

// real progress percentages mapped from the backend run phases
const URL_PHASE_PCT = {
  url_invalid: 5, page: 20, posts: 55, comments: 75,
  completed: 100, error: 100, cancelled: 100,
};

// steps in the checklist once a phase is reached (URL validated, platform
// detected, page details, posts, comments)
const STEP_DONE = { page: 2, posts: 3, comments: 4 };
let lastActiveStep = 0;

function setAnalysisSteps(phase, status) {
  const list = $("urlSearchSteps");
  if (!list) return;
  const items = Array.from(list.querySelectorAll("li[data-step]"));
  let done = 0;
  if (status === "completed") {
    done = items.length;
  } else if (phase === "url_invalid") {
    done = 0;
  } else if (STEP_DONE[phase] != null) {
    done = STEP_DONE[phase];
    lastActiveStep = Math.min(done, items.length - 1);
  }
  items.forEach((li, i) => {
    li.classList.remove("done", "active", "error");
    if (status === "error") {
      if (i === lastActiveStep) li.classList.add("error");
    } else if (i < done) {
      li.classList.add("done");
    } else if (i === done && status === "running") {
      li.classList.add("active");
    }
  });
}

async function pollSearchRun(runId, onCancelRequested) {
  let cancelled = false;
  if (onCancelRequested) onCancelRequested(() => { cancelled = true; });
  while (true) {
    const res = await fetch(`/api/search/${runId}`);
    const data = await res.json();
    const run = data.search;
    const fill = $("urlSearchProgressFill");
    const bar = $("urlSearchProgressBar");
    const label = $("urlSearchProgressLabel");
    label.textContent = run.message || run.error || run.status;

    const pct = URL_PHASE_PCT[run.phase];
    if (pct !== undefined) {
      fill.style.width = pct + "%";
      if (bar) bar.setAttribute("aria-valuenow", String(pct));
    }
    setAnalysisSteps(run.phase, run.status);

    if (run.status !== "running") {
      if (run.status === "error") toast(run.error || "Search failed", "error");
      return run;
    }
    if (cancelled) {
      // the user clicked Cancel — the backend stops the Apify run; stop
      // polling once it flips to a terminal status
      while (true) {
        const c = await fetch(`/api/search/${runId}`);
        const cd = await c.json();
        setAnalysisSteps(cd.search.phase, cd.search.status);
        if (cd.search.status !== "running") return cd.search;
        await sleep(1500);
      }
    }
    await sleep(1500);
  }
}

let recentData = [];
let recentFilter = "all";
let recentLimit = 8;

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

const RECENT_STATUS = {
  completed: ["ok", "✓", "Completed"],
  running: ["running", "◌", "Processing"],
  error: ["err", "!", "Failed"],
  cancelled: ["warn", "–", "Cancelled"],
};

async function fetchRecentSearches() {
  const res = await fetch(`/api/search/history?limit=${recentLimit}`);
  const data = await res.json();
  recentData = data.searches || [];
}

function renderRecentRows() {
  const list = $("recentSearchesList");
  const empty = $("recentSearchesEmpty");
  const rows = recentData.filter((s) => recentFilter === "all" || s.platform === recentFilter);
  if (!rows.length) {
    list.innerHTML = "";
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");
  list.innerHTML = rows.map((s) => {
    const [label, icon] = URL_LABELS[s.platform] || [s.platform || "URL", "🔗"];
    const [cls, mark, stLabel] = RECENT_STATUS[s.status] || ["warn", "–", s.status || "—"];
    const count = s.pages_stored || 0;
    return `
      <div class="rs-row" role="button" tabindex="0" onclick="reopenSearch('${s.run_id}')" onkeydown="if(event.key==='Enter')reopenSearch('${s.run_id}')" aria-label="Open search for ${esc(s.query)}">
        <span class="rs-icon" aria-hidden="true">${icon}</span>
        <span class="rs-main">
          <span class="rs-title">${esc(s.query)}</span>
          <span class="rs-sub">${esc(label)} · ${count} page${count === 1 ? "" : "s"}</span>
        </span>
        <span class="rs-right">
          <span class="rs-badge ${cls}">${mark} ${stLabel}</span>
          <span class="rs-time">${relativeTime(s.created_at)}</span>
          <button class="rs-del" type="button" title="Delete this search and its data" onclick="deleteSearch('${s.run_id}', event)">✕</button>
          <span class="rs-open">Open →</span>
        </span>
      </div>`;
  }).join("");
}

async function deleteSearch(runId, ev) {
  ev.stopPropagation();
  if (!confirm("Delete this search and all its pages, posts and leads? This cannot be undone.")) return;
  try {
    const res = await fetch(`/api/search/${encodeURIComponent(runId)}`, { method: "DELETE" });
    if (!res.ok) throw new Error((await res.json()).detail || "Delete failed");
    toast("Search deleted", "success");
    await fetchRecentSearches();
    renderRecentRows();
    renderSessionStats();
  } catch (err) {
    toast("Could not delete search: " + err.message, "error");
  }
}

function renderSessionStats() {
  const strip = $("sessionStats");
  if (!strip) return;
  if (!recentData.length) { strip.classList.add("hidden"); return; }
  const completed = recentData.filter((s) => s.status === "completed").length;
  const posts = recentData.reduce((sum, s) => sum + (s.pages_stored || 0), 0);
  $("statSearches").textContent = recentData.length;
  $("statCompleted").textContent = completed;
  $("statPosts").textContent = posts;
  strip.classList.remove("hidden");
}

function setRecentFilter(btn) {
  document.querySelectorAll("#recentFilters .filter-chip").forEach((c) => c.classList.toggle("active", c === btn));
  recentFilter = btn.dataset.f;
  renderRecentRows();
}

async function recentViewAll() {
  recentLimit = recentLimit === 8 ? 100 : 8;
  const viewAllBtn = $("recentViewAll");
  if (viewAllBtn) viewAllBtn.textContent = recentLimit === 100 ? "Show less" : "View all →";
  try {
    await fetchRecentSearches();
  } catch (err) { /* offline-safe */ }
  renderRecentRows();
  renderSessionStats();
}

async function renderRecentSearches() {
  try {
    await fetchRecentSearches();
  } catch (err) {
    recentData = [];
  }
  renderRecentRows();
  renderSessionStats();
}

async function reopenSearch(runId) {
  try {
    const res = await fetch(`/api/search/${runId}`);
    const data = await res.json();
    saveMemory("runId", runId);
    toast("Opened search: " + data.search.query, "info");
    navigateToView("pages");  // renderPagesScreen auto-fires collection if needed
  } catch (err) {
    toast("Could not open that search", "error");
  }
}

async function handleUrlSearch(event) {
  event.preventDefault();
  const url = $("urlSearchInput").value.trim();
  const maxPosts = parseInt($("urlSearchLimit").value, 10) || 20;
  const maxCommentsPerPost = parseInt($("urlSearchCommentsPerPost").value, 10) || 30;
  if (!url) return;

  const btn = $("urlSearchBtn");
  const btnText = btn.querySelector(".btn-text");
  const cancelBtn = $("urlSearchCancelBtn");
  btn.disabled = true;
  btn.classList.add("is-loading");
  if (btnText) btnText.textContent = "Analyzing Profile…";
  const prog = $("urlSearchProgress");
  prog.classList.remove("hidden");
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

  try {
    const res = await fetch(`/api/url/search?${params.toString()}`, { method: "POST" });
    if (!res.ok) {
      const err = await res.json();
      throw new Error((err.detail && (err.detail.message || err.detail)) || "URL search failed");
    }
    const data = await res.json();
    const [label, icon] = URL_LABELS[data.platform] || [data.platform, "🔗"];
    const chip = $("urlPlatformChip");
    chip.innerHTML = `<span class="intent-chip"><b>Platform:</b> ${icon} ${label}</span><span class="intent-chip"><b>Canonical:</b> ${esc(data.canonical_url)}</span><span class="intent-chip"><b>Status:</b> running…</span>`;
    chip.classList.remove("hidden");
    saveMemory("runId", data.run_id);
    cancelBtn.classList.remove("hidden");

    const run = await pollSearchRun(data.run_id, (setCancelFlag) => {
      cancelBtn.onclick = () => {
        setCancelFlag(true);
        cancelBtn.disabled = true;
        cancelBtn.textContent = "Cancelling…";
        fetch(`/api/search/${data.run_id}/cancel`, { method: "POST" }).catch(() => {});
        toast("Cancelling search…", "info");
      };
    });
    cancelBtn.classList.add("hidden");
    chip.classList.add("hidden");

    if (run.status === "cancelled") {
      setBadge("Cancelled", "status-warn");
      $("urlSearchProgressLabel").textContent = "Search cancelled";
      setTimeout(() => prog.classList.add("hidden"), 2500);
      renderRecentSearches();
      return;
    }

    setBadge(run.status === "completed" ? "Completed" : "Error",
             run.status === "completed" ? "status-success" : "status-error");
    $("urlSearchProgressFill").style.width = "100%";
    const labelEl = $("urlSearchProgressLabel");
    labelEl.textContent = run.message || run.error || "Completed";
    setTimeout(() => prog.classList.add("hidden"), 2500);

    if (run.status === "error") {
      toast(run.error || "URL search failed — is the URL correct?", "error");
    } else {
      toast("URL search done — page is ready", "success");
      navigateToView("pages");
      window.open(`/static/url_report.html?run_id=${encodeURIComponent(data.run_id)}`, "_blank");
    }
  } catch (err) {
    toast(err.message || "URL search failed", "error");
    setBadge("Idle", "status-idle");
    $("urlPlatformChip").classList.add("hidden");
  } finally {
    btn.disabled = false;
    btn.classList.remove("is-loading");
    if (btnText) btnText.textContent = "Analyze Profile";
    cancelBtn.classList.add("hidden");
    renderRecentSearches();
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
  try {
    const res = await fetch("/api/comment-filters/catalog");
    if (!res.ok) throw new Error("catalog failed");
    const cat = await res.json();
    const opts = (cat.presets || []).map((p) =>
      `<option value="${esc(p.key)}">Preset · ${esc(p.name)}</option>`);
    try {
      const r2 = await fetch("/api/comment-filters/rules");
      if (r2.ok) {
        const rules = (await r2.json()).rules || [];
        for (const r of rules) {
          opts.push(`<option value="${esc(r.id)}">${r.active ? "★ Active rule · " : "Rule · "}${esc(r.name)}</option>`);
        }
      }
    } catch (err) { /* non-admin users only get presets */ }
    if (presetSel) {
      presetSel.innerHTML = `<option value="">Default (admin rule or all)</option>` + opts.join("");
    }
    const cats = [...(cat.categories || []), ...(cat.custom_categories || [])];
    if (cats.length && catsWrap && catsBox) {
      catsWrap.classList.remove("hidden");
      catsBox.innerHTML = cats.map((c) =>
        `<label class="filter-cat"><input type="checkbox" value="${esc(c.key || c.id || c._id)}"><span>${esc(c.icon || "")} ${esc(c.name)}</span></label>`).join("");
    }
  } catch (err) {
    if (presetSel) presetSel.innerHTML = `<option value="">Filtering unavailable</option>`;
  }
}

// ── PAGES ────────────────────────────────────────────────────────────────
async function renderPagesScreen() {
  if (!memory.runId) { $("pagesGrid").innerHTML = ""; $("pagesListEmpty").classList.remove("hidden"); return; }
  setBadge("Viewing pages", "status-idle");
  const category = $("pagesCategoryFilter").value;
  const contact = $("pagesContactOnly").checked;
  const params = new URLSearchParams({ run_id: memory.runId, limit: "200" });
  if (category) params.set("category", category);
  if (contact) params.set("contact", "true");

  try {
    const res = await fetch("/api/pages?" + params.toString());
    const data = await res.json();
    const tbody = $("pagesGrid");
    const empty = $("pagesListEmpty");
    const statusBox = $("pagesScreenStatus");

    if (!data.pages.length) {
      tbody.innerHTML = "";
      empty.classList.remove("hidden");
      statusBox.classList.add("hidden");
      return;
    }
    empty.classList.add("hidden");

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
          : " · auto-analyzing posts now…")
      : "";
    const qualified = data.pages.filter((p) => p.has_qualifying_posts).length;
    statusBox.innerHTML = `<div class="summary-line">Found <b>${data.pages.length}</b> page(s)/channel(s) · <b>${qualified} with qualifying high-intent posts</b>${collectNote}</div>`;
    statusBox.classList.remove("hidden");

    tbody.innerHTML = data.pages.map((p) => {
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
        actionBtn = `<button class="btn-primary" onclick="openPage('${p.id}', event)">📝 View ${found} Posts${qualifying > 0 ? ` (${qualifying} qualifying)` : ""}</button>`;
      } else if (postStatus === "empty" || found === 0) {
        actionBtn = `<button class="btn-secondary" onclick="openPage('${p.id}', event)">↻ Re-analyze Posts</button>`;
      } else {
        actionBtn = `<button class="btn-primary" onclick="openPage('${p.id}', event)">🔍 Analyze Posts</button>`;
      }

      const avatar = p.profile_picture
        ? `<img class="pc-avatar" src="${esc(p.profile_picture)}" loading="lazy" onerror="this.style.display='none'">`
        : `<span class="pc-avatar pc-avatar-fallback">${esc((p.page_name || "?").charAt(0).toUpperCase())}</span>`;

      const pageInfo = platformInfo(p.platform);
      const platformCls = p.platform ? p.platform.toLowerCase() : "unknown";

      const contactChips = [];
      if (p.phone) contactChips.push(`<a class="pc-chip" href="tel:${esc(p.phone)}" onclick="event.stopPropagation()">📞 <b>${esc(p.phone)}</b></a>`);
      if (p.email) contactChips.push(`<a class="pc-chip" href="mailto:${esc(p.email)}" onclick="event.stopPropagation()">✉️ <b>${esc(p.email)}</b></a>`);
      if (p.whatsapp) contactChips.push(`<a class="pc-chip" href="https://wa.me/${esc(p.whatsapp.replace(/[^\d]/g, ''))}" target="_blank" rel="noopener" onclick="event.stopPropagation()">💬 WhatsApp</a>`);
      if (p.website) contactChips.push(`<a class="pc-chip pc-chip-link" href="${esc(p.website)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">🌐 ${esc(p.website.replace(/^https?:\/\//, '').replace(/\/$/, ''))}</a>`);
      if (p.address || p.country) contactChips.push(`<span class="pc-chip pc-chip-addr" title="${esc(p.address || p.country)}">📍 ${esc(p.address || p.country)}</span>`);
      if (p.category) contactChips.push(`<span class="pc-chip">🏷️ ${esc(p.category)}</span>`);

      return `<article class="page-card clickable-card" onclick="openPage('${p.id}')">
        <div class="pc-head">
          ${avatar}
          <div class="pc-body">
            <div class="pc-name">
              ${esc(p.page_name || "Unnamed page")}
              <span class="platform-badge ${platformCls}">${pageInfo.icon ? pageInfo.icon + " " : ""}${esc(pageInfo.name || p.platform || "Platform")}</span>
              ${p.verified ? '<span class="verified-badge" title="Verified">✓ Verified</span>' : ""}
              ${p.source_type === "group" ? '<span class="src-badge">GROUP</span>' : ""}
            </div>
            <a class="pc-link" href="${esc(p.facebook_url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">Open on ${esc(pageInfo.name || "platform")} ↗</a>
            ${p.about ? `<div class="pc-about-snippet" title="${esc(p.about)}">${esc(p.about.slice(0, 160))}${p.about.length > 160 ? "…" : ""}</div>` : ""}
          </div>
        </div>

        <div class="pc-stats">
          <div class="pc-stat">
            <div class="pc-stat-value">${fmt(p.followers)}</div>
            <div class="pc-stat-label">Followers / Subs</div>
          </div>
          <div class="pc-stat">
            <div class="pc-stat-value">${fmt(p.likes)}</div>
            <div class="pc-stat-label">Likes</div>
          </div>
          <div class="pc-stat">
            <div class="pc-stat-value">${found}</div>
            <div class="pc-stat-label">Posts Found</div>
          </div>
          <div class="pc-stat">
            <div class="pc-stat-value">${qualifying}</div>
            <div class="pc-stat-label">Qualifying Posts</div>
          </div>
          <div class="pc-stat">
            <div class="pc-stat-value">${fmt(p.total_comments_on_qualifying_posts)}</div>
            <div class="pc-stat-label">Total Comments</div>
          </div>
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

    if (anyRunning) {
      setTimeout(renderPagesScreen, 4000);
    }
  } catch (err) {
    toast("Could not load pages: " + err.message, "error");
  }
}

async function openPage(pageId, event) {
  if (event) event.stopPropagation();
  try {
    const res = await fetch(`/api/pages/${pageId}`);
    const page = await res.json();
    const status = page.posts_status || "not_started";
    if (status === "running") {
      toast("Posts are already being collected for this page", "info");
      await waitForPosts(pageId);
      navigateToView("posts");
      return;
    }
    if (status === "completed" && (page.posts_count || 0) > 0) {
      saveMemory("pageId", pageId);
      navigateToView("posts");
      return;
    }
    toast("Analyzing real posts for " + (page.page_name || "this page") + "...", "info");
    const postRes = await fetch(`/api/pages/${pageId}/posts?max_posts=20`, { method: "POST" });
    if (!postRes.ok) throw new Error((await postRes.json()).detail || "Collection failed");
    saveMemory("pageId", pageId);
    await waitForPosts(pageId);
    navigateToView("posts");
  } catch (err) {
    toast(err.message || "Failed to collect posts", "error");
  }
}

async function waitForPosts(pageId) {
  while (true) {
    const res = await fetch(`/api/pages/${pageId}/posts`);
    const data = await res.json();
    if (data.posts_status !== "running") return data;
    setBadge("Collecting posts", "status-running");
    await sleep(1500);
  }
}

function exportPagesCsv() {
  if (!memory.runId) return;
  window.open(`/api/export/pages.csv?run_id=${encodeURIComponent(memory.runId)}`, "_blank");
}

// ── POSTS ────────────────────────────────────────────────────────────────
async function renderPostsScreen() {
  if (!memory.pageId) { $("postsGrid").innerHTML = ""; $("postsListEmpty").classList.remove("hidden"); return; }
  try {
    const res = await fetch(`/api/pages/${memory.pageId}/posts`);
    const data = await res.json();
    $("postsPageName").textContent = data.page.page_name || "this page";

    const statusBox = $("postsScreenStatus");
    const page = data.page;
    const pageInfo = platformInfo(data.platform || page.platform);
    if (page.posts_status === "running") {
      statusBox.innerHTML = `<div class="summary-line status-running-text"><span class="mini-spinner"></span> Analyzing posts... ${data.posts_count || 0} found so far</div>`;
      statusBox.classList.remove("hidden");
      setTimeout(() => renderPostsScreen(), 2000);
    } else if (page.posts_status === "empty" || (page.posts_status === "completed" && !data.posts.length)) {
      statusBox.innerHTML = `<div class="summary-line status-error-text">${esc(page.posts_error || "No posts returned for this page.")}</div>`;
      statusBox.classList.remove("hidden");
    } else {
      const qual = data.qualifyingPosts || 0;
      const min = data.minComments || 10;
      const note = qual > 0
        ? `<span class="status-success-text">${qual} qualifying post(s) (relevant & ≥ ${min} comments)</span>`
        : `<span class="muted">No posts with ≥ ${min} comments</span>`;
      statusBox.innerHTML = `<div class="summary-line">Total Posts: <b>${data.totalPosts}</b> · Relevant: <b>${data.relevantPosts}</b> · Qualifying: <b>${qual}</b> · Total Comments on Qualifying: <b>${fmt(data.commentsOnQualifying)}</b> · Latest Post: <b>${esc(data.latestPostDate || "N/A")}</b><br>${note}</div>`;
      statusBox.classList.remove("hidden");
    }

    const tbody = $("postsGrid");
    const empty = $("postsListEmpty");
    if (!data.posts.length) {
      tbody.innerHTML = "";
      empty.classList.remove("hidden");
      return;
    }
    empty.classList.add("hidden");
    let anyRunning = false;
    const min = data.minComments || 10;

    tbody.innerHTML = data.posts.map((post) => {
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
        actionBtn = `<button class="btn-primary" onclick="openPost('${post.id}', event)">💬 View ${scraped} Comments & Leads</button>`;
      } else if (qualifying) {
        actionBtn = `<button class="btn-primary" onclick="openPost('${post.id}', event)">💬 Collect Comments (${total} available)</button>`;
      } else {
        actionBtn = `<button class="btn-secondary" onclick="openPost('${post.id}', event)">💬 Collect Comments (${total} available)</button>`;
      }

      const relBadge = post.is_relevant === true
        ? `<span class="badge" style="background:var(--green-soft);color:#127a48;border:1px solid rgba(31,174,106,0.3)">✓ Relevant</span>`
        : post.is_relevant === false
          ? `<span class="badge" style="background:var(--gray-soft);color:var(--text-dim)">✗ Low relevance</span>`
          : "";

      const qualBadge = qualifying
        ? `<span class="badge badge-lead">★ Qualifying (≥ ${min} comments)</span>`
        : `<span class="badge" style="background:var(--gray-soft);color:var(--text-faint)">&lt;${min} comments</span>`;

      const thumb = post.images && post.images.length
        ? `<img class="pt-thumb" src="${esc(post.images[0])}" loading="lazy" onerror="this.style.display='none'">`
        : (post.videos && post.videos.length ? `<span class="pt-thumb pt-thumb-video">🎬</span>` : `<span class="pt-thumb pt-thumb-fallback">📝</span>`);

      const captionText = post.caption || post.description || post.text || "No post caption text available.";

      return `<article class="post-tile clickable-card" onclick="openPost('${post.id}')">
        <div class="pt-main">
          ${thumb}
          <div class="pt-body">
            <div class="pt-caption" title="${esc(captionText)}">${esc(captionText)}</div>
            <div class="pt-meta">
              <span>📅 ${esc(post.published_date || "Date unknown")}</span>
              ${relBadge}
              ${qualBadge}
              ${post.post_url ? `<a class="pc-link" href="${esc(post.post_url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">View original post ↗</a>` : ""}
            </div>
          </div>
        </div>

        <div class="pt-stats">
          <div class="pc-stat">
            <div class="pc-stat-value">${fmt(post.likes_count)}</div>
            <div class="pc-stat-label">Likes</div>
          </div>
          <div class="pc-stat">
            <div class="pc-stat-value">${fmt(total)}</div>
            <div class="pc-stat-label">Total Comments</div>
          </div>
          <div class="pc-stat">
            <div class="pc-stat-value">${scraped}</div>
            <div class="pc-stat-label">Scraped & Analyzed</div>
          </div>
          <div class="pc-stat">
            <div class="pc-stat-value">${fmt(post.shares_count)}</div>
            <div class="pc-stat-label">Shares</div>
          </div>
        </div>

        <div class="pt-foot">
          ${actionBtn}
          ${cErr ? `<div class="row-error" title="${esc(cErr)}">${esc(cErr.slice(0, 90))}</div>` : ""}
        </div>
      </article>`;
    }).join("");

    if (anyRunning || page.posts_status === "running") {
      setTimeout(renderPostsScreen, 3000);
    }
  } catch (err) {
    toast("Could not load posts: " + err.message, "error");
  }
}

async function openPost(postId, event) {
  if (event) event.stopPropagation();
  try {
    const res = await fetch(`/api/posts/${postId}`);
    const post = await res.json();
    const status = post.comments_status || "not_started";
    const scraped = post.scraped_comment_count || 0;
    if (status === "running") {
      toast("Comments are already being collected for this post", "info");
      saveMemory("postId", postId);
      await waitForComments(postId);
      navigateToView("comments");
      return;
    }
    if (status === "completed" && scraped > 0) {
      saveMemory("postId", postId);
      navigateToView("comments");
      return;
    }
    if (status === "skipped") {
      saveMemory("postId", postId);
      navigateToView("comments");
      return;
    }
    toast("Collecting real comments + AI analysis...", "info");
    const postRes = await fetch(`/api/posts/${postId}/comments?max_comments=${memory.commentsPerPost}`, { method: "POST" });
    if (!postRes.ok) throw new Error((await postRes.json()).detail || "Collection failed");
    const started = await postRes.json();
    if (started.status === "skipped") {
      toast(started.message || "Not scraped — below the comment threshold", "info");
    }
    saveMemory("postId", postId);
    await waitForComments(postId);
    navigateToView("comments");
  } catch (err) {
    toast(err.message || "Failed to collect comments", "error");
  }
}

async function waitForComments(postId) {
  while (true) {
    const res = await fetch(`/api/posts/${postId}/comments`);
    const data = await res.json();
    if (data.comments_status !== "running") return data;
    setBadge("Collecting + analyzing", "status-running");
    await sleep(1500);
  }
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
  window.open(`/api/export/posts.csv?page_id=${encodeURIComponent(memory.pageId)}`, "_blank");
}

// ── COMMENTS / LEADS ─────────────────────────────────────────────────────

let activeCommentFilter = "all";
let searchDebounceTimer = null;

function setCommentsPerPost(el) {
  const n = Math.min(500, Math.max(1, parseInt(el.value, 10) || 50));
  el.value = n;
  saveMemory("commentsPerPost", n);
  const other = el.id === "commentsPerPostInput" ? "urlSearchCommentsPerPost" : "commentsPerPostInput";
  const otherEl = $(other);
  if (otherEl) otherEl.value = n;
}

function setCommentFilterType(type, btn) {
  activeCommentFilter = type;
  const pills = document.querySelectorAll("#commentFilterPills .filter-pill");
  pills.forEach((p) => p.classList.remove("active"));
  if (btn) btn.classList.add("active");
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

async function renderCommentsScreen() {
  if (!memory.postId) { $("commentsGrid").innerHTML = ""; $("commentsListEmpty").classList.remove("hidden"); return; }
  showCommentsScrapeProgress(0, 0, true);

  const searchVal = $("commentsSearchInput") ? $("commentsSearchInput").value.trim() : "";
  const qualityVal = $("commentsQualityFilter") ? $("commentsQualityFilter").value : "";
  const sortByVal = $("commentsSortBy") ? $("commentsSortBy").value : "score";
  const limitVal = memory.commentsPerPost || 50;

  const params = new URLSearchParams({
    filter_type: activeCommentFilter,
    sort_by: sortByVal,
    limit: String(limitVal)
  });
  if (searchVal) params.set("q", searchVal);
  if (qualityVal) params.set("quality", qualityVal);

  try {
    const res = await fetch(`/api/posts/${memory.postId}/comments?${params.toString()}`);
    const data = await res.json();
    $("commentsPostName").textContent = (data.post && data.post.caption ? data.post.caption.slice(0, 90) : "this post") || "this post";

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
      statusBox.innerHTML = `<div class="summary-line status-running-text"><span class="mini-spinner"></span> Collecting and analyzing comments in real-time...</div>`;
      statusBox.classList.remove("hidden");
      setTimeout(() => renderCommentsScreen(), 2000);
      return;
    }

    const filterLabels = {
      all: "Showing all collected comments",
      leads: "Showing high-intent business leads",
      contact: "Showing comments with direct phone or email contact",
      hot: "Showing high-priority hot prospects",
      pricing: "Showing pricing, cost, & budget inquiries",
      inquiry: "Showing customer questions & inquiries"
    };
    const activeLabel = filterLabels[activeCommentFilter] || "Showing comments";
    const searchNote = searchVal ? ` matching "<b>${esc(searchVal)}</b>"` : "";
    statusBox.innerHTML = `<div class="summary-line">${activeLabel}${searchNote} · <b>${data.total}</b> of <b>${data.all_count || 0}</b> total comments displayed</div>`;
    statusBox.classList.remove("hidden");

    const tbody = $("commentsGrid");
    const empty = $("commentsListEmpty");
    if (!hasComments) {
      tbody.innerHTML = "";
      empty.classList.remove("hidden");
      empty.querySelector(".empty-sub").textContent =
        searchVal ? `No comments found matching "${searchVal}" under this filter`
        : activeCommentFilter !== "all" ? `No comments under "${activeCommentFilter}" filter — click "All Comments" to see everything`
        : "No comments on this post yet — collect comments from the Posts screen";
      return;
    }
    empty.classList.add("hidden");

    tbody.innerHTML = data.comments.map((c) => {
      const priorityClass = { high: "badge-hot", medium: "badge-warm", low: "badge-cold" }[c.priority] || "badge-cold";
      const intent = c.intent ? c.intent.replace(/_/g, " ") : "";
      const commentInfo = platformInfo(c.platform);
      const platformCls = c.platform ? c.platform.toLowerCase() : "unknown";

      const contactChips = [];
      if (c.phone) contactChips.push(`<a class="lc-chip" href="tel:${esc(c.phone)}" onclick="event.stopPropagation()">📞 <b>${esc(c.phone)}</b></a>`);
      if (c.email) contactChips.push(`<a class="lc-chip" href="mailto:${esc(c.email)}" onclick="event.stopPropagation()">✉️ <b>${esc(c.email)}</b></a>`);
      if (c.whatsapp) contactChips.push(`<a class="lc-chip" href="https://wa.me/${esc(c.whatsapp.replace(/[^\d]/g, ''))}" target="_blank" rel="noopener" onclick="event.stopPropagation()">💬 WhatsApp</a>`);
      if (c.budget) contactChips.push(`<span class="lc-chip">💰 Budget: <b>${esc(c.budget)}</b></span>`);
      if (c.requirement) contactChips.push(`<span class="lc-chip">📋 Req: <b>${esc(c.requirement)}</b></span>`);
      if (c.location) contactChips.push(`<span class="lc-chip">📍 ${esc(c.location)}</span>`);
      if (intent) contactChips.push(`<span class="lc-chip lc-chip-intent">🎯 ${esc(intent)}</span>`);
      if (c.sentiment && c.sentiment !== "neutral") contactChips.push(`<span class="lc-chip" style="opacity:0.85">💭 ${esc(c.sentiment)}</span>`);

      return `<article class="lead-card clickable-card${c.has_contact ? " lead-card-highlight" : ""}" onclick="openLeadDetail('${c.id}')">
        <div class="lc-head">
          <div class="lc-author-wrap">
            <span class="lc-avatar">${esc((c.commenter_name || "?").trim().charAt(0).toUpperCase())}</span>
            <div class="lc-author-info">
              <div class="lc-name">
                ${esc(c.commenter_name || "Commenter")}
                <span class="platform-badge ${platformCls}">${esc(commentInfo.name || c.platform || "Social")}</span>
                ${c.has_contact ? `<span class="badge badge-lead">📞 Contact Ready</span>` : ""}
              </div>
              <div class="lc-meta">
                ${c.published_date ? `<span>🕒 ${esc(formatDate(c.published_date))}</span>` : ""}
                ${c.comment_url ? `<a class="pc-link" href="${esc(c.comment_url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">View on ${esc(commentInfo.name || "platform")} ↗</a>` : ""}
              </div>
            </div>
          </div>

          <div class="lc-score-wrap">
            <div class="score-pill" title="AI Lead Score: ${c.lead_score || 0}/100">${c.lead_score || 0}</div>
            ${c.priority ? `<span class="badge ${priorityClass}">${esc(c.priority.toUpperCase())}</span>` : ""}
          </div>
        </div>

        <div class="lc-text-box">
          <p class="lc-full-text">${esc(c.comment_text || "No comment text")}</p>
          ${c.reason ? `<div class="lc-ai-reason">🤖 <b>AI Intelligence:</b> ${esc(c.reason)}</div>` : ""}
        </div>

        <div class="lc-chips">
          ${contactChips.length ? contactChips.join("") : `<span class="muted" style="font-size:0.75rem">General comment</span>`}
        </div>

        <div class="lc-foot">
          <button class="btn-ghost" style="font-size:0.76rem;padding:5px 12px" onclick="openLeadDetail('${c.id}', event)">🔍 View Full Dossier</button>
        </div>
      </article>`;
    }).join("");
  } catch (err) {
    $("commentsScrapeProgress").classList.add("hidden");
    toast("Could not load comments: " + err.message, "error");
  }
}

function exportLeadsCsv() {
  if (!memory.postId) return;
  const isLeadOnly = activeCommentFilter === "leads";
  window.open(`/api/export/comments.csv?post_id=${encodeURIComponent(memory.postId)}&only_leads=${isLeadOnly}`, "_blank");
}

// ── LEAD DETAIL MODAL ────────────────────────────────────────────────────
async function openLeadDetail(commentId, event) {
  if (event) event.stopPropagation();
  try {
    const res = await fetch(`/api/comments/${commentId}`);
    if (!res.ok) throw new Error("not found");
    const d = await res.json();
    const page = d.page || {};
    const post = d.post || {};
    const comment = d.comment || {};
    const quality = { hot: "🔥 Hot", warm: "⚡ Warm", cold: "❄️ Cold", none: "—" }[d.lead_quality] || "—";
    const intent = d.intent ? d.intent.replace(/_/g, " ") : "—";

    $("leadDetailTitle").textContent = "Lead Intelligence Dossier — " + (d.commenter_name || "Prospect");
    const detailInfo = platformInfo(d.platform);
    $("leadDetailContent").innerHTML = `
      <div class="lead-detail-grid">
        <div class="detail-block detail-block-wide">
          <div class="detail-label">Original Comment Text</div>
          <div class="detail-value" style="font-size:0.95rem;background:var(--surface-2);padding:14px;border-radius:var(--radius-sm);border:1px solid var(--border-soft);white-space:pre-wrap;line-height:1.6">${esc(d.comment_text || "—")}</div>
          ${d.comment_url ? `<div style="margin-top:8px"><a class="pc-link" href="${esc(d.comment_url)}" target="_blank" rel="noopener">Open original comment on ${esc(detailInfo.name || "platform")} ↗</a></div>` : ""}
          ${d.reason ? `<div class="detail-reason">🤖 <b>AI Rationale:</b> ${esc(d.reason)} ${d.analyzed_by ? `(${esc(d.analyzed_by)})` : ""}</div>` : ""}
        </div>

        <div class="detail-block">
          <div class="detail-label">Phone Number</div>
          <div class="detail-value">${d.phone ? `<a href="tel:${esc(d.phone)}" class="contact-pill">📞 ${esc(d.phone)}</a>` : "—"}</div>
          <div class="detail-label">WhatsApp Contact</div>
          <div class="detail-value">${d.whatsapp ? `<a href="https://wa.me/${esc(d.whatsapp.replace(/[^\d]/g, ""))}" target="_blank" rel="noopener" class="contact-pill">💬 Direct Chat</a>` : "—"}</div>
          <div class="detail-label">Email Address</div>
          <div class="detail-value">${d.email ? `<a href="mailto:${esc(d.email)}" class="contact-pill">✉️ ${esc(d.email)}</a>` : "—"}</div>
          <div class="detail-label">Website</div>
          <div class="detail-value">${d.website ? `<a href="${esc(d.website)}" target="_blank" rel="noopener" class="pc-link">${esc(d.website)} ↗</a>` : "—"}</div>
        </div>

        <div class="detail-block">
          <div class="detail-label">Budget</div>
          <div class="detail-value">${esc(d.budget || "—")}</div>
          <div class="detail-label">Requirement / Inquiry</div>
          <div class="detail-value">${esc(d.requirement || "—")}</div>
          <div class="detail-label">Location / City</div>
          <div class="detail-value">${esc(d.location || "—")}</div>
          <div class="detail-label">Buying Intent</div>
          <div class="detail-value"><span class="badge" style="background:var(--violet-soft);color:#5535cf">${esc(intent)}</span></div>
          <div class="detail-label">Urgency</div>
          <div class="detail-value">${esc(d.urgency || "—")}</div>
        </div>

        <div class="detail-block">
          <div class="detail-label">Platform</div>
          <div class="detail-value">${detailInfo.icon ? detailInfo.icon + " " : ""}${esc(detailInfo.name || "—")}</div>
          <div class="detail-label">Priority Level</div>
          <div class="detail-value"><b>${esc(d.priority || "—").toUpperCase()}</b></div>
          <div class="detail-label">Lead Quality</div>
          <div class="detail-value">${quality}</div>
          <div class="detail-label">Confidence Score</div>
          <div class="detail-value">${d.confidence != null ? Math.round(d.confidence * 100) + "%" : "—"}</div>
          <div class="detail-label">Lead Score</div>
          <div class="detail-value"><b style="font-size:1.2rem;color:var(--amber-deep);font-family:var(--font-display)">${d.lead_score || 0}</b> / 100</div>
        </div>

        <div class="detail-block detail-block-wide">
          <div class="detail-label">Source Context — Page / Profile</div>
          <div class="detail-value">${esc(page.page_name || "—")}${page.facebook_url ? ` · <a class="pc-link" href="${esc(page.facebook_url)}" target="_blank" rel="noopener">Open Source Page ↗</a>` : ""}</div>
          <div class="detail-label">Source Context — Post</div>
          <div class="detail-value" style="font-size:0.84rem;color:var(--text-dim);margin-top:4px">${esc(post.caption || "—")}${post.post_url ? ` · <a class="pc-link" href="${esc(post.post_url)}" target="_blank" rel="noopener">Open Post ↗</a>` : ""}</div>
          <div class="detail-label">Commenter Profile Link</div>
          <div class="detail-value">${comment.author_profile_url ? `<a class="pc-link" href="${esc(comment.author_profile_url)}" target="_blank" rel="noopener">${esc(comment.author_profile_url)} ↗</a>` : "—"}</div>
        </div>
      </div>`;
    $("leadDetailModal").classList.remove("hidden");
  } catch (err) {
    toast("Could not load lead details", "error");
  }
}

function closeLeadDetails() {
  $("leadDetailModal").classList.add("hidden");
}

// ── Sign-in (admin) ──────────────────────────────────────────────────────
// The backend enforces a session for every /api call; signed-out visitors
// get 401 → send them to /login.
async function checkAuth() {
  try {
    const res = await fetch("/api/auth/me");
    if (res.status === 401) { location.href = "/login"; return; }
    const data = await res.json();
    if (!data.user) { location.href = "/login"; return; }
    const avatar = $("userAvatar");
    if (avatar) avatar.textContent = (data.user.name || data.user.email || "?").charAt(0).toUpperCase();
    const name = $("userName");
    if (name) name.textContent = data.user.email;
    const chip = $("userChip");
    if (chip) chip.classList.remove("hidden");
  } catch (err) { /* backend offline — leave the dashboard alone */ }
}

async function logout() {
  try { await fetch("/api/auth/logout", { method: "POST" }); } catch (err) {}
  location.href = "/login";
}

// ── Boot ─────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  const input = $("urlSearchInput");
  if (input) {
    input.addEventListener("input", () => {
      const hint = $("urlDetectionHint");
      if (hint) {
        hint.textContent = input.value.trim()
          ? "Ready to analyze — platform auto-detected"
          : "Waiting for URL…";
      }
    });
  }
  // one shared "comments per post" number across the comment section and URL search
  const cpp = $("commentsPerPostInput");
  if (cpp) cpp.value = memory.commentsPerPost;
  const urlCpp = $("urlSearchCommentsPerPost");
  if (urlCpp && !urlCpp.dataset.synced) {
    urlCpp.value = memory.commentsPerPost;
    urlCpp.addEventListener("change", () => setCommentsPerPost(urlCpp));
  }
  const modeRadios = document.querySelectorAll('input[name="filterMode"]');
  if (modeRadios.length) {
    modeRadios.forEach((r) => r.addEventListener("change", () => {
      urlFilterTouched = true;
      urlFilterModeChanged();
    }));
    urlFilterModeChanged();
  }
  if (window.AppConfig) AppConfig.load();
  applyUrlFilterDefaults();
  loadUrlFilterCatalog();
  renderRecentSearches();
  navigateToView("search");
  checkAuth();
});
