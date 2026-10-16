/* LeadAI — Keyword search (customer app, New search → "Keyword search" tab).
   Find profiles by keyword on Instagram / Facebook / YouTube / LinkedIn, filter
   them (followers, posts, comments, engagement, activity…), preview the token
   cost, run the staged search (discovery → profile check → recent posts →
   comments), browse the profiles that pass (rejected ones one click away, with
   a summary of why), re-apply other filters for free and continue the search.
   Location and business type are also pushed into the Apify search itself
   ("Searched Apify for …" shows what was sent).
   Loaded after app.js and uses its helpers ($, api, esc, toast, renderTable…).
   API: /api/keyword-search (see app/api/routes/keyword_search.py). */

"use strict";

const KW_API = "/api/keyword-search";
const KW_PLATFORMS = ["instagram", "facebook", "youtube", "linkedin"];
const KW_RUN_KEY = "leadai_kw_run_id";
const KW_POLL_MS = 1500;

/* Help text, unit and group of each filter the API knows (labels come from /capabilities). */
const KW_FILTER_UI = {
  min_followers: { group: "profile", unit: "followers", help: "Skip small accounts. Subscribers on YouTube." },
  max_followers: { group: "profile", unit: "followers", help: "Skip very large accounts, which rarely answer comments." },
  min_posts: { group: "profile", unit: "posts", help: "Total posts the profile has published." },
  location_keyword: { group: "profile", text: true, match: true, placeholder: "e.g. Kota, Rajasthan", help: "Places separated by commas, most specific first, e.g. Kota, Rajasthan. It's sent to the Apify search, and profiles must mention all of these places (or any of them, if you choose)." },
  location_match: { group: "profile", choice: true, inline: true },
  category_keyword: { group: "profile", text: true, placeholder: "e.g. software, IT services", help: "e.g. software, IT services — matched against the profile's category and bio; the first word also narrows YouTube searches." },
  exclude_keywords: { group: "profile", text: true, placeholder: "e.g. jobs, hiring, giveaway", help: "Comma-separated. Skip profiles whose name or bio contains any of them." },
  business_account_only: { group: "profile", bool: true, help: "Business or creator accounts only." },
  verified_only: { group: "profile", bool: true, help: "Accounts with a verified badge only." },
  has_contact_info: { group: "profile", bool: true, help: "An email, phone number or website was found on the profile." },
  min_avg_comments: { group: "posts", unit: "per post", step: "0.1", help: "Average comments on the recent posts analyzed." },
  min_total_comments: { group: "posts", unit: "comments", help: "Comments added up over the recent posts analyzed." },
  min_engagement_rate: { group: "posts", unit: "%", step: "0.1", help: "Average likes + comments per post ÷ followers × 100." },
  active_within_days: { group: "posts", unit: "days", min: 1, help: "The newest post is at most this many days old." },
};
const KW_FILTER_ORDER = ["min_followers", "max_followers", "min_posts", "business_account_only", "verified_only",
  "has_contact_info", "location_keyword", "category_keyword", "exclude_keywords",
  "min_avg_comments", "min_total_comments", "min_engagement_rate", "active_within_days"];

/* Live stepper: one step per count the run reports, tied to the stage that fills it. */
const KW_STEPS = [
  { key: "discovered", label: "Discovered", stage: "discovery" },
  { key: "enriched", label: "Profiles checked", stage: "enrichment" },
  { key: "passed", label: "Passed filters", stage: "sampling" },
  { key: "comments_scraped", label: "Comments collected", stage: "deep_scrape" },
  { key: "leads", label: "Leads", stage: "deep_scrape" },
];
const KW_PHASE_PCT = { queued: 4, discovery: 15, enrichment: 38, sampling: 58, deep_scrape: 80 };
const KW_TOKEN_STAGES = [
  ["discover", "discovery", "Keyword searches", "one per keyword × platform"],
  ["enrich", "enrichment", "Profile details", "only where search results lack them"],
  ["sample", "sampling", "Recent posts", "for profiles that pass the profile filters (worst case)"],
  ["collect", "deep_scrape", "Posts & comments", "for profiles that pass every filter"],
];
const KW_STATUS = {
  passed: ["completed", "Passed"], rejected: ["failed", "Rejected"], needs_sampling: ["pending", "Needs posts"],
  pending: ["running", "Checking"], error: ["failed", "Error"],
};
/* Results open on "Passed"; the other statuses stay one tab (or one "Show them" link) away. */
const KW_TABS = [["passed", "Passed"], ["rejected", "Rejected"], ["needs_sampling", "Needs posts"], ["error", "Errors"], ["", "All"]];
const KW_STAGE_WORDS = { discovery: "finding profiles", enrichment: "reading profile details", sampling: "reading recent posts", deep_scrape: "collecting comments" };
const KW_DEFAULT_TAB = "passed";

const KW = {
  booted: false,
  planBlocked: false,
  caps: null,
  capsAt: 0,
  capsLoading: null,
  capsError: "",
  mode: "link",
  keywords: [],
  estSeq: 0,
  est: null,           // last good estimate
  estKey: "",          // request it belongs to
  estFresh: false,
  starting: false,
  presets: [],
  runId: "",
  run: null,
  pollToken: 0,
  lastCounts: "",
  lastResultsAt: 0,
  res: { status: KW_DEFAULT_TAB, platform: "", sort: "followers", page: 1, pageSize: 25, counts: null },
  suggestions: [],     // one-click fixes offered when no profile passed
};

// ── small helpers ──────────────────────────────────────────────────────────
const kwNum = (n) => (n === null || n === undefined || n === "" || isNaN(Number(n)) ? "—" : Number(n).toLocaleString());
const kwPlat = (p) => PLATFORMS[p] || String(p || "");
const kwPlural = (n, one, many) => `${kwNum(n)} ${Number(n) === 1 ? one : (many || one + "s")}`;
function kwStore(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key) || "";
    if (value) localStorage.setItem(key, value); else localStorage.removeItem(key);
  } catch (_) { /* storage blocked: the in-memory state still works */ }
  return "";
}
function kwModeKey() { const u = portal.user || {}; return "leadai_search_mode." + (u.user_id || u.email || "anon"); }
function kwFilterLabel(key) {
  const f = KW.caps && KW.caps.filters && KW.caps.filters[key];
  return (f && f.label) || String(key).replace(/_/g, " ");
}
function kwErr(id, msg) {
  const el = $(id);
  if (el) el.textContent = msg || "";
}
function kwCanCreate() { return !portal.user || can("search.create"); }
function kwPlanHas() {
  if (KW.planBlocked) return false;
  const c = KW.caps && KW.caps.caps;
  if (!c) return true;
  if (c.plan && c.plan.has_keyword_search === false) return false;
  return !["max_keywords", "max_candidates", "max_recent_posts"].some((k) => c[k] !== undefined && Number(c[k]) <= 0);
}
/** 402 PLAN_LIMIT with limit 0 = the plan / trial doesn't include keyword search at all. */
function kwIsNoPlan(res) {
  const d = (res.data && res.data.detail) || {};
  return res.status === 402 && errCode(res.data) === "PLAN_LIMIT" && Number(d.limit) === 0;
}
function kwPlanBlocked() {
  KW.planBlocked = true;
  kwApplyAvailability(false);
  showQuotaExceededModal({ code: "FEATURE_NOT_AVAILABLE", feature: "keyword_search", plan: ((kwCaps().plan || {}).name) || "" });
}
function kwFeatureOn() {
  try {
    const cfg = window.AppConfig && AppConfig.get && AppConfig.get();
    if (cfg && cfg.features && cfg.features.keyword_search === false) return false;
  } catch (_) { /* config not loaded yet */ }
  return !KW.caps || KW.caps.enabled !== false;
}

// ── tabs: Search by link | Keyword search ─────────────────────────────────
function kwSetMode(mode, opts = {}) {
  if (mode === "keyword" && !kwFeatureOn()) mode = "link";
  KW.mode = mode;
  const link = mode !== "keyword";
  [["searchTabLink", link], ["searchTabKeyword", !link]].forEach(([id, on]) => {
    const t = $(id);
    if (!t) return;
    t.classList.toggle("active", on);
    t.setAttribute("aria-selected", on ? "true" : "false");
    t.tabIndex = on ? 0 : -1;
  });
  $("searchPanelLink").classList.toggle("hidden", !link);
  $("searchPanelKeyword").classList.toggle("hidden", link);
  const hero = document.querySelector("#view-search .hero");
  if (hero) {
    hero.querySelector(".hero-badge-text").textContent = link ? "URL Search Agent" : "Keyword Search Agent";
    hero.querySelector(".hero-sub").textContent = link
      ? "Paste a Facebook, Instagram, YouTube or LinkedIn link. The agent collects the page, its posts and comments, qualifies them and scores every lead with AI."
      : "Type what your buyers follow, like “villa rental” or “wedding photographer”. The agent finds matching profiles, keeps the ones that pass your filters and collects their comments for AI lead scoring.";
  }
  if (opts.remember !== false) kwStore(kwModeKey(), mode);
  if (opts.focus) { const t = $(link ? "searchTabLink" : "searchTabKeyword"); if (t) t.focus(); }
  if (!link) kwEnsureCaps().then(() => { if (KW.mode === "keyword") kwScheduleEstimate(); });
}
function kwRestoreMode() { kwSetMode(kwStore(kwModeKey()) === "keyword" ? "keyword" : "link", { remember: false }); }

// ── capabilities ──────────────────────────────────────────────────────────
async function kwEnsureCaps(force) {
  if (KW.capsLoading) return KW.capsLoading;
  if (KW.caps && !force && Date.now() - KW.capsAt < 120000) return KW.caps;
  KW.capsLoading = (async () => {
    const res = await api(KW_API + "/capabilities");
    if (res.ok) {
      const first = !KW.caps;
      KW.caps = res.data || {};
      KW.capsAt = Date.now();
      KW.capsError = "";
      kwApplyAvailability(first);
    } else {
      KW.capsError = res.status === 403 && errCode(res.data) === "permission_denied"
        ? "denied" : (res.networkError ? "You appear to be offline." : errText(res.data, "Keyword search is unavailable right now."));
      kwApplyAvailability(false);
    }
    return KW.caps;
  })().finally(() => { KW.capsLoading = null; });
  return KW.capsLoading;
}

