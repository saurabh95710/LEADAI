/* LeadAI — AI-orchestrated social lead intelligence dashboard.
   Flow: URL search (platform auto-detected) → Pages → Posts → Comments.
   All data comes from real Apify actor output — never fabricated. */

"use strict";

// ── Agent memory (workflow state survives refreshes) ─────────────────────
const memory = {
  runId: localStorage.getItem("leadai_run_id") || "",
  pageId: localStorage.getItem("leadai_page_id") || "",
  postId: localStorage.getItem("leadai_post_id") || "",
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

function relativeTime(ts) {
  if (!ts) return "";
  const t = new Date(String(ts).replace(" ", "T") + "Z").getTime();
  if (isNaN(t)) return "";
  const diff = Date.now() - t;
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return m + "m ago";
  const h = Math.floor(m / 60);
  if (h < 24) return h + "h ago";
  const d = Math.floor(h / 24);
  if (d < 30) return d + "d ago";
  return new Date(t).toLocaleDateString();
}

function formatDate(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  if (isNaN(d.getTime())) return String(ts);
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
      <button class="rs-row" onclick="reopenSearch('${s.run_id}')" aria-label="Open search for ${esc(s.query)}">
        <span class="rs-icon" aria-hidden="true">${icon}</span>
        <span class="rs-main">
          <span class="rs-title">${esc(s.query)}</span>
          <span class="rs-sub">${esc(label)} · ${count} page${count === 1 ? "" : "s"}</span>
        </span>
        <span class="rs-right">
          <span class="rs-badge ${cls}">${mark} ${stLabel}</span>
          <span class="rs-time">${relativeTime(s.created_at)}</span>
          <span class="rs-open">Open →</span>
        </span>
      </button>`;
  }).join("");
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

  try {
    const res = await fetch(`/api/url/search?url=${encodeURIComponent(url)}&max_posts=${maxPosts}`, { method: "POST" });
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
    statusBox.innerHTML = `<div class="summary-line">${data.pages.length} page(s) · <b>${qualified} with qualifying posts</b>${collectNote}</div>`;
    statusBox.classList.remove("hidden");

    tbody.innerHTML = data.pages.map((p) => {
      const postStatus = p.posts_status || "not_started";
      if (postStatus === "running") anyRunning = true;
      const stale = isStale(p.posts_started_at);
      const found = p.total_posts_found != null ? p.total_posts_found : (p.posts_count || 0);
      const qualifying = p.qualifying_posts_count || 0;
      const err = p.posts_error || "";

      const postsCell = {
        not_started: `<span class="muted">N/A</span>`,
        running: stale
          ? `<span class="muted">N/A</span>`
          : `<span class="muted">${found} found so far</span>`,
        completed: qualifying > 0
          ? `<b>${found} found</b><div class="muted">${qualifying} qualifying</div>`
          : `<span class="muted" title="${esc(err)}">No qualifying posts</span>`,
        empty: `<span class="muted" title="${esc(err)}">0 found</span>`,
        error: `<span class="muted" title="${esc(err)}">—</span>`,
      }[postStatus] || `<span class="muted">N/A</span>`;

      const commentsCell = (postStatus === "completed" && qualifying > 0)
        ? `<b>${fmt(p.total_comments_on_qualifying_posts)}</b> total`
        : `<span class="muted">—</span>`;

      const activityCell = {
        active: `<span class="status-success-text">✓ Active</span>`,
        recent: `Recent`,
        inactive: `<span class="muted">Inactive</span>`,
        unknown: `<span class="muted">N/A</span>`,
      }[p.activity_status] || `<span class="muted">N/A</span>`;

      const actionCell = {
        not_started: `<button class="btn-mini btn-primary-mini" onclick="openPage('${p.id}', event)">🔍 Analyze Posts</button>`,
        running: stale
          ? `<button class="btn-mini" onclick="openPage('${p.id}', event)">↻ Retry</button><div class="row-error">stuck — server restarted?</div>`
          : `<span class="mini-spinner"></span> Analyzing posts... ${found} so far`,
        completed: qualifying > 0
          ? `<button class="btn-mini" onclick="openPage('${p.id}', event)">📝 View Posts</button>`
          : `<span class="muted" title="${esc(err)}">No qualifying posts</span>`,
        empty: `<button class="btn-mini" onclick="openPage('${p.id}', event)">🔍 Analyze Posts</button><div class="row-error" title="${esc(err)}">${esc((err || "No posts returned").slice(0, 80))}</div>`,
        error: `<button class="btn-mini" onclick="openPage('${p.id}', event)">↻ Retry</button><div class="row-error" title="${esc(err)}">${esc((err || "Analyze failed").slice(0, 80))}</div>`,
      }[postStatus] || `<button class="btn-mini" onclick="openPage('${p.id}', event)">🔍 Analyze Posts</button>`;

      const avatar = p.profile_picture
        ? `<img class="pc-avatar" src="${esc(p.profile_picture)}" loading="lazy" onerror="this.style.display='none'">`
        : `<span class="pc-avatar pc-avatar-fallback">${esc((p.page_name || "?").charAt(0).toUpperCase())}</span>`;

      return `<article class="page-card clickable-card" onclick="openPage('${p.id}')">
        <div class="pc-head">
          ${avatar}
          <div class="pc-body">
            <div class="pc-name">${esc(p.page_name || "Unnamed page")}
              ${p.platform && p.platform !== "facebook" ? `<span class="src-badge">${esc(p.platform.toUpperCase())}</span>` : ""}
              ${p.source_type === "group" ? '<span class="src-badge">GROUP</span>' : ""}
              ${p.verified ? '<span class="verified-badge" title="Verified">✓ Verified</span>' : ""}
            </div>
            <a class="pc-link" href="${esc(p.facebook_url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">open ${p.platform && p.platform !== "facebook" ? "on " + esc(p.platform.charAt(0).toUpperCase() + p.platform.slice(1)) : "on Facebook"} ↗</a>
            ${p.category ? `<span class="pc-cat">${esc(p.category)}</span>` : ""}
          </div>
        </div>
        <div class="pc-stats">
          <div class="pc-stat"><div class="pc-stat-value">${fmt(p.followers)}</div><div class="pc-stat-label">Followers</div></div>
          <div class="pc-stat"><div class="pc-stat-value">${fmt(p.likes)}</div><div class="pc-stat-label">Likes</div></div>
          <div class="pc-stat"><div class="pc-stat-value">${postsCell}</div><div class="pc-stat-label">Posts</div></div>
          <div class="pc-stat"><div class="pc-stat-value">${commentsCell}</div><div class="pc-stat-label">Comments</div></div>
          <div class="pc-stat"><div class="pc-stat-value">${activityCell}</div><div class="pc-stat-label">Activity</div></div>
        </div>
        <div class="pc-contact">
          ${p.phone ? `<span class="pc-chip">📞 ${esc(p.phone)}</span>` : ""}
          ${p.email ? `<span class="pc-chip">✉️ ${esc(p.email)}</span>` : ""}
          ${p.website ? `<span class="pc-chip pc-chip-link"><a href="${esc(p.website)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">🌐 ${esc(p.website)}</a></span>` : ""}
          ${p.address ? `<span class="pc-chip pc-chip-addr" title="${esc(p.address)}">📍 ${esc(p.address)}</span>` : ""}
          ${!p.phone && !p.email && !p.website && !p.address ? `<span class="muted">No contact info collected</span>` : ""}
        </div>
        <div class="pc-foot">${actionCell}</div>
      </article>`;
    }).join("");

    if (anyRunning) {
      // auto-refresh counts while the background job works through the run
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
    // not_started / empty / error → (re)analyze posts
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
        ? `<span class="status-success-text">${qual} qualifying post(s) — relevant & ≥ ${min} Facebook comments</span>`
        : `<span class="muted">No qualifying posts — nothing above the ${min}-comment threshold</span>`;
      statusBox.innerHTML = `<div class="summary-line">Posts found: <b>${data.totalPosts}</b> · Relevant: <b>${data.relevantPosts}</b> · Qualifying: <b>${qual}</b> · Comments on qualifying posts: <b>${fmt(data.commentsOnQualifying)}</b> · Latest post: ${esc(data.latestPostDate || "N/A")}<br>${note}</div>`;
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

      let actionCell;
      if (qualifying) {
        actionCell = {
          not_started: `<button class="btn-mini btn-primary-mini" onclick="openPost('${post.id}', event)">💬 Collect comments</button>`,
          running: `<span class="mini-spinner"></span> Collecting... ${scraped} so far`,
          completed: `<button class="btn-mini" onclick="openPost('${post.id}', event)">💬 ${scraped} collected · View</button>`,
          skipped: `<span class="muted" title="${esc(cErr)}">Not scraped</span>`,
          empty: `<span class="muted" title="${esc(cErr)}">0 comments</span>`,
          error: `<button class="btn-mini" onclick="openPost('${post.id}', event)">↻ Retry</button><div class="row-error" title="${esc(cErr)}">${esc((cErr || "Collect failed").slice(0, 80))}</div>`,
        }[cStatus] || `<button class="btn-mini" onclick="openPost('${post.id}', event)">💬 Collect</button>`;
      } else if (scraped > 0) {
        actionCell = `<button class="btn-mini" onclick="openPost('${post.id}', event)">💬 ${scraped} collected · View</button>`;
      } else {
        actionCell = `<span class="muted" title="Below the ${min}-comment threshold — the comment scrape is skipped">Skip · &lt;${min}</span>`;
      }

      const relBadge = post.is_relevant === true
        ? `<span class="badge badge-relevant">✓ Relevant</span>`
        : post.is_relevant === false
          ? `<span class="muted">✗ Not relevant</span>`
          : `<span class="muted">—</span>`;

      const thumb = post.images && post.images.length
        ? `<img class="pt-thumb" src="${esc(post.images[0])}" loading="lazy" onerror="this.style.display='none'">`
        : (post.videos && post.videos.length ? `<span class="pt-thumb pt-thumb-video">🎬</span>` : `<span class="pt-thumb pt-thumb-fallback">📝</span>`);

      return `<article class="post-tile clickable-card" onclick="openPost('${post.id}')">
        <div class="pt-main">
          ${thumb}
          <div class="pt-body">
            <div class="pt-caption cell-truncate" title="${esc(post.caption || "")}">${esc(post.caption || "No caption")}</div>
            <div class="pt-meta">📅 ${esc(post.published_date || "—")} · ${relBadge}</div>
          </div>
        </div>
        <div class="pt-stats">
          <div class="pc-stat"><div class="pc-stat-value">${fmt(post.likes_count)}</div><div class="pc-stat-label">Likes</div></div>
          <div class="pc-stat"><div class="pc-stat-value">${fmt(total)}</div><div class="pc-stat-label">Comments</div></div>
          <div class="pc-stat"><div class="pc-stat-value">${fmt(post.shares_count)}</div><div class="pc-stat-label">Shares</div></div>
        </div>
        <div class="pt-foot">${actionCell}</div>
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
    const postRes = await fetch(`/api/posts/${postId}/comments?max_comments=100`, { method: "POST" });
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

function exportPostsCsv() {
  if (!memory.pageId) return;
  window.open(`/api/export/posts.csv?page_id=${encodeURIComponent(memory.pageId)}`, "_blank");
}

// ── COMMENTS / LEADS ─────────────────────────────────────────────────────
async function renderCommentsScreen() {
  if (!memory.postId) { $("commentsGrid").innerHTML = ""; $("commentsListEmpty").classList.remove("hidden"); return; }
  const onlyLeads = $("commentsLeadsOnly").checked;
  const contactOnly = $("commentsContactOnly").checked;
  try {
    const res = await fetch(`/api/posts/${memory.postId}/comments?only_leads=${onlyLeads}&contact_only=${contactOnly}`);
    const data = await res.json();
    $("commentsPostName").textContent = (data.post && data.post.caption ? data.post.caption.slice(0, 60) : "this post") || "this post";

    const statusBox = $("commentsScreenStatus");
    const total = data.total_comment_count || 0;
    const scraped = data.scraped_comment_count || 0;
    const hasComments = data.comments && data.comments.length;
    if (data.comments_status === "running" && !hasComments) {
      statusBox.innerHTML = `<div class="summary-line status-running-text"><span class="mini-spinner"></span> Collecting comments and running AI analysis...</div>`;
      statusBox.classList.remove("hidden");
      setTimeout(() => renderCommentsScreen(), 2000);
      return;
    }
    if (data.comments_status === "skipped") {
      statusBox.innerHTML = `<div class="summary-line status-warn-text">${esc(data.comments_error || "Not scraped — post has fewer than the comment threshold")}</div>`;
      statusBox.classList.remove("hidden");
    } else {
      const progress = (total > 0 && scraped > 0) ? ` · <b>${scraped} of ${total}</b> collected` : ` · ${scraped} collected`;
      if (data.comments_status === "running") {
        statusBox.innerHTML = `<div class="summary-line status-running-text"><span class="mini-spinner"></span> AI analysis in progress — showing <b>${data.total}</b> comment(s) found so far, refresh for updates…</div>`;
      } else if (onlyLeads) {
        statusBox.innerHTML = `<div class="summary-line">Total comments on this post: <b>${total}</b>${progress} · AI found <b>${data.total}</b> valuable lead(s)</div>`;
      } else if (contactOnly) {
        statusBox.innerHTML = `<div class="summary-line">Comments with a phone/email on this post: <b>${data.total}</b> of <b>${data.all_count || 0}</b> total</div>`;
      } else {
        statusBox.innerHTML = `<div class="summary-line">Total comments on this post: <b>${data.total}</b>${progress} · <b>${data.contact_count || 0}</b> with phone/email</div>`;
      }
      statusBox.classList.remove("hidden");
    }

    const tbody = $("commentsGrid");
    const empty = $("commentsListEmpty");
    if (!hasComments) {
      tbody.innerHTML = "";
      empty.classList.remove("hidden");
      empty.querySelector(".empty-sub").textContent =
        onlyLeads ? "No valuable comments found on this post yet — try collecting again or uncheck 'Leads only'"
        : contactOnly ? "No comments with a 10-digit phone number or email on this post yet"
        : "No comments on this post yet";
      return;
    }
    empty.classList.add("hidden");

    tbody.innerHTML = data.comments.map((c) => {
      const priorityClass = { high: "badge-hot", medium: "badge-warm", low: "badge-cold" }[c.priority] || "";
      const intent = c.intent ? c.intent.replace("_", " ") : "—";
      const contactBadge = c.has_contact
        ? `<span class="badge badge-lead" title="Has phone or email">📞 contact</span>`
        : "";
      return `<article class="lead-card clickable-card${c.has_contact ? " lead-card-highlight" : ""}" onclick="openLeadDetail('${c.id}')">
        <div class="lc-head">
          <span class="lc-avatar">${esc((c.commenter_name || "?").trim().charAt(0).toUpperCase())}</span>
          <div class="lc-body">
            <div class="lc-name">${esc(c.commenter_name || "Unknown")} ${contactBadge}
              ${c.comment_url ? `<a class="page-link" href="${esc(c.comment_url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">view on Facebook ↗</a>` : ""}
            </div>
            <div class="lc-text cell-truncate" title="${esc(c.comment_text || "")}">${esc(c.comment_text || "—")}</div>
            ${c.published_date ? `<div class="lc-meta">🕒 ${esc(formatDate(c.published_date))}</div>` : ""}
          </div>
          <div class="lc-right">
            <span class="score-pill" title="${esc(c.reason || "")}">${c.lead_score || 0}</span>
            ${c.priority ? `<span class="badge ${priorityClass}">${esc(c.priority)}</span>` : ""}
          </div>
        </div>
        <div class="lc-chips">
          ${c.phone ? `<span class="lc-chip">📞 ${esc(c.phone)}</span>` : ""}
          ${c.email ? `<span class="lc-chip">✉️ ${esc(c.email)}</span>` : ""}
          ${c.whatsapp ? `<span class="lc-chip">💬 ${esc(c.whatsapp)}</span>` : ""}
          ${c.budget || c.requirement ? `<span class="lc-chip">💰 ${esc(c.budget || c.requirement)}</span>` : ""}
          ${c.location ? `<span class="lc-chip">📍 ${esc(c.location)}</span>` : ""}
          ${intent !== "—" ? `<span class="lc-chip lc-chip-intent">🎯 ${esc(intent)}</span>` : ""}
          ${!c.phone && !c.email && !c.whatsapp && !c.budget && !c.requirement && !c.location && intent === "—" ? `<span class="muted">No details extracted</span>` : ""}
        </div>
      </article>`;
    }).join("");
  } catch (err) {
    toast("Could not load comments: " + err.message, "error");
  }
}

function exportLeadsCsv() {
  if (!memory.postId) return;
  window.open(`/api/export/comments.csv?post_id=${encodeURIComponent(memory.postId)}&only_leads=true`, "_blank");
}

// ── LEAD DETAIL MODAL ────────────────────────────────────────────────────
async function openLeadDetail(commentId) {
  try {
    const res = await fetch(`/api/comments/${commentId}`);
    if (!res.ok) throw new Error("not found");
    const d = await res.json();
    const page = d.page || {};
    const post = d.post || {};
    const comment = d.comment || {};
    const quality = { hot: "🔥 Hot", warm: "⚡ Warm", cold: "❄️ Cold", none: "—" }[d.lead_quality] || "—";
    const intent = d.intent ? d.intent.replace("_", " ") : "—";

    $("leadDetailTitle").textContent = "Lead Details — " + (d.commenter_name || "Unknown commenter");
    $("leadDetailContent").innerHTML = `
      <div class="lead-detail-grid">
        <div class="detail-block detail-block-wide">
          <div class="detail-label">Comment</div>
          <div class="detail-value">${esc(d.comment_text || "—")}</div>
          ${d.comment_url ? `<a class="page-link" href="${esc(d.comment_url)}" target="_blank" rel="noopener">View on Facebook ↗</a>` : ""}
          ${d.reason ? `<div class="detail-reason">AI: ${esc(d.reason)} (${esc(d.analyzed_by)})</div>` : ""}
        </div>
        <div class="detail-block">
          <div class="detail-label">Phone</div>
          <div class="detail-value">${d.phone ? `<a href="tel:${esc(d.phone)}">${esc(d.phone)}</a>` : "—"}</div>
          <div class="detail-label">WhatsApp</div>
          <div class="detail-value">${d.whatsapp ? `<a href="https://wa.me/${esc(d.whatsapp.replace(/[^\d]/g, ""))}" target="_blank">${esc(d.whatsapp)}</a>` : "—"}</div>
          <div class="detail-label">Email</div>
          <div class="detail-value">${d.email ? `<a href="mailto:${esc(d.email)}">${esc(d.email)}</a>` : "—"}</div>
          <div class="detail-label">Website</div>
          <div class="detail-value">${d.website ? `<a href="${esc(d.website)}" target="_blank" rel="noopener">${esc(d.website)}</a>` : "—"}</div>
        </div>
        <div class="detail-block">
          <div class="detail-label">Budget</div>
          <div class="detail-value">${esc(d.budget || "—")}</div>
          <div class="detail-label">Requirement</div>
          <div class="detail-value">${esc(d.requirement || "—")}</div>
          <div class="detail-label">Location</div>
          <div class="detail-value">${esc(d.location || "—")}</div>
          <div class="detail-label">Intent</div>
          <div class="detail-value">${esc(intent)}</div>
          <div class="detail-label">Urgency</div>
          <div class="detail-value">${esc(d.urgency || "—")}</div>
        </div>
        <div class="detail-block">
          <div class="detail-label">Priority</div>
          <div class="detail-value">${esc(d.priority || "—")}</div>
          <div class="detail-label">Lead Quality</div>
          <div class="detail-value">${quality}</div>
          <div class="detail-label">Confidence</div>
          <div class="detail-value">${d.confidence != null ? Math.round(d.confidence * 100) + "%" : "—"}</div>
          <div class="detail-label">Lead Score</div>
          <div class="detail-value">${d.lead_score || 0}<span class="muted">/100</span></div>
        </div>
        <div class="detail-block detail-block-wide">
          <div class="detail-label">Context — Page</div>
          <div class="detail-value">${esc(page.page_name || "—")}${page.facebook_url ? ` · <a class="page-link" href="${esc(page.facebook_url)}" target="_blank" rel="noopener">open ↗</a>` : ""}</div>
          <div class="detail-label">Context — Post</div>
          <div class="detail-value cell-truncate">${esc((post.caption || "—").slice(0, 200))}${post.post_url ? ` · <a class="page-link" href="${esc(post.post_url)}" target="_blank" rel="noopener">open ↗</a>` : ""}</div>
          <div class="detail-label">Commenter Profile</div>
          <div class="detail-value">${comment.author_profile_url ? `<a class="page-link" href="${esc(comment.author_profile_url)}" target="_blank" rel="noopener">${esc(comment.author_profile_url)}</a>` : "—"}</div>
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
  renderRecentSearches();
  navigateToView("search");
  checkAuth();
});
