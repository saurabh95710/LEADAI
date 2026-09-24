/* ============================================================
   LeadAI — Super Admin control center (/superadmin)
   Vanilla JS, hash routing, design system: /static/design/*.
   Every interpolated value goes through esc(); the backend enforces
   all permissions (anything here is cosmetic).
   ============================================================ */
(function () {
  'use strict';

  var UI = window.LeadAIUI || {};
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var S = { me: null, timers: [], navToken: 0, health: null, flagsCache: null };

  // ── formatting ────────────────────────────────────────────
  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function fmtN(n) {
    var v = Number(n || 0);
    return isFinite(v) ? v.toLocaleString(undefined, { maximumFractionDigits: 2 }) : '—';
  }
  function fmtMoney(v, cur) {
    var n = Number(v || 0);
    try {
      return new Intl.NumberFormat(undefined, { style: 'currency', currency: cur || 'USD',
        maximumFractionDigits: n % 1 ? 2 : 0 }).format(n);
    } catch (e) { return n.toFixed(2) + ' ' + (cur || ''); }
  }
  function toDate(v) {
    if (v == null || v === '') return null;
    var d = typeof v === 'number' ? new Date(v < 1e12 ? v * 1000 : v) : new Date(v);
    return isNaN(d.getTime()) ? null : d;
  }
  function fmtDate(v) { var d = toDate(v); return d ? d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '—'; }
  function fmtDT(v) { var d = toDate(v); return d ? d.toLocaleString(undefined, { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'; }
  function ago(v) {
    var d = toDate(v); if (!d) return '—';
    var s = Math.round((Date.now() - d.getTime()) / 1000);
    if (s < 0) { s = -s; return s < 3600 ? 'in ' + Math.round(s / 60) + 'm' : s < 86400 ? 'in ' + Math.round(s / 3600) + 'h' : 'in ' + Math.round(s / 86400) + 'd'; }
    if (s < 60) return 'just now';
    if (s < 3600) return Math.round(s / 60) + 'm ago';
    if (s < 86400) return Math.round(s / 3600) + 'h ago';
    if (s < 86400 * 30) return Math.round(s / 86400) + 'd ago';
    return fmtDate(d);
  }
  function short(id, n) { id = String(id || ''); return id.length > (n || 10) ? id.slice(0, n || 10) + '…' : id; }
  function titleCase(s) { return String(s || '').replace(/_/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); }); }
  function isoDay(d) { return d.toISOString().slice(0, 10); }

  /* Canonical status → tone mapping (single source for this portal; mirrors the Step 3
     design-system contract in components.css "STATUS PILLS"). */
  var STATUS_TONE = {
    // success
    active: 'success', completed: 'success', success: 'success', paid: 'success', confirmed: 'success',
    done: 'success', succeeded: 'success', sent: 'success', converted: 'success', published: 'success',
    ok: 'success', approved: 'success', extended: 'success', resolved: 'success', enabled: 'success',
    // running (info + animated dot)
    running: 'running', processing: 'running', in_progress: 'running', cancelling: 'running',
    // warning
    pending: 'warning', pending_payment: 'warning', payment_received: 'warning', pending_admin_confirmation: 'warning',
    draft: 'warning', invited: 'warning', queued: 'warning', warn: 'warning', medium: 'warning', pending_approval: 'warning',
    open: 'warning', refunded: 'warning',
    // danger
    failed: 'danger', error: 'danger', rejected: 'danger', refund_due: 'danger', failure: 'danger', high: 'danger',
    critical: 'danger', urgent: 'danger', down: 'danger', err: 'danger', past_due: 'danger',
    // neutral-dark (suspended = danger-soft)
    suspended: 'suspended', locked: 'suspended',
    deactivated: 'neutral', archived: 'neutral', cancelled: 'neutral', expired: 'neutral', revoked: 'neutral',
    disabled: 'neutral', inactive: 'neutral', removed: 'neutral', closed: 'neutral', void: 'neutral',
    // demo / trial → primary (violet)
    demo: 'primary', trial: 'primary', trialing: 'primary',
    // informational badges (not a lifecycle state)
    info: 'info', low: 'info', normal: 'muted', muted: 'muted', hidden: 'muted', tracking: 'muted'
  };
  var PILL_LABEL = { pending_admin_confirmation: 'Awaiting confirmation', pending_payment: 'Pending payment',
    payment_received: 'Payment received', member: 'User', disabled: 'Deactivated', error: 'Failed', refund_due: 'Refund due' };
  function toneOf(status) { return STATUS_TONE[String(status || '').toLowerCase()] || 'muted'; }
  // Rendered with the shared design-system pill (components.css .status-pill): data-status carries the
  // canonical colour; the explicit tone class covers portal-specific statuses (open, locked, extended…).
  var TONE_CLASS = { success: 'tone-success', running: 'tone-info is-live', info: 'tone-info', warning: 'tone-warning', danger: 'tone-danger',
    suspended: 'tone-danger', neutral: 'tone-neutral', primary: 'tone-primary', muted: 'tone-neutral' };
  function pill(status, label) {
    if (status == null || status === '') return '<span class="sa-muted">—</span>';
    var s = String(status).toLowerCase();
    return '<span class="status-pill ' + TONE_CLASS[toneOf(s)] + '" data-status="' + esc(s) + '">' + esc(label || PILL_LABEL[s] || titleCase(s)) + '</span>';
  }
  /** Non-status tag (e.g. "Super Admin", "Default"): tone is one of the STATUS_TONE tones. */
  function badge(label, tone) { return '<span class="status-pill ' + (TONE_CLASS[tone] || 'tone-info') + '">' + esc(label) + '</span>'; }
  function roleLabel(r) { return { owner: 'Admin (Owner)', admin: 'Admin', manager: 'Manager', member: 'User', viewer: 'Viewer' }[r] || titleCase(r); }

  // ── API ───────────────────────────────────────────────────
  function qs(obj) {
    var p = new URLSearchParams();
    Object.keys(obj || {}).forEach(function (k) {
      var v = obj[k]; if (v === '' || v == null || v === false && k !== 'success') return; p.set(k, v);
    });
    var s = p.toString(); return s ? '?' + s : '';
  }
  async function api(path, opts) {
    opts = opts || {};
    var init = { method: opts.method || 'GET', credentials: 'same-origin', headers: { Accept: 'application/json' } };
    if (opts.body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.body); }
    var res;
    try { res = await fetch(path, init); } catch (e) {
      var ne = new Error('Network error — check your connection and try again.'); ne.status = 0; throw ne;
    }
    var data = {};
    try { data = await res.json(); } catch (e) { data = {}; }
    if (res.status === 401) {
      location.href = '/login?superadmin=1';
      var ue = new Error('Your session expired. Redirecting to sign in…'); ue.status = 401; throw ue;
    }
    if (!res.ok) {
      var msg = UI.errorMessage ? UI.errorMessage(data, 'Request failed (' + res.status + ')') : ('Request failed (' + res.status + ')');
      var err = new Error(msg); err.status = res.status; err.data = data; throw err;
    }
    return data || {};
  }

  // ── toast ─────────────────────────────────────────────────
  function toast(msg, type) {
    var c = document.getElementById('toast-container');
    var el = document.createElement('div');
    el.className = 'toast ' + (type || 'success');
    el.setAttribute('role', type === 'error' ? 'alert' : 'status');
    var m = document.createElement('span'); m.className = 'toast-msg'; m.textContent = msg;
    var b = document.createElement('button'); b.className = 'toast-close'; b.type = 'button'; b.setAttribute('aria-label', 'Dismiss'); b.textContent = '✕';
    b.onclick = function () { el.remove(); };
    el.appendChild(m); el.appendChild(b); c.appendChild(el);
    setTimeout(function () { el.remove(); }, type === 'error' ? 7000 : 4200);
  }

  // ── states ────────────────────────────────────────────────
  function skeleton(kind) {
    if (kind === 'rows') return '<div class="sa-skel" aria-busy="true" aria-label="Loading">' + '<div class="skeleton"></div>'.repeat(6) + '</div>';
    return '<div class="sa-skel" aria-busy="true" aria-label="Loading"><div class="skeleton" style="width:40%;height:22px"></div>' +
      '<div class="sa-grid sa-kpis">' + '<div class="skeleton tall"></div>'.repeat(4) + '</div>' + '<div class="skeleton"></div>'.repeat(5) + '</div>';
  }
  var STATE_ICON = {
    empty: '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
    lock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    missing: '<circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="8" y1="11" x2="14" y2="11"/>',
    error: '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    offline: '<line x1="1" y1="1" x2="23" y2="23"/><path d="M16.72 11.06A10.94 10.94 0 0 1 19 12.55M5 12.55a10.94 10.94 0 0 1 5.17-2.39M10.71 5.05A16 16 0 0 1 22.58 9M1.42 9a15.91 15.91 0 0 1 4.7-2.88M8.53 16.11a6 6 0 0 1 6.95 0"/><line x1="12" y1="20" x2="12.01" y2="20"/>'
  };
  function stateIcon(kind, tone) {
    return '<div class="ico" data-tone="' + (tone || 'muted') + '" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' + STATE_ICON[kind] + '</svg></div>';
  }
  function emptyState(title, desc, actionHtml) {
    return '<div class="sa-state">' + stateIcon('empty') + '<h4>' + esc(title || 'Nothing here yet') + '</h4>' +
      (desc ? '<p>' + esc(desc) + '</p>' : '') + (actionHtml || '') + '</div>';
  }
  function errorState(err, retry) {
    var st = err && err.status;
    var id = 'retry' + Math.random().toString(36).slice(2, 8);
    var html;
    if (st === 403) {
      html = '<div class="sa-state" role="alert">' + stateIcon('lock', 'danger') + '<h4>Permission denied</h4><p>' +
        esc(err.message || 'Your account is not allowed to open this screen.') + '</p><p class="sa-small sa-muted">Only Super Admins can use this portal. Ask another Super Admin if you need access.</p>' +
        '<div class="sa-row" style="justify-content:center"><a class="btn btn-secondary btn-sm" href="#/dashboard">Back to dashboard</a><a class="btn btn-ghost btn-sm" href="/login?superadmin=1">Switch account</a></div></div>';
    } else if (st === 404) {
      html = '<div class="sa-state">' + stateIcon('missing') + '<h4>Not found</h4><p>' + esc(err.message || 'This record does not exist.') + '</p><a class="btn btn-secondary btn-sm" href="#/dashboard">Back to dashboard</a></div>';
    } else {
      html = '<div class="sa-state" role="alert">' + stateIcon(st === 0 ? 'offline' : 'error', 'warning') + '<h4>' + (st === 0 ? 'You appear to be offline' : 'Could not load') + '</h4><p>' + esc((err && err.message) || 'Unexpected error') + '</p>' +
        (retry ? '<button class="btn btn-primary btn-sm" type="button" id="' + id + '">Try again</button>' : '') + '</div>';
    }
    setTimeout(function () { var b = document.getElementById(id); if (b && retry) b.onclick = retry; }, 0);
    return html;
  }

  // ── busy helper ───────────────────────────────────────────
  async function busy(btn, fn) {
    if (btn) { btn.disabled = true; btn.setAttribute('aria-busy', 'true'); }
    try { return await fn(); }
    finally { if (btn && btn.isConnected) { btn.disabled = false; btn.removeAttribute('aria-busy'); } }
  }

  // ── modal ─────────────────────────────────────────────────
  var layer = function () { return document.getElementById('saLayer'); };
  function trapFocus(container, onClose) {
    var prev = document.activeElement;
    function key(e) {
      if (e.key === 'Escape') { e.stopPropagation(); onClose(); }
      if (e.key !== 'Tab') return;
      var f = $$('button,[href],input,select,textarea,[tabindex]:not([tabindex="-1"])', container).filter(function (x) { return !x.disabled && x.offsetParent !== null; });
      if (!f.length) return;
      if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
      else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
    }
    container.addEventListener('keydown', key);
    return function () { if (prev && prev.focus) try { prev.focus(); } catch (e) {} };
  }
  /** openModal({title, body, submitLabel, danger, size, onSubmit(form, fd) -> truthy closes}) */
  function openModal(o) {
    return new Promise(function (resolve) {
      var wrap = document.createElement('div');
      wrap.className = 'sa-overlay';
      var tid = 'm' + Math.random().toString(36).slice(2, 8);
      wrap.innerHTML = '<form class="sa-modal ' + (o.size || '') + '" role="dialog" aria-modal="true" aria-labelledby="' + tid + '" novalidate>' +
        '<header><h3 id="' + tid + '">' + esc(o.title) + '</h3><button type="button" class="modal-close" data-x aria-label="Close">✕</button></header>' +
        '<div class="body">' + (o.body || '') + '<div class="sa-err" data-err role="alert"></div></div>' +
        '<footer>' + (o.noCancel ? '' : '<button type="button" class="btn btn-secondary btn-sm" data-x>' + esc(o.cancelLabel || 'Cancel') + '</button>') +
        (o.submitLabel === null ? '' : '<button type="submit" class="btn btn-sm ' + (o.danger ? 'btn-danger' : 'btn-primary') + '" data-ok>' + esc(o.submitLabel || 'Save') + '</button>') +
        '</footer></form>';
      layer().appendChild(wrap);
      var form = $('form', wrap);
      var restore;
      function close(v) { wrap.remove(); if (restore) restore(); resolve(v); }
      restore = trapFocus(wrap, function () { close(null); });
      $$('[data-x]', wrap).forEach(function (b) { b.onclick = function () { close(null); }; });
      wrap.addEventListener('mousedown', function (e) { if (e.target === wrap) close(null); });
      form.onsubmit = async function (e) {
        e.preventDefault();
        var errEl = $('[data-err]', form); errEl.textContent = '';
        var ok = $('[data-ok]', form);
        try {
          var result = await busy(ok, function () { return o.onSubmit ? o.onSubmit(form, new FormData(form)) : true; });
          if (result !== false) close(result === undefined ? true : result);
        } catch (err) { errEl.textContent = err.message || String(err); }
      };
      if (o.onOpen) o.onOpen(form);
      var first = $('input:not([type=hidden]),select,textarea', form) || $('[data-ok]', form);
      if (first) first.focus();
    });
  }
  /** confirmDialog({title, message, confirmLabel, danger, reason: 'required'|'optional', typeToConfirm}) -> {reason}|null */
  function confirmDialog(o) {
    var body = '<p style="margin:0;color:var(--text-secondary)">' + esc(o.message) + '</p>';
    if (o.reason) body += '<div class="sa-field"><label for="cfReason">Reason' + (o.reason === 'required' ? '' : ' <span class="sa-muted">(optional)</span>') + '</label><textarea class="form-textarea" id="cfReason" name="reason" rows="3" maxlength="300" style="min-height:70px"></textarea><span class="hint">Recorded in the audit log.</span></div>';
    if (o.typeToConfirm) body += '<div class="sa-field"><label for="cfType">Type <b>' + esc(o.typeToConfirm) + '</b> to confirm</label><input class="form-input" id="cfType" name="confirm" autocomplete="off"></div>';
    return openModal({ title: o.title, body: body, submitLabel: o.confirmLabel || 'Confirm', danger: o.danger !== false,
      onSubmit: function (form, fd) {
        var reason = String(fd.get('reason') || '').trim();
        if (o.reason === 'required' && !reason) throw new Error('Please enter a reason.');
        if (o.typeToConfirm && String(fd.get('confirm') || '').trim() !== o.typeToConfirm) throw new Error('The confirmation text does not match.');
        return { reason: reason };
      } });
  }

  // ── drawer ────────────────────────────────────────────────
  function openDrawer(title, loader) {
    var wrap = document.createElement('div');
    wrap.className = 'sa-drawer-wrap';
    var tid = 'd' + Math.random().toString(36).slice(2, 8);
    wrap.innerHTML = '<aside class="sa-drawer" role="dialog" aria-modal="true" aria-labelledby="' + tid + '"><header><h3 id="' + tid + '">' + esc(title) + '</h3><button type="button" class="modal-close" aria-label="Close">✕</button></header><div class="body">' + skeleton('rows') + '</div></aside>';
    layer().appendChild(wrap);
    var restore;
    function close() { wrap.remove(); if (restore) restore(); }
    restore = trapFocus(wrap, close);
    $('.modal-close', wrap).onclick = close;
    $('.modal-close', wrap).focus();
    wrap.addEventListener('mousedown', function (e) { if (e.target === wrap) close(); });
    var body = $('.body', wrap);
    var run = async function () {
      body.innerHTML = skeleton('rows');
      try { await loader(body, close, run); } catch (err) { body.innerHTML = errorState(err, run); }
    };
    run();
    return close;
  }

  // ── charts: single-series daily columns · labeled axes · hover + keyboard tooltip · table fallback ──
  function niceMax(v) {
    if (!(v > 0)) return 1;
    var p = Math.pow(10, Math.floor(Math.log10(v))), f = v / p;
    var n = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p;
    return n < 2 && Number.isInteger(v) ? 2 : n;
  }
  function dayLabel(l) { return /^\d{4}-\d{2}-\d{2}$/.test(String(l)) ? fmtDate(l + 'T00:00:00') : String(l || ''); }
  // bucket labels from /analytics: day "YYYY-MM-DD", week = its ISO Monday "YYYY-MM-DD", month "YYYY-MM" (all UTC)
  var UNIT_NAME = { day: ['day', 'days', 'Day'], week: ['week', 'weeks', 'Week'], month: ['month', 'months', 'Month'] };
  function bucketLabel(l, unit) {
    if (unit === 'week') return 'Week of ' + dayLabel(l);
    if (unit === 'month' && /^\d{4}-\d{2}$/.test(String(l))) {
      var d = new Date(l + '-01T00:00:00');
      return isNaN(d.getTime()) ? String(l) : d.toLocaleDateString(undefined, { year: 'numeric', month: 'short' });
    }
    return dayLabel(l);
  }
  function lineChart(values, labels, o) {
    o = o || {};
    values = (values || []).map(function (v) { return Number(v) || 0; });
    var H = o.height || 150;
    var n = values.length;
    var top = niceMax(Math.max.apply(null, values.concat([0])));
    var fmt = o.money ? function (v) { return fmtMoney(v, o.currency); } : fmtN;
    var unit = UNIT_NAME[o.unit] ? o.unit : 'day', un = UNIT_NAME[unit];
    var lbl = function (l) { return bucketLabel(l, unit); };
    var total = o.total != null ? o.total : values.reduce(function (a, b) { return a + b; }, 0);
    var ticks = [top, top / 2, 0];
    var cols = values.map(function (v, i) {
      var tip = lbl(labels[i]) + ': ' + fmt(v);
      return '<span class="c" data-i="' + i + '" data-tip="' + esc(tip) + '"><i style="height:' + (v > 0 ? Math.max(1.5, v * 100 / top).toFixed(2) : 0) + '%"></i></span>';
    }).join('');
    var mid = n > 2 ? labels[Math.floor((n - 1) / 2)] : '';
    var tid = 'ct' + Math.random().toString(36).slice(2, 8);
    return '<figure class="sa-chart" data-chart tabindex="0" role="group" aria-label="' + esc((o.title || 'Chart') + ' — total ' + fmt(total) + ' over ' + n + ' ' + un[1] + '. Use the left and right arrow keys to read each ' + un[0] + '.') + '">' +
      '<div class="sa-plot" style="height:' + H + 'px"><div class="sa-yaxis" aria-hidden="true">' + ticks.map(function (t) { return '<span>' + esc(o.money ? fmtMoney(t, o.currency) : fmtN(t)) + '</span>'; }).join('') + '</div>' +
      '<div class="sa-area"><div class="sa-grid-l" aria-hidden="true"><i></i><i></i><i></i></div><div class="sa-cols' + (n > 60 ? ' dense' : '') + '">' + cols + '</div></div></div>' +
      '<div class="sa-xaxis" aria-hidden="true"><span>' + esc(lbl(labels[0])) + '</span><span>' + esc(mid ? lbl(mid) : '') + '</span><span>' + esc(lbl(labels[n - 1])) + '</span></div>' +
      '<div class="sa-chart-foot"><span class="sa-small sa-muted">' + esc(o.axisLabel || ((o.money ? 'Amount per ' : 'Count per ') + un[0])) + '</span><button type="button" class="sa-link sa-small" data-tbl aria-expanded="false" aria-controls="' + tid + '">Table view</button></div>' +
      '<div class="sa-chart-table" id="' + tid + '" hidden><table class="sa-table"><thead><tr><th>' + esc(un[2]) + '</th><th class="num">' + esc(o.money ? 'Amount' : 'Value') + '</th></tr></thead><tbody>' +
      values.map(function (v, i) { return '<tr><td>' + esc(lbl(labels[i])) + '</td><td class="num">' + esc(fmt(v)) + '</td></tr>'; }).join('') + '</tbody></table></div></figure>';
  }
  function barList(items, o) {
    o = o || {};
    if (!items.length) return emptyState(o.emptyTitle || 'No data', o.emptyDesc || '');
    var max = Math.max.apply(null, items.map(function (i) { return i.value; }).concat([1]));
    return '<ul class="sa-barlist">' + items.map(function (i) {
      var val = o.money ? fmtMoney(i.value, o.currency) : fmtN(i.value);
      return '<li title="' + esc(i.label + ': ' + val) + '"><span class="lbl">' + esc(i.label) + '</span>' +
        '<div class="sa-bar" aria-hidden="true"><i style="width:' + Math.max(2, i.value * 100 / max).toFixed(1) + '%"></i></div>' +
        '<span class="sa-mono">' + esc(val) + '</span></li>';
    }).join('') + '</ul>';
  }
  function bindCharts(root) {
    $$('[data-chart]', root).forEach(function (c) {
      if (c._bound) return; c._bound = true;
      var tip = document.createElement('div'); tip.className = 'tip'; tip.hidden = true; tip.setAttribute('role', 'status'); c.appendChild(tip);
      var cols = $$('.c', c), active = -1;
      function show(el) {
        cols.forEach(function (x) { x.classList.toggle('on', x === el); });
        if (!el) { tip.hidden = true; return; }
        var r = c.getBoundingClientRect(), b = el.getBoundingClientRect(), bar = el.firstChild.getBoundingClientRect();
        tip.textContent = el.getAttribute('data-tip'); tip.hidden = false;
        var half = tip.offsetWidth / 2 + 4;
        tip.style.left = Math.min(Math.max(b.left - r.left + b.width / 2, half), r.width - half) + 'px';
        tip.style.top = Math.max(bar.top - r.top, 18) + 'px';
      }
      c.addEventListener('mousemove', function (e) { var t = e.target.closest && e.target.closest('.c'); show(t && c.contains(t) ? t : null); });
      c.addEventListener('mouseleave', function () { show(null); });
      c.addEventListener('keydown', function (e) {
        if (e.target !== c || !cols.length) return;
        if (e.key === 'ArrowRight' || e.key === 'ArrowLeft' || e.key === 'Home' || e.key === 'End') {
          e.preventDefault();
          active = e.key === 'Home' ? 0 : e.key === 'End' ? cols.length - 1 : Math.min(cols.length - 1, Math.max(0, (active < 0 ? cols.length : active) + (e.key === 'ArrowRight' ? 1 : -1)));
          show(cols[active]);
        } else if (e.key === 'Escape') { active = -1; show(null); }
      });
      c.addEventListener('blur', function () { show(null); });
      var tb = $('[data-tbl]', c);
      if (tb) tb.onclick = function () {
        var t = $('.sa-chart-table', c); t.hidden = !t.hidden;
        tb.textContent = t.hidden ? 'Table view' : 'Hide table'; tb.setAttribute('aria-expanded', String(!t.hidden));
      };
    });
  }
  function usageBar(pct) {
    var p = Math.max(0, Math.min(100, Number(pct) || 0));
    return '<div class="sa-row" style="flex-wrap:nowrap"><div class="sa-bar ' + (p >= 100 ? 'bad' : p >= 80 ? 'warn' : '') + '" style="flex:1"><i style="width:' + p + '%"></i></div><span class="sa-mono">' + p.toFixed(0) + '%</span></div>';
  }
  function kpi(label, value, sub, o) {
    o = o || {};
    var tag = o.href ? 'button' : 'div';
    return '<' + tag + ' class="sa-kpi ' + (o.tone || '') + '"' + (o.href ? ' type="button" data-go="' + esc(o.href) + '"' : '') + '>' +
      '<div class="k" title="' + esc(label) + '">' + (o.icon ? ICON_SVG(o.icon) : '') + '<span>' + esc(label) + '</span></div>' + (Array.isArray(value) && value.length > 1 ? '<div class="v v-multi">' + value.map(function (x) { return '<span>' + esc(x) + '</span>'; }).join('') + '</div>' : '<div class="v">' + esc(Array.isArray(value) ? value[0] : value) + '</div>') + (sub ? '<div class="s">' + sub + '</div>' : '') +
      (o.href ? '<span class="go" aria-hidden="true">' + (/^\/admin/.test(o.href) ? '↗' : '→') + '</span>' : '') + '</' + tag + '>';
  }

  // ── data table component ──────────────────────────────────
  /**
   * listView(el, cfg) — the one data table of this portal.
   *  Server mode: cfg.url(params) -> endpoint (search / filter / sort / paging on the server).
   *  Local mode:  cfg.data() -> Promise<rows> (search / filter / sort / paging in the browser).
   * cfg: {rowsKey, columns:[{key,label,sort,sortVal(row),cls,render(row),csv(row),hidden}], filters:[{key,type,label,options,match(row,v)}],
   *   initial:{}, sort, limit, rowId, bulk:[{label,danger,run(ids,rows)}], rowClick(row), bindRow(tr,row,reload),
   *   empty:{title,desc}, toolbar(html), onData(data), exportUrl(state) -> server CSV report url, key (column prefs)}
   * Features: sticky header, debounced search, filters + "Clear", sortable headers (click / Enter),
   * pagination + page size, bulk selection, row actions, keyboard-openable rows, column visibility
   * (remembered per table) and export (CSV of the current view, plus the server report when one exists).
   */
  function csvCell(v) {
    var s = String(v == null ? '' : v).replace(/\s+/g, ' ').trim();
    if (/^[=+\-@\t\r]/.test(s)) s = "'" + s; // spreadsheet formula-injection guard
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }
  var _txt = document.createElement('template'); // inert: nothing inside loads or runs
  function htmlText(h) { _txt.innerHTML = h == null ? '' : String(h).replace(/<br\s*\/?>/gi, ' ').replace(/></g, '> <'); return (_txt.content.textContent || '').replace(/\s+/g, ' ').trim(); }
  function downloadCsv(name, header, rows) {
    var body = '﻿' + [header].concat(rows).map(function (r) { return r.map(csvCell).join(','); }).join('\r\n');
    var url = URL.createObjectURL(new Blob([body], { type: 'text/csv;charset=utf-8' }));
    var a = document.createElement('a'); a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
  }
  // Browser-built "Current view" CSVs never pass through the server, so record them
  // in the audit log (export.client). Fire-and-forget: the download never waits on it.
  function logClientExport(table, rows, columns, st, filterKeys) {
    var filters = {};
    (filterKeys || []).concat(['sort']).forEach(function (k) { if (st && st[k] != null && String(st[k]) !== '') filters[k] = String(st[k]); });
    try {
      fetch('/api/super-admin/exports/client-log', {
        method: 'POST', credentials: 'same-origin', keepalive: true,
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ table: String(table).slice(0, 80), rows: rows, columns: columns, filters: filters })
      }).catch(function () {});
    } catch (e) { /* never block the download */ }
  }
  function listView(el, cfg) {
    var local = typeof cfg.data === 'function';
    var st = Object.assign({ page: 1, limit: cfg.limit || 25, sort: cfg.sort || '' }, cfg.initial || {});
    var selected = {};
    var lastRows = [], allRows = null, seq = 0;
    var fid = 'lv' + Math.random().toString(36).slice(2, 7);
    var cols = cfg.columns.map(function (c, i) { return Object.assign({ _i: i }, c); });
    var prefKey = 'sa_cols:' + (cfg.key || (local ? 'local:' : '') + cols.map(function (c) { return c.label; }).join('|'));
    var hiddenCols = {};
    try { hiddenCols = JSON.parse(store(prefKey) || 'null') || {}; } catch (e) { hiddenCols = {}; }
    cols.forEach(function (c) { if (c.hidden && hiddenCols[c.label] === undefined) hiddenCols[c.label] = true; });
    var named = cols.filter(function (c) { return c.label; });
    var filterKeys = (cfg.filters || []).map(function (f) { return f.key; });
    var filtersHtml = (cfg.filters || []).map(function (f) {
      var id = fid + '_' + f.key;
      if (f.type === 'select') {
        return '<label class="sr-only" for="' + id + '">' + esc(f.label) + '</label><select class="form-select" id="' + id + '" data-f="' + esc(f.key) + '">' +
          f.options.map(function (o) { return '<option value="' + esc(o[0]) + '"' + (String(st[f.key] || '') === String(o[0]) ? ' selected' : '') + '>' + esc(o[1]) + '</option>'; }).join('') + '</select>';
      }
      if (f.type === 'date') {
        return '<label class="sa-date"><span>' + esc(f.label) + '</span><input class="form-input" type="date" id="' + id + '" data-f="' + esc(f.key) + '" value="' + esc(st[f.key] || '') + '"></label>';
      }
      return '<div class="sa-q sa-grow"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg><label class="sr-only" for="' + id + '">' + esc(f.label) + '</label><input class="form-input" type="search" id="' + id + '" data-f="' + esc(f.key) + '" placeholder="' + esc(f.label) + '" value="' + esc(st[f.key] || '') + '"></div>';
    }).join('');
    var colMenu = named.length > 2 ? '<span class="sa-rel"><button type="button" class="btn btn-secondary btn-sm" data-colbtn aria-haspopup="true" aria-expanded="false">' + ICON_SVG('cols') + 'Columns</button>' +
      '<div class="sa-pop sa-menu" data-colmenu hidden role="group" aria-label="Visible columns"><div class="sa-pop-head">Visible columns</div>' + named.map(function (c) {
        return '<label class="sa-check sa-menu-item"><input type="checkbox" data-col="' + c._i + '"' + (hiddenCols[c.label] ? '' : ' checked') + '> ' + esc(c.label) + '</label>';
      }).join('') + '<button type="button" class="sa-pop-item" data-colreset>Show all columns</button></div></span>' : '';
    var expMenu = '<span class="sa-rel"><button type="button" class="btn btn-secondary btn-sm" data-expbtn aria-haspopup="true" aria-expanded="false">' + ICON_SVG('download') + 'Export</button>' +
      '<div class="sa-pop sa-menu" data-expmenu hidden role="menu"><div class="sa-pop-head">Export CSV</div>' +
      '<button type="button" class="sa-pop-item" role="menuitem" data-exp="view"><span><b>Current view</b><small data-expcount>Visible columns · rows on screen</small></span></button>' +
      (cfg.exportUrl ? '<button type="button" class="sa-pop-item" role="menuitem" data-exp="server"><span><b>Full report</b><small>Server CSV with these filters · redacted · audited</small></span></button>' : '') + '</div></span>';
    el.innerHTML = '<div class="sa-filters">' + filtersHtml + '<button type="button" class="btn btn-ghost btn-sm" data-fclear hidden>Clear filters</button><span class="sa-spacer"></span>' +
      (cfg.toolbar || '') + colMenu + expMenu + '</div>' +
      '<div data-bulk aria-live="polite"></div><div data-body>' + skeleton('rows') + '</div><div class="sa-pager" data-pager></div>';
    var body = $('[data-body]', el), pager = $('[data-pager]', el), bulkEl = $('[data-bulk]', el), clearBtn = $('[data-fclear]', el);
    var timer;
    function syncClear() {
      var active = filterKeys.some(function (k) { return String(st[k] || '') !== ''; });
      clearBtn.hidden = !active;
    }
    $$('[data-f]', el).forEach(function (inp) {
      var ev = inp.tagName === 'SELECT' || inp.type === 'date' ? 'change' : 'input';
      inp.addEventListener(ev, function () {
        clearTimeout(timer);
        timer = setTimeout(function () { st[inp.getAttribute('data-f')] = inp.value.trim(); st.page = 1; syncClear(); load(); }, ev === 'input' ? 300 : 0);
      });
    });
    clearBtn.onclick = function () {
      $$('[data-f]', el).forEach(function (inp) { inp.value = ''; st[inp.getAttribute('data-f')] = ''; });
      st.page = 1; syncClear(); load();
    };
    // column visibility
    var colBtn = $('[data-colbtn]', el);
    if (colBtn) {
      var cm = $('[data-colmenu]', el);
      colBtn.onclick = function (e) { e.stopPropagation(); togglePop(colBtn, cm); var f = $('input', cm); if (!cm.hidden && f) f.focus(); };
      $$('[data-col]', cm).forEach(function (cb) {
        cb.onchange = function () {
          var c = cols[+cb.getAttribute('data-col')];
          var shown = named.filter(function (x) { return !hiddenCols[x.label]; }).length;
          if (!cb.checked && shown <= 1) { cb.checked = true; toast('At least one column must stay visible', 'warning'); return; }
          if (cb.checked) delete hiddenCols[c.label]; else hiddenCols[c.label] = true;
          store(prefKey, JSON.stringify(hiddenCols)); renderRows();
        };
      });
      $('[data-colreset]', cm).onclick = function () { hiddenCols = {}; store(prefKey, '{}'); $$('[data-col]', cm).forEach(function (cb) { cb.checked = true; }); renderRows(); };
    }
    var expBtn = $('[data-expbtn]', el), em = $('[data-expmenu]', el);
    expBtn.onclick = function (e) { e.stopPropagation(); $('[data-expcount]', em).textContent = visibleCols().filter(function (c) { return c.label; }).length + ' columns · ' + lastRows.length + ' row' + (lastRows.length === 1 ? '' : 's') + (local ? ' (all matching)' : ' on this page'); togglePop(expBtn, em); var f = $('.sa-pop-item', em); if (!em.hidden && f) f.focus(); };
    $$('[data-exp]', em).forEach(function (b) {
      b.onclick = function () {
        togglePop(expBtn, em, false);
        if (b.getAttribute('data-exp') === 'server') { location.href = cfg.exportUrl(st); toast('Export started — it is recorded in the audit log', 'info'); return; }
        if (!lastRows.length) { toast('Nothing to export in this view', 'warning'); return; }
        var vc = visibleCols().filter(function (c) { return c.label; });
        downloadCsv('leadai-' + (cfg.key || String(cfg.exportName || 'export')).replace(/[^a-z0-9]+/gi, '-') + '-' + isoDay(new Date()) + '.csv',
          vc.map(function (c) { return c.label; }),
          lastRows.map(function (r) { return vc.map(function (c) { return c.csv ? c.csv(r) : c.render ? htmlText(c.render(r)) : r[c.key]; }); }));
        logClientExport(cfg.key || cfg.exportName || 'table', lastRows.length, vc.map(function (c) { return c.label; }), st, filterKeys);
        toast('Exported ' + lastRows.length + ' row' + (lastRows.length === 1 ? '' : 's'));
      };
    });
    function visibleCols() { return cols.filter(function (c) { return !c.label || !hiddenCols[c.label]; }); }
    function renderBulk() {
      var ids = Object.keys(selected);
      var all = $('[data-all]', body);
      if (all) { var n = $$('[data-sel]', body).filter(function (cb) { return cb.checked; }).length; all.checked = n > 0 && n === $$('[data-sel]', body).length; all.indeterminate = n > 0 && !all.checked; }
      if (!cfg.bulk || !ids.length) { bulkEl.innerHTML = ''; return; }
      bulkEl.innerHTML = '<div class="sa-bulk"><b>' + ids.length + ' selected</b>' + cfg.bulk.map(function (b, i) {
        return '<button type="button" class="btn btn-xs ' + (b.danger ? 'btn-danger' : 'btn-secondary') + '" data-bulk-i="' + i + '">' + esc(b.label) + '</button>';
      }).join('') + '<span class="sa-spacer"></span><button type="button" class="btn btn-xs btn-ghost" data-bulk-clear>Clear selection</button></div>';
      $$('[data-bulk-i]', bulkEl).forEach(function (b) {
        b.onclick = async function () {
          var action = cfg.bulk[+b.getAttribute('data-bulk-i')];
          var rows = lastRows.filter(function (r) { return selected[cfg.rowId(r)]; });
          try { var done = await busy(b, function () { return action.run(Object.keys(selected), rows); }); if (done !== false) { selected = {}; reload(); } }
          catch (e) { toast(e.message, 'error'); }
        };
      });
      $('[data-bulk-clear]', bulkEl).onclick = function () { selected = {}; renderRows(); };
    }
    var lastData = {};
    function localPage(rows) {
      var q = String(st.q || st.search || '').toLowerCase();
      var out = rows.filter(function (r) {
        if (q && JSON.stringify(r).toLowerCase().indexOf(q) < 0 && !cols.some(function (c) { return c.render && htmlText(c.render(r)).toLowerCase().indexOf(q) >= 0; })) return false;
        return (cfg.filters || []).every(function (f) {
          var v = st[f.key]; if (!v || f.type !== 'select') return true;
          return f.match ? f.match(r, v) : String(r[f.key]) === String(v);
        });
      });
      if (st.sort) {
        var k = st.sort.replace('-', ''), dir = st.sort.charAt(0) === '-' ? -1 : 1;
        var col = cols.filter(function (c) { return c.sort === k; })[0] || {};
        var val = function (r) { var v = col.sortVal ? col.sortVal(r) : r[k]; return v == null ? '' : v; };
        out = out.slice().sort(function (a, b) { var x = val(a), y = val(b); return (typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true })) * dir; });
      }
      return out;
    }
    async function load() {
      var my = ++seq;
      body.style.opacity = '.55'; body.setAttribute('aria-busy', 'true');
      var data;
      try {
        if (local) {
          if (!allRows) allRows = (await cfg.data()) || [];
          data = { items: allRows };
        } else {
          var params = {};
          Object.keys(st).forEach(function (k) { params[k] = st[k]; });
          data = await api(cfg.url(params));
        }
      } catch (e) {
        if (my !== seq) return;
        body.style.opacity = ''; body.removeAttribute('aria-busy');
        body.innerHTML = errorState(e, load); pager.innerHTML = ''; return;
      }
      if (my !== seq || !el.isConnected) return;
      body.style.opacity = ''; body.removeAttribute('aria-busy');
      lastData = data;
      if (cfg.onData) cfg.onData(data);
      renderRows();
    }
    function reload() { allRows = null; return load(); }
    function renderRows() {
      var data = lastData;
      var rows, total, pages;
      if (local) {
        var filtered = localPage(data.items || []);
        total = filtered.length; pages = Math.max(1, Math.ceil(total / st.limit));
        if (st.page > pages) st.page = pages;
        lastRows = filtered;
        rows = filtered.slice((st.page - 1) * st.limit, st.page * st.limit);
      } else {
        rows = (typeof cfg.rowsKey === 'function' ? cfg.rowsKey(data) : data[cfg.rowsKey || 'items']) || [];
        lastRows = rows;
        total = data.total != null ? data.total : rows.length;
        pages = data.pages || Math.max(1, Math.ceil(total / st.limit));
      }
      if (!rows.length) {
        var filtered2 = filterKeys.some(function (k) { return st[k]; });
        var edesc = (cfg.empty || {}).desc || '';
        if (!filtered2 && /^Nothing matches these filters\.?$/.test(edesc)) edesc = 'Nothing has been recorded yet.';
        if (filtered2 && !edesc) edesc = 'Try adjusting the filters.';
        body.innerHTML = emptyState((cfg.empty || {}).title || 'No results', edesc,
          filtered2 && !clearBtn.hidden ? '<button type="button" class="btn btn-secondary btn-sm" data-empty-clear>Clear filters</button>' : '');
        var ec = $('[data-empty-clear]', body); if (ec) ec.onclick = function () { clearBtn.click(); };
        pager.innerHTML = ''; renderBulk(); return;
      }
      var vc = visibleCols();
      var html = '<div class="sa-table-wrap sa-sticky"><table class="sa-table"><thead><tr>' +
        (cfg.bulk ? '<th class="sa-cb"><input type="checkbox" data-all aria-label="Select all rows on this page"></th>' : '') +
        vc.map(function (c) {
          var sorted = st.sort && st.sort.replace('-', '') === c.sort;
          var dir = sorted ? (st.sort.charAt(0) === '-' ? 'descending' : 'ascending') : 'none';
          return '<th scope="col"' + (c.sort ? ' data-sort="' + esc(c.sort) + '" tabindex="0" aria-sort="' + dir + '"' : '') + (c.cls ? ' class="' + c.cls + '"' : '') + '>' +
            (c.label ? esc(c.label) : '<span class="sr-only">Actions</span>') + (c.sort ? '<span class="sa-sort" aria-hidden="true">' + (dir === 'descending' ? '↓' : dir === 'ascending' ? '↑' : '↕') + '</span>' : '') + '</th>';
        }).join('') + '</tr></thead><tbody>' +
        rows.map(function (r, i) {
          var id = cfg.rowId ? cfg.rowId(r) : i;
          return '<tr data-i="' + i + '"' + (cfg.rowClick ? ' class="clickable" tabindex="0"' : '') + (selected[id] ? ' aria-selected="true"' : '') + '>' +
            (cfg.bulk ? '<td class="sa-cb"><input type="checkbox" data-sel="' + esc(id) + '"' + (selected[id] ? ' checked' : '') + ' aria-label="Select row"></td>' : '') +
            vc.map(function (c) { return '<td' + (c.cls ? ' class="' + c.cls + '"' : '') + (c.label ? ' data-label="' + esc(c.label) + '"' : '') + '>' + (c.render ? c.render(r) : esc(r[c.key])) + '</td>'; }).join('') + '</tr>';
        }).join('') + '</tbody></table></div>';
      body.innerHTML = html;
      $$('th[data-sort]', body).forEach(function (th) {
        var go2 = function () {
          var k = th.getAttribute('data-sort');
          st.sort = st.sort === '-' + k ? k : '-' + k; st.page = 1;
          if (local) renderRows(); else load();
          var again = $('th[data-sort="' + k + '"]', body); if (again) again.focus();
        };
        th.onclick = go2; th.onkeydown = function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go2(); } };
      });
      if (cfg.bulk) {
        $$('[data-sel]', body).forEach(function (cb) {
          cb.onclick = function (e) { e.stopPropagation(); var k = cb.getAttribute('data-sel'); if (cb.checked) selected[k] = true; else delete selected[k]; cb.closest('tr').setAttribute('aria-selected', String(cb.checked)); renderBulk(); };
        });
        var all = $('[data-all]', body);
        all.onclick = function () { $$('[data-sel]', body).forEach(function (cb) { cb.checked = all.checked; var k = cb.getAttribute('data-sel'); if (all.checked) selected[k] = true; else delete selected[k]; }); renderBulk(); };
      }
      if (cfg.rowClick) {
        $$('tbody tr', body).forEach(function (tr) {
          tr.addEventListener('click', function (e) {
            if (e.target.closest('button,a,input,select,label')) return;
            cfg.rowClick(rows[+tr.getAttribute('data-i')]);
          });
          tr.addEventListener('keydown', function (e) {
            if (e.target !== tr) return;
            if (e.key === 'Enter') { e.preventDefault(); cfg.rowClick(rows[+tr.getAttribute('data-i')]); }
            if (e.key === 'ArrowDown' && tr.nextElementSibling) { e.preventDefault(); tr.nextElementSibling.focus(); }
            if (e.key === 'ArrowUp' && tr.previousElementSibling) { e.preventDefault(); tr.previousElementSibling.focus(); }
          });
        });
      }
      if (cfg.bindRow) $$('tbody tr', body).forEach(function (tr) { cfg.bindRow(tr, rows[+tr.getAttribute('data-i')], reload); });
      var from = (st.page - 1) * st.limit + 1, to = Math.min(total, from + rows.length - 1);
      pager.innerHTML = '<span>Showing <b>' + fmtN(from) + '–' + fmtN(to) + '</b> of <b>' + fmtN(total) + '</b></span>' +
        '<span class="sa-row"><label class="sa-small sa-muted" for="' + fid + 'lim">Rows</label><select class="form-select sa-lim" id="' + fid + 'lim">' +
        [10, 25, 50, 100].map(function (n) { return '<option' + (n === st.limit ? ' selected' : '') + '>' + n + '</option>'; }).join('') + '</select>' +
        '<nav class="sa-row" aria-label="Pagination"><button type="button" class="btn btn-secondary btn-xs" data-p="first" aria-label="First page"' + (st.page <= 1 ? ' disabled' : '') + '>«</button>' +
        '<button type="button" class="btn btn-secondary btn-xs" data-p="prev"' + (st.page <= 1 ? ' disabled' : '') + '>‹ Prev</button>' +
        '<span class="sa-small" aria-current="page">Page ' + st.page + ' / ' + pages + '</span>' +
        '<button type="button" class="btn btn-secondary btn-xs" data-p="next"' + (st.page >= pages ? ' disabled' : '') + '>Next ›</button>' +
        '<button type="button" class="btn btn-secondary btn-xs" data-p="last" aria-label="Last page"' + (st.page >= pages ? ' disabled' : '') + '>»</button></nav></span>';
      var jump = function (p) { st.page = Math.min(Math.max(1, p), pages); if (local) renderRows(); else load(); };
      $('[data-p=first]', pager).onclick = function () { jump(1); };
      $('[data-p=prev]', pager).onclick = function () { jump(st.page - 1); };
      $('[data-p=next]', pager).onclick = function () { jump(st.page + 1); };
      $('[data-p=last]', pager).onclick = function () { jump(pages); };
      $('#' + fid + 'lim', pager).onchange = function () { st.limit = +this.value; st.page = 1; if (local) renderRows(); else load(); };
      renderBulk();
    }
    syncClear();
    load();
    return { reload: reload, state: st };
  }
  // ── tabs (ARIA tablist: arrow keys / Home / End; ?tab= kept in the URL) ──
  function tabs(el, items, active, onChange) {
    var tid = 't' + Math.random().toString(36).slice(2, 7);
    if (!items.some(function (t) { return t[0] === active; })) active = items[0][0];
    el.innerHTML = '<div class="sa-tabs" role="tablist">' + items.map(function (t) {
      var on = t[0] === active;
      return '<button type="button" role="tab" id="' + tid + '_' + esc(t[0]) + '" aria-controls="' + tid + '_p" class="sa-tab' + (on ? ' active' : '') + '" aria-selected="' + on + '" tabindex="' + (on ? '0' : '-1') + '" data-tab="' + esc(t[0]) + '">' + esc(t[1]) + '</button>';
    }).join('') + '</div><div data-tabbody role="tabpanel" id="' + tid + '_p" tabindex="-1"></div>';
    var bodyEl = $('[data-tabbody]', el);
    var btns = $$('[data-tab]', el);
    function show(key, fromUser) {
      btns.forEach(function (b) { var on = b.getAttribute('data-tab') === key; b.classList.toggle('active', on); b.setAttribute('aria-selected', on); b.tabIndex = on ? 0 : -1; if (on) bodyEl.setAttribute('aria-labelledby', b.id); });
      if (fromUser) {
        try {
          var h = location.hash.split('?'), p = new URLSearchParams(h[1] || '');
          if (key === items[0][0]) p.delete('tab'); else p.set('tab', key);
          ['page', 'status', 'unread', 'range'].forEach(function (k) { if (k !== 'range' || key !== 'overview') p.delete(k); });
          var s = p.toString();
          history.replaceState(null, '', h[0] + (s ? '?' + s : ''));
        } catch (e) { /* ignore */ }
      }
      bodyEl.innerHTML = skeleton('rows');
      Promise.resolve(onChange(key, bodyEl)).then(function () { bindGo(bodyEl); bindCharts(bodyEl); }).catch(function (e) { bodyEl.innerHTML = errorState(e, function () { show(key); }); });
    }
    btns.forEach(function (b, i) {
      b.onclick = function () { show(b.getAttribute('data-tab'), true); };
      b.onkeydown = function (e) {
        var j = e.key === 'ArrowRight' ? i + 1 : e.key === 'ArrowLeft' ? i - 1 : e.key === 'Home' ? 0 : e.key === 'End' ? btns.length - 1 : null;
        if (j == null) return;
        e.preventDefault(); j = (j + btns.length) % btns.length; btns[j].focus(); btns[j].click();
      };
    });
    show(active);
    return show;
  }
  // generic delegated navigation for [data-go] (hash) inside a root
  function bindGo(root) {
    $$('[data-go]', root).forEach(function (b) {
      if (b._go) return; b._go = true;
      b.addEventListener('click', function (e) { e.preventDefault(); go(b.getAttribute('data-go')); });
    });
  }
  function go(hash) {
    if (/^\/admin/.test(hash)) { window.open(hash, '_blank', 'noopener'); return; }
    location.hash = hash.charAt(0) === '#' ? hash : '#' + hash;
  }
  function header(title, desc, actions) {
    return '<div class="sa-head"><div class="sa-head-text"><h1 tabindex="-1">' + esc(title) + '</h1>' + (desc ? '<p>' + esc(desc) + '</p>' : '') + '</div>' +
      (actions ? '<div class="sa-actions">' + actions + '</div>' : '') + '</div>';
  }
  function ICON_SVG(name) { return '<svg class="sa-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (IC[name] || '') + '</svg>'; }
  /** Server CSV report URL (redacted + audited server side) for the table's current filters. */
  function reportUrl(kind, f) {
    f = f || {};
    var status = /^(awaiting|refunded)$/.test(f.status || '') ? '' : f.status;
    return '/api/super-admin/reports/' + kind + '.csv' + qs({ organization_id: f.organization_id, from: f.from, to: f.to, status: status, category: f.category, action: f.action });
  }
  function adminLink(route, label) {
    return '<a class="btn btn-secondary btn-sm" href="/admin#/' + esc(route) + '" target="_blank" rel="noopener">' + esc(label || 'Open in platform console') + ' ↗</a>';
  }

  // ════════════════════════════════════════════════════════
  //  DASHBOARD
  // ════════════════════════════════════════════════════════
  async function viewDashboard(root) {
    var r = await Promise.all([api('/api/super-admin/dashboard'), api('/api/super-admin/health').catch(function () { return null; }),
      api('/api/super-admin/analytics?range=30d').catch(function () { return null; })]);
    var d = r[0].dashboard, h = r[1] && r[1].health, an = r[2];
    var cur = (d.payments && d.payments.currency) || 'USD';
    var sub = d.subscriptions || {};
    // never add currencies together: one figure per currency when there are several
    var perCur = function (map, single, c) { // -> one formatted figure per currency
      var ks = Object.keys(map || {});
      if (ks.length > 1) return ks.map(function (k) { return fmtMoney(map[k], k); });
      return [fmtMoney(ks.length ? map[ks[0]] : (single || 0), ks[0] || c)];
    };
    var mrrTxt = perCur(sub.mrr_by_currency, sub.mrr, cur);
    var rev30Txt = perCur(d.payments.revenue_30d_by_currency, d.payments.revenue_30d, cur).join(' · ');
    var errs = (d.errors.security_high_7d || 0) + (d.errors.system_errors_7d || 0);
    var html = header('Global dashboard', 'Every organization, user, subscription and job across the platform · updated ' + ago(d.generated_at),
      '<button type="button" class="btn btn-secondary btn-sm" id="dRefresh">' + ICON_SVG('repeat') + 'Refresh</button>' +
      '<a class="btn btn-secondary btn-sm" href="#/analytics">' + ICON_SVG('chart') + 'Analytics</a><a class="btn btn-secondary btn-sm" href="#/reports">' + ICON_SVG('download') + 'Reports</a>');
    html += '<div class="sa-grid sa-kpis">' +
      kpi('Organizations', fmtN(d.organizations.total), fmtN(d.organizations.active) + ' active · ' + fmtN(d.organizations.suspended) + ' suspended', { href: '#/organizations', icon: 'org' }) +
      kpi('Admins', fmtN(d.admins.total), 'owners & admins', { href: '#/admins', icon: 'shield' }) +
      kpi('Users', fmtN(d.users.total), fmtN(d.users.active) + ' active · +' + fmtN(d.users.new_7d) + ' this week', { href: '#/users', icon: 'users' }) +
      kpi('Demo accounts', fmtN(d.demo.accounts), fmtN(d.demo.pending_requests) + ' requests pending', { href: '#/demo', tone: d.demo.pending_requests ? 'warn' : '', icon: 'gift' }) +
      kpi('Active subscriptions', fmtN(sub.active), fmtN(sub.awaiting_confirmation) + ' awaiting confirmation', { href: '#/subscriptions', tone: sub.awaiting_confirmation ? 'warn' : '', icon: 'repeat' }) +
      kpi('MRR', mrrTxt, esc(rev30Txt) + ' collected (30d)', { href: '#/payments', icon: 'card' }) +
      kpi('Tokens used (24h)', fmtN(d.tokens.consumed_24h), fmtN(d.tokens.consumed_30d) + ' in 30 days', { href: '#/tokens', icon: 'coin' }) +
      kpi('Searches', fmtN(d.searches.total), fmtN(d.searches.running) + ' running · ' + fmtN(d.searches.failed) + ' failed', { href: '#/ops/searches', tone: d.searches.failed ? 'warn' : '', icon: 'search' }) +
      kpi('Apify jobs', fmtN(d.apify.total), fmtN(d.apify.failed) + ' failed · ' + fmtN(d.apify.failed_24h) + ' in 24h', { href: '#/ops/jobs', tone: d.apify.failed_24h ? 'bad' : '', icon: 'cpu' }) +
      kpi('Leads', fmtN(d.leads.total), fmtN(d.leads.hot) + ' hot · +' + fmtN(d.leads.last_30d) + ' (30d)', { href: '#/ops/leads', icon: 'star' }) +
      kpi('Platform errors (7d)', fmtN(errs), fmtN(d.errors.system_errors_7d) + ' server · ' + fmtN(d.errors.security_high_7d) + ' security', { href: '#/security', tone: errs ? 'bad' : '', icon: 'lock' }) +
      kpi('Payments', fmtN((d.payments.by_status.succeeded || {}).count || 0), fmtN(d.payments.pending) + ' pending · ' + fmtN(d.payments.failed) + ' failed · ' + fmtN(d.payments.refund_required) + ' refund due', { href: '#/payments', tone: d.payments.refund_required ? 'warn' : '', icon: 'card' }) +
      '</div>';
    // needs attention + subscriptions mix + health
    var attn = [
      [d.demo.pending_requests, 'demo request(s) waiting for approval', '#/demo?status=pending', 'warn'],
      [sub.awaiting_confirmation, 'paid subscription(s) awaiting your confirmation', '#/subscriptions', 'warn'],
      [d.payments.refund_required, 'payment(s) flagged refund due', '#/payments?status=refunded', 'err'],
      [d.apify.failed_24h, 'Apify job(s) failed in the last 24h', '#/ops/jobs?status=failed', 'err'],
      [d.searches.running, 'search(es) running right now', '#/ops/searches?status=running', 'info'],
      [d.errors.security_high_7d, 'high-severity security event(s) this week', '#/security', 'err'],
      [d.errors.system_errors_7d, 'server error(s) this week', '#/health', 'err']
    ].filter(function (x) { return x[0]; });
    var mix = [['active', sub.active], ['pending', sub.pending], ['pending_admin_confirmation', sub.awaiting_confirmation], ['suspended', sub.suspended], ['expired', sub.expired], ['cancelled', sub.cancelled]];
    var mixTotal = mix.reduce(function (a, x) { return a + (x[1] || 0); }, 0);
    html += '<div class="sa-grid sa-3 sa-section">' +
      '<div class="sa-card"><h3>Needs attention <span class="sa-small sa-muted">' + fmtN(attn.length) + ' item' + (attn.length === 1 ? '' : 's') + '</span></h3>' +
      (attn.length ? '<ul class="sa-attn">' + attn.map(function (x) {
        return '<li><a href="' + esc(x[2]) + '"><span class="sa-dot ' + esc(x[3] || 'ok') + '" aria-hidden="true"></span><span class="n">' + fmtN(x[0]) + '</span><span>' + esc(x[1]) + '</span><span class="arr" aria-hidden="true">→</span></a></li>';
      }).join('') + '</ul>' : '<div class="sa-ok-line"><span class="sa-dot ok" aria-hidden="true"></span>All clear — no queues waiting on you.</div>') + '</div>' +
      '<div class="sa-card"><h3>Subscriptions by status <a class="sa-link" href="#/subscriptions">Manage →</a></h3>' +
      '<div class="sa-stack" role="img" aria-label="' + esc(mix.map(function (x) { return (PILL_LABEL[x[0]] || titleCase(x[0])) + ' ' + (x[1] || 0); }).join(', ')) + '">' +
      mix.filter(function (x) { return x[1]; }).map(function (x) { return '<i data-tone="' + toneOf(x[0]) + '" style="flex:' + x[1] + '" title="' + esc((PILL_LABEL[x[0]] || titleCase(x[0])) + ': ' + x[1]) + '"></i>'; }).join('') + '</div>' +
      '<ul class="sa-feed">' + mix.map(function (x) {
        return '<li><a href="#/subscriptions?status=' + esc(x[0] === 'pending_admin_confirmation' ? 'awaiting' : x[0]) + '" class="sa-link">' + pill(x[0]) + '</a><span class="when"><b class="sa-mono" style="color:var(--text-primary)">' + fmtN(x[1] || 0) + '</b>' + (mixTotal ? ' · ' + Math.round((x[1] || 0) * 100 / mixTotal) + '%' : '') + '</span></li>';
      }).join('') + '</ul></div>' +
      '<div class="sa-card"><h3>System health <a class="sa-link" href="#/health">Details →</a></h3>' +
      (h ? '<ul class="sa-feed">' + h.components.map(function (c) {
        return '<li><span class="sa-dot ' + esc(c.status) + '" style="margin-top:6px" aria-hidden="true"></span><div><b>' + esc(c.label) + '</b><div class="sa-small sa-muted">' + esc(c.detail) + '</div></div><span class="when">' + pill(c.status === 'ok' ? 'ok' : c.status === 'warn' ? 'warn' : 'failed', c.status === 'ok' ? 'Healthy' : c.status === 'warn' ? 'Attention' : 'Unhealthy') + '</span></li>';
      }).join('') + '</ul>' : emptyState('Health unavailable', 'The health check did not respond.')) + '</div></div>';
    // 30-day trends (drill-down to analytics)
    if (an && an.days) {
      var tr = function (title, s, href, o) {
        var c = (o && o.currency) || cur;
        return '<div class="sa-card"><h3><span>' + esc(title) + ' <span class="sa-small sa-muted">30d</span></span><a class="sa-link" href="' + esc(href) + '">Drill down →</a></h3><div class="total">' + esc(o && o.money ? fmtMoney(s.total, c) : fmtN(s.total)) + '</div>' +
          lineChart(s.values, an.days, Object.assign({ title: title, total: s.total, height: 110 }, o || {}, { currency: c })) + '</div>';
      };
      var rv = an.business.revenue || {}, rvCur = Object.keys(rv.by_currency || {});
      html += '<div class="sa-grid sa-3 sa-section">' + tr('New organizations', an.business.registrations, '#/analytics') + tr('Searches', an.product.searches, '#/ops/searches') +
        (rvCur.length > 1 ? rvCur.map(function (c) { return tr('Revenue collected · ' + c, rv.by_currency[c], '#/payments?status=succeeded', { money: true, currency: c }); }).join('')
          : tr('Revenue collected', rv, '#/payments?status=succeeded', { money: true, currency: rvCur[0] || rv.currency || cur })) + '</div>';
    }
    var rec = d.recent || {};
    function feed(items, fn, emptyTxt) {
      return items && items.length ? '<ul class="sa-feed">' + items.map(fn).join('') + '</ul>' : emptyState(emptyTxt);
    }
    var orgLink = function (id, name) { return '<a class="sa-link' + (name ? '' : ' sa-mono') + '" href="#/organizations/' + esc(id) + '">' + (name ? esc(name) : 'org ' + esc(short(id))) + '</a>'; };
    html += '<div class="sa-grid sa-3 sa-section">' +
      '<div class="sa-card"><h3>Subscription requests <a class="sa-link" href="#/subscriptions">Queue →</a></h3>' + feed(rec.subscription_requests, function (s) {
        return '<li><div><b>' + esc(s.plan_id) + '</b> · ' + esc(fmtMoney(s.amount, s.currency)) + '<div class="sa-small">' + orgLink(s.organization_id, s.organization_name) + '</div></div><span class="when">' + esc(ago(s.created_at)) + '</span></li>';
      }, 'Nothing awaiting confirmation') + '</div>' +
      '<div class="sa-card"><h3>Recent registrations <a class="sa-link" href="#/organizations">All →</a></h3>' + feed(rec.registrations, function (o) {
        return '<li><div><a class="sa-link" href="#/organizations/' + esc(o._id) + '">' + esc(o.name) + '</a><div>' + pill(o.status) + '</div></div><span class="when">' + esc(ago(o.created_at)) + '</span></li>';
      }, 'No organizations yet') + '</div>' +
      '<div class="sa-card"><h3>Recent payments <a class="sa-link" href="#/payments">All →</a></h3>' + feed(rec.payments, function (p) {
        return '<li><div><b>' + esc(fmtMoney(p.amount, p.currency)) + '</b> ' + pill(p.status) + (p.refund_required ? ' ' + pill('refund_due') : '') + '<div class="sa-small">' + orgLink(p.organization_id, p.organization_name) + '</div></div><span class="when">' + esc(ago(p.created_at)) + '</span></li>';
      }, 'No payments yet') + '</div>' +
      '<div class="sa-card"><h3>Demo requests <a class="sa-link" href="#/demo">Inbox →</a></h3>' + feed(rec.demo_requests, function (x) {
        return '<li><div><b>' + esc(x.company || x.name) + '</b><div>' + pill(x.status) + '</div></div><span class="when">' + esc(ago(x.created_at)) + '</span></li>';
      }, 'No demo requests') + '</div>' +
      '<div class="sa-card sa-span2"><h3>Activity feed <a class="sa-link" href="#/audit">Audit log →</a></h3>' + feed(rec.activity, function (a) {
        return '<li><span class="sa-dot ' + (a.status === 'failure' ? 'err' : 'ok') + '" style="margin-top:6px" aria-hidden="true"></span><div><span class="sa-mono">' + esc(a.action) + '</span>' + (a.status === 'failure' ? ' ' + pill('failure') : '') + '<div class="sa-small sa-muted">' + esc(a.actor_email || 'system') + (a.organization_id ? ' · ' + orgLink(a.organization_id, a.organization_name) : '') + '</div></div><span class="when">' + esc(ago(a.at)) + '</span></li>';
      }, 'No activity yet') + '</div>' +
      '</div>';
    root.innerHTML = html;
    bindGo(root);
    $('#dRefresh', root).onclick = function () { route(); };
    S.timers.push(setInterval(function () { if (!document.hidden && location.hash.replace(/^#\/?/, '').split('?')[0] === 'dashboard') route(true); }, 60000));
  }
  // ════════════════════════════════════════════════════════
  //  ORGANIZATIONS
  // ════════════════════════════════════════════════════════
  var ORG_STATUS_OPTS = [['', 'All statuses'], ['active', 'Active'], ['demo', 'Demo'], ['trial', 'Trial'], ['pending', 'Pending demo'], ['suspended', 'Suspended'], ['disabled', 'Deactivated'], ['cancelled', 'Cancelled'], ['archived', 'Archived']];

  async function orgStatusAction(org, status, after) {
    var name = org.name || 'this organization';
    var cfg = {
      active: { t: 'Activate organization', m: 'Restore access for every member of "' + name + '".', l: 'Activate', danger: false, reason: 'optional' },
      suspended: { t: 'Suspend organization', m: 'All members of "' + name + '" are signed out immediately and cannot use the product until re-activated. Data is kept.', l: 'Suspend', reason: 'optional' },
      disabled: { t: 'Deactivate organization', m: 'Deactivates "' + name + '" (members signed out, access blocked). Data is kept and it can be re-activated.', l: 'Deactivate', reason: 'required' },
      archived: { t: 'Archive (soft delete) organization', m: 'Archives "' + name + '": hidden from lists, all access blocked, members signed out. Nothing is deleted — tenant data stays for audit and can be restored by re-activating.', l: 'Archive', reason: 'required', type: org.name }
    }[status];
    var res = await confirmDialog({ title: cfg.t, message: cfg.m, confirmLabel: cfg.l, danger: cfg.danger !== false, reason: cfg.reason, typeToConfirm: cfg.type });
    if (!res) return false;
    await api('/api/super-admin/organizations/' + encodeURIComponent(org.id || org._id) + '/status', { method: 'PATCH', body: { status: status, reason: res.reason } });
    toast('Organization ' + (PILL_LABEL[status] || status).toLowerCase());
    if (after) after();
    return true;
  }

  async function plansOptions(selected, includeBlank) {
    var p = await api('/api/super-admin/plans');
    return (includeBlank ? '<option value="">' + esc(includeBlank) + '</option>' : '') + (p.plans || []).filter(function (x) { return x.status !== 'archived'; }).map(function (x) {
      return '<option value="' + esc(x.slug) + '"' + (x.slug === selected ? ' selected' : '') + '>' + esc(x.name) + ' (' + esc(x.slug) + ')' + (x.status !== 'active' ? ' — ' + esc(x.status) : '') + '</option>';
    }).join('');
  }

  async function createOrgModal(after) {
    var opts = await plansOptions('', 'No plan (assign later)');
    var res = await openModal({ title: 'New organization', submitLabel: 'Create organization', body:
      '<div class="sa-form-grid"><div class="sa-field"><label for="coName">Organization name *</label><input class="form-input" id="coName" name="name" required maxlength="120"></div>' +
      '<div class="sa-field"><label for="coSlug">Slug <span class="sa-muted">(optional)</span></label><input class="form-input" id="coSlug" name="slug" maxlength="60" pattern="[a-z0-9-]*"></div></div>' +
      '<div class="sa-field"><label for="coPlan">Plan</label><select class="form-select" id="coPlan" name="plan_id">' + opts + '</select></div>' +
      '<div class="sa-field"><label for="coOwner">Owner email</label><input class="form-input" id="coOwner" name="owner_email" type="email" autocomplete="off"><span class="hint">A new owner gets an emailed one-time link to set their own password (valid 72 h). Passwords are never emailed.</span></div>',
      onSubmit: async function (f, fd) {
        var body = { name: String(fd.get('name') || '').trim() };
        if (!body.name) throw new Error('Organization name is required.');
        if (fd.get('slug')) body.slug = String(fd.get('slug')).trim();
        if (fd.get('plan_id')) body.plan_id = fd.get('plan_id');
        var email = String(fd.get('owner_email') || '').trim();
        if (email) { if (UI.isEmail && !UI.isEmail(email)) throw new Error('Enter a valid owner email.'); body.owner_email = email; }
        var out = await api('/api/admin/organizations', { method: 'POST', body: body });
        return out.organization;
      } });
    if (res) { toast('Organization created' + (res.owner_id ? ' — set-password link emailed to the owner' : '')); go('#/organizations/' + (res.id || res._id)); if (after) after(); }
  }

  async function editOrgModal(org, after) {
    var res = await openModal({ title: 'Edit organization', body:
      '<div class="sa-field"><label for="eoName">Name</label><input class="form-input" id="eoName" name="name" value="' + esc(org.name) + '" maxlength="120" required></div>' +
      '<div class="sa-form-grid"><div class="sa-field"><label for="eoTz">Timezone</label><input class="form-input" id="eoTz" name="timezone" value="' + esc(org.timezone || 'UTC') + '"></div>' +
      '<div class="sa-field"><label for="eoCur">Currency</label><input class="form-input" id="eoCur" name="currency" value="' + esc(org.currency || 'USD') + '" maxlength="3"></div></div>' +
      '<label class="sa-check"><input type="checkbox" name="admin_portal_enabled"' + (org.admin_portal_enabled !== false ? ' checked' : '') + '> Admin portal enabled for this organization</label>' +
      '<div class="sa-field"><label for="eoNotes">Internal notes <span class="sa-muted">(super admins only)</span></label><textarea class="form-textarea" id="eoNotes" name="notes" rows="3" maxlength="2000" style="min-height:70px">' + esc(org.admin_notes || '') + '</textarea></div>',
      onSubmit: function (f, fd) {
        return api('/api/super-admin/organizations/' + encodeURIComponent(org.id), { method: 'PATCH', body: {
          name: String(fd.get('name') || '').trim(), timezone: fd.get('timezone'), currency: fd.get('currency'),
          notes: fd.get('notes'), admin_portal_enabled: !!fd.get('admin_portal_enabled') } });
      } });
    if (res) { toast('Organization updated'); if (after) after(); }
  }

  async function impersonate(org) {
    var res = await openModal({ title: 'Impersonate "' + (org.name || '') + '"', submitLabel: 'Start support session', danger: true, body:
      '<div class="alert alert-warning"><div class="alert-body"><div class="alert-title">You will act as this organization\'s Admin.</div><div>The session is time-limited, every action is audited with your identity, and your Super Admin session is replaced until you exit impersonation from the banner in the app.</div></div></div>' +
      '<div class="sa-field"><label for="imReason">Reason (ticket / purpose) *</label><textarea class="form-textarea" id="imReason" name="reason" rows="3" minlength="5" maxlength="300" required style="min-height:70px"></textarea></div>',
      onSubmit: function (f, fd) {
        var reason = String(fd.get('reason') || '').trim();
        if (reason.length < 5) throw new Error('Enter a reason of at least 5 characters.');
        return api('/api/super-admin/impersonate', { method: 'POST', body: { organization_id: org.id, reason: reason } });
      } });
    if (res) { toast('Support session started — opening the customer app'); setTimeout(function () { location.href = res.redirect || '/'; }, 600); }
  }

  async function adjustTokens(orgId, after) {
    var res = await openModal({ title: 'Adjust token balance', submitLabel: 'Apply adjustment', body:
      '<div class="sa-field"><label for="atDelta">Tokens (+ to grant, − to remove) *</label><input class="form-input" id="atDelta" name="delta" type="number" step="1" required></div>' +
      '<div class="sa-field"><label for="atReason">Reason *</label><input class="form-input" id="atReason" name="reason" maxlength="300" required><span class="hint">Recorded in the token ledger and the audit log.</span></div>',
      onSubmit: function (f, fd) {
        var delta = parseInt(fd.get('delta'), 10);
        if (!delta) throw new Error('Enter a non-zero whole number.');
        if (!String(fd.get('reason') || '').trim()) throw new Error('A reason is required.');
        return api('/api/super-admin/tokens/' + encodeURIComponent(orgId) + '/adjust', { method: 'POST', body: { delta: delta, reason: String(fd.get('reason')).trim() } });
      } });
    if (res) { toast('Token balance updated'); if (after) after(); }
  }

  async function tokenExpiry(orgId, current, after) {
    var res = await openModal({ title: 'Token expiry', submitLabel: 'Save expiry', body:
      '<div class="sa-field"><label for="teDate">Expires on <span class="sa-muted">(leave empty = never)</span></label><input class="form-input" id="teDate" name="expires_at" type="date" value="' + esc(current ? String(current).slice(0, 10) : '') + '"></div>' +
      '<div class="sa-field"><label for="teReason">Reason *</label><input class="form-input" id="teReason" name="reason" maxlength="300" required></div>',
      onSubmit: function (f, fd) {
        if (!String(fd.get('reason') || '').trim()) throw new Error('A reason is required.');
        return api('/api/super-admin/tokens/' + encodeURIComponent(orgId) + '/expiry', { method: 'POST', body: { expires_at: fd.get('expires_at') || null, reason: String(fd.get('reason')).trim() } });
      } });
    if (res) { toast('Token expiry saved'); if (after) after(); }
  }

  async function viewOrganizations(root, q) {
    root.innerHTML = header('Organizations', 'All tenants across the platform. Archive = soft delete (data kept, access blocked).',
      '<button type="button" class="btn btn-primary btn-sm" id="oNew">+ New organization</button>') + '<div id="oList"></div>';
    var lv = listView($('#oList', root), {
      url: function (p) { return '/api/super-admin/organizations' + qs({ page: p.page, limit: p.limit, sort: p.sort, search: p.search, status: p.status, plan: p.plan }); },
      rowsKey: 'organizations', sort: '-created_at', initial: { status: q.status || '', search: q.q || '' }, key: 'organizations',
      exportUrl: function (st) { return reportUrl('organizations', { status: st.status }); },
      filters: [{ key: 'search', label: 'Search name or slug…' }, { key: 'status', type: 'select', label: 'Status', options: ORG_STATUS_OPTS }, { key: 'plan', label: 'Plan slug' }],
      rowId: function (o) { return o.id; },
      columns: [
        { label: 'Organization', sort: 'name', render: function (o) { return '<a class="sa-link cell-main" href="#/organizations/' + esc(o.id) + '">' + esc(o.name) + '</a><span class="cell-sub">' + esc(o.slug) + '</span>'; } },
        { label: 'Status', sort: 'status', render: function (o) { return pill(o.status); } },
        { label: 'Plan', render: function (o) { return esc(o.current_plan || o.plan_id || '—') + (o.subscription_status ? '<span class="cell-sub">' + pill(o.subscription_status) + '</span>' : ''); } },
        { label: 'Members', cls: 'num', render: function (o) { return fmtN(o.member_count); } },
        { label: 'Owner', render: function (o) { return esc(o.owner_email || '—'); } },
        { label: 'Created', sort: 'created_at', render: function (o) { return esc(fmtDate(o.created_at)); } },
        { label: '', cls: 'num', render: function (o) {
          return '<div class="row-actions"><a class="btn btn-secondary btn-xs" href="#/organizations/' + esc(o.id) + '">View</a>' +
            (o.status === 'active' || o.status === 'demo' || o.status === 'trial'
              ? '<button type="button" class="btn btn-danger btn-xs" data-act="suspended">Suspend</button>'
              : '<button type="button" class="btn btn-secondary btn-xs" data-act="active">Activate</button>') + '</div>'; } }
      ],
      bindRow: function (tr, o, reload) { $$('[data-act]', tr).forEach(function (b) { b.onclick = function () { orgStatusAction(o, b.getAttribute('data-act'), reload).catch(function (e) { toast(e.message, 'error'); }); }; }); },
      bulk: [
        { label: 'Suspend', danger: true, run: async function (ids) {
          var r = await confirmDialog({ title: 'Suspend ' + ids.length + ' organizations', message: 'All their members are signed out immediately.', confirmLabel: 'Suspend all', reason: 'optional' });
          if (!r) return false;
          for (var i = 0; i < ids.length; i++) await api('/api/super-admin/organizations/' + ids[i] + '/status', { method: 'PATCH', body: { status: 'suspended', reason: r.reason } });
          toast(ids.length + ' organizations suspended');
        } },
        { label: 'Activate', run: async function (ids) {
          var r = await confirmDialog({ title: 'Activate ' + ids.length + ' organizations', message: 'Members regain access.', confirmLabel: 'Activate all', danger: false });
          if (!r) return false;
          for (var i = 0; i < ids.length; i++) await api('/api/super-admin/organizations/' + ids[i] + '/status', { method: 'PATCH', body: { status: 'active', reason: r.reason } });
          toast(ids.length + ' organizations activated');
        } }
      ],
      empty: { title: 'No organizations', desc: 'Nothing matches these filters.' }
    });
    $('#oNew', root).onclick = function () { createOrgModal(lv.reload).catch(function (e) { toast(e.message, 'error'); }); };
  }

  async function viewOrgDetail(root, q, id) {
    var data = await api('/api/super-admin/organizations/' + encodeURIComponent(id));
    var o = data.organization; o.id = id;
    var u = o.usage_stats || {};
    var reload = function () { route(); };
    var statusBtns = [];
    if (o.status !== 'active') statusBtns.push('<button type="button" class="btn btn-secondary btn-sm" data-st="active">Activate</button>');
    if (o.status !== 'suspended' && o.status !== 'archived') statusBtns.push('<button type="button" class="btn btn-danger btn-sm" data-st="suspended">Suspend</button>');
    if (o.status !== 'disabled' && o.status !== 'archived') statusBtns.push('<button type="button" class="btn btn-danger btn-sm" data-st="disabled">Deactivate</button>');
    if (o.status !== 'archived') statusBtns.push('<button type="button" class="btn btn-danger btn-sm" data-st="archived">Archive</button>');
    setTitle(o.name, 'Customers › Organizations');
    root.innerHTML = '<div class="sa-row sa-small" style="margin-bottom:6px"><a class="sa-link" href="#/organizations">← Organizations</a></div>' +
      header(o.name, (o.slug || '') + ' · created ' + fmtDate(o.created_at),
        pill(o.status) + '<button type="button" class="btn btn-secondary btn-sm" id="odEdit">Edit</button>' +
        '<button type="button" class="btn btn-secondary btn-sm" id="odTok">Adjust tokens</button>' +
        (o.status !== 'archived' ? '<button type="button" class="btn btn-secondary btn-sm" id="odImp">Impersonate</button>' : '') + statusBtns.join('')) +
      '<div class="sa-grid sa-kpis">' +
      kpi('Members', fmtN(o.member_count), fmtN((o.admins || []).length) + ' admins') +
      kpi('Subscription', o.subscription ? (o.subscription.plan_id || '—') : 'None', o.subscription ? pill(o.subscription.status) : '') +
      kpi('Tokens left', o.tokens ? fmtN(o.tokens.remaining) : '—', o.tokens ? fmtN(o.tokens.used) + ' used of ' + fmtN(o.tokens.allocated) : 'not metered', { tone: o.tokens && o.tokens.allocated && o.tokens.used / o.tokens.allocated >= 0.8 ? 'warn' : '' }) +
      kpi('Searches', fmtN(u.total_searches), fmtN(u.running_searches) + ' running · ' + fmtN(u.failed_searches) + ' failed', { tone: u.failed_searches ? 'warn' : '' }) +
      kpi('Leads', fmtN(u.total_leads), fmtN(u.hot_leads) + ' hot') +
      kpi('AI calls', fmtN(u.ai_calls), fmtN(u.active_sessions) + ' active sessions') +
      '</div><div class="sa-section" id="odTabs"></div>';
    $('#odEdit', root).onclick = function () { editOrgModal(o, reload).catch(function (e) { toast(e.message, 'error'); }); };
    $('#odTok', root).onclick = function () { adjustTokens(id, reload).catch(function (e) { toast(e.message, 'error'); }); };
    if ($('#odImp', root)) $('#odImp', root).onclick = function () { impersonate(o).catch(function (e) { toast(e.message, 'error'); }); };
    $$('[data-st]', root).forEach(function (b) { b.onclick = function () { orgStatusAction(o, b.getAttribute('data-st'), reload).catch(function (e) { toast(e.message, 'error'); }); }; });

    tabs($('#odTabs', root), [['overview', 'Overview'], ['members', 'Users & Admins'], ['subscription', 'Subscription'], ['tokens', 'Usage & tokens'], ['searches', 'Searches'], ['leads', 'Leads'], ['activity', 'Activity'], ['sessions', 'Sessions']], q.tab || 'overview', async function (key, el) {
      if (key === 'overview') {
        var dr = o.demo_request;
        el.innerHTML = '<div class="sa-grid sa-2"><div class="sa-card"><h3>Profile</h3><dl class="sa-kv">' +
          '<dt>ID</dt><dd class="sa-mono">' + esc(id) + '</dd><dt>Status</dt><dd>' + pill(o.status) + (o.suspended_reason ? ' <span class="sa-small sa-muted">' + esc(o.suspended_reason) + '</span>' : '') + '</dd>' +
          '<dt>Plan</dt><dd>' + esc(o.plan_id || '—') + '</dd><dt>Timezone</dt><dd>' + esc(o.timezone || '—') + '</dd><dt>Currency</dt><dd>' + esc(o.currency || '—') + '</dd>' +
          '<dt>Admin portal</dt><dd>' + (o.admin_portal_enabled === false ? 'Disabled' : 'Enabled') + '</dd>' +
          (o.archived_at ? '<dt>Archived</dt><dd>' + esc(fmtDT(o.archived_at)) + ' by ' + esc(o.archived_by) + ' — ' + esc(o.archive_reason) + '</dd>' : '') +
          (o.admin_notes ? '<dt>Notes</dt><dd>' + esc(o.admin_notes) + '</dd>' : '') + '</dl></div>' +
          '<div class="sa-card"><h3>Admins</h3>' + ((o.admins || []).length ? '<ul class="sa-feed">' + o.admins.map(function (m) {
            return '<li><div><a class="sa-link" href="#/users/' + esc(m.user_id) + '">' + esc(m.name || m.email) + '</a><div class="sa-small sa-muted">' + esc(m.email) + '</div></div><span class="when">' + esc(roleLabel(m.role)) + ' ' + pill(m.user_status) + '</span></li>';
          }).join('') + '</ul>' : emptyState('No admins', 'This organization has no owner/admin.')) + '</div>' +
          '<div class="sa-card"><h3>Demo</h3>' + (dr ? '<dl class="sa-kv"><dt>Request</dt><dd>' + pill(dr.status) + '</dd><dt>Requested</dt><dd>' + esc(fmtDT(dr.created_at)) + '</dd>' +
            (dr.converted_at ? '<dt>Converted</dt><dd>' + esc(fmtDT(dr.converted_at)) + '</dd>' : '') + '</dl><div class="sa-row" style="margin-top:8px"><a class="sa-link" href="#/demo?q=' + esc(encodeURIComponent(dr.email || '')) + '">Open in demo inbox →</a></div>' : emptyState('No demo request')) + '</div>' +
          '<div class="sa-card"><h3>Recent searches <button type="button" class="sa-link" data-tabgo="searches">All →</button></h3>' + ((o.recent_searches || []).length ? '<ul class="sa-feed">' + o.recent_searches.map(function (s) {
            return '<li><div><button type="button" class="sa-link" data-chain="' + esc(s.run_id) + '">' + esc(short(s.query, 40)) + '</button><div class="sa-small sa-muted">' + esc(s.created_by || '') + '</div></div><span class="when">' + pill(s.status) + ' ' + esc(ago(s.created_at)) + '</span></li>';
          }).join('') + '</ul>' : emptyState('No searches yet')) + '</div></div>';
        $$('[data-chain]', el).forEach(function (b) { b.onclick = function () { chainDrawer(b.getAttribute('data-chain')); }; });
        $$('[data-tabgo]', el).forEach(function (b) { b.onclick = function () { $('[data-tab="' + b.getAttribute('data-tabgo') + '"]', root).click(); }; });
      } else if (key === 'members') {
        listView(el, {
          data: function () { return Promise.resolve(o.members || []); }, key: 'org-members', sort: 'name',
          filters: [{ key: 'q', label: 'Search name or email…' },
            { key: 'role', type: 'select', label: 'Role', options: [['', 'Any role'], ['owner', 'Admin (Owner)'], ['admin', 'Admin'], ['manager', 'Manager'], ['member', 'User'], ['viewer', 'Viewer']] },
            { key: 'user_status', type: 'select', label: 'Account', options: [['', 'Any account status'], ['active', 'Active'], ['suspended', 'Suspended'], ['disabled', 'Deactivated']] }],
          columns: [
            { label: 'User', sort: 'name', sortVal: function (m) { return (m.name || m.email || '').toLowerCase(); }, csv: function (m) { return (m.name || '') + ' <' + (m.email || '') + '>'; },
              render: function (m) { return '<a class="sa-link cell-main" href="#/users/' + esc(m.user_id) + '">' + esc(m.name || m.email || m.user_id) + '</a><span class="cell-sub">' + esc(m.email) + '</span>'; } },
            { label: 'Role', sort: 'role', render: function (m) { return esc(roleLabel(m.role)); } },
            { label: 'Membership', sort: 'status', render: function (m) { return pill(m.status); } },
            { label: 'Account', sort: 'user_status', render: function (m) { return pill(m.user_status); } },
            { label: 'Last login', sort: 'last_login', sortVal: function (m) { var d2 = toDate(m.last_login); return d2 ? d2.getTime() : 0; }, csv: function (m) { return fmtDT(m.last_login); }, render: function (m) { return '<span title="' + esc(fmtDT(m.last_login)) + '">' + esc(ago(m.last_login)) + '</span>'; } },
            { label: '', cls: 'num', render: function (m) {
              return '<div class="row-actions"><button type="button" class="btn btn-secondary btn-xs" data-reset>Reset access</button>' +
                (m.user_status === 'active' ? '<button type="button" class="btn btn-danger btn-xs" data-v="suspended">Suspend</button>' : '<button type="button" class="btn btn-secondary btn-xs" data-v="active">Activate</button>') + '</div>'; } }
          ],
          bindRow: function (tr, m) {
            $('[data-reset]', tr).onclick = function () { resetAccess(m.user_id, m.email).catch(function (e) { toast(e.message, 'error'); }); };
            $('[data-v]', tr).onclick = function () { userStatusAction({ id: m.user_id, email: m.email, name: m.name }, this.getAttribute('data-v'), reload).catch(function (e) { toast(e.message, 'error'); }); };
          },
          empty: { title: 'No members', desc: 'This organization has no members yet.' }
        });
      } else if (key === 'subscription') {
        listView(el, {
          data: function () { return Promise.resolve(o.subscriptions || []); }, key: 'org-subscriptions', sort: '-created_at',
          columns: [
            { label: 'Plan', sort: 'plan_id', render: function (s) { return '<span class="cell-main">' + esc(s.plan_id) + '</span>'; } },
            { label: 'Status', sort: 'status', render: function (s) { return pill(s.status); } },
            { label: 'Amount', sort: 'amount', cls: 'num', render: function (s) { return esc(fmtMoney(s.amount, s.currency)); } },
            { label: 'Cycle', render: function (s) { return esc(s.billing_cycle || '—'); } },
            { label: 'Period end', sort: 'current_period_end', render: function (s) { return esc(fmtDate(s.current_period_end)); } },
            { label: 'Created', sort: 'created_at', render: function (s) { return esc(fmtDate(s.created_at)); } },
            { label: '', cls: 'num', render: function () { return '<button type="button" class="btn btn-secondary btn-xs" data-sub>Manage</button>'; } }
          ],
          rowClick: function (s) { subDrawer(s._id, reload); },
          bindRow: function (tr, s) { $('[data-sub]', tr).onclick = function () { subDrawer(s._id, reload); }; },
          empty: { title: 'No subscription', desc: 'The organization has not selected a plan yet.' }
        });
      } else if (key === 'tokens') {
        var led = await api('/api/super-admin/tokens/' + encodeURIComponent(id) + '/ledger?limit=200');
        var users = await api('/api/super-admin/tokens/' + encodeURIComponent(id) + '/users?range=30d');
        var b = led.balance;
        el.innerHTML = '<div class="sa-grid sa-2"><div class="sa-card"><h3>Balance <span class="sa-row"><button type="button" class="btn btn-secondary btn-xs" id="tkAdj">Adjust</button><button type="button" class="btn btn-secondary btn-xs" id="tkExp">Expiry</button></span></h3>' +
          (b ? '<dl class="sa-kv"><dt>Allocated</dt><dd>' + fmtN(b.allocated) + '</dd><dt>Used</dt><dd>' + fmtN(b.used) + '</dd><dt>Remaining</dt><dd>' + fmtN(b.remaining) + '</dd><dt>Usage</dt><dd>' + usageBar(b.allocated ? b.used * 100 / b.allocated : 0) + '</dd><dt>Expires</dt><dd>' + (b.expires_at ? esc(fmtDT(b.expires_at)) + (b.expired ? ' ' + pill('expired') : '') : 'Never') + '</dd><dt>Source</dt><dd>' + esc(b.source || '—') + '</dd></dl>' : emptyState('Not token-metered', 'Grant tokens to start metering this organization.')) + '</div>' +
          '<div class="sa-card"><h3>Consumption by user (30d)</h3>' + barList((users.items || []).map(function (x) { return { label: x.email || x.user_id || 'system', value: x.tokens }; }), { emptyTitle: 'No consumption in 30 days' }) + '</div></div>' +
          '<div class="sa-card sa-section"><h3>Token ledger <span class="sa-small sa-muted">latest 200 entries</span></h3><div id="tkLed"></div></div>';
        listView($('#tkLed', el), {
          data: function () { return Promise.resolve(led.items || []); }, key: 'org-ledger', sort: '-created_at',
          filters: [{ key: 'q', label: 'Reason or actor…' }, { key: 'type', type: 'select', label: 'Type', options: [['', 'All types'], ['allocate', 'Allocate'], ['consume', 'Consume'], ['adjust', 'Adjust'], ['refund', 'Refund']] }],
          columns: [
            { label: 'When', sort: 'created_at', sortVal: function (l) { var d2 = toDate(l.created_at); return d2 ? d2.getTime() : 0; }, render: function (l) { return esc(fmtDT(l.created_at)); } },
            { label: 'Type', sort: 'type', render: function (l) { return esc(titleCase(l.type)); } },
            { label: 'Amount', sort: 'amount', cls: 'num', render: function (l) { return esc(fmtN(l.amount)); } },
            { label: 'Balance after', cls: 'num', render: function (l) { return esc(l.balance_after == null ? '—' : fmtN(l.balance_after)); } },
            { label: 'Reason', render: function (l) { return esc(l.reason || ''); } },
            { label: 'By', render: function (l) { return '<span class="sa-small">' + esc(l.actor || l.user_id || '') + '</span>'; } }
          ],
          empty: { title: 'No ledger entries' }
        });
        $('#tkAdj', el).onclick = function () { adjustTokens(id, reload).catch(function (e) { toast(e.message, 'error'); }); };
        $('#tkExp', el).onclick = function () { if (!b) return toast('Grant tokens first', 'warning'); tokenExpiry(id, b.expires_at, reload).catch(function (e) { toast(e.message, 'error'); }); };
      } else if (key === 'searches') {
        searchesList(el, { organization_id: id });
      } else if (key === 'leads') {
        leadsList(el, { organization_id: id });
      } else if (key === 'activity') {
        auditList(el, { organization_id: id });
      } else if (key === 'sessions') {
        sessionsList(el, { organization_id: id });
      }
    });
  }

  // ════════════════════════════════════════════════════════
  //  USERS & ADMINS
  // ════════════════════════════════════════════════════════
  async function userStatusAction(user, status, after) {
    var nm = user.name || user.email || 'this user';
    var cfg = { active: ['Activate user', 'Restore access for ' + nm + '.', 'Activate', false, 'optional'],
      suspended: ['Suspend user', nm + ' is signed out of every session immediately and cannot sign in until re-activated.', 'Suspend', true, 'optional'],
      disabled: ['Deactivate user', nm + ' is signed out and the account is deactivated. Their data is kept.', 'Deactivate', true, 'required'] }[status];
    var r = await confirmDialog({ title: cfg[0], message: cfg[1], confirmLabel: cfg[2], danger: cfg[3], reason: cfg[4] });
    if (!r) return false;
    await api('/api/super-admin/users/' + encodeURIComponent(user.id || user.user_id) + '/status', { method: 'PATCH', body: { status: status, reason: r.reason } });
    toast('User ' + (PILL_LABEL[status] || status).toLowerCase());
    if (after) after();
  }
  async function resetAccess(userId, email) {
    var r = await openModal({ title: 'Reset access', submitLabel: 'Email reset link', danger: true, body:
      '<p style="margin:0;color:var(--text-secondary)">Emails <b>' + esc(email || 'the user') + '</b> a one-time password-reset link (valid 60 minutes, single use). No password is generated or shown.</p>' +
      '<label class="sa-check"><input type="checkbox" name="revoke" checked> Also sign the user out of every active session</label>' +
      '<div class="sa-field"><label for="raReason">Reason</label><input class="form-input" id="raReason" name="reason" maxlength="300"></div>',
      onSubmit: function (f, fd) { return api('/api/super-admin/users/' + encodeURIComponent(userId) + '/reset-access', { method: 'POST', body: { reason: String(fd.get('reason') || ''), revoke_sessions: !!fd.get('revoke') } }); } });
    if (r) toast(r.message || 'Reset link sent');
  }

  async function viewAdmins(root, q) {
    root.innerHTML = header('Admins', 'Organization owners and admins across all organizations. Approving an admin = approving their organization\'s demo or subscription.',
      '<a class="btn btn-secondary btn-sm" href="#/demo">Demo queue</a><a class="btn btn-secondary btn-sm" href="#/subscriptions?status=awaiting">Confirmation queue</a>') + '<div id="aList"></div>';
    listView($('#aList', root), {
      url: function (p) { return '/api/super-admin/admins' + qs({ page: p.page, limit: p.limit, sort: p.sort, q: p.q, role: p.role, status: p.status }); },
      sort: '-joined_at', initial: { q: q.q || '' }, key: 'admins', exportUrl: function () { return reportUrl('admins'); },
      filters: [{ key: 'q', label: 'Search admin email, name or organization…' },
        { key: 'role', type: 'select', label: 'Role', options: [['', 'Owner & Admin'], ['owner', 'Owner'], ['admin', 'Admin']] },
        { key: 'status', type: 'select', label: 'Account', options: [['', 'Any account status'], ['active', 'Active'], ['suspended', 'Suspended'], ['disabled', 'Deactivated']] }],
      columns: [
        { label: 'Admin', render: function (a) { return '<a class="sa-link cell-main" href="#/users/' + esc(a.user_id) + '">' + esc(a.name || a.email) + '</a><span class="cell-sub">' + esc(a.email) + '</span>'; } },
        { label: 'Role', sort: 'role', render: function (a) { return esc(roleLabel(a.role)); } },
        { label: 'Organization', render: function (a) { return '<a class="sa-link" href="#/organizations/' + esc(a.organization_id) + '">' + esc(a.organization_name || short(a.organization_id)) + '</a><span class="cell-sub">' + pill(a.organization_status) + '</span>'; } },
        { label: 'Subscription', render: function (a) { return a.subscription_status ? pill(a.subscription_status) + '<span class="cell-sub">' + esc(a.plan_id || '') + '</span>' : '<span class="sa-muted">None</span>'; } },
        { label: 'Account', render: function (a) { return pill(a.user_status); } },
        { label: 'Last login', render: function (a) { return esc(ago(a.last_login)); } },
        { label: 'Joined', sort: 'joined_at', render: function (a) { return esc(fmtDate(a.joined_at)); } },
        { label: '', cls: 'num', render: function (a) {
          return '<div class="row-actions"><button type="button" class="btn btn-secondary btn-xs" data-reset>Reset access</button>' +
            (a.user_status === 'active' ? '<button type="button" class="btn btn-danger btn-xs" data-st="suspended">Suspend</button>' : '<button type="button" class="btn btn-secondary btn-xs" data-st="active">Activate</button>') + '</div>'; } }
      ],
      bindRow: function (tr, a, reload) {
        $('[data-reset]', tr).onclick = function () { resetAccess(a.user_id, a.email).catch(function (e) { toast(e.message, 'error'); }); };
        $$('[data-st]', tr).forEach(function (b) { b.onclick = function () { userStatusAction({ id: a.user_id, email: a.email, name: a.name }, b.getAttribute('data-st'), reload).catch(function (e) { toast(e.message, 'error'); }); }; });
      },
      empty: { title: 'No admins found' }
    });
  }

  async function viewUsers(root, q) {
    root.innerHTML = header('Users', 'Every account on the platform, across organizations.') + '<div id="uList"></div>';
    listView($('#uList', root), {
      url: function (p) { return '/api/super-admin/users' + qs({ page: p.page, limit: p.limit, sort: p.sort, search: p.search, status: p.status, role: p.role, org_id: p.org_id }); },
      rowsKey: 'users', sort: '-created_at', initial: { search: q.q || '', org_id: q.org || '' }, key: 'users',
      exportUrl: function (st) { return reportUrl('users', { status: st.status, organization_id: st.org_id }); },
      filters: [{ key: 'search', label: 'Search email or name…' },
        { key: 'status', type: 'select', label: 'Status', options: [['', 'All statuses'], ['active', 'Active'], ['suspended', 'Suspended'], ['disabled', 'Deactivated'], ['pending_approval', 'Pending approval']] },
        { key: 'role', type: 'select', label: 'Role', options: [['', 'Any role'], ['owner', 'Admin (Owner)'], ['admin', 'Admin'], ['manager', 'Manager'], ['member', 'User'], ['viewer', 'Viewer']] },
        { key: 'org_id', label: 'Organization ID' }],
      rowId: function (u) { return u.id; },
      columns: [
        { label: 'User', sort: 'email', render: function (u) { return '<a class="sa-link cell-main" href="#/users/' + esc(u.id) + '">' + esc(u.name || u.email) + '</a><span class="cell-sub">' + esc(u.email) + '</span>'; } },
        { label: 'Status', sort: 'status', render: function (u) { return pill(u.status || 'active') + (u.is_platform_admin ? ' ' + pill('info', 'Platform ' + titleCase(u.platform_role || 'staff')) : ''); } },
        { label: 'Organizations', render: function (u) { return (u.memberships || []).map(function (m) { return '<a class="sa-link" href="#/organizations/' + esc(m.organization_id) + '">' + esc(m.organization_name || short(m.organization_id)) + '</a> <span class="sa-small sa-muted">' + esc(roleLabel(m.role)) + '</span>'; }).join('<br>') || '<span class="sa-muted">—</span>'; } },
        { label: 'Last login', sort: 'last_login', render: function (u) { return esc(ago(u.last_login)); } },
        { label: 'Created', sort: 'created_at', render: function (u) { return esc(fmtDate(u.created_at)); } },
        { label: '', cls: 'num', render: function (u) {
          return u.is_platform_admin && u.platform_role === 'super_admin' ? '<span class="sa-small sa-muted">protected</span>' : '<div class="row-actions">' +
            ((u.status || 'active') === 'active' ? '<button type="button" class="btn btn-danger btn-xs" data-st="suspended">Suspend</button>' : '<button type="button" class="btn btn-secondary btn-xs" data-st="active">Activate</button>') + '</div>'; } }
      ],
      bindRow: function (tr, u, reload) { $$('[data-st]', tr).forEach(function (b) { b.onclick = function () { userStatusAction(u, b.getAttribute('data-st'), reload).catch(function (e) { toast(e.message, 'error'); }); }; }); },
      bulk: [{ label: 'Suspend', danger: true, run: async function (ids) {
        var r = await confirmDialog({ title: 'Suspend ' + ids.length + ' users', message: 'They are signed out immediately.', confirmLabel: 'Suspend all', reason: 'optional' });
        if (!r) return false;
        var fails = 0;
        for (var i = 0; i < ids.length; i++) { try { await api('/api/super-admin/users/' + ids[i] + '/status', { method: 'PATCH', body: { status: 'suspended', reason: r.reason } }); } catch (e) { fails++; } }
        toast((ids.length - fails) + ' suspended' + (fails ? ', ' + fails + ' skipped (protected or self)' : ''), fails ? 'warning' : 'success');
      } }, { label: 'Activate', run: async function (ids) {
        for (var i = 0; i < ids.length; i++) await api('/api/super-admin/users/' + ids[i] + '/status', { method: 'PATCH', body: { status: 'active' } });
        toast(ids.length + ' users activated');
      } }],
      empty: { title: 'No users found' }
    });
  }

  async function viewUserDetail(root, q, id) {
    var u = (await api('/api/super-admin/users/' + encodeURIComponent(id))).user;
    var us = u.usage || {};
    var reload = function () { route(); };
    var protectedAcct = u.is_platform_admin && u.platform_role === 'super_admin';
    setTitle(u.name || u.email, 'Customers › Users');
    root.innerHTML = '<div class="sa-row sa-small" style="margin-bottom:6px"><a class="sa-link" href="#/users">← Users</a></div>' +
      header(u.name || u.email, u.email + ' · joined ' + fmtDate(u.created_at),
        pill(u.status || 'active') + (protectedAcct ? pill('info', 'Super Admin') : '<button type="button" class="btn btn-secondary btn-sm" id="udReset">Reset access</button><button type="button" class="btn btn-secondary btn-sm" id="udRevoke">Sign out everywhere</button>' +
          ((u.status || 'active') !== 'active' ? '<button type="button" class="btn btn-secondary btn-sm" data-st="active">Activate</button>' : '<button type="button" class="btn btn-danger btn-sm" data-st="suspended">Suspend</button>') +
          (u.status !== 'disabled' ? '<button type="button" class="btn btn-danger btn-sm" data-st="disabled">Deactivate</button>' : ''))) +
      '<div class="sa-grid sa-kpis">' +
      kpi('Searches', fmtN(us.searches), fmtN(us.running) + ' running · ' + fmtN(us.failed) + ' failed', { tone: us.failed ? 'warn' : '' }) +
      kpi('Leads', fmtN(us.leads)) + kpi('Exports', fmtN(us.exports)) + kpi('Tokens consumed', fmtN(us.tokens_consumed)) +
      kpi('Active sessions', fmtN(u.active_sessions)) + kpi('Last login', ago(u.last_login)) + '</div>' +
      '<div class="sa-section" id="udTabs"></div>';
    if (!protectedAcct) {
      $('#udReset', root).onclick = function () { resetAccess(id, u.email).catch(function (e) { toast(e.message, 'error'); }); };
      $('#udRevoke', root).onclick = async function () {
        var r = await confirmDialog({ title: 'Sign out everywhere', message: 'Revokes every active session of ' + u.email + '.', confirmLabel: 'Revoke sessions', reason: 'optional' });
        if (!r) return;
        try { var out = await api('/api/super-admin/users/' + encodeURIComponent(id) + '/revoke-sessions', { method: 'POST', body: { reason: r.reason } }); toast(out.revoked + ' session(s) revoked'); reload(); } catch (e) { toast(e.message, 'error'); }
      };
      $$('[data-st]', root).forEach(function (b) { b.onclick = function () { userStatusAction({ id: id, email: u.email, name: u.name }, b.getAttribute('data-st'), reload).catch(function (e) { toast(e.message, 'error'); }); }; });
    }
    tabs($('#udTabs', root), [['profile', 'Profile'], ['searches', 'Searches'], ['leads', 'Leads'], ['sessions', 'Sessions'], ['activity', 'Audit history']], q.tab || 'profile', function (key, el) {
      if (key === 'profile') {
        el.innerHTML = '<div class="sa-grid sa-2"><div class="sa-card"><h3>Profile</h3><dl class="sa-kv"><dt>ID</dt><dd class="sa-mono">' + esc(id) + '</dd><dt>Email</dt><dd>' + esc(u.email) + '</dd><dt>Name</dt><dd>' + esc(u.name || '—') + '</dd><dt>Status</dt><dd>' + pill(u.status || 'active') + '</dd>' +
          '<dt>Platform role</dt><dd>' + esc(u.is_platform_admin ? titleCase(u.platform_role) : '—') + '</dd><dt>Created</dt><dd>' + esc(fmtDT(u.created_at)) + '</dd><dt>Last login</dt><dd>' + esc(fmtDT(u.last_login)) + '</dd>' +
          (u.password_changed_at ? '<dt>Password changed</dt><dd>' + esc(fmtDT(u.password_changed_at)) + '</dd>' : '') + '</dl></div>' +
          '<div class="sa-card"><h3>Organizations</h3>' + ((u.memberships || []).length ? '<ul class="sa-feed">' + u.memberships.map(function (m) {
            return '<li><div><a class="sa-link" href="#/organizations/' + esc(m.organization_id) + '">' + esc(m.organization_name) + '</a><div class="sa-small sa-muted">' + esc(roleLabel(m.role)) + '</div></div><span class="when">' + pill(m.status) + ' ' + pill(m.organization_status) + '</span></li>';
          }).join('') + '</ul>' : emptyState('No memberships')) + '</div></div>';
      } else if (key === 'searches') searchesList(el, { user_id: id });
      else if (key === 'leads') { el.innerHTML = '<div class="alert alert-info"><div class="alert-body">Leads are owned by organizations. Showing leads of this user\'s organizations.</div></div><div id="ulL" class="sa-section"></div>'; leadsList($('#ulL', el), { organization_id: ((u.memberships || [])[0] || {}).organization_id || '' }); }
      else if (key === 'sessions') sessionsList(el, { user_id: id });
      else if (key === 'activity') auditList(el, { user_id: id });
    });
  }

  // ════════════════════════════════════════════════════════
  //  DEMO MANAGEMENT
  // ════════════════════════════════════════════════════════
  function timeline(history, fields) {
    fields = fields || { status: 'status', at: 'at', by: 'by', note: 'note' };
    if (!history || !history.length) return emptyState('No history');
    return '<ol class="timeline">' + history.map(function (h, i) {
      var st = h[fields.status] || h.to || h.event;
      var bad = /cancel|reject|fail|expired|suspend/.test(String(st));
      return '<li class="timeline-item ' + (bad ? 'is-failed' : i === history.length - 1 ? 'is-current' : 'is-done') + '"><span class="timeline-dot" aria-hidden="true">' + (i + 1) + '</span><div><div class="timeline-title">' +
        esc(titleCase(h.from ? h.from + ' → ' + (h.to || '') : st)) + '</div><div class="timeline-desc">' + esc(fmtDT(h[fields.at] || h.created_at)) + (h[fields.by] ? ' · ' + esc(h[fields.by]) : '') + (h[fields.note] ? ' · ' + esc(h[fields.note]) : '') + '</div></div></li>';
    }).join('') + '</ol>';
  }

  async function demoAction(req, action, after) {
    var id = req.id || req._id;
    var base = '/api/super-admin/demo-requests/' + encodeURIComponent(id) + '/';
    if (action === 'approve') {
      var cfg = (await api('/api/super-admin/demo-config')).config;
      var r = await openModal({ title: 'Approve demo — ' + (req.company || req.name), submitLabel: 'Approve demo', body:
        '<p style="margin:0;color:var(--text-secondary)">Activates the organization in demo mode and emails the requester. Defaults come from the demo configuration; override for this account only if needed.</p>' +
        '<div class="sa-form-grid"><div class="sa-field"><label for="apDays">Duration (days)</label><input class="form-input" id="apDays" name="duration_days" type="number" min="1" max="365" value="' + esc(cfg.duration_days) + '"></div>' +
        '<div class="sa-field"><label for="apTok">Demo tokens</label><input class="form-input" id="apTok" name="tokens" type="number" min="0" value="' + esc(cfg.tokens) + '"></div></div>',
        onSubmit: function (f, fd) {
          var ov = {};
          if (+fd.get('duration_days') !== +cfg.duration_days) ov.duration_days = +fd.get('duration_days');
          if (+fd.get('tokens') !== +cfg.tokens) ov.tokens = +fd.get('tokens');
          return api(base + 'approve', { method: 'POST', body: { overrides: Object.keys(ov).length ? ov : null } });
        } });
      if (!r) return; toast('Demo approved');
    } else if (action === 'extend') {
      var e = await openModal({ title: 'Extend demo', submitLabel: 'Extend', body:
        '<div class="sa-form-grid"><div class="sa-field"><label for="exDays">Extra days</label><input class="form-input" id="exDays" name="days" type="number" min="0" max="365" value="7"></div>' +
        '<div class="sa-field"><label for="exTok">Extra tokens</label><input class="form-input" id="exTok" name="tokens" type="number" min="0" value="0"></div></div>',
        onSubmit: function (f, fd) {
          var days = +fd.get('days') || 0, tok = +fd.get('tokens') || 0;
          if (!days && !tok) throw new Error('Add days and/or tokens.');
          return api(base + 'extend', { method: 'POST', body: { days: days, tokens: tok } });
        } });
      if (!e) return; toast('Demo extended');
    } else {
      var labels = { reject: ['Reject demo request', 'The requester is told the request was not approved. Their organization stays blocked.', 'Reject'], cancel: ['Cancel demo', 'Ends the demo immediately; members lose access.', 'Cancel demo'] }[action];
      var c = await confirmDialog({ title: labels[0], message: labels[1], confirmLabel: labels[2], reason: 'required' });
      if (!c) return;
      await api(base + action, { method: 'POST', body: { reason: c.reason } });
      toast(action === 'reject' ? 'Request rejected' : 'Demo cancelled');
    }
    if (after) after();
  }
  function demoButtons(r) {
    var b = [];
    if (r.status === 'pending') b.push(['approve', 'Approve', 'btn-primary'], ['reject', 'Reject', 'btn-danger']);
    if (r.status === 'approved' || r.status === 'extended') b.push(['extend', 'Extend', 'btn-secondary'], ['cancel', 'Cancel', 'btn-danger']);
    return b;
  }
  function demoDrawer(id, after) {
    openDrawer('Demo request', async function (body, close) {
      var r = (await api('/api/super-admin/demo-requests/' + encodeURIComponent(id))).request;
      var d = r.demo || {}, t = d.tokens || {};
      body.innerHTML = '<dl class="sa-kv"><dt>Company</dt><dd>' + esc(r.company) + '</dd><dt>Name</dt><dd>' + esc(r.name) + '</dd><dt>Email</dt><dd>' + esc(r.email) + '</dd><dt>Phone</dt><dd>' + esc(r.phone || '—') + '</dd>' +
        '<dt>Requested</dt><dd>' + esc(fmtDT(r.created_at)) + '</dd><dt>Status</dt><dd>' + pill(r.status) + (r.status === 'converted' ? ' <span class="sa-small sa-muted">converted automatically when the subscription was confirmed</span>' : '') + '</dd>' +
        (r.message ? '<dt>Message</dt><dd>' + esc(r.message) + '</dd>' : '') +
        '<dt>Organization</dt><dd><a class="sa-link" href="#/organizations/' + esc(r.organization_id) + '">Open organization →</a></dd></dl>' +
        '<div class="sa-grid sa-kpis sa-section">' + kpi('Searches', fmtN((r.usage || {}).searches)) + kpi('Leads', fmtN((r.usage || {}).leads)) +
        kpi('Tokens used', fmtN(t.used || 0), t.allocated ? 'of ' + fmtN(t.allocated) : '') + kpi('Time left', d.is_demo ? (d.expired ? 'Expired' : d.days_remaining + ' days') : '—', d.expires_at ? 'until ' + fmtDate(d.expires_at) : '') + '</div>' +
        '<div class="sa-row sa-section" id="drBtns"></div><h4 class="sa-section">History</h4>' + timeline(r.history);
      var btns = demoButtons(r);
      $('#drBtns', body).innerHTML = btns.map(function (x) { return '<button type="button" class="btn btn-sm ' + x[2] + '" data-a="' + x[0] + '">' + x[1] + '</button>'; }).join('');
      $$('[data-a]', body).forEach(function (b) { b.onclick = function () { demoAction(r, b.getAttribute('data-a'), function () { close(); if (after) after(); }).catch(function (e) { toast(e.message, 'error'); }); }; });
    });
  }

  async function viewDemo(root, q) {
    root.innerHTML = header('Demo management', 'Signup = demo request. Nothing is usable until approved here. A demo converts automatically when its paid subscription is confirmed.') + '<div id="dmTabs"></div>';
    tabs($('#dmTabs', root), [['inbox', 'Inbox'], ['config', 'Demo configuration'], ['costs', 'Token costs']], q.tab || 'inbox', async function (key, el) {
      if (key === 'inbox') {
        el.innerHTML = '<div class="sa-chips" id="dmChips"></div><div id="dmList"></div>';
        var current = q.status || '';
        var lv;
        var renderChips = function (counts) {
          var all = Object.keys(counts || {}).reduce(function (a, k) { return a + counts[k]; }, 0);
          $('#dmChips', el).innerHTML = [['', 'All', all], ['pending', 'Pending'], ['approved', 'Approved'], ['extended', 'Extended'], ['converted', 'Converted'], ['rejected', 'Rejected'], ['cancelled', 'Cancelled']].map(function (c) {
            var n = c[2] != null ? c[2] : (counts || {})[c[0]] || 0;
            return '<button type="button" class="sa-chip' + (current === c[0] ? ' active' : '') + '" data-s="' + c[0] + '">' + esc(c[1]) + '<b>' + fmtN(n) + '</b></button>';
          }).join('');
          $$('[data-s]', el).forEach(function (b) { b.onclick = function () { current = b.getAttribute('data-s'); lv.state.status = current; lv.state.page = 1; lv.reload(); }; });
        };
        lv = listView($('#dmList', el), {
          url: function (p) { return '/api/super-admin/demo-requests' + qs({ page: p.page, limit: p.limit, sort: p.sort, q: p.q, status: p.status }); },
          sort: '-created_at', initial: { status: current, q: q.q || '' },
          filters: [{ key: 'q', label: 'Search company, name, email, phone…' }],
          onData: function (d) { renderChips(d.counts); },
          columns: [
            { label: 'Company', sort: 'company', render: function (r) { return '<span class="cell-main">' + esc(r.company) + '</span>'; } },
            { label: 'Name', sort: 'name', render: function (r) { return esc(r.name); } },
            { label: 'Email', render: function (r) { return esc(r.email); } },
            { label: 'Phone', render: function (r) { return esc(r.phone || '—'); } },
            { label: 'Requested', sort: 'created_at', render: function (r) { return esc(fmtDT(r.created_at)); } },
            { label: 'Status', sort: 'status', render: function (r) { return pill(r.status); } },
            { label: '', cls: 'num', render: function (r) { return '<div class="row-actions"><button type="button" class="btn btn-secondary btn-xs" data-a="review">Review</button>' + demoButtons(r).map(function (x) { return '<button type="button" class="btn btn-xs ' + x[2] + '" data-a="' + x[0] + '">' + x[1] + '</button>'; }).join('') + '</div>'; } }
          ],
          bindRow: function (tr, r, reload) {
            $$('[data-a]', tr).forEach(function (b) { b.onclick = function () {
              var a = b.getAttribute('data-a');
              if (a === 'review') return demoDrawer(r.id, reload);
              demoAction(r, a, reload).catch(function (e) { toast(e.message, 'error'); });
            }; });
          },
          empty: { title: 'No demo requests', desc: 'New signups from the website appear here.' }
        });
      } else if (key === 'config') {
        var data = await api('/api/super-admin/demo-config');
        var c = data.config;
        var num = function (k, label, hint) { return '<div class="sa-field"><label for="dc_' + k + '">' + esc(label) + '</label><input class="form-input" id="dc_' + k + '" name="' + k + '" type="number" min="0" value="' + esc(c[k]) + '">' + (hint ? '<span class="hint">' + esc(hint) + '</span>' : '') + '</div>'; };
        el.innerHTML = '<form class="sa-card" id="dcForm" novalidate><h3>What an approved demo gets</h3><div class="sa-form-grid">' +
          num('duration_days', 'Duration (days)') + num('tokens', 'Tokens granted') + num('max_searches', 'Max searches') + num('posts_per_search', 'Posts per search') +
          num('comments_per_post', 'Comments per post') + num('max_leads', 'Max leads') + num('max_users', 'Max users') + '</div>' +
          '<div class="sa-field sa-section"><label>Allowed platforms</label><div class="sa-row">' + (data.platforms || []).map(function (p) {
            return '<label class="sa-check"><input type="checkbox" name="allowed_platforms" value="' + esc(p) + '"' + ((c.allowed_platforms || []).indexOf(p) >= 0 ? ' checked' : '') + '> ' + esc(titleCase(p)) + '</label>';
          }).join('') + '</div></div><div class="sa-row sa-section">' +
          ['ai_enabled', 'exports_enabled', 'auto_approve'].map(function (k) { return '<label class="sa-check"><input type="checkbox" name="' + k + '"' + (c[k] ? ' checked' : '') + '> ' + esc({ ai_enabled: 'AI analysis enabled', exports_enabled: 'CSV exports enabled', auto_approve: 'Auto-approve new requests' }[k]) + '</label>'; }).join('') +
          '</div><div class="sa-err" id="dcErr" role="alert"></div><div class="sa-row sa-section"><button class="btn btn-primary btn-sm" type="submit">Save configuration</button><span class="sa-small sa-muted">Applies to demos approved from now on. Audited.</span></div></form>';
        $('#dcForm', el).onsubmit = async function (e) {
          e.preventDefault();
          var fd = new FormData(this), body = {};
          ['duration_days', 'tokens', 'max_searches', 'posts_per_search', 'comments_per_post', 'max_leads', 'max_users'].forEach(function (k) { body[k] = parseInt(fd.get(k), 10) || 0; });
          body.allowed_platforms = fd.getAll('allowed_platforms');
          ['ai_enabled', 'exports_enabled', 'auto_approve'].forEach(function (k) { body[k] = !!fd.get(k); });
          $('#dcErr', el).textContent = '';
          var btn = $('button[type=submit]', this);
          try { await busy(btn, function () { return api('/api/super-admin/demo-config', { method: 'PUT', body: body }); }); toast('Demo configuration saved'); }
          catch (err) { $('#dcErr', el).textContent = err.message; }
        };
      } else {
        var costs = (await api('/api/super-admin/token-costs')).costs;
        var help = { search: 'Starting one URL search', collect: 'Collecting more posts / comments', ai_call: 'One AI analysis of a comment', export: 'One CSV export' };
        el.innerHTML = '<form class="sa-card" id="tcForm" novalidate><h3>Tokens charged per action</h3><div class="sa-form-grid">' + Object.keys(costs).map(function (k) {
          return '<div class="sa-field"><label for="tc_' + k + '">' + esc(titleCase(k)) + '</label><input class="form-input" id="tc_' + k + '" name="' + esc(k) + '" type="number" min="0" value="' + esc(costs[k]) + '"><span class="hint">' + esc(help[k] || '') + '</span></div>';
        }).join('') + '</div><div class="sa-err" id="tcErr" role="alert"></div><div class="sa-row sa-section"><button class="btn btn-primary btn-sm" type="submit">Save token costs</button><span class="sa-small sa-muted">Applies to every organization immediately. Audited.</span></div></form>';
        $('#tcForm', el).onsubmit = async function (e) {
          e.preventDefault();
          var fd = new FormData(this), body = {};
          Object.keys(costs).forEach(function (k) { body[k] = parseInt(fd.get(k), 10) || 0; });
          var btn = $('button[type=submit]', this);
          try { await busy(btn, function () { return api('/api/super-admin/token-costs', { method: 'PUT', body: body }); }); toast('Token costs saved'); }
          catch (err) { $('#tcErr', el).textContent = err.message; }
        };
      }
    });
  }

  // ════════════════════════════════════════════════════════
  //  PLANS & PRICING
  // ════════════════════════════════════════════════════════
  async function planEditor(plan, after) {
    var schema = await api('/api/super-admin/plans/schema');
    var p = plan || { status: 'inactive', is_public: false, currency: 'USD', limits: {}, features: [] };
    var lim = p.limits || {};
    var feats = p.features || [];
    var body = '<div class="sa-form-grid">' +
      '<div class="sa-field"><label for="peName">Name *</label><input class="form-input" id="peName" name="name" required maxlength="80" value="' + esc(p.name || '') + '"></div>' +
      '<div class="sa-field"><label for="peSlug">Slug' + (plan ? ' <span class="sa-muted">(fixed)</span>' : '') + '</label><input class="form-input" id="peSlug" name="slug" maxlength="60" value="' + esc(p.slug || '') + '"' + (plan ? ' readonly' : '') + '></div>' +
      '<div class="sa-field"><label for="peStatus">Status</label><select class="form-select" id="peStatus" name="status">' + ['active', 'inactive', 'archived'].map(function (s) { return '<option' + (p.status === s ? ' selected' : '') + '>' + s + '</option>'; }).join('') + '</select></div>' +
      '<div class="sa-field"><label for="peCur">Currency</label><input class="form-input" id="peCur" name="currency" maxlength="3" value="' + esc(p.currency || 'USD') + '"></div>' +
      '<div class="sa-field"><label for="peM">Price / month</label><input class="form-input" id="peM" name="price_monthly" type="number" min="0" step="0.01" value="' + esc(p.price_monthly || 0) + '"></div>' +
      '<div class="sa-field"><label for="peY">Price / year</label><input class="form-input" id="peY" name="price_yearly" type="number" min="0" step="0.01" value="' + esc(p.price_yearly || 0) + '"></div>' +
      '<div class="sa-field"><label for="peT">Trial days</label><input class="form-input" id="peT" name="trial_days" type="number" min="0" value="' + esc(p.trial_days || 0) + '"></div>' +
      '<div class="sa-field"><label for="peO">Display order</label><input class="form-input" id="peO" name="display_order" type="number" value="' + esc(p.display_order == null ? 99 : p.display_order) + '"></div></div>' +
      '<div class="sa-field"><label for="peD">Description</label><textarea class="form-textarea" id="peD" name="description" rows="2" style="min-height:60px">' + esc(p.description || '') + '</textarea></div>' +
      '<div class="sa-row">' + [['is_public', 'Shown on the public pricing page'], ['is_default', 'Default / highlighted plan'], ['is_trial', 'Trial plan']].map(function (x) { return '<label class="sa-check"><input type="checkbox" name="' + x[0] + '"' + (p[x[0]] ? ' checked' : '') + '> ' + esc(x[1]) + '</label>'; }).join('') + '</div>' +
      '<h4 style="margin:6px 0 0">Limits</h4><div class="sa-form-grid">' + schema.limit_keys.map(function (k) {
        return '<div class="sa-field"><label for="pl_' + k + '">' + esc(titleCase(k)) + '</label><input class="form-input" id="pl_' + k + '" name="limit:' + k + '" type="number" min="0" step="1" value="' + esc(lim[k] == null ? '' : lim[k]) + '" placeholder="not set"></div>';
      }).join('') + '</div>' +
      '<h4 style="margin:6px 0 0">Features</h4><div class="sa-form-grid">' + Object.keys(schema.features).map(function (k) {
        return '<label class="sa-check"><input type="checkbox" name="feature" value="' + esc(k) + '"' + (feats.indexOf(k) >= 0 ? ' checked' : '') + '> ' + esc(schema.features[k]) + '</label>';
      }).join('') + '</div>';
    var saved = await openModal({ title: plan ? 'Edit plan — ' + p.name : 'New plan', size: 'lg', submitLabel: plan ? 'Save plan' : 'Create plan', body: body,
      onSubmit: function (f, fd) {
        var out = { name: String(fd.get('name') || '').trim(), status: fd.get('status'), currency: String(fd.get('currency') || 'USD').toUpperCase(),
          price_monthly: parseFloat(fd.get('price_monthly')) || 0, price_yearly: parseFloat(fd.get('price_yearly')) || 0,
          trial_days: parseInt(fd.get('trial_days'), 10) || 0, display_order: parseInt(fd.get('display_order'), 10) || 0,
          description: fd.get('description'), is_public: !!fd.get('is_public'), is_default: !!fd.get('is_default'), is_trial: !!fd.get('is_trial'),
          features: fd.getAll('feature'), limits: {} };
        if (!out.name) throw new Error('Name is required.');
        schema.limit_keys.forEach(function (k) { var v = fd.get('limit:' + k); if (v !== '' && v != null) out.limits[k] = parseInt(v, 10); });
        if (!plan) { if (fd.get('slug')) out.slug = String(fd.get('slug')).trim().toLowerCase(); return api('/api/super-admin/plans', { method: 'POST', body: out }); }
        return api('/api/super-admin/plans/' + encodeURIComponent(plan._id || plan.id), { method: 'PATCH', body: out });
      } });
    if (saved) { toast(plan ? 'Plan saved' : 'Plan created'); if (after) after(); }
  }

  async function viewPlans(root) {
    var data = await api('/api/super-admin/plans');
    var plans = data.plans || [];
    var reload = function () { route(); };
    var live = plans.filter(function (p) { return p.status === 'active'; });
    root.innerHTML = header('Plans', 'The single plan catalog — limits and features drive entitlements, billing and the public pricing page.',
      '<a class="btn btn-secondary btn-sm" href="#/pricing">Preview pricing page</a><button type="button" class="btn btn-primary btn-sm" id="plNew">+ New plan</button>') +
      '<div class="sa-grid sa-kpis">' + kpi('Plans', fmtN(plans.length), fmtN(live.length) + ' active', { icon: 'layers' }) +
      kpi('Public', fmtN(plans.filter(function (p) { return p.is_public && p.status === 'active'; }).length), 'shown on the website', { href: '#/pricing', icon: 'globe' }) +
      kpi('Live subscriptions', fmtN(plans.reduce(function (a, p) { return a + (p.live_subscriptions || 0); }, 0)), 'across all plans', { href: '#/subscriptions', icon: 'repeat' }) + '</div>' +
      '<div class="sa-section" id="plList"></div>';
    listView($('#plList', root), {
      data: function () { return Promise.resolve(plans); }, key: 'plans', sort: 'display_order',
      filters: [{ key: 'q', label: 'Search plan name or slug…' }, { key: 'status', type: 'select', label: 'Status', options: [['', 'All statuses'], ['active', 'Active'], ['inactive', 'Inactive'], ['archived', 'Archived']] }],
      columns: [
        { label: 'Plan', sort: 'name', render: function (p) { return '<span class="cell-main">' + esc(p.name) + '</span><span class="cell-sub sa-mono">' + esc(p.slug) + '</span>'; } },
        { label: 'Status', sort: 'status', render: function (p) { return pill(p.status); } },
        { label: 'Visibility', render: function (p) { return (p.is_public ? badge('Public', 'info') : badge('Hidden', 'muted')) + (p.is_default ? ' ' + badge('Default', 'primary') : ''); } },
        { label: 'Monthly', sort: 'price_monthly', cls: 'num', render: function (p) { return esc(fmtMoney(p.price_monthly, p.currency)); } },
        { label: 'Yearly', sort: 'price_yearly', cls: 'num', render: function (p) { return esc(fmtMoney(p.price_yearly, p.currency)); } },
        { label: 'Trial', sort: 'trial_days', cls: 'num', render: function (p) { return esc(p.trial_days || 0) + 'd'; } },
        { label: 'Key limits', render: function (p) { var l = p.limits || {}; return '<span class="sa-small">' + esc(['monthly_tokens', 'monthly_searches', 'team_members'].filter(function (k) { return l[k] != null; }).map(function (k) { return titleCase(k) + ': ' + fmtN(l[k]); }).join(' · ') || '—') + '</span>'; } },
        { label: 'Features', cls: 'num', render: function (p) { return String((p.features || []).length); } },
        { label: 'Live subs', sort: 'live_subscriptions', cls: 'num', render: function (p) { return fmtN(p.live_subscriptions); } },
        { label: 'Order', sort: 'display_order', cls: 'num', hidden: true, render: function (p) { return esc(p.display_order == null ? '—' : p.display_order); } },
        { label: '', cls: 'num', render: function (p) {
          return '<div class="row-actions"><button type="button" class="btn btn-secondary btn-xs" data-edit>Edit</button>' + (p.status !== 'archived' ? '<button type="button" class="btn btn-danger btn-xs" data-arch' + (p.live_subscriptions ? ' disabled title="Plan has live subscriptions"' : '') + '>Archive</button>' : '') + '</div>'; } }
      ],
      rowClick: function (p) { planEditor(p, reload).catch(function (e) { toast(e.message, 'error'); }); },
      bindRow: function (tr, p) {
        $('[data-edit]', tr).onclick = function () { planEditor(p, reload).catch(function (e) { toast(e.message, 'error'); }); };
        var a = $('[data-arch]', tr);
        if (a) a.onclick = async function () {
          var r = await confirmDialog({ title: 'Archive plan', message: '"' + p.name + '" disappears from pricing and can no longer be purchased. Existing data is untouched.', confirmLabel: 'Archive' });
          if (!r) return;
          try { await api('/api/super-admin/plans/' + encodeURIComponent(p._id), { method: 'DELETE' }); toast('Plan archived'); reload(); } catch (e) { toast(e.message, 'error'); }
        };
      },
      empty: { title: 'No plans yet', desc: 'Create the first plan to start selling.' }
    });
    $('#plNew', root).onclick = function () { planEditor(null, reload).catch(function (e) { toast(e.message, 'error'); }); };
  }
  async function viewPricing(root) {
    var res = await fetch('/api/public/pricing', { credentials: 'same-origin' });
    if (!res.ok) { var er = new Error('Could not load the public pricing feed'); er.status = res.status; throw er; }
    var plans = ((await res.json()).plans || []).sort(function (a, b) { return (a.display_order || 0) - (b.display_order || 0); });
    var schema = await api('/api/super-admin/plans/schema');
    var cycle = 'monthly';
    function render() {
      $('#prCards', root).innerHTML = plans.length ? plans.map(function (p) {
        var price = cycle === 'yearly' ? p.price_yearly : p.price_monthly;
        return '<div class="sa-card" style="' + (p.popular ? 'border-color:var(--primary);box-shadow:var(--glow)' : '') + '"><h3>' + esc(p.name) + (p.popular ? pill('active', 'Popular') : '') + '</h3>' +
          '<div style="font-size:26px;font-weight:800">' + esc(fmtMoney(price, p.currency)) + '<span class="sa-small sa-muted"> / ' + (cycle === 'yearly' ? 'year' : 'month') + '</span></div>' +
          (p.trial_days ? '<div class="sa-small sa-muted">' + esc(p.trial_days) + '-day trial</div>' : '') + '<p class="sa-small" style="color:var(--text-secondary)">' + esc(p.description || '') + '</p>' +
          '<ul style="margin:8px 0 0;padding-left:18px;font-size:13px">' + (p.highlights || []).map(function (h) { return '<li>' + esc(h) + '</li>'; }).join('') +
          (p.features || []).map(function (f) { return '<li>' + esc(schema.features[f] || f) + '</li>'; }).join('') + '</ul></div>';
      }).join('') : emptyState('No public plans', 'Only plans that are Active and Public appear on the website.');
    }
    root.innerHTML = header('Pricing preview', 'Exactly what the public website shows (GET /api/public/pricing). Edit prices and limits in Plans.',
      '<div class="sa-chips" style="margin:0"><button type="button" class="sa-chip active" data-c="monthly">Monthly</button><button type="button" class="sa-chip" data-c="yearly">Yearly</button></div><a class="btn btn-secondary btn-sm" href="#/plans">Edit plans</a><a class="btn btn-secondary btn-sm" href="/pricing" target="_blank" rel="noopener">Open website ↗</a>') +
      '<div class="sa-grid sa-3" id="prCards"></div>';
    $$('[data-c]', root).forEach(function (b) { b.onclick = function () { cycle = b.getAttribute('data-c'); $$('[data-c]', root).forEach(function (x) { x.classList.toggle('active', x === b); }); render(); }; });
    render();
  }

  // ════════════════════════════════════════════════════════
  //  SUBSCRIPTIONS & PAYMENTS
  // ════════════════════════════════════════════════════════
  async function subStatus(sub, target, after) {
    var labels = { cancelled: ['Cancel / reject subscription', sub.status === 'pending_admin_confirmation' ? 'Rejects this paid request. Any captured payment is flagged "refund required".' : 'Cancels the subscription; the organization loses paid access.', 'Cancel subscription', 'required'],
      suspended: ['Suspend subscription', 'Suspends the subscription and the organization.', 'Suspend', 'required'],
      active: ['Resume subscription', 'Re-activates the subscription and the organization.', 'Resume', 'optional'],
      expired: ['Expire subscription', 'Marks the subscription as expired now.', 'Expire', 'required'] }[target];
    var r = await confirmDialog({ title: labels[0], message: labels[1], confirmLabel: labels[2], reason: labels[3], danger: target !== 'active' });
    if (!r) return false;
    await api('/api/super-admin/subscriptions/' + encodeURIComponent(sub.id || sub._id) + '/status', { method: 'POST', body: { status: target, reason: r.reason } });
    toast('Subscription ' + (PILL_LABEL[target] || target).toLowerCase());
    if (after) after();
  }
  async function subConfirm(sub, after) {
    var r = await confirmDialog({ title: 'Confirm subscription', message: 'Activates plan "' + sub.plan_id + '" for ' + (sub.organization_name || 'this organization') + ', grants its tokens and converts any demo. This is the only path to ACTIVE.', confirmLabel: 'Confirm & activate', danger: false });
    if (!r) return false;
    await api('/api/super-admin/subscriptions/' + encodeURIComponent(sub.id || sub._id) + '/confirm', { method: 'POST' });
    toast('Subscription confirmed and activated');
    if (after) after();
  }
  async function changePlan(sub, after) {
    var opts = await plansOptions(sub.plan_id);
    var r = await openModal({ title: 'Change plan (upgrade / downgrade)', submitLabel: 'Change plan', body:
      '<div class="sa-field"><label for="cpPlan">New plan</label><select class="form-select" id="cpPlan" name="plan">' + opts + '</select></div>' +
      '<div class="sa-field"><label for="cpCycle">Billing cycle</label><select class="form-select" id="cpCycle" name="billing_cycle"><option value="monthly"' + (sub.billing_cycle !== 'yearly' ? ' selected' : '') + '>Monthly</option><option value="yearly"' + (sub.billing_cycle === 'yearly' ? ' selected' : '') + '>Yearly</option></select></div>' +
      '<div class="sa-field"><label for="cpReason">Reason</label><input class="form-input" id="cpReason" name="reason" maxlength="300"></div><p class="sa-small sa-muted" style="margin:0">Downgrades are refused while the organization has more active members than the new plan allows.</p>',
      onSubmit: function (f, fd) { return api('/api/super-admin/subscriptions/' + encodeURIComponent(sub.id || sub._id) + '/change-plan', { method: 'POST', body: { plan: fd.get('plan'), billing_cycle: fd.get('billing_cycle'), reason: fd.get('reason') || '' } }); } });
    if (r) { toast('Plan changed'); if (after) after(); }
  }
  async function extendSub(sub, after) {
    var r = await openModal({ title: 'Extend subscription period', submitLabel: 'Extend', body:
      '<div class="sa-field"><label for="esDays">Days to add</label><input class="form-input" id="esDays" name="days" type="number" min="1" max="365" value="7"></div>' +
      '<div class="sa-field"><label for="esReason">Reason *</label><input class="form-input" id="esReason" name="reason" maxlength="300" required></div>',
      onSubmit: function (f, fd) {
        if (!String(fd.get('reason') || '').trim()) throw new Error('A reason is required.');
        return api('/api/super-admin/subscriptions/' + encodeURIComponent(sub.id || sub._id) + '/extend', { method: 'POST', body: { days: parseInt(fd.get('days'), 10) || 0, reason: String(fd.get('reason')).trim() } });
      } });
    if (r) { toast('Subscription extended'); if (after) after(); }
  }
  function subActions(s) {
    var a = [];
    switch (s.status) {
      case 'pending_admin_confirmation': a.push(['confirm', 'Confirm', 'btn-primary'], ['cancelled', 'Reject', 'btn-danger']); break;
      case 'active': case 'trialing': a.push(['plan', 'Change plan', 'btn-secondary'], ['extend', 'Extend', 'btn-secondary'], ['suspended', 'Suspend', 'btn-danger'], ['cancelled', 'Cancel', 'btn-danger']); if (s.status === 'active') a.push(['expired', 'Expire', 'btn-danger']); break;
      case 'suspended': a.push(['active', 'Resume', 'btn-primary'], ['extend', 'Extend', 'btn-secondary'], ['cancelled', 'Cancel', 'btn-danger'], ['expired', 'Expire', 'btn-danger']); break;
      case 'pending_payment': case 'payment_received': a.push(['cancelled', 'Cancel', 'btn-danger']); break;
      case 'expired': a.push(['cancelled', 'Cancel', 'btn-danger']); break;
    }
    return a;
  }
  function runSubAction(s, key, after) {
    var p = key === 'confirm' ? subConfirm(s, after) : key === 'plan' ? changePlan(s, after) : key === 'extend' ? extendSub(s, after) : subStatus(s, key, after);
    return p.catch(function (e) { toast(e.message, 'error'); });
  }
  function subDrawer(id, after) {
    openDrawer('Subscription', async function (body, close, rerun) {
      var d = await api('/api/super-admin/subscriptions/' + encodeURIComponent(id) + '/detail');
      var s = d.subscription; s.organization_name = (d.organization || {}).name;
      body.innerHTML = '<dl class="sa-kv"><dt>Organization</dt><dd><a class="sa-link" href="#/organizations/' + esc(s.organization_id) + '">' + esc(s.organization_name || s.organization_id) + '</a></dd>' +
        '<dt>Plan</dt><dd>' + esc(s.plan_id) + '</dd><dt>Status</dt><dd>' + pill(s.status) + '</dd><dt>Amount</dt><dd>' + esc(fmtMoney(s.amount, s.currency)) + ' / ' + esc(s.billing_cycle || '—') + '</dd>' +
        '<dt>Provider</dt><dd>' + esc(s.provider || '—') + '</dd><dt>Started</dt><dd>' + esc(fmtDT(s.started_at)) + '</dd><dt>Period end</dt><dd>' + esc(fmtDT(s.current_period_end)) + '</dd>' +
        (s.requested_by_email ? '<dt>Requested by</dt><dd>' + esc(s.requested_by_email) + '</dd>' : '') + (s.cancel_reason ? '<dt>Cancel reason</dt><dd>' + esc(s.cancel_reason) + '</dd>' : '') + '</dl>' +
        '<div class="sa-row sa-section" id="sdBtns">' + subActions(s).map(function (x) { return '<button type="button" class="btn btn-sm ' + x[2] + '" data-a="' + x[0] + '">' + x[1] + '</button>'; }).join('') + '</div>' +
        '<h4 class="sa-section">Subscription history</h4>' + timeline(s.status_history || []) +
        '<h4 class="sa-section">Payments</h4>' + (d.payments.length ? '<div class="sa-table-wrap"><table class="sa-table"><thead><tr><th>When</th><th class="num">Amount</th><th>Status</th><th>Verified via</th></tr></thead><tbody>' + d.payments.map(function (p) {
          return '<tr><td>' + esc(fmtDT(p.created_at)) + '</td><td class="num">' + esc(fmtMoney(p.amount, p.currency)) + '</td><td>' + pill(p.status) + (p.refund_required ? ' ' + pill('refund_due') : '') + '</td><td>' + esc(p.verified_via || p.provider || '—') + '</td></tr>';
        }).join('') + '</tbody></table></div>' : emptyState('No payments')) +
        '<h4 class="sa-section">Payment events</h4>' + timeline(d.events, { status: 'event', at: 'created_at', by: 'by', note: 'note' });
      $$('[data-a]', body).forEach(function (b) { b.onclick = function () { runSubAction(s, b.getAttribute('data-a'), function () { rerun(); if (after) after(); }); }; });
    });
  }

  async function viewSubscriptions(root, q) {
    root.innerHTML = header('Subscriptions', 'Payment success never activates anything: verified payments wait in the confirmation queue until you confirm them.',
      '<a class="btn btn-secondary btn-sm" href="#/plans">Plans</a><a class="btn btn-secondary btn-sm" href="#/payments">Payments</a>') + '<div id="sbTabs"></div>';
    tabs($('#sbTabs', root), [['queue', 'Confirmation queue'], ['all', 'All subscriptions']], q.status && q.status !== 'awaiting' ? 'all' : (q.tab || 'queue'), function (key, el) {
      if (key === 'queue') {
        listView(el, {
          url: function (p) { return '/api/super-admin/subscriptions/queue' + qs({ page: p.page, limit: p.limit }); }, key: 'sub-queue',
          exportUrl: function () { return reportUrl('subscriptions', { status: 'pending_admin_confirmation' }); },
          columns: [
            { label: 'Organization', render: function (s) { return '<a class="sa-link cell-main" href="#/organizations/' + esc(s.organization_id) + '">' + esc(s.organization_name || short(s.organization_id)) + '</a><span class="cell-sub">' + esc(s.requested_by_email || '') + '</span>'; } },
            { label: 'Plan', render: function (s) { return esc(s.plan_id) + '<span class="cell-sub">' + esc(s.billing_cycle || '') + '</span>'; } },
            { label: 'Amount', cls: 'num', render: function (s) { return esc(fmtMoney(s.amount, s.currency)); } },
            { label: 'Payment', render: function (s) { return pill('succeeded', 'Verified') + '<span class="cell-sub">' + esc(s.provider || '') + '</span>'; } },
            { label: 'Waiting since', render: function (s) { return esc(ago(s.payment_received_at || s.updated_at || s.created_at)); } },
            { label: '', cls: 'num', render: function () { return '<div class="row-actions"><button type="button" class="btn btn-secondary btn-xs" data-a="view">Details</button><button type="button" class="btn btn-primary btn-xs" data-a="confirm">Confirm</button><button type="button" class="btn btn-danger btn-xs" data-a="cancelled">Reject</button></div>'; } }
          ],
          bindRow: function (tr, s, reload) { $$('[data-a]', tr).forEach(function (b) { b.onclick = function () { var a = b.getAttribute('data-a'); if (a === 'view') subDrawer(s.id, reload); else runSubAction(s, a, reload); }; }); },
          empty: { title: 'Queue is empty', desc: 'Verified payments awaiting your confirmation appear here.' }
        });
      } else {
        listView(el, {
          url: function (p) { return '/api/super-admin/subscriptions' + qs({ page: p.page, limit: p.limit, sort: p.sort, status: p.status, q: p.q, plan: p.plan }); },
          rowsKey: 'subscriptions', sort: '-created_at', initial: { status: q.status || '' }, key: 'subscriptions',
          exportUrl: function (st) { return reportUrl('subscriptions', { status: st.status }); },
          filters: [{ key: 'q', label: 'Organization name…' }, { key: 'status', type: 'select', label: 'Status', options: [['', 'All statuses'], ['awaiting', 'Awaiting confirmation'], ['pending', 'Pending payment'], ['active', 'Active'], ['trialing', 'Trialing'], ['suspended', 'Suspended'], ['expired', 'Expired'], ['cancelled', 'Cancelled']] }, { key: 'plan', label: 'Plan slug' }],
          columns: [
            { label: 'Organization', render: function (s) { return '<a class="sa-link cell-main" href="#/organizations/' + esc(s.organization_id) + '">' + esc(s.organization_name) + '</a>'; } },
            { label: 'Plan', render: function (s) { return esc(s.plan_id); } },
            { label: 'Status', sort: 'status', render: function (s) { return pill(s.status); } },
            { label: 'Amount', sort: 'amount', cls: 'num', render: function (s) { return esc(fmtMoney(s.amount, s.currency)); } },
            { label: 'Cycle', render: function (s) { return esc(s.billing_cycle || '—'); } },
            { label: 'Period end', render: function (s) { return esc(fmtDate(s.current_period_end)); } },
            { label: 'Created', sort: 'created_at', render: function (s) { return esc(fmtDate(s.created_at)); } },
            { label: '', cls: 'num', render: function () { return '<button type="button" class="btn btn-secondary btn-xs" data-a="view">Manage</button>'; } }
          ],
          rowClick: function (s) { subDrawer(s.id); },
          bindRow: function (tr, s, reload) { $('[data-a]', tr).onclick = function () { subDrawer(s.id, reload); }; },
          empty: { title: 'No subscriptions' }
        });
      }
    });
  }

  async function viewPayments(root, q) {
    root.innerHTML = header('Payments', 'Provider-verified payments, failures and refunds. Only signed webhooks / provider checks can mark a payment succeeded.',
      '<a class="btn btn-secondary btn-sm" href="#/subscriptions">Subscriptions</a>') + '<div class="sa-grid sa-kpis" id="pySum"></div><div class="sa-section" id="pyList"></div>';
    listView($('#pyList', root), {
      url: function (p) { return '/api/super-admin/payments' + qs({ page: p.page, limit: p.limit, sort: p.sort, status: p.status, q: p.q }); },
      sort: '-created_at', initial: { status: q.status || '' }, key: 'payments',
      exportUrl: function (st) { return reportUrl('payments', { status: st.status }); },
      filters: [{ key: 'q', label: 'Organization name…' }, { key: 'status', type: 'select', label: 'Status', options: [['', 'All'], ['pending', 'Pending'], ['succeeded', 'Successful'], ['failed', 'Failed'], ['refunded', 'Refunded / refund due']] }],
      onData: function (d) {
        var s = d.summary || {}, cur = ((d.currencies || [])[0]) || ((d.items || [])[0] || {}).currency;
        var amt = function (x) { // one figure per currency, never summed across currencies
          x = x || {}; var m = x.amount_by_currency || {}, ks = Object.keys(m);
          if (ks.length > 1) return ks.map(function (k) { return fmtMoney(m[k], k); }).join(' · ');
          return fmtMoney(ks.length ? m[ks[0]] : (x.amount || 0), ks[0] || cur);
        };
        $('#pySum', root).innerHTML = kpi('Successful', fmtN((s.succeeded || {}).count || 0), esc(amt(s.succeeded))) +
          kpi('Pending', fmtN((s.pending || {}).count || 0), esc(amt(s.pending))) +
          kpi('Failed', fmtN((s.failed || {}).count || 0), '', { tone: (s.failed || {}).count ? 'bad' : '' }) +
          kpi('Refund required', fmtN((s.refund_required || {}).count || 0), 'paid, then rejected/cancelled', { tone: (s.refund_required || {}).count ? 'warn' : '' });
      },
      columns: [
        { label: 'Organization', render: function (p) { return '<a class="sa-link cell-main" href="#/organizations/' + esc(p.organization_id) + '">' + esc(p.organization_name || short(p.organization_id)) + '</a><span class="cell-sub">' + esc(p.plan_id || '') + '</span>'; } },
        { label: 'Amount', sort: 'amount', cls: 'num', render: function (p) { return esc(fmtMoney(p.amount_paid || p.amount, p.currency)); } },
        { label: 'Status', sort: 'status', render: function (p) { return pill(p.status) + (p.refund_required ? ' ' + pill('refund_due') : ''); } },
        { label: 'Verification', render: function (p) { return esc(p.verified_via || '—') + '<span class="cell-sub">' + esc(p.provider || '') + '</span>'; } },
        { label: 'Subscription', render: function (p) { return pill(p.subscription_status); } },
        { label: 'Invoice', render: function (p) { return pill({ paid: 'paid', refund_due: 'refund_due', void: 'void', open: 'pending' }[p.invoice_status] || 'muted', titleCase(p.invoice_status)); } },
        { label: 'Created', sort: 'created_at', render: function (p) { return esc(fmtDT(p.created_at)); } }
      ],
      rowClick: function (p) {
        openDrawer('Payment events', async function (body) {
          body.innerHTML = '<dl class="sa-kv"><dt>Organization</dt><dd>' + esc(p.organization_name || p.organization_id) + '</dd><dt>Amount</dt><dd>' + esc(fmtMoney(p.amount_paid || p.amount, p.currency)) + '</dd><dt>Status</dt><dd>' + pill(p.status) + '</dd>' +
            (p.failure_message ? '<dt>Failure</dt><dd>' + esc(p.failure_message) + '</dd>' : '') + '</dl><div class="sa-row sa-section"><button type="button" class="btn btn-secondary btn-sm" id="pySub">Open subscription</button></div><h4 class="sa-section">Timeline</h4>' +
            timeline(p.events || [], { status: 'event', at: 'created_at', by: 'by', note: 'note' });
          $('#pySub', body).onclick = function () { subDrawer(p.subscription_id); };
        });
      },
      empty: { title: 'No payments' }
    });
  }

  // ════════════════════════════════════════════════════════
  //  TOKENS & USAGE
  // ════════════════════════════════════════════════════════
  async function viewTokens(root) {
    var s = await api('/api/super-admin/tokens/summary');
    var t = s.totals;
    function tbl(rows, cols, empty) {
      return rows.length ? '<div class="sa-table-wrap"><table class="sa-table"><thead><tr>' + cols.map(function (c) { return '<th' + (c[2] ? ' class="num"' : '') + '>' + esc(c[0]) + '</th>'; }).join('') + '</tr></thead><tbody>' +
        rows.map(function (r) { return '<tr>' + cols.map(function (c) { return '<td' + (c[2] ? ' class="num"' : '') + '>' + c[1](r) + '</td>'; }).join('') + '</tr>'; }).join('') + '</tbody></table></div>' : emptyState(empty);
    }
    var orgCell = function (r) { return '<a class="sa-link" href="#/organizations/' + esc(r.organization_id) + '?tab=tokens">' + esc(r.organization_name || short(r.organization_id)) + '</a>'; };
    root.innerHTML = header('Tokens & usage', 'Balances, consumption, warnings and anomalies across all organizations.',
      '<a class="btn btn-secondary btn-sm" href="#/demo?tab=costs">Token costs</a>') +
      '<div class="sa-grid sa-kpis">' + kpi('Allocated', fmtN(t.allocated), fmtN(t.organizations) + ' metered orgs') + kpi('Used', fmtN(t.used), usageBar(t.allocated ? t.used * 100 / t.allocated : 0)) +
      kpi('Remaining', fmtN(t.remaining)) + kpi('Consumed (24h)', fmtN(s.consumed_24h)) +
      kpi('Usage warnings', fmtN(s.warnings.length), '≥ 80% used', { tone: s.warnings.length ? 'warn' : '' }) + kpi('Anomalies', fmtN(s.anomalies.length), '24h > 3× daily avg', { tone: s.anomalies.length ? 'bad' : '' }) + '</div>' +
      '<div class="alert alert-info sa-section"><div class="alert-body"><div class="alert-title">Over-limit behaviour</div><div>' + esc(s.over_limit_behaviour) + '</div></div></div>' +
      '<div class="sa-grid sa-2 sa-section"><div class="sa-card"><h3>Usage warnings (≥ 80%)</h3>' + tbl(s.warnings, [['Organization', orgCell], ['Used', function (r) { return usageBar(r.percentage); }], ['Remaining', function (r) { return fmtN(r.remaining); }, 1]], 'No organization above 80%') + '</div>' +
      '<div class="sa-card"><h3>Anomalies (last 24h)</h3>' + tbl(s.anomalies, [['Organization', orgCell], ['Last 24h', function (r) { return fmtN(r.last_24h); }, 1], ['Daily avg', function (r) { return fmtN(r.daily_average); }, 1], ['Factor', function (r) { return r.factor ? r.factor + '×' : 'new'; }, 1]], 'No unusual consumption') + '</div></div>' +
      (s.expired.length ? '<div class="sa-card sa-section"><h3>Expired balances</h3>' + tbl(s.expired, [['Organization', orgCell], ['Expired', function (r) { return esc(fmtDT(r.expires_at)); }], ['Remaining', function (r) { return fmtN(r.remaining); }, 1]], '') + '</div>' : '') +
      '<div class="sa-card sa-section"><h3>Balances</h3><div id="tkList"></div></div>';
    listView($('#tkList', root), {
      url: function (p) { return '/api/super-admin/tokens' + qs({ page: p.page, limit: p.limit, sort: p.sort, q: p.q }); },
      sort: '-used', filters: [{ key: 'q', label: 'Organization name…' }], key: 'token-balances', exportUrl: function () { return reportUrl('usage'); },
      columns: [
        { label: 'Organization', render: orgCell },
        { label: 'Allocated', sort: 'allocated', cls: 'num', render: function (b) { return fmtN(b.allocated); } },
        { label: 'Used', sort: 'used', cls: 'num', render: function (b) { return fmtN(b.used); } },
        { label: 'Remaining', sort: 'remaining', cls: 'num', render: function (b) { return fmtN(b.remaining); } },
        { label: 'Usage', render: function (b) { return usageBar(b.percentage); } },
        { label: 'Expires', sort: 'expires_at', render: function (b) { return b.expires_at ? esc(fmtDate(b.expires_at)) : '<span class="sa-muted">Never</span>'; } },
        { label: '', cls: 'num', render: function () { return '<div class="row-actions"><button type="button" class="btn btn-secondary btn-xs" data-a="adj">Adjust</button><button type="button" class="btn btn-secondary btn-xs" data-a="exp">Expiry</button><a class="btn btn-secondary btn-xs" data-a="led">Ledger</a></div>'; } }
      ],
      bindRow: function (tr, b, reload) {
        $('[data-a=adj]', tr).onclick = function () { adjustTokens(b.organization_id, reload).catch(function (e) { toast(e.message, 'error'); }); };
        $('[data-a=exp]', tr).onclick = function () { tokenExpiry(b.organization_id, b.expires_at, reload).catch(function (e) { toast(e.message, 'error'); }); };
        $('[data-a=led]', tr).href = '#/organizations/' + encodeURIComponent(b.organization_id) + '?tab=tokens';
      },
      empty: { title: 'No token balances', desc: 'Organizations get balances when a demo is approved or a subscription is confirmed.' }
    });
  }

  // ════════════════════════════════════════════════════════
  //  LEADAI OPERATIONS
  // ════════════════════════════════════════════════════════
  var PLATFORM_OPTS = [['', 'All platforms'], ['facebook', 'Facebook'], ['instagram', 'Instagram'], ['youtube', 'YouTube'], ['linkedin', 'LinkedIn']];

  function searchesList(el, fixed, o) {
    fixed = fixed || {}; o = o || {};
    return listView(el, {
      url: function (p) { return '/api/super-admin/searches' + qs(Object.assign({ page: p.page, limit: p.limit, sort: p.sort, q: p.q, status: p.status, platform: p.platform, from: p.from, to: p.to, apify_only: o.apifyOnly ? 'true' : '' }, fixed)); },
      sort: '-created_at', initial: o.initial || {}, key: o.apifyOnly ? 'apify-jobs' : 'searches',
      exportUrl: function (st) { return reportUrl(o.apifyOnly ? 'apify-jobs' : 'searches', { organization_id: fixed.organization_id, status: st.status, from: st.from, to: st.to }); },
      filters: [{ key: 'q', label: 'URL, run id or user email…' },
        { key: 'status', type: 'select', label: 'Status', options: [['', 'All statuses'], ['running', 'Running'], ['completed', 'Completed'], ['failed', 'Failed'], ['cancelled', 'Cancelled']] },
        { key: 'platform', type: 'select', label: 'Platform', options: PLATFORM_OPTS },
        { key: 'from', type: 'date', label: 'From' }, { key: 'to', type: 'date', label: 'To' }],
      columns: [
        { label: o.apifyOnly ? 'Job' : 'Search', render: function (s) { return '<span class="cell-main" title="' + esc(s.query) + '">' + esc(short(s.query, 48)) + '</span><span class="cell-sub sa-mono">' + esc(s.run_id) + '</span>'; } },
        { label: 'Organization', render: function (s) { return s.organization_id ? '<a class="sa-link" href="#/organizations/' + esc(s.organization_id) + '">' + esc(s.organization_name || short(s.organization_id)) + '</a>' : '<span class="sa-muted">—</span>'; } },
        { label: 'User', render: function (s) { return s.user_id ? '<a class="sa-link" href="#/users/' + esc(s.user_id) + '">' + esc(s.created_by || short(s.user_id)) + '</a>' : esc(s.created_by || '—'); } },
        { label: 'Platform', render: function (s) { return esc(titleCase(s.platform || '—')); } },
        o.apifyOnly ? { label: 'Apify run', render: function (s) { return '<span class="sa-mono">' + esc(short(s.apify_run_id, 14) || '—') + '</span><span class="cell-sub">' + esc(s.actor_id || '') + (s.usage_usd ? ' · $' + esc(Number(s.usage_usd).toFixed(4)) : '') + '</span>'; } } : null,
        { label: 'Status', sort: 'status', render: function (s) { return pill(s.status) + (s.error ? '<span class="cell-sub" style="color:var(--danger-text)" title="' + esc(s.error) + '">' + esc(short(s.error, 40)) + '</span>' : ''); } },
        { label: 'Started', sort: 'created_at', render: function (s) { return esc(fmtDT(s.created_at)); } },
        { label: '', cls: 'num', render: function (s) { return '<div class="row-actions"><button type="button" class="btn btn-secondary btn-xs" data-chain>Investigate</button><a class="btn btn-secondary btn-xs" href="/admin#/jobs/details:' + esc(encodeURIComponent(s.run_id)) + '" target="_blank" rel="noopener">Job ↗</a></div>'; } }
      ].filter(Boolean),
      rowClick: function (s) { chainDrawer(s.run_id); },
      bindRow: function (tr, s) { $('[data-chain]', tr).onclick = function () { chainDrawer(s.run_id); }; },
      empty: { title: o.apifyOnly ? 'No Apify jobs' : 'No searches', desc: 'Nothing matches these filters.' }
    });
  }

  function leadsList(el, fixed, o) {
    fixed = fixed || {}; o = o || {};
    return listView(el, {
      url: function (p) { return '/api/super-admin/leads' + qs(Object.assign({ page: p.page, limit: p.limit, sort: p.sort, q: p.q, quality: p.quality, platform: p.platform, organization_id: p.organization_id }, fixed)); },
      sort: '-created_at', initial: o.initial || {}, key: 'leads',
      exportUrl: function (st) { return reportUrl('leads', { organization_id: fixed.organization_id || st.organization_id }); },
      filters: [{ key: 'q', label: 'Commenter or text…' }, { key: 'quality', type: 'select', label: 'Quality', options: [['', 'Any quality'], ['hot', 'Hot'], ['warm', 'Warm'], ['cold', 'Cold']] }, { key: 'platform', type: 'select', label: 'Platform', options: PLATFORM_OPTS }].concat(fixed.organization_id ? [] : [{ key: 'organization_id', label: 'Organization ID' }]),
      columns: [
        { label: 'Lead', render: function (l) { return '<span class="cell-main">' + esc(l.commenter_name || 'Anonymous') + '</span><span class="cell-sub" title="' + esc(l.comment_text) + '">' + esc(short(l.comment_text, 70)) + '</span>'; } },
        { label: 'Contact (masked)', render: function (l) { return '<span class="sa-mono">' + esc(l.phone || '—') + '</span><span class="cell-sub sa-mono">' + esc(l.email || '') + '</span>'; } },
        { label: 'Quality', render: function (l) { return pill(l.lead_quality === 'hot' ? 'failed' : l.lead_quality === 'warm' ? 'pending' : 'info', titleCase(l.lead_quality || '—')); } },
        { label: 'Score', sort: 'lead_score', cls: 'num', render: function (l) { return esc(l.lead_score == null ? '—' : l.lead_score); } },
        { label: 'Organization', render: function (l) { return '<a class="sa-link" href="#/organizations/' + esc(l.organization_id) + '">' + esc(l.organization_name || short(l.organization_id)) + '</a>'; } },
        { label: 'Found', sort: 'created_at', render: function (l) { return esc(fmtDate(l.created_at)); } },
        { label: '', cls: 'num', render: function (l) { return '<div class="row-actions">' + (l.search_run_id ? '<button type="button" class="btn btn-secondary btn-xs" data-chain>Source run</button>' : '') + '<a class="btn btn-secondary btn-xs" href="/admin#/leads/details:' + esc(encodeURIComponent(l.id)) + '" target="_blank" rel="noopener">Open ↗</a></div>'; } }
      ],
      bindRow: function (tr, l) { var b = $('[data-chain]', tr); if (b) b.onclick = function () { chainDrawer(l.search_run_id); }; },
      empty: { title: 'No leads', desc: 'Leads appear once searches have analysed comments.' }
    });
  }

  function chainHtml(c) {
    var s = c.search, cnt = c.counts;
    var node = function (label, value, href, bad) {
      var inner = '<b>' + esc(label) + '</b>' + value;
      return href ? '<a class="node' + (bad ? ' bad' : '') + '" href="' + esc(href) + '"' + (/^\/admin/.test(href) ? ' target="_blank" rel="noopener"' : '') + ' style="text-decoration:none;color:inherit">' + inner + '</a>' : '<span class="node' + (bad ? ' bad' : '') + '">' + inner + '</span>';
    };
    var arrow = '<span class="arrow" aria-hidden="true">→</span>';
    var failed = /error|failed/.test(s.status || '');
    var jobs = c.apify_jobs || [];
    return '<nav class="sa-chain" aria-label="Investigation chain">' +
      node('Organization', esc(c.organization ? c.organization.name : '—') + (c.organization ? ' ' + pill(c.organization.status) : ''), c.organization ? '#/organizations/' + (c.organization.id || c.organization._id) : null) + arrow +
      node('Admin', esc((c.admins[0] || {}).email || '—') + (c.admins.length > 1 ? ' +' + (c.admins.length - 1) : ''), c.admins[0] ? '#/users/' + c.admins[0].user_id : null) + arrow +
      node('User', esc((c.user || {}).email || '—'), c.user && c.user.id ? '#/users/' + c.user.id : null) + arrow +
      node('Search', pill(s.status), null, failed) + arrow +
      node('Apify job', esc(jobs.length ? short(jobs[0].apify_run_id || 'run', 12) : 'none'), '/admin#/jobs/details:' + encodeURIComponent(s.run_id), jobs.some(function (j) { return j.status === 'failed'; })) + arrow +
      node('Pages', fmtN(cnt.pages), '/admin#/pages?run=' + encodeURIComponent(s.run_id)) + arrow +
      node('Posts', fmtN(cnt.posts), '/admin#/posts?run=' + encodeURIComponent(s.run_id)) + arrow +
      node('Comments', fmtN(cnt.comments) + ' <span class="sa-small sa-muted">(' + fmtN(cnt.analyzed) + ' analysed)</span>', '/admin#/ci') + arrow +
      node('Leads', fmtN(cnt.leads), '#/ops/leads?run=' + encodeURIComponent(s.run_id)) + '</nav>' +
      (c.error ? '<div class="alert alert-danger"><div class="alert-body"><div class="alert-title">Error</div><div class="sa-mono">' + esc(c.error) + '</div></div></div>' : '') +
      '<div class="sa-grid sa-2 sa-section"><div class="sa-card"><h3>Search run</h3><dl class="sa-kv"><dt>Run ID</dt><dd class="sa-mono">' + esc(s.run_id) + '</dd><dt>URL</dt><dd>' + esc(s.query) + '</dd><dt>Platform</dt><dd>' + esc(titleCase(s.platform || '—')) + '</dd>' +
      '<dt>Phase</dt><dd>' + esc(s.phase || '—') + '</dd><dt>Message</dt><dd>' + esc(s.message || '—') + '</dd><dt>Started</dt><dd>' + esc(fmtDT(s.created_at)) + '</dd><dt>Completed</dt><dd>' + esc(fmtDT(s.completed_at)) + '</dd></dl></div>' +
      '<div class="sa-card"><h3>Apify job(s)</h3>' + (jobs.length ? jobs.map(function (j) {
        return '<dl class="sa-kv" style="margin-bottom:8px"><dt>Run</dt><dd class="sa-mono">' + esc(j.apify_run_id || '—') + '</dd><dt>Actor</dt><dd>' + esc(j.actor_id || '—') + '</dd><dt>Status</dt><dd>' + pill(j.status) + '</dd>' +
          (j.usage_usd != null ? '<dt>Cost</dt><dd>$' + esc(Number(j.usage_usd).toFixed(4)) + '</dd>' : '') + (j.error ? '<dt>Error</dt><dd>' + esc(j.error) + '</dd>' : '') + '</dl>';
      }).join('') : emptyState('No Apify metadata recorded')) + '</div></div>' +
      '<div class="sa-card sa-section"><h3>Audit trail for this run</h3>' + ((c.audit || []).length ? '<ul class="sa-feed">' + c.audit.map(function (a) {
        return '<li><span class="sa-mono">' + esc(a.action) + '</span> ' + (a.status === 'failure' ? pill('failure') : '') + '<span class="sa-small sa-muted">' + esc(a.actor_email || '') + '</span><span class="when">' + esc(fmtDT(a.at)) + '</span></li>';
      }).join('') + '</ul>' : emptyState('No audit entries')) + '</div>';
  }
  function chainDrawer(runId) {
    openDrawer('Investigate search run', async function (body) {
      var c = (await api('/api/super-admin/searches/' + encodeURIComponent(runId) + '/chain')).chain;
      body.innerHTML = '<div class="sa-row" style="margin-bottom:10px"><a class="sa-link" href="#/ops/chain/' + esc(encodeURIComponent(runId)) + '">Open full investigation page →</a></div>' + chainHtml(c);
    });
  }
  async function viewChain(root, q, runId) {
    var c = (await api('/api/super-admin/searches/' + encodeURIComponent(runId) + '/chain')).chain;
    setTitle('Investigation', 'LeadAI Operations › Searches');
    root.innerHTML = '<div class="sa-row sa-small" style="margin-bottom:6px"><a class="sa-link" href="#/ops/searches">← Searches</a></div>' +
      header('Failure investigation', 'Organization → Admin → User → Search → Apify job → Pages → Posts → Comments → Leads') + chainHtml(c);
  }

  async function opsKpis(el) {
    var s = (await api('/api/super-admin/leadai/summary')).summary;
    el.innerHTML = '<div class="sa-grid sa-kpis">' +
      kpi('Searches', fmtN(s.searches.total), fmtN(s.searches.last_24h) + ' in 24h', { href: '#/ops/searches' }) +
      kpi('Running', fmtN(s.searches.running), '', { href: '#/ops/searches?status=running' }) +
      kpi('Failed', fmtN(s.searches.failed), '', { href: '#/ops/searches?status=failed', tone: s.searches.failed ? 'warn' : '' }) +
      kpi('Apify jobs', fmtN(s.apify_jobs.total), fmtN(s.apify_jobs.failed_24h) + ' failed in 24h', { href: '#/ops/jobs', tone: s.apify_jobs.failed_24h ? 'bad' : '' }) +
      kpi('Pages', fmtN(s.pages), '', { href: '/admin#/pages' }) + kpi('Posts', fmtN(s.posts), '', { href: '/admin#/posts' }) +
      kpi('Comments', fmtN(s.comments), fmtN(s.analyzed) + ' analysed', { href: '/admin#/ci' }) +
      kpi('Leads', fmtN(s.leads), fmtN(s.hot_leads) + ' hot', { href: '#/ops/leads' }) + '</div>';
    bindGo(el);
    return s;
  }

  async function viewOpsAgent(root) {
    var flags = (await api('/api/super-admin/feature-flags')).flags;
    var f = {}; flags.forEach(function (x) { f[x.key] = x.value; });
    root.innerHTML = header('URL Search Agent', 'Paste-a-URL lead discovery: agent status, platforms and live jobs across all organizations.',
      '<a class="btn btn-secondary btn-sm" href="#/flags">Feature flags</a>' + adminLink('limits', 'Scraping limits') + adminLink('platforms', 'Platforms & actors')) +
      '<div class="sa-grid sa-3"><div class="sa-card"><h3>Agent</h3><dl class="sa-kv"><dt>URL Search</dt><dd>' + pill(f['features.url_search.enabled'] ? 'active' : 'disabled', f['features.url_search.enabled'] ? 'Enabled' : 'Disabled') + '</dd>' +
      '<dt>AI analysis</dt><dd>' + pill(f['features.ai_analysis.enabled'] ? 'active' : 'disabled', f['features.ai_analysis.enabled'] ? 'Enabled' : 'Disabled') + '</dd><dt>Maintenance</dt><dd>' + pill(f['maintenance.enabled'] ? 'warn' : 'active', f['maintenance.enabled'] ? 'On' : 'Off') + '</dd></dl></div>' +
      '<div class="sa-card"><h3>Platforms</h3><dl class="sa-kv">' + ['facebook', 'instagram', 'linkedin', 'youtube'].map(function (p) { return '<dt>' + esc(titleCase(p)) + '</dt><dd>' + pill(f['platform.' + p + '.enabled'] ? 'active' : 'disabled', f['platform.' + p + '.enabled'] ? 'Enabled' : 'Disabled') + '</dd>'; }).join('') + '</dl></div>' +
      '<div class="sa-card"><h3>Operate</h3><ul class="sa-feed"><li><a class="sa-link" href="#/ops/searches?status=running">Running searches</a></li><li><a class="sa-link" href="#/ops/jobs?status=failed">Failed Apify jobs</a></li><li><a class="sa-link" href="/admin#/failed" target="_blank" rel="noopener">Retry failed jobs ↗</a></li><li><a class="sa-link" href="/admin#/apify" target="_blank" rel="noopener">Apify connection ↗</a></li></ul></div></div>' +
      '<div class="sa-section" id="agK"></div><div class="sa-card sa-section"><h3>Live jobs</h3><div id="agList"></div></div>';
    await opsKpis($('#agK', root));
    searchesList($('#agList', root), {}, { initial: { status: 'running' } });
  }
  async function viewOpsSearches(root, q) {
    root.innerHTML = header('Searches', 'Every URL search across all organizations. Click a row to investigate the full chain.', adminLink('jobs', 'Jobs browser')) + '<div id="osK"></div><div class="sa-section" id="osList"></div>';
    opsKpis($('#osK', root)).catch(function () {});
    searchesList($('#osList', root), {}, { initial: { status: q.status || '', q: q.q || '', from: q.from || '', to: q.to || '' } });
  }
  async function viewOpsJobs(root, q) {
    root.innerHTML = header('Apify jobs', 'Scraper runs with actor, run id, cost and errors.', adminLink('failed', 'Failed jobs & retry') + adminLink('apify', 'Apify registry')) + '<div id="ojList"></div>';
    searchesList($('#ojList', root), {}, { apifyOnly: true, initial: { status: q.status || '' } });
  }
  async function viewOpsLeads(root, q) {
    root.innerHTML = header('Leads', 'Global lead monitor. Contact details are masked here; open a lead in the platform console for item-level work.', adminLink('leads', 'Lead browser')) + '<div id="olList"></div>';
    leadsList($('#olList', root), q.run ? { run_id: q.run } : {}, { initial: { organization_id: q.org || '' } });
  }

  // ════════════════════════════════════════════════════════
  //  AI MANAGEMENT
  // ════════════════════════════════════════════════════════
  function pick(o, keys) { for (var i = 0; i < keys.length; i++) if (o && o[keys[i]] != null) return o[keys[i]]; return 0; }

  async function promptVersionModal(base, after) {
    base = base || {};
    var r = await openModal({ title: base.prompt_key ? 'New version — ' + base.prompt_key : 'New prompt', size: 'lg', submitLabel: 'Save version', body:
      '<div class="sa-form-grid"><div class="sa-field"><label for="ppKey">Prompt key</label><input class="form-input" id="ppKey" name="prompt_key" value="' + esc(base.prompt_key || '') + '"' + (base.prompt_key ? ' readonly' : '') + ' pattern="[a-z0-9_]{2,60}" required></div>' +
      '<div class="sa-field"><label for="ppName">Name</label><input class="form-input" id="ppName" name="name" value="' + esc(base.name || '') + '" required></div>' +
      '<div class="sa-field"><label for="ppModel">Model</label><input class="form-input" id="ppModel" name="model" value="' + esc(base.model || 'gemini-2.5-flash') + '"></div>' +
      '<div class="sa-field"><label for="ppPurpose">Purpose</label><input class="form-input" id="ppPurpose" name="purpose" value="' + esc(base.purpose || '') + '"></div></div>' +
      '<div class="sa-field"><label for="ppSys">System instructions</label><textarea class="form-textarea sa-mono" id="ppSys" name="system_instructions" rows="8" required>' + esc(base.system_instructions || '') + '</textarea></div>' +
      '<div class="sa-field"><label for="ppTpl">User template</label><textarea class="form-textarea sa-mono" id="ppTpl" name="user_template" rows="4" required>' + esc(base.user_template || '') + '</textarea><span class="hint">Variables: ' + esc((base.variables || ['author', 'post_caption', 'comment_text', 'business_category']).map(function (v) { return '{{' + v + '}}'; }).join(' ')) + '</span></div>' +
      '<div class="sa-field"><label for="ppReason">Change reason *</label><input class="form-input" id="ppReason" name="change_reason" required maxlength="300"></div>' +
      '<label class="sa-check"><input type="checkbox" name="make_active"> Activate immediately (otherwise saved as an inactive draft version)</label>',
      onSubmit: function (f, fd) {
        if (!String(fd.get('change_reason') || '').trim()) throw new Error('A change reason is required.');
        return api('/api/super-admin/ai/prompts', { method: 'POST', body: { prompt_key: String(fd.get('prompt_key')).trim(), name: fd.get('name'), purpose: fd.get('purpose') || '', model: fd.get('model'),
          system_instructions: fd.get('system_instructions'), user_template: fd.get('user_template'), variables: base.variables || null,
          change_reason: String(fd.get('change_reason')).trim(), make_active: !!fd.get('make_active') } });
      } });
    if (r) { toast('Prompt version saved'); if (after) after(); }
  }

  async function viewAI(root, q) {
    root.innerHTML = header('AI management', 'Usage and cost, versioned prompts with activation / rollback, and model registry.', adminLink('ai', 'Runtime settings & live test')) + '<div id="aiTabs"></div>';
    tabs($('#aiTabs', root), [['overview', 'Overview'], ['prompts', 'Prompts & versions'], ['models', 'Models']], q.tab || 'overview', async function (key, el) {
      if (key === 'overview') {
        var range = q.range || '30d';
        var m = (await api('/api/super-admin/ai/overview?range=' + range)).metrics;
        var series = m.requests_over_time || [];
        el.innerHTML = '<div class="sa-chips">' + ['24h', '7d', '30d', '90d'].map(function (r) { return '<a class="sa-chip' + (r === range ? ' active' : '') + '" href="#/ai?range=' + r + '">' + r + '</a>'; }).join('') + '</div>' +
          '<div class="sa-grid sa-kpis">' + kpi('Requests', fmtN(m.total_requests), fmtN(m.requests_today) + ' today') + kpi('Failed', fmtN(m.failed_requests), fmtN(m.fallback_requests) + ' rule fallbacks', { tone: m.failed_requests ? 'warn' : '' }) +
          kpi('Avg latency', fmtN(m.avg_latency_ms) + ' ms') + kpi('Tokens', fmtN(m.total_tokens), fmtN(m.avg_tokens) + ' avg / call') + kpi('Est. cost', '$' + Number(m.estimated_cost_usd || 0).toFixed(4)) +
          kpi('Leads by AI', fmtN(m.ai_leads_generated), fmtN(m.ai_comments_analyzed) + ' comments analysed') + '</div>' +
          '<div class="sa-grid sa-2 sa-section"><div class="sa-card"><h3>Requests over time</h3>' + (series.length ? lineChart(series.map(function (x) { return pick(x, ['requests', 'count', 'total']); }), series.map(function (x) { return x.date || x._id || ''; }), { title: 'AI requests' }) : emptyState('No AI calls in this range')) + '</div>' +
          '<div class="sa-card"><h3>Usage by model</h3>' + barList((m.usage_by_model || []).map(function (x) { return { label: x.model || x._id || 'unknown', value: pick(x, ['requests', 'count']) }; }), { emptyTitle: 'No model usage' }) + '</div>' +
          '<div class="sa-card"><h3>Usage by organization</h3>' + barList((m.usage_by_org || []).map(function (x) { return { label: x.organization_name || x.organization_id, value: x.requests || 0 }; }), { emptyTitle: 'No organization usage' }) + '</div>' +
          '<div class="sa-card"><h3>Cost by organization (USD)</h3>' + barList((m.usage_by_org || []).map(function (x) { return { label: x.organization_name || x.organization_id, value: x.cost || 0 }; }), { emptyTitle: 'No cost recorded', money: true, currency: 'USD' }) + '</div></div>';
        bindCharts(el);
      } else if (key === 'prompts') {
        var groups = (await api('/api/super-admin/ai/prompts')).groups || {};
        var keys = Object.keys(groups);
        el.innerHTML = '<div class="sa-row" style="margin-bottom:10px"><button type="button" class="btn btn-primary btn-sm" id="ppNew">+ New prompt</button></div>' + (keys.length ? keys.map(function (k) {
          var vs = groups[k]; var active = vs.filter(function (v) { return v.is_active; })[0];
          return '<div class="sa-card sa-section"><h3><span>' + esc(k) + ' <span class="sa-small sa-muted">' + esc((active || vs[0]).name || '') + '</span></span><button type="button" class="btn btn-secondary btn-xs" data-new="' + esc(k) + '">New version</button></h3>' +
            '<div class="sa-table-wrap"><table class="sa-table"><thead><tr><th>Version</th><th>State</th><th>Model</th><th>Change reason</th><th>By</th><th>Created</th><th></th></tr></thead><tbody>' + vs.map(function (v) {
              return '<tr><td class="cell-main">v' + esc(v.version) + '</td><td>' + (v.is_active ? pill('active', 'Active') : pill('muted', 'Inactive')) + '</td><td>' + esc(v.model || '') + '</td><td>' + esc(v.change_reason || '') + '</td><td class="sa-small">' + esc(v.created_by || '') + '</td><td>' + esc(fmtDT(v.created_at)) + '</td>' +
                '<td><div class="row-actions"><button type="button" class="btn btn-secondary btn-xs" data-view="' + esc((v.id || v._id)) + '">View</button>' + (v.is_active ? '' : '<button type="button" class="btn btn-secondary btn-xs" data-act="' + esc((v.id || v._id)) + '">Activate</button><button type="button" class="btn btn-secondary btn-xs" data-rb="' + esc((v.id || v._id)) + '">Roll back to this</button>') + '</div></td></tr>';
            }).join('') + '</tbody></table></div></div>';
        }).join('') : emptyState('No prompts', 'Default prompts are seeded on first use.'));
        var all = {}; keys.forEach(function (k) { groups[k].forEach(function (v) { all[(v.id || v._id)] = v; }); });
        var reload = function () { route(); };
        $('#ppNew', el).onclick = function () { promptVersionModal(null, reload); };
        $$('[data-new]', el).forEach(function (b) { b.onclick = function () { var vs = groups[b.getAttribute('data-new')]; promptVersionModal(vs.filter(function (v) { return v.is_active; })[0] || vs[0], reload); }; });
        $$('[data-view]', el).forEach(function (b) { b.onclick = function () { var v = all[b.getAttribute('data-view')]; openDrawer(v.prompt_key + ' v' + v.version, function (body) { body.innerHTML = '<dl class="sa-kv"><dt>Purpose</dt><dd>' + esc(v.purpose || '—') + '</dd><dt>Model</dt><dd>' + esc(v.model) + '</dd><dt>Previous version</dt><dd>' + esc(v.previous_version || '—') + '</dd></dl><h4 class="sa-section">System instructions</h4><pre class="sa-pre">' + esc(v.system_instructions) + '</pre><h4 class="sa-section">User template</h4><pre class="sa-pre">' + esc(v.user_template) + '</pre>'; }); }; });
        $$('[data-act]', el).forEach(function (b) { b.onclick = async function () {
          var v = all[b.getAttribute('data-act')];
          var r = await confirmDialog({ title: 'Activate v' + v.version, message: 'New AI analyses use this version immediately. The current active version is kept for rollback.', confirmLabel: 'Activate', danger: false });
          if (!r) return;
          try { await api('/api/super-admin/ai/prompts/' + (v.id || v._id) + '/activate', { method: 'POST' }); toast('Version activated'); reload(); } catch (e) { toast(e.message, 'error'); }
        }; });
        $$('[data-rb]', el).forEach(function (b) { b.onclick = async function () {
          var v = all[b.getAttribute('data-rb')];
          var r = await confirmDialog({ title: 'Roll back to v' + v.version, message: 'Creates a new active version identical to v' + v.version + ' (history is preserved).', confirmLabel: 'Roll back' });
          if (!r) return;
          try { await api('/api/super-admin/ai/prompts/' + (v.id || v._id) + '/rollback', { method: 'POST' }); toast('Rolled back'); reload(); } catch (e) { toast(e.message, 'error'); }
        }; });
      } else {
        var models = (await api('/api/super-admin/ai/models')).models || [];
        var editModel = async function (m) {
          var r = await openModal({ title: 'Edit model — ' + m.display_name, body:
            '<div class="sa-form-grid"><div class="sa-field"><label for="mdN">Display name</label><input class="form-input" id="mdN" name="display_name" value="' + esc(m.display_name) + '"></div><div class="sa-field"><label for="mdP">Purpose</label><input class="form-input" id="mdP" name="purpose" value="' + esc(m.purpose || '') + '"></div>' +
            '<div class="sa-field"><label for="mdT">Temperature (0–2)</label><input class="form-input" id="mdT" name="temperature" type="number" step="0.05" min="0" max="2" value="' + esc(m.temperature) + '"></div><div class="sa-field"><label for="mdX">Max tokens</label><input class="form-input" id="mdX" name="max_tokens" type="number" min="1" value="' + esc(m.max_tokens) + '"></div>' +
            '<div class="sa-field"><label for="mdI">$ / 1k input</label><input class="form-input" id="mdI" name="cost_input_per_1k" type="number" step="0.000001" min="0" value="' + esc(m.cost_input_per_1k) + '"></div><div class="sa-field"><label for="mdO">$ / 1k output</label><input class="form-input" id="mdO" name="cost_output_per_1k" type="number" step="0.000001" min="0" value="' + esc(m.cost_output_per_1k) + '"></div></div>' +
            '<label class="sa-check"><input type="checkbox" name="is_enabled"' + (m.is_enabled ? ' checked' : '') + '> Enabled</label><label class="sa-check"><input type="checkbox" name="is_default"' + (m.is_default ? ' checked' : '') + '> Default model</label>',
            onSubmit: function (f, fd) {
              return api('/api/super-admin/ai/models/' + (m.id || m._id), { method: 'PATCH', body: { display_name: fd.get('display_name'), purpose: fd.get('purpose'), temperature: parseFloat(fd.get('temperature')), max_tokens: parseInt(fd.get('max_tokens'), 10),
                cost_input_per_1k: parseFloat(fd.get('cost_input_per_1k')), cost_output_per_1k: parseFloat(fd.get('cost_output_per_1k')), is_enabled: !!fd.get('is_enabled'), is_default: !!fd.get('is_default') } });
            } });
          if (r) { toast('Model updated'); route(); }
        };
        listView(el, {
          data: function () { return Promise.resolve(models); }, key: 'ai-models', sort: 'display_name',
          filters: [{ key: 'q', label: 'Search model…' }, { key: 'enabled', type: 'select', label: 'State', options: [['', 'Enabled & disabled'], ['yes', 'Enabled'], ['no', 'Disabled']], match: function (m, v) { return !!m.is_enabled === (v === 'yes'); } }],
          columns: [
            { label: 'Model', sort: 'display_name', render: function (m) { return '<span class="cell-main">' + esc(m.display_name) + '</span><span class="cell-sub sa-mono">' + esc(m.model_name) + '</span>'; } },
            { label: 'Purpose', render: function (m) { return esc(m.purpose || ''); } },
            { label: 'Enabled', render: function (m) { return m.is_enabled ? pill('enabled', 'Enabled') : pill('disabled', 'Disabled'); } },
            { label: 'Default', render: function (m) { return m.is_default ? badge('Default', 'primary') : ''; } },
            { label: 'Temp.', sort: 'temperature', cls: 'num', render: function (m) { return esc(m.temperature); } },
            { label: 'Max tokens', sort: 'max_tokens', cls: 'num', render: function (m) { return fmtN(m.max_tokens); } },
            { label: '$ / 1k in', sort: 'cost_input_per_1k', cls: 'num', render: function (m) { return esc(m.cost_input_per_1k); } },
            { label: '$ / 1k out', sort: 'cost_output_per_1k', cls: 'num', render: function (m) { return esc(m.cost_output_per_1k); } },
            { label: '', cls: 'num', render: function () { return '<button type="button" class="btn btn-secondary btn-xs" data-m>Edit</button>'; } }
          ],
          rowClick: function (m) { editModel(m).catch(function (e) { toast(e.message, 'error'); }); },
          bindRow: function (tr, m) { $('[data-m]', tr).onclick = function () { editModel(m).catch(function (e) { toast(e.message, 'error'); }); }; },
          empty: { title: 'No models registered' }
        });
      }
    });
  }

  // ════════════════════════════════════════════════════════
  //  ANALYTICS
  // ════════════════════════════════════════════════════════
  async function viewAnalytics(root, q) {
    var range = q.range || (q.from ? '' : '30d');
    var gran = ['day', 'week', 'month'].indexOf(q.g) >= 0 ? q.g : 'day';
    var a = await api('/api/super-admin/analytics' + qs({ range: range || '30d', from: q.from, to: q.to, organization_id: q.org, granularity: gran }));
    var unit = a.granularity || gran;
    var labels = a.buckets || a.days;
    function chart(title, s, href, o) {
      o = o || {};
      var cur = o.currency || s.currency || 'USD';
      return '<div class="sa-card"><h3><span>' + esc(title) + '</span>' + (href ? '<a class="sa-link" href="' + esc(href) + '"' + (/^\/admin/.test(href) ? ' target="_blank" rel="noopener"' : '') + '>Drill down →</a>' : '') + '</h3>' +
        '<div class="total">' + esc(o.money ? fmtMoney(s.total, cur) : fmtN(s.total)) + (o.distinct ? ' <span class="sa-small sa-muted">distinct</span>' : '') + '</div>' +
        lineChart(s.values, labels, { money: o.money, currency: cur, title: title, total: s.total, unit: unit }) + '</div>';
    }
    // money never mixes currencies: one revenue chart per currency
    function revenueCharts(rev, href) {
      var byCur = (rev && rev.by_currency) || {}, curs = Object.keys(byCur);
      if (curs.length <= 1) return chart('Revenue (collected)' + (curs.length ? ' · ' + curs[0] : ''), rev, href, { money: true, currency: curs[0] || rev.currency });
      return curs.map(function (c) { return chart('Revenue (collected) · ' + c, byCur[c], href, { money: true, currency: c }); }).join('');
    }
    var fromTo = qs({ from: labels[0], to: labels[labels.length - 1] }).replace('?', '');
    var b = a.business, p = a.product, snap = a.snapshot;
    root.innerHTML = header('Analytics', 'Business and product metrics · ' + fmtDate(a.from) + ' – ' + fmtDate(a.to) + ' · per ' + (UNIT_NAME[unit] || UNIT_NAME.day)[0] + ' (UTC' + (unit === 'week' ? ', weeks start Monday' : '') + ')' + (q.org ? ' · organization ' + q.org : ' · all organizations'),
      '<button type="button" class="btn btn-secondary btn-sm" id="anTable">Table view</button>') +
      '<form class="sa-filters" id="anForm"><div class="sa-chips" style="margin:0">' + ['7d', '30d', '90d', '365d'].map(function (r) { return '<a class="sa-chip' + (r === range ? ' active' : '') + '" href="#/analytics' + qs({ range: r, org: q.org, g: gran === 'day' ? '' : gran }) + '"' + (r === range ? ' aria-current="true"' : '') + '>' + r + '</a>'; }).join('') + '</div>' +
      '<div class="sa-chips" style="margin:0" role="group" aria-label="Group by">' + [['day', 'Day'], ['week', 'Week'], ['month', 'Month']].map(function (g) { return '<a class="sa-chip' + (g[0] === unit ? ' active' : '') + '" href="#/analytics' + qs({ range: range, from: q.from, to: q.to, org: q.org, g: g[0] === 'day' ? '' : g[0] }) + '"' + (g[0] === unit ? ' aria-current="true"' : '') + '>' + g[1] + '</a>'; }).join('') + '</div>' +
      '<label class="sa-small sa-muted" for="anFrom">From</label><input class="form-input" type="date" id="anFrom" name="from" value="' + esc(q.from || '') + '"><label class="sa-small sa-muted" for="anTo">To</label><input class="form-input" type="date" id="anTo" name="to" value="' + esc(q.to || '') + '">' +
      '<label class="sr-only" for="anOrg">Organization ID</label><input class="form-input" id="anOrg" name="org" placeholder="Organization ID (optional)" value="' + esc(q.org || '') + '"><button class="btn btn-secondary btn-sm" type="submit">Apply</button></form>' +
      '<div class="sa-grid sa-kpis">' + kpi('Active customers', fmtN(snap.active_customers), '', { href: '#/organizations?status=active' }) + kpi('Demo accounts', fmtN(snap.demo_accounts), '', { href: '#/organizations?status=demo' }) + kpi('Total users', fmtN(snap.total_users), '', { href: '#/users' }) +
      kpi('Demo → paid', fmtN(b.demo_conversions.total), b.demo_requests.total ? Math.round(b.demo_conversions.total * 100 / b.demo_requests.total) + '% of requests in range' : '') + kpi('Churned', fmtN(b.churn.total), 'cancelled / expired in range', { tone: b.churn.total ? 'warn' : '' }) + '</div>' +
      '<h3 class="sa-section" style="font-size:15px">Business</h3><div class="sa-grid sa-3">' +
      chart('Registrations (organizations)', b.registrations, '#/organizations') + chart('Demo requests', b.demo_requests, '#/demo') + chart('Demo conversions', b.demo_conversions, '#/demo?status=converted') +
      chart('Subscriptions activated', b.subscriptions_activated, '#/subscriptions?status=active') + chart('Churn', b.churn, '#/subscriptions?status=cancelled') + revenueCharts(b.revenue, '#/payments?status=succeeded') +
      '<div class="sa-card"><h3>Plan distribution (active)</h3>' + barList(Object.keys(snap.plan_distribution).map(function (k) { return { label: k, value: snap.plan_distribution[k] }; }).sort(function (x, y) { return y.value - x.value; }), { emptyTitle: 'No active subscriptions' }) + '</div></div>' +
      '<h3 class="sa-section" style="font-size:15px">Product</h3><div class="sa-grid sa-3">' +
      chart('Searches', p.searches, '#/ops/searches?' + fromTo) + chart('Failed searches', p.failed_searches, '#/ops/searches?status=failed&' + fromTo) + chart('Active users', p.active_users, '#/users', { distinct: true }) +
      chart('Apify jobs', p.apify_jobs, '#/ops/jobs') + chart('Leads', p.leads, '#/ops/leads') + chart('AI calls', p.ai_calls, '#/ai') +
      chart('Tokens consumed', p.tokens_consumed, '#/tokens') + chart('Errors (high severity)', p.errors, '#/security') + chart('New users', p.new_users, '#/users') + '</div>' +
      '<div id="anTbl" hidden class="sa-card sa-section"></div>';
    bindCharts(root); bindGo(root);
    $('#anForm', root).onsubmit = function (e) {
      e.preventDefault(); var fd = new FormData(this);
      go('#/analytics' + qs({ from: fd.get('from'), to: fd.get('to'), org: String(fd.get('org') || '').trim(), range: fd.get('from') || fd.get('to') ? '' : range, g: unit === 'day' ? '' : unit }));
    };
    $('#anTable', root).onclick = function () {
      var t = $('#anTbl', root);
      if (t.hidden) {
        var revCur = (b.revenue && b.revenue.by_currency) || {};
        var revCols = Object.keys(revCur).length > 1 ? Object.keys(revCur).map(function (c) { return ['Revenue ' + c, revCur[c]]; }) : [['Revenue' + (b.revenue.currency ? ' ' + b.revenue.currency : ''), b.revenue]];
        var series = [['Registrations', b.registrations], ['Demo requests', b.demo_requests], ['Conversions', b.demo_conversions], ['Activated', b.subscriptions_activated], ['Churn', b.churn]].concat(revCols).concat([['Searches', p.searches], ['Failed', p.failed_searches], ['Active users', p.active_users], ['Leads', p.leads], ['AI calls', p.ai_calls], ['Tokens', p.tokens_consumed], ['Errors', p.errors]]);
        var un = UNIT_NAME[unit] || UNIT_NAME.day;
        t.innerHTML = '<h3>Values per ' + esc(un[0]) + '</h3><div class="sa-table-wrap"><table class="sa-table"><thead><tr><th>' + esc(un[2]) + '</th>' + series.map(function (s) { return '<th class="num">' + esc(s[0]) + '</th>'; }).join('') + '</tr></thead><tbody>' +
          labels.map(function (d, i) { return '<tr><td class="sa-mono">' + esc(bucketLabel(d, unit)) + '</td>' + series.map(function (s) { return '<td class="num">' + esc(fmtN((s[1].values || [])[i])) + '</td>'; }).join('') + '</tr>'; }).join('') + '</tbody></table></div>';
      }
      t.hidden = !t.hidden; this.textContent = t.hidden ? 'Table view' : 'Hide table';
    };
  }

  // ════════════════════════════════════════════════════════
  //  WEBSITE (CMS editor) — /api/admin/cms/*
  // ════════════════════════════════════════════════════════
  var CMS = '/api/admin/cms';
  function cid(x) { return x && (x.id || x._id); }
  function getPath(obj, path) { return path.split('.').reduce(function (o, k) { return o == null ? undefined : o[k]; }, obj); }
  function setPath(obj, path, val) {
    var ks = path.split('.'), o = obj;
    for (var i = 0; i < ks.length - 1; i++) { if (o[ks[i]] == null || typeof o[ks[i]] !== 'object') o[ks[i]] = {}; o = o[ks[i]]; }
    o[ks[ks.length - 1]] = val;
  }
  function bindModel(el, model, onDirty) {
    var handler = function (e) {
      var t = e.target, p = t.getAttribute && t.getAttribute('data-bind');
      if (!p) return;
      setPath(model, p, t.type === 'checkbox' ? t.checked : t.value);
      if (onDirty) onDirty();
    };
    el.addEventListener('input', handler); el.addEventListener('change', handler);
  }
  function fInput(label, path, value, o) {
    o = o || {};
    var id = 'w_' + path.replace(/[^a-z0-9]/gi, '_');
    if (o.type === 'textarea') return '<div class="sa-field' + (o.wide ? '" style="grid-column:1/-1' : '') + '"><label for="' + id + '">' + esc(label) + '</label><textarea class="form-textarea" id="' + id + '" data-bind="' + esc(path) + '" rows="' + (o.rows || 3) + '" style="min-height:60px">' + esc(value || '') + '</textarea>' + (o.hint ? '<span class="hint">' + esc(o.hint) + '</span>' : '') + '</div>';
    if (o.options) return '<div class="sa-field"><label for="' + id + '">' + esc(label) + '</label><select class="form-select" id="' + id + '" data-bind="' + esc(path) + '">' + o.options.map(function (x) { var v = Array.isArray(x) ? x[0] : x, l = Array.isArray(x) ? x[1] : (x || '—'); return '<option value="' + esc(v) + '"' + (String(value || '') === String(v) ? ' selected' : '') + '>' + esc(l) + '</option>'; }).join('') + '</select></div>';
    if (o.type === 'checkbox') return '<label class="sa-check"><input type="checkbox" data-bind="' + esc(path) + '"' + (value ? ' checked' : '') + '> ' + esc(label) + '</label>';
    return '<div class="sa-field"><label for="' + id + '">' + esc(label) + '</label><input class="form-input" id="' + id + '" type="' + (o.type || 'text') + '" data-bind="' + esc(path) + '" value="' + esc(value == null ? '' : value) + '"' + (o.readonly ? ' readonly' : '') + '></div>';
  }
  function moveIdx(arr, i, d) { var j = i + d; if (j < 0 || j >= arr.length) return; var t = arr[i]; arr[i] = arr[j]; arr[j] = t; }

  function sectionHtml(s, i, n, schema) {
    var p = 'sections.' + i;
    var items = s.items || [];
    return '<div class="sa-card sa-section" data-sec="' + i + '"><h3><span>#' + (i + 1) + ' · ' + esc(schema.section_types[s.type] || s.type) + ' <span class="sa-mono sa-small sa-muted">' + esc(s.key || '') + '</span></span><span class="sa-row">' +
      '<label class="sa-check sa-small"><input type="checkbox" data-bind="' + p + '.enabled"' + (s.enabled !== false ? ' checked' : '') + '> Visible</label>' +
      '<button type="button" class="btn btn-secondary btn-xs" data-sm="' + i + ':-1"' + (i === 0 ? ' disabled' : '') + ' aria-label="Move section up">↑</button><button type="button" class="btn btn-secondary btn-xs" data-sm="' + i + ':1"' + (i === n - 1 ? ' disabled' : '') + ' aria-label="Move section down">↓</button>' +
      '<button type="button" class="btn btn-danger btn-xs" data-sd="' + i + '">Remove</button></span></h3>' +
      '<div class="sa-form-grid">' + fInput('Key (unique)', p + '.key', s.key) + fInput('Type', p + '.type', s.type, { options: Object.keys(schema.section_types).map(function (k) { return [k, schema.section_types[k]]; }) }) +
      fInput('Eyebrow', p + '.eyebrow', s.eyebrow) + fInput('Title', p + '.title', s.title) + fInput('Highlight (accent words)', p + '.highlight', s.highlight) + fInput('Card title', p + '.card_title', s.card_title) +
      fInput('Subtitle', p + '.subtitle', s.subtitle, { type: 'textarea', wide: true, rows: 2 }) + fInput('Body', p + '.body', s.body, { type: 'textarea', wide: true, rows: 5, hint: schema.body_format }) +
      fInput('Note', p + '.note', s.note) + '<div></div>' +
      fInput('Primary CTA label', p + '.cta_primary.label', (s.cta_primary || {}).label) + fInput('Primary CTA URL', p + '.cta_primary.url', (s.cta_primary || {}).url) +
      fInput('Secondary CTA label', p + '.cta_secondary.label', (s.cta_secondary || {}).label) + fInput('Secondary CTA URL', p + '.cta_secondary.url', (s.cta_secondary || {}).url) + '</div>' +
      '<h4 style="margin:14px 0 6px;font-size:13px">Items (' + items.length + ')</h4>' + items.map(function (it, j) {
        var ip = p + '.items.' + j;
        return '<div style="border:1px solid var(--border);border-radius:10px;padding:10px;margin-bottom:8px"><div class="sa-row" style="justify-content:space-between;margin-bottom:6px"><b class="sa-small">Item ' + (j + 1) + '</b><span class="sa-row">' +
          '<button type="button" class="btn btn-secondary btn-xs" data-im="' + i + ':' + j + ':-1"' + (j === 0 ? ' disabled' : '') + ' aria-label="Move item up">↑</button><button type="button" class="btn btn-secondary btn-xs" data-im="' + i + ':' + j + ':1"' + (j === items.length - 1 ? ' disabled' : '') + ' aria-label="Move item down">↓</button><button type="button" class="btn btn-danger btn-xs" data-id="' + i + ':' + j + '">Remove</button></span></div>' +
          '<div class="sa-form-grid">' + fInput('Icon', ip + '.icon', it.icon, { options: [''].concat(schema.icons) }) + fInput('Tone', ip + '.tone', it.tone, { options: [''].concat(schema.item_tones) }) +
          fInput('Title', ip + '.title', it.title) + fInput('Label', ip + '.label', it.label) + fInput('Value', ip + '.value', it.value) + fInput('URL', ip + '.url', it.url) +
          fInput('Description', ip + '.description', it.description, { type: 'textarea', wide: true, rows: 2 }) + '</div></div>';
      }).join('') + '<button type="button" class="btn btn-secondary btn-xs" data-ia="' + i + '">+ Add item</button></div>';
  }

  async function pageEditor(el, pageId, schema, back) {
    var page = await api(CMS + '/pages/' + encodeURIComponent(pageId));
    var model = { title: page.title || '', seo: Object.assign({ title: '', description: '', og_image: '', robots: 'index,follow' }, page.seo || {}), sections: JSON.parse(JSON.stringify(page.sections || [])) };
    var dirty = false;
    function markDirty() { dirty = true; var d = $('#peDirty', el); if (d) d.textContent = 'Unsaved changes'; }
    function render() {
      el.innerHTML = '<div class="sa-row" style="margin-bottom:8px"><button type="button" class="sa-link" id="peBack">← All pages</button></div>' +
        '<div class="sa-head"><div><h2>' + esc(page.title || page.slug) + '</h2><p><span class="sa-mono">/' + esc(page.slug) + '</span> · ' + pill(page.status) + (page.published_at ? ' · published ' + esc(ago(page.published_at)) : '') + '</p></div>' +
        '<div class="sa-actions"><span class="sa-small" id="peDirty" style="color:var(--warning-text)">' + (dirty ? 'Unsaved changes' : '') + '</span><button type="button" class="btn btn-secondary btn-sm" id="peVer">Versions</button>' +
        (page.status === 'published' ? '<button type="button" class="btn btn-secondary btn-sm" id="peUnpub">Unpublish</button>' : '') +
        '<button type="button" class="btn btn-secondary btn-sm" id="peSave">Save draft</button><button type="button" class="btn btn-primary btn-sm" id="pePub">Save &amp; publish</button></div></div>' +
        '<div class="sa-err" id="peErr" role="alert"></div>' +
        '<div class="sa-card"><h3>Page &amp; SEO</h3><div class="sa-form-grid">' + fInput('Page title', 'title', model.title) + fInput('SEO title', 'seo.title', model.seo.title) +
        fInput('SEO description', 'seo.description', model.seo.description, { type: 'textarea', wide: true, rows: 2 }) + fInput('OG image URL', 'seo.og_image', model.seo.og_image) +
        fInput('Robots', 'seo.robots', model.seo.robots, { options: schema.robots }) + '</div></div>' +
        model.sections.map(function (s, i) { return sectionHtml(s, i, model.sections.length, schema); }).join('') +
        '<div class="sa-card sa-section"><h3>Add section</h3><div class="sa-row"><label class="sr-only" for="peNewType">Section type</label><select class="form-select" id="peNewType" style="width:auto">' +
        Object.keys(schema.section_types).map(function (k) { return '<option value="' + esc(k) + '">' + esc(schema.section_types[k]) + '</option>'; }).join('') + '</select><button type="button" class="btn btn-secondary btn-sm" id="peAdd">+ Add section</button></div></div>';
      wire();
    }
    function wire() {
      $('#peBack', el).onclick = async function () {
        if (dirty) { var r = await confirmDialog({ title: 'Discard changes?', message: 'You have unsaved changes on this page.', confirmLabel: 'Discard' }); if (!r) return; }
        back();
      };
      $('#peAdd', el).onclick = function () {
        var type = $('#peNewType', el).value, base = type, k = 1;
        var keys = model.sections.map(function (s) { return s.key; });
        while (keys.indexOf(k === 1 ? base : base + '-' + k) >= 0) k++;
        model.sections.push({ key: k === 1 ? base : base + '-' + k, type: type, enabled: true, items: [] });
        markDirty(); render();
      };
      $$('[data-sm]', el).forEach(function (b) { b.onclick = function () { var a = b.getAttribute('data-sm').split(':'); moveIdx(model.sections, +a[0], +a[1]); markDirty(); render(); }; });
      $$('[data-sd]', el).forEach(function (b) { b.onclick = async function () { var i = +b.getAttribute('data-sd'); var r = await confirmDialog({ title: 'Remove section', message: 'Remove section "' + (model.sections[i].key || '') + '" from the draft?', confirmLabel: 'Remove' }); if (!r) return; model.sections.splice(i, 1); markDirty(); render(); }; });
      $$('[data-ia]', el).forEach(function (b) { b.onclick = function () { var s = model.sections[+b.getAttribute('data-ia')]; (s.items = s.items || []).push({}); markDirty(); render(); }; });
      $$('[data-id]', el).forEach(function (b) { b.onclick = function () { var a = b.getAttribute('data-id').split(':'); model.sections[+a[0]].items.splice(+a[1], 1); markDirty(); render(); }; });
      $$('[data-im]', el).forEach(function (b) { b.onclick = function () { var a = b.getAttribute('data-im').split(':'); moveIdx(model.sections[+a[0]].items, +a[1], +a[2]); markDirty(); render(); }; });
      $('#peSave', el).onclick = function () { save(false, this); };
      $('#pePub', el).onclick = function () { save(true, this); };
      if ($('#peUnpub', el)) $('#peUnpub', el).onclick = async function () {
        var r = await confirmDialog({ title: 'Unpublish page', message: '/' + page.slug + ' is taken off the public website (content is kept as a draft).', confirmLabel: 'Unpublish' });
        if (!r) return;
        try { await api(CMS + '/pages/' + encodeURIComponent(pageId) + '/unpublish', { method: 'POST' }); toast('Page unpublished'); page = await api(CMS + '/pages/' + encodeURIComponent(pageId)); render(); } catch (e) { toast(e.message, 'error'); }
      };
      $('#peVer', el).onclick = function () {
        openDrawer('Versions — /' + page.slug, async function (body, close) {
          var vs = (await api(CMS + '/pages/' + encodeURIComponent(pageId) + '/versions')).items || [];
          body.innerHTML = '<p class="sa-small sa-muted">Each publish snapshots the page. Restoring loads a version as the current draft (publish it to go live).</p>' + (vs.length ? '<ul class="sa-feed">' + vs.slice().reverse().map(function (v) {
            return '<li><div><b>Version ' + esc(v.version != null ? v.version : v.index + 1) + '</b> <span class="sa-small">' + esc(v.title || '') + '</span><div class="sa-small sa-muted">' + esc(fmtDT(v.snapshot_at)) + ' · ' + esc(v.snapshot_by || '') + '</div></div><span class="when"><button type="button" class="btn btn-secondary btn-xs" data-rs="' + esc(v.index) + '">Restore to draft</button></span></li>';
          }).join('') + '</ul>' : emptyState('No versions yet', 'A version is created each time the page is published.'));
          $$('[data-rs]', body).forEach(function (x) { x.onclick = async function () {
            if (dirty) { var ok = await confirmDialog({ title: 'Discard unsaved changes?', message: 'Restoring replaces the draft.', confirmLabel: 'Restore' }); if (!ok) return; }
            try { await api(CMS + '/pages/' + encodeURIComponent(pageId) + '/restore/' + x.getAttribute('data-rs'), { method: 'POST' }); toast('Version restored to draft'); close(); pageEditor(el, pageId, schema, back); } catch (e) { toast(e.message, 'error'); }
          }; });
        });
      };
    }
    async function save(publish, btn) {
      $('#peErr', el).textContent = '';
      try {
        await busy(btn, async function () {
          await api(CMS + '/pages/' + encodeURIComponent(pageId), { method: 'PUT', body: { title: model.title, seo: model.seo, sections: model.sections } });
          if (publish) await api(CMS + '/pages/' + encodeURIComponent(pageId) + '/publish', { method: 'POST' });
        });
        dirty = false;
        toast(publish ? 'Saved and published' : 'Draft saved');
        page = await api(CMS + '/pages/' + encodeURIComponent(pageId));
        model.sections = JSON.parse(JSON.stringify(page.sections || model.sections));
        render();
      } catch (e) { $('#peErr', el).textContent = e.message; el.scrollIntoView({ block: 'start' }); }
    }
    bindModel(el, model, markDirty);
    render();
  }

  function tokenHex(name) {
    var v = '';
    try { v = getComputedStyle(document.documentElement).getPropertyValue(name).trim(); } catch (e) { v = ''; }
    return /^#[0-9a-f]{6}$/i.test(v) ? v : '#000000';
  }
  function simpleCrud(el, cfg) {
    // cfg: {title, list(q) -> items, fields:[[key,label,type,opts]], create(body), update(id, body), remove(id), reorder(ids)?, cols(item) -> [html..], headers:[..], search}
    var q = '';
    async function load() {
      var body = $('[data-body]', el);
      body.innerHTML = skeleton('rows');
      var items;
      try { items = await cfg.list(q); } catch (e) { body.innerHTML = errorState(e, load); return; }
      body.innerHTML = items.length ? '<div class="sa-table-wrap sa-sticky"><table class="sa-table"><thead><tr>' + (cfg.reorder ? '<th style="width:70px">Order</th>' : '') + cfg.headers.map(function (h) { return '<th>' + esc(h) + '</th>'; }).join('') + '<th></th></tr></thead><tbody>' + items.map(function (it, i) {
        return '<tr>' + (cfg.reorder ? '<td><div class="row-actions" style="justify-content:flex-start"><button type="button" class="btn btn-secondary btn-xs" data-mv="' + i + ':-1"' + (i === 0 || q ? ' disabled' : '') + ' aria-label="Move up">↑</button><button type="button" class="btn btn-secondary btn-xs" data-mv="' + i + ':1"' + (i === items.length - 1 || q ? ' disabled' : '') + ' aria-label="Move down">↓</button></div></td>' : '') +
          cfg.cols(it).map(function (c) { return '<td>' + c + '</td>'; }).join('') + '<td class="num"><div class="row-actions"><button type="button" class="btn btn-secondary btn-xs" data-e="' + i + '">Edit</button><button type="button" class="btn btn-danger btn-xs" data-d="' + i + '">Delete</button></div></td></tr>';
      }).join('') + '</tbody></table></div>' : emptyState('Nothing here yet', q ? 'No match for this search.' : 'Add the first item.');
      $$('[data-e]', body).forEach(function (b) { b.onclick = function () { editor(items[+b.getAttribute('data-e')]); }; });
      $$('[data-d]', body).forEach(function (b) { b.onclick = async function () {
        var it = items[+b.getAttribute('data-d')];
        var r = await confirmDialog({ title: 'Delete', message: 'Remove this item from the website? This cannot be undone.', confirmLabel: 'Delete' });
        if (!r) return;
        try { await cfg.remove(cid(it)); toast('Deleted'); load(); } catch (e) { toast(e.message, 'error'); }
      }; });
      $$('[data-mv]', body).forEach(function (b) { b.onclick = async function () {
        var a = b.getAttribute('data-mv').split(':'); var arr = items.slice(); moveIdx(arr, +a[0], +a[1]);
        try { await cfg.reorder(arr.map(cid)); load(); } catch (e) { toast(e.message, 'error'); }
      }; });
    }
    async function editor(item) {
      item = item || { enabled: true };
      var r = await openModal({ title: (cid(item) ? 'Edit ' : 'New ') + cfg.title, body: cfg.fields.map(function (f) {
        var id = 'sc_' + f[0], v = item[f[0]];
        if (f[2] === 'textarea') return '<div class="sa-field"><label for="' + id + '">' + esc(f[1]) + '</label><textarea class="form-textarea" id="' + id + '" name="' + f[0] + '" rows="4">' + esc(v || '') + '</textarea></div>';
        if (f[2] === 'checkbox') return '<label class="sa-check"><input type="checkbox" name="' + f[0] + '"' + (v !== false ? ' checked' : '') + '> ' + esc(f[1]) + '</label>';
        return '<div class="sa-field"><label for="' + id + '">' + esc(f[1]) + '</label><input class="form-input" id="' + id + '" name="' + f[0] + '" type="' + (f[2] || 'text') + '"' + (f[3] ? ' ' + f[3] : '') + ' value="' + esc(v == null ? '' : v) + '"></div>';
      }).join(''), onSubmit: function (form, fd) {
        var body = {};
        cfg.fields.forEach(function (f) {
          if (f[2] === 'checkbox') body[f[0]] = !!fd.get(f[0]);
          else if (f[2] === 'number') { var n = fd.get(f[0]); if (n !== '' && n != null) body[f[0]] = parseInt(n, 10); }
          else body[f[0]] = String(fd.get(f[0]) || '').trim();
        });
        return cid(item) ? cfg.update(cid(item), body) : cfg.create(body);
      } });
      if (r) { toast('Saved'); load(); }
    }
    el.innerHTML = '<div class="sa-filters">' + (cfg.search ? '<label class="sr-only" for="scQ">Search</label><input class="form-input sa-grow" type="search" id="scQ" placeholder="Search…">' : '') + '<span class="sa-spacer"></span><button type="button" class="btn btn-primary btn-sm" id="scNew">+ Add</button></div><div data-body></div>';
    var t;
    if (cfg.search) $('#scQ', el).addEventListener('input', function () { var v = this.value.trim(); clearTimeout(t); t = setTimeout(function () { q = v; load(); }, 300); });
    $('#scNew', el).onclick = function () { editor(null); };
    load();
  }

  async function viewWebsite(root, q) {
    var schema = await api(CMS + '/schema');
    root.innerHTML = header('Website', 'Public website content: pages (draft → publish with versions), FAQ, testimonials, navigation, settings, media and the contact inbox. All text is sanitized on save.',
      '<a class="btn btn-secondary btn-sm" href="/website" target="_blank" rel="noopener">Open website ↗</a><a class="btn btn-secondary btn-sm" href="#/pricing">Pricing preview</a>') + '<div id="wbTabs"></div>';
    tabs($('#wbTabs', root), [['pages', 'Pages'], ['faq', 'FAQ'], ['testimonials', 'Testimonials'], ['navigation', 'Navigation'], ['settings', 'Settings'], ['media', 'Media'], ['contact', 'Contact inbox']], q.tab || (q.page ? 'pages' : 'pages'), async function (key, el) {
      if (key === 'pages') {
        var showList = function () {
          el.innerHTML = '<div id="wpList"></div>';
          var openPage = function (p) { pageEditor(el, cid(p), schema, showList).catch(function (err) { el.innerHTML = errorState(err, showList); }); };
          var lvp = listView($('#wpList', el), {
            data: async function () { return (await api(CMS + '/pages')).pages || []; }, key: 'cms-pages', sort: 'slug',
            filters: [{ key: 'q', label: 'Search title or slug…' }, { key: 'status', type: 'select', label: 'Status', options: [['', 'All statuses'], ['published', 'Published'], ['draft', 'Draft']] }],
            toolbar: '<button type="button" class="btn btn-primary btn-sm" id="wpNew">+ New page</button>',
            columns: [
              { label: 'Page', sort: 'slug', render: function (p) { return '<span class="cell-main">' + esc(p.title || p.slug) + '</span><span class="cell-sub sa-mono">/' + esc(p.slug) + '</span>'; } },
              { label: 'Status', sort: 'status', render: function (p) { return pill(p.status) + (p.has_draft_changes ? ' ' + pill('draft', 'Unpublished edits') : ''); } },
              { label: 'Updated', sort: 'updated_at', render: function (p) { return esc(fmtDT(p.updated_at)); } },
              { label: 'Published', sort: 'published_at', render: function (p) { return esc(fmtDT(p.published_at)); } },
              { label: '', cls: 'num', render: function () { return '<div class="row-actions"><button type="button" class="btn btn-secondary btn-xs" data-open>Edit</button><button type="button" class="btn btn-danger btn-xs" data-del>Delete</button></div>'; } }
            ],
            rowClick: openPage,
            bindRow: function (tr, p, reloadList) {
              $('[data-open]', tr).onclick = function () { openPage(p); };
              $('[data-del]', tr).onclick = async function () {
                var r = await confirmDialog({ title: 'Delete page', message: 'Permanently deletes /' + p.slug + ' including its versions.', confirmLabel: 'Delete', typeToConfirm: p.slug });
                if (!r) return;
                try { await api(CMS + '/pages/' + encodeURIComponent(cid(p)), { method: 'DELETE' }); toast('Page deleted'); reloadList(); } catch (err) { toast(err.message, 'error'); }
              };
            },
            empty: { title: 'No pages', desc: 'Create a page to start.' }
          });
          void lvp;
          $('#wpNew', el).onclick = async function () {
            var r = await openModal({ title: 'New page', submitLabel: 'Create draft', body: '<div class="sa-field"><label for="npT">Title</label><input class="form-input" id="npT" name="title" required maxlength="200"></div><div class="sa-field"><label for="npS">Slug</label><input class="form-input" id="npS" name="slug" required maxlength="60" pattern="[a-z0-9-]+"><span class="hint">Lowercase letters, digits and dashes.</span></div>',
              onSubmit: function (f, fd) { if (!/^[a-z0-9-]{1,60}$/.test(String(fd.get('slug') || ''))) throw new Error('Slug: lowercase letters, digits and dashes.'); return api(CMS + '/pages', { method: 'POST', body: { title: String(fd.get('title') || '').trim(), slug: fd.get('slug') } }); } });
            if (r) { toast('Draft created'); pageEditor(el, r.id, schema, showList).catch(function (err) { el.innerHTML = errorState(err, showList); }); }
          };
        };
        if (q.page) await pageEditor(el, q.page, schema, showList); else showList();
      } else if (key === 'faq') {
        simpleCrud(el, { title: 'FAQ item', search: true, reorder: function (ids) { return api(CMS + '/faq/reorder', { method: 'POST', body: { ids: ids } }); },
          list: async function (qq) { return (await api(CMS + '/faq' + qs({ q: qq }))).faq || []; },
          fields: [['question', 'Question'], ['answer', 'Answer', 'textarea'], ['category', 'Category'], ['enabled', 'Visible on the website', 'checkbox']],
          headers: ['Question', 'Category', 'Visible'],
          cols: function (f) { return ['<span class="cell-main">' + esc(short(f.question, 90)) + '</span><span class="cell-sub">' + esc(short(f.answer, 110)) + '</span>', esc(f.category || ''), f.enabled !== false ? pill('active', 'Visible') : pill('muted', 'Hidden')]; },
          create: function (b) { return api(CMS + '/faq', { method: 'POST', body: b }); }, update: function (id, b) { return api(CMS + '/faq/' + encodeURIComponent(id), { method: 'PUT', body: b }); },
          remove: function (id) { return api(CMS + '/faq/' + encodeURIComponent(id), { method: 'DELETE' }); } });
      } else if (key === 'testimonials') {
        simpleCrud(el, { title: 'testimonial',
          list: async function () { return (await api(CMS + '/testimonials')).testimonials || []; },
          fields: [['quote', 'Quote', 'textarea'], ['author_name', 'Author name'], ['author_title', 'Author title'], ['company', 'Company'], ['avatar_url', 'Avatar URL', 'url'], ['rating', 'Rating (1–5)', 'number', 'min="1" max="5"'], ['order', 'Order', 'number'], ['enabled', 'Visible on the website', 'checkbox']],
          headers: ['Quote', 'Author', 'Rating', 'Visible'],
          cols: function (t) { return ['<span title="' + esc(t.quote || t.text) + '">' + esc(short(t.quote || t.text, 90)) + '</span>', esc(t.author_name || '') + '<span class="cell-sub">' + esc([t.author_title, t.company].filter(Boolean).join(' · ')) + '</span>', esc(t.rating ? '★'.repeat(t.rating) : '—'), t.enabled !== false ? pill('active', 'Visible') : pill('muted', 'Hidden')]; },
          create: function (b) { return api(CMS + '/testimonials', { method: 'POST', body: b }); }, update: function (id, b) { return api(CMS + '/testimonials/' + encodeURIComponent(id), { method: 'PUT', body: b }); },
          remove: function (id) { return api(CMS + '/testimonials/' + encodeURIComponent(id), { method: 'DELETE' }); } });
      } else if (key === 'navigation') {
        var loc = schema.navigation_locations[0];
        var renderNavEditor = async function () {
          var items = ((await api(CMS + '/navigation/' + loc)).items || []).map(function (x) { return { label: x.label || '', url: x.url || '', group: x.group || '', target: x.target || '_self', enabled: x.enabled !== false }; });
          var model = { items: items };
          var draw = function () {
            el.innerHTML = '<div class="sa-chips">' + schema.navigation_locations.map(function (l) { return '<button type="button" class="sa-chip' + (l === loc ? ' active' : '') + '" data-loc="' + esc(l) + '">' + esc(titleCase(l)) + '</button>'; }).join('') + '</div>' +
              '<div class="sa-card"><h3>' + esc(titleCase(loc)) + ' menu <button type="button" class="btn btn-secondary btn-xs" id="nvAdd">+ Add link</button></h3>' + (model.items.length ? model.items.map(function (it, i) {
                var p = 'items.' + i;
                return '<div class="sa-form-grid" style="grid-template-columns:repeat(auto-fit,minmax(130px,1fr));align-items:end;border-bottom:1px solid var(--border-subtle);padding:8px 0">' + fInput('Label', p + '.label', it.label) + fInput('URL', p + '.url', it.url) + fInput('Group', p + '.group', it.group) +
                  fInput('Opens in', p + '.target', it.target, { options: [['_self', 'Same tab'], ['_blank', 'New tab']] }) + '<div class="sa-row">' + fInput('Visible', p + '.enabled', it.enabled, { type: 'checkbox' }) +
                  '<button type="button" class="btn btn-secondary btn-xs" data-nm="' + i + ':-1"' + (i === 0 ? ' disabled' : '') + ' aria-label="Move up">↑</button><button type="button" class="btn btn-secondary btn-xs" data-nm="' + i + ':1"' + (i === model.items.length - 1 ? ' disabled' : '') + ' aria-label="Move down">↓</button><button type="button" class="btn btn-danger btn-xs" data-nd="' + i + '">✕</button></div></div>';
              }).join('') : emptyState('No links in this menu')) + '<div class="sa-err" id="nvErr" role="alert"></div><div class="sa-row sa-section"><button type="button" class="btn btn-primary btn-sm" id="nvSave">Save menu</button></div></div>';
            $$('[data-loc]', el).forEach(function (b) { b.onclick = function () { loc = b.getAttribute('data-loc'); renderNavEditor().catch(function (e) { el.innerHTML = errorState(e); }); }; });
            $('#nvAdd', el).onclick = function () { model.items.push({ label: '', url: '', group: '', target: '_self', enabled: true }); draw(); };
            $$('[data-nm]', el).forEach(function (b) { b.onclick = function () { var a = b.getAttribute('data-nm').split(':'); moveIdx(model.items, +a[0], +a[1]); draw(); }; });
            $$('[data-nd]', el).forEach(function (b) { b.onclick = function () { model.items.splice(+b.getAttribute('data-nd'), 1); draw(); }; });
            $('#nvSave', el).onclick = async function () {
              $('#nvErr', el).textContent = '';
              try { await busy(this, function () { return api(CMS + '/navigation/' + loc, { method: 'PUT', body: { items: model.items } }); }); toast('Menu saved'); } catch (e) { $('#nvErr', el).textContent = e.message; }
            };
          };
          bindModel(el, model);
          draw();
        };
        await renderNavEditor();
      } else if (key === 'settings') {
        var d = await api(CMS + '/settings');
        var vals = d.settings || {}, fields = d.fields || schema.settings;
        el.innerHTML = '<form class="sa-card" id="wsForm" novalidate><h3>Website settings</h3><div class="sa-form-grid">' + fields.map(function (f) {
          var id = 'ws_' + f.key, v = vals[f.key];
          if (f.kind === 'bool') return '<label class="sa-check" style="grid-column:1/-1"><input type="checkbox" name="' + esc(f.key) + '"' + (v === true || v === 'true' ? ' checked' : '') + '> ' + esc(f.label) + '</label>';
          if (f.kind === 'color') return '<div class="sa-field"><label for="' + id + '">' + esc(f.label) + '</label><div class="sa-row" style="flex-wrap:nowrap"><input type="color" aria-label="' + esc(f.label) + ' picker" data-mirror="' + id + '" value="' + esc(/^#[0-9a-f]{6}$/i.test(v || '') ? v : tokenHex('--primary')) + '" style="width:42px;height:38px;border:0;background:none"><input class="form-input" id="' + id + '" name="' + esc(f.key) + '" value="' + esc(v || '') + '" placeholder="#RRGGBB" pattern="#[0-9a-fA-F]{6}"></div></div>';
          return '<div class="sa-field"><label for="' + id + '">' + esc(f.label) + '</label><input class="form-input" id="' + id + '" name="' + esc(f.key) + '" type="' + (f.kind === 'email' ? 'email' : f.kind === 'url' ? 'url' : 'text') + '" value="' + esc(v == null ? '' : v) + '"></div>';
        }).join('') + '</div><div class="sa-err" id="wsErr" role="alert"></div><div class="sa-row sa-section"><button class="btn btn-primary btn-sm" type="submit">Save settings</button><span class="sa-small sa-muted">Validated on the server; the change is audited with before/after values.</span></div></form>';
        $$('[data-mirror]', el).forEach(function (c) { c.oninput = function () { $('#' + c.getAttribute('data-mirror'), el).value = c.value; }; });
        $('#wsForm', el).onsubmit = async function (e) {
          e.preventDefault();
          var body = {}, form = this;
          fields.forEach(function (f) {
            var inp = form.elements[f.key]; if (!inp) return;
            var v = f.kind === 'bool' ? inp.checked : inp.value.trim();
            var before = vals[f.key];
            if (f.kind === 'bool' ? (v !== (before === true || before === 'true')) : v !== (before == null ? '' : String(before))) body[f.key] = v;
          });
          if (!Object.keys(body).length) return toast('No changes to save', 'info');
          $('#wsErr', el).textContent = '';
          try { await busy($('button[type=submit]', form), function () { return api(CMS + '/settings', { method: 'PUT', body: body }); }); toast('Website settings saved'); Object.assign(vals, body); } catch (err) { $('#wsErr', el).textContent = err.message; }
        };
      } else if (key === 'media') {
        var offset = 0, limit = 24;
        var drawMedia = async function () {
          var d2 = await api(CMS + '/media' + qs({ offset: offset, limit: limit }));
          var items = d2.items || [];
          el.innerHTML = '<form class="sa-card" id="mdUp"><h3>Upload</h3><div class="sa-row"><label class="sr-only" for="mdFile">Image file</label><input type="file" id="mdFile" name="file" accept="image/png,image/jpeg,image/webp,image/gif,image/x-icon,image/vnd.microsoft.icon" required><button class="btn btn-primary btn-sm" type="submit">Upload</button><span class="sa-small sa-muted">JPEG, PNG, WebP, GIF or ICO · max 5 MB</span></div></form>' +
            '<div class="sa-grid sa-section" style="grid-template-columns:repeat(auto-fill,minmax(170px,1fr))">' + (items.length ? items.map(function (m, i) {
              return '<div class="sa-card" style="padding:10px"><div style="aspect-ratio:4/3;background:var(--surface);border-radius:8px;display:flex;align-items:center;justify-content:center;overflow:hidden"><img src="' + esc(m.url) + '" alt="' + esc(m.original_name || m.filename) + '" loading="lazy" style="max-width:100%;max-height:100%"></div>' +
                '<div class="sa-small" style="margin-top:6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="' + esc(m.original_name) + '">' + esc(m.original_name || m.filename) + '</div><div class="sa-small sa-muted">' + esc(fmtN(Math.round((m.size || 0) / 1024))) + ' KB · ' + esc(fmtDate(m.uploaded_at)) + '</div>' +
                '<div class="sa-row" style="margin-top:6px"><button type="button" class="btn btn-secondary btn-xs" data-cp="' + i + '">Copy URL</button><button type="button" class="btn btn-danger btn-xs" data-dl="' + i + '">Delete</button></div></div>';
            }).join('') : '<div style="grid-column:1/-1">' + emptyState('No media yet', 'Uploaded images can be used for logos, OG images and avatars.') + '</div>') + '</div>' +
            '<div class="sa-pager"><span>' + fmtN(d2.total) + ' files</span><span class="sa-row"><button type="button" class="btn btn-secondary btn-xs" id="mdPrev"' + (offset <= 0 ? ' disabled' : '') + '>← Prev</button><button type="button" class="btn btn-secondary btn-xs" id="mdNext"' + (offset + limit >= d2.total ? ' disabled' : '') + '>Next →</button></span></div>';
          $('#mdPrev', el).onclick = function () { offset = Math.max(0, offset - limit); drawMedia(); };
          $('#mdNext', el).onclick = function () { offset += limit; drawMedia(); };
          $$('[data-cp]', el).forEach(function (b) { b.onclick = function () { var u = location.origin + items[+b.getAttribute('data-cp')].url; try { navigator.clipboard.writeText(u).then(function () { toast('URL copied'); }, function () { toast(u, 'info'); }); } catch (e) { toast(u, 'info'); } }; });
          $$('[data-dl]', el).forEach(function (b) { b.onclick = async function () {
            var m = items[+b.getAttribute('data-dl')];
            var r = await confirmDialog({ title: 'Delete image', message: 'Deletes ' + (m.original_name || m.filename) + '. Pages or settings still pointing at it will show a broken image.', confirmLabel: 'Delete' });
            if (!r) return;
            try { await api(CMS + '/media/' + encodeURIComponent(cid(m)), { method: 'DELETE' }); toast('Image deleted'); drawMedia(); } catch (e) { toast(e.message, 'error'); }
          }; });
          $('#mdUp', el).onsubmit = async function (e) {
            e.preventDefault();
            var f = $('#mdFile', el).files[0];
            if (!f) return toast('Choose a file first', 'warning');
            if (f.size > 5 * 1024 * 1024) return toast('File is larger than 5 MB', 'error');
            var fd = new FormData(); fd.append('file', f);
            var btn = $('button[type=submit]', this);
            try {
              await busy(btn, async function () {
                var res = await fetch(CMS + '/media/upload', { method: 'POST', credentials: 'same-origin', body: fd });
                var data = {}; try { data = await res.json(); } catch (x) {}
                if (res.status === 401) { location.href = '/login?superadmin=1'; return; }
                if (!res.ok) throw new Error(UI.errorMessage ? UI.errorMessage(data, 'Upload failed') : 'Upload failed');
              });
              toast('Uploaded'); offset = 0; drawMedia();
            } catch (err) { toast(err.message, 'error'); }
          };
        };
        await drawMedia();
      } else {
        el.innerHTML = '<div class="sa-chips" id="ciChips"></div><div id="ciList"></div>';
        var lv = listView($('#ciList', el), {
          url: function (p) { return CMS + '/contact-submissions' + qs({ offset: (p.page - 1) * p.limit, limit: p.limit, q: p.q, read: p.read, sort: p.sort }); },
          sort: '-created_at', initial: { read: q.read || '' },
          filters: [{ key: 'q', label: 'Name, email, company or message…' }, { key: 'read', type: 'select', label: 'State', options: [['', 'All messages'], ['false', 'Unread'], ['true', 'Read']] }],
          onData: function (d3) { $('#ciChips', el).innerHTML = '<span class="sa-chip active">' + fmtN(d3.unread || 0) + ' unread</span><span class="sa-chip">' + fmtN(d3.total || 0) + ' matching</span>'; },
          columns: [
            { label: 'From', sort: 'name', render: function (m) { return (m.read ? '' : '<span class="sa-dot warn" aria-label="Unread"></span> ') + '<span class="cell-main">' + esc(m.name || '—') + '</span><span class="cell-sub">' + esc(m.email || '') + (m.company ? ' · ' + esc(m.company) : '') + '</span>'; } },
            { label: 'Message', render: function (m) { return '<span title="' + esc(m.message) + '">' + esc(short(m.subject ? m.subject + ' — ' + m.message : m.message, 100)) + '</span>'; } },
            { label: 'Received', sort: 'created_at', render: function (m) { return esc(fmtDT(m.created_at)); } },
            { label: '', cls: 'num', render: function (m) { return '<button type="button" class="btn btn-secondary btn-xs" data-rd>' + (m.read ? 'Mark unread' : 'Mark read') + '</button>'; } }
          ],
          rowClick: function (m) {
            openDrawer('Contact message', async function (body) {
              body.innerHTML = '<dl class="sa-kv"><dt>Name</dt><dd>' + esc(m.name) + '</dd><dt>Email</dt><dd>' + esc(m.email) + '</dd>' + (m.company ? '<dt>Company</dt><dd>' + esc(m.company) + '</dd>' : '') + (m.phone ? '<dt>Phone</dt><dd>' + esc(m.phone) + '</dd>' : '') + (m.subject ? '<dt>Subject</dt><dd>' + esc(m.subject) + '</dd>' : '') + '<dt>Received</dt><dd>' + esc(fmtDT(m.created_at)) + '</dd></dl><h4 class="sa-section">Message</h4><div class="sa-card" style="white-space:pre-wrap;overflow-wrap:anywhere">' + esc(m.message) + '</div>' +
                (m.email ? '<div class="sa-row sa-section"><a class="btn btn-secondary btn-sm" href="mailto:' + esc(encodeURIComponent(m.email).replace(/%40/g, '@')) + '">Reply by email</a></div>' : '');
              if (!m.read) { try { await api(CMS + '/contact-submissions/' + encodeURIComponent(cid(m)) + '/read?read=true', { method: 'PATCH' }); lv.reload(); refreshBell(); } catch (e) {} }
            });
          },
          bindRow: function (tr, m, reload) { $('[data-rd]', tr).onclick = async function () { try { await api(CMS + '/contact-submissions/' + encodeURIComponent(cid(m)) + '/read?read=' + (!m.read), { method: 'PATCH' }); reload(); } catch (e) { toast(e.message, 'error'); } }; },
          empty: { title: 'No contact messages', desc: 'Messages from the website contact form land here.' }
        });
      }
    });
  }

  // ════════════════════════════════════════════════════════
  //  SUPPORT TICKETS
  // ════════════════════════════════════════════════════════
  var TICKET_PILL = { open: ['pending', 'Open'], waiting: ['info', 'Waiting on customer'], resolved: ['completed', 'Resolved'], closed: ['cancelled', 'Closed'] };
  function ticketPill(s) { var x = TICKET_PILL[s] || ['muted', titleCase(s)]; return pill(x[0], x[1]); }
  async function viewSupport(root, q) {
    root.innerHTML = header('Support', 'Tickets raised by organization admins. Replies and status changes notify the organization\'s admins and are audited.') + '<div class="sa-chips" id="spChips"></div><div id="spList"></div>';
    var current = q.status || 'active';
    var lv;
    var chips = function (counts) {
      counts = counts || {};
      var all = Object.keys(counts).reduce(function (a, k) { return a + counts[k]; }, 0);
      $('#spChips', root).innerHTML = [['active', 'Needs attention', (counts.open || 0) + (counts.waiting || 0)], ['open', 'Open'], ['waiting', 'Waiting on customer'], ['resolved', 'Resolved'], ['closed', 'Closed'], ['', 'All', all]].map(function (c) {
        return '<button type="button" class="sa-chip' + (current === c[0] ? ' active' : '') + '" data-s="' + c[0] + '">' + esc(c[1]) + '<b>' + fmtN(c[2] != null ? c[2] : counts[c[0]] || 0) + '</b></button>';
      }).join('');
      $$('[data-s]', root).forEach(function (b) { b.onclick = function () { current = b.getAttribute('data-s'); lv.state.status = current; lv.state.page = 1; lv.reload(); }; });
    };
    lv = listView($('#spList', root), {
      url: function (p) { return '/api/super-admin/support/tickets' + qs({ page: p.page, limit: p.limit, sort: p.sort, status: p.status, q: p.q, priority: p.priority, organization_id: p.org }); },
      sort: '-updated_at', initial: { status: current, org: q.org || '' },
      filters: [{ key: 'q', label: 'Subject, organization, requester or #number…' }, { key: 'priority', type: 'select', label: 'Priority', options: [['', 'Any priority'], ['urgent', 'Urgent'], ['high', 'High'], ['normal', 'Normal'], ['low', 'Low']] }],
      onData: function (d) { chips(d.counts); },
      columns: [
        { label: 'Ticket', sort: 'number', render: function (t) { return '<a class="sa-link cell-main" href="#/support/' + esc(t.id) + '">#' + esc(t.number) + ' ' + esc(short(t.subject, 60)) + '</a><span class="cell-sub">' + esc(t.created_by || '') + '</span>'; } },
        { label: 'Organization', render: function (t) { return '<a class="sa-link" href="#/organizations/' + esc(t.organization_id) + '">' + esc(t.organization_name || short(t.organization_id)) + '</a>'; } },
        { label: 'Category', render: function (t) { return esc(titleCase(t.category || '')) + '<span class="cell-sub">' + pill(t.priority === 'urgent' || t.priority === 'high' ? 'failed' : 'muted', titleCase(t.priority || 'normal')) + '</span>'; } },
        { label: 'Status', render: function (t) { return ticketPill(t.status) + (t.awaiting_staff ? '<span class="cell-sub">' + pill('warn', 'Awaiting reply') + '</span>' : ''); } },
        { label: 'Messages', cls: 'num', render: function (t) { return fmtN(t.messages_count); } },
        { label: 'Updated', sort: 'updated_at', render: function (t) { return esc(ago(t.updated_at)); } }
      ],
      rowClick: function (t) { go('#/support/' + t.id); },
      empty: { title: 'No tickets', desc: 'Nothing in this queue.' }
    });
  }
  async function viewSupportTicket(root, q, id) {
    var t = (await api('/api/super-admin/support/tickets/' + encodeURIComponent(id))).ticket;
    setTitle('Ticket #' + t.number, 'Customers › Support');
    var reload = function () { route(); };
    root.innerHTML = '<div class="sa-row sa-small" style="margin-bottom:6px"><a class="sa-link" href="#/support">← Support</a></div>' +
      header('#' + t.number + ' · ' + t.subject, (t.organization_name || '') + ' · opened by ' + (t.created_by || '—') + ' · ' + fmtDT(t.created_at),
        ticketPill(t.status) + ['open', 'waiting', 'resolved', 'closed'].filter(function (s) { return s !== t.status; }).map(function (s) { return '<button type="button" class="btn btn-secondary btn-sm" data-st="' + s + '">Mark ' + esc((TICKET_PILL[s] || [0, s])[1].toLowerCase()) + '</button>'; }).join('') +
        '<a class="btn btn-secondary btn-sm" href="#/organizations/' + esc(t.organization_id) + '">Organization</a>') +
      '<div class="sa-grid sa-2" style="grid-template-columns:minmax(0,2fr) minmax(0,1fr)"><div><div class="sa-card"><h3>Conversation</h3><div style="display:grid;gap:10px">' + (t.messages || []).map(function (m) {
        return '<div style="max-width:85%;justify-self:' + (m.from_staff ? 'end' : 'start') + ';background:' + (m.from_staff ? 'var(--primary-light)' : 'var(--surface)') + ';border:1px solid var(--border);border-radius:12px;padding:10px 12px"><div class="sa-small" style="font-weight:600">' + esc(m.author || m.author_email || '') + (m.from_staff ? ' ' + pill('info', 'Staff') : '') + ' <span class="sa-muted" style="font-weight:400">· ' + esc(fmtDT(m.at)) + '</span></div><div style="white-space:pre-wrap;overflow-wrap:anywhere;margin-top:4px;font-size:13.5px">' + esc(m.body) + '</div></div>';
      }).join('') + '</div></div>' +
      '<form class="sa-card sa-section" id="tkReply"><h3>Reply as LeadAI Support</h3><label class="sr-only" for="tkMsg">Reply</label><textarea class="form-textarea" id="tkMsg" name="message" rows="5" maxlength="5000" required></textarea>' +
      '<div class="sa-row sa-section"><label class="sa-small" for="tkSt">Then set status</label><select class="form-select" id="tkSt" name="status" style="width:auto"><option value="waiting">Waiting on customer</option><option value="resolved">Resolved</option><option value="open">Keep open</option><option value="closed">Closed</option></select><button class="btn btn-primary btn-sm" type="submit">Send reply</button></div><div class="sa-err" id="tkErr" role="alert"></div></form></div>' +
      '<div><div class="sa-card"><h3>Details</h3><dl class="sa-kv"><dt>Status</dt><dd>' + ticketPill(t.status) + '</dd><dt>Priority</dt><dd>' + esc(titleCase(t.priority || 'normal')) + '</dd><dt>Category</dt><dd>' + esc(titleCase(t.category || '')) + '</dd><dt>Organization</dt><dd><a class="sa-link" href="#/organizations/' + esc(t.organization_id) + '">' + esc(t.organization_name || t.organization_id) + '</a></dd><dt>Requester</dt><dd>' + esc(t.created_by || '—') + '</dd><dt>Updated</dt><dd>' + esc(fmtDT(t.updated_at)) + '</dd></dl></div>' +
      '<div class="sa-card sa-section"><h3>Status history</h3>' + timeline(t.status_history || []) + '</div></div></div>';
    $$('[data-st]', root).forEach(function (b) { b.onclick = async function () {
      var s = b.getAttribute('data-st');
      var r = await confirmDialog({ title: 'Change ticket status', message: 'Set ticket #' + t.number + ' to "' + (TICKET_PILL[s] || [0, s])[1] + '". The organization\'s admins are notified.', confirmLabel: 'Update status', danger: s === 'closed', reason: 'optional' });
      if (!r) return;
      try { await api('/api/super-admin/support/tickets/' + encodeURIComponent(id) + '/status', { method: 'POST', body: { status: s, reason: r.reason } }); toast('Ticket updated'); reload(); } catch (e) { toast(e.message, 'error'); }
    }; });
    $('#tkReply', root).onsubmit = async function (e) {
      e.preventDefault();
      var fd = new FormData(this), msg = String(fd.get('message') || '').trim();
      if (!msg) { $('#tkErr', root).textContent = 'Write a reply first.'; return; }
      try { await busy($('button[type=submit]', this), function () { return api('/api/super-admin/support/tickets/' + encodeURIComponent(id) + '/messages', { method: 'POST', body: { message: msg, status: fd.get('status') } }); }); toast('Reply sent — the organization was notified'); reload(); }
      catch (err) { $('#tkErr', root).textContent = err.message; }
    };
  }

  // ════════════════════════════════════════════════════════
  //  NOTIFICATIONS & EMAIL OUTBOX
  // ════════════════════════════════════════════════════════
  function sevPill(s) { return pill({ danger: 'failed', warning: 'pending', success: 'completed', info: 'info' }[s] || 'info', titleCase(s || 'info')); }
  function internalLink(link) {
    if (!link) return '';
    var m = /^\/superadmin#\/?(.*)$/.exec(link);
    return m ? '#/' + m[1] : link;
  }
  async function viewNotifications(root, q) {
    root.innerHTML = header('Notifications', 'Platform events for super admins: demo requests, payments, approvals, failures and security events.') + '<div id="ntTabs"></div>';
    tabs($('#ntTabs', root), [['inbox', 'Inbox'], ['outbox', 'Email outbox'], ['config', 'Configuration']], q.tab || 'inbox', async function (key, el) {
      if (key === 'inbox') {
        var unread = q.unread === '1';
        el.innerHTML = '<div class="sa-row" style="margin-bottom:10px"><div class="sa-chips" style="margin:0"><button type="button" class="sa-chip' + (!unread ? ' active' : '') + '" data-u="0">All</button><button type="button" class="sa-chip' + (unread ? ' active' : '') + '" data-u="1">Unread</button></div><span class="sa-spacer"></span><button type="button" class="btn btn-secondary btn-sm" id="ntAll">Mark all read</button></div><div id="ntList"></div>';
        var lv = listView($('#ntList', el), {
          url: function (p) { return '/api/super-admin/notifications' + qs({ page: p.page, limit: p.limit, unread: p.unread ? 'true' : '' }); },
          initial: { unread: unread },
          columns: [
            { label: '', render: function (n) { return n.read ? '' : '<span class="sa-dot warn" title="Unread" aria-label="Unread"></span>'; } },
            { label: 'Event', render: function (n) { return '<span class="cell-main">' + esc(n.title) + '</span><span class="cell-sub">' + esc(n.message) + '</span>'; } },
            { label: 'Type', render: function (n) { return '<span class="sa-mono">' + esc(n.type) + '</span>'; } },
            { label: 'Severity', render: function (n) { return sevPill(n.severity); } },
            { label: 'When', render: function (n) { return esc(ago(n.created_at)); } },
            { label: '', cls: 'num', render: function (n) { return '<div class="row-actions">' + (n.type === 'support_ticket' && n.data && n.data.ticket_id ? '<a class="btn btn-secondary btn-xs" href="#/support/' + esc(n.data.ticket_id) + '">Open</a>' : n.link ? '<a class="btn btn-secondary btn-xs" href="' + esc(internalLink(n.link)) + '">Open</a>' : '') + (n.read ? '' : '<button type="button" class="btn btn-secondary btn-xs" data-read>Mark read</button>') + '</div>'; } }
          ],
          bindRow: function (tr, n, reload) { var b = $('[data-read]', tr); if (b) b.onclick = async function () { await api('/api/super-admin/notifications/read', { method: 'POST', body: { id: n.id } }); reload(); refreshBell(); }; },
          empty: { title: 'No notifications', desc: 'You are all caught up.' }
        });
        $$('[data-u]', el).forEach(function (b) { b.onclick = function () { $$('[data-u]', el).forEach(function (x) { x.classList.toggle('active', x === b); }); lv.state.unread = b.getAttribute('data-u') === '1'; lv.state.page = 1; lv.reload(); }; });
        $('#ntAll', el).onclick = async function () { await api('/api/super-admin/notifications/read', { method: 'POST', body: {} }); toast('All notifications marked read'); lv.reload(); refreshBell(); };
      } else if (key === 'outbox') {
        listView(el, {
          url: function (p) { return '/api/super-admin/email-outbox' + qs({ page: p.page, limit: p.limit, status: p.status, kind: p.kind, q: p.q }); },
          filters: [{ key: 'q', label: 'Recipient or subject…' }, { key: 'status', type: 'select', label: 'Status', options: [['', 'All'], ['queued', 'Queued'], ['sent', 'Sent'], ['failed', 'Failed']] },
            { key: 'kind', type: 'select', label: 'Kind', options: [['', 'All kinds'], ['password_reset', 'Password reset'], ['account_setup', 'Account setup'], ['invitation', 'Invitation'], ['demo', 'Demo'], ['generic', 'Generic']] }],
          columns: [
            { label: 'To', render: function (m) { return esc(m.to); } },
            { label: 'Subject', render: function (m) { return '<span class="cell-main">' + esc(m.subject) + '</span><span class="cell-sub">' + esc(m.kind) + '</span>'; } },
            { label: 'Status', render: function (m) { return pill(m.status) + (m.error ? '<span class="cell-sub" style="color:var(--danger-text)">' + esc(short(m.error, 50)) + '</span>' : ''); } },
            { label: 'Queued', render: function (m) { return esc(fmtDT(m.created_at)); } }
          ],
          rowClick: function (m) { openDrawer('Email — ' + m.subject, function (body) { body.innerHTML = '<dl class="sa-kv"><dt>To</dt><dd>' + esc(m.to) + '</dd><dt>Kind</dt><dd>' + esc(m.kind) + '</dd><dt>Status</dt><dd>' + pill(m.status) + '</dd><dt>Sent</dt><dd>' + esc(fmtDT(m.sent_at)) + '</dd></dl><p class="sa-small sa-muted">One-time link tokens are redacted.</p><pre class="sa-pre">' + esc(m.body) + '</pre>'; }); },
          empty: { title: 'Outbox empty' }
        });
      } else {
        await flagsEditor(el, ['notifications']);
      }
    });
  }

  // ════════════════════════════════════════════════════════
  //  AUDIT LOGS
  // ════════════════════════════════════════════════════════
  function auditDrawer(a) {
    openDrawer('Audit event', function (body) {
      var d = a.details || {};
      var hasBA = d.before !== undefined || d.after !== undefined;
      body.innerHTML = '<dl class="sa-kv"><dt>When</dt><dd>' + esc(fmtDT(a.at)) + '</dd><dt>Who</dt><dd>' + esc(a.actor_email || a.user || 'system') + ' <span class="sa-muted">' + esc(a.actor_role || a.role || '') + '</span></dd>' +
        '<dt>Action</dt><dd class="sa-mono">' + esc(a.action) + '</dd><dt>Category</dt><dd>' + esc(a.category) + '</dd><dt>Result</dt><dd>' + pill(a.status || (a.success === false ? 'failure' : 'success')) + '</dd>' +
        '<dt>Organization</dt><dd>' + (a.organization_id ? '<a class="sa-link" href="#/organizations/' + esc(a.organization_id) + '">' + esc(a.organization_id) + '</a>' : '—') + '</dd>' +
        '<dt>Resource</dt><dd>' + esc(a.resource_type || '—') + ' <span class="sa-mono">' + esc(a.resource_id || '') + '</span></dd><dt>IP</dt><dd>' + esc(a.ip || '—') + '</dd><dt>User agent</dt><dd class="sa-small">' + esc(a.user_agent || '—') + '</dd></dl>' +
        (hasBA ? '<div class="sa-grid sa-2 sa-section"><div><h4>Before</h4><pre class="sa-pre">' + esc(JSON.stringify(d.before, null, 2)) + '</pre></div><div><h4>After</h4><pre class="sa-pre">' + esc(JSON.stringify(d.after, null, 2)) + '</pre></div></div>' : '') +
        '<h4 class="sa-section">Details (secrets redacted)</h4><pre class="sa-pre">' + esc(JSON.stringify(d, null, 2)) + '</pre>';
    });
  }
  function auditList(el, fixed, o) {
    fixed = fixed || {}; o = o || {};
    var cats = [['', 'All categories']];
    var lvHolder = document.createElement('div');
    el.innerHTML = '';
    el.appendChild(lvHolder);
    var lv = listView(lvHolder, {
      url: function (p) { return '/api/super-admin/audit-logs' + qs(Object.assign({ page: p.page, limit: p.limit, q: p.q, actor_email: p.actor_email, action: p.action, category: p.category, status: p.status, from: p.from, to: p.to }, fixed)); },
      limit: 50, initial: o.initial || {},
      filters: [{ key: 'q', label: 'Resource id / action…' }, { key: 'actor_email', label: 'Actor email' }, { key: 'action', label: 'Action prefix (e.g. subscription.)' },
        { key: 'category', type: 'select', label: 'Category', options: cats }, { key: 'status', type: 'select', label: 'Result', options: [['', 'Any result'], ['success', 'Success'], ['failure', 'Failure']] },
        { key: 'from', type: 'date', label: 'From' }, { key: 'to', type: 'date', label: 'To' }],
      key: 'audit', exportUrl: function (st) { return reportUrl('audit-logs', { organization_id: fixed.organization_id, from: st.from, to: st.to, category: st.category, action: st.action, status: st.status }); },
      onData: function (d) {
        var sel = $('select[data-f=category]', el);
        if (sel && d.categories && sel.options.length <= 1) d.categories.forEach(function (c) { var op = document.createElement('option'); op.value = c; op.textContent = c; sel.appendChild(op); });
      },
      columns: [
        { label: 'When', render: function (a) { return '<span title="' + esc(fmtDT(a.at)) + '">' + esc(fmtDT(a.at)) + '</span>'; } },
        { label: 'Who', render: function (a) { return esc(a.actor_email || a.user || 'system') + '<span class="cell-sub">' + esc(a.actor_role || a.role || '') + '</span>'; } },
        { label: 'Action', render: function (a) { return '<span class="sa-mono">' + esc(a.action) + '</span><span class="cell-sub">' + esc(a.category) + '</span>'; } },
        { label: 'Organization', render: function (a) { return a.organization_id ? '<a class="sa-link sa-mono" href="#/organizations/' + esc(a.organization_id) + '">' + esc(short(a.organization_id)) + '</a>' : '—'; } },
        { label: 'Resource', render: function (a) { return esc(a.resource_type || '') + '<span class="cell-sub sa-mono">' + esc(short(a.resource_id, 16)) + '</span>'; } },
        { label: 'Result', render: function (a) { return pill(a.status || 'success'); } }
      ],
      rowClick: auditDrawer,
      empty: { title: 'No audit events', desc: 'Nothing matches these filters.' }
    });
    return lv;
  }
  async function viewAudit(root, q) {
    root.innerHTML = header('Audit logs', 'Tamper-resistant trail of every important action: who, what, when, organization, resource, before/after and result. Read-only.') + '<div id="auList"></div>';
    auditList($('#auList', root), q.org ? { organization_id: q.org } : {}, { initial: { action: q.action || '', category: q.category || '' } });
  }

  // ════════════════════════════════════════════════════════
  //  SECURITY CENTER
  // ════════════════════════════════════════════════════════
  function sessionsList(el, fixed) {
    fixed = fixed || {};
    return listView(el, {
      url: function (p) { return '/api/super-admin/sessions' + qs(Object.assign({ page: p.page, limit: p.limit, q: p.q, scope: p.scope }, fixed)); },
      limit: 50,
      filters: [{ key: 'q', label: 'Email…' }, { key: 'scope', type: 'select', label: 'Scope', options: [['', 'All sessions'], ['site', 'Customer app'], ['admin', 'Admin consoles']] }],
      columns: [
        { label: 'Account', render: function (s) { return '<span class="cell-main">' + esc(s.email) + '</span>' + (s.current ? pill('info', 'This session') : '') + (s.impersonated_by ? '<span class="cell-sub">' + pill('warn', 'Impersonation by ' + s.impersonated_by) + '</span>' : ''); } },
        { label: 'Scope', render: function (s) { return esc(s.scope === 'admin' ? 'Admin' : 'App') + '<span class="cell-sub">' + esc(s.role || '') + '</span>'; } },
        { label: 'Organization', render: function (s) { return s.organization_id ? '<a class="sa-link" href="#/organizations/' + esc(s.organization_id) + '">' + esc(s.organization_name || short(s.organization_id)) + '</a>' : '—'; } },
        { label: 'Device', render: function (s) { return '<span class="sa-small" title="' + esc(s.user_agent) + '">' + esc(short(s.user_agent, 36)) + '</span><span class="cell-sub sa-mono">ip#' + esc(s.ip_hash || '') + '</span>'; } },
        { label: 'Started', render: function (s) { return esc(ago(s.created_at)); } },
        { label: 'Expires', render: function (s) { return esc(fmtDate(s.expires_at)); } },
        { label: '', cls: 'num', render: function () { return '<button type="button" class="btn btn-danger btn-xs" data-rv>Revoke</button>'; } }
      ],
      bindRow: function (tr, s, reload) {
        $('[data-rv]', tr).onclick = async function () {
          var r = await confirmDialog({ title: 'Revoke session', message: 'Signs ' + s.email + ' out of this session immediately.' + (s.current ? ' This is YOUR current session — you will be signed out.' : ''), confirmLabel: 'Revoke', reason: 'optional' });
          if (!r) return;
          try { await api('/api/super-admin/sessions/' + s.ref + '/revoke', { method: 'POST', body: { reason: r.reason } }); toast('Session revoked'); if (s.current) location.href = '/login?superadmin=1'; else reload(); } catch (e) { toast(e.message, 'error'); }
        };
      },
      empty: { title: 'No active sessions' }
    });
  }
  async function viewSecurity(root, q) {
    var ov = await api('/api/super-admin/security/overview');
    var o = ov.overview, g = ov.groups;
    root.innerHTML = header('Security center', 'Logins, lockouts, security events, sessions, permission violations and cross-tenant attempts.', adminLink('security', 'Security configuration')) +
      '<div class="sa-grid sa-kpis">' + kpi('Failed logins (24h)', fmtN(o.failed_logins_24h), fmtN(o.failed_logins_7d) + ' in 7 days', { tone: o.failed_logins_24h > 20 ? 'bad' : o.failed_logins_24h ? 'warn' : '' }) +
      kpi('Active lockouts', fmtN(o.active_lockouts), '', { tone: o.active_lockouts ? 'warn' : '' }) + kpi('Rate-limit events (7d)', fmtN(o.rate_limit_events_7d)) +
      kpi('Permission violations (7d)', fmtN(o.permission_violations_7d), '', { tone: o.permission_violations_7d ? 'warn' : '' }) + kpi('Cross-tenant attempts (7d)', fmtN(o.cross_tenant_attempts_7d), '', { tone: o.cross_tenant_attempts_7d ? 'bad' : '' }) +
      kpi('High severity (7d)', fmtN(o.high_severity_7d), '', { tone: o.high_severity_7d ? 'bad' : '' }) + kpi('Active sessions', fmtN(o.active_sessions)) + kpi('Logins (24h)', fmtN(o.successful_logins_24h)) + '</div><div class="sa-section" id="seTabs"></div>';
    tabs($('#seTabs', root), [['events', 'Security events'], ['logins', 'Login activity'], ['lockouts', 'Lockouts'], ['sessions', 'Active sessions'], ['config', 'Configuration']], q.tab || 'events', function (key, el) {
      if (key === 'events') {
        var typeOpts = [['', 'All types'], ['login_failed', 'Failed login'], ['account_locked', 'Account locked']].concat(g.rate_limit.map(function (t) { return [t, titleCase(t)]; }), g.permission.map(function (t) { return [t, titleCase(t)]; }), g.cross_tenant.map(function (t) { return [t, titleCase(t)]; }), [['invalid_webhook_signature', 'Invalid webhook signature']]);
        listView(el, {
          url: function (p) { return '/api/super-admin/security-events' + qs({ page: p.page, limit: p.limit, type: p.type, severity: p.severity, q: p.q }); },
          limit: 50, initial: { type: q.type || '' },
          filters: [{ key: 'q', label: 'Actor email, IP or path…' }, { key: 'type', type: 'select', label: 'Type', options: typeOpts }, { key: 'severity', type: 'select', label: 'Severity', options: [['', 'Any severity'], ['low', 'Low'], ['medium', 'Medium'], ['high', 'High'], ['critical', 'Critical']] }],
          columns: [
            { label: 'When', render: function (e) { return esc(fmtDT(e.at)); } },
            { label: 'Event', render: function (e) { return '<span class="cell-main">' + esc(titleCase(e.type)) + '</span><span class="cell-sub sa-mono">' + esc((e.method || '') + ' ' + (e.path || '')) + '</span>'; } },
            { label: 'Severity', render: function (e) { return pill(e.severity); } },
            { label: 'Actor', render: function (e) { return esc(e.actor_email || 'anonymous') + '<span class="cell-sub">' + esc(e.ip || '') + '</span>'; } },
            { label: 'Target org', render: function (e) { return e.target_organization_id ? '<a class="sa-link sa-mono" href="#/organizations/' + esc(e.target_organization_id) + '">' + esc(short(e.target_organization_id)) + '</a>' : '—'; } }
          ],
          rowClick: function (e) { openDrawer('Security event', function (body) { body.innerHTML = '<pre class="sa-pre">' + esc(JSON.stringify(e, null, 2)) + '</pre>'; }); },
          empty: { title: 'No security events', desc: 'Nothing suspicious recorded for these filters.' }
        });
      } else if (key === 'logins') {
        listView(el, {
          url: function (p) { return '/api/super-admin/security/logins' + qs({ page: p.page, limit: p.limit, q: p.q, success: p.success }); },
          limit: 50,
          filters: [{ key: 'q', label: 'Email or IP…' }, { key: 'success', type: 'select', label: 'Result', options: [['', 'All attempts'], ['true', 'Successful'], ['false', 'Failed']] }],
          columns: [
            { label: 'When', render: function (a) { return esc(fmtDT(a.at)); } },
            { label: 'Account', render: function (a) { return esc(a.actor_email || a.user || '—'); } },
            { label: 'Result', render: function (a) { return pill(a.status || (a.success ? 'success' : 'failure')); } },
            { label: 'IP', render: function (a) { return '<span class="sa-mono">' + esc(a.ip || '—') + '</span>'; } },
            { label: 'Device', render: function (a) { return '<span class="sa-small">' + esc(short(a.user_agent, 50)) + '</span>'; } }
          ],
          empty: { title: 'No login activity' }
        });
      } else if (key === 'lockouts') {
        listView(el, {
          url: function (p) { return '/api/super-admin/security/lockouts' + qs({ page: p.page, limit: p.limit, active_only: p.active_only }); },
          filters: [{ key: 'active_only', type: 'select', label: 'Show', options: [['', 'All tracked accounts'], ['true', 'Currently locked']] }],
          columns: [
            { label: 'Account', render: function (l) { return esc(l.email); } },
            { label: 'State', render: function (l) { return l.locked ? pill('suspended', 'Locked') : pill('muted', 'Tracking'); } },
            { label: 'Failures', cls: 'num', render: function (l) { return fmtN(l.failures); } },
            { label: 'Locked until', render: function (l) { return esc(fmtDT(l.locked_until)); } },
            { label: '', cls: 'num', render: function () { return '<button type="button" class="btn btn-secondary btn-xs" data-clear>Unlock</button>'; } }
          ],
          bindRow: function (tr, l, reload) { $('[data-clear]', tr).onclick = async function () {
            var r = await confirmDialog({ title: 'Unlock account', message: 'Clears the failed-login counter and lock for ' + l.email + '.', confirmLabel: 'Unlock', danger: false, reason: 'optional' });
            if (!r) return;
            try { await api('/api/super-admin/security/lockouts/clear', { method: 'POST', body: { email: l.email, reason: r.reason } }); toast('Account unlocked'); reload(); } catch (e) { toast(e.message, 'error'); }
          }; },
          empty: { title: 'No lockouts', desc: 'Accounts are locked after repeated failed sign-ins.' }
        });
      } else if (key === 'sessions') {
        sessionsList(el, {});
      } else {
        el.innerHTML = '<div class="sa-card"><h3>Security configuration</h3><p class="sa-small" style="color:var(--text-secondary)">Session timeout, login protection, lockout threshold/minutes and impersonation duration are managed in the platform console security settings. Audit logging is always on and cannot be disabled.</p><div class="sa-row">' +
          adminLink('security', 'Security settings') + adminLink('settings', 'Global settings') + '<a class="btn btn-secondary btn-sm" href="#/roles">Roles & permissions</a></div></div>';
      }
    });
  }

  // ════════════════════════════════════════════════════════
  //  ROLES & PERMISSIONS
  // ════════════════════════════════════════════════════════
  async function viewRoles(root) {
    var d = await api('/api/super-admin/role-permissions');
    var locked = d.locked_roles || [];
    function matrix(kind, roles, perms) {
      var names = Object.keys(roles);
      return '<div class="sa-table-wrap"><table class="sa-table sa-matrix"><thead><tr><th>Permission</th>' + names.map(function (r) {
        return '<th>' + esc(kind === 'org' ? (d.labels[r] || titleCase(r)) : titleCase(r)) + (locked.indexOf(r) >= 0 ? '<br><span class="sa-small sa-muted">locked</span>' : '<br><button type="button" class="btn btn-secondary btn-xs" data-save="' + esc(kind + ':' + r) + '">Save</button>') + '</th>';
      }).join('') + '</tr></thead><tbody>' + perms.map(function (p) {
        return '<tr><td class="sa-mono">' + esc(p) + '</td>' + names.map(function (r) {
          var on = roles[r].indexOf(p) >= 0;
          return '<td><input type="checkbox" aria-label="' + esc(r + ': ' + p) + '" data-role="' + esc(kind + ':' + r) + '" value="' + esc(p) + '"' + (on ? ' checked' : '') + (locked.indexOf(r) >= 0 ? ' disabled' : '') + '></td>';
        }).join('') + '</tr>';
      }).join('') + '</tbody></table></div>';
    }
    root.innerHTML = header('Roles & permissions', 'Global permission matrix. Owner and Super Admin are immutable. Changes apply on the next request of every affected user and are audited.') +
      '<div class="sa-card"><h3>Organization roles <span class="sa-small sa-muted">("member" is shown to customers as User)</span></h3>' + matrix('org', d.org, d.all_org_permissions) + '</div>' +
      '<div class="sa-card sa-section"><h3>Platform staff roles</h3>' + matrix('platform', d.platform, d.all_platform_permissions) + '</div>';
    $$('[data-save]', root).forEach(function (b) { b.onclick = async function () {
      var key = b.getAttribute('data-save'), parts = key.split(':');
      var perms = $$('input[data-role="' + key + '"]', root).filter(function (c) { return c.checked; }).map(function (c) { return c.value; });
      var r = await confirmDialog({ title: 'Save ' + parts[1] + ' permissions', message: perms.length + ' permissions will be granted to every "' + parts[1] + '" (' + parts[0] + ' role).', confirmLabel: 'Save', danger: false });
      if (!r) return;
      try { await busy(b, function () { return api('/api/super-admin/role-permissions', { method: 'PUT', body: { kind: parts[0], role: parts[1], permissions: perms } }); }); toast('Permissions saved'); } catch (e) { toast(e.message, 'error'); }
    }; });
  }

  // ════════════════════════════════════════════════════════
  //  INTEGRATIONS / HEALTH / FLAGS / MAINTENANCE / REPORTS
  // ════════════════════════════════════════════════════════
  function yes(v, okLabel, badLabel) { return v ? pill('active', okLabel || 'Configured') : pill('warn', badLabel || 'Not configured'); }
  async function viewIntegrations(root) {
    var i = (await api('/api/super-admin/integrations')).integrations;
    var ap = i.apify, ai = i.ai, pay = i.payments, em = i.email;
    root.innerHTML = header('Integrations', 'Connection status only — secret values are never sent to the browser.') + '<div class="sa-grid sa-2">' +
      '<div class="sa-card"><h3>Apify <a class="sa-link" href="/admin#/apify" target="_blank" rel="noopener">Manage ↗</a></h3><dl class="sa-kv"><dt>API token</dt><dd>' + yes(ap.configured) + '</dd><dt>Last connection test</dt><dd>' + (ap.last_test_ok == null ? '<span class="sa-muted">never</span>' : pill(ap.last_test_ok ? 'active' : 'failed', ap.last_test_ok ? 'Passed' : 'Failed')) + ' <span class="sa-small sa-muted">' + esc(ap.last_test_at ? fmtDT(ap.last_test_at) : '') + '</span></dd>' +
      '<dt>Jobs (24h)</dt><dd>' + fmtN(ap.jobs_24h) + ' · <span style="color:' + (ap.failures_24h ? 'var(--danger-text)' : 'inherit') + '">' + fmtN(ap.failures_24h) + ' failed</span></dd><dt>Usage (30d)</dt><dd>$' + esc(Number(ap.usage_usd_30d || 0).toFixed(2)) + '</dd>' +
      Object.keys(ap.actors).map(function (p) { return '<dt>' + esc(titleCase(p)) + '</dt><dd><span class="sa-mono">' + esc(ap.actors[p] || '—') + '</span> ' + (ap.platforms_enabled[p] ? pill('active', 'On') : pill('disabled', 'Off')) + '</dd>'; }).join('') + '</dl></div>' +
      '<div class="sa-card"><h3>AI provider <a class="sa-link" href="#/ai">AI management →</a></h3><dl class="sa-kv"><dt>Provider</dt><dd>' + esc(titleCase(ai.provider)) + '</dd><dt>API key</dt><dd>' + yes(ai.configured) + '</dd><dt>AI enabled</dt><dd>' + yes(ai.enabled, 'Enabled', 'Disabled') + '</dd><dt>Model</dt><dd class="sa-mono">' + esc(ai.model) + '</dd><dt>Calls (24h)</dt><dd>' + fmtN(ai.requests_24h) + ' · ' + fmtN(ai.failures_24h) + ' failed</dd></dl></div>' +
      '<div class="sa-card"><h3>Payment provider</h3><dl class="sa-kv"><dt>Active provider</dt><dd>' + pill(pay.provider === 'mock' ? 'warn' : 'active', titleCase(pay.provider)) + '</dd><dt>Stripe secret key</dt><dd>' + yes(pay.stripe_key_configured) + '</dd><dt>Stripe webhook secret</dt><dd>' + yes(pay.stripe_webhook_secret_configured) + '</dd><dt>BILLING_WEBHOOK_SECRET</dt><dd>' + yes(pay.billing_webhook_secret_configured) + '</dd><dt>Failed payments (7d)</dt><dd>' + fmtN(pay.failed_payments_7d) + '</dd></dl>' +
      (pay.provider === 'mock' ? '<p class="sa-small sa-muted">The mock provider is active because no Stripe secret key is configured.</p>' : '') + '</div>' +
      '<div class="sa-card"><h3>Email <a class="sa-link" href="#/notifications?tab=outbox">Outbox →</a></h3><dl class="sa-kv"><dt>SMTP host</dt><dd>' + yes(em.smtp_configured, 'Configured', 'Not configured — queue only') + '</dd><dt>SMTP auth</dt><dd>' + yes(em.smtp_auth_configured) + '</dd><dt>PUBLIC_BASE_URL</dt><dd>' + yes(em.public_base_url_configured, 'Set', 'Not set — links are relative') + '</dd>' +
      '<dt>Outbox</dt><dd>' + fmtN(em.outbox.queued) + ' queued · ' + fmtN(em.outbox.sent) + ' sent · ' + fmtN(em.outbox.failed) + ' failed</dd></dl></div>' +
      '<div class="sa-card"><h3>Storage</h3><dl class="sa-kv"><dt>Exports</dt><dd>' + fmtN(i.storage.exports) + '</dd><dt>Website media files</dt><dd>' + fmtN(i.storage.media_files) + '</dd></dl></div>' +
      '<div class="sa-card"><h3>Database <a class="sa-link" href="/admin#/database" target="_blank" rel="noopener">Details ↗</a></h3><dl class="sa-kv"><dt>Connection</dt><dd>' + yes(i.database.connected, 'Connected', 'Unreachable') + '</dd><dt>Ping latency</dt><dd>' + esc(i.database.latency_ms == null ? '—' : i.database.latency_ms + ' ms') + '</dd></dl></div></div>' +
      '<p class="sa-small sa-muted sa-section">Secrets are edited only in the locked environment panel of the platform console (<a href="/admin#/environment" target="_blank" rel="noopener">Environment ↗</a>).</p>';
  }

  async function viewHealth(root) {
    var h = (await api('/api/super-admin/health')).health;
    setHealthPill(h.overall);
    root.innerHTML = header('System health', 'Checked ' + ago(h.checked_at) + ' · refreshes every 30 seconds.', '<button type="button" class="btn btn-secondary btn-sm" id="hRef">↻ Refresh</button>' + adminLink('health', 'Platform console health') + adminLink('logs', 'Logs')) +
      '<div class="sa-grid sa-kpis">' + h.components.map(function (c) {
        return '<div class="sa-kpi ' + (c.status === 'warn' ? 'warn' : c.status === 'ok' ? '' : 'bad') + '"><div class="k">' + esc(c.label) + '</div><div class="v" style="font-size:16px"><span class="sa-dot ' + esc(c.status) + '" aria-hidden="true"></span> ' + esc(c.status === 'ok' ? 'Healthy' : c.status === 'warn' ? 'Attention' : 'Unhealthy') + '</div><div class="s">' + esc(c.detail) + '</div></div>';
      }).join('') + '</div>' +
      '<div class="sa-grid sa-2 sa-section"><div class="sa-card"><h3>Recent server errors (5xx)</h3>' + ((h.recent_errors || []).length ? '<ul class="sa-feed">' + h.recent_errors.map(function (e) { return '<li><div><b>' + esc(e.title) + '</b><div class="sa-small sa-muted">' + esc(e.message) + '</div></div><span class="when">' + esc(ago(e.created_at)) + '</span></li>'; }).join('') + '</ul>' : emptyState('No server errors in 7 days')) + '</div>' +
      '<div class="sa-card"><h3>Collections</h3>' + barList(Object.keys(h.collection_counts || {}).map(function (k) { return { label: k, value: h.collection_counts[k] }; })) + '</div></div>';
    $('#hRef', root).onclick = function () { route(); };
    S.timers.push(setInterval(function () { if (!document.hidden && /^#\/?health/.test(location.hash)) route(true); }, 30000));
  }

  async function flagsEditor(el, groups, o) {
    o = o || {};
    var d = await api('/api/super-admin/feature-flags');
    var flags = d.flags.filter(function (f) { return !groups || groups.indexOf(f.group) >= 0; });
    var titles = { features: 'Product features', platforms: 'Scraping platforms', maintenance: 'Maintenance mode', notifications: 'Notifications' };
    var byGroup = {};
    flags.forEach(function (f) { (byGroup[f.group] = byGroup[f.group] || []).push(f); });
    el.innerHTML = '<form id="ffForm" novalidate><div class="alert alert-info"><div class="alert-body">' + esc(d.note) + '</div></div><div class="sa-grid ' + (Object.keys(byGroup).length > 1 ? 'sa-2' : '') + ' sa-section">' + Object.keys(byGroup).map(function (g) {
      return '<div class="sa-card"><h3>' + esc(titles[g] || g) + '</h3>' + byGroup[g].map(function (f) {
        var id = 'ff_' + f.key.replace(/\./g, '_');
        if (f.type === 'text') return '<div class="sa-flag" style="display:block"><label for="' + id + '" style="font-weight:600;font-size:13px">' + esc(f.label) + '</label><textarea class="form-textarea" id="' + id + '" name="' + esc(f.key) + '" data-orig="' + esc(f.value) + '" maxlength="500" rows="2" style="min-height:60px;margin-top:6px">' + esc(f.value) + '</textarea><div class="sa-small sa-muted">' + esc(f.help) + '</div></div>';
        return '<div class="sa-flag"><label class="sa-toggle"><input type="checkbox" id="' + id + '" name="' + esc(f.key) + '" data-orig="' + (f.value ? '1' : '0') + '"' + (f.value ? ' checked' : '') + '><span></span></label><div><label for="' + id + '" style="font-weight:600;font-size:13px">' + esc(f.label) + '</label><div class="sa-small sa-muted">' + esc(f.help) + '</div></div></div>';
      }).join('') + '</div>';
    }).join('') + '</div><div class="sa-row sa-section"><label class="sr-only" for="ffReason">Reason</label><input class="form-input" id="ffReason" name="__reason" placeholder="Reason for this change (audited)" style="max-width:360px"><button class="btn btn-primary btn-sm" type="submit">Save changes</button><span class="sa-small sa-muted" id="ffDirty"></span></div></form>';
    var form = $('#ffForm', el);
    var changes = function () {
      var ch = {};
      $$('[data-orig]', form).forEach(function (i) {
        if (i.type === 'checkbox') { if ((i.checked ? '1' : '0') !== i.getAttribute('data-orig')) ch[i.name] = i.checked; }
        else if (i.value.trim() !== i.getAttribute('data-orig')) ch[i.name] = i.value.trim();
      });
      return ch;
    };
    form.addEventListener('change', function () { var n = Object.keys(changes()).length; $('#ffDirty', form).textContent = n ? n + ' unsaved change(s)' : ''; });
    form.addEventListener('input', function () { var n = Object.keys(changes()).length; $('#ffDirty', form).textContent = n ? n + ' unsaved change(s)' : ''; });
    form.onsubmit = async function (e) {
      e.preventDefault();
      var ch = changes();
      if (!Object.keys(ch).length) return toast('No changes to save', 'info');
      var risky = Object.keys(ch).filter(function (k) { return (k === 'maintenance.enabled' && ch[k]) || (/^features\.|^platform\./.test(k) && ch[k] === false); });
      if (risky.length) {
        var r = await confirmDialog({ title: 'Confirm platform change', message: 'You are turning ' + (ch['maintenance.enabled'] ? 'ON maintenance mode (customers see a maintenance page)' : 'OFF: ' + risky.join(', ')) + '. New actions are blocked immediately; existing data is not changed.', confirmLabel: 'Apply' });
        if (!r) return;
      }
      try {
        var out = await busy($('button[type=submit]', form), function () { return api('/api/super-admin/feature-flags', { method: 'PUT', body: { changes: ch, reason: $('#ffReason', form).value } }); });
        toast(out.changed.length + ' setting(s) saved');
        if (o.after) o.after(); else flagsEditor(el, groups, o);
      } catch (err) { toast(err.message, 'error'); }
    };
  }
  async function viewFlags(root) {
    root.innerHTML = header('Feature flags', 'Global switches for product features, scraping platforms, maintenance and notifications.', adminLink('features', 'Platform console features')) + '<div id="ffBody"></div>';
    await flagsEditor($('#ffBody', root), ['features', 'platforms', 'maintenance']);
  }
  async function viewMaintenance(root) {
    root.innerHTML = header('Maintenance', 'Maintenance mode blocks the customer app with a 503 page. Admin consoles, sign-in and health stay reachable.', adminLink('maintenance', 'Maintenance tools') + adminLink('settings', 'Global settings')) + '<div id="mtBody"></div>';
    await flagsEditor($('#mtBody', root), ['maintenance']);
  }

  async function viewReports(root) {
    var d = await api('/api/super-admin/reports');
    root.innerHTML = header('Reports & exports', 'Redacted CSV exports (no passwords, hashes, tokens or keys; lead contact data masked). Every export is audited. Max ' + fmtN(d.max_rows) + ' rows.') +
      '<form class="sa-card" id="rpForm"><h3>Filters (optional)</h3><div class="sa-filters" style="margin:0"><label class="sr-only" for="rpOrg">Organization ID</label><input class="form-input sa-grow" id="rpOrg" name="organization_id" placeholder="Organization ID">' +
      '<label class="sa-small sa-muted" for="rpFrom">From</label><input class="form-input" type="date" id="rpFrom" name="from"><label class="sa-small sa-muted" for="rpTo">To</label><input class="form-input" type="date" id="rpTo" name="to">' +
      '<label class="sr-only" for="rpStatus">Status</label><input class="form-input" id="rpStatus" name="status" placeholder="Status (e.g. active, failed)"></div></form>' +
      '<div class="sa-grid sa-3 sa-section">' + d.kinds.map(function (k) { return '<div class="sa-card"><h3>' + esc(k.label) + '</h3><button type="button" class="btn btn-secondary btn-sm" data-k="' + esc(k.key) + '">Download CSV</button></div>'; }).join('') + '</div>' +
      '<div class="sa-card sa-section"><h3>Recent exports <a class="sa-link" href="#/audit?action=report.exported">All in audit log →</a></h3>' + ((d.recent || []).length ? '<ul class="sa-feed">' + d.recent.map(function (a) {
        return '<li><div><span class="sa-mono">' + esc(a.resource_id) + '</span> · ' + esc(fmtN((a.details || {}).rows || 0)) + ' rows<div class="sa-small sa-muted">' + esc(a.actor_email) + '</div></div><span class="when">' + esc(fmtDT(a.at)) + '</span></li>';
      }).join('') + '</ul>' : emptyState('No exports yet')) + '</div>';
    $$('[data-k]', root).forEach(function (b) { b.onclick = function () {
      var fd = new FormData($('#rpForm', root));
      location.href = '/api/super-admin/reports/' + b.getAttribute('data-k') + '.csv' + qs({ organization_id: String(fd.get('organization_id') || '').trim(), from: fd.get('from'), to: fd.get('to'), status: String(fd.get('status') || '').trim() });
      toast('Export started — it is recorded in the audit log', 'info');
    }; });
  }

  // ════════════════════════════════════════════════════════
  //  NAVIGATION / ROUTER / SHELL
  // ════════════════════════════════════════════════════════
  var IC = {
    dash: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
    org: '<path d="M3 21h18"/><path d="M5 21V7l8-4v18"/><path d="M19 21V11l-6-3"/>',
    shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    users: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/>',
    gift: '<rect x="3" y="8" width="18" height="4" rx="1"/><path d="M12 8v13"/><path d="M19 12v9H5v-9"/><path d="M7.5 8a2.5 2.5 0 0 1 0-5C11 3 12 8 12 8s1-5 4.5-5a2.5 2.5 0 0 1 0 5"/>',
    layers: '<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>',
    tag: '<path d="M20.59 13.41 13.42 20.58a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><circle cx="7" cy="7" r="1.5"/>',
    repeat: '<polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/>',
    card: '<rect x="1" y="4" width="22" height="16" rx="2"/><line x1="1" y1="10" x2="23" y2="10"/>',
    coin: '<circle cx="12" cy="12" r="9"/><path d="M14.5 9a2.5 2.5 0 0 0-2.5-1.5c-1.4 0-2.5.8-2.5 2s1.1 1.7 2.5 2 2.5.8 2.5 2-1.1 2-2.5 2A2.5 2.5 0 0 1 9.5 15"/><path d="M12 6v1.5M12 16.5V18"/>',
    bolt: '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
    search: '<circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    cpu: '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3"/>',
    file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>',
    msg: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    star: '<polygon points="12 2 15 9 22 9.3 16.5 14 18.2 21 12 17.3 5.8 21 7.5 14 2 9.3 9 9"/>',
    brain: '<path d="M9 3a3 3 0 0 0-3 3v1a3 3 0 0 0-2 5 3 3 0 0 0 2 5v1a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3z"/><path d="M15 3a3 3 0 0 1 3 3v1a3 3 0 0 1 2 5 3 3 0 0 1-2 5v1a3 3 0 0 1-6 0"/>',
    chart: '<line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>',
    bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/>',
    list: '<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>',
    lock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    cols: '<rect x="3" y="4" width="18" height="16" rx="2"/><line x1="9" y1="4" x2="9" y2="20"/><line x1="15" y1="4" x2="15" y2="20"/>',
    key: '<circle cx="7.5" cy="15.5" r="4.5"/><path d="M21 2l-9.6 9.6M15.5 7.5l3 3L22 7l-3-3"/>',
    globe: '<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15 15 0 0 1 0 20 15 15 0 0 1 0-20z"/>',
    plug: '<path d="M9 2v6M15 2v6"/><path d="M6 8h12v4a6 6 0 0 1-12 0z"/><path d="M12 18v4"/>',
    heart: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    flag: '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><line x1="4" y1="22" x2="4" y2="15"/>',
    download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
    gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
    tool: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94z"/>'
  };
  var EXT = '<svg class="sa-ext" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>';
  var MENU = [
    ['Overview', [['dashboard', 'Dashboard', 'dash']]],
    ['Customers', [['organizations', 'Organizations', 'org'], ['admins', 'Admins', 'shield'], ['users', 'Users', 'users'], ['demo', 'Demo Management', 'gift', 'demo'], ['support', 'Support', 'msg', 'support']]],
    ['Revenue', [['plans', 'Plans', 'layers'], ['pricing', 'Pricing', 'tag'], ['subscriptions', 'Subscriptions', 'repeat', 'queue'], ['payments', 'Payments', 'card'], ['tokens', 'Tokens & Usage', 'coin']]],
    ['LeadAI Operations', [['ops/agent', 'URL Search Agent', 'bolt'], ['ops/searches', 'Searches', 'search'], ['ops/jobs', 'Apify Jobs', 'cpu'], ['/admin#/pages', 'Pages', 'file'], ['/admin#/posts', 'Posts', 'msg'], ['/admin#/ci', 'Comments', 'msg'], ['ops/leads', 'Leads', 'star']]],
    ['Intelligence', [['ai', 'AI Management', 'brain'], ['analytics', 'Analytics', 'chart']]],
    ['Governance', [['notifications', 'Notifications', 'bell', 'notif'], ['audit', 'Audit Logs', 'list'], ['security', 'Security Center', 'lock'], ['roles', 'Roles & Permissions', 'key']]],
    ['Platform', [['website', 'Website / CMS', 'globe'], ['integrations', 'Integrations', 'plug'], ['health', 'System Health', 'heart'], ['flags', 'Feature Flags', 'flag'], ['reports', 'Reports / Exports', 'download'], ['/admin#/settings', 'Global Settings', 'gear'], ['maintenance', 'Maintenance', 'tool']]]
  ];
  var ROUTES = {
    dashboard: [viewDashboard, 'Dashboard'], organizations: [viewOrganizations, 'Organizations', viewOrgDetail], admins: [viewAdmins, 'Admins'],
    users: [viewUsers, 'Users', viewUserDetail], demo: [viewDemo, 'Demo Management'], plans: [viewPlans, 'Plans'], pricing: [viewPricing, 'Pricing'],
    subscriptions: [viewSubscriptions, 'Subscriptions'], payments: [viewPayments, 'Payments'], tokens: [viewTokens, 'Tokens & Usage'],
    'ops/agent': [viewOpsAgent, 'URL Search Agent'], 'ops/searches': [viewOpsSearches, 'Searches'], 'ops/jobs': [viewOpsJobs, 'Apify Jobs'],
    'ops/leads': [viewOpsLeads, 'Leads'], 'ops/chain': [null, 'Investigation', viewChain],
    ai: [viewAI, 'AI Management'], analytics: [viewAnalytics, 'Analytics'], notifications: [viewNotifications, 'Notifications'],
    audit: [viewAudit, 'Audit Logs'], security: [viewSecurity, 'Security Center'], roles: [viewRoles, 'Roles & Permissions'],
    website: [viewWebsite, 'Website / CMS'], cms: [viewWebsite, 'Website / CMS'], support: [viewSupport, 'Support', viewSupportTicket], integrations: [viewIntegrations, 'Integrations'], health: [viewHealth, 'System Health'],
    flags: [viewFlags, 'Feature Flags'], reports: [viewReports, 'Reports / Exports'], maintenance: [viewMaintenance, 'Maintenance']
  };
  var GROUP_OF = {};
  MENU.forEach(function (g) { g[1].forEach(function (i) { GROUP_OF[i[0]] = g[0]; }); });

  function store(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } }
  function renderNav() {
    var closed = {};
    try { closed = JSON.parse(store('sa_nav_closed') || '{}') || {}; } catch (e) { closed = {}; }
    $('#saNav').innerHTML = MENU.map(function (g) {
      return '<div class="sa-group' + (closed[g[0]] ? ' closed' : '') + '" data-g="' + esc(g[0]) + '"><button type="button" class="sa-group-btn" aria-expanded="' + !closed[g[0]] + '"><span>' + esc(g[0]) + '</span><svg class="chev" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg></button><div class="sa-group-items">' +
        g[1].map(function (i) {
          var ext = i[0].charAt(0) === '/';
          return '<a class="sa-item" href="' + (ext ? esc(i[0]) : '#/' + esc(i[0])) + '"' + (ext ? ' target="_blank" rel="noopener"' : ' data-route="' + esc(i[0]) + '"') + ' title="' + esc(i[1]) + (ext ? ' (opens the platform console)' : '') + '" aria-label="' + esc(i[1]) + (ext ? ' (opens the platform console in a new tab)' : '') + '">' +
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + IC[i[2]] + '</svg><span class="sa-label">' + esc(i[1]) + '</span>' +
            (ext ? EXT : '') + (i[3] ? '<span class="sa-count" data-count="' + i[3] + '" hidden></span>' : '') + '</a>';
        }).join('') + '</div></div>';
    }).join('');
    $$('.sa-group-btn').forEach(function (b) {
      b.onclick = function () {
        var g = b.parentNode; g.classList.toggle('closed');
        b.setAttribute('aria-expanded', !g.classList.contains('closed'));
        closed[g.getAttribute('data-g')] = g.classList.contains('closed');
        store('sa_nav_closed', JSON.stringify(closed));
      };
    });
    $$('.sa-item[data-route]').forEach(function (a) { a.addEventListener('click', function () { document.body.classList.remove('sa-nav-open'); }); });
  }
  function setCount(key, n) {
    $$('[data-count="' + key + '"]').forEach(function (el) {
      el.textContent = n > 99 ? '99+' : String(n); el.hidden = !n;
      var a = el.closest('.sa-item'); if (a) a.setAttribute('aria-label', a.getAttribute('title').replace(/ \(.*$/, '') + (n ? ' — ' + n + ' waiting' : ''));
    });
  }
  var CRUMB_LINK = { 'Searches': '#/ops/searches', 'Organizations': '#/organizations', 'Users': '#/users', 'Support': '#/support', 'LeadAI Operations': '#/ops/searches' };
  /** setTitle(title, 'Parent › Child') — top-bar title + breadcrumb trail (Super Admin › … › current). */
  function setTitle(title, crumb) {
    $('#saTitle').textContent = title;
    var parts = String(crumb || '').split(' › ').filter(Boolean);
    if (!parts.length || parts[parts.length - 1] !== title) parts.push(title);
    var trail = [['Super Admin', '#/dashboard']].concat(parts.map(function (p, i) { return [p, i < parts.length - 1 ? CRUMB_LINK[p] : null]; }));
    $('#saCrumb').innerHTML = '<ol>' + trail.map(function (t, i) {
      var last = i === trail.length - 1;
      return '<li>' + (last ? '<span aria-current="page">' + esc(t[0]) + '</span>' : t[1] ? '<a href="' + esc(t[1]) + '">' + esc(t[0]) + '</a>' : '<span>' + esc(t[0]) + '</span>') + '</li>';
    }).join('') + '</ol>';
    document.title = title + ' · Super Admin · LeadAI';
  }
  function parseHash() {
    var h = location.hash.replace(/^#\/?/, '');
    var parts = h.split('?');
    var path = decodeURIComponent(parts[0] || 'dashboard').replace(/\/+$/, '') || 'dashboard';
    var query = {};
    new URLSearchParams(parts[1] || '').forEach(function (v, k) { query[k] = v; });
    var seg = path.split('/');
    var key = seg[0], param = seg.slice(1).join('/');
    if (ROUTES[seg[0] + '/' + seg[1]]) { key = seg[0] + '/' + seg[1]; param = seg.slice(2).join('/'); }
    return { key: key, param: param, query: query };
  }
  async function route(silent) {
    S.timers.forEach(clearInterval); S.timers = [];
    $$('.sa-pop').forEach(function (p) { p.hidden = true; });
    var r = parseHash();
    var def = ROUTES[r.key];
    $$('.sa-item[data-route]').forEach(function (a) { var on = a.getAttribute('data-route') === r.key; a.classList.toggle('active', on); if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
    var view = $('#view');
    var box = document.createElement('div');
    if (!def || (r.param && !def[2]) || (!r.param && !def[0])) {
      setTitle('Not found', '');
      view.innerHTML = '';
      box.innerHTML = '<div class="sa-state">' + stateIcon('missing') + '<h4>404 — page not found</h4><p>The route <span class="sa-mono">#/' + esc(location.hash.replace(/^#\/?/, '')) + '</span> does not exist in the Super Admin portal.</p><a class="btn btn-primary btn-sm" href="#/dashboard">Back to dashboard</a></div>';
      view.appendChild(box); return;
    }
    setTitle(def[1], (GROUP_OF[r.key] || '') + ' › ' + def[1]);
    if (!silent) { view.innerHTML = ''; box.innerHTML = skeleton(); view.appendChild(box); window.scrollTo(0, 0); }
    try {
      var target = silent ? document.createElement('div') : box;
      await (r.param ? def[2](target, r.query, r.param) : def[0](target, r.query));
      if (silent) { view.innerHTML = ''; view.appendChild(target); }
      bindGo(view); bindCharts(view);
    } catch (err) {
      if (!silent) box.innerHTML = errorState(err, function () { route(); });
      else toast(err.message, 'error');
    }
  }

  // ── top bar: search, bell, health, profile ────────────────
  function togglePop(btn, pop, open) {
    var show = open != null ? open : pop.hidden;
    $$('.sa-pop').forEach(function (p) { if (p !== pop && !p.hidden) { p.hidden = true; if (p._btn) p._btn.setAttribute('aria-expanded', 'false'); } });
    pop.hidden = !show; if (btn) { btn.setAttribute('aria-expanded', String(show)); pop._btn = btn; }
    if (!pop._keys) {
      pop._keys = true;
      pop.addEventListener('keydown', function (e) {
        var items = $$('.sa-pop-item,input[type=checkbox]', pop).filter(function (x) { return x.offsetParent !== null; });
        var i = items.indexOf(document.activeElement);
        if (e.key === 'ArrowDown') { e.preventDefault(); (items[i + 1] || items[0]).focus(); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); (items[i - 1] || items[items.length - 1]).focus(); }
        else if (e.key === 'Escape') { e.stopPropagation(); pop.hidden = true; if (pop._btn) { pop._btn.setAttribute('aria-expanded', 'false'); pop._btn.focus(); } }
        else if (e.key === 'Tab') { pop.hidden = true; if (pop._btn) pop._btn.setAttribute('aria-expanded', 'false'); }
      });
    }
  }
  function setHealthPill(overall) {
    S.health = overall;
    var cls = overall === 'ok' ? 'ok' : overall === 'warn' ? 'warn' : 'err';
    $('#saHealthDot').className = 'sa-dot ' + cls;
    $('#saHealthLbl').textContent = overall === 'ok' ? 'Healthy' : overall === 'warn' ? 'Attention' : overall === 'down' ? 'Down' : 'Degraded';
    $('#saHealth').setAttribute('aria-label', 'System health: ' + $('#saHealthLbl').textContent + ' — open details');
  }
  async function refreshHealth() { try { setHealthPill((await api('/api/super-admin/health')).health.overall); } catch (e) { setHealthPill('down'); } }
  async function refreshBell() {
    try {
      var d = await api('/api/super-admin/notifications?limit=8');
      var n = d.unread || 0;
      $('#saBellCount').textContent = n > 99 ? '99+' : n; $('#saBellCount').hidden = !n;
      setCount('notif', n);
      S.bell = d.items || [];
    } catch (e) { /* the view shows errors */ }
  }
  async function refreshCounts() {
    try { setCount('demo', (await api('/api/super-admin/demo-requests?status=pending&limit=1')).total || 0); } catch (e) {}
    try { setCount('queue', (await api('/api/super-admin/subscriptions/queue?limit=1')).total || 0); } catch (e) {}
    try { setCount('support', (await api('/api/super-admin/support/tickets?status=open&limit=1')).total || 0); } catch (e) {}
  }
  function renderBell() {
    var pop = $('#saBellPop'), items = S.bell || [];
    pop.innerHTML = '<div class="sa-pop-head"><span>Notifications</span><button type="button" class="sa-link" id="bellAll">Mark all read</button></div>' +
      (items.length ? items.map(function (n, i) {
        return '<button type="button" class="sa-pop-item" data-n="' + i + '"><span class="sa-dot ' + (n.read ? '' : n.severity === 'danger' ? 'err' : 'warn') + '" style="margin-top:6px" aria-hidden="true"></span><span style="min-width:0"><b>' + esc(n.title) + '</b><small>' + esc(short(n.message, 90)) + ' · ' + esc(ago(n.created_at)) + '</small></span></button>';
      }).join('') : '<div class="sa-state" style="padding:18px"><p>You are all caught up.</p></div>') +
      '<a class="sa-pop-item" href="#/notifications" style="justify-content:center;font-weight:600">Open notification center</a>';
    $$('[data-n]', pop).forEach(function (b) { b.onclick = async function () {
      var n = items[+b.getAttribute('data-n')];
      togglePop($('#saBell'), pop, false);
      try { await api('/api/super-admin/notifications/read', { method: 'POST', body: { id: n.id } }); } catch (e) {}
      refreshBell();
      if (n.type === 'support_ticket' && n.data && n.data.ticket_id) go('#/support/' + n.data.ticket_id);
      else if (n.link) { var l = internalLink(n.link); if (l.charAt(0) === '#') go(l); else location.href = l; }
      else go('#/notifications');
    }; });
    $('#bellAll', pop).onclick = async function () { await api('/api/super-admin/notifications/read', { method: 'POST', body: {} }); await refreshBell(); renderBell(); };
  }
  function wireSearch() {
    var inp = $('#saSearch'), pop = $('#saSearchPop'), t;
    try { new MutationObserver(function () { inp.setAttribute('aria-expanded', String(!pop.hidden)); }).observe(pop, { attributes: true, attributeFilter: ['hidden'] }); } catch (e) { /* old browser */ }
    var labels = { organizations: 'Organizations', users: 'Users', searches: 'Search runs', demo_requests: 'Demo requests' };
    inp.addEventListener('input', function () {
      clearTimeout(t);
      var q = inp.value.trim();
      if (q.length < 2) { pop.hidden = true; return; }
      t = setTimeout(async function () {
        try {
          var d = await api('/api/super-admin/search?q=' + encodeURIComponent(q));
          var groups = d.groups || {};
          var keys = Object.keys(groups);
          pop.innerHTML = keys.length ? keys.map(function (k) {
            return '<div class="sa-pop-head">' + esc(labels[k] || k) + '</div>' + groups[k].map(function (it) {
              return '<button type="button" class="sa-pop-item" role="option" data-k="' + esc(k) + '" data-id="' + esc(it.id) + '"><span style="min-width:0"><b>' + esc(it.label || it.id) + '</b><small>' + esc(it.sub || '') + '</small></span><span style="margin-left:auto">' + pill(it.status) + '</span></button>';
            }).join('');
          }).join('') : '<div class="sa-state" style="padding:18px"><p>No results for “' + esc(q) + '”</p></div>';
          pop.hidden = false;
          $$('[data-k]', pop).forEach(function (b) { b.onclick = function () {
            var k = b.getAttribute('data-k'), id = b.getAttribute('data-id');
            pop.hidden = true; inp.value = '';
            if (k === 'organizations') go('#/organizations/' + id);
            else if (k === 'users') go('#/users/' + id);
            else if (k === 'searches') chainDrawer(id);
            else demoDrawer(id);
          }; });
        } catch (e) { pop.innerHTML = '<div class="sa-state" style="padding:14px"><p>' + esc(e.message) + '</p></div>'; pop.hidden = false; }
      }, 250);
    });
    inp.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { pop.hidden = true; inp.blur(); }
      if (e.key === 'ArrowDown') { var f = $('.sa-pop-item', pop); if (f) { e.preventDefault(); f.focus(); } }
    });
    pop.addEventListener('keydown', function (e) {
      var items = $$('.sa-pop-item', pop), i = items.indexOf(document.activeElement);
      if (e.key === 'ArrowDown' && i < items.length - 1) { e.preventDefault(); items[i + 1].focus(); }
      if (e.key === 'ArrowUp') { e.preventDefault(); if (i > 0) items[i - 1].focus(); else inp.focus(); }
      if (e.key === 'Escape') { pop.hidden = true; inp.focus(); }
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === '/' && !/INPUT|TEXTAREA|SELECT/.test((document.activeElement || {}).tagName || '') && !$('.sa-overlay,.sa-drawer-wrap')) { e.preventDefault(); inp.focus(); }
    });
  }
  async function logout() {
    try { await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' }); } catch (e) {}
    location.href = '/login?superadmin=1';
  }
  async function loadMe() {
    try {
      var d = await api('/api/auth/me');
      var u = d.user || d;
      S.me = u;
      var name = u.name || u.email || 'Super Admin';
      $('#saMe').textContent = name; $('#saAvatar').textContent = name.charAt(0).toUpperCase();
      $('#saProfileEmail').textContent = u.email || 'Signed in';
      if (u.impersonated_by) {
        var b = $('#saImp');
        b.hidden = false;
        b.innerHTML = '<b>Impersonation active</b><span>Acting as ' + esc(u.organization_name || u.organization_id) + ' · reason: ' + esc(u.impersonation_reason || '—') + '</span><button type="button" class="btn btn-secondary btn-sm" id="impExit">Exit impersonation</button>';
        $('#impExit').onclick = async function () { try { await api('/api/super-admin/impersonate/exit', { method: 'POST' }); location.href = '/superadmin'; } catch (e) { toast(e.message, 'error'); } };
      }
    } catch (e) { /* 401 handled in api() */ }
  }

  function init() {
    renderNav();
    if (store('sa_collapsed') === '1') document.body.classList.add('sa-collapsed');
    $('#saCollapse').onclick = function () {
      var c = document.body.classList.toggle('sa-collapsed'); store('sa_collapsed', c ? '1' : '0');
      $('.sa-label', this).textContent = c ? 'Expand sidebar' : 'Collapse sidebar';
      this.setAttribute('aria-label', c ? 'Expand sidebar' : 'Collapse sidebar');
    };
    $('#saBurger').onclick = function () {
      var o = document.body.classList.toggle('sa-nav-open'); this.setAttribute('aria-expanded', String(o));
      if (o) { var a = $('.sa-item.active', $('#saSidebar')) || $('.sa-item', $('#saSidebar')); if (a) a.focus(); }
    };
    document.addEventListener('click', function (e) {
      if (document.body.classList.contains('sa-nav-open') && !e.target.closest('#saSidebar,#saBurger')) document.body.classList.remove('sa-nav-open');
      if (!e.target.closest('.sa-rel,.sa-search')) $$('.sa-pop').forEach(function (p) { p.hidden = true; });
    });
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape' || $('.sa-overlay,.sa-drawer-wrap')) return;
      $$('.sa-pop').forEach(function (p) { p.hidden = true; if (p._btn) p._btn.setAttribute('aria-expanded', 'false'); });
      if (document.body.classList.contains('sa-nav-open')) { document.body.classList.remove('sa-nav-open'); $('#saBurger').setAttribute('aria-expanded', 'false'); $('#saBurger').focus(); }
    });
    $('#saLogout').onclick = logout; $('#saLogout2').onclick = logout;
    $('#saProfile').onclick = function (e) { e.stopPropagation(); var pp = $('#saProfilePop'); togglePop(this, pp); if (!pp.hidden) $('.sa-pop-item', pp).focus(); };
    $('#saBell').onclick = function (e) { e.stopPropagation(); renderBell(); var bp = $('#saBellPop'); togglePop(this, bp); if (!bp.hidden) { var f = $('.sa-pop-item', bp); if (f) f.focus(); } };
    $('#saHealth').onclick = function () { go('#/health'); };
    wireSearch();
    window.addEventListener('hashchange', function () {
      document.body.classList.remove('sa-nav-open'); $('#saBurger').setAttribute('aria-expanded', 'false');
      route().then(function () { var h = $('#view h1'); if (h && !$('.sa-overlay,.sa-drawer-wrap') && !(document.activeElement || document.body).closest('#view')) h.focus({ preventScroll: true }); });
    });
    loadMe();
    route();
    refreshHealth(); refreshBell(); refreshCounts();
    setInterval(function () { if (!document.hidden) { refreshBell(); refreshCounts(); refreshHealth(); } }, 60000);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