/** Tab visibility, plan / switch notices, platform availability, caps and defaults. */
function kwApplyAvailability(first) {
  const on = kwFeatureOn() && KW.capsError !== "denied";
  const tab = $("searchTabKeyword");
  if (tab) tab.classList.toggle("hidden", !on);
  $("searchModeBar").classList.toggle("hidden", !on);
  if (!on && KW.mode === "keyword") kwSetMode("link", { remember: false });
  const notice = $("kwNotice");
  const form = $("kwFormWrap");
  let msg = "";
  if (KW.capsError && KW.capsError !== "denied") {
    msg = `<div class="inline-alert alert-danger"><div><strong>Couldn't load keyword search</strong><div>${esc(KW.capsError)}</div></div><button type="button" class="btn-secondary btn-sm" data-kw-action="reload-caps">↻ Try again</button></div>`;
  } else if (KW.caps && !kwPlanHas()) {
    const plan = (KW.caps.caps.plan || {});
    msg = `<div class="inline-alert alert-info kw-upgrade"><div><strong>Keyword search isn't included in ${esc(plan.name ? "the " + plan.name + " plan" : "your plan")}</strong>
      <div>Upgrade to find new Instagram, Facebook, YouTube and LinkedIn profiles by keyword, filter them by followers, activity and comments, and collect their leads automatically. Your past keyword searches stay available below.</div></div>${upgradeButtonHtml("FEATURE_NOT_AVAILABLE")}</div>`;
  } else if (KW.caps && !KW_PLATFORMS.some((p) => (KW.caps.platform_enabled || {})[p])) {
    msg = `<div class="inline-alert alert-warning"><div><strong>Keyword search is paused</strong><div>Your administrator has switched it off on every platform. Search by link still works.</div></div></div>`;
  }
  notice.innerHTML = msg;
  notice.classList.toggle("hidden", !msg);
  const usable = Boolean(KW.caps) && !KW.capsError && kwPlanHas() && kwCanCreate();
  form.classList.toggle("hidden", !usable);
  if (!KW.caps) return;
  kwRenderPlatforms();
  kwRenderLimits();
  if (first) {
    kwRenderFilterFields("kwf", $("kwFilterFields"), KW.caps.default_filters || {}, false);
    kwFillLimits();
  } else {
    kwRefreshSupport("kwf");
  }
  kwUpdateFiltersSummary();
  kwUpdateKeywordUi();
}

function kwRenderPlatforms() {
  const box = $("kwPlatforms");
  const enabled = KW.caps.platform_enabled || {};
  const checked = new Set(kwSelectedPlatforms());
  const firstRender = !box.children.length;
  box.innerHTML = KW_PLATFORMS.map((p) => {
    const on = Boolean(enabled[p]);
    const why = on ? "" : p === "linkedin"
      ? "Off by default (LinkedIn's terms forbid scraping). Your administrator can switch it on."
      : "Switched off by your administrator.";
    const isChecked = on && (firstRender ? (p === "instagram" || p === "facebook") : checked.has(p));
    const icon = (URL_LABELS[p] || [])[1] || "";
    return `<label class="kw-platform${on ? "" : " is-off"}">
      <input type="checkbox" name="kwPlatform" value="${p}" ${isChecked ? "checked" : ""} ${on ? "" : "disabled"} ${why ? `aria-describedby="kwPlatWhy_${p}"` : ""}>
      <span class="kw-platform-body"><span class="kw-platform-name">${icon} ${esc(kwPlat(p))}</span>
      ${why ? `<span class="kw-platform-why" id="kwPlatWhy_${p}">${esc(why)}</span>` : ""}</span></label>`;
  }).join("");
  if (firstRender && !kwSelectedPlatforms().length) {
    const first = box.querySelector('input[name="kwPlatform"]:not(:disabled)');
    if (first) first.checked = true;
  }
}
function kwSelectedPlatforms() {
  return Array.from(document.querySelectorAll('#kwPlatforms input[name="kwPlatform"]:checked')).map((i) => i.value);
}

function kwCaps() { return (KW.caps && KW.caps.caps) || {}; }
function kwRenderLimits() {
  const c = kwCaps();
  const set = (id, max, hint) => {
    const el = $(id);
    if (!el) return;
    if (max) el.max = max;
    const h = $(id + "Hint");
    if (h) h.textContent = hint;
  };
  set("kwMaxCandidates", c.max_candidates, `1 – ${kwNum(c.max_candidates)} across all platforms`);
  set("kwRecentPosts", c.max_recent_posts, `1 – ${kwNum(c.max_recent_posts)} per profile`);
  set("kwCommentsPerPost", c.comments_per_post, `1 – ${kwNum(c.comments_per_post)} per post`);
  const kwHint = $("kwKeywordsCount");
  if (kwHint) kwHint.textContent = `${KW.keywords.length} of ${c.max_keywords || 5}`;
}
function kwFillLimits() {
  const c = kwCaps();
  const d = (KW.caps && KW.caps.default_filters) || {};
  $("kwMaxCandidates").value = Math.min(50, c.max_candidates || 50);
  $("kwRecentPosts").value = Math.min(d.recent_posts_to_analyze || 10, c.max_recent_posts || 30);
  $("kwCommentsPerPost").value = Math.min(c.comments_per_post_default || 30, c.comments_per_post || 100);
  $("kwMaxTokens").value = "";
}
/** Clamp a limit input to [1, cap]; returns a note when the value had to change. */
function kwClamp(id, cap, label) {
  const el = $(id);
  const raw = el.value.trim();
  if (raw === "") return "";
  let v = Math.floor(Number(raw));
  let note = "";
  if (!isFinite(v) || v < 1) { v = 1; note = `${label} set to 1 (the minimum).`; }
  if (cap && v > cap) { v = cap; note = `${label} lowered to ${kwNum(cap)}, your plan's limit.`; }
  if (String(v) !== raw) el.value = v;
  return note;
}
function kwClampAll() {
  const c = kwCaps();
  const notes = [kwClamp("kwMaxCandidates", c.max_candidates, "Profiles to discover"),
    kwClamp("kwRecentPosts", c.max_recent_posts, "Recent posts"),
    kwClamp("kwCommentsPerPost", c.comments_per_post, "Comments per post")].filter(Boolean);
  const box = $("kwLimitNote");
  box.textContent = notes.join(" ");
  box.classList.toggle("hidden", !notes.length);
}

// ── filters (generated for the new-search form "kwf" and the re-apply panel "kwr") ──
/** A filter no platform can measure (or one the API marks hidden, e.g. language) isn't offered. */
function kwFilterOffered(k) {
  const f = KW.caps && KW.caps.filters && KW.caps.filters[k];
  if (!f || f.hidden || !KW_FILTER_UI[k] || KW_FILTER_UI[k].inline) return false;
  const plats = (KW.caps && KW.caps.platforms) || {};
  const names = Object.keys(plats);
  return !names.length || names.some((p) => (((plats[p] || {}).filters || {})[k] || {}).support !== "unsupported");
}
function kwMatchSelect(prefix, value) {
  const id = `${prefix}_location_match`;
  return `<span class="kw-match-wrap"><label for="${id}">Profiles must mention</label>
    <select id="${id}" class="form-input form-input-sm" data-kw-filter="location_match" aria-describedby="${prefix}_location_keyword_help">
      <option value="all" ${value === "any" ? "" : "selected"}>all places</option>
      <option value="any" ${value === "any" ? "selected" : ""}>any place</option></select></span>`;
}
function kwRenderFilterFields(prefix, container, values, strict) {
  const keys = KW_FILTER_ORDER.filter(kwFilterOffered);
  const field = (k) => {
    const ui = KW_FILTER_UI[k];
    const id = `${prefix}_${k}`;
    const v = values[k];
    const desc = `${id}_help ${id}_sup ${id}_err`;
    if (ui.bool) {
      return `<div class="kw-ff kw-ff-bool"><label class="kw-check" for="${id}"><input type="checkbox" id="${id}" data-kw-filter="${k}" ${v ? "checked" : ""} aria-describedby="${desc}"><span>${esc(kwFilterLabel(k))}</span></label>
        <span class="field-hint" id="${id}_help">${esc(ui.help)}</span><span class="kw-sup-row" id="${id}_sup"></span><div class="field-error" id="${id}_err" role="alert"></div></div>`;
    }
    const val = Array.isArray(v) ? v.join(", ") : (v === undefined || v === null ? "" : v);
    const input = ui.text
      ? `<input type="text" id="${id}" class="form-input form-input-sm" data-kw-filter="${k}" value="${esc(val)}" placeholder="${esc(ui.placeholder || "")}" maxlength="${k === "exclude_keywords" ? 2000 : 100}" autocomplete="off" aria-describedby="${desc}">`
      : `<span class="kw-unit-wrap"><input type="number" id="${id}" class="form-input form-input-sm" data-kw-filter="${k}" value="${esc(val)}" min="${ui.min || 0}" step="${ui.step || 1}" inputmode="${ui.step ? "decimal" : "numeric"}" placeholder="off" aria-describedby="${desc}"><span class="kw-unit" aria-hidden="true">${esc(ui.unit || "")}</span></span>`;
    return `<div class="kw-ff${ui.match ? " kw-ff-wide" : ""}"><label class="field-label" for="${id}">${esc(kwFilterLabel(k))}</label>${input}${ui.match ? kwMatchSelect(prefix, values.location_match) : ""}
      <span class="field-hint" id="${id}_help">${esc(ui.help)}</span><span class="kw-sup-row" id="${id}_sup"></span><div class="field-error" id="${id}_err" role="alert"></div></div>`;
  };
  const group = (g, title, sub) => `<fieldset class="kw-fgroup"><legend class="kw-fgroup-title">${esc(title)} <span class="kw-fgroup-sub">${esc(sub)}</span></legend>
      <div class="kw-fgrid">${keys.filter((k) => KW_FILTER_UI[k].group === g).map(field).join("")}</div></fieldset>`;
  container.innerHTML = group("profile", "Profile", "checked right after discovery, before any posts are fetched")
    + group("posts", "Recent posts", "measured on the recent posts analyzed for each profile")
    + `<div class="kw-strict"><label class="kw-check" for="${prefix}_strict"><input type="checkbox" id="${prefix}_strict" data-kw-strict ${strict ? "checked" : ""} aria-describedby="${prefix}_strict_help"><span>Strict mode</span></label>
      <p class="field-hint" id="${prefix}_strict_help">Off: when a platform can't measure a filter (for example total posts on Facebook) or a profile doesn't show a value, the profile is kept and the filter is listed as “not measurable” or “unknown” next to it. On: those profiles are rejected.</p></div>`;
  kwRefreshSupport(prefix);
}

/** Per-platform support badges for the platforms the search uses. */
function kwRefreshSupport(prefix) {
  if (!KW.caps) return;
  const plats = prefix === "kwr" && KW.run ? KW.run.platforms : kwSelectedPlatforms();
  Object.keys(KW_FILTER_UI).forEach((k) => {
    const row = $(`${prefix}_${k}_sup`);
    if (!row) return;
    row.innerHTML = plats.map((p) => {
      const c = ((((KW.caps.platforms || {})[p] || {}).filters || {})[k]) || {};
      if (!c.support || c.support === "supported") return "";
      const no = c.support === "unsupported";
      return `<span class="kw-sup ${no ? "kw-sup-no" : "kw-sup-part"}" title="${esc(c.note || "")}">${esc(kwPlat(p))}: ${no ? "not available" : "partial"}${c.note ? `<span class="sr-only"> (${esc(c.note)})</span>` : ""}</span>`;
    }).join("");
  });
}

