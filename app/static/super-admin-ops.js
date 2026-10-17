/* ============================================================
   LeadAI — Super Admin › Operations (merged from the old /admin console)
   Operations overview (staff landing page), Jobs (all runs, failed jobs with
   retry, job detail, by organization), Leads (detail drawer: notes, follow-ups,
   status, priority, assignment, delete; bulk actions), Platforms (+ detail),
   Pages, Posts, Comments (Contact Intelligence) and Exports.
   Backend: /api/admin/* — viewer reads, manager writes, Super Admin deletes
   runs. The server checks every call; the UI only hides what a role can't use.
   Registered through window.SAKit (same UI kit as super-admin-partners.js).
   ============================================================ */
(function () {
  'use strict';
  var K = window.SAKit;
  if (!K) return;
  var api = K.api, qs = K.qs, esc = K.esc, pill = K.pill, badge = K.badge, fmtN = K.fmtN, $ = K.$, $$ = K.$$;
  var A = '/api/admin';

  (function () {
    var st = document.createElement('style');
    st.textContent = [
      '.ops-dash{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}',
      '.ops-dash>*{min-width:0}.ops-dash>.ops-full{grid-column:1/-1}',
      '@media (max-width:900px){.ops-dash{grid-template-columns:minmax(0,1fr)}}',
      '.ops-spark{display:block;width:100%;height:26px;margin-top:8px;color:var(--primary)}',
      '.ops-spark polyline{fill:none;stroke:currentColor;stroke-width:1.6;vector-effect:non-scaling-stroke}',
      '.ops-spark path{fill:currentColor;opacity:.12}',
      '.ops-delta{font-size:11.5px;font-weight:650;white-space:nowrap}',
      '.ops-delta.up{color:var(--success-text)}.ops-delta.down{color:var(--danger-text)}.ops-delta.flat{color:var(--sa-muted)}',
      '.ops-range{display:flex;gap:6px;flex-wrap:wrap;align-items:center}',
      '.ops-range .form-input{width:auto;min-width:0;padding:6px 9px;font-size:12.5px}',
      '.ops-health{display:flex;gap:8px 14px;flex-wrap:wrap;align-items:center;font-size:12.5px;padding:8px 10px;border:1px solid var(--border-subtle);border-radius:10px;background:var(--surface);margin-top:12px}',
      '.ops-actor{display:flex;gap:6px;align-items:center;margin-bottom:4px;flex-wrap:wrap}',
      '.ops-actor .form-input{flex:1 1 160px;min-width:0;padding:6px 9px;font-size:12.5px}',
      '.ops-actor .sa-mono{flex:1 1 160px;min-width:0;overflow-wrap:anywhere}',
      '.ops-list{list-style:none;margin:0;padding:0;display:grid;gap:8px}',
      '.ops-list li{border:1px solid var(--border-subtle);border-radius:10px;padding:8px 10px;background:var(--surface);font-size:13px;min-width:0}',
      '.ops-list .meta{display:flex;gap:6px;align-items:center;flex-wrap:wrap;font-size:11.5px;color:var(--sa-muted)}',
      '.ops-list .txt{white-space:pre-wrap;overflow-wrap:anywhere;margin-top:4px}',
      '.ops-inline{display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end;margin-top:8px}',
      '.ops-inline .form-input,.ops-inline .form-textarea,.ops-inline .form-select{flex:1 1 180px;min-width:0;width:auto}',
      '.ops-contact{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:6px}',
      '.ops-contact .sa-mono{overflow-wrap:anywhere}',
      '.ops-h{margin:18px 0 8px;font-size:13px;font-weight:700;display:flex;align-items:center;gap:8px;flex-wrap:wrap}',
      '.ops-clip{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;min-width:140px;max-width:380px;white-space:normal;overflow-wrap:anywhere}',
      '.ops-quote{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;line-height:1.55;background:var(--surface);border:1px solid var(--border-subtle);border-radius:10px;padding:10px 12px;margin:0 0 12px}',
      '.ops-btns{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}',
      '.ops-barlist a.lbl{color:var(--text-link);text-decoration:none}.ops-barlist a.lbl:hover{text-decoration:underline}',
      '.ops-table tr[data-run]{cursor:pointer}',
      '.ops-banner{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:10px 12px;border:1px solid var(--border);border-radius:12px;background:var(--surface);font-size:13px;margin-bottom:12px}',
      '.ops-banner .sa-spacer{min-width:8px}',
      '.ops-kpis4{grid-template-columns:repeat(auto-fill,minmax(110px,1fr))}',
      '.ops-form{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;align-items:start}',
      '@media (max-width:900px){.ops-form{grid-template-columns:minmax(0,1fr)}}',
      '.ops-field-off{opacity:.55}'
    ].join('\n');
    document.head.appendChild(st);
  })();

  // ── constants ──────────────────────────────────────────
  var PLATFORMS = ['facebook', 'instagram', 'linkedin', 'youtube'];
  var PLAT_NAME = { facebook: 'Facebook', instagram: 'Instagram', linkedin: 'LinkedIn', youtube: 'YouTube' };
  var PLAT_OPTS = [['', 'All platforms']].concat(PLATFORMS.map(function (p) { return [p, PLAT_NAME[p]]; }));
  var JOB_STATUS = [['', 'All statuses'], ['running', 'Running'], ['completed', 'Completed'], ['error', 'Failed'], ['cancelled', 'Cancelled'], ['queued', 'Queued']];
  // mirrors app/pipeline/lead_lifecycle.py (the backend enforces transitions)
  var LEAD_STATUSES = ['new', 'contacted', 'qualified', 'follow_up', 'converted', 'lost', 'disqualified', 'archived'];
  var LEAD_TRANSITIONS = {
    new: ['contacted', 'qualified', 'follow_up', 'disqualified', 'lost', 'archived'],
    contacted: ['qualified', 'follow_up', 'lost', 'archived'],
    qualified: ['follow_up', 'converted', 'lost', 'archived'],
    follow_up: ['contacted', 'qualified', 'converted', 'lost', 'archived'],
    converted: ['archived'], lost: ['archived'], disqualified: ['archived'], archived: []
  };
  var LEAD_LABEL = { new: 'New', contacted: 'Contacted', qualified: 'Qualified', follow_up: 'Follow-up', converted: 'Converted', lost: 'Lost', disqualified: 'Disqualified', archived: 'Archived' };
  var LEAD_TONE = { new: 'info', contacted: 'primary', qualified: 'primary', follow_up: 'warning', converted: 'success', lost: 'neutral', disqualified: 'neutral', archived: 'neutral' };
  var LEAD_STATUS_OPTS = [['', 'Any status']].concat(LEAD_STATUSES.map(function (s) { return [s, LEAD_LABEL[s]]; }));
  var QUALITY_TONE = { hot: 'danger', high: 'danger', warm: 'warning', medium: 'warning', cold: 'info', low: 'info' };
  var PRIORITY_OPTS = [['high', 'High'], ['medium', 'Medium'], ['low', 'Low']];
  var CI_TOGGLES = [
    ['ci.detect_phone', 'Phone', 'Extract phone numbers.'],
    ['ci.detect_email', 'Email', 'Extract email addresses.'],
    ['ci.detect_budget', 'Budget', 'Detect budget mentions.'],
    ['ci.detect_location', 'Location', 'Detect location mentions.'],
    ['ci.detect_urgency', 'Urgency', 'Detect urgency.'],
    ['ci.detect_buying_intent', 'Buying intent', 'Detect buying intent.'],
    ['ci.detect_selling_intent', 'Selling intent', 'Detect selling intent (off by default to preserve lead filtering).'],
    ['ci.ignore_emoji_only', 'Ignore emoji-only', 'Skip comments that are only emoji.'],
    ['ci.ignore_spam', 'Ignore spam', 'Skip spam-looking comments.'],
    ['ci.ignore_low_value', 'Ignore low value', 'Skip low-value comments.']
  ];
  // export scopes and the filters GET /api/admin/export/{scope}.csv applies to each one
  var EXPORTS = [
    { scope: 'jobs', label: 'Jobs', hint: 'Search runs: run id, query, platform, status, phase, message, pages and timestamps.', f: ['platform', 'q', 'from', 'to'] },
    { scope: 'pages', label: 'Pages', hint: 'Collected pages, sorted by followers.', f: ['platform'] },
    { scope: 'posts', label: 'Posts', hint: 'Collected posts, sorted by publish date.', f: ['platform'] },
    { scope: 'leads', label: 'Leads', hint: 'AI-analysed comments, sorted by score, with status, priority, assignee and any phone or email found.', f: ['platform', 'q', 'from', 'to', 'only_leads'] },
    { scope: 'follow-ups', label: 'Follow-ups', hint: 'Every follow-up task across leads: due date, status and note.', f: ['platform', 'from', 'to'] }
  ];
  var MINE = { operations: 1, jobs: 1, failed: 1, leads: 1, platforms: 1, pages: 1, posts: 1, ci: 1, exports: 1 };

  // ── helpers ────────────────────────────────────────────
  function canM() { return K.can('manager'); }
  function canS() { return K.can('super_admin'); }
  function enc(v) { return encodeURIComponent(String(v == null ? '' : v)); }
  function off(p) { return (Math.max(1, p.page || 1) - 1) * (p.limit || 25); }
  function dayStart(d) { return d ? d + 'T00:00:00' : ''; }
  function dayEnd(d) { return d ? d + 'T23:59:59' : ''; }
  function platName(p) { return p ? (PLAT_NAME[p] || K.titleCase(p)) : '—'; }
  function isLive(j) { return j && (j.status === 'running' || j.status === 'queued'); }
  function safeUrl(u) { u = String(u || ''); return /^https?:\/\//i.test(u) ? u : ''; }
  function extLink(u, label) {
    var s = safeUrl(u);
    return s ? '<a class="sa-link" href="' + esc(s) + '" target="_blank" rel="noopener">' + esc(label || K.short(s, 60)) + ' ↗</a>' : (u ? esc(K.short(u, 60)) : '—');
  }
  function routeOf(href) { return String(href || '').replace(/^#\/?/, '').split('?')[0]; }
  /** Can this person open the route? (their sidebar has it, or it is one of this module's pages) */
  function canGo(href) {
    if (!href) return false;
    var key = routeOf(href);
    if (/^ops\/chain/.test(key)) return canS();
    var seg = key.split('/');
    if (MINE[seg[0]]) return true;
    var nav = document.getElementById('saNav');
    if (!nav) return false;
    var has = function (k) { return !!nav.querySelector('.sa-item[data-route="' + k.replace(/"/g, '') + '"]'); };
    return has(key) || (seg.length > 1 && has(seg[0] + '/' + seg[1])) || has(seg[0]);
  }
  function lnk(href, html) { return canGo(href) ? '<a class="sa-link" href="' + esc(href) + '">' + html + '</a>' : html; }
  /** K.kpi that only links when the person can open the target page. */
  function kp(label, value, sub, href, o) { o = Object.assign({}, o || {}); if (href && canGo(href)) o.href = href; return K.kpi(label, value, sub, o); }
  function qualityBadge(q) { return q ? badge(K.titleCase(q), QUALITY_TONE[String(q).toLowerCase()] || 'muted') : '<span class="sa-muted">—</span>'; }
  function priorityBadge(p) { return p ? badge(K.titleCase(p), QUALITY_TONE[String(p).toLowerCase()] || 'muted') : '<span class="sa-muted">—</span>'; }
  function leadPill(s) { s = s || 'new'; return badge(LEAD_LABEL[s] || K.titleCase(s), LEAD_TONE[s] || 'muted'); }
  function readOnly() { return canM() ? '' : badge('Read-only', 'muted'); }
  function opts(list, cur) { return list.map(function (o) { return '<option value="' + esc(o[0]) + '"' + (String(o[0]) === String(cur == null ? '' : cur) ? ' selected' : '') + '>' + esc(o[1]) + '</option>'; }).join(''); }
  function delta(ch, suffix) {
    if (ch == null || !isFinite(ch)) return '';
    var cls = ch === 0 ? 'flat' : ch > 0 ? 'up' : 'down';
    return '<span class="ops-delta ' + cls + '">' + (ch >= 0 ? '▲' : '▼') + ' ' + Math.abs(ch).toFixed(1) + '%' + (suffix || '') + '</span>';
  }
  function spark(series) {
    var v = (series || []).map(function (x) { return Number(x) || 0; });
    if (v.length < 2) return '';
    var max = Math.max.apply(null, v.concat([1])), n = v.length;
    var pts = v.map(function (x, i) { return (i * 100 / (n - 1)).toFixed(2) + ',' + (25 - x * 22 / max).toFixed(2); });
    return '<svg class="ops-spark" viewBox="0 0 100 26" preserveAspectRatio="none" aria-hidden="true"><path d="M0,26 L' + pts.join(' L') + ' L100,26 Z"/><polyline points="' + pts.join(' ') + '"/></svg>';
  }
  function duration(j) {
    if (!j.completed_at || !j.created_at) return '—';
    var ms = new Date(j.completed_at).getTime() - new Date(j.created_at).getTime();
    if (!isFinite(ms) || ms < 0) return '—';
    return secs(ms / 1000);
  }
  function secs(s) {
    s = Number(s); if (!isFinite(s) || s < 0) return '—';
    if (s < 60) return Math.round(s) + 's';
    if (s < 3600) return Math.floor(s / 60) + 'm ' + Math.round(s % 60) + 's';
    return Math.floor(s / 3600) + 'h ' + Math.round((s % 3600) / 60) + 'm';
  }
  function apifyOf(j) {
    var info = j.scrape_info || {};
    var cost = j.usageUsd != null ? j.usageUsd : info.usageUsd != null ? info.usageUsd : info.usageTotalUsd != null ? info.usageTotalUsd : info.usage_usd;
    return { run: j.apify_run_id || j.actorRunId || info.runId || info.run_id || '', actor: info.actorId || info.actor_id || info.actor || '', cost: cost };
  }
  function money4(v) { var n = Number(v); return isFinite(n) ? '$' + n.toFixed(4) : '—'; }
  function orgCell(r) {
    if (!r.organization_id) return '<span class="sa-muted">—</span>';
    var label = esc(r.organization_name || K.short(r.organization_id, 12));
    return canS() ? '<a class="sa-link" href="#/organizations/' + esc(enc(r.organization_id)) + '">' + label + '</a>' : '<span class="sa-mono">' + label + '</span>';
  }
  function contactLine(r) {
    var v = [r.phone, r.whatsapp, r.email].filter(Boolean);
    return v.length ? '<span class="sa-mono">' + v.map(function (x) { return esc(String(x)); }).join(' · ') + '</span>' : '<span class="sa-muted">No contact</span>';
  }
  /** Keep a page's filters in the URL (no hashchange, so no re-render). */
  function syncQuery(params) {
    try {
      var h = location.hash.split('?'), p = new URLSearchParams(h[1] || '');
      Object.keys(params).forEach(function (k) { var v = params[k]; if (v == null || v === '') p.delete(k); else p.set(k, v); });
      var s = p.toString();
      if (h[0] + (s ? '?' + s : '') !== location.hash) history.replaceState(null, '', h[0] + (s ? '?' + s : ''));
    } catch (e) { /* ignore */ }
  }
  /** Set a listView filter from outside (chips, KPI toggles): same path as the person changing it. */
  function setFilter(scope, key, value) {
    var el = $('[data-f="' + key + '"]', scope);
    if (!el) return;
    el.value = value;
    el.dispatchEvent(new Event(el.tagName === 'SELECT' || el.type === 'date' ? 'change' : 'input'));
  }
  // polling: one set per page; a timer stops itself once its page is gone, and skips while a dialog is open
  var TIMERS = [];
  function stopTimers() { TIMERS.forEach(clearInterval); TIMERS = []; }
  function every(ms, fn) {
    var at = routeOf(location.hash);
    var id = setInterval(function () {
      if (routeOf(location.hash) !== at) { clearInterval(id); return; }
      if (document.hidden || document.querySelector('#saLayer .sa-overlay,#saLayer .sa-drawer-wrap')) return;
      fn();
    }, ms);
    TIMERS.push(id);
  }
  /** Authenticated file download: errors become a toast instead of a JSON page. */
  function download(url, btn) {
    return K.busy(btn, async function () {
      var res;
      try { res = await fetch(url, { credentials: 'same-origin' }); } catch (e) { K.toast('Network error — check your connection and try again.', 'error'); return false; }
      if (res.status === 401) { location.href = '/login?superadmin=1'; return false; }
      if (!res.ok) {
        var m = 'Download failed (' + res.status + ')';
        try { var d = await res.json(); m = typeof d.detail === 'string' ? d.detail : (d.message || m); } catch (e) { /* not JSON */ }
        K.toast(m, 'error'); return false;
      }
      var blob = await res.blob();
      var name = (/filename="?([^";]+)"?/i.exec(res.headers.get('Content-Disposition') || '') || [])[1] || url.split('?')[0].split('/').pop();
      var a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = name;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 2000);
      K.toast('Downloaded ' + name);
      return true;
    });
  }
  var CFG = null;
  function publicConfig() { if (!CFG) CFG = api('/api/public/config').catch(function () { return {}; }); return CFG; }

  // ════════════════════════════════════════════════════════
  //  JOB REPORT (drawer + detail page) and job actions
  // ════════════════════════════════════════════════════════
  function reportHtml(d, id, o) {
    o = o || {};
    var j = d.job || {}, c = d.counts || {}, it = j.intent || {};
    var plat = j.platform || it.platform, url = it.canonical_url || j.query, ap = apifyOf(j), run = enc(id), live = isLive(j);
    var kv = function (k, v) { return '<dt>' + esc(k) + '</dt><dd>' + v + '</dd>'; };
    var info = kv('Run ID', '<span class="sa-mono">' + esc(id) + '</span>') +
      (j.retried_from ? kv('Retry of', '<a class="sa-link sa-mono" href="#/jobs/' + esc(enc(j.retried_from)) + '">' + esc(j.retried_from) + '</a>') : '') +
      kv('Query / URL', extLink(url, K.short(url, 70))) + kv('Platform', esc(platName(plat))) + kv('Phase', esc(j.phase || '—')) +
      kv('Provider', esc(j.provider || '—')) + kv('Organization', orgCell(j)) + kv('Started by', esc(j.created_by || '—')) +
      kv('Created', esc(K.fmtDT(j.created_at))) + kv('Completed', j.completed_at ? esc(K.fmtDT(j.completed_at)) : live ? pill('running', 'In progress') : '—') +
      kv('Duration', esc(duration(j)));
    var err = j.status === 'error' ? '<div class="alert alert-danger" style="margin-top:10px"><div class="alert-body"><div class="alert-title">Failed</div><div class="sa-mono">' + esc(String(j.error || j.message || 'Failed').slice(0, 300)) + '</div></div></div>' : '';
    var extra = (j.pages_found != null ? kv('Pages found', fmtN(j.pages_found)) : '') + (j.pages_stored != null ? kv('Pages stored', fmtN(j.pages_stored)) : '') +
      (j.limit != null ? kv('Post limit', esc(j.limit)) : '') + (it.max_comments_per_post ? kv('Comments / post', esc(it.max_comments_per_post)) : '') +
      (ap.cost != null ? kv('Apify cost', esc(money4(ap.cost))) : '') + (ap.run ? kv('Apify run', '<span class="sa-mono">' + esc(ap.run) + '</span>') : '') +
      (ap.actor ? kv('Actor', '<span class="sa-mono">' + esc(ap.actor) + '</span>') : '');
    var cf = j.comment_filter || {}, cs = j.comment_filter_summary;
    var filter = (j.comment_filter || cs) ? '<h4 class="ops-h">Keyword filter <span class="sa-small sa-muted">comments filtered before AI</span></h4><dl class="sa-kv">' +
      (cf.mode ? kv('Mode', badge(cf.mode === 'preset' ? (cf.rule_id ? 'Saved rule' : 'Preset · ' + (cf.preset || '')) : cf.mode === 'custom' ? 'Custom keywords' : cf.mode, 'primary')) : '') +
      (cs ? kv('Matched → AI', badge(fmtN(cs.matched || 0), 'success') + ' of ' + fmtN(cs.total || 0)) +
        kv('Skipped (NOT_MATCHED)', badge(fmtN(cs.not_matched || 0), 'danger') + ' <span class="sa-small sa-muted">kept stored, no AI call</span>') +
        (cs.no_filter ? kv('No filter', fmtN(cs.no_filter)) : '') +
        (cs.rule_id ? kv('Rule', lnk('#/keyword-rules', '<span class="sa-mono">' + esc(cs.rule_id) + '</span>')) : '') : '') + '</dl>' : '';
    var counts = '<div class="sa-grid ops-kpis4">' +
      kp('Pages', fmtN(c.pages || 0), '', '#/pages?run=' + run) + kp('Posts', fmtN(c.posts || 0), '', '#/posts?run=' + run) +
      kp('Comments', fmtN(c.comments || 0), '', '#/ci?run=' + run) + kp('AI analysed', fmtN(c.analyzed || 0), '', '#/ai') +
      kp('Leads', fmtN(c.leads || 0), '', '#/leads?run=' + run) + '</div>';
    var btns = '<div class="ops-btns">' +
      (o.drawer ? '<a class="btn btn-primary btn-sm" href="#/jobs/' + esc(run) + '">Open full report</a>' : '') +
      '<a class="btn btn-secondary btn-sm" href="#/pages?run=' + esc(run) + '">Pages of this run</a>' +
      '<a class="btn btn-secondary btn-sm" href="#/posts?run=' + esc(run) + '">Posts of this run</a>' +
      '<a class="btn btn-secondary btn-sm" href="#/ci?run=' + esc(run) + '">Comments of this run</a>' +
      (canGo('#/logs') ? '<a class="btn btn-secondary btn-sm" href="#/logs?q=' + esc(run) + '">View logs</a>' : '') +
      (canS() ? '<a class="btn btn-secondary btn-sm" href="#/ops/chain/' + esc(run) + '">Investigate the chain</a>' : '') + '</div>';
    var grid = o.drawer ? 'sa-grid' : 'sa-grid sa-2';
    return '<div class="' + grid + '"><div class="sa-card"><h3>Run info ' + pill(j.status) + '</h3><dl class="sa-kv">' + info + '</dl>' + err +
      (j.message && j.status !== 'error' ? '<p class="sa-small sa-muted" style="margin:10px 0 0">' + esc(String(j.message).slice(0, 300)) + '</p>' : '') + '</div>' +
      '<div class="sa-card"><h3>Collected data <span class="sa-small sa-muted">live counts</span></h3>' + counts +
      (extra ? '<dl class="sa-kv" style="margin-top:12px">' + extra + '</dl>' : '') + filter + '</div></div>' + btns;
  }
  function jobDrawer(id) {
    K.openDrawer('Run report', async function (body) {
      var d = await api(A + '/jobs/' + enc(id));
      body.innerHTML = '<div class="sa-row" style="margin-bottom:12px">' + pill(d.job.status) + ' ' + badge(platName(d.job.platform || (d.job.intent || {}).platform), 'muted') +
        ' <span class="sa-mono sa-small">' + esc(id) + '</span></div>' + reportHtml(d, id, { drawer: true });
      K.bindGo(body);
    });
  }
  var JOB_ACT = {
    retry: ['Retry search run', ' with its original URL and limits? A new run id is created; the new run belongs to the same organization.', 'Retry', false, 'Re-run '],
    cancel: ['Cancel search run', '? The run is marked cancelled and its scrape stops.', 'Cancel run', true, 'Stop '],
    del: ['Delete search run', ' for good? The run and all its pages, posts, comments and AI leads are removed. This cannot be undone.', 'Delete forever', true, 'Delete ']
  };
  async function jobAction(act, id) {
    var c = JOB_ACT[act];
    var ok = await K.confirmDialog({ title: c[0], message: c[4] + id + c[1], confirmLabel: c[2], danger: c[3] });
    if (!ok) return false;
    try {
      var r = await api(A + '/jobs/' + enc(id) + (act === 'del' ? '' : '/' + act), { method: act === 'del' ? 'DELETE' : 'POST' });
      K.toast(act === 'retry' ? 'Retry started — new run ' + (r.run_id || '') : act === 'cancel' ? 'Cancellation requested' : 'Search run deleted');
      return r || true;
    } catch (e) { K.toast(e.message, 'error'); return false; }
  }
  async function retryMany(ids) {
    if (!ids.length) return false;
    var ok = await K.confirmDialog({ title: 'Retry failed runs', message: 'Re-run ' + ids.length + ' failed run' + (ids.length === 1 ? '' : 's') + ', one by one, with their original URLs and limits?', confirmLabel: 'Retry ' + ids.length, danger: false });
    if (!ok) return false;
    var n = 0;
    for (var i = 0; i < ids.length; i++) {
      try { await api(A + '/jobs/' + enc(ids[i]) + '/retry', { method: 'POST' }); n++; } catch (e) { /* keep going */ }
    }
    K.toast('Retried ' + n + ' of ' + ids.length + ' runs', n === ids.length ? 'success' : 'warning');
    return true;
  }
  function jobButtons(j) {
    var live = isLive(j), b = '';
    if (canM() && j.status === 'running') b += '<button type="button" class="btn btn-secondary btn-xs" data-act="cancel">Cancel</button>';
    if (canM() && !live) b += '<button type="button" class="btn btn-secondary btn-xs" data-act="retry">Retry</button>';
    if (canS()) b += '<a class="btn btn-secondary btn-xs" href="#/ops/chain/' + esc(enc(j.run_id)) + '" title="Follow this run step by step: search, Apify job, pages, posts, comments, leads">Investigate</a>';
    if (canS() && !live) b += '<button type="button" class="btn btn-danger btn-xs" data-act="del" aria-label="Delete run ' + esc(j.run_id) + '">Delete</button>';
    return '<div class="row-actions">' + b + '</div>';
  }
  function bindJobButtons(tr, j, reload) {
    $$('[data-act]', tr).forEach(function (b) {
      b.onclick = async function (e) { e.stopPropagation(); var r = await K.busy(b, function () { return jobAction(b.getAttribute('data-act'), j.run_id); }); if (r) reload(); };
    });
  }
  var JOB_COLS = function (failed) {
    return [
      { label: 'Run ID', render: function (j) { return '<span class="cell-main sa-mono">' + esc(j.run_id) + '</span>' + (j.retried_from ? '<span class="cell-sub">retry of ' + esc(j.retried_from) + '</span>' : ''); } },
      { label: 'Query', render: function (j) { return '<span class="ops-clip" title="' + esc(j.query) + '">' + esc(K.short(j.query, 70)) + '</span>'; } },
      { label: 'Platform', render: function (j) { return esc(platName(j.platform || (j.intent || {}).platform)); } },
      failed ? null : { label: 'Status', render: function (j) { return pill(j.status); } },
      failed ? null : { label: 'Phase', render: function (j) { return esc(j.phase || '—'); } },
      { label: failed ? 'Error' : 'Message', render: function (j) { var m = String(j.error || j.message || (failed ? 'Unknown error' : '')); return m ? '<span class="cell-sub ops-clip"' + (failed ? ' style="color:var(--danger-text)"' : '') + ' title="' + esc(m) + '">' + esc(K.short(m, failed ? 160 : 90)) + '</span>' : '<span class="sa-muted">—</span>'; } },
      { label: 'Organization', hidden: true, render: orgCell },
      { label: 'Apify run', hidden: true, render: function (j) { var a = apifyOf(j); return a.run || a.actor || a.cost != null ? '<span class="sa-mono">' + esc(K.short(a.run, 14) || '—') + '</span><span class="cell-sub">' + esc(a.actor) + (a.cost != null ? ' · ' + esc(money4(a.cost)) : '') + '</span>' : '<span class="sa-muted">—</span>'; } },
      { label: 'Created', render: function (j) { return '<span title="' + esc(K.fmtDT(j.created_at)) + '">' + esc(K.ago(j.created_at)) + '</span>'; } },
      { label: '', cls: 'num', render: jobButtons }
    ].filter(Boolean);
  };

  // ════════════════════════════════════════════════════════
  //  OPERATIONS OVERVIEW (the old /admin dashboard)
  // ════════════════════════════════════════════════════════
  var KPI_NAV = { searches: '#/jobs', running: '#/jobs?status=running', failed: '#/jobs?tab=failed', pages: '#/pages', posts: '#/posts', comments: '#/ci', analyzed: '#/ci', leads: '#/leads' };
  var ACT_SERIES = [['leads', 'Leads'], ['searches', 'Searches'], ['pages', 'Pages'], ['posts', 'Posts'], ['comments', 'Comments']];
  async function viewOperations(root, q) {
    stopTimers();
    var days = [7, 30, 90].indexOf(+q.days) >= 0 ? +q.days : 30, from = q.from || '', to = q.to || '';
    var r = await Promise.all([
      api(A + '/dashboard' + (from || to ? qs({ from_date: from, to_date: to }) : qs({ days: days }))),
      api(A + '/saas/overview').catch(function () { return null; }),
      publicConfig()
    ]);
    var d = r[0], saas = r[1], widgets = (((r[2] || {}).appearance || {}).dashboard_widgets) || {};
    var kpiOf = function (k) { return (d.kpis || []).filter(function (x) { return x.key === k; })[0] || { value: 0, change: null, series: [] }; };
    var counts = d.counts || {}, perf = d.performance || {}, st = d.status || {}, act = d.activity || {};
    var leads = kpiOf('leads'), searches = kpiOf('searches');
    // appearance.dashboard_widgets (Global Settings): show / order per widget, as the old console did
    var ORDER = { hero: 0, kpis: 2, activity: 3, leads_by_platform: 4, alerts: 5, system_status: 6, keyword_filter: 7, recent_jobs: 9 };
    var w = function (id, cls, html, tour) {
      var o = widgets[id] || {};
      if (o.show === false) return '';
      return '<section class="' + cls + '" data-widget="' + id + '" style="order:' + (typeof o.order === 'number' ? o.order : ORDER[id]) + '"' + (tour ? ' data-tour="' + tour + '"' : '') + '>' + html + '</section>';
    };
    var range = '<div class="ops-range" role="group" aria-label="Date range">' + [7, 30, 90].map(function (n) {
      return '<button type="button" class="sa-chip' + (!from && !to && days === n ? ' active' : '') + '" data-days="' + n + '" aria-pressed="' + (!from && !to && days === n) + '">' + n + 'd</button>';
    }).join('') + '<label class="sa-small sa-muted" for="opFrom">From</label><input class="form-input" type="date" id="opFrom" value="' + esc(from) + '">' +
      '<label class="sa-small sa-muted" for="opTo">To</label><input class="form-input" type="date" id="opTo" value="' + esc(to) + '">' +
      '<button type="button" class="btn btn-secondary btn-sm" id="opApply">Apply</button><button type="button" class="btn btn-ghost btn-sm" id="opRefresh">Refresh</button></div>';
    var html = K.header('Operations overview', 'Scraping and lead activity across every organization: runs, collected data, AI analysis and alerts. ' + (d.range ? d.range.label : ''), range);

    // hero: leads in range + the run figures (and what the old console received but never showed)
    var hero = '<div class="sa-grid sa-kpis">' +
      K.kpi('Leads in range', fmtN(leads.value), fmtN(counts.leads_contact || 0) + ' with phone or email ' + delta(leads.change, ' vs previous'), { icon: 'star' }) +
      kp('Searches', fmtN(searches.value), delta(searches.change), '#/jobs', { icon: 'search' }) +
      kp('Running now', fmtN(kpiOf('running').value), 'live runs', '#/jobs?status=running', { icon: 'bolt' }) +
      kp('Success rate', perf.success_rate != null ? perf.success_rate + '%' : '—', 'completed runs in range', '#/jobs?status=completed', { icon: 'chart' }) +
      kp('Failed runs', fmtN(kpiOf('failed').value), 'all time', '#/jobs?tab=failed', { icon: 'cpu', tone: kpiOf('failed').value ? 'warn' : '' }) +
      K.kpi('Avg run time', perf.avg_duration_s != null ? secs(perf.avg_duration_s) : '—', 'completed runs in range', { icon: 'repeat' }) +
      kp('Avg lead score', counts.avg_lead_score != null ? String(counts.avg_lead_score) : '—', 'all leads', '#/scoring', { icon: 'star' }) +
      kp('Overdue follow-ups', fmtN(counts.overdue_follow_ups || 0), 'pending and past due', '#/exports?scope=follow-ups', { icon: 'bell', tone: counts.overdue_follow_ups ? 'warn' : '' }) +
      kp('Staff accounts', fmtN(counts.admin_users || 0), 'platform console users', '#/security', { icon: 'shield' }) + '</div>';

    var saasCard = '';
    if (saas) {
      var sp = saas.platform || {}, pr = saas.product || {}, hh = saas.health || {};
      var hp = function (label, v, good) { return '<span>' + esc(label) + ' ' + pill(v === good ? 'ok' : v === 'down' ? 'failed' : 'warn', K.titleCase(String(v == null ? 'unknown' : v))) + '</span>'; };
      saasCard = '<div class="sa-card"><h3><span>Platform foundation <span class="sa-small sa-muted">all organizations</span></span>' + lnk('#/organizations', 'Manage organizations →') + '</h3><div class="sa-grid sa-kpis">' +
        kp('Organizations', fmtN(sp.organizations_total), fmtN(sp.organizations_active) + ' active · ' + fmtN(sp.organizations_trial) + ' trial · ' + fmtN(sp.organizations_suspended) + ' suspended', '#/organizations', { icon: 'org' }) +
        kp('Platform users', fmtN(sp.users_total), fmtN(sp.users_active) + ' active', '#/users', { icon: 'users' }) +
        kp('Total searches', fmtN(pr.searches_total), 'all organizations', '#/jobs', { icon: 'search' }) +
        kp('Total leads', fmtN(pr.leads_total), fmtN(pr.posts_total) + ' posts · ' + fmtN(pr.comments_total) + ' comments', '#/leads', { icon: 'star' }) + '</div>' +
        '<div class="ops-health"><b>System</b>' + hp('API', hh.fastapi, 'ok') + hp('MongoDB', hh.mongodb, 'ok') + hp('Apify', hh.apify, 'ok') + hp('Gemini', hh.gemini, 'ok') +
        '<span>Background jobs ' + (hh.background_jobs_active ? pill('running', fmtN(hh.background_jobs_active) + ' active') : badge('0 active', 'muted')) + '</span></div></div>';
    }

    var kpiGrid = '<div class="sa-grid sa-kpis">' + (d.kpis || []).map(function (k) {
      var href = KPI_NAV[k.key], go = href && canGo(href);
      return '<' + (go ? 'button type="button" data-go="' + esc(href) + '"' : 'div') + ' class="sa-kpi">' +
        '<div class="k"><span>' + esc(k.label) + '</span></div><div class="v">' + esc(fmtN(k.value)) + '</div>' +
        '<div class="s">' + (delta(k.change, ' vs previous') || '<span class="sa-muted">all time</span>') + '</div>' + spark(k.series) +
        (go ? '<span class="go" aria-hidden="true">→</span></button>' : '</div>');
    }).join('') + '</div>';

    var activity = '<div class="sa-card"><h3><span>Activity <span class="sa-small sa-muted">per day</span></span></h3>' +
      '<div class="sa-chips" role="group" aria-label="Series">' + ACT_SERIES.map(function (s, i) { return '<button type="button" class="sa-chip' + (i ? '' : ' active') + '" data-series="' + s[0] + '" aria-pressed="' + !i + '">' + esc(s[1]) + '</button>'; }).join('') + '</div><div data-chart-box></div></div>';
    var lbp = d.leads_by_platform || [], maxP = Math.max.apply(null, lbp.map(function (p) { return p.count; }).concat([1]));
    var byPlat = '<div class="sa-card"><h3>Leads by platform <span class="sa-small sa-muted">in range</span></h3>' + (lbp.length ? '<ul class="sa-barlist ops-barlist">' + lbp.map(function (p) {
      return '<li><a class="lbl" href="#/platforms/' + esc(enc(p.platform)) + '">' + esc(platName(p.platform)) + '</a><div class="sa-bar" aria-hidden="true"><i style="width:' + Math.max(2, p.count * 100 / maxP).toFixed(1) + '%"></i></div><span class="sa-mono">' + esc(fmtN(p.count)) + '</span></li>';
    }).join('') + '</ul>' : K.emptyState('No leads in this window', 'Leads appear once comments are analysed.')) + '</div>';

    var al = d.alerts || [];
    var sev = { ok: 'ok', warn: 'warn', info: 'info' };
    var alerts = '<div class="sa-card"><h3>Alerts <span class="sa-small sa-muted">' + fmtN(al.length) + '</span></h3>' + (al.length ? '<ul class="sa-attn">' + al.map(function (a) {
      var href = String(a.route || '#/health').replace('#/features', '#/flags');
      var inner = '<span class="sa-dot ' + (sev[a.severity] || 'err') + '" aria-hidden="true"></span><span style="min-width:0"><b>' + esc(a.title) + '</b><span class="sa-small sa-muted" style="display:block">' + esc(a.message) + '</span></span>';
      return '<li>' + (canGo(href) ? '<a href="' + esc(href) + '">' + inner + '<span class="arr" aria-hidden="true">→</span></a>' : '<a role="presentation" style="cursor:default">' + inner + '</a>') + '</li>';
    }).join('') + '</ul>' : '<div class="sa-ok-line"><span class="sa-dot ok" aria-hidden="true"></span> All quiet — no alerts for this window.</div>') + '</div>';

    var srow = function (label, href, v) { return '<dt>' + lnk(href, esc(label)) + '</dt><dd>' + v + '</dd>'; };
    var status = '<div class="sa-card"><h3>System status</h3><dl class="sa-kv">' +
      srow('Database', '#/database', st.database ? pill('ok', 'Connected') : pill('failed', 'Down')) +
      srow('Apify token', '#/apify', st.apify_token ? pill('ok', st.apify_token_hint || 'Configured') : pill('failed', 'Not configured')) +
      srow('Gemini key', '#/ai', st.gemini_key ? pill('ok', 'Configured') : pill('warn', 'Not set (rule fallback)')) +
      srow('URL search', '#/flags', st.url_search_enabled ? pill('active', 'Enabled') : pill('disabled', 'Disabled')) +
      srow('Maintenance', '#/maintenance', st.maintenance ? pill('warn', 'Active') : pill('inactive', 'Off')) + '</dl>' +
      '<div class="ops-btns"><a class="btn btn-secondary btn-sm" href="#/jobs?tab=failed">Failed jobs</a>' +
      (canGo('#/health') ? '<a class="btn btn-secondary btn-sm" href="#/health">Health check</a>' : '') +
      (canGo('#/usage') ? '<a class="btn btn-secondary btn-sm" href="#/usage">Usage &amp; cost</a>' : '') +
      '<a class="btn btn-secondary btn-sm" href="#/exports">Exports</a></div></div>';

    var kf = d.comment_filter || {}, kt = kf.totals || {}, rule = kf.active_rule;
    var keyword = '<div class="sa-card"><h3><span>Keyword filter <span class="sa-small sa-muted">runs before AI</span></span>' + lnk('#/keyword-rules', 'Manage rules →') + '</h3>' +
      '<div class="ops-banner">' + (rule ? '<span class="sa-dot ok" aria-hidden="true"></span><span>Active rule <b>' + esc(rule.name || '—') + '</b> applies to new scrapes without their own filter. <span class="sa-small sa-muted">' +
        esc(String(rule.match_mode || 'any').toUpperCase()) + ' · ' + fmtN((rule.include_keywords || []).length) + ' keywords · ' + fmtN((rule.categories || []).length) + ' categories</span></span>'
        : '<span class="sa-dot" aria-hidden="true"></span><span><b>No active rule</b> — every comment goes to the AI. Create and activate a rule to filter comments before the AI stage.</span>') + '</div>' +
      '<div class="sa-grid sa-kpis">' + kp('Comments', fmtN(kt.comments || 0), 'all collected', '#/keyword-rules?tab=comments&status=all') +
      kp('Matched → AI', fmtN(kt.matched || 0), '', '#/keyword-rules?tab=comments&status=MATCHED') +
      kp('Skipped', fmtN(kt.not_matched || 0), 'no keyword match · kept stored', '#/keyword-rules?tab=comments&status=NOT_MATCHED') +
      kp('Filter coverage', kt.coverage_pct != null ? kt.coverage_pct + '%' : '—', fmtN(kt.no_filter || 0) + ' with no filter', '#/keyword-rules?tab=rules') + '</div></div>';

    var plats = '<div class="sa-card"><h3><span>Platforms <span class="sa-small sa-muted">all time</span></span><a class="sa-link" href="#/platforms">Manage →</a></h3><div class="sa-table-wrap"><table class="sa-table"><thead><tr><th scope="col">Platform</th><th scope="col">Status</th><th scope="col" class="num">Pages</th><th scope="col" class="num">Posts</th><th scope="col" class="num">Comments</th><th scope="col" class="num">Leads</th></tr></thead><tbody>' +
      (d.platforms || []).map(function (p) {
        return '<tr><td><a class="sa-link" href="#/platforms/' + esc(enc(p.platform)) + '">' + esc(platName(p.platform)) + '</a></td><td>' + pill(p.enabled ? 'active' : 'disabled', p.enabled ? 'Enabled' : 'Disabled') + '</td>' +
          '<td class="num">' + fmtN(p.pages) + '</td><td class="num">' + fmtN(p.posts) + '</td><td class="num">' + fmtN(p.comments) + '</td><td class="num">' + fmtN(p.leads) + '</td></tr>';
      }).join('') + '</tbody></table></div></div>';

    var rj = d.recent_jobs || [];
    var recent = '<div class="sa-card"><h3><span>Recent jobs <span class="sa-small sa-muted">click a row for the run report</span></span><a class="sa-link" href="#/jobs">All jobs →</a></h3>' + (rj.length ?
      '<div class="sa-table-wrap"><table class="sa-table ops-table"><thead><tr><th scope="col">Run ID</th><th scope="col">Query</th><th scope="col">Platform</th><th scope="col">Status</th><th scope="col">Phase</th><th scope="col">Created</th></tr></thead><tbody>' +
      rj.map(function (j) {
        return '<tr data-run="' + esc(j.run_id) + '" tabindex="0"><td class="sa-mono">' + esc(j.run_id) + '</td><td><span class="ops-clip" title="' + esc(j.query) + '">' + esc(K.short(j.query, 60)) + '</span></td><td>' + esc(platName(j.platform || (j.intent || {}).platform)) + '</td><td>' + pill(j.status) + '</td><td>' + esc(j.phase || '—') + '</td><td><span title="' + esc(K.fmtDT(j.created_at)) + '">' + esc(K.ago(j.created_at)) + '</span></td></tr>';
      }).join('') + '</tbody></table></div>' : K.emptyState('No searches yet', 'Customers start searches in the user app.')) + '</div>';

    html += '<div class="ops-dash">' + w('hero', 'ops-full', hero, 'ops-hero') + (saasCard ? '<section class="ops-full" style="order:1">' + saasCard + '</section>' : '') +
      w('kpis', 'ops-full', kpiGrid) + w('activity', '', activity) + w('leads_by_platform', '', byPlat) + w('alerts', '', alerts) + w('system_status', '', status) +
      w('keyword_filter', 'ops-full', keyword) + '<section class="ops-full" style="order:8">' + plats + '</section>' + w('recent_jobs', 'ops-full', recent) + '</div>';
    root.innerHTML = html;

    var box = $('[data-chart-box]', root);
    var drawChart = function (key) {
      if (!box) return;
      var label = (ACT_SERIES.filter(function (s) { return s[0] === key; })[0] || ['', key])[1];
      box.innerHTML = K.lineChart(act[key] || [], act.labels || [], { title: label + ' per day', height: 140 });
      K.bindCharts(box);
    };
    drawChart('leads');
    $$('[data-series]', root).forEach(function (b) {
      b.onclick = function () {
        $$('[data-series]', root).forEach(function (x) { x.classList.toggle('active', x === b); x.setAttribute('aria-pressed', String(x === b)); });
        drawChart(b.getAttribute('data-series'));
      };
    });
    $$('tr[data-run]', root).forEach(function (tr) {
      var open = function () { jobDrawer(tr.getAttribute('data-run')); };
      tr.onclick = open; tr.onkeydown = function (e) { if (e.key === 'Enter') { e.preventDefault(); open(); } };
    });
    $$('[data-days]', root).forEach(function (b) { b.onclick = function () { K.go('#/operations?days=' + b.getAttribute('data-days')); }; });
    $('#opApply', root).onclick = function () {
      var f = $('#opFrom', root).value, t = $('#opTo', root).value;
      if (f && t && f > t) { K.toast('"From" must be before "To"', 'warning'); return; }
      K.go(f || t ? '#/operations' + qs({ from: f, to: t }) : '#/operations?days=30');
    };
    $('#opRefresh', root).onclick = function () { K.route(); };
    K.bindGo(root);
    every(30000, function () { K.route(true); });
  }

  // ════════════════════════════════════════════════════════
  //  JOBS (all runs · failed jobs · by organization) + detail
  // ════════════════════════════════════════════════════════
  async function viewJobs(root, q) {
    stopTimers();
    var items = [['all', 'All jobs'], ['failed', 'Failed jobs']];
    if (canS()) items.push(['orgs', 'By organization']);
    root.innerHTML = K.header('Jobs', 'Every search run across all organizations. Click a row for the run report. Retry re-creates a run with the same URL and limits; delete removes the run and everything it collected.', readOnly()) +
      '<div id="jbTabs" data-tour="jobs-tabs"></div>';
    var first = true;
    K.tabs($('#jbTabs', root), items, q.tab || 'all', function (key, body) {
      var qq = first ? q : {}; first = false;
      stopTimers();
      if (key === 'failed') return failedTab(body);
      if (key === 'orgs') return orgSearchesTab(body, qq);
      return jobsTab(body, qq);
    });
  }
  function jobsTab(el, q) {
    el.innerHTML = '<div class="sa-chips" data-chips role="group" aria-label="Status" data-tour="jobs-chips"></div><div data-tour="jobs-table" data-list></div>';
    var list = $('[data-list]', el), chips = $('[data-chips]', el), live = false;
    var status = q.status === 'failed' ? 'error' : (q.status || '');
    var lv = K.listView(list, {
      key: 'ops-jobs', limit: 20,
      url: function (p) {
        syncQuery({ status: p.status, platform: p.platform, q: p.q, from: p.from, to: p.to });
        return A + '/jobs' + qs({ status: p.status, platform: p.platform, q: p.q, from_date: dayStart(p.from), to_date: dayEnd(p.to), offset: off(p), limit: p.limit });
      },
      initial: { status: status, platform: q.platform || '', q: q.q || '', from: q.from || '', to: q.to || '' },
      filters: [{ key: 'q', label: 'Search query or run id…' }, { key: 'status', type: 'select', label: 'Status', options: JOB_STATUS },
        { key: 'platform', type: 'select', label: 'Platform', options: PLAT_OPTS }, { key: 'from', type: 'date', label: 'From' }, { key: 'to', type: 'date', label: 'To' }],
      toolbar: canM() ? '<button type="button" class="btn btn-secondary btn-sm" data-jcsv title="Server CSV of every run matching the platform, search and dates (status is not applied)">Download CSV</button>' : '',
      columns: JOB_COLS(false),
      rowId: function (j) { return j.run_id; },
      rowClick: function (j) { jobDrawer(j.run_id); },
      bindRow: function (tr, j, reload) { bindJobButtons(tr, j, reload); },
      onData: function (d) { live = (d.items || []).some(isLive); renderChips(); },
      empty: { title: 'No jobs', desc: 'Nothing matches these filters.' }
    });
    function renderChips() {
      var cur = lv.state.status || '';
      chips.innerHTML = JOB_STATUS.map(function (s) { return '<button type="button" class="sa-chip' + (cur === s[0] ? ' active' : '') + '" aria-pressed="' + (cur === s[0]) + '" data-st="' + esc(s[0]) + '">' + esc(s[0] ? s[1] : 'All') + '</button>'; }).join('');
      $$('[data-st]', chips).forEach(function (b) { b.onclick = function () { setFilter(list, 'status', b.getAttribute('data-st')); }; });
    }
    renderChips();
    var csv = $('[data-jcsv]', list);
    if (csv) csv.onclick = function () { var s = lv.state; download(A + '/export/jobs.csv' + qs({ platform: s.platform, q: s.q, from_date: dayStart(s.from), to_date: dayEnd(s.to) }), csv); };
    // a run still going: refresh the list every 10 s
    every(10000, function () { if (live && el.isConnected) lv.reload(); });
  }
  function failedTab(el) {
    el.innerHTML = '<div class="sa-grid sa-kpis" data-tour="failed-kpis" data-fk>' + K.skeleton('rows') + '</div><div class="sa-section" data-list></div>';
    var list = $('[data-list]', el), fk = $('[data-fk]', el), shown = [];
    var lv = K.listView(list, {
      key: 'ops-failed', limit: 25,
      url: function (p) { return A + '/failed-jobs' + qs({ offset: off(p), limit: p.limit }); },
      filters: [],
      toolbar: canM() ? '<button type="button" class="btn btn-primary btn-sm" data-retryall>Retry all on this page</button>' : '',
      columns: JOB_COLS(true),
      rowId: function (j) { return j.run_id; },
      rowClick: function (j) { jobDrawer(j.run_id); },
      bindRow: function (tr, j, reload) { bindJobButtons(tr, j, reload); },
      bulk: canM() ? [{ label: 'Retry selected', run: function (ids) { return retryMany(ids); } }] : null,
      onData: function (d) {
        shown = d.items || [];
        var s = d.summary || {}, top = s.top_error;
        fk.innerHTML = K.kpi('Failed runs', fmtN(s.total || 0), 'all time', { tone: s.total ? 'bad' : '', icon: 'cpu' }) +
          K.kpi('Today', fmtN(s.today || 0), 'since midnight (UTC)', { tone: s.today ? 'warn' : '' }) + K.kpi('This week', fmtN(s.this_week || 0), 'last 7 days') +
          kp('Most common error', top ? fmtN(top.count) + ' run' + (top.count === 1 ? '' : 's') : '—', top ? '<span class="ops-clip" title="' + esc(top.message) + '">' + esc(K.short(top.message, 90)) + '</span>' : 'no failures', '#/logs?level=ERROR');
        K.bindGo(fk);
        var ra = $('[data-retryall]', list); if (ra) ra.disabled = !shown.length;
      },
      empty: { title: 'No failed jobs', desc: 'Everything is healthy.' }
    });
    var ra = $('[data-retryall]', list);
    if (ra) ra.onclick = async function () { var done = await K.busy(ra, function () { return retryMany(shown.map(function (j) { return j.run_id; })); }); if (done) lv.reload(); };
  }
  // Super Admin: searches with organization, user and Apify metadata (/api/super-admin/searches)
  function orgSearchesTab(el, q) {
    el.innerHTML = '<div data-list></div>';
    var stMap = { error: 'failed' };
    K.listView($('[data-list]', el), {
      key: 'ops-org-searches', sort: '-created_at',
      url: function (p) {
        syncQuery({ status: p.status, platform: p.platform, q: p.q, from: p.from, to: p.to, org: p.organization_id, apify: p.apify });
        return '/api/super-admin/searches' + qs({ page: p.page, limit: p.limit, sort: p.sort, q: p.q, status: p.status, platform: p.platform, from: p.from, to: p.to, organization_id: p.organization_id, apify_only: p.apify });
      },
      initial: { status: stMap[q.status] || q.status || '', platform: q.platform || '', q: q.q || '', from: q.from || '', to: q.to || '', organization_id: q.org || '', apify: q.apify === 'true' ? 'true' : '' },
      exportUrl: function (s) { return '/api/super-admin/reports/' + (s.apify ? 'apify-jobs' : 'searches') + '.csv' + qs({ organization_id: s.organization_id, status: s.status, from: s.from, to: s.to }); },
      filters: [{ key: 'q', label: 'URL, run id or user email…' },
        { key: 'status', type: 'select', label: 'Status', options: [['', 'All statuses'], ['running', 'Running'], ['completed', 'Completed'], ['failed', 'Failed'], ['cancelled', 'Cancelled']] },
        { key: 'platform', type: 'select', label: 'Platform', options: PLAT_OPTS },
        { key: 'apify', type: 'select', label: 'Runs', options: [['', 'All searches'], ['true', 'Apify jobs only']] },
        { key: 'organization_id', label: 'Organization ID' }, { key: 'from', type: 'date', label: 'From' }, { key: 'to', type: 'date', label: 'To' }],
      columns: [
        { label: 'Search', render: function (s) { return '<span class="cell-main ops-clip" title="' + esc(s.query) + '">' + esc(K.short(s.query, 48)) + '</span><span class="cell-sub sa-mono">' + esc(s.run_id) + '</span>'; } },
        { label: 'Organization', render: function (s) { return s.organization_id ? '<a class="sa-link" href="#/organizations/' + esc(enc(s.organization_id)) + '">' + esc(s.organization_name || K.short(s.organization_id)) + '</a>' : '<span class="sa-muted">—</span>'; } },
        { label: 'User', render: function (s) { return s.user_id ? '<a class="sa-link" href="#/users/' + esc(enc(s.user_id)) + '">' + esc(s.created_by || K.short(s.user_id)) + '</a>' : esc(s.created_by || '—'); } },
        { label: 'Platform', render: function (s) { return esc(platName(s.platform)); } },
        { label: 'Apify run', render: function (s) { return '<span class="sa-mono">' + esc(K.short(s.apify_run_id, 14) || '—') + '</span><span class="cell-sub">' + esc(s.actor_id || '') + (s.usage_usd ? ' · ' + esc(money4(s.usage_usd)) : '') + '</span>'; } },
        { label: 'Status', sort: 'status', render: function (s) { return pill(s.status) + (s.error ? '<span class="cell-sub ops-clip" style="color:var(--danger-text)" title="' + esc(s.error) + '">' + esc(K.short(s.error, 40)) + '</span>' : ''); } },
        { label: 'Started', sort: 'created_at', render: function (s) { return esc(K.fmtDT(s.created_at)); } },
        { label: '', cls: 'num', render: function (s) { return '<div class="row-actions"><a class="btn btn-secondary btn-xs" href="#/ops/chain/' + esc(enc(s.run_id)) + '">Investigate</a><a class="btn btn-secondary btn-xs" href="#/jobs/' + esc(enc(s.run_id)) + '">Job</a></div>'; } }
      ],
      rowClick: function (s) { jobDrawer(s.run_id); },
      empty: { title: 'No searches', desc: 'Nothing matches these filters.' }
    });
  }
  async function viewJobDetail(root, q, param) {
    stopTimers();
    var id = String(param || '').replace(/^details:/, '');
    var d = await api(A + '/jobs/' + enc(id));
    var j = d.job || {}, live = isLive(j);
    var acts = '<button type="button" class="btn btn-secondary btn-sm" data-jd="refresh">Refresh</button>' +
      (canM() && !live ? '<button type="button" class="btn btn-primary btn-sm" data-jd="retry">Retry</button>' : '') +
      (canM() && j.status === 'running' ? '<button type="button" class="btn btn-secondary btn-sm" data-jd="cancel">Cancel run</button>' : '') +
      (canS() && !live ? '<button type="button" class="btn btn-danger btn-sm" data-jd="del">Delete</button>' : '') + readOnly();
    root.innerHTML = '<div class="sa-row sa-small" style="margin-bottom:6px"><a class="sa-link" href="#/jobs">← All jobs</a></div>' +
      K.header('Job ' + id, 'Full run report from the search history record' + (live ? ' · refreshes every 5 seconds while the run is going' : '') + '.', acts) +
      reportHtml(d, id, {}) +
      '<div class="sa-card sa-section"><h3>Raw run record</h3><pre class="sa-pre">' + esc(JSON.stringify(Object.assign({}, j, { intent: j.intent || {} }), null, 2).slice(0, 6000)) + '</pre></div>';
    $$('[data-jd]', root).forEach(function (b) {
      b.onclick = async function () {
        var a = b.getAttribute('data-jd');
        if (a === 'refresh') { K.route(); return; }
        var r = await K.busy(b, function () { return jobAction(a, id); });
        if (!r) return;
        if (a === 'del') K.go('#/jobs');
        else if (a === 'retry' && r.run_id) K.go('#/jobs/' + enc(r.run_id));
        else K.route();
      };
    });
    if (live) every(5000, function () { K.route(true); });
  }

  // ════════════════════════════════════════════════════════
  //  LEADS (browser · detail drawer · bulk)
  // ════════════════════════════════════════════════════════
  async function viewLeads(root, q) {
    stopTimers();
    var tabsOn = canS();
    root.innerHTML = K.header('Leads', 'AI-analysed comments across every organization. Open a lead to work it: status, priority, owner, notes and follow-ups.', readOnly() +
      (canM() ? '<button type="button" class="btn btn-secondary btn-sm" id="ldCsv" title="Server CSV with the platform, search, dates and leads-only filters">Download CSV</button>' : '')) +
      '<div id="ldBody"></div>';
    var ctx = { run: q.run || '' };
    var body = $('#ldBody', root);
    var lvRef = {};
    var csv = $('#ldCsv', root);
    if (csv) csv.onclick = function () {
      var s = lvRef.lv ? lvRef.lv.state : {};
      download(A + '/export/leads.csv' + qs({ platform: s.platform, q: s.q, from_date: dayStart(s.from), to_date: dayEnd(s.to), only_leads: s.scope === 'all' ? 'false' : '' }), csv);
    };
    if (!tabsOn) { leadsTab(body, q, ctx, lvRef); return lvRef; }
    var first = true;
    K.tabs(body, [['all', 'Leads'], ['orgs', 'By organization']], q.tab || (q.org ? 'orgs' : 'all'), function (key, el) {
      var isFirst = first, qq = isFirst ? q : {}; first = false;
      if (key === 'orgs') return orgLeadsTab(el, qq);
      return leadsTab(el, qq, isFirst ? ctx : { run: '' }, lvRef);
    });
    return lvRef;
  }
  function runBanner(ctx) {
    if (!ctx.run) return '';
    return '<div class="ops-banner" data-runbanner><span>Only run <a class="sa-link sa-mono" href="#/jobs/' + esc(enc(ctx.run)) + '">' + esc(ctx.run) + '</a></span>' +
      '<span class="sa-spacer"></span><button type="button" class="btn btn-ghost btn-xs" data-runclear>Remove run filter ✕</button></div>';
  }
  function bindRunBanner(el, list, ctx) {
    var b = $('[data-runclear]', el);
    if (!b) return;
    b.onclick = function () {
      ctx.run = ''; syncQuery({ run: '' });
      var bn = $('[data-runbanner]', el); if (bn) bn.remove();
      var f = $('[data-f="q"]', list); setFilter(list, 'q', f ? f.value : '');     // reload without the run
    };
  }
  function leadsTab(el, q, ctx, lvRef) {
    el.innerHTML = '<div class="sa-grid sa-kpis" data-tour="leads-hero" data-lk>' + K.skeleton('rows') + '</div>' + '<div class="sa-section">' + runBanner(ctx) + '</div><div data-tour="leads-table" data-list></div>';
    var list = $('[data-list]', el), lk = $('[data-lk]', el);
    var lv = K.listView(list, {
      key: 'ops-leads', limit: 25,
      url: function (p) {
        syncQuery({ run: ctx.run, platform: p.platform, quality: p.quality, status: p.status, q: p.q, from: p.from, to: p.to, scope: p.scope });
        return A + '/leads' + qs({ run_id: ctx.run, platform: p.platform, quality: p.quality, status: p.status, q: p.q, from_date: dayStart(p.from), to_date: dayEnd(p.to), only_leads: p.scope === 'all' ? 'false' : '', offset: off(p), limit: p.limit });
      },
      initial: { platform: q.platform || '', quality: q.quality || '', status: q.status || '', q: q.q || '', from: q.from || '', to: q.to || '', scope: q.scope === 'all' ? 'all' : '' },
      filters: [{ key: 'q', label: 'Name, text, phone, email or page…' }, { key: 'platform', type: 'select', label: 'Platform', options: PLAT_OPTS },
        { key: 'quality', type: 'select', label: 'Quality', options: [['', 'Any quality'], ['hot', 'Hot'], ['warm', 'Warm'], ['cold', 'Cold']] },
        { key: 'status', type: 'select', label: 'Status', options: LEAD_STATUS_OPTS },
        { key: 'scope', type: 'select', label: 'Show', options: [['', 'Leads only'], ['all', 'All analysed comments']] },
        { key: 'from', type: 'date', label: 'From' }, { key: 'to', type: 'date', label: 'To' }],
      columns: [
        { label: 'Commenter', render: function (l) { return '<span class="cell-main">' + esc(l.commenter_name || l.author_name || '—') + '</span><span class="cell-sub">' + esc(l.page_name || '') + '</span>'; } },
        { label: 'Comment', render: function (l) { return '<span class="ops-clip" title="' + esc(l.comment_text) + '">' + esc(K.short(l.comment_text, 140)) + '</span>'; } },
        { label: 'Platform', render: function (l) { return esc(platName(l.platform)); } },
        { label: 'Score', cls: 'num', render: function (l) { return '<b>' + esc(l.lead_score == null ? '—' : l.lead_score) + '</b>'; } },
        { label: 'Quality', render: function (l) { return qualityBadge(l.lead_quality); } },
        { label: 'Priority', render: function (l) { return priorityBadge(l.lead_priority || l.priority); } },
        { label: 'Status', render: function (l) { return leadPill(l.lead_status); } },
        { label: 'Assigned to', render: function (l) { return l.assigned_to ? esc(l.assigned_to) : '<span class="sa-muted">—</span>'; } },
        { label: 'Contact', render: contactLine },
        { label: 'Analysed', render: function (l) { return '<span title="' + esc(K.fmtDT(l.analyzed_at)) + '">' + esc(K.ago(l.analyzed_at)) + '</span>'; } }
      ],
      rowId: function (l) { return l._id; },
      rowClick: function (l) { leadDrawer(l._id, function () { lv.reload(); }); },
      bulk: canM() ? leadBulk() : null,
      onData: function (d) { renderLeadKpis(lk, d.summary || {}, lv, list); },
      empty: { title: 'No leads', desc: 'Nothing matches these filters.' }
    });
    lvRef.lv = lv;
    bindRunBanner(el, list, ctx);
  }
  function renderLeadKpis(lk, s, lv, list) {
    var cur = (lv && lv.state.platform) || '';
    var plats = Object.keys(s.by_platform || {}).sort(function (a, b) { return s.by_platform[b] - s.by_platform[a]; }).slice(0, 3);
    lk.innerHTML = K.kpi('Total leads', fmtN(s.total || 0), fmtN(s.with_contact || 0) + ' with phone or email', { icon: 'star' }) +
      kp('Found this week', fmtN(s.this_week || 0), 'last 7 days', '#/analytics') +
      kp('Avg score', s.avg_score != null ? String(s.avg_score) : '—', 'all leads', '#/scoring') +
      plats.map(function (p) {
        var on = cur === p;
        return '<button type="button" class="sa-kpi' + (on ? ' warn' : '') + '" data-lplat="' + esc(p) + '" aria-pressed="' + on + '" title="' + esc(on ? 'Show every platform' : 'Show only ' + platName(p) + ' leads') + '">' +
          '<div class="k"><span>' + esc(platName(p)) + '</span></div><div class="v">' + esc(fmtN(s.by_platform[p])) + '</div><div class="s">' + (on ? 'Filtered · click to clear' : 'Click to filter') + '</div></button>';
      }).join('');
    K.bindGo(lk);
    $$('[data-lplat]', lk).forEach(function (b) { b.onclick = function () { var p = b.getAttribute('data-lplat'); setFilter(list, 'platform', cur === p ? '' : p); }; });
  }
  function leadBulk() {
    var bulk = function (action, value, ids) {
      return api(A + '/leads/bulk', { method: 'POST', body: { ids: ids, action: action, value: value } }).then(function (r) { K.toast('Updated ' + fmtN(r.affected) + ' lead' + (r.affected === 1 ? '' : 's')); return true; });
    };
    var pick = async function (title, label, options, action, ids) {
      var v = await K.openModal({ title: title + ' · ' + ids.length + ' lead' + (ids.length === 1 ? '' : 's'), submitLabel: 'Apply',
        body: '<div class="sa-field"><label for="opBulkV">' + esc(label) + '</label>' + (options ? '<select class="form-select" id="opBulkV" name="v">' + opts(options) + '</select>'
          : '<input class="form-input" id="opBulkV" name="v" maxlength="120" placeholder="Email or name of the person (empty = unassign)" value="' + esc((K.me() || {}).email || '') + '">') + '</div>' +
          (action === 'set_status' ? '<p class="sa-small sa-muted" style="margin:8px 0 0">Bulk changes skip the one-lead transition rules (for example, archived leads can be re-opened).</p>' : ''),
        onSubmit: function (f, fd) { return bulk(action, String(fd.get('v') || '').trim(), ids); } });
      return v ? true : false;
    };
    return [
      { label: 'Set status…', run: function (ids) { return pick('Set status', 'New status', LEAD_STATUSES.map(function (s) { return [s, LEAD_LABEL[s]]; }), 'set_status', ids); } },
      { label: 'Set priority…', run: function (ids) { return pick('Set priority', 'Priority', PRIORITY_OPTS, 'set_priority', ids); } },
      { label: 'Assign…', run: function (ids) { return pick('Assign leads', 'Assign to', null, 'assign', ids); } },
      { label: 'Delete…', danger: true, run: async function (ids) {
        var ok = await K.confirmDialog({ title: 'Delete ' + ids.length + ' lead' + (ids.length === 1 ? '' : 's'), message: 'Permanent. The selected AI lead records, with their notes and follow-ups, are removed. The collected comments stay.', confirmLabel: 'Delete forever' });
        if (!ok) return false;
        return bulk('delete', null, ids);
      } }
    ];
  }
  // Super Admin: leads with organization and masked contact (/api/super-admin/leads), exact run filter
  function orgLeadsTab(el, q) {
    var fixed = q.run ? { run_id: q.run } : {};
    el.innerHTML = (q.run ? '<div class="ops-banner"><span>Leads of run <a class="sa-link sa-mono" href="#/jobs/' + esc(enc(q.run)) + '">' + esc(q.run) + '</a></span><span class="sa-spacer"></span><a class="btn btn-ghost btn-xs" href="#/leads?tab=orgs">Remove run filter ✕</a></div>' : '') + '<div data-list></div>';
    K.listView($('[data-list]', el), {
      key: 'ops-org-leads', sort: '-created_at',
      url: function (p) { syncQuery({ org: p.organization_id, q: p.q, quality: p.quality, platform: p.platform }); return '/api/super-admin/leads' + qs(Object.assign({ page: p.page, limit: p.limit, sort: p.sort, q: p.q, quality: p.quality, platform: p.platform, organization_id: p.organization_id }, fixed)); },
      initial: { organization_id: q.org || '', q: q.q || '', quality: q.quality || '', platform: q.platform || '' },
      exportUrl: function (s) { return '/api/super-admin/reports/leads.csv' + qs({ organization_id: s.organization_id }); },
      filters: [{ key: 'q', label: 'Commenter or text…' }, { key: 'quality', type: 'select', label: 'Quality', options: [['', 'Any quality'], ['hot', 'Hot'], ['warm', 'Warm'], ['cold', 'Cold']] },
        { key: 'platform', type: 'select', label: 'Platform', options: PLAT_OPTS }, { key: 'organization_id', label: 'Organization ID' }],
      columns: [
        { label: 'Lead', render: function (l) { return '<span class="cell-main">' + esc(l.commenter_name || 'Anonymous') + '</span><span class="cell-sub ops-clip" title="' + esc(l.comment_text) + '">' + esc(K.short(l.comment_text, 70)) + '</span>'; } },
        { label: 'Contact (masked)', render: function (l) { return '<span class="sa-mono">' + esc(l.phone || '—') + '</span><span class="cell-sub sa-mono">' + esc(l.email || '') + '</span>'; } },
        { label: 'Quality', render: function (l) { return qualityBadge(l.lead_quality); } },
        { label: 'Score', sort: 'lead_score', cls: 'num', render: function (l) { return esc(l.lead_score == null ? '—' : l.lead_score); } },
        { label: 'Organization', render: function (l) { return orgCell(l); } },
        { label: 'Found', sort: 'created_at', render: function (l) { return esc(K.fmtDate(l.created_at)); } },
        { label: '', cls: 'num', render: function (l) { return '<div class="row-actions">' + (l.search_run_id ? '<a class="btn btn-secondary btn-xs" href="#/jobs/' + esc(enc(l.search_run_id)) + '">Source run</a>' : '') + '<button type="button" class="btn btn-secondary btn-xs" data-open>Open</button></div>'; } }
      ],
      bindRow: function (tr, l, reload) { var b = $('[data-open]', tr); if (b) b.onclick = function () { leadDrawer(l.id, reload); }; },
      rowClick: function (l) { leadDrawer(l.id); },
      empty: { title: 'No leads', desc: 'Leads appear once searches have analysed comments.' }
    });
  }
  async function viewLeadDetail(root, q, param) {
    var ref = await viewLeads(root, q);
    var id = String(param || '').replace(/^details:/, '');
    if (id) leadDrawer(id, function () { if (ref && ref.lv) ref.lv.reload(); });
  }
  function leadDrawer(id, after) {
    K.openDrawer('Lead', async function (body, close, rerun) {
      var base = A + '/leads/' + enc(id);
      var r = await Promise.all([api(base),
        api(base + '/notes').catch(function (e) { return { error: e.message }; }),
        api(base + '/follow-ups').catch(function (e) { return { error: e.message }; })]);
      var l = r[0].lead || {}, notes = r[1].notes || [], fus = r[2].follow_ups || [];
      var h = body.closest('.sa-drawer') && $('header h3', body.closest('.sa-drawer'));
      if (h) h.textContent = 'Lead · ' + (l.commenter_name || l.author_name || 'Unknown');
      var m = canM(), cur = l.lead_status || 'new';
      var kv = function (k, v) { return v == null || v === '' ? '' : '<dt>' + esc(k) + '</dt><dd>' + v + '</dd>'; };
      var contacts = [['phone', l.phone], ['whatsapp', l.whatsapp], ['email', l.email]].filter(function (c) { return c[1]; });
      var html = '<div class="sa-row" style="margin-bottom:10px">' + leadPill(cur) + qualityBadge(l.lead_quality) + (l.lead_priority || l.priority ? priorityBadge(l.lead_priority || l.priority) : '') +
        badge(platName(l.platform), 'muted') + (l.is_lead ? badge('Lead', 'success') : badge('Not a lead', 'muted')) + '</div>' +
        '<blockquote class="ops-quote">' + esc(String(l.comment_text || '').slice(0, 600) || '—') + '</blockquote>' +
        '<dl class="sa-kv">' + kv('Commenter', esc(l.commenter_name || l.author_name || '—')) + kv('Page', esc(l.page_name || '—')) +
        kv('Post', safeUrl(l.post_url) ? extLink(l.post_url, 'Open post') : '—') +
        kv('Lead score', '<b>' + esc(l.lead_score == null ? '—' : l.lead_score) + '</b>' + (l.signal_score != null ? ' <span class="sa-small sa-muted">signal ' + esc(l.signal_score) + '</span>' : '')) +
        kv('Confidence', l.confidence != null ? Math.round(Number(l.confidence) * 100) + '%' : '—') + kv('Intent', esc(l.intent || '—')) +
        kv('Budget', l.budget ? esc(l.budget) : '') + kv('Requirement', l.requirement ? esc(l.requirement) : '') + kv('Urgency', l.urgency ? esc(l.urgency) : '') +
        kv('Location', l.location ? esc(l.location) : '') + kv('Website', l.website ? extLink(l.website, l.website) : '') +
        kv('Reason', l.reason ? '<span class="sa-small">' + esc(String(l.reason).slice(0, 300)) + '</span>' : '') +
        kv('Analysed', esc(K.fmtDT(l.analyzed_at)) + (l.analyzed_by ? ' ' + badge(l.analyzed_by === 'gemini' ? 'Gemini' : K.titleCase(l.analyzed_by), l.analyzed_by === 'gemini' ? 'primary' : 'muted') : '')) +
        kv('Assigned to', esc(l.assigned_to || '—')) + kv('Organization', orgCell(l)) + '</dl>';
      html += '<h4 class="ops-h">Contact</h4>' + (contacts.length ? contacts.map(function (c) {
        var v = String(c[1]), href = c[0] === 'email' ? 'mailto:' + v : 'tel:' + v.replace(/[^\d+]/g, '');
        return '<div class="ops-contact">' + badge(K.titleCase(c[0]), 'muted') + '<span class="sa-mono">' + esc(v) + '</span><button type="button" class="btn btn-secondary btn-xs" data-copy="' + esc(v) + '">Copy</button><a class="btn btn-secondary btn-xs" href="' + esc(href) + '">Open</a></div>';
      }).join('') : '<p class="sa-small sa-muted" style="margin:0">No phone, WhatsApp or email was found in this comment.</p>');
      if (m) {
        var next = [cur].concat(LEAD_TRANSITIONS[cur] || []);
        html += '<h4 class="ops-h" data-tour="lead-manage">Work this lead</h4><form data-manage class="sa-form-grid" style="align-items:start" novalidate>' +
          '<div class="sa-field"><label for="ldStatus">Status</label><select class="form-select" id="ldStatus" name="status">' + next.map(function (s) { return '<option value="' + s + '"' + (s === cur ? ' selected' : '') + '>' + esc(LEAD_LABEL[s] || s) + (s === cur ? ' (current)' : '') + '</option>'; }).join('') + '</select>' +
          (next.length === 1 ? '<span class="hint">No further status from here.</span>' : '<span class="hint">Only the allowed next steps are offered.</span>') + '</div>' +
          '<div class="sa-field"><label for="ldPrio">Priority</label><select class="form-select" id="ldPrio" name="priority">' + (l.lead_priority ? '' : '<option value="">Not set</option>') + opts(PRIORITY_OPTS, l.lead_priority || '') + '</select></div>' +
          '<div class="sa-field"><label for="ldAssign">Assigned to</label><input class="form-input" id="ldAssign" name="assigned_to" maxlength="120" value="' + esc(l.assigned_to || '') + '" placeholder="Email or name (empty = nobody)"></div>' +
          '<div class="sa-field"><label for="ldReason">Reason <span class="sa-muted">(optional, kept with a status change)</span></label><input class="form-input" id="ldReason" name="reason" maxlength="300"></div>' +
          '<div class="sa-row" style="grid-column:1/-1"><button type="submit" class="btn btn-primary btn-sm">Save changes</button><button type="button" class="btn btn-ghost btn-sm" data-assign-me>Assign to me</button><span class="sa-spacer"></span><button type="button" class="btn btn-danger btn-sm" data-del-lead>Delete lead</button></div></form>';
      } else {
        html += '<p class="sa-small sa-muted" style="margin:14px 0 0">Read-only: your role can view leads but not change them.</p>';
      }
      var hist = (l.status_history || []).slice().reverse();
      if (hist.length) html += '<h4 class="ops-h">Status history <span class="sa-small sa-muted">' + hist.length + '</span></h4><ul class="sa-feed">' + hist.map(function (x) {
        return '<li><span>' + leadPill(x.from_status) + ' → ' + leadPill(x.to_status) + (x.reason ? '<span class="sa-small sa-muted" style="display:block">' + esc(x.reason) + '</span>' : '') + '</span><span class="when">' + esc(x.changed_by || '') + ' · ' + esc(K.fmtDT(x.changed_at)) + '</span></li>';
      }).join('') + '</ul>';
      html += '<h4 class="ops-h">Notes <span class="sa-small sa-muted">' + notes.length + '</span></h4>' + (r[1].error ? '<p class="sa-small sa-muted">Notes unavailable: ' + esc(r[1].error) + '</p>' : notes.length ? '<ul class="ops-list">' + notes.slice().reverse().map(function (n) {
        return '<li><div class="meta"><span>' + esc(n.created_by || n.author || '') + '</span><span>·</span><span>' + esc(K.fmtDT(n.created_at)) + '</span><span class="sa-spacer"></span>' +
          (m && n.id ? '<button type="button" class="btn btn-ghost btn-xs" data-del-note="' + esc(n.id) + '" aria-label="Delete note">Delete</button>' : '') + '</div><div class="txt">' + esc(n.text) + '</div></li>';
      }).join('') + '</ul>' : '<p class="sa-small sa-muted" style="margin:0">No notes yet.</p>') +
        (m ? '<div class="ops-inline"><label class="sr-only" for="ldNote">Add a note</label><textarea class="form-textarea" id="ldNote" rows="2" maxlength="2000" placeholder="Add a note…" style="min-height:54px"></textarea><button type="button" class="btn btn-secondary btn-sm" data-add-note>Add note</button></div>' : '');
      var now = Date.now();
      html += '<h4 class="ops-h">Follow-ups <span class="sa-small sa-muted">' + fus.length + '</span></h4>' + (r[2].error ? '<p class="sa-small sa-muted">Follow-ups unavailable: ' + esc(r[2].error) + '</p>' : fus.length ? '<ul class="ops-list">' + fus.map(function (f) {
        var s = f.status || 'pending', overdue = s === 'pending' && f.due_at && new Date(f.due_at).getTime() < now;
        return '<li><div class="meta">' + (overdue ? pill('failed', 'Overdue') : pill(s === 'done' ? 'done' : s === 'cancelled' ? 'cancelled' : 'pending', K.titleCase(s))) + '<span>due ' + esc(K.fmtDT(f.due_at)) + '</span><span class="sa-spacer"></span>' +
          (m && s === 'pending' ? '<button type="button" class="btn btn-secondary btn-xs" data-fu="' + esc(f.id) + '" data-fu-st="done">Done</button><button type="button" class="btn btn-ghost btn-xs" data-fu="' + esc(f.id) + '" data-fu-st="cancelled">Cancel</button>' : '') +
          '</div>' + (f.note ? '<div class="txt">' + esc(f.note) + '</div>' : '') + '</li>';
      }).join('') + '</ul>' : '<p class="sa-small sa-muted" style="margin:0">No follow-ups scheduled.</p>') +
        (m ? '<div class="ops-inline"><div class="sa-field" style="flex:0 1 220px"><label for="ldFuDue">Due</label><input class="form-input" type="datetime-local" id="ldFuDue"></div><div class="sa-field" style="flex:1 1 180px"><label for="ldFuNote">What to do</label><input class="form-input" id="ldFuNote" maxlength="500" placeholder="Call back with the price list"></div><button type="button" class="btn btn-secondary btn-sm" data-add-fu>Schedule</button></div>' : '');
      body.innerHTML = html;

      var done = function (msg) { K.toast(msg); rerun(); if (after) after(); };
      $$('[data-copy]', body).forEach(function (b) {
        b.onclick = async function () { try { await navigator.clipboard.writeText(b.getAttribute('data-copy')); K.toast('Copied'); } catch (e) { K.toast('Copy failed — select the text instead', 'error'); } };
      });
      var form = $('[data-manage]', body);
      if (form) {
        form.onsubmit = async function (e) {
          e.preventDefault();
          var fd = new FormData(form), patch = {};
          var st = fd.get('status'), pr = fd.get('priority'), as = String(fd.get('assigned_to') || '').trim(), rs = String(fd.get('reason') || '').trim();
          if (st && st !== cur) { patch.status = st; if (rs) patch.reason = rs; patch.changed_by = (K.me() || {}).email || 'admin'; }
          if (pr && pr !== (l.lead_priority || '')) patch.priority = pr;
          if (as !== (l.assigned_to || '')) patch.assigned_to = as;
          if (!Object.keys(patch).length) { K.toast('Nothing changed', 'info'); return; }
          try { await K.busy($('[type=submit]', form), function () { return api(base, { method: 'PATCH', body: patch }); }); done('Lead updated'); }
          catch (err) { K.toast(err.message, 'error'); }
        };
        $('[data-assign-me]', form).onclick = function () { $('#ldAssign', form).value = (K.me() || {}).email || ''; };
        $('[data-del-lead]', form).onclick = async function () {
          var ok = await K.confirmDialog({ title: 'Delete lead', message: 'Permanent. This AI lead record, with its notes and follow-ups, is removed. The collected comment stays.', confirmLabel: 'Delete forever' });
          if (!ok) return;
          try { await api(A + '/leads/bulk', { method: 'POST', body: { ids: [id], action: 'delete' } }); K.toast('Lead deleted'); close(); if (after) after(); }
          catch (err) { K.toast(err.message, 'error'); }
        };
      }
      var addNote = $('[data-add-note]', body);
      if (addNote) addNote.onclick = async function () {
        var text = $('#ldNote', body).value.trim();
        if (!text) { K.toast('Write the note first', 'warning'); return; }
        try { await K.busy(addNote, function () { return api(base + '/notes', { method: 'POST', body: { text: text } }); }); done('Note added'); }
        catch (err) { K.toast(err.message, 'error'); }
      };
      $$('[data-del-note]', body).forEach(function (b) {
        b.onclick = async function () {
          var ok = await K.confirmDialog({ title: 'Delete note', message: 'Delete this note? This cannot be undone.', confirmLabel: 'Delete note' });
          if (!ok) return;
          try { await api(base + '/notes/' + enc(b.getAttribute('data-del-note')), { method: 'DELETE' }); done('Note deleted'); } catch (err) { K.toast(err.message, 'error'); }
        };
      });
      var addFu = $('[data-add-fu]', body);
      if (addFu) addFu.onclick = async function () {
        var due = $('#ldFuDue', body).value;
        if (!due || isNaN(new Date(due).getTime())) { K.toast('Pick a due date and time', 'warning'); return; }
        try { await K.busy(addFu, function () { return api(base + '/follow-ups', { method: 'POST', body: { due_at: new Date(due).toISOString(), note: $('#ldFuNote', body).value.trim() } }); }); done('Follow-up scheduled'); }
        catch (err) { K.toast(err.message, 'error'); }
      };
      $$('[data-fu]', body).forEach(function (b) {
        b.onclick = async function () {
          var s = b.getAttribute('data-fu-st');
          try { await K.busy(b, function () { return api(base + '/follow-ups/' + enc(b.getAttribute('data-fu')), { method: 'PATCH', body: { status: s } }); }); done('Follow-up ' + (s === 'done' ? 'done' : 'cancelled')); }
          catch (err) { K.toast(err.message, 'error'); }
        };
      });
    });
  }

  // ════════════════════════════════════════════════════════
  //  PLATFORMS (+ detail)
  // ════════════════════════════════════════════════════════
  function platKpis(p) {
    var s = p.stats || {}, f = '?platform=' + enc(p.platform);
    return '<div class="sa-grid ops-kpis4">' + kp('Pages', fmtN(s.pages), '', '#/pages' + f) + kp('Posts', fmtN(s.posts), '', '#/posts' + f) +
      kp('Comments', fmtN(s.comments), '', '#/ci' + f) + kp('Leads', fmtN(s.leads), '', '#/leads' + f) + '</div>';
  }
  function platToggle(p) {
    return canM() ? '<label class="sa-toggle" title="' + esc((p.enabled ? 'Disable ' : 'Enable ') + platName(p.platform)) + '"><input type="checkbox" data-toggle="' + esc(p.platform) + '"' + (p.enabled ? ' checked' : '') + ' aria-label="' + esc(platName(p.platform)) + ' enabled"><span></span></label>' : '';
  }
  function bindToggles(root, after) {
    $$('[data-toggle]', root).forEach(function (el) {
      el.onchange = async function () {
        var p = el.getAttribute('data-toggle'), want = el.checked;
        if (!want) {
          var ok = await K.confirmDialog({ title: 'Disable ' + platName(p), message: 'Every scrape entry point for ' + platName(p) + ' stops, including searches customers start in the app. Running searches finish.', confirmLabel: 'Disable ' + platName(p) });
          if (!ok) { el.checked = true; return; }
        }
        el.disabled = true;
        try {
          var r = await api(A + '/platforms/' + enc(p) + '/toggle', { method: 'POST' });
          K.toast(platName(p) + (r.enabled ? ' enabled' : ' disabled'), r.enabled ? 'success' : 'warning');
          if (after) after();
        } catch (e) { K.toast(e.message, 'error'); el.checked = !want; }
        finally { if (el.isConnected) el.disabled = false; }
      };
    });
  }
  async function viewPlatforms(root) {
    stopTimers();
    var ps = (await api(A + '/platforms')).platforms || [];
    var on = ps.filter(function (p) { return p.enabled; }).length;
    root.innerHTML = K.header('Platforms', 'Turn each social platform on or off for everyone, and choose the Apify actor (the ready-made scraper on Apify) it collects with. Turning a platform off stops all new searches on it, including in the customer app.',
      badge(on + '/' + ps.length + ' enabled', on === ps.length ? 'success' : 'warning') + readOnly()) +
      '<div class="sa-grid sa-2" data-tour="plat-cards">' + ps.map(function (p) {
        return '<div class="sa-card" data-plat="' + esc(p.platform) + '"><h3><a class="sa-link" href="#/platforms/' + esc(enc(p.platform)) + '" style="font-size:14px;font-weight:700">' + esc(platName(p.platform)) + '</a><span class="sa-row">' +
          pill(p.enabled ? 'active' : 'disabled', p.enabled ? 'Enabled' : 'Disabled') + platToggle(p) + '</span></h3>' + platKpis(p) +
          '<h4 class="ops-h">Actors <span class="sa-small sa-muted">Apify scrapers this platform uses</span></h4>' + (p.actors || []).map(function (a) {
            return '<div class="ops-actor">' + badge(a.kind, 'muted') + (canM() ? '<label class="sr-only" for="act_' + esc(a.key) + '">' + esc(a.key) + '</label><input class="form-input sa-mono" id="act_' + esc(a.key) + '" data-actor="' + esc(a.key) + '" data-kind="' + esc(a.kind) + '" data-orig="' + esc(a.value) + '" value="' + esc(a.value) + '" autocomplete="off">' +
              '<button type="button" class="btn btn-secondary btn-xs" data-test="' + esc(a.key) + '" title="Tests the saved actor id with LeadAI\'s Apify token">Test</button>' : '<span class="sa-mono">' + esc(a.value) + '</span>') + '</div>' +
              '<div class="sa-small sa-muted" style="margin:0 0 8px">' + (a.overridden ? 'Override of the default ' + esc(a.default) : 'Default actor') + '</div>';
          }).join('') + (canM() ? '<div class="ops-btns"><button type="button" class="btn btn-primary btn-sm" data-save="' + esc(p.platform) + '">Save actors</button></div>' : '') + '</div>';
      }).join('') + '</div>';
    bindToggles(root, function () { K.route(true); });
    $$('[data-test]', root).forEach(function (b) {
      b.onclick = async function () {
        try {
          var r = await K.busy(b, function () { return api(A + '/actors/test', { method: 'POST', body: { key: b.getAttribute('data-test') } }); });
          K.toast(r.ok ? 'Actor ' + r.actor_id + ' is reachable' : 'Test failed: ' + (r.error || 'unknown error'), r.ok ? 'success' : 'error');
        } catch (e) { K.toast(e.message, 'error'); }
      };
    });
    $$('[data-save]', root).forEach(function (b) {
      b.onclick = async function () {
        var card = b.closest('[data-plat]'), p = b.getAttribute('data-save');
        var rows = $$('[data-actor]', card), changed = rows.filter(function (i) { return i.value.trim() !== i.getAttribute('data-orig'); });
        var empty = rows.filter(function (i) { return !i.value.trim(); })[0];
        if (empty) { K.toast('Actor ' + empty.getAttribute('data-actor') + ' cannot be empty', 'error'); empty.focus(); return; }
        if (!changed.length) { K.toast('No actor changed', 'info'); return; }
        try {
          await K.busy(b, async function () {
            for (var i = 0; i < changed.length; i++) await api(A + '/platforms/' + enc(p) + '/actor', { method: 'POST', body: { kind: changed[i].getAttribute('data-kind'), actor_id: changed[i].value.trim() } });
          });
          K.toast('Actors saved — the next scrape uses the new actor ids');
          K.route(true);
        } catch (e) { K.toast(e.message, 'error'); }
      };
    });
  }
  async function viewPlatformDetail(root, q, param) {
    stopTimers();
    var name = String(param || '').replace(/^details:/, '');
    var r = await Promise.all([api(A + '/platforms'), api(A + '/jobs' + qs({ platform: name, limit: 8 })).catch(function () { return { items: [] }; })]);
    var p = (r[0].platforms || []).filter(function (x) { return x.platform === name; })[0];
    var back = '<div class="sa-row sa-small" style="margin-bottom:6px"><a class="sa-link" href="#/platforms">← All platforms</a></div>';
    if (!p) { root.innerHTML = back + K.emptyState('Unknown platform', '"' + name + '" is not one of the configured platforms.', '<a class="btn btn-secondary btn-sm" href="#/platforms">All platforms</a>'); return; }
    var jobs = r[1].items || [];
    root.innerHTML = back + K.header(platName(p.platform), 'Platform detail: live statistics, actors and recent runs.', readOnly()) +
      '<div class="sa-grid sa-2"><div class="sa-card"><h3>Status <span class="sa-row">' + pill(p.enabled ? 'active' : 'disabled', p.enabled ? 'Enabled' : 'Disabled') + platToggle(p) + '</span></h3>' + platKpis(p) + '</div>' +
      '<div class="sa-card"><h3>Actors used by this platform ' + (canM() ? '<a class="sa-link" href="#/platforms">Edit →</a>' : '') + '</h3><dl class="sa-kv">' + (p.actors || []).map(function (a) {
        return '<dt>' + esc(a.kind) + '</dt><dd><span class="sa-mono">' + esc(a.value) + '</span> ' + badge(a.overridden ? 'Override' : 'Default', a.overridden ? 'primary' : 'muted') + '</dd>';
      }).join('') + '</dl></div></div>' +
      '<div class="sa-card sa-section"><h3><span>Recent runs on ' + esc(platName(p.platform)) + '</span><a class="sa-link" href="#/jobs?platform=' + esc(enc(p.platform)) + '">All ' + esc(platName(p.platform)) + ' jobs →</a></h3>' +
      (jobs.length ? '<div class="sa-table-wrap"><table class="sa-table ops-table"><thead><tr><th scope="col">Run ID</th><th scope="col">Query</th><th scope="col">Status</th><th scope="col">Phase</th><th scope="col">Created</th></tr></thead><tbody>' + jobs.map(function (j) {
        return '<tr data-run="' + esc(j.run_id) + '" tabindex="0"><td class="sa-mono">' + esc(j.run_id) + '</td><td><span class="ops-clip" title="' + esc(j.query) + '">' + esc(K.short(j.query, 60)) + '</span></td><td>' + pill(j.status) + '</td><td>' + esc(j.phase || '—') + '</td><td>' + esc(K.ago(j.created_at)) + '</td></tr>';
      }).join('') + '</tbody></table></div>' : K.emptyState('No runs yet', 'No searches have run on ' + platName(p.platform) + '.')) + '</div>';
    $$('tr[data-run]', root).forEach(function (tr) {
      var open = function () { jobDrawer(tr.getAttribute('data-run')); };
      tr.onclick = open; tr.onkeydown = function (e) { if (e.key === 'Enter') { e.preventDefault(); open(); } };
    });
    bindToggles(root, function () { K.route(true); });
  }

  // ════════════════════════════════════════════════════════
  //  PAGES · POSTS
  // ════════════════════════════════════════════════════════
  function csvButton(scope, label) { return canM() ? '<button type="button" class="btn btn-secondary btn-sm" data-csv="' + scope + '" title="Server CSV of every ' + esc(label) + ' on the chosen platform">Download CSV</button>' : ''; }
  function bindCsv(el, lv, scope) {
    var b = $('[data-csv]', el);
    if (b) b.onclick = function () { download(A + '/export/' + scope + '.csv' + qs({ platform: lv.state.platform }), b); };
  }
  function kvModal(title, rows) {
    return K.openModal({ title: title, submitLabel: null, cancelLabel: 'Close', size: 'lg',
      onOpen: function (f) { var x = $('footer [data-x]', f); if (x) setTimeout(function () { x.focus(); }, 0); },
      body: '<dl class="sa-kv">' + rows.filter(function (r) { return r; }).map(function (r) { return '<dt>' + esc(r[0]) + '</dt><dd>' + r[1] + '</dd>'; }).join('') + '</dl>' });
  }
  async function pageModal(id) {
    try {
      var p = await api(A + '/pages/' + enc(id));
      var url = p.facebook_url || p.page_url || p.url;
      kvModal('Page · ' + (p.page_name || 'Unknown'), [['Name', '<b>' + esc(p.page_name || '—') + '</b>'], ['URL', extLink(url)], ['Platform', esc(platName(p.platform))],
        ['Category', esc(p.category || '—')], ['City / State', esc([p.city, p.state].filter(Boolean).join(', ') || '—')], ['Followers', fmtN(p.followers || 0)],
        ['Verified', p.verified ? 'Yes' : '—'], ['Phone', esc(p.phone || '—')], ['Email', esc(p.email || '—')], p.whatsapp ? ['WhatsApp', esc(p.whatsapp)] : null,
        ['Website', p.website ? extLink(p.website, p.website) : '—'], ['Description', '<span class="sa-small" style="white-space:pre-wrap">' + esc(String(p.description || '').slice(0, 600) || '—') + '</span>'],
        ['Run', p.search_run_id ? '<a class="sa-link sa-mono" href="#/jobs/' + esc(enc(p.search_run_id)) + '">' + esc(p.search_run_id) + '</a>' : '—'],
        ['Collected', esc(K.fmtDT(p.collected_at || p.created_at))],
        ['More', '<a class="sa-link" href="#/posts?q=' + esc(enc(p.page_name || '')) + '">Posts of this page</a> · <a class="sa-link" href="#/ci?q=' + esc(enc(p.page_name || '')) + '">Comments of this page</a>']]);
    } catch (e) { K.toast(e.message || 'Could not load the page', 'error'); }
  }
  async function postModal(id) {
    try {
      var o = await api(A + '/posts/' + enc(id));
      kvModal('Post · ' + (o.page_name || 'Unknown page'), [['URL', extLink(o.post_url, 'Open post')], ['Page', esc(o.page_name || '—')], ['Platform', esc(platName(o.platform))],
        ['Caption', '<span style="white-space:pre-wrap">' + esc(String(o.caption || o.description || '').slice(0, 1200) || '—') + '</span>'],
        (o.hashtags || []).length ? ['Hashtags', o.hashtags.map(function (h) { return badge(h, 'muted'); }).join(' ')] : null,
        ['Reactions', fmtN(o.reaction_count || o.likes_count || 0)], ['Comments', fmtN(o.total_comment_count || o.comments_count || 0)], ['Shares', fmtN(o.shares_count || 0)],
        ['Published', esc(K.fmtDT(o.published_date))], ['Collected', esc(K.fmtDT(o.collected_at || o.created_at))],
        ['Run', o.collection_run_id || o.search_run_id ? '<a class="sa-link sa-mono" href="#/jobs/' + esc(enc(o.collection_run_id || o.search_run_id)) + '">' + esc(o.collection_run_id || o.search_run_id) + '</a>' : '—']]);
    } catch (e) { K.toast(e.message || 'Could not load the post', 'error'); }
  }
  async function viewPages(root, q) {
    stopTimers();
    root.innerHTML = K.header('Pages', 'Every page collected by a search run, across all organizations. Click a page for its details.', '') + '<div data-list data-tour="pages-list"></div>';
    var el = $('[data-list]', root);
    var lv = K.listView(el, {
      key: 'ops-pages', limit: 25,
      url: function (p) {
        syncQuery({ run: p.run, platform: p.platform, q: p.q, contact: p.contact, from: p.from, to: p.to });
        return A + '/pages' + qs({ platform: p.platform, q: p.q, run_id: p.run, contact: p.contact, from_date: dayStart(p.from), to_date: dayEnd(p.to), offset: off(p), limit: p.limit });
      },
      // the run is an ordinary filter here, so "Clear filters" clears it too (the old console kept it)
      initial: { run: q.run || '', platform: q.platform || '', q: q.q || '', contact: q.contact === 'true' ? 'true' : '', from: q.from || '', to: q.to || '' },
      filters: [{ key: 'q', label: 'Name, category or city…' }, { key: 'run', label: 'Run id' }, { key: 'platform', type: 'select', label: 'Platform', options: PLAT_OPTS },
        { key: 'contact', type: 'select', label: 'Contact info', options: [['', 'Any contact info'], ['true', 'With contact info']] },
        { key: 'from', type: 'date', label: 'From' }, { key: 'to', type: 'date', label: 'To' }],
      toolbar: csvButton('pages', 'page'),
      columns: [
        { label: 'Page', render: function (p) { var u = safeUrl(p.facebook_url || p.page_url || p.url); return '<span class="cell-main">' + esc(p.page_name || '—') + '</span>' + (u ? '<a class="cell-sub sa-link" href="' + esc(u) + '" target="_blank" rel="noopener">open profile ↗</a>' : ''); } },
        { label: 'Platform', render: function (p) { return esc(platName(p.platform)); } },
        { label: 'Category', render: function (p) { return esc(p.category || '—'); } },
        { label: 'City', render: function (p) { return esc(p.city || '—'); } },
        { label: 'Followers', cls: 'num', render: function (p) { return fmtN(p.followers || 0); } },
        { label: 'Contact', render: function (p) { return p.phone || p.email || p.whatsapp || p.website ? contactLine(p) + (p.website ? '<span class="cell-sub">' + esc(K.short(p.website, 40)) + '</span>' : '') : '<span class="sa-muted">No contact</span>'; } },
        { label: 'Run', render: function (p) { return p.search_run_id ? '<a class="sa-link sa-mono" href="#/jobs/' + esc(enc(p.search_run_id)) + '">' + esc(K.short(p.search_run_id, 16)) + '</a>' : '<span class="sa-muted">—</span>'; } },
        { label: 'Collected', render: function (p) { return '<span title="' + esc(K.fmtDT(p.collected_at || p.created_at)) + '">' + esc(K.ago(p.collected_at || p.created_at)) + '</span>'; } }
      ],
      rowClick: function (p) { pageModal(p._id); },
      empty: { title: 'No pages', desc: 'Nothing matches these filters.' }
    });
    bindCsv(el, lv, 'pages');
  }
  async function viewPosts(root, q) {
    stopTimers();
    root.innerHTML = K.header('Posts', 'Every post collected by a search run, across all organizations. Click a post for its details.', '') + '<div data-list></div>';
    var el = $('[data-list]', root);
    var lv = K.listView(el, {
      key: 'ops-posts', limit: 25,
      url: function (p) {
        syncQuery({ run: p.run, platform: p.platform, q: p.q, from: p.from, to: p.to });
        return A + '/posts' + qs({ platform: p.platform, q: p.q, run_id: p.run, from_date: dayStart(p.from), to_date: dayEnd(p.to), offset: off(p), limit: p.limit });
      },
      initial: { run: q.run || '', platform: q.platform || '', q: q.q || '', from: q.from || '', to: q.to || '' },
      filters: [{ key: 'q', label: 'Caption or page…' }, { key: 'run', label: 'Run id' }, { key: 'platform', type: 'select', label: 'Platform', options: PLAT_OPTS },
        { key: 'from', type: 'date', label: 'Published from' }, { key: 'to', type: 'date', label: 'Published to' }],
      toolbar: csvButton('posts', 'post'),
      columns: [
        { label: 'Post', render: function (o) { return '<span class="ops-clip" title="' + esc(o.caption || o.description) + '">' + esc(K.short(o.caption || o.description || '—', 110)) + '</span>'; } },
        { label: 'Platform', render: function (o) { return esc(platName(o.platform)); } },
        { label: 'Page', render: function (o) { return esc(o.page_name || '—'); } },
        { label: 'Reactions', cls: 'num', render: function (o) { return fmtN(o.reaction_count || o.likes_count || 0); } },
        { label: 'Comments', cls: 'num', render: function (o) { return fmtN(o.total_comment_count || 0); } },
        { label: 'Shares', cls: 'num', render: function (o) { return fmtN(o.shares_count || 0); } },
        { label: 'Run', hidden: true, render: function (o) { var r = o.collection_run_id || o.search_run_id; return r ? '<a class="sa-link sa-mono" href="#/jobs/' + esc(enc(r)) + '">' + esc(K.short(r, 16)) + '</a>' : '<span class="sa-muted">—</span>'; } },
        { label: 'Published', render: function (o) { return o.published_date ? '<span title="' + esc(K.fmtDT(o.published_date)) + '">' + esc(K.ago(o.published_date)) + '</span>' : '—'; } }
      ],
      rowClick: function (o) { postModal(o._id); },
      empty: { title: 'No posts', desc: 'Nothing matches these filters.' }
    });
    bindCsv(el, lv, 'posts');
  }

  // ════════════════════════════════════════════════════════
  //  COMMENTS · CONTACT INTELLIGENCE
  // ════════════════════════════════════════════════════════
  async function viewCI(root, q) {
    stopTimers();
    var r = await Promise.all([api(A + '/comments?limit=1'), api(A + '/comment-intelligence'), api('/api/comment-filters/stats').catch(function () { return null; })]);
    var sm = r[0].summary || {}, set = r[1].settings || {}, kr = (r[2] && r[2].totals) || {};
    var ctx = { run: q.run || '' };
    var m = canM();
    var intents = Object.keys(sm.intents || {}).sort(function (a, b) { return sm.intents[b] - sm.intents[a]; });
    var maxI = Math.max.apply(null, intents.map(function (i) { return sm.intents[i]; }).concat([1]));
    root.innerHTML = K.header('Comments', 'Contact intelligence for every collected comment: the intent, quality and confidence the engine gave it, and any phone or email it found. Click a comment to see the full lead.',
      badge(fmtN(sm.analyzed || 0) + ' analysed', 'primary') + badge(fmtN(sm.leads || 0) + ' leads', 'success') + readOnly()) +
      (kr.comments ? '<div class="ops-banner"><span>Keyword filter: <b>' + fmtN(kr.matched || 0) + '</b> matched → AI · <b>' + fmtN(kr.not_matched || 0) + '</b> skipped (kept stored)' + (kr.coverage_pct != null ? ' · ' + esc(kr.coverage_pct) + '% coverage' : '') + '</span><span class="sa-spacer"></span>' + (canGo('#/keyword-rules') ? '<a class="btn btn-secondary btn-xs" href="#/keyword-rules">Manage rules</a>' : '') + '</div>' : '') +
      '<div class="sa-grid sa-2"><div class="sa-card"><h3>Pipeline <span class="sa-small sa-muted">all time</span></h3><div class="sa-grid ops-kpis4">' +
      K.kpi('Total', fmtN(sm.total || 0), 'analysed comments') + kp('Analysed', fmtN(sm.analyzed || 0), '', '#/ai') + kp('Leads', fmtN(sm.leads || 0), '', '#/leads') +
      kp('Contacts', fmtN(sm.contacts || 0), 'phone or email', '#/ci?contact=true') + kp('High value', fmtN(sm.high_value || 0), 'leads scoring 80+', '#/ci?quality=high&is_lead=true') +
      kp('Avg confidence', sm.avg_confidence != null ? Math.round(sm.avg_confidence * 100) + '%' : '—', 'avg score ' + (sm.avg_score != null ? sm.avg_score : '—'), '#/scoring') + '</div>' +
      (intents.length ? '<h4 class="ops-h">Intent <span class="sa-small sa-muted">click to filter</span></h4><ul class="sa-barlist ops-barlist">' + intents.map(function (i) {
        return '<li><a class="lbl" href="#/ci?intent=' + esc(enc(i)) + '">' + esc(K.titleCase(i)) + '</a><div class="sa-bar" aria-hidden="true"><i style="width:' + Math.max(2, sm.intents[i] * 100 / maxI).toFixed(1) + '%"></i></div><span class="sa-mono">' + fmtN(sm.intents[i]) + '</span></li>';
      }).join('') + '</ul>' : '') + '</div>' +
      '<div class="sa-card" data-tour="ci-settings"><h3>Detection settings <span class="sa-small sa-muted">' + (m ? 'saved instantly' : 'read-only') + '</span></h3>' + CI_TOGGLES.map(function (t, i) {
        return '<div class="sa-flag"><label class="sa-toggle"><input type="checkbox" id="ciT' + i + '" data-ci="' + t[0] + '"' + (set[t[0]] ? ' checked' : '') + (m ? '' : ' disabled') + '><span></span></label><div><label for="ciT' + i + '" style="font-weight:600;font-size:13px">' + esc(t[1]) + '</label><div class="sa-small sa-muted">' + esc(t[2]) + '</div></div></div>';
      }).join('') + '<div class="sa-flag"><div class="sa-field" style="width:100%"><label for="ciMin">Minimum lead score</label><input class="form-input" type="number" id="ciMin" min="0" max="100" step="1" value="' + esc(set['ci.min_lead_score'] != null ? set['ci.min_lead_score'] : 0) + '"' + (m ? '' : ' disabled') + ' style="max-width:140px"><span class="hint">Comments below this score are not treated as leads. Applies to the next analysis.</span></div></div></div></div>' +
      '<div class="sa-card sa-section"><h3>Comments <span class="sa-small sa-muted">sorted by lead score</span></h3>' + runBanner(ctx) + '<div data-list></div></div>';
    var list = $('[data-list]', root);
    K.listView(list, {
      key: 'ops-ci', limit: 25,
      url: function (p) {
        syncQuery({ run: ctx.run, q: p.q, platform: p.platform, intent: p.intent, quality: p.quality, is_lead: p.is_lead, contact: p.contact, min_confidence: p.min_confidence, from: p.from, to: p.to });
        return A + '/comments' + qs({ run_id: ctx.run, q: p.q, platform: p.platform, intent: p.intent, quality: p.quality, is_lead: p.is_lead, contact: p.contact, min_confidence: p.min_confidence, from_date: dayStart(p.from), to_date: dayEnd(p.to), offset: off(p), limit: p.limit });
      },
      initial: { q: q.q || '', platform: q.platform || '', intent: q.intent || '', quality: q.quality || '', is_lead: q.is_lead === 'true' || q.is_lead === 'false' ? q.is_lead : '', contact: q.contact === 'true' ? 'true' : '', min_confidence: q.min_confidence || '', from: q.from || '', to: q.to || '' },
      filters: [{ key: 'q', label: 'Text, name, page, phone or email…' }, { key: 'platform', type: 'select', label: 'Platform', options: PLAT_OPTS },
        { key: 'intent', type: 'select', label: 'Intent', options: [['', 'All intents']].concat(intents.map(function (i) { return [i, K.titleCase(i)]; })) },
        { key: 'quality', type: 'select', label: 'Quality', options: [['', 'All qualities'], ['high', 'High'], ['medium', 'Medium'], ['low', 'Low']] },
        { key: 'is_lead', type: 'select', label: 'Leads', options: [['', 'Leads and others'], ['true', 'Leads only'], ['false', 'Not leads']] },
        { key: 'contact', type: 'select', label: 'Contact', options: [['', 'Any contact'], ['true', 'With contact']] },
        { key: 'min_confidence', type: 'select', label: 'Confidence', options: [['', 'Any confidence'], ['0.5', '50% or more'], ['0.7', '70% or more'], ['0.9', '90% or more']] },
        { key: 'from', type: 'date', label: 'From' }, { key: 'to', type: 'date', label: 'To' }],
      columns: [
        { label: 'Commenter', render: function (c) { return '<span class="cell-main">' + esc(c.commenter_name || c.author_name || '—') + '</span><span class="cell-sub">' + esc(c.page_name || '') + '</span>'; } },
        { label: 'Comment', render: function (c) { return '<span class="ops-clip" title="' + esc(c.comment_text) + '">' + esc(K.short(c.comment_text, 240)) + '</span>'; } },
        { label: 'Platform', render: function (c) { return esc(platName(c.platform)); } },
        { label: 'Lead', render: function (c) { return c.is_lead ? badge('Lead', 'success') : '<span class="sa-muted">—</span>'; } },
        { label: 'Intent', render: function (c) { return c.intent ? badge(K.titleCase(c.intent), 'primary') : '<span class="sa-muted">—</span>'; } },
        { label: 'Quality', render: function (c) { return qualityBadge(c.lead_quality); } },
        { label: 'Score', cls: 'num', render: function (c) { return '<b>' + esc(c.lead_score == null ? 0 : c.lead_score) + '</b>'; } },
        { label: 'Confidence', cls: 'num', render: function (c) { return c.confidence != null ? Math.round(c.confidence * 100) + '%' : '—'; } },
        { label: 'Contact', render: contactLine },
        { label: 'Analysed', render: function (c) { return c.analyzed_at ? '<span title="' + esc(K.fmtDT(c.analyzed_at)) + '">' + esc(K.ago(c.analyzed_at)) + '</span>' : '—'; } }
      ],
      rowClick: function (c) { leadDrawer(c._id); },
      empty: { title: 'No comments', desc: 'Nothing matches these filters.' }
    });
    bindRunBanner(root, list, ctx);
    if (m) {
      $$('[data-ci]', root).forEach(function (el) {
        el.onchange = async function () {
          var body = {}; body[el.getAttribute('data-ci')] = el.checked;
          var label = (CI_TOGGLES.filter(function (t) { return t[0] === el.getAttribute('data-ci'); })[0] || [])[1];
          try { await api(A + '/comment-intelligence', { method: 'PUT', body: body }); K.toast(label + (el.checked ? ' on' : ' off')); }
          catch (e) { K.toast(e.message, 'error'); el.checked = !el.checked; }
        };
      });
      var min = $('#ciMin', root), t;
      min.addEventListener('input', function () {
        clearTimeout(t);
        t = setTimeout(async function () {
          var v = parseInt(min.value, 10);
          if (isNaN(v) || v < 0 || v > 100) { K.toast('Use a number from 0 to 100', 'warning'); return; }
          try { await api(A + '/comment-intelligence', { method: 'PUT', body: { 'ci.min_lead_score': v } }); K.toast('Minimum lead score set to ' + v); }
          catch (e) { K.toast(e.message, 'error'); }
        }, 600);
      });
    }
  }

  // ════════════════════════════════════════════════════════
  //  EXPORTS (create with filters · quick downloads · history)
  // ════════════════════════════════════════════════════════
  async function viewExports(root, q) {
    stopTimers();
    var m = canM(), scope0 = EXPORTS.some(function (e) { return e.scope === q.scope; }) ? q.scope : 'leads';
    var createCard = m ? '<div class="sa-card" data-tour="exp-create"><h3>Create an export</h3><form id="exForm" novalidate><div class="ops-form">' +
      '<div class="sa-field"><label for="exScope">Data</label><select class="form-select" id="exScope" name="scope">' + EXPORTS.map(function (e) { return '<option value="' + e.scope + '"' + (e.scope === scope0 ? ' selected' : '') + '>' + esc(e.label) + '</option>'; }).join('') + '</select><span class="hint" id="exHint"></span></div>' +
      '<div class="sa-field" data-ff="platform"><label for="exPlat">Platform</label><select class="form-select" id="exPlat" name="platform">' + opts(PLAT_OPTS, '') + '</select></div>' +
      '<div class="sa-field" data-ff="q"><label for="exQ">Search text</label><input class="form-input" id="exQ" name="q" maxlength="120" placeholder="Query, run id, name, phone…"></div>' +
      '<div class="sa-field" data-ff="from"><label for="exFrom">From</label><input class="form-input" type="date" id="exFrom" name="from"></div>' +
      '<div class="sa-field" data-ff="to"><label for="exTo">To</label><input class="form-input" type="date" id="exTo" name="to"></div>' +
      '<div class="sa-field" data-ff="only_leads"><span style="font-size:12.5px;font-weight:600;color:var(--text-secondary)">Rows</span><label class="sa-check"><input type="checkbox" id="exOnly" name="only_leads" checked> Leads only (untick for every analysed comment)</label></div>' +
      '</div><div class="ops-btns"><button type="submit" class="btn btn-primary btn-sm" id="exGo">Download CSV</button><span class="sa-small sa-muted" style="align-self:center">Generated live from the database; every export is recorded below and in the audit log.</span></div></form></div>'
      : '<div class="ops-banner"><span>Read-only: downloading exports needs the manager role. You can still see who exported what.</span></div>';
    root.innerHTML = K.header('Exports', 'CSV downloads generated live from the database on every request. Nothing is stored or queued.', readOnly() + (canGo('#/reports') ? '<a class="btn btn-secondary btn-sm" href="#/reports">Billing &amp; account reports</a>' : '')) +
      createCard +
      (m ? '<div class="sa-grid sa-3 sa-section">' + EXPORTS.map(function (e) {
        return '<div class="sa-card"><h3>' + esc(e.label) + ' <span class="sa-small sa-muted">.csv</span></h3><p class="sa-small sa-muted" style="margin:0 0 10px">' + esc(e.hint) + '</p><button type="button" class="btn btn-secondary btn-sm" data-quick="' + e.scope + '">Download all ' + esc(e.label.toLowerCase()) + '</button></div>';
      }).join('') + '<div class="sa-card"><h3>Application log <span class="sa-small sa-muted">.log</span></h3><p class="sa-small sa-muted" style="margin:0 0 10px">The raw app.log file (plain text).</p><button type="button" class="btn btn-secondary btn-sm" data-applog>Download app.log</button></div></div>' : '') +
      '<div class="sa-card sa-section" data-tour="exp-history"><h3>Export history <span class="sa-small sa-muted">who downloaded what</span></h3><div data-list></div></div>';
    var hist = K.listView($('[data-list]', root), {
      key: 'ops-exports', limit: 25,
      url: function (p) { return A + '/audit-logs' + qs({ category: 'exports', q: p.q, offset: off(p), limit: p.limit }); },
      filters: [{ key: 'q', label: 'Search by person…' }],
      columns: [
        { label: 'When', render: function (a) { return esc(K.fmtDT(a.at)); } },
        { label: 'Who', render: function (a) { return esc(a.actor_email || a.user || 'system') + (a.role || a.actor_role ? '<span class="cell-sub">' + esc(K.titleCase(a.role || a.actor_role)) + '</span>' : ''); } },
        { label: 'Data', render: function (a) { var d = a.details || {}; return esc(K.titleCase(String(d.scope || a.action || '—').replace('-', ' '))); } },
        { label: 'Rows', cls: 'num', render: function (a) { var d = a.details || {}; return d.rows != null ? fmtN(d.rows) : '—'; } },
        { label: 'Platform', render: function (a) { var d = a.details || {}; return esc(d.platform ? platName(d.platform) : 'All'); } },
        { label: 'Format', render: function (a) { var d = a.details || {}; return esc(String(d.format || 'csv').toUpperCase()); } },
        { label: 'Result', render: function (a) { return pill(a.status === 'failure' || a.success === false ? 'failed' : 'ok', a.status === 'failure' || a.success === false ? 'Failed' : 'Done'); } }
      ],
      empty: { title: 'No exports yet', desc: 'Downloads from this page and the Download CSV buttons appear here.' }
    });
    var after = function (ok) { if (ok) setTimeout(function () { hist.reload(); }, 300); };
    $$('[data-quick]', root).forEach(function (b) { b.onclick = function () { download(A + '/export/' + b.getAttribute('data-quick') + '.csv', b).then(after); }; });
    var lg = $('[data-applog]', root);
    if (lg) lg.onclick = function () { download(A + '/logs/download', lg); };
    var form = $('#exForm', root);
    if (!form) return;
    var sync = function () {
      var e = EXPORTS.filter(function (x) { return x.scope === form.scope.value; })[0];
      $$('[data-ff]', form).forEach(function (f) {
        var on = e.f.indexOf(f.getAttribute('data-ff')) >= 0;
        f.classList.toggle('ops-field-off', !on);
        $$('input,select', f).forEach(function (i) { i.disabled = !on; });
      });
      $('#exHint', form).textContent = e.hint;
    };
    form.scope.onchange = sync; sync();
    form.onsubmit = function (ev) {
      ev.preventDefault();
      var e = EXPORTS.filter(function (x) { return x.scope === form.scope.value; })[0], has = function (k) { return e.f.indexOf(k) >= 0; };
      var f = form.from.value, t = form.to.value;
      if (f && t && f > t) { K.toast('"From" must be before "To"', 'warning'); return; }
      var params = { platform: has('platform') ? form.platform.value : '', q: has('q') ? form.q.value.trim() : '', from_date: has('from') ? dayStart(f) : '', to_date: has('to') ? dayEnd(t) : '' };
      if (has('only_leads') && !form.only_leads.checked) params.only_leads = 'false';
      download(A + '/export/' + e.scope + '.csv' + qs(params), $('#exGo', form)).then(after);
    };
  }

  // `#/failed` (old console link): the Failed jobs tab
  function viewFailed() { location.replace('#/jobs?tab=failed'); }

  // ════════════════════════════════════════════════════════
  //  GUIDES
  // ════════════════════════════════════════════════════════
  function gReady(sel) { return function () { var el = $(sel); return el && !$('.sa-skel', el) ? el : null; }; }
  function gClose() {
    $$('#saLayer .sa-overlay [data-x]').forEach(function (x) { x.click(); });
    $$('#saLayer .sa-drawer-wrap .modal-close').forEach(function (x) { x.click(); });
  }
  async function gOpenLead() {
    if ($('#saLayer [data-tour="lead-manage"], #saLayer .sa-drawer')) return;
    var t0 = Date.now(), tr = null;
    while (Date.now() - t0 < 5000 && !(tr = $('[data-tour="leads-table"] tbody tr.clickable'))) await new Promise(function (r) { setTimeout(r, 120); });
    if (tr) { tr.click(); var t1 = Date.now(); while (Date.now() - t1 < 4000 && (!$('#saLayer .sa-drawer') || $('#saLayer .sa-drawer .sa-skel'))) await new Promise(function (r) { setTimeout(r, 120); }); }
  }
  function defineGuides() {
    if (!window.LeadTour) return;
    var steps = function (list) { return list.map(function (s) { var own = s.before; return Object.assign({}, s, { before: async function () { if (!s.keep) gClose(); if (own) await own(); } }); }); };
    LeadTour.define('ops-jobs', { title: 'Jobs', onEnd: gClose, steps: steps([
      { route: '#/jobs', target: '[data-tour="jobs-tabs"] .sa-tabs', title: 'Every search run', body: 'All jobs lists every search run across all organizations. Failed jobs collects the runs that ended in an error, with retry. The Super Admin also gets By organization: each search with its organization, user and Apify run.' },
      { route: '#/jobs', target: '[data-tour="jobs-chips"]', title: 'Filter by status', body: 'One click shows only running, completed, failed, cancelled or queued runs. While a run is going, the list refreshes every 10 seconds.' },
      { route: '#/jobs', target: '[data-tour="jobs-table"] .sa-filters', title: 'Search and narrow down', body: 'Search by URL or run id, pick a platform or a date range. Clear filters resets everything. The filters stay in the address, so you can share the link.' },
      { route: '#/jobs', target: gReady('[data-tour="jobs-table"] [data-body]'), optional: true, title: 'Open a run', body: 'Click a row for the run report: what was collected, the Apify cost and the keyword filter. Managers can retry or cancel a run here; only the Super Admin can delete one.' },
      { route: '#/jobs?tab=failed', target: '[data-tour="failed-kpis"]', title: 'Failed jobs', body: 'How many runs failed today, this week and in total, and the most common error. Retry one run, tick several and retry them together, or retry the whole page.' }
    ]) });
    LeadTour.define('ops-leads', { title: 'Leads', onEnd: gClose, steps: steps([
      { route: '#/leads', target: '[data-tour="leads-hero"]', title: 'Leads at a glance', body: 'Every lead the AI found, how many have a phone or email, and this week\'s new ones. A platform box filters the list to that platform; click it again to clear.' },
      { route: '#/leads', target: '[data-tour="leads-table"] .sa-filters', title: 'Find the right leads', body: 'Search by name, comment, phone, email or page, and filter by platform, quality, status and dates. "Show" switches between leads only and every analysed comment.' },
      { route: '#/leads', target: gReady('[data-tour="leads-table"] [data-body]'), optional: true, title: 'Work several at once', body: 'Managers can tick leads to set their status or priority, assign them to someone, or delete them.' },
      { before: gOpenLead, keep: true, target: '#saLayer .sa-drawer', optional: true, placement: 'left', title: 'One lead in full', body: 'The comment, the contact details (copy or open them), the AI\'s reasoning, its status history, notes and follow-ups. Managers change status, priority and owner here and schedule follow-ups.' }
    ]) });
    if (K.addGuide) {
      K.addGuide({ id: 'ops-jobs', label: 'Jobs & failed jobs', description: 'Filter runs, read a run report, retry failures', route: 'jobs' });
      K.addGuide({ id: 'ops-leads', label: 'Working leads', description: 'Filter leads, bulk actions, notes and follow-ups', route: 'leads' });
    }
  }

  // ════════════════════════════════════════════════════════
  //  REGISTER
  // ════════════════════════════════════════════════════════
  function countFailed() {
    api(A + '/failed-jobs?limit=1').then(function (d) { K.setCount('failedjobs', ((d.summary || {}).today) || 0); }, function () {});
  }
  defineGuides();
  var after = document.querySelector('script[src*="super-admin-access"]') ? 'Access' : 'Revenue';
  K.register('Operations', after, [
    ['operations', 'Operations overview', 'dash', null, 'viewer'],
    ['jobs', 'Jobs', 'cpu', 'failedjobs', 'viewer'],
    ['leads', 'Leads', 'star', null, 'viewer'],
    ['platforms', 'Platforms', 'globe', null, 'viewer'],
    ['pages', 'Pages', 'file', null, 'viewer'],
    ['posts', 'Posts', 'list', null, 'viewer'],
    ['ci', 'Comments', 'msg', null, 'viewer'],
    ['exports', 'Exports', 'download', null, 'viewer']
  ], {
    operations: [viewOperations, 'Operations overview', null, 'viewer'],
    jobs: [viewJobs, 'Jobs', viewJobDetail, 'viewer'],
    failed: [viewFailed, 'Failed jobs', null, 'viewer'],
    leads: [viewLeads, 'Leads', viewLeadDetail, 'viewer'],
    platforms: [viewPlatforms, 'Platforms', viewPlatformDetail, 'viewer'],
    pages: [viewPages, 'Pages', null, 'viewer'],
    posts: [viewPosts, 'Posts', null, 'viewer'],
    ci: [viewCI, 'Comments', null, 'viewer'],
    exports: [viewExports, 'Exports', null, 'viewer']
  }, countFailed);
})();
