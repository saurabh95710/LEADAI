/* LeadAI — AI-orchestrated Facebook lead intelligence dashboard.
   Flow: Search (agent) → Pages → Posts → Comments (AI-analyzed leads).
   The agent works on real provider data (Apify / Bright Data) only. */

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

// ── Search ───────────────────────────────────────────────────────────────
async function handleSearch(event) {
  event.preventDefault();
  const query = $("searchQuery").value.trim();
  const limit = parseInt($("searchLimit").value, 10) || 10;
  const provider = (document.querySelector('input[name="provider"]:checked') || {}).value || "apify";
  saveMemory("provider", provider);
  if (!query) return;

  const btn = $("searchBtn");
  const cancelBtn = $("searchCancelBtn");
  btn.disabled = true;
  const prog = $("searchProgress");
  prog.classList.remove("hidden");
  setBadge("Searching", "status-running");

  try {
    const res = await fetch(`/api/search?query=${encodeURIComponent(query)}&limit=${limit}&provider=${provider}`, { method: "POST" });
    if (!res.ok) throw new Error((await res.json()).detail || "Search failed");
    const data = await res.json();
    saveMemory("runId", data.run_id);
    renderIntentChips(data.intent);
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

    if (run.status === "cancelled") {
      setBadge("Cancelled", "status-warn");
      $("searchProgressLabel").textContent = "Search cancelled";
      setTimeout(() => prog.classList.add("hidden"), 2500);
      renderRecentSearches();
      return;
    }

    setBadge(run.status === "completed" ? "Completed" : run.status === "partial" ? "Partial" : "Error",
             run.status === "completed" ? "status-success" : run.status === "partial" ? "status-warn" : "status-error");

    $("searchProgressFill").style.width = run.status === "completed" ? "100%" : "100%";
    $("searchProgressLabel").textContent = run.message || run.error || "Completed";
    setTimeout(() => prog.classList.add("hidden"), 2500);

    if (run.pages_stored > 0) {
      // the backend already auto-collects posts (then top comments) for this
      // run — land on the pages screen, live counts fill in as it works
      navigateToView("pages");
    }
    else renderRecentSearches();
  } catch (err) {
    toast(err.message || "Search failed", "error");
    setBadge("Idle", "status-idle");
  } finally {
    btn.disabled = false;
    cancelBtn.classList.add("hidden");
    renderRecentSearches();
  }
}

function renderIntentChips(intent) {
  const box = $("intentChips");
  if (!intent) { box.classList.add("hidden"); return; }
  const chips = [];
  if (intent.keyword) chips.push(["🎯 Keyword", intent.keyword]);
  if (intent.city) chips.push(["🏙️ City", intent.city]);
  if (intent.state) chips.push(["🗺️ State", intent.state]);
  if (intent.category) chips.push(["🏷️ Category", intent.category]);
  if (!chips.length) { box.classList.add("hidden"); return; }
  box.innerHTML = chips.map(([label, value]) =>
    `<span class="intent-chip"><b>${label}:</b> ${esc(value)}</span>`).join("");
  box.classList.remove("hidden");
}

async function pollSearchRun(runId, onCancelRequested) {
  let cancelled = false;
  if (onCancelRequested) onCancelRequested(() => { cancelled = true; });
  while (true) {
    const res = await fetch(`/api/search/${runId}`);
    const data = await res.json();
    const run = data.search;
    const fill = $("searchProgressFill");
    const label = $("searchProgressLabel");
    label.textContent = run.message || run.error || run.status;

    const phase = { searching: 30, stored: 70, enriching: 90, queued: 5, completed: 100 }[run.phase] || 30;
    fill.style.width = (phase + Math.random() * 4).toFixed(1) + "%";

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
        if (cd.search.status !== "running") return cd.search;
        await sleep(1500);
      }
    }
    await sleep(1500);
  }
}