function kwReadFilters(prefix) {
  const out = {};
  document.querySelectorAll(`[id^="${prefix}_"][data-kw-filter]`).forEach((el) => {
    const k = el.dataset.kwFilter;
    const ui = KW_FILTER_UI[k] || {};
    if (ui.bool) { if (el.checked) out[k] = true; return; }
    if (ui.choice) { if (el.value) out[k] = el.value; return; }
    const raw = el.value.trim();
    if (!ui.text) {
      if (el.validity && el.validity.badInput) { out[k] = "not-a-number"; return; }
      if (raw === "") return;
      out[k] = Number(raw);
      return;
    }
    if (!raw) return;
    out[k] = k === "exclude_keywords" ? raw.split(",").map((w) => w.trim()).filter(Boolean) : raw;
  });
  if (!out.location_keyword) delete out.location_match;   // only meaningful with a location
  return out;
}
function kwReadStrict(prefix) { const el = $(`${prefix}_strict`); return Boolean(el && el.checked); }
function kwFillFilters(prefix, filters, strict) {
  document.querySelectorAll(`[id^="${prefix}_"][data-kw-filter]`).forEach((el) => {
    const k = el.dataset.kwFilter;
    const v = (filters || {})[k];
    if (KW_FILTER_UI[k].bool) el.checked = Boolean(v);
    else if (KW_FILTER_UI[k].choice) el.value = v === "any" ? "any" : "all";
    else el.value = Array.isArray(v) ? v.join(", ") : (v === undefined || v === null ? "" : v);
  });
  const s = $(`${prefix}_strict`);
  if (s) s.checked = Boolean(strict);
}
function kwUpdateFiltersSummary() {
  const n = Object.keys(kwReadFilters("kwf")).filter((k) => k !== "location_match").length;
  const el = $("kwFiltersSummary");
  if (el) el.textContent = `${n} active${kwReadStrict("kwf") ? " · strict" : ""}`;
}

/** Field-level errors from a 422 ({errors: {field: msg}} or FastAPI's validation list). */
function kwShowErrors(prefix, data) {
  const d = (data && data.detail) || {};
  let errors = {};
  if (d && typeof d === "object" && !Array.isArray(d) && d.errors) errors = d.errors;
  else if (Array.isArray(d)) d.forEach((e) => { const loc = e.loc || []; errors[loc[loc.length - 1]] = String(e.msg || "invalid").replace(/^Value error,\s*/i, ""); });
  const shown = [];
  Object.entries(errors).forEach(([field, msg]) => {
    let id = `${prefix}_${field}_err`;
    if (field === "keywords") id = "kwKeywordsErr";
    else if (field === "platforms") id = "kwPlatformsErr";
    else if (field === "max_candidates") id = "kwMaxCandidatesErr";
    else if (field === "recent_posts" || (field === "recent_posts_to_analyze" && prefix === "kwf")) id = "kwRecentPostsErr";
    else if (field === "comments_per_post") id = "kwCommentsPerPostErr";
    else if (field === "max_tokens") id = "kwMaxTokensErr";
    else if (field === "location_match") id = `${prefix}_location_keyword_err`;
    const el = $(id);
    const label = field === "keywords" ? "Keywords" : KW_FILTER_UI[field] ? kwFilterLabel(field) : field.replace(/_/g, " ");
    const text = /^must|^required|^at most|^each|^unknown/i.test(msg) ? `${label} ${msg}.`.replace(/\.\.$/, ".") : String(msg);
    if (el) {
      el.textContent = text;
      const input = $(id.replace(/_err$|Err$/, ""));
      if (input) input.setAttribute("aria-invalid", "true");
      if (prefix === "kwf" && KW_FILTER_UI[field]) $("kwFiltersPanel").open = true;
    }
    shown.push(text);
  });
  return shown;
}
function kwClearErrors(prefix) {
  const scope = prefix === "kwr" ? $("kwReapply") : $("kwForm");
  if (!scope) return;
  scope.querySelectorAll(".field-error").forEach((e) => { e.textContent = ""; });
  scope.querySelectorAll('[aria-invalid="true"]').forEach((e) => e.removeAttribute("aria-invalid"));
}

// ── keywords (chips) ──────────────────────────────────────────────────────
function kwAddKeywords(text) {
  const max = kwCaps().max_keywords || 5;
  let refused = 0;
  String(text || "").split(/[,\n]/).map((k) => k.replace(/\s+/g, " ").trim()).filter(Boolean).forEach((k) => {
    if (k.length > 100) { refused++; return; }
    if (KW.keywords.some((x) => x.toLowerCase() === k.toLowerCase())) return;
    if (KW.keywords.length >= max) { refused++; return; }
    KW.keywords.push(k);
  });
  kwErr("kwKeywordsErr", refused ? (KW.keywords.length >= max
    ? `Your plan allows up to ${max} keyword${max === 1 ? "" : "s"} per search.` : "Keywords can be at most 100 characters.") : "");
  kwUpdateKeywordUi();
  kwScheduleEstimate();
}
function kwRemoveKeyword(i) {
  KW.keywords.splice(i, 1);
  kwErr("kwKeywordsErr", "");
  kwUpdateKeywordUi();
  kwScheduleEstimate();
  $("kwKeywordInput").focus();
}
function kwUpdateKeywordUi() {
  const list = $("kwChipList");
  if (!list) return;
  list.innerHTML = KW.keywords.map((k, i) => `<li class="kw-chip"><span>${esc(k)}</span><button type="button" class="kw-chip-x" data-kw-remove="${i}" aria-label="Remove keyword ${esc(k)}">×</button></li>`).join("");
  const max = kwCaps().max_keywords || 5;
  const input = $("kwKeywordInput");
  input.disabled = KW.keywords.length >= max;
  input.placeholder = KW.keywords.length >= max ? `Limit of ${max} reached` : KW.keywords.length ? "Add another…" : "e.g. villa rental, goa homes";
  const cnt = $("kwKeywordsCount");
  if (cnt) cnt.textContent = `${KW.keywords.length} of ${max}`;
}

// ── request body ──────────────────────────────────────────────────────────
function kwBody() {
  const pending = $("kwKeywordInput").value.trim();
  const keywords = KW.keywords.slice();
  if (pending && !keywords.some((k) => k.toLowerCase() === pending.toLowerCase())) keywords.push(...pending.split(",").map((k) => k.trim()).filter(Boolean));
  const filters = kwReadFilters("kwf");
  const intOr = (id) => { const v = parseInt($(id).value, 10); return isFinite(v) && v > 0 ? v : undefined; };
  const recent = intOr("kwRecentPosts");
  if (recent) filters.recent_posts_to_analyze = recent;
  const limits = {};
  const mc = intOr("kwMaxCandidates"); if (mc) limits.max_candidates = mc;
  if (recent) limits.recent_posts = recent;
  const cpp = intOr("kwCommentsPerPost"); if (cpp) limits.comments_per_post = cpp;
  const body = { keywords, platforms: kwSelectedPlatforms(), filters, strict: kwReadStrict("kwf"), limits };
  const mt = intOr("kwMaxTokens"); if (mt) body.max_tokens = mt;
  return body;
}

// ── cost preview ──────────────────────────────────────────────────────────
const kwScheduleEstimate = debounce(() => kwEstimate(), 600);

async function kwEstimate() {
  if (KW.mode !== "keyword" || !KW.caps || !kwPlanHas() || !kwCanCreate()) return null;
  const body = kwBody();
  const box = $("kwEstimate");
  kwUpdateStartState();
  if (!body.keywords.length || !body.platforms.length) {
    KW.est = null; KW.estKey = ""; KW.estFresh = false;
    box.innerHTML = `<div class="kw-est-empty">${ico("coins")} Add a keyword and pick a platform to see what the search costs.</div>`;
    return null;
  }
  const key = JSON.stringify(body);
  if (key === KW.estKey && KW.estFresh) return KW.est;
  const seq = ++KW.estSeq;
  box.setAttribute("aria-busy", "true");
  const status = box.querySelector(".kw-est-status");
  if (status) status.textContent = "Updating…";
  else box.innerHTML = `<div class="kw-est-empty">${ico("coins")} Working out the cost…</div>`;
  const res = await api(KW_API + "/estimate", { method: "POST", body });
  if (seq !== KW.estSeq) return null;          // a newer request is on its way
  box.removeAttribute("aria-busy");
  kwClearErrors("kwf");
  if (res.ok) {
    KW.est = res.data; KW.estKey = key; KW.estFresh = true;
    box.innerHTML = kwEstimateHtml(res.data, true);
    kwUpdateStartState();
    return res.data;
  }
  KW.est = null; KW.estKey = ""; KW.estFresh = false;
  const code = errCode(res.data);
  if (res.status === 422) {
    const errs = kwShowErrors("kwf", res.data);
    box.innerHTML = `<div class="inline-alert alert-danger" role="alert"><div><strong>Fix the highlighted ${errs.length === 1 ? "field" : "fields"}</strong><div>${esc(errs.join(" ") || errText(res.data))}</div></div></div>`;
  } else if (kwIsNoPlan(res)) {
    KW.planBlocked = true;
    kwApplyAvailability(false);
    box.innerHTML = "";
  } else if (res.status === 402 || code === "FEATURE_NOT_AVAILABLE") {
    const copy = ENTITLEMENT_COPY[code] || {};
    const detail = Object.assign({ code }, (res.data && res.data.detail) || {});
    box.innerHTML = `<div class="inline-alert alert-warning"><div><strong>${esc(copy.title || "Not available on your plan")}</strong><div>${esc(copy.msg ? copy.msg(detail) : errText(res.data))}</div></div>${upgradeButtonHtml(code)}</div>`;
  } else if (res.status === 403) {
    box.innerHTML = `<div class="inline-alert alert-warning"><div><strong>Switched off</strong><div>${esc(errText(res.data, "Keyword search is switched off by the administrator."))}</div></div></div>`;
    if (code === "platform_disabled" || code === "feature_disabled") kwEnsureCaps(true);
  } else {
    box.innerHTML = `<div class="inline-alert alert-danger"><div><strong>Couldn't estimate the cost</strong><div>${esc(res.networkError ? "You appear to be offline." : errText(res.data, "Please try again."))}</div></div><button type="button" class="btn-secondary btn-sm" data-kw-action="estimate">↻ Retry</button></div>`;
  }
  kwUpdateStartState();
  return null;
}

