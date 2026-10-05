/* ============================================================
   LeadAI — Partner Portal (/partner)
   Vanilla JS, hash routing, org-admin shell styles.
   Every value is escaped; the backend enforces every permission
   (navigation filtering here is cosmetic).
   ============================================================ */
(function () {
  'use strict';

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var S = { me: null, view: null };

  // ── formatting ────────────────────────────────────────────
  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function money(v, cur) {
    var n = Number(v || 0);
    try { return new Intl.NumberFormat(undefined, { style: 'currency', currency: cur || 'USD', maximumFractionDigits: 2 }).format(n); }
    catch (e) { return n.toFixed(2) + ' ' + (cur || ''); }
  }
  function num(v) { return Number(v || 0).toLocaleString(); }
  function date(v) { if (!v) return '—'; var d = new Date(v); return isNaN(d) ? '—' : d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }); }
  function dt(v) { if (!v) return '—'; var d = new Date(v); return isNaN(d) ? '—' : d.toLocaleString(undefined, { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' }); }
  function title(s) { return String(s || '').replace(/[_.]/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); }); }
  var TONE = { active: 'success', approved: 'info', paid: 'success', customer: 'success', published: 'success', payable: 'success',
    pending: 'warning', requested: 'warning', changes_requested: 'warning', under_review: 'warning', qualified: 'info',
    processing: 'brand', signed_up: 'info', demo: 'brand', subscription: 'brand', payment: 'brand',
    suspended: 'suspended', rejected: 'danger', reversed: 'danger', failed: 'danger', churned: 'neutral',
    refund: 'danger', chargeback: 'danger', cancelled: 'neutral', archived: 'neutral', disabled: 'neutral' };
  Object.assign(TONE, { registered: 'warning', won: 'success', lost: 'neutral', expired: 'neutral' });
  var STAGE_LABEL = { signed_up: 'Signed up', demo: 'Demo approved', subscription: 'Checkout started', payment: 'Payment received', customer: 'Customer' };
  // Self-serve free trial (public config): website sign-ups start a trial at once,
  // so the referral "demo" stage is a started trial. Set by loadTrial() before any view renders.
  function trialOn() { return !!(S.trial && S.trial.self_serve); }
  function trialName() { var d = S.trial && S.trial.days; return (d > 0 ? d + '-day ' : '') + 'free trial'; }
  function trialsLabel() { return trialOn() ? 'trials started' : 'demos approved'; }
  async function loadTrial() {
    try { var r = await fetch('/api/public/config', { credentials: 'same-origin', headers: { Accept: 'application/json' } }); S.trial = r.ok ? ((await r.json()).trial || {}) : {}; }
    catch (e) { S.trial = {}; }
    STAGE_LABEL.demo = trialOn() ? 'Free trial' : 'Demo approved';
  }
  function pill(s, label) { return s ? '<span class="oa-pill" data-tone="' + (TONE[s] || 'neutral') + '">' + esc(label || title(s)) + '</span>' : '<span class="oa-muted">—</span>'; }
  function iso(d) { return d.toISOString().slice(0, 10); }

  // ── API ───────────────────────────────────────────────────
  function qs(o) {
    var p = new URLSearchParams();
    Object.keys(o || {}).forEach(function (k) { if (o[k] !== '' && o[k] != null) p.set(k, o[k]); });
    var s = p.toString(); return s ? '?' + s : '';
  }
  async function api(path, opts) {
    opts = opts || {};
    var init = { method: opts.method || 'GET', credentials: 'same-origin', headers: { Accept: 'application/json' } };
    if (opts.body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.body); }
    var res;
    try { res = await fetch(path, init); } catch (e) { var ne = new Error('Network error — check your connection and try again.'); ne.status = 0; throw ne; }
    var data = {};
    try { data = await res.json(); } catch (e) { data = {}; }
    if (res.status === 401) { location.href = '/login?partner=1'; var ue = new Error('Your session expired. Redirecting to sign in…'); ue.status = 401; throw ue; }
    if (!res.ok) {
      var d = data.detail, msg = typeof d === 'string' ? d : (d && d.message) || (Array.isArray(d) && d[0] && d[0].msg) || ('Request failed (' + res.status + ')');
      var err = new Error(msg); err.status = res.status; err.code = d && d.code; err.data = data; throw err;
    }
    return data;
  }

  // ── UI primitives ─────────────────────────────────────────
  function toast(msg, type) {
    var el = document.createElement('div');
    el.className = 'oa-toast ' + (type || 'success'); el.setAttribute('role', type === 'error' ? 'alert' : 'status');
    el.textContent = msg; $('#oa-toasts').appendChild(el);
    setTimeout(function () { el.remove(); }, type === 'error' ? 7000 : 4000);
  }
  function loading() { return '<div class="oa-card" aria-busy="true"><div class="oa-skel oa-skel-line" style="width:40%"></div><div class="oa-skel oa-skel-line"></div><div class="oa-skel oa-skel-line"></div><div class="oa-skel oa-skel-line" style="width:70%"></div></div>'; }
  function empty(t, d, action) { return '<div class="oa-state"><div class="oa-state-icon brand" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-6l-2 3h-4l-2-3H2"/></svg></div><h3>' + esc(t) + '</h3>' + (d ? '<p>' + esc(d) + '</p>' : '') + (action || '') + '</div>'; }
  function errorBox(err) {
    var denied = err && err.status === 403;
    return '<div class="oa-state" role="alert"><div class="oa-state-icon ' + (denied ? 'danger' : 'warning') + '" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg></div><h3>' +
      (denied ? 'Not available for your account' : 'Could not load') + '</h3><p>' + esc(err && err.message) + '</p>' +
      (denied ? '' : '<div class="oa-actions"><button type="button" class="btn btn-primary btn-sm" data-retry>Try again</button></div>') + '</div>';
  }
  function head(t, d, actions) {
    return '<div class="oa-head"><div class="oa-head-text"><h1 tabindex="-1">' + esc(t) + '</h1>' + (d ? '<p>' + esc(d) + '</p>' : '') + '</div>' + (actions ? '<div class="oa-actions">' + actions + '</div>' : '') + '</div>';
  }
  /** stat box; with `href` (from go()) the whole box is a link to that page — `meta` must not contain links then */
  function stat(label, value, meta, tone, href) {
    var inner = '<div class="oa-stat-label">' + esc(label) + (href ? '<span class="pp-stat-go" aria-hidden="true">→</span>' : '') + '</div><div class="oa-stat-value' + (tone ? ' ' + tone : '') + '">' + esc(value) + '</div>' + (meta ? '<div class="oa-stat-meta">' + meta + '</div>' : '');
    return href ? '<a class="oa-card oa-stat pp-stat-link" href="' + esc(href) + '">' + inner + '</a>' : '<div class="oa-card oa-stat">' + inner + '</div>';
  }
  /** '#/route?query' when the partner may open that route, else '' (the box then stays non-clickable) */
  function go(key, query) {
    var r = ROUTES.filter(function (x) { return x[0] === key; })[0];
    return r && allowed(r) ? '#/' + key + qs(query) : '';
  }
  /** query string of the current hash route: '#/commissions?status=pending' -> {status: 'pending'} */
  function hashQuery() {
    var i = location.hash.indexOf('?'), o = {};
    if (i >= 0) new URLSearchParams(location.hash.slice(i + 1)).forEach(function (v, k) { o[k] = v; });
    return o;
  }
  function only(o, keys) { var r = {}; keys.forEach(function (k) { if (o && o[k]) r[k] = o[k]; }); return r; }
  function card(t, body, sub, actions) {
    return '<section class="oa-card"><div class="oa-card-head"><div><div class="oa-card-title">' + esc(t) + '</div>' + (sub ? '<div class="oa-card-sub">' + esc(sub) + '</div>' : '') + '</div>' + (actions || '') + '</div>' + body + '</section>';
  }
  function kv(rows) { return '<dl class="oa-kv">' + rows.map(function (r) { return '<dt>' + esc(r[0]) + '</dt><dd>' + (r[2] ? r[1] : esc(r[1] == null || r[1] === '' ? '—' : r[1])) + '</dd>'; }).join('') + '</dl>'; }
  async function busy(btn, fn) {
    if (btn) { btn.disabled = true; btn.setAttribute('aria-busy', 'true'); }
    try { return await fn(); } finally { if (btn && btn.isConnected) { btn.disabled = false; btn.removeAttribute('aria-busy'); } }
  }
  function copyText(text, btn) {
    var done = function () { toast('Copied to clipboard'); if (btn) { var t = btn.textContent; btn.textContent = 'Copied'; setTimeout(function () { btn.textContent = t; }, 1500); } };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, function () { toast('Copy failed — select and copy manually', 'error'); });
    else toast('Copy is not supported in this browser', 'error');
  }
  // links are absolute when PUBLIC_BASE_URL is set; otherwise use this origin
  function abs(url) { return url && url.charAt(0) === '/' ? location.origin + url : url; }
  function linkRow(url, label) {
    url = abs(url);
    return '<div class="oa-field"><label>' + esc(label) + '</label><div class="pp-link"><input class="form-input" readonly value="' + esc(url) + '" aria-label="' + esc(label) + '"><button type="button" class="btn btn-secondary btn-sm" data-copy="' + esc(url) + '">Copy</button></div></div>';
  }
  function bindCopy(root) { $$('[data-copy]', root).forEach(function (b) { b.onclick = function () { copyText(b.getAttribute('data-copy'), b); }; }); }

  /** modal({title, body, submit, danger, wide, onSubmit(form) -> truthy closes}) */
  function modal(o) {
    return new Promise(function (resolve) {
      var root = $('#oa-modal-root'), prev = document.activeElement, id = 'm' + Math.random().toString(36).slice(2, 8);
      var back = document.createElement('div'); back.className = 'oa-modal-back';
      back.innerHTML = '<form class="oa-modal ' + (o.wide ? 'wide' : '') + '" role="dialog" aria-modal="true" aria-labelledby="' + id + '" novalidate>' +
        '<div class="oa-modal-head"><h2 id="' + id + '">' + esc(o.title) + '</h2><button type="button" class="modal-close" data-close aria-label="Close">✕</button></div>' +
        '<div class="oa-modal-body">' + (o.body || '') + '<p class="oa-err" data-err role="alert"></p></div>' +
        '<div class="oa-modal-foot"><button type="button" class="btn btn-secondary btn-sm" data-close>Cancel</button>' +
        (o.submit === null ? '' : '<button type="submit" class="btn btn-sm ' + (o.danger ? 'btn-danger' : 'btn-primary') + '" data-ok>' + esc(o.submit || 'Save') + '</button>') + '</div></form>';
      root.appendChild(back);
      var form = $('form', back);
      function close(v) { back.remove(); document.removeEventListener('keydown', key); if (prev && prev.focus) prev.focus(); resolve(v); }
      function key(e) { if (e.key === 'Escape') close(null); }
      document.addEventListener('keydown', key);
      $$('[data-close]', back).forEach(function (b) { b.onclick = function () { close(null); }; });
      back.addEventListener('mousedown', function (e) { if (e.target === back) close(null); });
      form.onsubmit = async function (e) {
        e.preventDefault();
        var errEl = $('[data-err]', form); errEl.textContent = '';
        try {
          var r = await busy($('[data-ok]', form), function () { return o.onSubmit ? o.onSubmit(form) : true; });
          if (r !== false) close(r === undefined ? true : r);
        } catch (err) { errEl.textContent = err.message || String(err); }
      };
      if (o.onOpen) o.onOpen(form);
      var first = $('input:not([readonly]),select,textarea', form); if (first) first.focus();
    });
  }
  function field(name, label, value, o) {
    o = o || {};
    var id = 'f_' + name + Math.random().toString(36).slice(2, 5);
    var input;
    if (o.type === 'textarea') input = '<textarea class="form-input form-textarea" id="' + id + '" name="' + name + '" rows="' + (o.rows || 3) + '" maxlength="' + (o.max || 2000) + '">' + esc(value) + '</textarea>';
    else if (o.options) input = '<select class="form-select" id="' + id + '" name="' + name + '">' + o.options.map(function (op) { var v = Array.isArray(op) ? op[0] : op, l = Array.isArray(op) ? op[1] : title(op); return '<option value="' + esc(v) + '"' + (String(v) === String(value) ? ' selected' : '') + '>' + esc(l) + '</option>'; }).join('') + '</select>';
    else input = '<input class="form-input" id="' + id + '" name="' + name + '" type="' + (o.type || 'text') + '" value="' + esc(value == null ? '' : value) + '"' + (o.max ? ' maxlength="' + o.max + '"' : '') + (o.min != null ? ' min="' + o.min + '"' : '') + (o.step ? ' step="' + o.step + '"' : '') + (o.placeholder ? ' placeholder="' + esc(o.placeholder) + '"' : '') + (o.readonly ? ' readonly' : '') + ' autocomplete="off">';
    return '<div class="oa-field' + (o.span ? ' span-2' : '') + '"><label for="' + id + '">' + esc(label) + '</label>' + input + (o.hint ? '<span class="oa-small oa-muted">' + esc(o.hint) + '</span>' : '') + '</div>';
  }
  function formData(form) { var o = {}; new FormData(form).forEach(function (v, k) { o[k] = typeof v === 'string' ? v.trim() : v; }); return o; }

  // ── date range ────────────────────────────────────────────
  function defaultRange() { var t = new Date(), f = new Date(Date.now() - 29 * 864e5); return { from: iso(f), to: iso(t) }; }
  function rangeControl(r) {
    return '<div class="pp-range" role="group" aria-label="Date range"><label class="sr-only" for="rgF">From</label><input type="date" class="form-input" id="rgF" value="' + esc(r.from) + '" max="' + iso(new Date()) + '"><span class="oa-muted">→</span><label class="sr-only" for="rgT">To</label><input type="date" class="form-input" id="rgT" value="' + esc(r.to) + '" max="' + iso(new Date()) + '">' +
      '<div class="oa-seg" role="group" aria-label="Quick ranges"><button type="button" data-days="7">7d</button><button type="button" data-days="30">30d</button><button type="button" data-days="90">90d</button></div>' +
      '<button type="button" class="btn btn-secondary btn-sm" data-refresh aria-label="Refresh">↻ Refresh</button></div>';
  }
  function bindRange(root, r, reload) {
    var f = $('#rgF', root), t = $('#rgT', root);
    function apply() { if (f.value && t.value && f.value <= t.value) { r.from = f.value; r.to = t.value; reload(); } else toast('Choose a valid date range', 'error'); }
    f.onchange = apply; t.onchange = apply;
    $$('[data-days]', root).forEach(function (b) { b.onclick = function () { var d = +b.getAttribute('data-days'); r.to = iso(new Date()); r.from = iso(new Date(Date.now() - (d - 1) * 864e5)); f.value = r.from; t.value = r.to; reload(); }; });
    $('[data-refresh]', root).onclick = reload;
  }

  // ── paged table ───────────────────────────────────────────
  /** table(el, {url(st), rows(data), columns:[{label, render(row), cls}], filters:[{key,type,label,options}], empty:{title,desc}, onData, bindRow}) */
  function table(el, cfg) {
    var st = Object.assign({ page: 1, limit: 25 }, cfg.initial || {});
    var fid = 't' + Math.random().toString(36).slice(2, 6);
    var filters = (cfg.filters || []).map(function (f) {
      var id = fid + f.key;
      if (f.type === 'select') return '<label class="sr-only" for="' + id + '">' + esc(f.label) + '</label><select class="form-select" id="' + id + '" data-f="' + f.key + '">' + f.options.map(function (o) { return '<option value="' + esc(o[0]) + '"' + (String(st[f.key] || '') === String(o[0]) ? ' selected' : '') + '>' + esc(o[1]) + '</option>'; }).join('') + '</select>';
      if (f.type === 'date') return '<label class="oa-datef"><span class="oa-datef-l">' + esc(f.label) + '</span><input type="date" class="form-input" data-f="' + f.key + '" value="' + esc(st[f.key] || '') + '"></label>';
      return '<label class="sr-only" for="' + id + '">' + esc(f.label) + '</label><input class="form-input oa-grow" type="search" id="' + id + '" data-f="' + f.key + '" placeholder="' + esc(f.label) + '" value="' + esc(st[f.key] || '') + '">';
    }).join('');
    el.innerHTML = '<div class="oa-toolbar"><div class="oa-toolbar-filters">' + filters + '</div><div class="oa-toolbar-tools"><button type="button" class="btn btn-secondary btn-sm" data-reload>↻ Refresh</button></div></div><div data-body></div>';
    var body = $('[data-body]', el), seq = 0, timer;
    $$('[data-f]', el).forEach(function (inp) {
      var ev = inp.type === 'search' ? 'input' : 'change';
      inp.addEventListener(ev, function () { clearTimeout(timer); timer = setTimeout(function () { st[inp.getAttribute('data-f')] = inp.value; st.page = 1; load(); }, inp.type === 'search' ? 300 : 0); });
    });
    $('[data-reload]', el).onclick = function () { load(); };
    async function load() {
      var my = ++seq;
      if (!body.innerHTML) body.innerHTML = loading(); else body.style.opacity = '.55';
      try {
        var data = await api(cfg.url(st));
        if (my !== seq) return;
        body.style.opacity = '';
        if (cfg.onData) cfg.onData(data);
        var rows = cfg.rows ? cfg.rows(data) : (data.items || []);
        if (!rows.length) { body.innerHTML = empty((cfg.empty || {}).title || 'Nothing here yet', (cfg.empty || {}).desc); return; }
        var total = data.total != null ? data.total : rows.length, pages = data.pages || 1;
        body.innerHTML = '<div class="oa-table-wrap"><table class="oa-table"><thead><tr>' + cfg.columns.map(function (c) { return '<th scope="col"' + (c.cls ? ' class="' + c.cls + '"' : '') + '>' + esc(c.label) + '</th>'; }).join('') + '</tr></thead><tbody>' +
          rows.map(function (r, i) { return '<tr data-i="' + i + '">' + cfg.columns.map(function (c) { return '<td' + (c.cls ? ' class="' + c.cls + '"' : '') + '>' + c.render(r) + '</td>'; }).join('') + '</tr>'; }).join('') + '</tbody></table></div>' +
          (data.pages ? '<div class="oa-pager"><span>' + num(total) + ' total · page ' + st.page + ' of ' + pages + '</span><div class="oa-pager-btns"><button type="button" class="btn btn-secondary btn-sm" data-pg="-1"' + (st.page <= 1 ? ' disabled' : '') + '>← Prev</button><button type="button" class="btn btn-secondary btn-sm" data-pg="1"' + (st.page >= pages ? ' disabled' : '') + '>Next →</button></div></div>' : '');
        $$('[data-pg]', body).forEach(function (b) { b.onclick = function () { st.page += +b.getAttribute('data-pg'); load(); }; });
        if (cfg.bindRow) $$('tbody tr', body).forEach(function (tr) { cfg.bindRow(tr, rows[+tr.getAttribute('data-i')], load); });
        bindCopy(body);
      } catch (err) {
        if (my !== seq) return;
        body.style.opacity = '';
        body.innerHTML = errorBox(err);
        var rb = $('[data-retry]', body); if (rb) rb.onclick = load;
      }
    }
    load();
    return { reload: load, state: st };
  }

  // ════════════════════════════════════════════════════════
  //  VIEWS
  // ════════════════════════════════════════════════════════
  function has(p) { return S.me && S.me.permissions.indexOf(p) >= 0; }
  function balancesOf(b) { var keys = Object.keys(b || {}); return keys.length ? keys : ['USD']; }

  async function viewApplication(root) {
    var a = (await api('/api/partner/v1/application')).application;
    var editable = a.status === 'changes_requested';
    var status = { pending: 'Your application is being reviewed. We\'ll email you as soon as there\'s a decision.',
      changes_requested: 'Our team asked for changes. Update your application below and resubmit it.',
      rejected: 'Your application was not approved.', approved: 'Your application is approved.' }[a.status] || '';
    root.innerHTML = head('Your partner application', status) +
      (a.review_note && (a.status === 'changes_requested' || a.status === 'rejected') ? '<div class="pp-banner"><b>Note from the LeadAI team:</b> ' + esc(a.review_note) + '</div>' : '') +
      '<div class="oa-grid oa-grid-2">' +
      card('Status', kv([['Status', pill(a.status), true], ['Submitted', date(a.created_at)], ['Partner type', title(a.partner_type)], ['Last update', dt(a.updated_at)]])) +
      card('History', '<ul class="oa-feed">' + (a.history || []).slice().reverse().map(function (h) { return '<li><span class="oa-feed-main">' + pill(h.status) + (h.note ? ' <span class="oa-small">' + esc(h.note) + '</span>' : '') + '</span><time>' + esc(dt(h.at)) + '</time></li>'; }).join('') + '</ul>') +
      '</div>' + (editable ? '<form class="oa-card" id="appForm" novalidate><div class="oa-card-head"><div class="oa-card-title">Update and resubmit</div></div><div class="oa-form-grid">' +
        field('name', 'Full name', a.name, { max: 120 }) + field('company', 'Company', a.company, { max: 160 }) +
        field('phone', 'Phone', a.phone, { max: 30 }) + field('website', 'Website', a.website, { max: 300, placeholder: 'https://' }) +
        field('country', 'Country', a.country, { max: 80 }) + field('city', 'City', a.city, { max: 80 }) +
        field('partner_type', 'Partner type', a.partner_type, { options: [['affiliate', 'Affiliate'], ['reseller', 'Reseller']] }) +
        field('business_type', 'Business type', a.business_type, { max: 80 }) +
        field('experience', 'Experience', a.experience, { type: 'textarea', span: true }) +
        field('promotion_plan', 'How you will promote LeadAI', a.promotion_plan, { type: 'textarea', span: true, max: 3000 }) +
        '</div><p class="oa-err" id="appErr" role="alert"></p><div class="oa-actions" style="margin-top:12px"><button class="btn btn-primary btn-sm" type="submit">Resubmit application</button></div></form>' : '');
    var form = $('#appForm', root);
    if (form) form.onsubmit = async function (e) {
      e.preventDefault(); $('#appErr', root).textContent = '';
      var body = formData(form);
      try { await busy($('button[type=submit]', form), function () { return api('/api/partner/v1/application', { method: 'PUT', body: body }); }); toast('Application resubmitted'); route(); }
      catch (err) { $('#appErr', root).textContent = err.message; }
    };
  }

  async function viewDashboard(root, q, ctx) {
    var r = ctx.range || (ctx.range = defaultRange());
    root.innerHTML = head('Dashboard', 'Your referral performance, customers and earnings.', rangeControl(r)) + '<div data-dash>' + loading() + '</div>';
    bindRange(root, r, function () { route(true); });
    var d = (await api('/api/partner/v1/dashboard' + qs({ from: r.from, to: r.to }))).dashboard;
    var k = d.kpis, cur = balancesOf(d.balances)[0], b = d.balances[cur] || {};
    var links = '';
    if (has('referral_links.create')) {
      try {
        var L = (await api('/api/partner/v1/referral-links')).links;
        links = card('Your referral link', linkRow(L.referral_url, 'Share this link') + '<p class="oa-small oa-muted" style="margin-top:8px">Referral code <b class="oa-mono">' + esc(L.referral_code) + '</b> · Partner ID <span class="oa-mono">' + esc(L.partner_code) + '</span></p>', '', '<a class="btn btn-secondary btn-sm" href="#/campaigns">Campaign links</a>');
      } catch (e) { links = ''; }
    }
    var list = function (items, fn, emptyText) { return items.length ? '<ul class="oa-feed">' + items.map(fn).join('') + '</ul>' : '<div class="oa-mini-empty">' + esc(emptyText) + '</div>'; };
    var nt = S.me.open_tasks || 0;
    $('[data-dash]', root).innerHTML = (nt ? '<div class="pp-banner" role="status">You have <b>' + nt + ' open task' + (nt === 1 ? '' : 's') + '</b> from the LeadAI team. <a class="oa-link" href="#/tasks">Open tasks →</a></div>' : '') + links +
      '<div class="oa-grid oa-grid-4">' +
      stat('Referral clicks', num(k.clicks), num(k.unique_clicks) + ' unique', '', go('analytics')) +
      stat('Referrals', num(k.referrals), num(k.demos) + ' ' + trialsLabel(), '', go('referrals', { from: r.from, to: r.to })) +
      stat('Customers', num(k.customers), num(k.total_customers) + ' total · ' + num(k.active_subscriptions) + ' active subs', '', go('customers', { stage: 'customer' })) +
      stat('Conversion', k.conversion_rate + '%', 'clicks → signups · ' + k.customer_rate + '% signups → customers', '', go('analytics')) +
      '</div><div class="oa-grid oa-grid-4">' +
      stat('Revenue generated', money(k.revenue, cur), 'in this period', '', go('analytics') || go('customers', { stage: 'customer' })) +
      stat('Pending commission', money(b.pending, cur), 'in hold period / review', '', go('commissions', { status: 'pending' })) +
      (go('wallet') ? stat('Available balance', money(b.available, cur), has('payouts.request') ? 'Request a payout from your wallet' : 'payable now', '', go('wallet'))
        : stat('Available balance', money(b.available, cur), 'payable now', '', go('commissions', { status: 'payable' }))) +
      stat('Paid out', money(b.paid, cur), num(k.pending_payout_count) + ' payout(s) in progress · ' + money(k.pending_payouts, cur), '', go('payouts', { status: 'paid' }) || go('commissions', { status: 'paid' })) +
      '</div><div class="oa-grid oa-grid-2">' +
      card('Recent referrals', list(d.recent_referrals, function (x) { return '<li><span class="oa-feed-main"><b>' + esc(x.company || 'New signup') + '</b> ' + pill(x.stage, STAGE_LABEL[x.stage]) + '</span><time>' + esc(date(x.signed_up_at)) + '</time></li>'; }, 'No referrals yet — share your link to get started.'), '', has('referrals.view') ? '<a class="oa-link" href="#/referrals">View all</a>' : '') +
      card('Recent customers', list(d.recent_customers, function (x) { return '<li><span class="oa-feed-main"><b>' + esc(x.company || '—') + '</b> <span class="oa-small oa-muted">' + esc(x.plan_id || '') + '</span></span><time>' + esc(date(x.converted_at)) + '</time></li>'; }, 'No paying customers yet.'), '', has('customers.view') ? '<a class="oa-link" href="#/customers">View all</a>' : '') +
      card('Recent commission activity', list(d.recent_commissions, function (x) { return '<li><span class="oa-feed-main"><span class="pp-money">' + esc(money(x.amount, x.currency)) + '</span> ' + esc(x.company || title(x.kind)) + ' ' + pill(x.status) + '</span><time>' + esc(date(x.created_at)) + '</time></li>'; }, 'No commissions yet.'), '', has('commissions.view') ? '<a class="oa-link" href="#/commissions">View all</a>' : '') +
      card('Recent notifications', list(d.recent_notifications || [], function (n) { return '<li' + (n.severity === 'danger' ? ' class="fail"' : '') + '><span class="oa-feed-main"><b>' + esc(n.title) + '</b> <span class="oa-small">' + esc(n.message) + '</span></span><time>' + esc(date(n.created_at)) + '</time></li>'; }, 'You are all caught up.'), '', '<a class="oa-link" href="#/notifications">View all</a>') +
      '</div>';
    bindCopy(root);
  }

  async function viewReferrals(root, q) {
    root.innerHTML = head('Referrals', 'Businesses that signed up through your links, campaigns, codes or coupons.') + '<div id="refT"></div>';
    var camps = [];
    try { if (has('campaigns.manage')) camps = (await api('/api/partner/v1/campaigns')).items || []; } catch (e) { camps = []; }
    table($('#refT', root), {
      initial: only(q, ['stage', 'q', 'campaign_id', 'from', 'to']),
      url: function (st) { return '/api/partner/v1/referrals' + qs({ page: st.page, limit: st.limit, stage: st.stage, q: st.q, campaign_id: st.campaign_id, from: st.from, to: st.to }); },
      filters: [{ key: 'q', label: 'Search company…' }, { key: 'stage', type: 'select', label: 'Stage', options: [['', 'All stages']].concat(Object.keys(STAGE_LABEL).map(function (k) { return [k, STAGE_LABEL[k]]; })) }]
        .concat(camps.length ? [{ key: 'campaign_id', type: 'select', label: 'Campaign', options: [['', 'All campaigns']].concat(camps.map(function (c) { return [c.id, c.name]; })) }] : [])
        .concat([{ key: 'from', type: 'date', label: 'From' }, { key: 'to', type: 'date', label: 'To' }]),
      columns: [
        { label: 'Company', render: function (r) { return '<button type="button" class="oa-link" data-ref style="background:none;border:0;padding:0;font:inherit;cursor:pointer"><b>' + esc(r.company || '—') + '</b></button><div class="oa-small oa-muted">' + esc(r.email || '') + '</div>'; } },
        { label: 'Source', render: function (r) { return esc(title(r.source)) + (r.managed ? ' <span class="oa-small oa-muted">(managed)</span>' : ''); } },
        { label: 'Stage', render: function (r) { return pill(r.stage, STAGE_LABEL[r.stage]) + (r.churned_at ? ' ' + pill('churned') : ''); } },
        { label: 'Signed up', render: function (r) { return esc(date(r.signed_up_at)); } },
        { label: 'Customer since', render: function (r) { return esc(date(r.converted_at)); } },
        { label: 'Revenue', cls: 'oa-num', render: function (r) { return '<span class="pp-money">' + esc(money(r.revenue_total)) + '</span>'; } },
        { label: 'Commission', cls: 'oa-num', render: function (r) { return '<span class="pp-money">' + esc(money(r.commission_total)) + '</span>'; } }
      ],
      bindRow: function (tr, r) { var b = $('[data-ref]', tr); if (b) b.onclick = function () { referralDetail(r.id); }; },
      empty: { title: 'No referrals yet', desc: 'Share your referral link — signups appear here as soon as they register.' }
    });
  }
  async function referralDetail(id) {
    var d;
    try { d = (await api('/api/partner/v1/referrals/' + encodeURIComponent(id))).referral; } catch (e) { toast(e.message, 'error'); return; }
    var hist = (d.events || []).map(function (e) { return '<li' + (/refund|chargeback|churned|invalidated/.test(e.stage) ? ' class="fail"' : '') + '><span class="oa-feed-main">' + pill(e.stage, STAGE_LABEL[e.stage]) + (e.amount ? ' <span class="pp-money">' + esc(money(e.amount)) + '</span>' : '') + (e.reason ? ' <span class="oa-small oa-muted">' + esc(e.reason) + '</span>' : '') + '</span><time>' + esc(dt(e.at)) + '</time></li>'; }).join('');
    var coms = (d.commissions || []).map(function (c) { return '<tr><td>' + esc(date(c.created_at)) + '</td><td>' + esc(title(c.kind === 'commission' ? c.event : c.kind)) + '</td><td class="oa-num pp-money">' + esc(money(c.amount, c.currency)) + '</td><td>' + pill(c.status) + '</td></tr>'; }).join('');
    await modal({ title: d.company || 'Referral', submit: null, wide: true,
      body: kv([['Stage', pill(d.stage, STAGE_LABEL[d.stage]), true], ['Source', title(d.source)], ['Signed up', dt(d.signed_up_at)], ['Customer since', d.converted_at ? dt(d.converted_at) : '—'], ['Revenue', money(d.revenue_total)], ['Commission', money(d.commission_total)]]) +
        '<h3 class="oa-card-title" style="margin:16px 0 6px">Conversion history</h3><ul class="oa-feed">' + (hist || '<li><span class="oa-feed-main oa-muted">No events</span></li>') + '</ul>' +
        (d.commissions ? '<h3 class="oa-card-title" style="margin:16px 0 6px">Commissions</h3>' + (coms ? '<div class="oa-table-wrap"><table class="oa-table"><thead><tr><th>Date</th><th>Type</th><th class="oa-num">Amount</th><th>Status</th></tr></thead><tbody>' + coms + '</tbody></table></div>' : '<div class="oa-mini-empty">No commissions yet</div>') : '') });
  }

  async function viewCustomers(root, q, ctx, param) {
    if (param) return viewCustomer(root, param);
    var canCreate = has('customers.create');
    root.innerHTML = head('Customers', canCreate ? 'Organizations you referred or onboarded. ' + (trialOn() ? 'New customers start a ' + trialName() + ' at once.' : 'New customers go through LeadAI\'s demo approval.') : 'Organizations attributed to you.',
      canCreate ? '<a class="btn btn-secondary btn-sm" href="#/campaigns">Onboarding links</a><button type="button" class="btn btn-primary btn-sm" id="newCust">+ New customer</button>' : '') + '<div id="custT"></div>';
    var t = table($('#custT', root), {
      initial: only(q, ['stage', 'q', 'managed']),
      url: function (st) { return '/api/partner/v1/customers' + qs({ page: st.page, limit: st.limit, q: st.q, stage: st.stage, managed: st.managed }); },
      filters: [{ key: 'q', label: 'Search company…' }, { key: 'stage', type: 'select', label: 'Stage', options: [['', 'All stages']].concat(Object.keys(STAGE_LABEL).map(function (k) { return [k, STAGE_LABEL[k]]; })) }, { key: 'managed', type: 'select', label: 'Type', options: [['', 'All customers'], ['true', 'Managed by me'], ['false', 'Referred']] }],
      columns: [
        { label: 'Company', render: function (r) { return '<a class="oa-link" href="#/customers/' + esc(r.organization_id) + '"><b>' + esc(r.company || (r.organization || {}).name || '—') + '</b></a>'; } },
        { label: 'Workspace', render: function (r) { return pill((r.organization || {}).status); } },
        { label: 'Subscription', render: function (r) { return r.subscription ? pill(r.subscription.status) + ' <span class="oa-small oa-muted">' + esc(r.subscription.plan_id || '') + '</span>' : '<span class="oa-muted">—</span>'; } },
        { label: 'Members', cls: 'oa-num', render: function (r) { return esc(num(r.members)); } },
        { label: 'Stage', render: function (r) { return pill(r.stage, STAGE_LABEL[r.stage]) + (r.managed ? ' <span class="oa-small oa-muted">managed</span>' : ''); } },
        { label: 'Revenue', cls: 'oa-num', render: function (r) { return '<span class="pp-money">' + esc(money(r.revenue_total)) + '</span>'; } }
      ],
      empty: { title: 'No customers yet', desc: canCreate ? 'Onboard your first customer with “New customer”.' : 'Customers you refer appear here.' }
    });
    var nb = $('#newCust', root);
    if (nb) nb.onclick = async function () {
      var ok = await modal({ title: 'New customer', submit: 'Create customer', wide: true,
        body: '<p class="oa-small oa-muted" style="margin-top:0">' + esc(onboardNote('The owner')) + '</p><div class="oa-form-grid">' +
          field('company', 'Company *', '', { max: 160 }) + field('name', 'Owner name *', '', { max: 120 }) + field('email', 'Owner email *', '', { type: 'email', max: 200 }) +
          field('phone', 'Phone', '', { max: 30 }) + field('notes', 'Notes for the LeadAI team', '', { type: 'textarea', span: true }) + '</div>',
        onSubmit: function (f) {
          var d = formData(f);
          if (!d.company || !d.name || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(d.email)) throw new Error('Company, owner name and a valid email are required.');
          return api('/api/partner/v1/customers', { method: 'POST', body: d });
        } });
      if (ok) { toast(createdMessage(ok)); t.reload(); }
    };
  }
  /** what happens when a partner onboards a customer (self-serve trial or demo approval) */
  function onboardNote(who) {
    return trialOn() ? 'LeadAI creates the workspace and its ' + trialName() + ' starts at once. ' + who + ' gets an email to set a password.'
      : 'LeadAI creates the workspace as a demo request. ' + who + ' gets an email to set a password; the workspace opens after LeadAI approves the demo.';
  }
  function createdMessage(res) {
    var st = res && res.customer && res.customer.status;
    if (st === 'approved') return 'Customer created — their ' + trialName() + ' has started. The owner gets an email to set a password.';
    if (st === 'pending') return 'Customer created — the owner gets an email to set a password; the workspace opens once LeadAI approves the demo.';
    return (res && res.message) || 'Customer created';
  }

  async function viewCustomer(root, orgId) {
    var c = (await api('/api/partner/v1/customers/' + encodeURIComponent(orgId))).customer;
    var org = c.organization || {}, sub = c.subscription;
    root.innerHTML = '<p><a class="oa-link" href="#/customers">← Customers</a></p>' + head(c.company || org.name || 'Customer', 'Attributed ' + date(c.signed_up_at) + ' via ' + title(c.source) + '.',
      (c.managed && has('reseller.customers.manage') && c.owner_activated === false) ? '<button type="button" class="btn btn-secondary btn-sm" id="resend">Resend setup email</button>' : '') +
      '<div class="oa-grid oa-grid-3">' +
      card('Workspace', kv([['Status', pill(org.status), true], ['Plan', org.plan_id], [trialOn() ? 'Trial ends' : 'Demo ends', date(org.demo_expires_at)], ['Members', num(c.members)], ['Created', date(org.created_at)]])) +
      card('Subscription', sub ? kv([['Status', pill(sub.status), true], ['Plan', sub.plan_id], ['Billing', title(sub.billing_cycle)], ['Renews', date(sub.current_period_end)]]) : '<div class="oa-mini-empty">No subscription yet</div>') +
      card('Your earnings', kv([['Stage', pill(c.stage, STAGE_LABEL[c.stage]), true], ['Revenue', money(c.revenue_total)], ['Commission', money(c.commission_total)], ['Customer since', date(c.converted_at)]]
        .concat(c.tokens ? [['Tokens left', num(c.tokens.balance != null ? c.tokens.balance : c.tokens.available)]] : [])
        .concat(c.managed ? [['Owner signed in', c.owner_activated ? 'Yes' : 'Not yet']] : []))) +
      '</div>' + card('Commissions from this customer', (c.commissions || []).length ? '<div class="oa-table-wrap"><table class="oa-table"><thead><tr><th>Date</th><th>Event</th><th class="oa-num">Payment</th><th class="oa-num">Commission</th><th>Status</th></tr></thead><tbody>' +
        c.commissions.map(function (x) { return '<tr><td>' + esc(date(x.created_at)) + '</td><td>' + esc(title(x.event)) + '</td><td class="oa-num pp-money">' + esc(money(x.base_amount, x.currency)) + '</td><td class="oa-num pp-money">' + esc(money(x.amount, x.currency)) + '</td><td>' + pill(x.status) + '</td></tr>'; }).join('') + '</tbody></table></div>' : '<div class="oa-mini-empty">No commissions yet — they are created when a payment is confirmed.</div>');
    var rb = $('#resend', root);
    if (rb) rb.onclick = function () { busy(rb, function () { return api('/api/partner/v1/customers/' + encodeURIComponent(orgId) + '/resend-setup', { method: 'POST' }); }).then(function () { toast('Setup email sent'); }, function (e) { toast(e.message, 'error'); }); };
  }

  async function viewCommissions(root, q) {
    root.innerHTML = head('Commissions', 'Created when a referred customer\'s payment is confirmed. Pending commissions become available after the hold period.') + '<div id="bal"></div><div id="comT"></div>';
    table($('#comT', root), {
      initial: only(q, ['status', 'from', 'to']),
      url: function (st) { return '/api/partner/v1/commissions' + qs({ page: st.page, limit: st.limit, status: st.status, from: st.from, to: st.to }); },
      onData: function (d) { var b = d.balances || {}; $('#bal', root).innerHTML = Object.keys(b).length ? '<div class="oa-grid oa-grid-4">' + Object.keys(b).map(function (c) { return stat('Pending · ' + c, money(b[c].pending, c), 'qualifying / awaiting approval', '', go('commissions', { status: 'pending' })) + stat('Available · ' + c, money(b[c].available, c), 'payable now', '', go('commissions', { status: 'payable' })) + stat('In payout · ' + c, money(b[c].processing, c), '', '', go('commissions', { status: 'processing' })) + stat('Paid · ' + c, money(b[c].paid, c), money(b[c].reversed, c) + ' reversed', '', go('commissions', { status: 'paid' })); }).join('') + '</div><div style="height:14px"></div>' : ''; },
      filters: [{ key: 'status', type: 'select', label: 'Status', options: [['', 'All statuses'], ['pending', 'Pending (qualifying)'], ['qualified', 'Qualified'], ['approved', 'Approved'], ['payable', 'Payable'], ['processing', 'In payout'], ['paid', 'Paid'], ['reversed', 'Reversed']] }, { key: 'from', type: 'date', label: 'From' }, { key: 'to', type: 'date', label: 'To' }],
      columns: [
        { label: 'Date', render: function (r) { return esc(date(r.created_at)); } },
        { label: 'Customer', render: function (r) { return esc(r.company || (r.kind === 'adjustment' ? 'Adjustment' : '—')) + (r.note ? '<div class="oa-small oa-muted">' + esc(r.note) + '</div>' : ''); } },
        { label: 'Event', render: function (r) { return esc(title(r.event)); } },
        { label: 'Rate', render: function (r) { return r.rate_type ? esc(r.rate_type === 'percentage' ? r.rate_value + '%' : r.rate_type === 'hybrid' ? r.rate_value + '% + ' + money(r.rate_fixed, r.currency) : money(r.rate_value, r.currency)) : '—'; } },
        { label: 'Payment', cls: 'oa-num', render: function (r) { return r.base_amount ? '<span class="pp-money">' + esc(money(r.base_amount, r.currency)) + '</span>' : '—'; } },
        { label: 'Commission', cls: 'oa-num', render: function (r) { return '<span class="pp-money">' + esc(money(r.amount, r.currency)) + '</span>' + (r.reversed_amount ? '<div class="oa-small oa-muted">' + esc(money(r.reversed_amount, r.currency)) + ' reversed</div>' : '') + (r.clawed_back ? '<div class="oa-small oa-muted">' + esc(money(r.clawed_back, r.currency)) + ' clawed back</div>' : ''); } },
        { label: 'Status', render: function (r) { return pill(r.status) + (r.status === 'pending' ? '<div class="oa-small oa-muted">qualifies ' + esc(date(r.hold_until)) + '</div>' : '') + (r.status === 'qualified' && r.requires_manual_approval ? '<div class="oa-small oa-muted">under review</div>' : '') + (r.chargeback ? ' ' + pill('chargeback') : '') + (r.reversal_reason ? '<div class="oa-small oa-muted">' + esc(r.reversal_reason) + '</div>' : ''); } }
      ],
      empty: { title: 'No commissions yet', desc: 'Commissions appear when customers you referred pay for a subscription.' }
    });
  }

  async function viewWallet(root) {
    var w = (await api('/api/partner/v1/wallet')).wallet;
    var curs = balancesOf(w.balances);
    var me = S.me.partner || {};
    var sched = (S.me.program || {}).payout_schedule || 'on_request';
    root.innerHTML = head('Wallet', 'Balances are calculated from your commissions — you can\'t edit them. Minimum payout: ' + money(w.min_payout, curs[0]) + '. ' + (sched === 'on_request' ? 'Approved commissions are payable right away.' : 'Approved commissions become payable on the ' + sched + ' payout day.'),
      has('payouts.request') ? '<button type="button" class="btn btn-primary btn-sm" id="reqPay">Request payout</button>' : '') +
      (!me.has_payout_info && has('payouts.request') ? '<div class="pp-banner">Add your payout details in <a class="oa-link" href="#/settings">Settings</a> before requesting a payout.</div>' : '') +
      curs.map(function (c) { var b = w.balances[c] || {}; return '<div class="oa-grid oa-grid-4">' + stat('Available · ' + c, money(b.available, c), 'payable — ready for payout', '', go('commissions', { status: 'payable' })) + stat('Pending · ' + c, money(b.pending, c), 'qualifying or awaiting approval', '', go('commissions', { status: 'pending' })) + stat('In payout · ' + c, money(b.processing, c), 'requested, not yet paid', '', go('payouts') || go('commissions', { status: 'processing' })) + stat('Paid · ' + c, money(b.paid, c), money(b.reversed, c) + ' reversed · ' + money(b.lifetime, c) + ' lifetime', '', go('payouts', { status: 'paid' }) || go('commissions', { status: 'paid' })) + '</div>'; }).join('<div style="height:14px"></div>') +
      card('Transactions', (w.transactions || []).length ? '<div class="oa-table-wrap"><table class="oa-table"><thead><tr><th>Date</th><th>Type</th><th>Customer</th><th class="oa-num">Amount</th><th class="oa-num">Available after</th><th>Note</th></tr></thead><tbody>' +
        w.transactions.map(function (t) { return '<tr><td>' + esc(dt(t.created_at)) + '</td><td>' + esc(title(t.type)) + '</td><td class="oa-small">' + esc(t.company || '—') + '</td><td class="oa-num pp-money">' + esc(money(t.amount, t.currency)) + '</td><td class="oa-num pp-money">' + esc(money(t.available_after, t.currency)) + '</td><td class="oa-small">' + esc(t.note || '') + '</td></tr>'; }).join('') + '</tbody></table></div>' : empty('No transactions yet', 'Every commission, approval, refund and payout is recorded here.'));
    var rb = $('#reqPay', root);
    if (rb) rb.onclick = async function () {
      var options = curs.filter(function (c) { return (w.balances[c] || {}).available > 0; });
      if (!options.length) { toast('No available balance to withdraw yet', 'error'); return; }
      var ok = await modal({ title: 'Request payout', submit: 'Request payout',
        body: '<p style="margin-top:0">Your whole available balance in the selected currency is paid out to your saved payout method.</p>' + field('currency', 'Currency', options[0], { options: options.map(function (c) { return [c, c + ' — ' + money(w.balances[c].available, c)]; }) }),
        onSubmit: function (f) { return api('/api/partner/v1/payouts', { method: 'POST', body: formData(f) }); } });
      if (ok) { toast('Payout requested'); location.hash = '#/payouts'; }
    };
  }

  async function viewPayouts(root, q) {
    root.innerHTML = head('Payouts', 'Your payout requests and their status.', has('payouts.request') ? '<a class="btn btn-secondary btn-sm" href="#/wallet">Wallet</a>' : '') + '<div id="poT"></div>';
    table($('#poT', root), {
      initial: only(q, ['status']),
      url: function (st) { return '/api/partner/v1/payouts' + qs({ page: st.page, limit: st.limit, status: st.status }); },
      filters: [{ key: 'status', type: 'select', label: 'Status', options: [['', 'All statuses'], ['requested', 'Requested'], ['under_review', 'Under review'], ['approved', 'Approved'], ['processing', 'Processing'], ['paid', 'Paid'], ['failed', 'Failed'], ['rejected', 'Rejected'], ['cancelled', 'Cancelled']] }],
      columns: [
        { label: 'Requested', render: function (r) { return esc(dt(r.requested_at)); } },
        { label: 'Amount', cls: 'oa-num', render: function (r) { return '<span class="pp-money">' + esc(money(r.amount, r.currency)) + '</span>'; } },
        { label: 'Method', render: function (r) { return esc(title(r.method)); } },
        { label: 'Commissions', cls: 'oa-num', render: function (r) { return esc((r.commission_ids || []).length); } },
        { label: 'Status', render: function (r) { return pill(r.status) + (r.reference ? '<div class="oa-small oa-muted">Ref ' + esc(r.reference) + '</div>' : '') + (r.failure_reason ? '<div class="oa-small oa-muted">' + esc(r.failure_reason) + '</div>' : ''); } },
        { label: '', cls: 'oa-num', render: function (r) { return r.status === 'requested' && has('payouts.request') ? '<button type="button" class="btn btn-ghost btn-xs" data-cancel>Cancel</button>' : ''; } }
      ],
      bindRow: function (tr, r, reload) { var b = $('[data-cancel]', tr); if (b) b.onclick = function () { busy(b, function () { return api('/api/partner/v1/payouts/' + r.id + '/cancel', { method: 'POST' }); }).then(function () { toast('Payout cancelled'); reload(); }, function (e) { toast(e.message, 'error'); }); }; },
      empty: { title: 'No payouts yet', desc: 'Request a payout from your wallet once your available balance reaches the minimum.' }
    });
  }

  function bars(values, labels, money_, cur) {
    if (!values.some(function (v) { return v > 0; })) return '<div class="oa-mini-empty">No data in this period.</div>';
    var max = Math.max.apply(null, values.concat([0])) || 1;
    return '<div class="pp-bars" role="img" aria-label="Chart">' + values.map(function (v, i) { return '<span style="height:' + (v > 0 ? Math.max(2, v * 100 / max) : 0) + '%" title="' + esc(labels[i] + ': ' + (money_ ? money(v, cur) : num(v))) + '"></span>'; }).join('') + '</div><div class="pp-axis"><span>' + esc(labels[0] || '') + '</span><span>' + esc(labels[labels.length - 1] || '') + '</span></div>';
  }
  async function viewAnalytics(root, q, ctx) {
    var r = ctx.range || (ctx.range = defaultRange());
    ctx.unit = ctx.unit || 'day';
    var camps = [];
    try { if (has('campaigns.manage')) camps = (await api('/api/partner/v1/campaigns')).items || []; } catch (e) { camps = []; }
    var campSel = camps.length ? '<label class="sr-only" for="anCamp">Campaign</label><select class="form-select" id="anCamp" style="width:auto;height:36px;padding:6px 10px"><option value="">All campaigns</option>' + camps.map(function (c) { return '<option value="' + esc(c.id) + '"' + (ctx.campaign === c.id ? ' selected' : '') + '>' + esc(c.name) + '</option>'; }).join('') + '</select>' : '';
    var params = { from: r.from, to: r.to, unit: ctx.unit, campaign_id: ctx.campaign || '' };
    root.innerHTML = head('Analytics', 'Clicks, visitors, signups, demos, customers, revenue and commission — from your real referral and billing records.',
      campSel + rangeControl(r) + '<a class="btn btn-secondary btn-sm" id="anCsv" href="/api/partner/v1/analytics.csv' + esc(qs(params)) + '" download>⤓ Export CSV</a>') + '<div data-an>' + loading() + '</div>';
    bindRange(root, r, function () { route(true); });
    var cs = $('#anCamp', root); if (cs) cs.onchange = function () { ctx.campaign = cs.value; route(true); };
    var a = (await api('/api/partner/v1/analytics' + qs(params))).analytics;
    var t = a.totals;
    var seg = '<div class="oa-seg" role="group" aria-label="Group by">' + ['day', 'week', 'month'].map(function (u) { return '<button type="button" data-unit="' + u + '" aria-pressed="' + (a.unit === u) + '">' + title(u) + '</button>'; }).join('') + '</div>';
    var maxF = Math.max.apply(null, a.funnel.map(function (f) { return f.count; }).concat([1]));
    var curs = Object.keys(a.commission_by_status || {});
    var revCur = Object.keys(a.revenue_by_currency || {})[0];
    var statusTable = curs.length ? '<div class="oa-table-wrap"><table class="oa-table"><thead><tr><th>Currency</th>' + ['pending', 'qualified', 'approved', 'payable', 'processing', 'paid', 'reversed'].map(function (k) { return '<th class="oa-num">' + esc(title(k)) + '</th>'; }).join('') + '</tr></thead><tbody>' +
      curs.map(function (c) { var b = a.commission_by_status[c]; return '<tr><td>' + esc(c) + '</td>' + ['pending', 'qualified', 'approved', 'payable', 'processing', 'paid', 'reversed'].map(function (k) { return '<td class="oa-num pp-money">' + esc(money(b[k], c)) + '</td>'; }).join('') + '</tr>'; }).join('') + '</tbody></table></div>' : '<div class="oa-mini-empty">No commissions in this period.</div>';
    var po = a.payouts || { by_status: {} };
    var poRows = Object.keys(po.by_status || {}).map(function (k) { return '<li class="oa-hbar"><span class="oa-hbar-label">' + esc(title(k)) + '</span><span class="oa-hbar-track"><span style="width:100%"></span></span><span class="oa-hbar-val">' + esc(po.by_status[k].count) + '</span></li>'; }).join('');
    $('[data-an]', root).innerHTML =
      '<div class="oa-grid oa-grid-4">' + stat('Clicks', num(t.clicks), num(t.visitors) + ' unique visitors', '', go('campaigns')) + stat('Signups', num(t.referrals), num(t.demos) + ' ' + trialsLabel(), '', go('referrals', { from: r.from, to: r.to, campaign_id: ctx.campaign })) +
      stat('Customers', num(t.customers), num(t.active_subscriptions) + ' active subscriptions', '', go('customers', { stage: 'customer' })) + stat('Conversion', t.conversion_rate + '%', 'visitors → signups · ' + t.customer_rate + '% signups → customers', '', go('referrals', { from: r.from, to: r.to, campaign_id: ctx.campaign })) + '</div>' +
      '<div class="oa-grid oa-grid-4">' + stat('Revenue generated', money(t.revenue, revCur), 'paid invoices, net of refunds', '', go('customers', { stage: 'customer' })) + stat('Commission earned', money(t.commission, curs[0]), 'excluding reversed', '', go('commissions', { from: r.from, to: r.to })) +
      stat('Payouts', num(po.count), money(t.payouts, curs[0]) + ' paid in period', '', go('payouts')) + stat('Campaigns', num(a.campaigns.length), 'with activity in range', '', go('campaigns')) + '</div>' +
      '<div class="oa-grid oa-grid-2">' + card('Clicks', bars(a.series.clicks, a.labels), '', seg) + card('Unique visitors', bars(a.series.visitors, a.labels)) +
      card('Signups', bars(a.series.referrals, a.labels)) + card(trialOn() ? 'Trials started' : 'Demos approved', bars(a.series.demos, a.labels)) +
      card('Customers', bars(a.series.customers, a.labels)) + card('Revenue generated', bars(a.series.revenue, a.labels, true, revCur)) +
      card('Commission earned', bars(a.series.commission, a.labels, true, curs[0])) + card('Payouts paid', bars(a.series.payouts, a.labels, true, curs[0])) + '</div>' +
      '<div class="oa-grid oa-grid-2">' + card('Funnel', '<ul class="oa-hbars">' + a.funnel.map(function (f) { return '<li class="oa-hbar"><span class="oa-hbar-label">' + esc(f.stage === 'demos' ? (trialOn() ? 'Trials' : 'Demos') : title(f.stage)) + '</span><span class="oa-hbar-track"><span style="width:' + (f.count * 100 / maxF) + '%"></span></span><span class="oa-hbar-val">' + esc(num(f.count)) + '</span></li>'; }).join('') + '</ul>') +
      card('Referral sources', a.sources.length ? '<ul class="oa-hbars">' + a.sources.map(function (s2) { return '<li class="oa-hbar"><span class="oa-hbar-label">' + esc(title(s2.source)) + '</span><span class="oa-hbar-track"><span style="width:' + (s2.count * 100 / Math.max.apply(null, a.sources.map(function (x) { return x.count; }))) + '%"></span></span><span class="oa-hbar-val">' + esc(num(s2.count)) + '</span></li>'; }).join('') + '</ul>' : '<div class="oa-mini-empty">No signups in this period.</div>') + '</div>' +
      card('Commissions by status', statusTable) +
      card('Payouts by status', poRows ? '<ul class="oa-hbars">' + poRows + '</ul>' : '<div class="oa-mini-empty">No payouts in this period.</div>') +
      card('Campaign performance', a.campaigns.length ? '<div class="oa-table-wrap"><table class="oa-table"><thead><tr><th>Campaign</th><th>Status</th><th class="oa-num">Clicks</th><th class="oa-num">Visitors</th><th class="oa-num">Signups</th><th class="oa-num">' + (trialOn() ? 'Trials' : 'Demos') + '</th><th class="oa-num">Customers</th><th class="oa-num">Revenue</th><th class="oa-num">Conversion</th></tr></thead><tbody>' +
        a.campaigns.map(function (c) { return '<tr><td><b>' + esc(c.name) + '</b> <span class="oa-small oa-muted">/' + esc(c.slug) + '</span>' + (c.kind === 'onboarding' ? ' <span class="oa-pill" data-tone="brand">Onboarding</span>' : '') + '</td><td>' + pill(c.status) + '</td><td class="oa-num">' + num(c.clicks) + '</td><td class="oa-num">' + num(c.visitors) + '</td><td class="oa-num">' + num(c.referrals) + '</td><td class="oa-num">' + num(c.demos) + '</td><td class="oa-num">' + num(c.customers) + '</td><td class="oa-num pp-money">' + esc(money(c.revenue, revCur)) + '</td><td class="oa-num">' + esc(c.conversion_rate) + '%</td></tr>'; }).join('') + '</tbody></table></div>' : '<div class="oa-mini-empty">No campaigns yet — create one under Campaigns.</div>');
    $$('[data-unit]', root).forEach(function (b2) { b2.onclick = function () { ctx.unit = b2.getAttribute('data-unit'); route(true); }; });
  }

  // ── Sell LeadAI: the live product & pricing kit ────────────
  async function viewSell(root) {
    var k = (await api('/api/partner/v1/sales')).sales;
    var planCard = function (p) {
      var cyc = p.cycles || {}, m = cyc.monthly, y = cyc.yearly;
      var priceRow = function (label, c) {
        if (!c) return '';
        return '<div style="margin-top:8px"><div class="oa-small oa-muted">' + esc(label) + '</div><div>' +
          (c.discount ? '<s class="oa-muted">' + esc(money(c.list_price, p.currency)) + '</s> ' : '') + '<b>' + esc(money(c.customer_price, p.currency)) + '</b>' +
          (c.discount ? ' <span class="oa-small oa-muted">' + esc(c.discount.name) + (c.discount.first_payment_only ? ' · first payment' : '') + '</span>' : '') + '</div>' +
          '<div class="oa-small">You earn <b class="pp-money">' + esc(money(c.commission_first_payment, p.currency)) + '</b> on the first payment' +
          (c.commission_per_renewal ? ' + <b class="pp-money">' + esc(money(c.commission_per_renewal, p.currency)) + '</b> per renewal' : '') + '</div></div>';
      };
      // every API-coverage choice: the price is all included; customers who bring their own
      // Apify (scraping) / Gemini (AI) key pay less — partners can sell either way
      var opts = (p.coverage_options || []).filter(function (o) { return o.monthly || o.yearly; });
      var covPrice = function (c, per) {
        if (!c) return '';
        return '<span>' + (c.customer_price < c.list_price ? '<s class="oa-muted">' + esc(money(c.list_price, p.currency)) + '</s> ' : '') + '<b class="pp-money">' + esc(money(c.customer_price, p.currency)) + '</b><span class="oa-muted">/' + per + '</span></span>';
      };
      var covBlock = opts.length > 1 ? '<div class="pp-cov-wrap"><div class="oa-small" style="font-weight:600">Price by who provides the APIs</div>' +
        '<p class="oa-small oa-muted" style="margin:2px 0 6px">Prices include Apify scraping and Gemini AI. A customer who brings their own key for either pays less — sell whichever fits.</p>' +
        '<ul class="pp-cov">' + opts.map(function (o) {
          var m = o.monthly, y = o.yearly;
          var earn = [m ? money(m.commission_first_payment, p.currency) + ' monthly' : '', y ? money(y.commission_first_payment, p.currency) + ' yearly' : ''].filter(Boolean).join(' · ');
          return '<li' + (o.key === 'all_included' ? ' class="is-base"' : '') + '><span class="pp-cov-name">' + esc(o.label) + '</span>' +
            '<span class="pp-cov-price">' + [covPrice(m, 'mo'), covPrice(y, 'yr')].filter(Boolean).join(' <span class="oa-muted" aria-hidden="true">·</span> ') + '</span>' +
            '<span class="pp-cov-earn oa-small oa-muted">You earn <b class="pp-money">' + esc(earn) + '</b> on the first payment</span></li>';
        }).join('') + '</ul></div>' : '';
      var r = p.commission_rule;
      return '<section class="oa-card pp-asset"><div class="oa-card-head"><div><div class="oa-card-title">' + esc(p.name) + (p.is_default ? ' <span class="oa-pill" data-tone="brand">Most popular</span>' : '') + '</div>' +
        (p.description ? '<div class="oa-card-sub">' + esc(p.description) + '</div>' : '') + '</div></div>' +
        (p.free ? '<p class="oa-small"><b>Free plan</b> — a good first step; you earn when they upgrade.</p>' : priceRow(covBlock ? 'Monthly · all included' : 'Monthly', m) + priceRow(covBlock ? 'Yearly · all included' : 'Yearly', y) + covBlock) +
        (r ? '<p class="oa-small oa-muted">' + esc(r.type === 'percentage' ? r.value + '%' : r.type === 'hybrid' ? r.value + '% + ' + money(r.fixed_amount, p.currency) : money(r.value, p.currency)) + ' commission' +
          (r.recurring ? (r.duration_months ? ', recurring for ' + r.duration_months + ' months' : ', recurring for life') : ', first payment only') + (r.cap_amount ? ', capped at ' + money(r.cap_amount, p.currency) + ' per customer' : '') + '</p>' : '') +
        '<ul class="oa-feed">' + (p.highlights || []).slice(0, 7).map(function (h) { return '<li><span class="oa-feed-main oa-small">' + esc(h) + '</span></li>'; }).join('') + '</ul>' +
        (p.share_url ? linkRow(p.share_url, 'Share link for ' + p.name) : '') + '</section>';
    };
    var share = k.referral_url ? 'Try LeadAI — it finds real buyers in your social media comments: ' + abs(k.referral_url) : '';
    root.innerHTML = head('Sell LeadAI', 'Everything you need to sell: the live plans and prices your customers pay, what you earn on each, and links that attribute the sale to you.',
      (has('deals.manage') ? '<a class="btn btn-primary btn-sm" href="#/deals">+ Register a deal</a>' : '') + (has('marketing.access') ? '<a class="btn btn-secondary btn-sm" href="#/marketing">Marketing center</a>' : '')) +
      '<div class="oa-grid oa-grid-2">' + card('The pitch', '<ul class="oa-feed">' + k.pitch.map(function (x) { return '<li><span class="oa-feed-main">' + esc(x) + '</span></li>'; }).join('') + '</ul>' +
        (share ? '<button type="button" class="btn btn-secondary btn-sm" data-copy="' + esc(share) + '" style="margin-top:8px">Copy a short pitch with your link</button>' : '')) +
      card('How you get credit', (k.referral_url ? linkRow(k.referral_url, 'Your referral link') : '') + '<p class="oa-small" style="margin-top:8px">Code <b class="oa-mono">' + esc(k.referral_code) + '</b> · clicks are remembered for <b>' + esc(k.attribution_window_days) + ' days</b>. ' +
        'Talking to a prospect who hasn\'t signed up yet? <b>Register the deal</b> — once LeadAI approves it your claim is protected for ' + esc(k.deal_protection_days) + ' days.</p>' +
        (trialOn() ? '<p class="oa-small">Businesses who sign up through your link start a <b>' + esc(trialName()) + '</b> at once — no approval, no card. You earn when they choose a paid plan.</p>' : '') +
        (k.coupons.length ? '<p class="oa-small"><b>Your coupons:</b> ' + k.coupons.map(function (c) { return '<span class="oa-mono">' + esc(c.code) + '</span> (' + esc(c.discount_type === 'percentage' ? c.discount_value + '%' : money(c.discount_value, c.currency)) + ' off)'; }).join(', ') + '</p>' : '')) + '</div>' +
      (k.plans.length ? '<div class="oa-grid oa-grid-3" style="margin-top:14px">' + k.plans.map(planCard).join('') + '</div>' : empty('No plans published', 'LeadAI has no public plans right now.'));
    bindCopy(root);
  }

  // ── Deals: register prospects (deal registration) ───────────
  var DEAL_LABEL = { registered: 'Awaiting review', approved: 'Approved · protected', rejected: 'Rejected', won: 'Won', lost: 'Lost', expired: 'Expired' };
  async function viewDeals(root) {
    var plans = [];
    try { if (has('sales.view')) plans = ((await api('/api/partner/v1/sales')).sales.plans || []).filter(function (p) { return !p.free; }); } catch (e) { plans = []; }
    root.innerHTML = head('Deals', 'Register prospects you are working on. LeadAI reviews each one; an approved deal protects your claim if the prospect signs up through someone else.',
      '<button type="button" class="btn btn-primary btn-sm" id="newDeal">+ Register a deal</button>') + '<div id="dlChips" class="oa-actions" style="margin-bottom:10px"></div><div id="dlT"></div>';
    var t = table($('#dlT', root), {
      url: function (st) { return '/api/partner/v1/deals' + qs({ page: st.page, limit: st.limit, status: st.status, q: st.q }); },
      filters: [{ key: 'q', label: 'Search company or contact…' }, { key: 'status', type: 'select', label: 'Status', options: [['', 'All deals']].concat(Object.keys(DEAL_LABEL).map(function (s) { return [s, DEAL_LABEL[s]]; })) }],
      onData: function (d) { var c = d.counts || {}; $('#dlChips', root).innerHTML = Object.keys(DEAL_LABEL).filter(function (s) { return c[s]; }).map(function (s) { return pill(s, DEAL_LABEL[s] + ' · ' + c[s]); }).join(' '); },
      columns: [
        { label: 'Prospect', render: function (d) { return '<b>' + esc(d.company) + '</b><div class="oa-small oa-muted">' + esc([d.contact_name, d.contact_email].filter(Boolean).join(' · ')) + '</div>'; } },
        { label: 'Plan', render: function (d) { return esc(d.expected_plan || '—'); } },
        { label: 'Value', cls: 'oa-num', render: function (d) { return d.expected_value != null ? '<span class="pp-money">' + esc(money(d.expected_value, d.currency)) + '</span>' : '—'; } },
        { label: 'Status', render: function (d) { return pill(d.status, DEAL_LABEL[d.status]) + (d.protected_until && d.status === 'approved' ? '<div class="oa-small oa-muted">until ' + esc(date(d.protected_until)) + '</div>' : '') + (d.review_note ? '<div class="oa-small oa-muted">' + esc(d.review_note) + '</div>' : ''); } },
        { label: 'Signed up', render: function (d) { return d.referral_stage ? pill(d.referral_stage, STAGE_LABEL[d.referral_stage]) : '<span class="oa-muted">Not yet</span>'; } },
        { label: 'Registered', render: function (d) { return esc(date(d.created_at)); } },
        { label: '', cls: 'oa-num', render: function (d) {
          var open = d.status === 'registered' || d.status === 'approved';
          return open ? '<div class="oa-actions" style="justify-content:flex-end">' + (has('customers.create') && !d.organization_id ? '<button type="button" class="btn btn-secondary btn-xs" data-dc>Onboard as customer</button>' : '') + '<button type="button" class="btn btn-ghost btn-xs" data-de>Edit</button></div>' : ''; } }
      ],
      bindRow: function (tr, d, reload) {
        var e = $('[data-de]', tr); if (e) e.onclick = async function () {
          var ok = await modal({ title: 'Update deal — ' + d.company, submit: 'Save',
            body: '<div class="oa-form-grid">' + field('expected_plan', 'Expected plan', d.expected_plan || '', { options: [['', '—']].concat(plans.map(function (p) { return [p.slug, p.name]; })) }) +
              field('expected_value', 'Expected value', d.expected_value == null ? '' : d.expected_value, { type: 'number', min: 0, step: '0.01' }) +
              field('notes', 'Notes', d.notes || '', { type: 'textarea', span: true }) + '</div><label class="oa-switch"><input type="checkbox" name="close_as_lost"><span class="oa-switch-text"><b>Close this deal as lost</b><span>The prospect is no longer interested.</span></span></label>',
            onSubmit: function (f) { var x = formData(f); return api('/api/partner/v1/deals/' + d.id, { method: 'PATCH', body: { expected_plan: x.expected_plan || null, expected_value: x.expected_value === '' ? null : parseFloat(x.expected_value), notes: x.notes, close_as_lost: !!x.close_as_lost } }); } });
          if (ok) { toast('Deal updated'); reload(); }
        };
        var c = $('[data-dc]', tr); if (c) c.onclick = async function () {
          if (!(await modal({ title: 'Onboard ' + d.company + '?', submit: 'Create customer', body: '<p>' + esc(onboardNote(d.contact_email)) + '</p>' }))) return;
          busy(c, function () { return api('/api/partner/v1/deals/' + d.id + '/convert', { method: 'POST' }); }).then(function (res) { toast(createdMessage(res)); reload(); }, function (err) { toast(err.message, 'error'); });
        };
      },
      empty: { title: 'No deals yet', desc: 'Register a prospect before they sign up to protect your claim.' }
    });
    $('#newDeal', root).onclick = async function () {
      var ok = await modal({ title: 'Register a deal', submit: 'Register deal', wide: true,
        body: '<div class="oa-form-grid">' + field('company', 'Company *', '', { max: 160 }) + field('contact_name', 'Contact name', '', { max: 120 }) +
          field('contact_email', 'Contact email *', '', { type: 'email', max: 200 }) + field('contact_phone', 'Contact phone', '', { max: 30 }) +
          field('website', 'Website', '', { max: 300, placeholder: 'https://' }) + field('expected_plan', 'Expected plan', '', { options: [['', '—']].concat(plans.map(function (p) { return [p.slug, p.name]; })) }) +
          field('expected_value', 'Expected value', '', { type: 'number', min: 0, step: '0.01' }) + field('notes', 'Notes for LeadAI', '', { type: 'textarea', span: true }) + '</div>',
        onSubmit: function (f) {
          var x = formData(f);
          if (!x.company || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(x.contact_email)) throw new Error('Company and a valid contact email are required.');
          return api('/api/partner/v1/deals', { method: 'POST', body: { company: x.company, contact_name: x.contact_name || null, contact_email: x.contact_email, contact_phone: x.contact_phone || null, website: x.website || null, expected_plan: x.expected_plan || null, expected_value: x.expected_value === '' ? null : parseFloat(x.expected_value), notes: x.notes || null } });
        } });
      if (ok) { toast(ok.message || 'Deal registered'); t.reload(); }
    };
  }

  // ── Tasks: work assigned by the LeadAI team ─────────────────
  var TASK_LABEL = { open: 'To do', in_progress: 'In progress', submitted: 'Waiting for review', done: 'Done', cancelled: 'Cancelled' };
  Object.assign(TONE, { open: 'warning', in_progress: 'brand', submitted: 'info', done: 'success' });
  var SECTION_LABEL = { sell: 'Sell LeadAI', deals: 'Deals', referrals: 'Referrals', customers: 'Customers', campaigns: 'Campaigns', coupons: 'Coupons', marketing: 'Marketing center',
    commissions: 'Commissions', wallet: 'Wallet', payouts: 'Payouts', analytics: 'Analytics', profile: 'Profile', api: 'API access', settings: 'Settings & security' };
  // the calendar day LeadAI picked (no timezone shift)
  function dueDay(t) { if (!t.due_date) return t.due_at ? date(t.due_at) : ''; var x = t.due_date.split('-'); return new Date(+x[0], +x[1] - 1, +x[2]).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }); }
  function taskBadges(t) {
    return (t.priority === 'high' ? ' <span class="oa-pill" data-tone="danger">High priority</span>' : t.priority === 'low' ? ' <span class="oa-pill" data-tone="neutral">Low priority</span>' : '') +
      (t.overdue ? ' <span class="oa-pill" data-tone="danger">Overdue</span>' : '');
  }
  async function openTask(id, reload) {
    var t = (await api('/api/partner/v1/tasks/' + encodeURIComponent(id))).task;
    var canStart = t.status === 'open', canSubmit = t.status === 'open' || t.status === 'in_progress';
    var sec = t.section && SECTION_LABEL[t.section] ? '<a class="btn btn-secondary btn-sm" href="#/' + esc(t.section) + '" data-close>Open ' + esc(SECTION_LABEL[t.section]) + ' →</a>' : '';
    var ok = await modal({ title: t.title, wide: true, submit: canSubmit ? 'Submit for review' : null,
      body: (t.status === 'in_progress' && t.review_note ? '<div class="pp-banner"><b>LeadAI sent this back:</b> ' + esc(t.review_note) + '</div>' : '') +
        (t.status === 'done' && t.review_note ? '<div class="pp-banner"><b>Note from LeadAI:</b> ' + esc(t.review_note) + '</div>' : '') +
        kv([['Status', pill(t.status, TASK_LABEL[t.status]) + taskBadges(t), true], ['Due', t.due_at ? dueDay(t) : 'No due date'], ['Assigned', dt(t.created_at)]]) +
        (t.details ? '<div class="oa-card" style="white-space:pre-wrap;margin:12px 0">' + esc(t.details) + '</div>' : '') +
        ((sec || canStart) ? '<div class="oa-actions" style="margin:8px 0 12px">' + (canStart ? '<button type="button" class="btn btn-secondary btn-sm" data-start>Start task</button>' : '') + sec + '</div>' : '') +
        (t.submission_note && !canSubmit ? '<div class="oa-field"><label>What you reported</label><div class="oa-small" style="white-space:pre-wrap">' + esc(t.submission_note) + '</div></div>' : '') +
        (canSubmit ? field('note', 'What did you do? *', t.status === 'in_progress' ? (t.submission_note || '') : '', { type: 'textarea', rows: 4, max: 4000, hint: 'LeadAI reviews this before the task is marked done. Include links, customer names or numbers.' }) : '') +
        '<div class="oa-card-sub" style="margin-top:12px">History</div><ul class="oa-feed">' + (t.history || []).slice().reverse().map(function (h) {
          return '<li><span class="oa-feed-main">' + pill(h.status, TASK_LABEL[h.status]) + ' <span class="oa-small oa-muted">' + esc(h.by || '') + '</span>' + (h.note ? '<div class="oa-small" style="white-space:pre-wrap">' + esc(h.note) + '</div>' : '') + '</span><time>' + esc(dt(h.at)) + '</time></li>'; }).join('') + '</ul>',
      onOpen: function (form) {
        var sb = $('[data-start]', form); if (sb) sb.onclick = function () {
          busy(sb, function () { return api('/api/partner/v1/tasks/' + t.id + '/start', { method: 'POST', body: {} }); }).then(function () { toast('Task started'); sb.remove(); if (reload) reload(); refreshTasks(); }, function (err) { toast(err.message, 'error'); });
        };
      },
      onSubmit: function (f) {
        var x = formData(f);
        if (!x.note) throw new Error('Tell LeadAI what you did.');
        return api('/api/partner/v1/tasks/' + t.id + '/submit', { method: 'POST', body: { note: x.note } });
      } });
    if (ok) { toast(ok.message || 'Sent for review'); if (reload) reload(); refreshTasks(); }
  }
  async function viewTasks(root, q, ctx, param) {
    root.innerHTML = head('Tasks', 'Work the LeadAI team has asked you to do. Open a task, do the work, then tell LeadAI what you did. They review it and mark it done.') +
      '<div id="tkChips" class="oa-actions" style="margin-bottom:10px"></div><div id="tkT"></div>';
    var t = table($('#tkT', root), {
      url: function (st) { return '/api/partner/v1/tasks' + qs({ page: st.page, limit: st.limit, status: st.status, q: st.q }); },
      filters: [{ key: 'q', label: 'Search tasks…' }, { key: 'status', type: 'select', label: 'Status', options: [['', 'All tasks']].concat(Object.keys(TASK_LABEL).map(function (s) { return [s, TASK_LABEL[s]]; })) }],
      onData: function (d) { var c = d.counts || {}; $('#tkChips', root).innerHTML = Object.keys(TASK_LABEL).filter(function (s) { return c[s]; }).map(function (s) { return pill(s, TASK_LABEL[s] + ' · ' + c[s]); }).join(' '); },
      columns: [
        { label: 'Task', render: function (x) { return '<b>' + esc(x.title) + '</b>' + taskBadges(x) + (x.details ? '<div class="oa-small oa-muted">' + esc(x.details.length > 110 ? x.details.slice(0, 110) + '…' : x.details) + '</div>' : ''); } },
        { label: 'Due', render: function (x) { return x.due_at ? esc(dueDay(x)) : '<span class="oa-muted">—</span>'; } },
        { label: 'Status', render: function (x) { return pill(x.status, TASK_LABEL[x.status]) + (x.status === 'in_progress' && x.review_note ? '<div class="oa-small oa-muted">Sent back: ' + esc(x.review_note) + '</div>' : ''); } },
        { label: 'Assigned', render: function (x) { return esc(date(x.created_at)); } },
        { label: '', cls: 'oa-num', render: function (x) { return '<button type="button" class="btn ' + (x.status === 'open' || x.status === 'in_progress' ? 'btn-primary' : 'btn-ghost') + ' btn-xs" data-open>' + (x.status === 'open' || x.status === 'in_progress' ? 'Open' : 'View') + '</button>'; } }
      ],
      bindRow: function (tr, x, reload) { $('[data-open]', tr).onclick = function () { openTask(x.id, reload).catch(function (err) { toast(err.message, 'error'); }); }; },
      empty: { title: 'No tasks', desc: 'When the LeadAI team assigns you work, it shows up here and you get a notification.' }
    });
    if (param) {
      history.replaceState(null, '', '#/tasks');
      setTimeout(function () { openTask(param, t.reload).catch(function (err) { toast(err.message, 'error'); }); }, 0);
    }
  }
  async function refreshTasks() {
    try {
      var me = (await api('/api/partner/v1/me')).me; S.me.open_tasks = me.open_tasks; renderNav();
      var key = location.hash.replace(/^#\/?/, '').split(/[/?]/)[0];
      $$('#oa-nav a').forEach(function (a) { if (a.getAttribute('data-route') === key) a.setAttribute('aria-current', 'page'); });
    } catch (e) { /* nav count only */ }
  }

  async function viewCampaigns(root) {
    var L = has('referral_links.create') ? (await api('/api/partner/v1/referral-links')).links : null;
    root.innerHTML = head('Campaigns', 'Campaign links let you track each channel separately.', '<button type="button" class="btn btn-primary btn-sm" id="newCamp">+ New campaign</button>') +
      (L ? card('Main links', '<div class="oa-form-grid">' + linkRow(L.referral_url, 'Referral link') + linkRow(L.signup_url, trialOn() ? 'Direct sign-up link (' + trialName() + ')' : 'Direct demo request link') + '</div>') : '') + '<div id="campT" style="margin-top:14px"></div>';
    bindCopy(root);
    var t = table($('#campT', root), {
      url: function () { return '/api/partner/v1/campaigns'; },
      columns: [
        { label: 'Campaign', render: function (c) { return '<b>' + esc(c.name) + '</b>' + (c.kind === 'onboarding' ? ' <span class="oa-pill" data-tone="brand">Onboarding</span>' : '') + '<div class="oa-small oa-muted">Lands on ' + esc(c.landing_path || 'default page') + '</div>'; } },
        { label: 'Link', render: function (c) { return '<div class="pp-link"><input class="form-input" readonly value="' + esc(abs(c.url)) + '" aria-label="Campaign link"><button type="button" class="btn btn-secondary btn-xs" data-copy="' + esc(abs(c.url)) + '">Copy</button></div>'; } },
        { label: 'Status', render: function (c) { return pill(c.status); } },
        { label: '', cls: 'oa-num', render: function (c) { return '<button type="button" class="btn btn-ghost btn-xs" data-edit>Edit</button>'; } }
      ],
      bindRow: function (tr, c, reload) { $('[data-edit]', tr).onclick = function () { editCampaign(c).then(function (ok) { if (ok) reload(); }); }; },
      empty: { title: 'No campaigns yet', desc: 'Create one per channel — newsletter, webinar, social — to see which converts best.' }
    });
    $('#newCamp', root).onclick = function () { editCampaign(null).then(function (ok) { if (ok) t.reload(); }); };
  }
  async function editCampaign(c) {
    var ok = await modal({ title: c ? 'Edit campaign' : 'New campaign', submit: c ? 'Save' : 'Create',
      body: '<div class="oa-form-grid">' + field('name', 'Name *', c ? c.name : '', { max: 80 }) + (c ? field('slug', 'Slug', c.slug, { readonly: true }) : field('slug', 'Slug (optional)', '', { max: 40, placeholder: 'spring-webinar' })) +
        field('landing_path', 'Landing page', c ? c.landing_path : '', { max: 200, placeholder: '/pricing', hint: 'A LeadAI site path; empty = the program default.' }) +
        field('status', 'Status', c ? c.status : 'active', { options: ['active', 'archived'] }) +
        (!c && has('reseller.customers.manage') ? field('kind', 'Link type', 'referral', { options: [['referral', 'Referral link'], ['onboarding', 'Customer onboarding link (managed customers)']], hint: 'Signups through an onboarding link become customers you manage.' }) : '') + '</div>',
      onSubmit: function (f) {
        var d = formData(f); if (!d.name) throw new Error('Name is required.');
        if (c) { delete d.slug; delete d.kind; } else if (!d.slug) delete d.slug;
        if (!d.landing_path) d.landing_path = null;
        return api(c ? '/api/partner/v1/campaigns/' + c.id : '/api/partner/v1/campaigns', { method: c ? 'PATCH' : 'POST', body: d });
      } });
    if (ok) toast(c ? 'Campaign saved' : 'Campaign created');
    return ok;
  }

  async function viewCoupons(root) {
    var d = await api('/api/partner/v1/coupons');
    root.innerHTML = head('Coupons', 'Customers who pay with your coupon get a discount and are attributed to you.',
      d.can_create ? '<button type="button" class="btn btn-primary btn-sm" id="newCp">+ New coupon</button>' : '') +
      (!d.can_create ? '<div class="pp-banner">Coupons are issued by the LeadAI partner team. Contact us if you need one.</div>' : '') + '<div id="cpT"></div>';
    var t = table($('#cpT', root), {
      url: function () { return '/api/partner/v1/coupons'; },
      columns: [
        { label: 'Code', render: function (c) { return '<b class="oa-mono">' + esc(c.code) + '</b> <button type="button" class="btn btn-ghost btn-xs" data-copy="' + esc(c.code) + '">Copy</button>'; } },
        { label: 'Discount', render: function (c) { return esc(c.discount_type === 'percentage' ? c.discount_value + '%' : money(c.discount_value, c.currency)); } },
        { label: 'Redeemed', cls: 'oa-num', render: function (c) { return esc(num(c.times_redeemed) + (c.max_redemptions ? ' / ' + num(c.max_redemptions) : '')); } },
        { label: 'Expires', render: function (c) { return esc(date(c.expires_at)); } },
        { label: 'Plans', render: function (c) { return esc((c.plan_slugs || []).join(', ') || 'All'); } },
        { label: 'Status', render: function (c) { return pill(c.status) + (c.created_by_role === 'super_admin' ? '<div class="oa-small oa-muted">issued by LeadAI</div>' : ''); } },
        { label: '', cls: 'oa-num', render: function (c) { return d.can_create && c.created_by_role === 'partner' ? '<button type="button" class="btn btn-ghost btn-xs" data-edit>Edit</button>' : ''; } }
      ],
      bindRow: function (tr, c, reload) {
        var b = $('[data-edit]', tr); if (!b) return;
        b.onclick = async function () {
          var ok = await modal({ title: 'Edit coupon ' + c.code, submit: 'Save',
            body: '<div class="oa-form-grid">' + field('discount_value', 'Discount % *', c.discount_value, { type: 'number', min: 1, step: '0.5', hint: 'Up to ' + d.max_percent + '%.' }) +
              field('max_redemptions', 'Max redemptions', c.max_redemptions || '', { type: 'number', min: 1 }) +
              field('expires_at', 'Expires', c.expires_at ? String(c.expires_at).slice(0, 10) : '', { type: 'date' }) +
              field('status', 'Status', c.status === 'active' ? 'active' : 'disabled', { options: [['active', 'Active'], ['disabled', 'Disabled']] }) + '</div>',
            onSubmit: function (f) {
              var x = formData(f);
              return api('/api/partner/v1/coupons/' + encodeURIComponent(c.id), { method: 'PATCH', body: { discount_type: 'percentage', discount_value: parseFloat(x.discount_value) || 0,
                max_redemptions: x.max_redemptions ? parseInt(x.max_redemptions, 10) : null, expires_at: x.expires_at || null, plan_slugs: c.plan_slugs || [], status: x.status } });
            } });
          if (ok) { toast('Coupon saved'); reload(); }
        };
      },
      empty: { title: 'No coupons yet', desc: d.can_create ? 'Create a coupon to share with your audience.' : '' }
    });
    var nb = $('#newCp', root);
    if (nb) nb.onclick = async function () {
      var ok = await modal({ title: 'New coupon', submit: 'Create coupon',
        body: '<div class="oa-form-grid">' + field('code', 'Code *', '', { max: 30, placeholder: 'SPRING15' }) + field('discount_value', 'Discount % *', '', { type: 'number', min: 1, step: '0.5', hint: 'Up to ' + d.max_percent + '%.' }) +
          field('max_redemptions', 'Max redemptions', '', { type: 'number', min: 1 }) + field('expires_at', 'Expires', '', { type: 'date' }) + '</div>',
        onSubmit: function (f) { var x = formData(f); return api('/api/partner/v1/coupons', { method: 'POST', body: { code: x.code, discount_type: 'percentage', discount_value: parseFloat(x.discount_value) || 0, max_redemptions: x.max_redemptions ? parseInt(x.max_redemptions, 10) : null, expires_at: x.expires_at || null } }); } });
      if (ok) { toast('Coupon created'); t.reload(); }
    };
  }

  async function viewMarketing(root, q, ctx) {
    var d = await api('/api/partner/v1/marketing-assets');
    var all = d.items || [], cats = d.categories || {};
    ctx.cat = ctx.cat || '';
    var present = Object.keys(cats).filter(function (k) { return all.some(function (a) { return a.category === k; }); });
    root.innerHTML = head('Marketing center', 'Approved logos, images, brochures, videos, banners, email templates and copy. Text already contains your referral link.') +
      '<div class="oa-toolbar"><div class="oa-toolbar-filters"><div class="oa-seg" role="group" aria-label="Category"><button type="button" data-cat="" aria-pressed="' + (!ctx.cat) + '">All</button>' +
      present.map(function (k) { return '<button type="button" data-cat="' + esc(k) + '" aria-pressed="' + (ctx.cat === k) + '">' + esc(cats[k]) + '</button>'; }).join('') + '</div>' +
      '<label class="sr-only" for="mkQ">Search assets</label><input class="form-input oa-grow" id="mkQ" type="search" placeholder="Search assets…" value="' + esc(ctx.q || '') + '"></div></div><div id="mkList"></div>';
    function render() {
      var items = all.filter(function (a) { return (!ctx.cat || a.category === ctx.cat) && (!ctx.q || (a.title + ' ' + (a.description || '')).toLowerCase().indexOf(ctx.q.toLowerCase()) >= 0); });
      $('#mkList', root).innerHTML = items.length ? '<div class="oa-grid oa-grid-3">' + items.map(function (a) {
        var ft = a.file_type || '', fp = a.file_path, img = (fp && /^image\//.test(ft)) || (!fp && a.url && /\.(png|jpe?g|gif|webp)(\?|$)/i.test(a.url));
        var preview = img ? '<img src="' + esc(fp || a.url) + '" alt="' + esc(a.title) + '" loading="lazy">' : (fp && /^video\//.test(ft) ? '<video src="' + esc(fp) + '" controls preload="metadata" style="max-width:100%;border-radius:var(--radius-md)"></video>' : '');
        var tpl = a.category === 'email_templates' && a.content ? (a.subject ? 'Subject: ' + a.subject + '\n\n' : '') + a.content : a.content;
        // referral links are site paths when PUBLIC_BASE_URL is unset: make copied text usable anywhere
        if (tpl) tpl = tpl.replace(/(^|[\s("'])(\/r\/[A-Za-z0-9_-]+(?:\/[A-Za-z0-9_-]+)?)/g, function (m, pre, path) { return pre + location.origin + path; });
        return '<section class="oa-card pp-asset"><div class="oa-card-title">' + esc(a.title) + '</div><div class="oa-small oa-muted">' + esc(a.category_label) + (a.file_size ? ' · ' + esc(Math.max(1, Math.round(a.file_size / 1024))) + ' KB' : '') + '</div>' +
          (a.description ? '<p class="oa-small">' + esc(a.description) + '</p>' : '') + preview +
          (tpl ? '<pre>' + esc(tpl) + '</pre><button type="button" class="btn btn-secondary btn-sm" data-copy="' + esc(tpl) + '">Copy text</button>' : '') +
          (fp ? '<a class="btn btn-primary btn-sm" href="' + esc(fp) + '?download=true">⤓ Download</a>' : '') +
          (a.url ? '<a class="btn btn-secondary btn-sm" href="' + esc(a.url) + '" target="_blank" rel="noopener noreferrer">Open link ↗</a>' : '') + '</section>';
      }).join('') + '</div>' : empty(all.length ? 'No matching assets' : 'No marketing materials yet', all.length ? 'Try another category or search.' : 'The LeadAI partner team publishes logos, banners, brochures and copy here.');
      bindCopy(root);
    }
    $$('[data-cat]', root).forEach(function (b2) { b2.onclick = function () { ctx.cat = b2.getAttribute('data-cat'); $$('[data-cat]', root).forEach(function (x) { x.setAttribute('aria-pressed', String(x === b2)); }); render(); }; });
    var t; $('#mkQ', root).addEventListener('input', function () { var v = this.value; clearTimeout(t); t = setTimeout(function () { ctx.q = v.trim(); render(); }, 200); });
    render();
  }

  /** '/partner#/x' -> '#/x' (this portal); other site paths ('/partners') stay as they are */
  function notifHref(link) {
    link = String(link || '');
    if (/^\/partner\/?#\//.test(link)) return link.slice(link.indexOf('#'));
    return /^\/(?!\/)/.test(link) ? link : '';
  }
  async function viewNotifications(root) {
    root.innerHTML = head('Notifications', '', '<button type="button" class="btn btn-secondary btn-sm" id="readAll">Mark all read</button>') + '<div id="nT"></div>';
    var t = table($('#nT', root), {
      url: function (st) { return '/api/partner/v1/notifications' + qs({ page: st.page, limit: st.limit, unread: st.unread }); },
      rows: function (d) { d.pages = Math.max(1, Math.ceil((d.total || 0) / 25)); return d.items || []; },
      filters: [{ key: 'unread', type: 'select', label: 'Show', options: [['', 'All'], ['true', 'Unread only']] }],
      columns: [
        { label: '', render: function (n) { return n.read ? '' : '<span class="oa-pill" data-tone="brand">New</span>'; } },
        { label: 'Notification', render: function (n) { return '<b>' + esc(n.title) + '</b><div class="oa-small">' + esc(n.message) + '</div>'; } },
        { label: 'When', render: function (n) { return esc(dt(n.created_at)); } },
        { label: '', cls: 'oa-num', render: function (n) { var h = notifHref(n.link); return h ? '<a class="oa-link" href="' + esc(h) + '" data-open' + (h.charAt(0) === '/' ? ' target="_blank" rel="noopener"' : '') + '>Open</a>' : ''; } }
      ],
      bindRow: function (tr, n) { if (!n.read) tr.addEventListener('click', function () { if (n.read) return; n.read = true; var pl = $('.oa-pill', tr); if (pl) pl.remove(); api('/api/partner/v1/notifications/read', { method: 'POST', body: { id: n.id } }).then(refreshBell, function () {}); }); },
      empty: { title: 'No notifications', desc: 'Application updates, referrals, commissions and payouts are announced here.' }
    });
    $('#readAll', root).onclick = function () { api('/api/partner/v1/notifications/read', { method: 'POST', body: {} }).then(function () { refreshBell(); t.reload(); }, function (e) { toast(e.message, 'error'); }); };
  }

  async function viewApi(root) {
    root.innerHTML = head('API access', 'Read-only API keys for /api/partner/v1 (dashboard, referrals, customers, commissions, wallet, payouts, analytics).', '<button type="button" class="btn btn-primary btn-sm" id="newKey">+ New API key</button>') +
      card('Usage', '<pre class="oa-pre oa-mono" style="white-space:pre-wrap">curl -H "X-API-Key: lap_live_…" ' + esc(location.origin) + '/api/partner/v1/dashboard</pre><p class="oa-small oa-muted">Keys are read-only and limited to 60 requests per minute. Revoke a key at once if it leaks.</p><div id="apiIdx" class="oa-small"></div>') + '<div id="kT" style="margin-top:14px"></div>';
    api('/api/partner/v1').then(function (d) { var el = $('#apiIdx', root); if (el) el.innerHTML = '<b>Endpoints your partner account can read</b><ul class="oa-feed">' + d.endpoints.map(function (e) { return '<li><span class="oa-feed-main oa-mono">' + esc(e) + '</span></li>'; }).join('') + '</ul>'; }, function () {});
    var t = table($('#kT', root), {
      url: function () { return '/api/partner/v1/api-keys'; },
      columns: [
        { label: 'Name', render: function (k) { return '<b>' + esc(k.name) + '</b><div class="oa-mono oa-small">' + esc(k.prefix) + '</div>'; } },
        { label: 'Created', render: function (k) { return esc(date(k.created_at)); } },
        { label: 'Last used', render: function (k) { return esc(k.last_used_at ? dt(k.last_used_at) : 'Never'); } },
        { label: 'Status', render: function (k) { return pill(k.is_active ? 'active' : 'disabled', k.is_active ? 'Active' : 'Revoked'); } },
        { label: '', cls: 'oa-num', render: function (k) { return k.is_active ? '<button type="button" class="btn btn-ghost btn-xs" data-revoke>Revoke</button>' : ''; } }
      ],
      bindRow: function (tr, k, reload) { var b = $('[data-revoke]', tr); if (b) b.onclick = async function () { if (await modal({ title: 'Revoke API key?', body: '<p>Applications using “' + esc(k.name) + '” stop working immediately.</p>', submit: 'Revoke', danger: true })) { api('/api/partner/v1/api-keys/' + encodeURIComponent(k.key_id), { method: 'DELETE' }).then(function () { toast('Key revoked'); reload(); }, function (e) { toast(e.message, 'error'); }); } }; },
      empty: { title: 'No API keys', desc: 'Create a key to pull your partner data into your own tools.' }
    });
    $('#newKey', root).onclick = async function () {
      var res = await modal({ title: 'New API key', submit: 'Create key', body: field('name', 'Key name', 'Reporting', { max: 60 }),
        onSubmit: function (f) { return api('/api/partner/v1/api-keys', { method: 'POST', body: formData(f) }); } });
      if (res) {
        await modal({ title: 'Your new API key', submit: null, body: '<p>' + esc(res.message) + '</p>' + linkRow(res.api_key, 'API key'), onOpen: bindCopy });
        t.reload();
      }
    };
  }

  async function viewProfile(root) {
    var p = (await api('/api/partner/v1/profile')).partner;
    var tier = S.me.tier;
    root.innerHTML = head('Profile', 'Your partner details as LeadAI sees them.') +
      '<div class="oa-grid oa-grid-2">' + card('Partner account', kv([['Partner ID', p.partner_code], ['Referral code', p.referral_code], ['Type', title(p.partner_type)], ['Status', pill(p.status), true], ['Tier', tier ? tier.name : '—'], ['Partner since', date(p.approved_at)]])) +
      (S.me.tier ? card('Tier: ' + S.me.tier.name, ((S.me.tier.benefits || []).length ? '<ul class="oa-feed">' + S.me.tier.benefits.map(function (b) { return '<li><span class="oa-feed-main">' + esc(b) + '</span></li>'; }).join('') + '</ul>' : '<p class="oa-small oa-muted">No extra benefits listed.</p>') +
        (S.me.next_tier ? '<p class="oa-small" style="margin-top:10px"><b>Next: ' + esc(S.me.next_tier.name) + '</b> — ' + ['min_customers:customers:customers', 'min_referrals:referrals:referrals', 'min_revenue:revenue:revenue'].map(function (x) { var k = x.split(':'), need = (S.me.next_tier.requirements || {})[k[0]]; return need ? esc(k[2] === 'revenue' ? money(S.me.next_tier.progress[k[1]]) + ' / ' + money(need) : num(S.me.next_tier.progress[k[1]]) + ' / ' + num(need)) + ' ' + k[2] : ''; }).filter(Boolean).join(' · ') + '</p>' : '')) : '') +
      card('Your permissions', '<div>' + (p.effective_permissions || []).map(function (x) { return '<span class="pp-perm oa-pill" data-tone="brand">' + esc((S.me.permission_labels || {})[x] || x) + '</span>'; }).join('') + '</div><p class="oa-small oa-muted" style="margin-top:10px">Permissions are managed by the LeadAI partner team.</p>') + '</div>' +
      '<form class="oa-card" id="profForm" novalidate><div class="oa-card-head"><div class="oa-card-title">Contact &amp; business details</div></div><div class="oa-form-grid">' +
      field('name', 'Full name', p.name, { max: 120 }) + field('company', 'Company', p.company, { max: 160 }) + field('phone', 'Phone', p.phone, { max: 30 }) +
      field('website', 'Website', p.website, { max: 300, placeholder: 'https://' }) + field('country', 'Country', p.country, { max: 80 }) + field('city', 'City', p.city, { max: 80 }) +
      field('business_type', 'Business type', p.business_type, { max: 80 }) + field('email', 'Email (sign-in)', p.email, { readonly: true, hint: 'Contact LeadAI to change your sign-in email.' }) +
      '</div><p class="oa-err" id="profErr" role="alert"></p><div class="oa-actions" style="margin-top:12px"><button class="btn btn-primary btn-sm" type="submit">Save profile</button></div></form>';
    $('#profForm', root).onsubmit = async function (e) {
      e.preventDefault(); $('#profErr', root).textContent = '';
      var d = formData(this); delete d.email;
      try { await busy($('button[type=submit]', this), function () { return api('/api/partner/v1/profile', { method: 'PATCH', body: d }); }); toast('Profile saved'); await loadMe(); }
      catch (err) { $('#profErr', root).textContent = err.message; }
    };
  }

  async function viewSettings(root) {
    var p = S.me.partner || {};
    var methods = (S.me.program || {}).payout_methods || [];
    var po = p.payout_info || {}, tax = p.tax_info || {};
    var html = head('Settings & security', 'Payout details, notifications, password and two-factor authentication.');
    if (S.me.is_active_partner && has('profile.manage')) {
      html += '<form class="oa-card" id="payForm" novalidate><div class="oa-card-head"><div><div class="oa-card-title">Payout details</div><div class="oa-card-sub">' + (p.has_payout_info ? 'Saved: ' + esc(title(po.method)) + ' · ' + esc(po.account_number || po.iban || po.upi_id || po.paypal_email || '') + '. Enter new details to replace them.' : 'Required before your first payout.') + '</div></div></div><div class="oa-form-grid">' +
        field('method', 'Method', po.method || methods[0], { options: methods.map(function (m) { return [m, title(m)]; }) }) +
        field('account_name', 'Account holder name', po.account_name || '', { max: 120 }) +
        field('destination', 'Account number / IBAN / UPI ID / PayPal email', '', { max: 120, placeholder: 'Enter to update' }) +
        field('ifsc', 'IFSC / SWIFT / routing (bank only)', '', { max: 40 }) +
        field('bank_name', 'Bank name', po.bank_name || '', { max: 120 }) +
        field('tax_id', 'Tax ID (PAN / VAT / EIN)', '', { max: 60, placeholder: tax.tax_id ? 'Saved: ' + tax.tax_id : '' }) +
        '</div><p class="oa-err" id="payErr" role="alert"></p><div class="oa-actions" style="margin-top:12px"><button class="btn btn-primary btn-sm" type="submit">Save payout details</button><span class="oa-small oa-muted">Changes are logged and you\'ll get a confirmation email.</span></div></form>';
      html += '<section class="oa-card"><div class="oa-card-head"><div class="oa-card-title">Email notifications</div></div><label class="oa-switch"><input type="checkbox" id="emailPref"' + (((p.settings || {}).email_notifications !== false) ? ' checked' : '') + '><span class="oa-switch-text"><b>Email me about commissions, payouts and referrals</b><span>In-app notifications are always on.</span></span></label></section>';
    }
    html += '<form class="oa-card" id="pwForm" novalidate><div class="oa-card-head"><div class="oa-card-title">Change password</div></div><div class="oa-form-grid">' +
      field('current_password', 'Current password', '', { type: 'password' }) + field('new_password', 'New password', '', { type: 'password', hint: '8+ characters, an uppercase letter and a number.' }) +
      '</div><p class="oa-err" id="pwErr" role="alert"></p><div class="oa-actions" style="margin-top:12px"><button class="btn btn-primary btn-sm" type="submit">Update password</button></div></form>' +
      '<section class="oa-card" id="twofa">' + loading() + '</section>';
    root.innerHTML = html;
    var pf = $('#payForm', root);
    if (pf) pf.onsubmit = async function (e) {
      e.preventDefault(); $('#payErr', root).textContent = '';
      var d = formData(this), info = { method: d.method, account_name: d.account_name || undefined, bank_name: d.bank_name || undefined };
      if (!d.destination) { $('#payErr', root).textContent = 'Enter the account number, IBAN, UPI ID or PayPal email.'; return; }
      if (d.method === 'paypal') info.paypal_email = d.destination; else if (d.method === 'upi') info.upi_id = d.destination;
      else if (/^[A-Z]{2}\d{2}/i.test(d.destination)) info.iban = d.destination; else info.account_number = d.destination;
      if (d.ifsc) info[/^[A-Z]{4}0/i.test(d.ifsc) ? 'ifsc' : 'swift'] = d.ifsc;
      var body = { payout_info: info };
      if (d.tax_id) body.tax_info = { tax_id: d.tax_id };
      try { await busy($('button[type=submit]', this), function () { return api('/api/partner/v1/profile/payout', { method: 'PUT', body: body }); }); toast('Payout details saved'); await loadMe(); route(true); }
      catch (err) { $('#payErr', root).textContent = err.message; }
    };
    var ep = $('#emailPref', root);
    if (ep) ep.onchange = function () { api('/api/partner/v1/profile', { method: 'PATCH', body: { settings: { email_notifications: ep.checked } } }).then(function () { toast('Preference saved'); }, function (e) { toast(e.message, 'error'); ep.checked = !ep.checked; }); };
    $('#pwForm', root).onsubmit = async function (e) {
      e.preventDefault(); $('#pwErr', root).textContent = '';
      var d = formData(this), form = this;
      try { var r = await busy($('button[type=submit]', this), function () { return api('/api/auth/password/change', { method: 'POST', body: d }); }); toast(r.message || 'Password updated'); form.reset(); }
      catch (err) { $('#pwErr', root).textContent = err.message; }
    };
    render2fa($('#twofa', root));
  }
  async function render2fa(box) {
    try {
      var st = await api('/api/auth/2fa/status');
      box.innerHTML = '<div class="oa-card-head"><div><div class="oa-card-title">Two-factor authentication</div><div class="oa-card-sub">' + (st.totp_enabled ? 'On — sign-in needs a code from your authenticator app.' : 'Off — protect your payouts with an authenticator app.') + '</div></div>' +
        '<button type="button" class="btn btn-' + (st.totp_enabled ? 'secondary' : 'primary') + ' btn-sm" id="tfaBtn">' + (st.totp_enabled ? 'Turn off' : 'Turn on') + '</button></div>';
      $('#tfaBtn', box).onclick = async function () {
        if (st.totp_enabled) {
          var off = await modal({ title: 'Turn off two-factor authentication', submit: 'Turn off', danger: true, body: field('password', 'Current password', '', { type: 'password' }),
            onSubmit: function (f) { return api('/api/auth/2fa/disable', { method: 'POST', body: formData(f) }); } });
          if (off) { toast('Two-factor authentication is off'); render2fa(box); }
          return;
        }
        var s = await api('/api/auth/2fa/setup', { method: 'POST' });
        var on = await modal({ title: 'Turn on two-factor authentication', submit: 'Verify & turn on',
          body: '<p style="margin-top:0">Add this key to your authenticator app (Google Authenticator, 1Password, Authy…), then enter the 6-digit code it shows.</p>' + linkRow(s.secret, 'Setup key') + '<p class="oa-small oa-muted">Or open: <span class="oa-mono" style="word-break:break-all">' + esc(s.otpauth_url) + '</span></p>' + field('code', 'Code', '', { max: 6, placeholder: '123456' }),
          onOpen: bindCopy,
          onSubmit: function (f) { return api('/api/auth/2fa/enable', { method: 'POST', body: { secret: s.secret, code: formData(f).code } }); } });
        if (on) { toast('Two-factor authentication is on'); render2fa(box); }
      };
    } catch (e) { box.innerHTML = errorBox(e); }
  }

  function viewSupport(root) {
    var p = (S.me || {}).program || {};
    root.innerHTML = head('Help & support', 'Answers to common partner questions.') + '<div class="oa-grid oa-grid-2">' +
      card('How attribution works', '<p class="oa-small">When someone clicks your link, a cookie remembers you for <b>' + esc(p.attribution_window_days || 30) + ' days</b> (' + esc(p.attribution_model === 'last_touch' ? 'the last partner link clicked wins' : 'the first partner link clicked wins') + '). If they ' + (trialOn() ? 'sign up for a ' + esc(trialName()) : 'request a demo') + ' in that window, the new organization is attributed to you — once, permanently. Customers can also enter your referral code, or pay with your coupon.</p>') +
      card('When do I earn?', '<p class="oa-small">Commissions are created when a referred customer\'s payment is verified and confirmed by LeadAI — on the first payment and, for recurring rules, on renewals. Each one is <b>pending</b> during a <b>' + esc(p.commission_hold_days || 0) + '-day qualification period</b>, then <b>qualified → approved → payable</b>. A payout moves it to <b>processing</b> and then <b>paid</b>. If the customer is refunded, charges back or cancels during qualification, the commission is reversed (a paid one is deducted from your next payout).</p>') +
      card('Payouts', '<p class="oa-small">Request a payout of your available balance from the Wallet when it reaches <b>' + esc(money(p.min_payout)) + '</b>. LeadAI reviews and pays it to your saved payout method; you\'ll see the transfer reference here.</p>') +
      card('Rules', '<p class="oa-small">Self-referrals, coupon abuse, spam and misleading claims are not allowed and lead to reversal of commissions and suspension.</p>') +
      '</div>' + card('Contact the partner team', '<p class="oa-small">Questions about commissions, payouts or your account? Send us a message — choose “Partnership” as the topic.</p><div class="oa-actions"><a class="btn btn-primary btn-sm" href="/contact?topic=partnership" target="_blank" rel="noopener">Contact us ↗</a></div>');
  }

  // ════════════════════════════════════════════════════════
  //  ROUTING + SHELL
  // ════════════════════════════════════════════════════════
  var IC = {
    cart: '<circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"/>',
    brief: '<rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/>',
    dash: '<rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/>',
    link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    users: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
    coin: '<circle cx="12" cy="12" r="9"/><path d="M12 7v10M9 9.5h4.5a2 2 0 0 1 0 4H10a2 2 0 0 0 0 4h5"/>',
    wallet: '<path d="M20 12V8H6a2 2 0 0 1 0-4h12v4"/><path d="M4 6v12a2 2 0 0 0 2 2h14v-4"/><path d="M18 12a2 2 0 0 0 0 4h4v-4z"/>',
    send: '<line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>',
    chart: '<line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>',
    flag: '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><line x1="4" y1="22" x2="4" y2="15"/>',
    tag: '<path d="M20.59 13.41 13.42 20.58a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/>',
    image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/>',
    bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/>',
    key: '<circle cx="7.5" cy="15.5" r="5.5"/><path d="m21 2-9.6 9.6M15.5 7.5l3 3L22 7l-3-3"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/>',
    gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
    help: '<circle cx="12" cy="12" r="9"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3M12 17h.01"/>',
    doc: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>',
    tasks: '<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>'
  };
  // [route, label, icon, permission ('' = any signed-in partner), view, group, activeOnly]
  var ROUTES = [
    ['dashboard', 'Dashboard', 'dash', 'dashboard.view', viewDashboard, 'Overview', true],
    ['tasks', 'Tasks', 'tasks', '', viewTasks, 'Overview', true],
    ['analytics', 'Analytics', 'chart', 'analytics.view', viewAnalytics, 'Overview', true],
    ['sell', 'Sell LeadAI', 'cart', 'sales.view', viewSell, 'Sell', true],
    ['deals', 'Deals', 'brief', 'deals.manage', viewDeals, 'Sell', true],
    ['referrals', 'Referrals', 'link', 'referrals.view', viewReferrals, 'Growth', true],
    ['customers', 'Customers', 'users', 'customers.view', viewCustomers, 'Growth', true],
    ['campaigns', 'Campaigns', 'flag', 'campaigns.manage', viewCampaigns, 'Growth', true],
    ['coupons', 'Coupons', 'tag', 'coupons.manage', viewCoupons, 'Growth', true],
    ['marketing', 'Marketing center', 'image', 'marketing.access', viewMarketing, 'Growth', true],
    ['commissions', 'Commissions', 'coin', 'commissions.view', viewCommissions, 'Earnings', true],
    ['wallet', 'Wallet', 'wallet', 'wallet.view', viewWallet, 'Earnings', true],
    ['payouts', 'Payouts', 'send', 'wallet.view', viewPayouts, 'Earnings', true],
    ['application', 'Application', 'doc', '', viewApplication, 'Account', false],
    ['notifications', 'Notifications', 'bell', '', viewNotifications, 'Account', false],
    ['api', 'API access', 'key', 'api.access', viewApi, 'Account', true],
    ['profile', 'Profile', 'user', 'profile.manage', viewProfile, 'Account', true],
    ['settings', 'Settings & security', 'gear', '', viewSettings, 'Account', false],
    ['support', 'Help & support', 'help', '', viewSupport, 'Account', false]
  ];
  function allowed(r) {
    var active = S.me && S.me.is_active_partner;
    if (r[0] === 'application') return !active;
    if (r[6] && !active) return false;
    return !r[3] || has(r[3]);
  }
  function renderNav() {
    var groups = {};
    ROUTES.filter(allowed).forEach(function (r) { (groups[r[5]] = groups[r[5]] || []).push(r); });
    $('#oa-nav').innerHTML = Object.keys(groups).map(function (g) {
      return '<div class="oa-nav-group"><div class="oa-nav-label">' + esc(g) + '</div>' + groups[g].map(function (r) {
        var n = r[0] === 'tasks' ? (S.me.open_tasks || 0) : 0;
        return '<a href="#/' + r[0] + '" data-route="' + r[0] + '"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + IC[r[2]] + '</svg><span>' + esc(r[1]) + '</span>' +
          (n ? '<span class="oa-pill" data-tone="warning" style="margin-left:auto" aria-label="' + n + ' open">' + (n > 99 ? '99+' : n) + '</span>' : '') + '</a>';
      }).join('') + '</div>';
    }).join('');
  }
  var ctxByRoute = {};
  async function route(silent) {
    var h = location.hash.replace(/^#\/?/, '').split('?')[0];
    var seg = h.split('/'), key = seg[0], param = seg.slice(1).join('/');
    var first = ROUTES.filter(allowed)[0];
    var def = ROUTES.filter(function (r) { return r[0] === key; })[0];
    if (!key) { location.replace('#/' + (first ? first[0] : 'support')); return; }
    $$('#oa-nav a').forEach(function (a) { if (a.getAttribute('data-route') === key) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
    var main = $('#oa-content');
    if (!def) { main.innerHTML = empty('Page not found', 'This section does not exist.', '<div class="oa-actions"><a class="btn btn-primary btn-sm" href="#/' + esc(first ? first[0] : 'support') + '">Go back</a></div>'); return; }
    if (!allowed(def)) {
      main.innerHTML = errorBox({ status: 403, message: S.me.is_active_partner ? 'Your partner account does not include “' + def[1] + '”. Contact the LeadAI partner team to request access.' : 'This section opens once your partner application is approved.' });
      return;
    }
    $('#oa-crumb-title').textContent = def[1];
    document.title = def[1] + ' · Partner Portal · LeadAI';
    var box = document.createElement('div');
    if (!silent) { main.innerHTML = loading(); }
    var ctx = ctxByRoute[key] || (ctxByRoute[key] = {});
    try {
      await def[4](box, hashQuery(), ctx, param);
      main.innerHTML = ''; main.appendChild(box);
      if (!silent) { var h1 = $('h1', main); if (h1) h1.focus({ preventScroll: true }); }
    } catch (err) {
      main.innerHTML = errorBox(err);
      var rb = $('[data-retry]', main); if (rb) rb.onclick = function () { route(); };
    }
  }

  async function refreshBell() {
    try {
      var d = await api('/api/partner/v1/notifications?unread=true&limit=1');
      var b = $('#oa-bell-count'); b.textContent = d.unread > 99 ? '99+' : d.unread; b.hidden = !d.unread;
    } catch (e) { /* the view shows errors */ }
  }
  async function loadMe() {
    S.me = (await api('/api/partner/v1/me')).me;
    var p = S.me.partner || {};
    var name = p.company || p.name || S.me.name || S.me.email;
    $('#pp-name').textContent = name;
    $('#pp-sub').textContent = S.me.is_active_partner ? title(S.me.partner_type) + ' partner' : 'Application ' + title(S.me.application_status).toLowerCase();
    $('#oa-profile-name').textContent = S.me.name || S.me.email;
    $('#oa-avatar').textContent = (S.me.name || S.me.email || 'P').charAt(0).toUpperCase();
    $('#oa-menu-name').textContent = S.me.name || '';
    $('#oa-menu-email').textContent = S.me.email;
    renderNav();
  }

  async function init() {
    try { await Promise.all([loadMe(), loadTrial()]); }
    catch (e) {
      $('#oa-gate').innerHTML = '<div class="oa-gate-inner"><div class="oa-card">' + errorBox(e) + '<div class="oa-actions" style="justify-content:center"><a class="btn btn-secondary btn-sm" href="/login?partner=1">Sign in again</a></div></div></div>';
      var rb = $('[data-retry]', $('#oa-gate')); if (rb) rb.onclick = function () { location.reload(); };
      return;
    }
    $('#oa-gate').hidden = true; $('#oa-app').hidden = false;
    if (S.me.impersonated_by) {
      var bar = document.createElement('div');
      bar.className = 'pp-banner'; bar.setAttribute('role', 'status');
      bar.style.cssText = 'position:sticky;top:0;z-index:70;margin:0;border-radius:0;display:flex;gap:12px;align-items:center;justify-content:center;flex-wrap:wrap';
      bar.innerHTML = '<span>You are viewing the Partner Portal <b>as ' + esc((S.me.partner || {}).company || S.me.name || S.me.email) + '</b> (Super Admin ' + esc(S.me.impersonated_by) + '). Everything you do is recorded.</span><button type="button" class="btn btn-secondary btn-sm" id="ppExitImp">Exit to Super Admin</button>';
      $('.oa-main').insertBefore(bar, $('.oa-main').firstChild);
      $('#ppExitImp').onclick = async function () {
        try { var r = await fetch('/api/super-admin/impersonate/exit', { method: 'POST', credentials: 'same-origin' }); var d = await r.json(); location.href = d.redirect || '/superadmin'; }
        catch (e) { location.href = '/login?superadmin=1'; }
      };
    }
    var close = function () { $('#oa-sidebar').classList.remove('open'); $('#oa-scrim').classList.remove('open'); $('#oa-menu-btn').setAttribute('aria-expanded', 'false'); };
    $('#oa-menu-btn').onclick = function () { var o = $('#oa-sidebar').classList.toggle('open'); $('#oa-scrim').classList.toggle('open', o); this.setAttribute('aria-expanded', String(o)); };
    $('#oa-scrim').onclick = close;
    $('#oa-nav').addEventListener('click', function (e) { if (e.target.closest('a')) close(); });
    var pop = $('#oa-profile-pop');
    $('#oa-profile-btn').onclick = function (e) { e.stopPropagation(); pop.hidden = !pop.hidden; this.setAttribute('aria-expanded', String(!pop.hidden)); };
    document.addEventListener('click', function (e) { if (!e.target.closest('.oa-rel')) { pop.hidden = true; $('#oa-profile-btn').setAttribute('aria-expanded', 'false'); } });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') { pop.hidden = true; close(); } });
    $('#oa-logout').onclick = async function () { try { await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' }); } catch (e) {} location.href = '/login?partner=1'; };
    window.addEventListener('hashchange', function () { route(); });
    route();
    refreshBell();
    setInterval(function () { if (!document.hidden) refreshBell(); }, 60000);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