async function renderRecentSearches() {
  try {
    const res = await fetch("/api/search/history?limit=8");
    const data = await res.json();
    const list = $("recentSearchesList");
    const empty = $("recentSearchesEmpty");
    if (!data.searches || !data.searches.length) {
      list.innerHTML = "";
      empty.classList.remove("hidden");
      return;
    }
    empty.classList.add("hidden");
    list.innerHTML = data.searches.map((s) => `
      <button class="recent-search-chip" onclick="reopenSearch('${s.run_id}')">
        <span class="rs-query">${esc(s.query)}</span>
        <span class="rs-meta">${s.status} · ${s.pages_stored || 0} pages · ${esc(s.created_at || "").replace("T", " ").slice(0, 16)}</span>
      </button>`).join("");
  } catch (err) { /* offline-safe */ }
}

async function reopenSearch(runId) {
  try {
    const res = await fetch(`/api/search/${runId}`);
    const data = await res.json();
    saveMemory("runId", runId);
    renderIntentChips(data.search.intent);
    toast("Opened search: " + data.search.query, "info");
    navigateToView("pages");  // renderPagesScreen auto-fires collection if needed
  } catch (err) {
    toast("Could not open that search", "error");
  }
}

// ── URL SEARCH — paste a social media link, platform auto-detected ──────
const URL_LABELS = {
  facebook: ["Facebook", "🏠"], instagram: ["Instagram", "📸"],
  youtube: ["YouTube", "▶️"], linkedin: ["LinkedIn", "💼"],
};

// replaced with the real handler by handleSearch/handleUrlSearch while a run
// is active — exists so the inline onclick never throws
function cancelCurrentSearch() {}