/** Cost breakdown (preview card, or the compact version in the confirm dialog). */
function kwEstimateHtml(est, full) {
  const t = est.tokens || {};
  const avail = est.tokens_available;
  const rows = KW_TOKEN_STAGES.map(([k, , label, sub]) => `<div class="kw-est-row"><span class="kw-est-label">${esc(label)}${full ? `<span class="kw-est-sub">${esc(sub)}</span>` : ""}</span><span class="kw-est-val">${kwNum(t[k])}</span></div>`).join("");
  const short = avail !== null && avail !== undefined && avail < (t.total_max || 0);
  const cantStart = avail !== null && avail !== undefined && avail < (t.discover || 0);
  const plat = Object.entries(est.per_platform || {}).map(([p, info]) => {
    const bits = [`up to ${kwPlural(info.candidates_max, "profile")}`];
    bits.push(info.posts_from_discovery ? "recent posts may come free with the search" : "recent posts fetched separately");
    if (info.needs_enrichment) bits.push("profile details fetched separately");
    const unsup = (info.unsupported_filters || []).map(kwFilterLabel);
    return `<li><span class="platform-badge ${esc(p)}">${esc(kwPlat(p))}</span> ${esc(bits.join(" · "))}${unsup.length ? `<span class="kw-sup kw-sup-no">can't measure: ${esc(unsup.join(", "))}</span>` : ""}</li>`;
  }).join("");
  const notes = (est.notes || []).filter((n) => full || !/^Worst case/.test(n));
  return `${full ? `<div class="kw-est-head"><h3 class="kw-est-title">${ico("coins")} Cost preview</h3><span class="kw-est-status" aria-hidden="true"></span></div>` : ""}
    <div class="kw-est-grid">${rows}</div>
    <div class="kw-est-total"><span>Worst case <b>${kwNum(t.total_max)} tokens</b></span>
      ${avail !== null && avail !== undefined ? `<span class="${short ? "text-warn" : ""}">You have <b>${kwNum(avail)}</b> left</span>` : ""}
      ${est.apify_usd_estimate != null ? `<span title="Rough Apify usage for this search">≈ $${Number(est.apify_usd_estimate).toFixed(2)} Apify usage</span>` : ""}</div>
    ${cantStart ? `<div class="inline-alert alert-danger kw-est-alert"><div><strong>Not enough tokens to start</strong><div>The keyword searches alone need ${kwNum(t.discover)} tokens.</div></div>${upgradeButtonHtml("TOKENS_EXHAUSTED")}</div>`
      : short ? `<div class="inline-alert alert-warning kw-est-alert"><div>If every profile passes, this search needs more tokens than you have. It stops when they run out and keeps everything found so far; you can continue it later.</div></div>` : ""}
    ${full && plat ? `<ul class="kw-est-plat">${plat}</ul>` : ""}
    ${notes.length ? `<ul class="kw-est-notes">${notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}`;
}

function kwUpdateStartState() {
  const btn = $("kwStartBtn");
  if (!btn || btn.classList.contains("is-loading")) return;
  const body = { keywords: KW.keywords.length || $("kwKeywordInput").value.trim(), platforms: kwSelectedPlatforms().length };
  const blockers = searchBlockers();
  const ready = body.keywords && body.platforms && kwCanCreate() && kwPlanHas() && !blockers.length;
  btn.disabled = !ready;
  btn.setAttribute("aria-disabled", ready ? "false" : "true");
  const hint = $("kwStartHint");
  if (hint) {
    hint.textContent = blockers.length ? alertText(blockers[0])
      : !body.keywords ? "Add at least one keyword."
        : !body.platforms ? "Pick at least one platform." : "You confirm the cost before anything is charged.";
  }
}

// ── start ─────────────────────────────────────────────────────────────────
async function kwStart(e) {
  if (e) e.preventDefault();
  if (KW.starting) return;
  const pending = $("kwKeywordInput").value.trim();
  if (pending) { kwAddKeywords(pending); $("kwKeywordInput").value = ""; }
  kwClearErrors("kwf");
  kwClampAll();
  if (!KW.keywords.length) { kwErr("kwKeywordsErr", "Enter at least one keyword."); $("kwKeywordInput").focus(); return; }
  if (!kwSelectedPlatforms().length) { kwErr("kwPlatformsErr", "Pick at least one platform."); const f = document.querySelector('#kwPlatforms input:not(:disabled)'); if (f) f.focus(); return; }
  const blockers = searchBlockers();
  if (blockers.length) { showQuotaExceededModal(blockers[0]); return; }
  KW.starting = true;
  const btn = $("kwStartBtn");
  const label = btn.querySelector(".btn-text");
  btn.disabled = true; btn.classList.add("is-loading"); btn.setAttribute("aria-busy", "true");
  label.textContent = "Checking cost…";
  try {
    let est = await kwEstimate();
    if (!est) { const f = $("kwForm").querySelector('[aria-invalid="true"]'); if (f) f.focus(); else $("kwEstimate").scrollIntoView({ block: "nearest" }); return; }
    for (let attempt = 0; attempt < 2; attempt++) {
      // not even the keyword searches are covered: skip the cost question, the server explains (402)
      const avail = est.tokens_available;
      const cantStart = avail !== null && avail !== undefined && avail < ((est.tokens || {}).discover || 0);
      if (!cantStart && !(await kwConfirmCost(est, "start"))) return;
      label.textContent = "Starting…";
      const res = await api(KW_API, { method: "POST", body: Object.assign(kwBody(), { confirm: true }) });
      if (res.ok) {
        toast("Keyword search started", "success");
        refreshSummary();
        kwOpenRun(res.data.run_id, { fresh: true, scroll: true });
        renderRecentSearches();
        return;
      }
      const code = errCode(res.data);
      if (res.status === 409 && code === "CONFIRM_REQUIRED" && res.data.detail && res.data.detail.estimate) { est = res.data.detail.estimate; continue; }
      kwHandleError(res, "kwf", "The search couldn't start");
      return;
    }
  } finally {
    KW.starting = false;
    btn.classList.remove("is-loading"); btn.removeAttribute("aria-busy");
    label.textContent = "Find profiles";
    kwUpdateStartState();
  }
}

/** Shared error handling for start / continue / re-apply / presets. */
function kwHandleError(res, prefix, fallback) {
  const code = errCode(res.data);
  if (res.status === 422) {
    const errs = kwShowErrors(prefix, res.data);
    toast(errs[0] || errText(res.data, "Some values are invalid"), "error");
    const f = document.querySelector(prefix === "kwr" ? '#kwReapply [aria-invalid="true"]' : '#kwForm [aria-invalid="true"]');
    if (f) f.focus();
    return;
  }
  if (kwIsNoPlan(res)) { kwPlanBlocked(); return; }
  if (res.status === 503 && code === "start_failed") {
    toast(errText(res.data, "The search could not be started.") + (/nothing was charged/i.test(errText(res.data, "")) ? "" : " Nothing was charged — please try again."), "error");
    return;
  }
  if (res.status === 409 && code === "run_running") {
    toast(KW.run && KW.run.status !== "running" ? "Still stopping — try again in a moment." : errText(res.data, "This search is still running."), "info");
    return;
  }
  if (res.status === 403 && (code === "feature_disabled" || code === "platform_disabled")) {
    toast(errText(res.data, "Switched off by the administrator."), "error");
    kwEnsureCaps(true);
    return;
  }
  if (res.status === 429) {
    toast(code === "cost_limit" ? errText(res.data, "Too many searches are running. Wait for one to finish.") : "Too many requests — wait a moment and try again.", "error");
    return;
  }
  if (handleEntitlementError(res.status, res.data)) return;
  toastError(res.networkError ? "You appear to be offline." : errText(res.data, fallback));
}

/** Confirm dialog with the token breakdown. kind: start | continue. */
function kwConfirmCost(est, kind, extraHtml) {
  const t = est.tokens || {};
  const what = kind === "start"
    ? `Search ${KW.keywords.map((k) => `“${k}”`).join(", ")} on ${kwSelectedPlatforms().map(kwPlat).join(", ")}. Tokens are charged stage by stage as the search runs, never more than this worst case.`
    : "Process the remaining profiles of this search. Tokens are charged stage by stage, never more than this worst case.";
  return confirmAction({
    title: kind === "start" ? "Start this keyword search?" : "Continue this search?",
    message: what,
    details: (kind === "continue" ? kwContinuePlanHtml(est) : "") + kwEstimateHtml(est, false) + (extraHtml || ""),
    confirmLabel: `${kind === "start" ? "Start" : "Continue"} · up to ${kwNum(t.total_max)} tokens`,
  });
}
function kwContinuePlanHtml(est) {
  const bits = [];
  if (est.undiscovered_pairs) bits.push(`${kwPlural(est.undiscovered_pairs, "keyword search")} left to run`);
  if (est.to_profile) bits.push(`${kwPlural(est.to_profile, "profile")} to check`);
  if (est.to_sample) bits.push(`recent posts of ${kwPlural(est.to_sample, "profile")}`);
  if (est.to_collect) bits.push(`comments of up to ${kwPlural(est.to_collect, "profile")}`);
  return bits.length ? `<p class="kw-confirm-plan">${esc(bits.join(" · "))}</p>` : "";
}

// ── one run: progress ─────────────────────────────────────────────────────
async function kwOpenRun(runId, opts = {}) {
  if (!runId) return;
  const token = ++KW.pollToken;
  KW.runId = runId;
  kwStore(KW_RUN_KEY, runId);
  KW.res = { status: KW_DEFAULT_TAB, platform: "", sort: "followers", page: 1, pageSize: 25, counts: null };
  KW.lastCounts = ""; KW.lastResultsAt = 0;
  $("kwReapply").classList.add("hidden");
  $("kwReapplyToggle").setAttribute("aria-expanded", "false");
  $("kwReapplyResult").innerHTML = "";
  const wrap = $("kwRun");
  wrap.classList.remove("hidden");
  if (opts.fresh) { $("kwRunLabel").textContent = "Starting keyword search…"; $("kwStages").innerHTML = ""; $("kwRunAlerts").innerHTML = ""; }
  if (opts.scroll) wrap.scrollIntoView({ behavior: "smooth", block: "start" });
  await kwEnsureCaps();
  let failures = 0;
  let sawRunning = false;
  while (token === KW.pollToken) {
    const res = await api(`${KW_API}/${encodeURIComponent(runId)}`);
    if (token !== KW.pollToken) return;
    if (!res.ok) {
      if (res.status === 404) { toast("That keyword search no longer exists", "error"); kwStore(KW_RUN_KEY, ""); kwCloseRun(); return; }
      if (++failures >= 4) { $("kwRunLabel").textContent = "Lost track of the search status — reopen it from Recent searches."; kwSetSpinner(false); return; }
      await sleep(2500);
      continue;
    }
    failures = 0;
    KW.run = res.data;
    const running = res.data.status === "running";
    sawRunning = sawRunning || running;
    kwRenderRun(res.data, { badge: sawRunning });
    const counts = JSON.stringify(res.data.stage_counts || {});
    if (!running || (counts !== KW.lastCounts && Date.now() - KW.lastResultsAt > 2500)) {
      KW.lastCounts = counts;
      KW.lastResultsAt = Date.now();
      kwLoadCandidates();
    }
    if (!running) { if (sawRunning) kwFinished(res.data); return; }
    await sleep(KW_POLL_MS);
  }
}
function kwCloseRun() {
  KW.pollToken++;
  KW.runId = ""; KW.run = null;
  $("kwRun").classList.add("hidden");
  $("kwResults").classList.add("hidden");
}
function kwSetSpinner(on) { $("kwRunSpinner").classList.toggle("hidden", !on); }

function kwStepState(run, step) {
  const st = (run.stages || {})[step.stage] || "pending";
  if (run.status === "completed" && run.phase === "completed") return "done";
  return { done: "done", running: run.status === "running" ? "active" : "cancelled", failed: "error", stopped: "cancelled" }[st] || "";
}
function kwRenderRun(run, opts = {}) {
  const running = run.status === "running";
  const c = run.stage_counts || {};
  $("kwRunTitle").innerHTML = `${ico("search")} Keyword: ${esc((run.keywords || []).join(", "))} <span class="kw-run-plats">${(run.platforms || []).map((p) => `<span class="platform-badge ${esc(p)}">${esc(kwPlat(p))}</span>`).join("")}</span>`;
  $("kwRunLabel").textContent = run.message || run.error || (running ? "Working…" : run.status);
  const searched = kwSearchedText(run);
  $("kwSearched").textContent = searched;
  $("kwSearched").classList.toggle("hidden", !searched);
  kwSetSpinner(running);
  const pct = run.status !== "running" ? 100 : (KW_PHASE_PCT[run.phase] || 8);
  $("kwRunFill").style.width = pct + "%";
  $("kwRunBar").setAttribute("aria-valuenow", String(pct));
  const steps = KW_STEPS.map((s) => {
    const state = kwStepState(run, s);
    return `<li class="kw-step ${state}" ${state === "active" ? 'aria-current="step"' : ""}><span class="ps-dot" aria-hidden="true"></span><span class="kw-step-body"><span class="kw-step-count">${kwNum(c[s.key] || 0)}</span><span class="kw-step-label">${esc(s.label)}</span></span></li>`;
  }).join("");
  $("kwStages").innerHTML = steps;
  const done = KW_STEPS.filter((s) => kwStepState(run, s) === "done").length;
  $("kwRunStepCount").textContent = running ? `${run.phase === "queued" ? "Queued" : "Step " + Math.min(5, done + 1) + " of 5"}` : "";
  const created = parseDate(run.created_at);
  const end = parseDate(run.completed_at) || new Date();
  if (created) {
    const sec = Math.max(0, Math.floor((end - created) / 1000));
    $("kwRunElapsed").textContent = `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
  }
  // tokens charged per stage
  const tc = run.tokens_charged || {};
  const total = Object.values(tc).reduce((a, b) => a + (Number(b) || 0), 0);
  const est = run.estimate && run.estimate.tokens;
  const parts = KW_TOKEN_STAGES.filter(([, stage]) => tc[stage]).map(([, stage, label]) => `${label.toLowerCase()} ${kwNum(tc[stage])}`);
  $("kwRunMeta").innerHTML = `<span class="cost-chip">${ico("coins")} <b>${kwNum(total)}</b> tokens used${est ? ` of ≤ ${kwNum(est.total_max)}` : ""}</span>`
    + (parts.length ? `<span class="cost-chip">${esc(parts.join(" · "))}</span>` : "")
    + (run.max_tokens ? `<span class="cost-chip">Budget ${kwNum(run.max_tokens)}</span>` : "")
    + (c.rejected ? `<span class="cost-chip">${kwNum(c.rejected)} rejected</span>` : "")
    + (c.errors ? `<span class="cost-chip">${kwNum(c.errors)} errors</span>` : "");
  // platform errors + stop states
  // each platform's problem in plain words: what went wrong, what to do, and the
  // original error folded away for an administrator
  const alerts = Object.entries(run.platform_errors || {}).map(([p, e]) => {
    e = e || {};
    const what = KW_STAGE_WORDS[e.stage] || String(e.stage || "searching").replace(/_/g, " ");
    return `<div class="inline-alert alert-warning"><div><strong>${esc(kwPlat(p))} stopped while ${esc(what)}</strong>`
      + `<div>${esc(e.message || "")}</div>`
      + (e.action ? `<div><strong>What to do:</strong> ${esc(e.action)}</div>` : "")
      + (isOwnKeyError(e.message) ? ownKeyActionHtml(e.message) : "")
      + (e.technical ? `<details class="kw-tech"><summary>Technical details</summary><code>${esc(e.technical)}</code></details>` : "")
      + `</div></div>`;
  });
  const canGo = kwCanCreate();
  if (!running) {
    if (run.phase === "stopped_tokens") {
      alerts.unshift(`<div class="inline-alert alert-warning"><div><strong>Stopped: out of tokens</strong><div>Everything found so far is kept below. Add tokens, then continue the search where it stopped.</div></div>${canGo ? `<button type="button" class="btn-primary btn-sm" data-kw-action="continue">Continue search</button>` : ""}${upgradeButtonHtml("TOKENS_EXHAUSTED")}</div>`);
    } else if (run.phase === "stopped_budget") {
      alerts.unshift(`<div class="inline-alert alert-warning"><div><strong>Stopped at this search's token budget (${kwNum(run.max_tokens)})</strong><div>Everything found so far is kept below. Continue with a higher budget to finish it.</div></div>${canGo ? `<button type="button" class="btn-primary btn-sm" data-kw-action="continue">Continue search</button>` : ""}</div>`);
    } else if (run.status === "cancelled") {
      alerts.unshift(`<div class="inline-alert alert-info"><div><strong>Search cancelled</strong><div>Profiles found before you cancelled are kept below. You can continue it later.</div></div>${canGo ? `<button type="button" class="btn-secondary btn-sm" data-kw-action="continue">Continue search</button>` : ""}</div>`);
    } else if (run.status === "error") {
      const perPlatform = Object.keys(run.platform_errors || {}).length;
      alerts.unshift(`<div class="inline-alert alert-danger"><div><strong>The search couldn't finish</strong><div>${perPlatform
        ? "No platform could complete the search. The reason and what to do are shown below for each platform."
        : esc(run.error || run.message || "")}</div>${isOwnKeyError(run.error) ? ownKeyActionHtml(run.error) : ""}</div></div>`);
    }
  }
  $("kwRunAlerts").innerHTML = alerts.join("");
  const cancel = $("kwCancelBtn");
  cancel.classList.toggle("hidden", !running || !can("search.cancel"));
  if (!running) { cancel.disabled = false; cancel.textContent = "✕ Cancel search"; }
  const collected = (run.stages || {}).deep_scrape === "done";
  $("kwRunActions").innerHTML = running ? "" : [
    c.passed ? `<button type="button" class="btn-primary btn-sm" data-kw-action="open-pages">${collected ? `View ${kwPlural(c.passed, "page")} &amp; leads` : "View collected pages &amp; leads"} →</button>` : "",
    `<button type="button" class="btn-ghost btn-sm" data-kw-action="new-search">New keyword search</button>`,
  ].join(" ");
  $("kwRunActions").classList.toggle("hidden", running);
  $("kwReapplyToggle").disabled = running;
  $("kwReapplyToggle").title = running ? "Available when the search has finished" : "";
  if (opts.badge !== false) setBadge(running ? "Keyword search" : run.status === "completed" ? "Completed" : run.status === "cancelled" ? "Cancelled" : "Error",
    running ? "status-running" : run.status === "completed" ? "status-success" : run.status === "cancelled" ? "status-warn" : "status-error");
}
/* ── what was sent to Apify ── */
const KW_EXTRA_INPUTS = {
  minSubscribers: (v) => `≥ ${kwNum(v)} subscribers`, maxSubscribers: (v) => `≤ ${kwNum(v)} subscribers`,
  minVideos: (v) => `≥ ${kwNum(v)} videos`, verifiedOnly: (v) => (v ? "verified only" : ""), countryHint: (v) => `country ${v}`,
};
function kwQueryText(q) {
  let t = `'${q.query_text || q.keyword}'`;
  if ((q.locations || []).length) t += ` near '${q.locations.join("', '")}'`;
  const extra = Object.entries(q.extra_inputs || {}).map(([k, v]) => (KW_EXTRA_INPUTS[k] ? KW_EXTRA_INPUTS[k](v) : `${k} ${v}`)).filter(Boolean);
  return `${t} (${kwPlat(q.platform)}${extra.length ? ": " + extra.join(", ") : ""})`;
}
/** "Searched Apify for: 'developer job kota' (Instagram), 'developer job' near 'kota, Rajasthan' (Facebook)". */
function kwSearchedText(run) {
  const qs = ((run && run.discovery_queries) || []).filter((q) => q && q.platform);
  if (!qs.length) return "";
  const shown = qs.slice(0, 8).map(kwQueryText);
  return `Searched Apify for: ${shown.join(", ")}${qs.length > 8 ? ` and ${qs.length - 8} more` : ""}`;
}

