/* ============================================================
   LeadAI — Super Admin › API usage & limits (#/api-limits)
   LeadAI's own providers (Apify, Gemini, email, SMS, payments) with their
   meters, Gemini's "blocked until" state, the last live error and a 7-day
   history, plus every organization's own keys, tokens and plan meters.
   Backend: /api/super-admin/api-limits (Super Admin) or /api/admin/api-limits
   (platform staff: viewer reads, manager checks now). The server checks
   every call; the UI only hides what a role can't use.
   Registered through window.SAKit (same UI kit as the other modules).
   ============================================================ */
(function () {
  'use strict';
  var K = window.SAKit;
  if (!K) return;
  var api = K.api, qs = K.qs, esc = K.esc, fmtN = K.fmtN, $ = K.$, $$ = K.$$;

  (function () {
    var st = document.createElement('style');
    st.textContent = [
      '.al-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}',
      '.al-grid>*{min-width:0}',
      '@media (max-width:640px){.al-grid{grid-template-columns:minmax(0,1fr)}}',
      '.al-card{display:flex;flex-direction:column;gap:12px;border-left:3px solid var(--border)}',
      '.al-card.lv-ok{border-left-color:var(--success)}.al-card.lv-warning{border-left-color:var(--warning)}',
      '.al-card.lv-critical,.al-card.lv-exhausted,.al-card.lv-error{border-left-color:var(--danger)}',
      '.al-card-h{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}',
      '.al-card-h h3{margin:0 0 2px;display:block}',
      '.al-card-h .status-pill{flex-shrink:0}',
      '.al-msg{margin:0;font-size:13px;line-height:1.5;color:var(--text-secondary);overflow-wrap:anywhere}',
      '.al-meters{display:grid;gap:12px}',
      '.al-meter{display:grid;gap:5px;min-width:0}',
      '.al-mtop{display:flex;justify-content:space-between;gap:4px 10px;font-size:12.5px;flex-wrap:wrap}',
      '.al-mtop b{font-variant-numeric:tabular-nums;font-weight:600}',
      '.al-bar{height:8px;border-radius:999px;background:var(--surface-active);overflow:hidden}',
      '.al-bar>i{display:block;height:100%;border-radius:999px;background:var(--success)}',
      '.al-bar.lv-warning>i{background:var(--warning)}',
      '.al-bar.lv-critical>i,.al-bar.lv-exhausted>i,.al-bar.lv-error>i{background:var(--danger)}',
      '.al-meter.al-info{font-size:12px;color:var(--text-secondary)}',
      '.al-meter.al-info .al-mtop{font-size:12px}.al-meter.al-info .al-bar{height:4px}',
      '.al-meter.al-info .al-bar>i{background:var(--sa-muted)}',
      '.al-mfoot{display:flex;gap:4px 8px;flex-wrap:wrap;align-items:center;font-size:11.5px;color:var(--sa-muted)}',
      '.al-src{border:1px solid var(--border);border-radius:999px;padding:0 8px;line-height:18px;background:var(--surface);white-space:nowrap}',
      '.al-note{display:flex;gap:10px;align-items:flex-start;padding:9px 11px;border-radius:10px;border:1px solid var(--border);background:var(--surface);font-size:12.5px;line-height:1.45;overflow-wrap:anywhere}',
      '.al-note>div{min-width:0}',
      '.al-note.warn{background:var(--warning-soft);border-color:var(--warning-border)}',
      '.al-note.bad{background:var(--danger-soft);border-color:var(--danger-border)}',
      '.al-note .sa-ico{width:16px;height:16px;flex-shrink:0;margin-top:1px}',
      '.al-acts{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:auto}',
      '.al-meta{display:flex;gap:4px 14px;flex-wrap:wrap;font-size:12.5px;color:var(--text-secondary);margin:-6px 0 14px}',
      '.al-meta b{color:var(--text-primary);font-weight:600}',
      '.al-sec{display:flex;align-items:baseline;gap:6px 12px;flex-wrap:wrap;margin:22px 0 10px}',
      '.al-sec h2{margin:0;font-size:15px;font-weight:700}',
      '.al-cell{display:grid;gap:3px;min-width:0}',
      '.al-cell .status-pill{justify-self:start}',
      '.al-spark{display:block;width:100%;height:64px;color:var(--primary)}',
      '.al-spark polyline{fill:none;stroke:currentColor;stroke-width:1.8;vector-effect:non-scaling-stroke}',
      '.al-spark line{stroke-width:1;vector-effect:non-scaling-stroke;stroke-dasharray:3 3}',
      '.al-spark .w{stroke:var(--warning)}.al-spark .c{stroke:var(--danger)}',
      '.al-hist+.al-hist{margin-top:16px}',
      '.al-hist h4{margin:0 0 6px;font-size:13px;display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap}',
      '.al-table td,.al-table th{white-space:nowrap}',
      '@media (max-width:640px){.al-meta{margin-top:0}}'
    ].join('\n');
    document.head.appendChild(st);
  })();

  // ── level / formatting helpers ──────────────────────────
  var LV = { ok: ['OK', 'success'], warning: ['Warning', 'warning'], critical: ['Critical', 'danger'], exhausted: ['Limit reached', 'danger'],
    error: ['Error', 'danger'], unknown: ['Unknown', 'muted'], not_configured: ['Not set up', 'neutral'] };
  var ORDER = ['exhausted', 'critical', 'error', 'warning', 'ok', 'unknown', 'not_configured'];
  var BAD = { critical: 1, exhausted: 1, error: 1 };
  function lvPill(lv) { return K.limitPill ? K.limitPill(lv) : K.badge((LV[lv] || LV.unknown)[0], (LV[lv] || LV.unknown)[1]); }
  function root() { return K.can('super_admin') ? '/api/super-admin/api-limits' : '/api/admin/api-limits'; }
  var P = { cfg: {} };
  function thresholds() { return [Number(P.cfg.warn_percent) || 80, Number(P.cfg.critical_percent) || 95]; }
  function levelFor(pct) {
    if (pct == null) return 'ok';
    var t = thresholds();
    return pct >= 100 ? 'exhausted' : pct >= t[1] ? 'critical' : pct >= t[0] ? 'warning' : 'ok';
  }
  function toDate(v) { if (!v) return null; var d = new Date(v); return isNaN(d.getTime()) ? null : d; }
  /** "in 45 min" / "in 3 h" / "in 5 days" for a future time. */
  function until(v) {
    var d = toDate(v); if (!d) return '';
    var s = Math.round((d.getTime() - Date.now()) / 1000);
    if (s <= 0) return 'now';
    if (s < 90) return 'in ' + s + ' s';
    if (s < 5400) return 'in ' + Math.round(s / 60) + ' min';
    if (s < 172800) return 'in ' + Math.round(s / 3600) + ' h';
    return 'in ' + Math.round(s / 86400) + ' days';
  }
  var CURRENCY = /^(USD|INR|EUR|GBP|AUD|CAD|SGD|AED|JPY)$/;
  function amount(v, unit) {
    if (v == null) return '—';
    if (CURRENCY.test(unit || '')) return K.fmtMoney(v, unit);
    return fmtN(v) + (unit ? ' ' + unit : '');
  }
  /** "42 / 100 emails", "$42.10 / $49" */
  function pair(used, limit, unit) {
    return CURRENCY.test(unit || '') ? K.fmtMoney(used, unit) + ' / ' + K.fmtMoney(limit, unit) : fmtN(used) + ' / ' + fmtN(limit) + (unit ? ' ' + unit : '');
  }
  function pctText(p) { return p == null ? '' : (Math.round(p * 10) / 10) + '%'; }
  /** A /superadmin#/x link as an in-portal hash ('' when the person's role can't open that page). */
  function inLink(link) {
    if (!link) return '';
    var m = /^\/superadmin#\/?(.*)$/.exec(link);
    if (!m) return '';
    var h = '#/' + m[1], key = m[1].split('?')[0];
    return K.can(K.routeRole(key)) ? h : '';
  }
  function canCheck() { return K.can('manager'); }
  function roDis() { return canCheck() ? '' : ' disabled title="Needs the Manager role"'; }
  function who(st) {
    if (st.provider === 'apify') return 'Apify';
    if (st.provider === 'gemini') return 'Google';
    var m = /\(([^)]+)\)/.exec(st.label || '');
    return m ? m[1] : st.provider === 'sms' ? 'the SMS provider' : 'the provider';
  }
  function rateText(e) {
    var d = e && e.data && e.data.detail;
    if (e && e.status === 409 && d && d.code === 'CHECK_RUNNING') return 'A full check is already running. The page updates when you check again in a minute.';
    if (e && e.status === 429 && d && typeof d === 'object') return (d.message || 'Too many checks.') + (d.retry_after ? ' (wait ' + Math.ceil(d.retry_after) + ' s)' : '');
    return (e && e.message) || 'Check failed';
  }

  // ── meters ──────────────────────────────────────────────
  function srcChips(m, st) {
    var s = m.source === 'provider_api' ? ['From ' + who(st), 'Read from ' + who(st) + '\'s own API'] :
      m.source === 'configured' ? ['Your setting', 'A value an admin typed in'] : ['Counted by LeadAI', 'LeadAI counts its own calls (the provider has no usage API for this)'];
    var out = '<span class="al-src" title="' + esc(s[1]) + '">' + esc(s[0]) + '</span>';
    if (m.limit_source === 'configured' && m.limit != null) out += '<span class="al-src" title="The limit comes from Global Settings">Limit: your setting</span>';
    return out;
  }
  function settingsHash(st) {
    var h = st.provider === 'gemini' ? '#/settings?tab=ai' : inLink(P.cfg.settings_link) || '#/settings?tab=notifications';
    return K.can(K.routeRole('settings')) ? h : '';
  }
  /** One meter: role=meter with a text equivalent; informational meters are smaller and never coloured. */
  function meter(m, st) {
    var info = !!m.informational;
    var has = m.limit != null && m.used != null;
    var pct = m.percent != null ? Number(m.percent) : (has && m.limit ? m.used * 100 / m.limit : null);
    var lv = info ? 'info' : (m.level || levelFor(pct));
    var val, words;
    if (has) {
      val = pair(m.used, m.limit, m.unit) + (pct != null ? ' · ' + pctText(pct) : '');
      words = m.label + ': ' + amount(m.used, m.unit) + ' of ' + amount(m.limit, m.unit) + ' used' + (pct != null ? ' (' + pctText(pct) + ')' : '') + (info ? '' : ', ' + (LV[lv] || LV.ok)[0]);
    } else if (m.remaining != null && m.used == null) {
      val = amount(m.remaining, m.unit) + ' left'; words = m.label + ': ' + val;
    } else {
      val = amount(m.used, m.unit) + (m.limit == null ? ' · no limit set' : ''); words = m.label + ': ' + val;
    }
    var foot = srcChips(m, st);
    if (m.resets_at) foot += '<span title="' + esc(K.fmtDT(m.resets_at)) + '">resets ' + esc(until(m.resets_at)) + '</span>';
    if (m.warn_below != null) foot += '<span>warns below ' + esc(amount(m.warn_below, m.unit)) + '</span>';
    if (m.limit == null && m.used != null && st.scope === 'platform' && (st.provider === 'gemini' || st.provider === 'email')) {
      var sh = settingsHash(st);
      foot += sh ? '<a class="sa-link" href="' + esc(sh) + '">Limit not set — set it in Global Settings</a>' : '<span>Limit not set (Global Settings)</span>';
    }
    var bar = has ? '<div class="al-bar lv-' + esc(lv) + '" role="meter" aria-label="' + esc(m.label) + '" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' +
      Math.round(Math.max(0, Math.min(100, pct || 0))) + '" aria-valuetext="' + esc(words) + '"><i style="width:' + Math.max(0, Math.min(100, pct || 0)).toFixed(1) + '%"></i></div>' : '';
    return '<div class="al-meter' + (info ? ' al-info' : '') + '"><div class="al-mtop"><span>' + esc(m.label) + (info ? ' <span class="sa-muted">(for information)</span>' : '') + '</span><b' + (has ? ' aria-hidden="true"' : '') + '>' + esc(val) + '</b></div>' +
      bar + (has ? '' : '<span class="sr-only">' + esc(words) + '</span>') + '<div class="al-mfoot">' + foot + '</div></div>';
  }
  var BLOCK_TEXT = {
    daily: function (b) { return 'Daily quota used up — resumes at 00:00 Pacific' + (b.until ? ' (' + until(b.until) + ')' : ''); },
    minute: function (b) { return 'Per-minute quota hit — resumes ' + (b.until ? until(b.until) : 'shortly'); },
    rate: function (b) { return 'Rate-limited by Google — resumes ' + (b.until ? until(b.until) : 'shortly'); },
    credit: function () { return 'Prepaid credit used up — resumes once credit is added in Google AI Studio → Billing'; },
    billing: function () { return 'Billing is off for the Google project — resumes once billing is turned on'; },
    auth: function () { return 'Google rejected the key — resumes once the key is replaced'; },
    region: function () { return 'Gemini is not available where the request came from — resumes once the server runs in a supported region or uses a key that allows it'; }
  };
  function blockHtml(b) {
    if (!b || !b.kind) return '';
    var t = (BLOCK_TEXT[b.kind] || function () { return 'Paused by Google (' + b.kind + ')'; })(b);
    var hard = b.kind !== 'minute' && b.kind !== 'rate';
    return '<div class="al-note ' + (hard ? 'bad' : 'warn') + '" role="note">' + K.icon('clock') + '<div><b>' + esc(t) + '</b>' +
      (b.until ? '<div class="sa-small">Until ' + esc(K.fmtDT(b.until)) + ' your time</div>' : '') +
      ((b.quota_ids || []).length ? '<div class="sa-small sa-muted">Quota: <span class="sa-mono">' + esc(b.quota_ids.join(', ')) + '</span></div>' : '') +
      (b.at ? '<div class="sa-small sa-muted">Since ' + esc(K.ago(b.at)) + '</div>' : '') + '</div></div>';
  }
  function eventHtml(e, st) {
    if (!e || !e.message) return '';
    var exp = toDate(e.expires_at);
    if (exp && exp.getTime() <= Date.now()) return '';   // a transient warning that has passed
    var same = st && st.message === e.message; // the card message already says it
    var bits = [esc(K.ago(e.at))];
    if (same) bits.push('as described above');
    if (exp) bits.push('clears ' + esc(until(e.expires_at)) + ' unless it happens again');
    if (e.admins_notified) bits.push('organization admins were told');
    return '<div class="al-note' + (BAD[e.level] ? ' bad' : ' warn') + '">' + K.icon('alert') + '<div><b>Last live error</b> <span class="sa-small sa-muted">· ' + bits.join(' · ') + '</span>' +
      (same ? '' : '<div>' + esc(e.message) + '</div>') + (e.code ? '<div class="sa-small sa-muted sa-mono">' + esc(e.code) + '</div>' : '') + '</div></div>';
  }
  /** A provider status as a card (platform page, organization details drawer). */
  function card(st, o) {
    o = o || {};
    var lv = st.level || 'unknown', help = o.help === false ? '' : inLink(st.help_link);
    var metrics = (st.metrics || []).slice().sort(function (a, b) { return (a.informational ? 1 : 0) - (b.informational ? 1 : 0); });
    var cyc = st.cycle && (st.cycle.start_at || st.cycle.end_at) ? '<div class="sa-small sa-muted">Usage cycle ' + esc(K.fmtDate(st.cycle.start_at)) + ' – ' + esc(K.fmtDate(st.cycle.end_at)) + '</div>' : '';
    var acts = '';
    if (lv === 'not_configured' && help) acts += '<a class="btn btn-primary btn-sm" href="' + esc(help) + '">Set up</a>';
    else if (help) acts += '<a class="btn btn-' + (lv === 'ok' ? 'ghost' : 'secondary') + ' btn-sm" href="' + esc(help) + '">' + (lv === 'ok' ? 'Manage' : 'Fix it') + '</a>';
    if (o.history !== false && st.configured !== false) acts += '<button type="button" class="btn btn-ghost btn-sm" data-hist="' + esc(st.provider) + '" data-scope="' + esc(st.scope) + '">' + K.icon('chart') + 'History</button>';
    return '<article class="sa-card al-card lv-' + esc(lv) + '" data-provider="' + esc(st.provider) + '" aria-label="' + esc(st.label + ': ' + (LV[lv] || LV.unknown)[0]) + '">' +
      '<div class="al-card-h"><div><h3>' + esc(st.label || st.provider) + (st.plan_name ? ' <span class="sa-muted" style="font-weight:500">· ' + esc(st.plan_name) + '</span>' : '') + '</h3>' +
      '<div class="sa-small sa-muted">' + (st.checked_at ? 'Checked ' + esc(K.ago(st.checked_at)) : 'Not checked yet') + '</div>' + cyc +
      (st.last_bad_at && lv === 'ok' ? '<div class="sa-small sa-muted" title="' + esc(K.fmtDT(st.last_bad_at)) + '">Last problem ' + esc(K.ago(st.last_bad_at)) + (st.ok_streak ? ' · fine for ' + fmtN(st.ok_streak) + ' check' + (st.ok_streak === 1 ? '' : 's') : '') + '</div>' : '') +
      '</div>' + lvPill(lv) + '</div>' +
      (st.message ? '<p class="al-msg">' + esc(st.message) + '</p>' : '') + blockHtml(st.block) + eventHtml(st.event, st) +
      (metrics.length ? '<div class="al-meters">' + metrics.map(function (m) { return meter(m, st); }).join('') + '</div>' : '') +
      (acts ? '<div class="al-acts">' + acts + '</div>' : '') + '</article>';
  }

  // ── history (7 days) ────────────────────────────────────
  function spark(points, label) {
    var t = thresholds(), n = points.length;
    var t0 = points[0].t, t1 = points[n - 1].t, span = Math.max(1, t1 - t0);
    var xy = points.map(function (p) { return ((p.t - t0) * 300 / span).toFixed(1) + ',' + (60 - Math.min(100, p.v) * 0.56 - 2).toFixed(1); }).join(' ');
    var vals = points.map(function (p) { return p.v; }), peak = Math.max.apply(null, vals);
    var y = function (v) { return (60 - v * 0.56 - 2).toFixed(1); };
    return '<svg class="al-spark" viewBox="0 0 300 60" preserveAspectRatio="none" role="img" aria-label="' +
      esc(label + ' over 7 days: ' + pctText(vals[0]) + ' at the start, ' + pctText(vals[n - 1]) + ' now, peak ' + pctText(peak)) + '">' +
      '<line class="w" x1="0" x2="300" y1="' + y(t[0]) + '" y2="' + y(t[0]) + '"/><line class="c" x1="0" x2="300" y1="' + y(t[1]) + '" y2="' + y(t[1]) + '"/>' +
      (n > 1 ? '<polyline points="' + xy + '"/>' : '<circle cx="150" cy="' + y(vals[0]) + '" r="2.5" fill="currentColor"/>') + '</svg>';
  }
  var RANKV = { not_configured: 0, unknown: 1, ok: 2, warning: 3, critical: 4, error: 5, exhausted: 6 };
  function openHistory(st) {
    K.openDrawer('History — ' + (st.label || st.provider), async function (body) {
      var d = await api(root() + '/history' + qs({ provider: st.provider, scope: st.scope, days: 7 }));
      var items = d.items || [];
      if (!items.length) { body.innerHTML = K.emptyState('No history yet', 'A point is recorded at every check and kept for 30 days.'); return; }
      var labels = {}, info = {};
      (st.metrics || []).forEach(function (m) { labels[m.key] = m.label; if (m.informational) info[m.key] = true; });
      var series = {}, days = {};
      items.forEach(function (it) {
        var day = String(it.at || '').slice(0, 10), dd = days[day] = days[day] || { n: 0, lv: null, peak: {} };
        dd.n++;
        if (!dd.lv || (RANKV[it.level] || 0) > (RANKV[dd.lv] || 0)) dd.lv = it.level;
        (it.metrics || []).forEach(function (m) {
          if (m.percent == null || info[m.key]) return;  // momentary meters (memory, runs) are no trend
          (series[m.key] = series[m.key] || []).push({ t: new Date(it.at).getTime(), v: Number(m.percent) });
          dd.peak[m.key] = Math.max(dd.peak[m.key] || 0, Number(m.percent));
        });
      });
      var keys = Object.keys(series);
      var dayKeys = Object.keys(days).sort().reverse();
      body.innerHTML = '<p class="sa-small sa-muted" style="margin-top:0">' + fmtN(items.length) + ' checks in the last 7 days. Dashed lines: warning (' + thresholds()[0] + '%) and critical (' + thresholds()[1] + '%).</p>' +
        (keys.length ? keys.map(function (k) {
          var s = series[k], last = s[s.length - 1].v;
          return '<div class="al-hist"><h4><span>' + esc(labels[k] || k) + '</span><span class="sa-mono">' + esc(pctText(last)) + ' now</span></h4>' + spark(s, labels[k] || k) + '</div>';
        }).join('') : '<p class="sa-small sa-muted">This service has no percentage meter, so only its level is shown.</p>') +
        '<h4 class="sa-section" style="margin-bottom:6px">By day</h4><div class="sa-table-wrap"><table class="sa-table al-table"><caption class="sr-only">Worst level and peak use per day</caption><thead><tr><th scope="col">Day</th><th scope="col">Worst level</th>' +
        keys.map(function (k) { return '<th scope="col" class="num">' + esc(labels[k] || k) + '</th>'; }).join('') + '<th scope="col" class="num">Checks</th></tr></thead><tbody>' +
        dayKeys.map(function (day) {
          var dd = days[day];
          return '<tr><td>' + esc(K.fmtDate(day + 'T12:00:00')) + '</td><td>' + lvPill(dd.lv || 'unknown') + '</td>' +
            keys.map(function (k) { return '<td class="num">' + (dd.peak[k] != null ? esc(pctText(dd.peak[k])) : '—') + '</td>'; }).join('') + '<td class="num">' + fmtN(dd.n) + '</td></tr>';
        }).join('') + '</tbody></table></div>';
    });
  }
  function bindCards(el, statuses) {
    $$('[data-hist]', el).forEach(function (b) {
      if (b._al) return; b._al = true;
      b.onclick = function () {
        var p = b.getAttribute('data-hist'), sc = b.getAttribute('data-scope');
        var st = statuses.filter(function (s) { return s.provider === p && s.scope === sc; })[0] || { provider: p, scope: sc };
        openHistory(st);
      };
    });
  }

  // ── organizations table ─────────────────────────────────
  function find(row, p) { return (row.statuses || []).filter(function (s) { return s.provider === p; })[0]; }
  function mainMetric(s) {
    var ms = (s.metrics || []).filter(function (m) { return !m.informational && m.percent != null; });
    ms.sort(function (a, b) { return b.percent - a.percent; });
    return ms[0];
  }
  function cell(s, empty) {
    if (!s) return '<span class="sa-muted sa-small">' + esc(empty || '—') + '</span>';
    var m = mainMetric(s), sub;
    if (s.provider === 'tokens' && (s.metrics || [])[0]) { var t = s.metrics[0]; sub = amount(t.remaining, '') + ' of ' + amount(t.limit, '') + ' left'; }
    else if (m) sub = m.label + ': ' + pctText(m.percent);
    else sub = K.short(s.message || '', 48);
    return '<div class="al-cell" title="' + esc(s.message || '') + '">' + lvPill(s.level || 'unknown') + '<span class="cell-sub">' + esc(sub) + '</span></div>';
  }
  function orgTable(el, first, q) {
    var st = { level: q.level || '', q: q.q || '', page: +q.page || 1 }, data = first, seq = 0, timer;
    var canOrg = K.can(K.routeRole('organizations'));
    function sync() {
      try {
        var h = location.hash.split('?'), p = new URLSearchParams(h[1] || '');
        ['level', 'q', 'page'].forEach(function (k) { if (st[k] && !(k === 'page' && st.page === 1)) p.set(k, st[k]); else p.delete(k); });
        var s = p.toString(); history.replaceState(null, '', h[0] + (s ? '?' + s : ''));
      } catch (e) { /* ignore */ }
    }
    async function load() {
      var my = ++seq, body = $('[data-al-body]', el);
      if (body) { body.style.opacity = '.55'; body.setAttribute('aria-busy', 'true'); }
      try { data = await api(root() + qs({ level: st.level, q: st.q, page: st.page, limit: 25 })); }
      catch (e) { if (my === seq && body) { body.style.opacity = ''; body.removeAttribute('aria-busy'); body.innerHTML = K.errorState(e, load); } return; }
      if (my !== seq || !el.isConnected) return;
      sync(); paint(false);
    }
    function paint(full) {
      var o = (data && data.organizations) || { summary: {}, items: [], total: 0, page: 1, pages: 0 };
      var sm = o.summary || {}, all = Object.keys(sm).reduce(function (a, k) { return a + (sm[k] || 0); }, 0);
      var chips = '<div class="sa-chips" role="group" aria-label="Filter by level" style="margin:0"><button type="button" class="sa-chip' + (!st.level ? ' active' : '') + '" data-lv="" aria-pressed="' + !st.level + '">All<b>' + fmtN(all) + '</b></button>' +
        ORDER.filter(function (k) { return sm[k] || st.level === k; }).map(function (k) {
          return '<button type="button" class="sa-chip' + (st.level === k ? ' active' : '') + '" data-lv="' + k + '" aria-pressed="' + (st.level === k) + '">' + esc(LV[k][0]) + '<b>' + fmtN(sm[k] || 0) + '</b></button>';
        }).join('') + '</div>';
      var rows = o.items || [];
      var table = !rows.length ? K.emptyState(st.level || st.q ? 'No organizations match' : 'No organizations checked yet',
        st.level || st.q ? 'Try another level or search.' : 'Organizations appear after the first check. Use "Check all organizations".') :
        '<div class="sa-table-wrap"><table class="sa-table sa-pk-table"><caption class="sr-only">Organizations by worst API limit level</caption><thead><tr><th scope="col">Organization</th><th scope="col">Worst level</th><th scope="col">Own Apify</th><th scope="col">Own Gemini</th><th scope="col">Tokens</th><th scope="col">Plan</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead><tbody>' +
        rows.map(function (r, i) {
          var plan = find(r, 'plan');
          return '<tr data-i="' + i + '"><td data-label="Organization"><span class="cell-main">' + (canOrg ? '<a class="sa-link" href="#/organizations/' + esc(r.organization_id) + '">' + esc(r.name) + '</a>' : esc(r.name)) + '</span>' +
            '<span class="cell-sub">' + (r.organization_status ? K.pill(r.organization_status) : '') + '</span></td>' +
            '<td data-label="Worst level">' + lvPill(r.level) + '</td>' +
            '<td data-label="Own Apify">' + cell(find(r, 'apify'), 'LeadAI provides') + '</td>' +
            '<td data-label="Own Gemini">' + cell(find(r, 'gemini'), 'LeadAI provides') + '</td>' +
            '<td data-label="Tokens">' + cell(find(r, 'tokens'), 'Not metered') + '</td>' +
            '<td data-label="Plan">' + (plan && plan.plan_name ? '<span class="cell-sub">' + esc(plan.plan_name) + '</span>' : '') + cell(plan, 'No plan meters') + '</td>' +
            '<td data-label="Actions"><div class="row-actions" style="justify-content:flex-start;flex-wrap:wrap"><button type="button" class="btn btn-secondary btn-xs" data-chk="' + i + '"' + roDis() + '>Check now</button>' +
            '<button type="button" class="btn btn-ghost btn-xs" data-det="' + i + '">Details</button></div></td></tr>';
        }).join('') + '</tbody></table></div>';
      var pages = o.pages || 1;
      var pager = rows.length ? '<div class="sa-pager"><span>' + fmtN(o.total) + ' organization' + (o.total === 1 ? '' : 's') + '</span><nav class="sa-row" aria-label="Pagination">' +
        '<button type="button" class="btn btn-secondary btn-xs" data-pg="-1"' + (st.page <= 1 ? ' disabled' : '') + '>‹ Prev</button><span class="sa-small" aria-current="page">Page ' + st.page + ' / ' + pages + '</span>' +
        '<button type="button" class="btn btn-secondary btn-xs" data-pg="1"' + (st.page >= pages ? ' disabled' : '') + '>Next ›</button></nav></div>' : '';
      if (full) {
        el.innerHTML = '<div class="sa-filters" data-tour="al-org-filters">' + chips.replace('style="margin:0"', 'style="margin:0" data-al-chips') +
          '<div class="sa-q sa-grow"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg><label class="sr-only" for="alQ">Search organizations</label><input class="form-input" type="search" id="alQ" placeholder="Search organizations…" value="' + esc(st.q) + '"></div></div>' +
          '<div data-al-body>' + table + pager + '</div>';
        $('#alQ', el).addEventListener('input', function () {
          var v = this.value.trim(); clearTimeout(timer);
          timer = setTimeout(function () { st.q = v; st.page = 1; load(); }, 300);
        });
      } else {
        var c = $('[data-al-chips]', el); if (c) c.outerHTML = chips.replace('style="margin:0"', 'style="margin:0" data-al-chips');
        var b = $('[data-al-body]', el); b.style.opacity = ''; b.removeAttribute('aria-busy'); b.innerHTML = table + pager;
      }
      $$('[data-lv]', el).forEach(function (b) { b.onclick = function () { st.level = b.getAttribute('data-lv'); st.page = 1; load(); }; });
      $$('[data-pg]', el).forEach(function (b) { b.onclick = function () { st.page = Math.max(1, st.page + Number(b.getAttribute('data-pg'))); load(); }; });
      $$('[data-det]', el).forEach(function (b) {
        b.onclick = function () {
          var r = rows[+b.getAttribute('data-det')];
          K.openDrawer(r.name + ' — API limits', function (body) {
            body.innerHTML = '<p class="sa-small sa-muted" style="margin-top:0">Own keys (when the organization brings its own), LeadAI tokens and plan meters. Worst level: ' + lvPill(r.level) + '</p>' +
              '<div class="al-grid" style="grid-template-columns:minmax(0,1fr)">' + (r.statuses || []).map(function (s) { return card(s, { help: false }); }).join('') + '</div>' +
              (canOrg ? '<p class="sa-section"><a class="btn btn-secondary btn-sm" href="#/organizations/' + esc(r.organization_id) + '">Open organization</a></p>' : '');
            bindCards(body, r.statuses || []);
          });
        };
      });
      $$('[data-chk]', el).forEach(function (b) {
        b.onclick = async function () {
          var r = rows[+b.getAttribute('data-chk')];
          try {
            await K.busy(b, function () { return api(root() + '/refresh', { method: 'POST', body: { scope: r.organization_id } }); });
            K.toast('Checked ' + r.name);
            load();
          } catch (e) { K.toast(rateText(e), 'error'); }
        };
      });
    }
    paint(true);
    return { reload: load };
  }

  // ── the page ────────────────────────────────────────────
  async function viewApiLimits(rootEl, q) {
    var d = await api(root() + qs({ level: q.level, q: q.q, page: q.page, limit: 25 }));
    P.cfg = d.settings || {};
    var cfg = P.cfg, mgr = canCheck();
    var setLink = K.can(K.routeRole('settings')) ? inLink(cfg.settings_link) || '#/settings?tab=notifications' : '';
    rootEl.innerHTML = '<div data-tour="al-head">' + K.header('API usage & limits', 'How much of each outside service LeadAI and its customers have used, and how close each is to its limit. Warnings go out at ' + (cfg.warn_percent || 80) + '%, urgent alerts at ' + (cfg.critical_percent || 95) + '%.',
      '<button type="button" class="btn btn-primary btn-sm" id="alCheck"' + roDis() + '>' + K.icon('refresh') + 'Check now</button>' +
      '<span class="sa-rel"><button type="button" class="btn btn-secondary btn-sm" id="alMore" aria-haspopup="true" aria-expanded="false" aria-controls="alMorePop"' + roDis() + '>More checks ▾</button>' +
      '<div class="sa-pop sa-menu" id="alMorePop" hidden role="menu"><button type="button" class="sa-pop-item" role="menuitem" id="alAll"><span><b>Check all organizations</b><small>Own Apify keys, tokens and plan meters of every working organization</small></span></button></div></span>' +
      (setLink ? '<a class="btn btn-ghost btn-sm" href="' + esc(setLink) + '">' + K.icon('gear') + 'Alert settings</a>' : '')) + '</div>' +
      '<div class="al-meta" id="alMeta" aria-live="polite"></div>' +
      (cfg.enabled === false ? '<div class="alert alert-warning" style="margin-bottom:14px"><div class="alert-body"><div class="alert-title">Background checks are off</div>Nothing is checked or alerted on its own; the numbers below are from the last check. ' +
        (setLink ? '<a class="sa-link" href="' + esc(setLink) + '">Turn on "Watch API usage and limits" in Global Settings →</a>' : 'Ask the Super Admin to turn on "Watch API usage and limits" in Global Settings.') + '</div></div>' : '') +
      '<div class="al-sec"><h2 id="alPlatT">LeadAI\'s services</h2><span class="sa-small sa-muted">The keys and accounts LeadAI provides to every customer</span></div>' +
      '<div class="al-grid" id="alPlat" data-tour="al-platform" role="region" aria-labelledby="alPlatT"></div>' +
      '<div class="al-sec"><h2 id="alOrgT">Organizations</h2><span class="sa-small sa-muted">Their own Apify / Gemini keys, LeadAI tokens and plan meters (worst first)</span></div>' +
      '<div id="alOrgs" data-tour="al-orgs" role="region" aria-labelledby="alOrgT"></div>';
    function meta() {
      var el = $('#alMeta', rootEl); if (!el) return;
      el.innerHTML = '<span>Last checked <b>' + (d.checked_at ? esc(K.ago(d.checked_at)) : 'never') + '</b>' + (d.checked_at ? ' <span class="sa-muted">(' + esc(K.fmtDT(d.checked_at)) + ')</span>' : '') + '</span>' +
        (cfg.enabled === false ? '<span>Next check <b>off</b></span>' : d.next_check_at ? '<span>Next check <b>' + esc(until(d.next_check_at)) + '</b></span>' : '') +
        '<span>Every <b>' + fmtN(cfg.check_minutes || 15) + ' min</b></span>' + (mgr ? '' : '<span>' + esc(K.roleName()) + ': read-only</span>');
    }
    function plat() {
      var el = $('#alPlat', rootEl), items = d.platform || [];
      el.innerHTML = items.length ? items.map(function (s) { return card(s); }).join('') :
        '<div class="sa-card">' + K.emptyState('Not checked yet', 'Use "Check now" to read the usage and limits of LeadAI\'s providers.') + '</div>';
      bindCards(el, items);
    }
    meta(); plat();
    var orgs = orgTable($('#alOrgs', rootEl), d, q);
    var more = $('#alMore', rootEl), pop = $('#alMorePop', rootEl);
    more.onclick = function (e) {
      e.stopPropagation(); pop.hidden = !pop.hidden; more.setAttribute('aria-expanded', String(!pop.hidden));
      if (!pop.hidden) $('.sa-pop-item', pop).focus();
    };
    $('#alCheck', rootEl).onclick = async function () {
      var b = this;
      try {
        var r = await K.busy(b, function () { return api(root() + '/refresh', { method: 'POST', body: { scope: 'platform' } }); });
        d.platform = r.platform || d.platform; d.checked_at = r.checked_at || d.checked_at; d.next_check_at = r.next_check_at || d.next_check_at;
        meta(); plat(); K.toast('LeadAI\'s services checked');
      } catch (e) { K.toast(rateText(e), 'error'); }
    };
    $('#alAll', rootEl).onclick = async function () {
      pop.hidden = true; more.setAttribute('aria-expanded', 'false');
      try {
        var r = await K.busy(more, function () { return api(root() + '/refresh', { method: 'POST', body: { scope: 'all' } }); });
        d.platform = r.platform || d.platform; d.checked_at = r.checked_at || d.checked_at; d.next_check_at = r.next_check_at || d.next_check_at;
        meta(); plat(); orgs.reload();
        K.toast('Checked ' + fmtN(r.organizations_checked || 0) + ' organization' + (r.organizations_checked === 1 ? '' : 's'));
      } catch (e) { K.toast(rateText(e), 'error'); }
    };
  }

  // nav count: LeadAI services and organizations at critical / error / limit reached
  function count() {
    api(root() + '?limit=1').then(function (d) {
      var n = (d.platform || []).filter(function (s) { return BAD[s.level]; }).length, sm = (d.organizations || {}).summary || {};
      K.setCount('apilimits', n + (sm.critical || 0) + (sm.exhausted || 0) + (sm.error || 0));
    }).catch(function () { /* the page shows errors */ });
  }

  K.register('Platform', null, [['api-limits', 'API limits', 'activity', 'apilimits', 'viewer']],
    { 'api-limits': [viewApiLimits, 'API usage & limits', null, 'viewer'] }, count);

  // ── guide ───────────────────────────────────────────────
  if (window.LeadTour) {
    var end = function () { $$('#saLayer .sa-drawer-wrap .modal-close').forEach(function (x) { x.click(); }); };
    window.LeadTour.define('sa-limits', { title: 'API usage & limits', onEnd: end, steps: [
      { route: '#/api-limits', target: '[data-tour="al-head"]', title: 'Before anything runs out',
        body: 'LeadAI checks how much of Apify, Gemini, email and SMS it has used, and every organization\'s own keys, tokens and plan, in the background. You get a warning before a limit is reached and an urgent alert just before.\n\nCheck now reads the numbers again at once; Alert settings sets the thresholds and who is told.' },
      { route: '#/api-limits', target: '[data-tour="al-platform"]', title: 'LeadAI\'s services',
        body: 'One card per service LeadAI provides. Green is fine, amber is a warning, red means it is (almost) used up.\n\nEach bar says where its number comes from: read from the provider, counted by LeadAI, or a limit you typed in. History shows the last 7 days.' },
      { route: '#/api-limits', target: '[data-tour="al-orgs"]', title: 'Organizations',
        body: 'Customers that bring their own keys, and everyone\'s tokens and plan meters, worst first. Filter by level, search by name, check one organization now or open its details.' }
    ] });
    if (K.addGuide) K.addGuide({ id: 'sa-limits', label: 'API usage & limits', description: 'Provider limits, warnings and organizations close to a limit', route: 'api-limits' });
  }
})();