async function handleUrlSearch(event) {
  event.preventDefault();
  const url = $("urlSearchInput").value.trim();
  const maxPosts = parseInt($("urlSearchLimit").value, 10) || 20;
  if (!url) return;

  const btn = $("urlSearchBtn");
  const cancelBtn = $("urlSearchCancelBtn");
  btn.disabled = true;
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
    renderIntentChips({ keyword: url, type: "url" });
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
    const providerBadge = data.pages[0] && data.pages[0].provider
      ? ` · via <b>${data.pages[0].provider === "brightdata" ? "Bright Data" : "Apify"}</b>`
      : "";
    const collectNote = needsCollect
      ? (data.pages.some((p) => p.posts_status === "running")
          ? " · analyzing posts…"
          : " · auto-analyzing posts now…")
      : "";
    const qualified = data.pages.filter((p) => p.has_qualifying_posts).length;
    statusBox.innerHTML = `<div class="summary-line">${data.pages.length} real Facebook page(s) · <b>${qualified} with qualifying posts</b>${collectNote}${providerBadge}</div>`;
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

      return `<tr class="clickable-row" onclick="openPage('${p.id}')">
        <td>
          <div class="page-cell">
            <img class="page-avatar" src="${esc(p.profile_picture || "")}" loading="lazy" onerror="this.style.display='none'">
            <div class="page-cell-body">
              <div class="page-name">${esc(p.page_name || "Unnamed page")}${p.platform && p.platform !== "facebook" ? `<span class="src-badge">${esc(p.platform.toUpperCase())}</span>` : ""}${p.source_type === "group" ? '<span class="src-badge">GROUP</span>' : ""}${p.verified ? '<span class="verified-badge" title="Verified">✓</span>' : ""}</div>
              <a class="page-link" href="${esc(p.facebook_url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">open ${p.platform && p.platform !== "facebook" ? "on " + esc(p.platform.charAt(0).toUpperCase() + p.platform.slice(1)) : "on Facebook"} ↗</a>
            </div>
          </div>
        </td>
        <td>${esc(p.category || "—")}</td>
        <td class="num">${fmt(p.followers)}</td>
        <td class="num">${fmt(p.likes)}</td>
        <td>${esc(p.phone || "—")}</td>
        <td>${esc(p.email || "—")}</td>
        <td>${p.website ? `<a class="page-link" href="${esc(p.website)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">visit ↗</a>` : "—"}</td>
        <td class="cell-truncate" title="${esc(p.address || "")}">${esc(p.address || "—")}</td>
        <td class="num">${postsCell}</td>
        <td class="num">${commentsCell}</td>
        <td>${activityCell}</td>
        <td class="num">${actionCell}</td>
      </tr>`;
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
        ? `<img class="post-thumb" src="${esc(post.images[0])}" loading="lazy" onerror="this.style.display='none'">`
        : (post.videos && post.videos.length ? `<span class="post-thumb">🎬</span>` : "");

      return `<tr class="clickable-row" onclick="openPost('${post.id}')">
        <td>
          <div class="post-cell">
            ${thumb}
            <div class="post-cell-body">
              <div class="post-text cell-truncate" title="${esc(post.caption || "")}">${esc(post.caption || "No caption")}</div>
            </div>
          </div>
        </td>
        <td>${esc(post.published_date || "—")}</td>
        <td class="num">${fmt(post.likes_count)}</td>
        <td class="num">${fmt(total)}</td>
        <td class="num">${fmt(post.shares_count)}</td>
        <td>${relBadge}</td>
        <td class="num">${actionCell}</td>
      </tr>`;
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
  try {
    const res = await fetch(`/api/posts/${memory.postId}/comments?only_leads=${onlyLeads}`);
    const data = await res.json();
    $("commentsPostName").textContent = (data.post && data.post.caption ? data.post.caption.slice(0, 60) : "this post") || "this post";

    const statusBox = $("commentsScreenStatus");
    const total = data.total_comment_count || 0;
    const scraped = data.scraped_comment_count || 0;
    if (data.comments_status === "running") {
      statusBox.innerHTML = `<div class="summary-line status-running-text"><span class="mini-spinner"></span> Collecting comments and running AI analysis...</div>`;
      statusBox.classList.remove("hidden");
      setTimeout(() => renderCommentsScreen(), 2000);
    } else if (data.comments_status === "skipped") {
      statusBox.innerHTML = `<div class="summary-line status-warn-text">${esc(data.comments_error || "Not scraped — post has fewer than the comment threshold")}</div>`;
      statusBox.classList.remove("hidden");
    } else {
      const progress = (total > 0 && scraped > 0) ? ` · <b>${scraped} of ${total}</b> collected` : ` · ${scraped} collected`;
      statusBox.innerHTML = onlyLeads
        ? `<div class="summary-line">Total comments on this post: <b>${total}</b>${progress} · AI found <b>${data.total}</b> valuable lead(s)</div>`
        : `<div class="summary-line">Total comments on this post: <b>${total}</b>${progress}</div>`;
      statusBox.classList.remove("hidden");
    }

    const tbody = $("commentsGrid");
    const empty = $("commentsListEmpty");
    if (!data.comments.length) {
      tbody.innerHTML = "";
      empty.classList.remove("hidden");
      empty.querySelector(".empty-sub").textContent =
        onlyLeads ? "No valuable comments found on this post yet — try collecting again or uncheck 'Leads only'" : "No comments on this post yet";
      return;
    }
    empty.classList.add("hidden");

    tbody.innerHTML = data.comments.map((c) => {
      const priorityClass = { high: "badge-hot", medium: "badge-warm", low: "badge-cold" }[c.priority] || "";
      const intent = c.intent ? c.intent.replace("_", " ") : "—";
      return `<tr class="clickable-row" onclick="openLeadDetail('${c.id}')">
        <td>
          <div class="commenter-cell">
            <div class="commenter-name">${esc(c.commenter_name || "Unknown")}</div>
            ${c.comment_url ? `<a class="page-link" href="${esc(c.comment_url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">view on Facebook ↗</a>` : ""}
          </div>
        </td>
        <td class="cell-truncate comment-text" title="${esc(c.comment_text || "")}">${esc(c.comment_text || "—")}</td>
        <td>${esc(c.phone || "—")}</td>
        <td>${esc(c.email || "—")}</td>
        <td>${esc(c.whatsapp || "—")}</td>
        <td class="cell-truncate" title="${esc([c.budget, c.requirement].filter(Boolean).join(" · ") || "")}">${esc(c.budget || c.requirement || "—")}</td>
        <td>${esc(c.location || "—")}</td>
        <td>${esc(intent)}</td>
        <td><span class="badge ${priorityClass}">${esc(c.priority)}</span></td>
        <td class="num"><span class="score-pill" title="${esc(c.reason || "")}">${c.lead_score || 0}</span></td>
      </tr>`;
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

// ── Boot ─────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  const saved = memory.provider || localStorage.getItem("leadai_provider") || "apify";
  const checked = document.querySelector(`input[name="provider"][value="${saved}"]`);
  if (checked) checked.checked = true;
  renderRecentSearches();
  navigateToView("search");
});