function kwFinished(run) {
  refreshSummary();
  renderRecentSearches();
  if (run.status === "completed" && run.phase === "completed") toast("Keyword search finished — " + (run.message || "results are ready"), "success");
  else if (run.phase === "stopped_tokens" || run.phase === "stopped_budget") toast("Keyword search stopped — see why above the results", "info");
  else if (run.status === "error" && run.error) toastError(run.error);
}

async function kwCancel() {
  const btn = $("kwCancelBtn");
  btn.disabled = true;
  btn.textContent = "Cancelling…";
  const r = await api(`${KW_API}/${encodeURIComponent(KW.runId)}/cancel`, { method: "POST" });
  if (!r.ok) {
    btn.disabled = false;
    btn.textContent = "✕ Cancel search";
    toast(errText(r.data, "Could not cancel the search"), "error");
    return;
  }
  toast("Cancelling — the search stops at its next checkpoint", "info");
}

/** Continue: ask the server what is left (409 + estimate), confirm, then run it. */
async function kwContinue(btn) {
  if (!KW.runId) return;
  if (btn) btn.disabled = true;
  try {
    const pre = await api(`${KW_API}/${encodeURIComponent(KW.runId)}/continue`, { method: "POST", body: { confirm: false } });
    const code = errCode(pre.data);
    if (pre.ok) { kwOpenRun(KW.runId); return; }   // (never without confirm, but follow it if so)
    if (!(pre.status === 409 && code === "CONFIRM_REQUIRED")) {
      if (pre.status === 409 && code === "nothing_to_continue") { toast("Nothing is left to analyze in this search", "info"); return; }
      if (pre.status === 409 && code === "run_running") { toast(KW.run && KW.run.status !== "running" ? "Still stopping — try again in a moment." : errText(pre.data, "This search is still running."), "info"); return; }
      kwHandleError(pre, "kwr", "Couldn't continue the search");
      return;
    }
    const est = pre.data.detail.estimate || {};
    const budget = KW.run && KW.run.max_tokens;
    const more = Number((est.tokens || {}).total_max || 0);
    // max_tokens on /continue = at most this many MORE tokens; without it the old budget applies (maybe already spent)
    const extra = `<label class="kw-confirm-budget" for="kwContinueBudget">Spend at most this many more tokens${budget ? "" : ' <span class="muted">(optional)</span>'}
        <input type="number" id="kwContinueBudget" class="form-input form-input-sm" min="1" step="1" inputmode="numeric" ${budget ? `value="${esc(more || "")}"` : 'placeholder="no limit"'} aria-describedby="kwContinueBudgetHint">
        <span class="field-hint" id="kwContinueBudgetHint">${budget ? `This search had a budget of ${esc(kwNum(budget))} tokens. Leave this empty to keep that budget, which may already be used up.` : "Leave empty to continue without a limit."}</span></label>`;
    if (!(await kwConfirmCost(est, "continue", extra))) return;
    const body = { confirm: true };
    const b = $("kwContinueBudget");
    if (b && parseInt(b.value, 10) > 0) body.max_tokens = parseInt(b.value, 10);
    const res = await api(`${KW_API}/${encodeURIComponent(KW.runId)}/continue`, { method: "POST", body });
    if (!res.ok) { kwHandleError(res, "kwr", "Couldn't continue the search"); return; }
    toast("Continuing the search", "success");
    refreshSummary();
    $("kwReapply").classList.add("hidden");
    $("kwReapplyToggle").setAttribute("aria-expanded", "false");
    kwOpenRun(KW.runId, { scroll: true });
  } finally {
    if (btn && document.contains(btn)) btn.disabled = false;
  }
}

// ── results ───────────────────────────────────────────────────────────────
async function kwLoadCandidates() {
  if (!KW.runId) return;
  const runId = KW.runId;
  const box = $("kwResults");
  box.classList.remove("hidden");
  const list = $("kwTable");
  const r = KW.res;
  const params = new URLSearchParams({ sort: r.sort, page: String(r.page), page_size: String(r.pageSize) });
  if (r.status) params.set("status", r.status);
  if (r.platform) params.set("platform", r.platform);
  if (!list.children.length) list.innerHTML = skeletonRows(3);
  const res = await api(`${KW_API}/${encodeURIComponent(runId)}/candidates?${params}`);
  if (runId !== KW.runId) return;
  if (!res.ok) {
    RETRY.kwcands = kwLoadCandidates;
    clearTable("kwcands");
    list.innerHTML = failureState(res, "kwcands", "keyword search results");
    $("kwPager").innerHTML = "";
    return;
  }
  const data = res.data;
  r.counts = data.counts || {};
  kwRenderStatusTabs();
  kwRenderPlatformFilter();
  const total = Object.values(r.counts).reduce((a, b) => a + (b || 0), 0);
  const c = r.counts;
  $("kwResultsSummary").textContent = total
    ? `${kwNum(total)} found · ${kwNum(c.passed)} passed · ${kwNum(c.rejected)} rejected${c.needs_sampling ? ` · ${kwNum(c.needs_sampling)} need posts` : ""}${c.error ? ` · ${kwNum(c.error)} errors` : ""}`
    : "";
  kwRenderHiddenNote();
  if (!data.items.length) {
    clearTable("kwcands");
    const running = KW.run && KW.run.status === "running";
    if (running && !total) {
      list.innerHTML = emptyState({ icon: "search", title: "Searching…", sub: "Profiles appear here as soon as they are discovered." });
    } else if (r.status === "passed" && !r.platform && total) {
      list.innerHTML = running
        ? emptyState({ icon: "search", title: "No profile has passed yet", sub: "Profiles that pass every filter appear here as soon as they are checked." })
        : kwZeroPassedHtml();
    } else {
      list.innerHTML = emptyState({ icon: "search", title: r.status || r.platform ? "No profiles match" : "No profiles found", sub: r.status || r.platform ? "Try another tab or platform." : "Try other keywords or fewer filters." });
    }
    renderPager($("kwPager"), null, "kwcands");
    return;
  }
  const filterLabels = (keys) => (keys || []).map(kwFilterLabel);
  renderTable({
    key: "kwcands", el: list, label: "Keyword search profiles", rows: data.items, sort: r.sort,
    onSort: (v) => { r.sort = v; r.page = 1; $("kwSort").value = v; kwLoadCandidates(); },
    columns: [
      { id: "name", label: "Profile", required: true, cls: "col-main", text: (x) => x.name || x.url,
        html: (x) => {
          const url = safeUrl(x.url);
          const name = esc(x.name || (x.profile || {}).handle || x.profile_id || "Profile");
          const handle = (x.profile || {}).handle;
          return `<span class="mini-main"><span class="mini-title">${url ? `<a class="kw-prof-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${name}<span class="sr-only"> (opens in a new tab)</span> ↗</a>` : name}</span>
            <span class="mini-sub">${handle ? "@" + esc(handle) + " · " : ""}${(x.keywords || []).map((k) => `<span class="kw-mini-chip">${esc(k)}</span>`).join(" ")}</span></span>`;
        } },
      { id: "platform", label: "Platform", cls: "col-tag", text: (x) => kwPlat(x.platform), html: (x) => `<span class="platform-badge ${esc(x.platform)}">${esc(kwPlat(x.platform))}</span>` },
      { id: "followers", label: "Followers", cls: "col-num", sort: { desc: "followers" }, text: (x) => (x.metrics || {}).followers, html: (x) => `<span class="cell-mono">${fmt((x.metrics || {}).followers)}</span>` },
      { id: "posts", label: "Posts", cls: "col-num", text: (x) => (x.metrics || {}).posts_count, html: (x) => `<span class="cell-mono">${fmt((x.metrics || {}).posts_count)}</span>` },
      { id: "avg", label: "Avg comments", cls: "col-num", sort: { desc: "avg_comments" }, text: (x) => (x.metrics || {}).avg_comments,
        html: (x) => { const v = (x.metrics || {}).avg_comments; return `<span class="cell-mono">${v === null || v === undefined ? "—" : esc(Number(v).toFixed(v % 1 ? 1 : 0))}</span>`; } },
      { id: "eng", label: "Engagement", cls: "col-num", sort: { desc: "engagement" }, text: (x) => (x.metrics || {}).engagement_rate,
        html: (x) => { const v = (x.metrics || {}).engagement_rate; return `<span class="cell-mono">${v === null || v === undefined ? "—" : esc(Number(v).toFixed(Number(v) < 10 ? 2 : 1)) + "%"}</span>`; } },
      { id: "last", label: "Last post", cls: "col-date", text: (x) => (x.metrics || {}).last_post_at || "",
        html: (x) => { const d = (x.metrics || {}).last_post_at; return d ? `<span class="cell-mono" title="${esc(formatDate(d))}">${esc(relativeTime(d))}</span>` : `<span class="muted">—</span>`; } },
      { id: "status", label: "Result", required: true, cls: "kw-col-why", text: (x) => [(KW_STATUS[x.status] || [0, x.status])[1], kwWhyText(x)].filter(Boolean).join(": "),
        html: (x) => {
          const [cls, label] = KW_STATUS[x.status] || ["neutral", x.status];
          const fails = (x.reasons || []).filter((o) => o.outcome === "fail").map((o) => `<li>${esc(o.message)}</li>`);
          const err = x.status === "error" && x.error ? `<li class="text-danger" title="${esc(x.error_technical || "")}">${esc(x.error)}</li>` : "";
          const deepErr = x.deep_status === "error" && x.status !== "error"
            ? `<li class="text-danger">${esc(x.error || "Comments couldn't be fetched.")}${/continue/i.test(x.error || "") ? "" : " Continue the search to retry."}</li>` : "";
          const ok = x.status === "passed" && !fails.length ? `<li class="kw-why-ok">All filters passed</li>` : "";
          const paid = Object.values(x.charges || {}).reduce((a, b) => a + (Number(b) || 0), 0);
          const cost = paid ? `<li class="muted">${kwNum(paid)} tokens charged</li>` : "";
          const ns = x.status === "needs_sampling" ? `<li class="muted">Profile filters pass; recent posts not analyzed yet</li>` : "";
          const chips = filterLabels(x.unsupported).map((l) => `<span class="kw-sup kw-sup-no" title="This platform can't measure it">Not measurable: ${esc(l)}</span>`)
            .concat(filterLabels(x.unknown).map((l) => `<span class="kw-sup kw-sup-part" title="This profile doesn't show the value">Unknown: ${esc(l)}</span>`)).join("");
          return `<span class="status-pill sp-${cls}">${esc(label)}</span><ul class="kw-why">${fails.join("")}${err}${deepErr}${ok}${ns}${cost}</ul>${chips ? `<span class="kw-sup-row">${chips}</span>` : ""}${kwRowAction(x)}`;
        } },
    ],
  });
  renderPager($("kwPager"), { total: data.total, page: data.page, page_size: data.page_size, total_pages: Math.ceil(data.total / data.page_size) }, "kwcands");
}
/** On the Passed tab: "28 profiles didn't match your filters — Show them". */
function kwRenderHiddenNote() {
  const el = $("kwHiddenNote");
  if (!el) return;
  const c = KW.res.counts || {};
  const parts = [];
  if (KW.res.status === "passed") {
    if (c.rejected) parts.push(`${kwPlural(c.rejected, "profile")} didn't match your filters — <button type="button" class="link-btn" data-kw-show="rejected">Show them</button>`);
    if (c.error) parts.push(`${kwPlural(c.error, "profile")} couldn't be checked — <button type="button" class="link-btn" data-kw-show="error">Show ${Number(c.error) === 1 ? "it" : "them"}</button>`);
    if (c.needs_sampling) parts.push(`${kwPlural(c.needs_sampling, "profile")} still need their posts analyzed — <button type="button" class="link-btn" data-kw-show="needs_sampling">Show them</button>`);
  }
  el.innerHTML = parts.join('<span class="kw-hidden-sep" aria-hidden="true"> · </span>');
  el.classList.toggle("hidden", !parts.length);
}

/** One-click fix for a rejection reason: {label, patch (filters to change), focus, strict}. */
function kwSuggestionFor(item) {
  const f = (KW.run && KW.run.filters) || {};
  const k = item.filter;
  const label = kwFilterLabel(k);
  const lower = label.charAt(0).toLowerCase() + label.slice(1);
  if (item.outcome === "unknown") return { label: "Turn off strict mode", patch: {}, strict: false, focus: "strict" };
  if (k === "location_keyword") {
    const places = String(f.location_keyword || "").split(/[,;]/).map((x) => x.trim()).filter(Boolean);
    if (places.length > 1 && f.location_match !== "any") return { label: "Use 'any' location match", patch: { location_match: "any" }, focus: "location_match" };
    return { label: "Remove the location filter", patch: { location_keyword: "" }, focus: "location_keyword" };
  }
  if (k === "category_keyword") return { label: "Remove the business type filter", patch: { category_keyword: "" }, focus: "category_keyword" };
  if (k === "exclude_keywords") return { label: "Edit the excluded words", patch: {}, focus: "exclude_keywords" };
  const t = Number(f[k]);
  if (["min_followers", "min_posts", "min_avg_comments", "min_total_comments", "min_engagement_rate"].includes(k) && isFinite(t)) {
    const half = k === "min_avg_comments" || k === "min_engagement_rate" ? Math.round(t * 5) / 10 : Math.floor(t / 2);
    return { label: `Lower ${lower}`, patch: { [k]: half > 0 ? half : "" }, focus: k };
  }
  if (k === "max_followers" && isFinite(t)) return { label: "Raise maximum followers", patch: { max_followers: t * 2 }, focus: k };
  if (k === "active_within_days" && isFinite(t)) return { label: "Allow older posts", patch: { active_within_days: Math.min(36500, t * 2) }, focus: k };
  if (["business_account_only", "verified_only", "has_contact_info"].includes(k)) return { label: `Turn off “${label}”`, patch: { [k]: false }, focus: k };
  return null;
}

/** Finished with no passing profile: why, what was searched, and one-click fixes. */
function kwZeroPassedHtml() {
  const run = KW.run || {};
  const c = KW.res.counts || {};
  const summary = run.rejection_summary || [];
  KW.suggestions = [];
  const items = summary.map((s) => {
    const sug = kwCanCreate() ? kwSuggestionFor(s) : null;
    let btn = "";
    if (sug) { KW.suggestions.push(sug); btn = ` <button type="button" class="link-btn kw-suggest" data-kw-suggest="${KW.suggestions.length - 1}">${esc(sug.label)}</button>`; }
    return `<li><b class="kw-zero-n">${kwNum(s.count)}</b> — ${esc(s.message)}${btn}</li>`;
  }).join("");
  const searched = kwSearchedText(run);
  const rejected = Number(c.rejected || 0);
  return `<div class="kw-zero" role="status">
      <div class="empty-icon" aria-hidden="true">${ico("search", "ico-empty")}</div>
      <h3 class="kw-zero-title">No profile matched all your filters</h3>
      <p class="kw-zero-sub">${rejected ? `${kwPlural(rejected, "profile was", "profiles were")} found but rejected.` : "No profile could be checked against every filter."} ${kwCanCreate() ? "Change a filter below and re-apply it — that's free, nothing is scraped again." : ""}</p>
      ${items ? `<ul class="kw-zero-list" aria-label="Why profiles were rejected">${items}</ul>` : ""}
      ${searched ? `<p class="kw-zero-searched">${esc(searched)}</p>` : ""}
      <div class="kw-zero-actions">${rejected ? `<button type="button" class="btn-secondary btn-sm" data-kw-show="rejected">Show the ${kwPlural(rejected, "rejected profile")}</button>` : ""}
      ${kwCanCreate() ? `<button type="button" class="btn-ghost btn-sm" data-kw-suggest="open">Re-run with different filters</button>` : ""}</div>
    </div>`;
}

/** Open the re-apply panel prefilled with a suggestion (nothing is applied until the user confirms). */
function kwOpenReapplyWith(sug) {
  if (!KW.run) return;
  const panel = $("kwReapply");
  const filters = Object.assign({}, KW.run.filters || {}, (sug && sug.patch) || {});
  const strict = sug && sug.strict === false ? false : KW.run.strict;
  panel.classList.remove("hidden");
  $("kwReapplyToggle").setAttribute("aria-expanded", "true");
  kwRenderFilterFields("kwr", $("kwReapplyFields"), filters, strict);
  $("kwReapplyResult").innerHTML = sug ? `<div class="inline-alert alert-info"><div><strong>Suggested change filled in: ${esc(sug.label.toLowerCase())}</strong><div>Review it, then re-apply. Re-applying is free.</div></div></div>` : "";
  const target = sug && $(sug.focus === "strict" ? "kwr_strict" : `kwr_${sug.focus}`);
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
  (target || panel.querySelector("input, select")).focus({ preventScroll: true });
}

/** Next step for a row (inside the Result cell, so the table fits without an actions column). */
function kwRowAction(x) {
  let h = "";
  if (x.status === "passed" && x.page_ref) {
    h = `<button type="button" class="btn-ghost btn-sm" data-kw-open-page="${esc(x.page_ref)}" aria-label="Open the posts and leads of ${esc(x.name || "this profile")}">Open page</button>`;
  } else if (x.status === "passed" && x.deep_status === "error" && kwCanCreate() && !(KW.run && KW.run.status === "running")) {
    h = `<button type="button" class="btn-ghost btn-sm" data-kw-action="continue" title="Continue the search to fetch the comments again">Retry comments</button>`;
  } else if (x.status === "passed") {
    h = `<span class="muted kw-note">${x.deep_status === "error" ? "Comments failed" : KW.run && KW.run.status === "running" ? "Collecting…" : "Not collected yet"}</span>`;
  }
  return h ? `<div class="kw-row-action">${h}</div>` : "";
}
function kwWhyText(x) {
  const fails = (x.reasons || []).filter((o) => o.outcome === "fail").map((o) => o.message);
  if (x.status === "error" && x.error) fails.push(x.error);
  if (x.unsupported && x.unsupported.length) fails.push("not measurable: " + x.unsupported.map(kwFilterLabel).join(", "));
  if (x.unknown && x.unknown.length) fails.push("unknown: " + x.unknown.map(kwFilterLabel).join(", "));
  return fails.join("; ");
}
function kwRenderStatusTabs() {
  const c = KW.res.counts || {};
  const all = Object.values(c).reduce((a, b) => a + (b || 0), 0);
  $("kwStatusTabs").innerHTML = KW_TABS.map(([v, label]) => {
    const n = v ? (c[v] || 0) : all;
    const on = KW.res.status === v;
    return `<button type="button" role="tab" class="seg-tab${on ? " active" : ""}" data-kw-status="${v}" aria-selected="${on}" tabindex="${on ? 0 : -1}" aria-controls="kwTable">${esc(label)} <span class="seg-count">${kwNum(n)}</span></button>`;
  }).join("");
}
function kwRenderPlatformFilter() {
  const sel = $("kwPlatformFilter");
  const plats = (KW.run && KW.run.platforms) || [];
  const want = ["", ...plats].join(",");
  if (sel.dataset.opts !== want) {
    sel.innerHTML = `<option value="">All platforms</option>` + plats.map((p) => `<option value="${esc(p)}">${esc(kwPlat(p))}</option>`).join("");
    sel.dataset.opts = want;
  }
  sel.value = KW.res.platform;
  $("kwPlatformFilterWrap").classList.toggle("hidden", plats.length < 2);
}
PAGERS.kwcands = (p) => { KW.res.page = p; kwLoadCandidates(); $("kwResults").scrollIntoView({ block: "start" }); };

function kwOpenPages() {
  if (!KW.runId) return;
  if (memory.runId !== KW.runId) { saveMemory("pageId", ""); saveMemory("postId", ""); }
  saveMemory("runId", KW.runId);
  navigateToView("pages", { userInitiated: true });
}
function kwOpenPage(pageId) {
  if (memory.runId !== KW.runId) { saveMemory("pageId", ""); saveMemory("postId", ""); }
  saveMemory("runId", KW.runId);
  openPage(pageId);
}

// ── re-apply with different filters ───────────────────────────────────────
function kwToggleReapply() {
  const panel = $("kwReapply");
  const open = panel.classList.contains("hidden");
  panel.classList.toggle("hidden", !open);
  $("kwReapplyToggle").setAttribute("aria-expanded", open ? "true" : "false");
  if (!open || !KW.run) return;
  kwRenderFilterFields("kwr", $("kwReapplyFields"), KW.run.filters || {}, KW.run.strict);
  $("kwReapplyResult").innerHTML = "";
  const first = panel.querySelector("input");
  if (first) first.focus();
}
async function kwReapply(e) {
  if (e) e.preventDefault();
  if (!KW.runId) return;
  kwClearErrors("kwr");
  const btn = $("kwReapplyBtn");
  btn.disabled = true; btn.setAttribute("aria-busy", "true");
  const filters = kwReadFilters("kwr");
  if (!filters.recent_posts_to_analyze && KW.run && KW.run.filters) filters.recent_posts_to_analyze = KW.run.filters.recent_posts_to_analyze;
  const res = await api(`${KW_API}/${encodeURIComponent(KW.runId)}/reapply`, { method: "POST", body: { filters, strict: kwReadStrict("kwr") } });
  btn.disabled = false; btn.removeAttribute("aria-busy");
  const out = $("kwReapplyResult");
  if (!res.ok) {
    if (res.status === 409) {
      const stopping = errCode(res.data) === "run_running" && KW.run && KW.run.status !== "running";
      out.innerHTML = `<div class="inline-alert alert-warning"><div>${esc(stopping ? "Still stopping — try again in a moment." : errText(res.data, "Wait for the search to finish first."))}</div></div>`;
      return;
    }
    kwHandleError(res, "kwr", "Couldn't re-apply the filters");
    return;
  }
  const d = res.data;
  const c = d.counts || {};
  KW.run = Object.assign({}, KW.run, { filters, strict: kwReadStrict("kwr") });
  const cost = (d.continue_cost || {}).total_max || 0;
  let more = "";
  if (cost > 0 && kwCanCreate()) {
    // what /continue would process (free preview: 409 with the plan)
    const pre = await api(`${KW_API}/${encodeURIComponent(KW.runId)}/continue`, { method: "POST", body: { confirm: false } });
    const est = pre.status === 409 && pre.data.detail && pre.data.detail.estimate;
    const n = est ? (est.to_collect || est.to_sample || 0) : ((c.needs_sampling || 0) + (c.pending || 0));
    if (n) more = `<button type="button" class="btn-primary btn-sm" data-kw-action="continue">Fetch ${kwPlural(n, "more profile")} (≈${kwNum(cost)} tokens)</button>`;
  }
  out.innerHTML = `<div class="inline-alert alert-info"><div><strong>${d.changed ? `${kwPlural(d.changed, "profile")} changed status` : "No profile changed status"}</strong>
      <div>Now ${kwNum(c.passed)} passed · ${kwNum(c.rejected)} rejected${c.needs_sampling ? ` · ${kwNum(c.needs_sampling)} need their recent posts analyzed` : ""}${c.error ? ` · ${kwNum(c.error)} errors` : ""}. Re-applying is free: nothing was scraped.</div>
      ${(d.notes || []).map((n) => `<div class="field-hint">${esc(n)}</div>`).join("")}</div>${more}</div>`;
  KW.res.page = 1;
  // the run first (its rejection summary feeds the "no profile passed" state), then the list
  const st = await api(`${KW_API}/${encodeURIComponent(KW.runId)}`);
  if (st.ok) { KW.run = st.data; kwRenderRun(st.data, { badge: false }); }
  kwLoadCandidates();
}

// ── presets ───────────────────────────────────────────────────────────────
async function kwLoadPresets(selectId) {
  if (!kwCanCreate()) return;
  const res = await api(KW_API + "/presets");
  if (!res.ok) return;
  KW.presets = res.data.presets || [];
  const sel = $("kwPresetSelect");
  sel.innerHTML = `<option value="">${KW.presets.length ? "Choose a saved preset…" : "No saved presets yet"}</option>`
    + KW.presets.map((p) => `<option value="${esc(p.id)}">${esc(p.name)}${p.is_shared ? " (shared)" : ""}</option>`).join("");
  sel.value = selectId || "";
  $("kwPresetDelete").classList.toggle("hidden", !sel.value);
}
function kwApplyPreset(id) {
  const p = KW.presets.find((x) => x.id === id);
  $("kwPresetDelete").classList.toggle("hidden", !p);
  if (!p) return;
  KW.keywords = [];
  kwAddKeywords((p.keywords || []).join(","));
  const plats = new Set(p.platforms || []);
  document.querySelectorAll('#kwPlatforms input[name="kwPlatform"]').forEach((i) => { i.checked = !i.disabled && plats.has(i.value); });
  kwFillFilters("kwf", p.filters || {}, p.strict);
  const lim = p.limits || {};
  if (lim.max_candidates) $("kwMaxCandidates").value = lim.max_candidates;
  const rp = lim.recent_posts || (p.filters || {}).recent_posts_to_analyze;
  if (rp) $("kwRecentPosts").value = rp;
  if (lim.comments_per_post) $("kwCommentsPerPost").value = lim.comments_per_post;
  kwClampAll();
  kwRefreshSupport("kwf");
  kwUpdateFiltersSummary();
  kwScheduleEstimate();
  const off = (p.platforms || []).filter((x) => !(KW.caps.platform_enabled || {})[x]);
  toast(`Loaded preset “${p.name}”${off.length ? ` — ${off.map(kwPlat).join(", ")} ${off.length === 1 ? "is" : "are"} switched off` : ""}`, "info");
}
function kwPresetForm(open) {
  const f = $("kwPresetForm");
  f.classList.toggle("hidden", !open);
  $("kwPresetSave").setAttribute("aria-expanded", open ? "true" : "false");
  kwErr("kwPresetNameErr", "");
  if (open) {
    const cur = KW.presets.find((x) => x.id === $("kwPresetSelect").value);
    $("kwPresetName").value = cur ? cur.name : (KW.keywords[0] ? `Keyword: ${KW.keywords.slice(0, 2).join(", ")}` : "");
    $("kwPresetName").focus();
    $("kwPresetName").select();
  } else {
    $("kwPresetSave").focus();
  }
}
async function kwSavePreset(e) {
  e.preventDefault();
  const name = $("kwPresetName").value.trim();
  if (!name) { kwErr("kwPresetNameErr", "Give the preset a name."); $("kwPresetName").focus(); return; }
  const b = kwBody();
  const btn = $("kwPresetSubmit");
  btn.disabled = true;
  const res = await api(KW_API + "/presets", { method: "POST", body: {
    name, keywords: b.keywords, platforms: b.platforms, filters: b.filters, strict: b.strict, limits: b.limits,
    is_shared: $("kwPresetShared").checked } });
  btn.disabled = false;
  if (!res.ok) {
    if (res.status === 422) { const errs = kwShowErrors("kwf", res.data); kwErr("kwPresetNameErr", errs.join(" ") || errText(res.data)); return; }
    kwHandleError(res, "kwf", "Couldn't save the preset");
    return;
  }
  toast(`Preset “${name}” saved`, "success");
  kwPresetForm(false);
  await kwLoadPresets(res.data.preset && res.data.preset.id);
}
async function kwDeletePreset() {
  const id = $("kwPresetSelect").value;
  const p = KW.presets.find((x) => x.id === id);
  if (!p) return;
  if (!(await confirmAction({ title: "Delete this preset?", message: `“${p.name}” is removed for everyone it was shared with. Searches you already ran are kept.`, confirmLabel: "Delete preset", danger: true }))) return;
  const res = await api(`${KW_API}/presets/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!res.ok) { toast(errText(res.data, "Couldn't delete the preset"), "error"); return; }
  toast("Preset deleted", "success");
  await kwLoadPresets("");
  $("kwPresetSelect").focus();
}

// ── reset / new search ────────────────────────────────────────────────────
function kwResetForm() {
  KW.keywords = [];
  $("kwKeywordInput").value = "";
  kwUpdateKeywordUi();
  if (KW.caps) {
    kwFillFilters("kwf", KW.caps.default_filters || {}, false);
    kwFillLimits();
  }
  $("kwPresetSelect").value = "";
  $("kwPresetDelete").classList.add("hidden");
  kwClearErrors("kwf");
  kwUpdateFiltersSummary();
  kwScheduleEstimate();
}

// ── events ────────────────────────────────────────────────────────────────
function kwBind() {
  const tabs = $("searchModeTabs");
  tabs.addEventListener("click", (e) => {
    const t = e.target.closest("[data-search-mode]");
    if (t) kwSetMode(t.dataset.searchMode);
  });
  tabs.addEventListener("keydown", (e) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    const vis = Array.from(tabs.querySelectorAll("[data-search-mode]")).filter((t) => !t.classList.contains("hidden"));
    const i = vis.indexOf(document.activeElement);
    const n = e.key === "Home" ? 0 : e.key === "End" ? vis.length - 1 : (i + (e.key === "ArrowRight" ? 1 : -1) + vis.length) % vis.length;
    e.preventDefault();
    kwSetMode(vis[n].dataset.searchMode, { focus: true });
  });

  const input = $("kwKeywordInput");
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      if (input.value.trim()) { kwAddKeywords(input.value); input.value = ""; }
    } else if (e.key === "Backspace" && !input.value && KW.keywords.length) {
      kwRemoveKeyword(KW.keywords.length - 1);
    }
  });
  input.addEventListener("paste", (e) => {
    const text = (e.clipboardData || window.clipboardData).getData("text");
    if (/[,\n]/.test(text)) { e.preventDefault(); kwAddKeywords(input.value + "," + text); input.value = ""; }
  });
  input.addEventListener("blur", () => { if (input.value.trim()) { kwAddKeywords(input.value); input.value = ""; } });
  input.addEventListener("input", () => { kwErr("kwKeywordsErr", ""); kwUpdateStartState(); });
  $("kwChipBox").addEventListener("click", (e) => {
    const x = e.target.closest("[data-kw-remove]");
    if (x) { kwRemoveKeyword(parseInt(x.dataset.kwRemove, 10)); return; }
    if (e.target === e.currentTarget || e.target.id === "kwChipList") input.focus();
  });

  const form = $("kwForm");
  form.addEventListener("submit", kwStart);
  form.addEventListener("input", (e) => {
    if (e.target === input) return;
    if (e.target.matches("[data-kw-filter], [data-kw-strict]")) kwUpdateFiltersSummary();
    if (e.target.getAttribute("aria-invalid")) { e.target.removeAttribute("aria-invalid"); const er = $(e.target.id + "_err") || $(e.target.id + "Err"); if (er) er.textContent = ""; }
    kwScheduleEstimate();
  });
  form.addEventListener("change", (e) => {
    if (e.target.name === "kwPlatform") { kwErr("kwPlatformsErr", ""); kwRefreshSupport("kwf"); kwUpdateStartState(); }
    if (e.target.matches("#kwMaxCandidates, #kwRecentPosts, #kwCommentsPerPost")) kwClampAll();
    kwUpdateFiltersSummary();
    kwScheduleEstimate();
  });

  $("kwPresetSelect").addEventListener("change", (e) => kwApplyPreset(e.target.value));
  $("kwPresetSave").addEventListener("click", () => kwPresetForm($("kwPresetForm").classList.contains("hidden")));
  $("kwPresetCancel").addEventListener("click", () => kwPresetForm(false));
  $("kwPresetForm").addEventListener("submit", kwSavePreset);
  $("kwPresetForm").addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); kwPresetForm(false); } });
  $("kwPresetDelete").addEventListener("click", kwDeletePreset);
  $("kwResetBtn").addEventListener("click", kwResetForm);

  $("kwCancelBtn").addEventListener("click", kwCancel);
  $("kwReapplyToggle").addEventListener("click", kwToggleReapply);
  $("kwReapply").addEventListener("submit", kwReapply);
  $("kwReapplyCancel").addEventListener("click", () => { kwToggleReapply(); $("kwReapplyToggle").focus(); });
  $("kwStatusTabs").addEventListener("click", (e) => {
    const t = e.target.closest("[data-kw-status]");
    if (!t) return;
    KW.res.status = t.dataset.kwStatus; KW.res.page = 1;
    kwRenderStatusTabs();
    kwLoadCandidates();
  });
  $("kwStatusTabs").addEventListener("keydown", (e) => {
    if (!["ArrowLeft", "ArrowRight"].includes(e.key)) return;
    const all = Array.from($("kwStatusTabs").querySelectorAll("[data-kw-status]"));
    const i = all.indexOf(document.activeElement);
    if (i < 0) return;
    const n = all[(i + (e.key === "ArrowRight" ? 1 : -1) + all.length) % all.length];
    e.preventDefault();
    KW.res.status = n.dataset.kwStatus; KW.res.page = 1;
    kwRenderStatusTabs();
    kwLoadCandidates();
    const again = $("kwStatusTabs").querySelector(`[data-kw-status="${n.dataset.kwStatus}"]`);
    if (again) again.focus();
  });
  $("kwPlatformFilter").addEventListener("change", (e) => { KW.res.platform = e.target.value; KW.res.page = 1; kwLoadCandidates(); });
  $("kwSort").addEventListener("change", (e) => { KW.res.sort = e.target.value; KW.res.page = 1; kwLoadCandidates(); });

  $("searchPanelKeyword").addEventListener("click", (e) => {
    const op = e.target.closest("[data-kw-open-page]");
    if (op) { kwOpenPage(op.dataset.kwOpenPage); return; }
    const show = e.target.closest("[data-kw-show]");
    if (show) {
      KW.res.status = show.dataset.kwShow; KW.res.page = 1;
      kwRenderStatusTabs();
      kwLoadCandidates();
      const tab = $("kwStatusTabs").querySelector(`[data-kw-status="${show.dataset.kwShow}"]`);
      if (tab) tab.focus();
      return;
    }
    const sg = e.target.closest("[data-kw-suggest]");
    if (sg) { kwOpenReapplyWith(sg.dataset.kwSuggest === "open" ? null : KW.suggestions[Number(sg.dataset.kwSuggest)]); return; }
    const a = e.target.closest("[data-kw-action]");
    if (!a) return;
    const act = a.dataset.kwAction;
    if (act === "continue") kwContinue(a);
    else if (act === "open-pages") kwOpenPages();
    else if (act === "estimate") { KW.estFresh = false; kwEstimate(); }
    else if (act === "reload-caps") kwEnsureCaps(true);
    else if (act === "new-search") {
      kwStore(KW_RUN_KEY, "");
      kwCloseRun();
      setBadge("Idle", "status-idle");
      $("kwCard").scrollIntoView({ behavior: "smooth", block: "start" });
      $("kwKeywordInput").focus({ preventScroll: true });
    }
  });
}

// ── public ────────────────────────────────────────────────────────────────
/** After sign-in: bind once, restore the remembered tab, re-attach a running search. */
async function kwBoot() {
  if (KW.booted || !$("searchPanelKeyword")) return;
  KW.booted = true;
  kwBind();
  kwRestoreMode();
  if (window.AppConfig && AppConfig.load) {
    try { await AppConfig.load(); } catch (_) { /* public config optional */ }
  }
  await kwEnsureCaps();
  kwLoadPresets();
  const last = kwStore(KW_RUN_KEY);
  if (last) {
    const res = await api(`${KW_API}/${encodeURIComponent(last)}`);
    if (res.ok && res.data.status === "running") kwOpenRun(last);
    else if (res.ok && KW.mode === "keyword") kwOpenRun(last);
    else if (!res.ok && res.status === 404) kwStore(KW_RUN_KEY, "");
  }
}
function kwOnShow() {
  if (!KW.booted) { kwBoot(); return; }
  kwEnsureCaps().then(() => { if (KW.mode === "keyword") { kwUpdateStartState(); kwScheduleEstimate(); } });
}
/** Reopen a keyword run (Recent searches, Search history). */
function kwOpen(runId) {
  if (currentView !== "search") navigateToView("search", { userInitiated: true });
  kwSetMode("keyword", { remember: false });
  kwOpenRun(runId, { scroll: true });
}

window.KeywordSearch = {
  boot: kwBoot,
  onShow: kwOnShow,
  setMode: kwSetMode,
  restoreMode: kwRestoreMode,
  open: kwOpen,
  mode: () => KW.mode,
  available: () => kwFeatureOn() && KW.capsError !== "denied",
  runId: () => KW.runId,
};
