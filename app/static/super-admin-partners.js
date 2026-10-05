/* ============================================================
   LeadAI — Super Admin › Partner Management
   Registered into the existing Super Admin portal through
   window.SAKit (same UI kit, routing, modals and audit trail).
   Backend: /api/super-admin/partners/* (partners.view / .manage).
   ============================================================ */
(function () {
  'use strict';
  var K = window.SAKit;
  if (!K) return;
  var api = K.api, qs = K.qs, esc = K.esc, pill = K.pill, $ = K.$, $$ = K.$$;
  var B = '/api/super-admin/partners';
  var META = null;

  // plain vertical list for history / keys / coupons inside cards and drawers
  (function () { var st = document.createElement('style'); st.textContent = '.pt-list{list-style:none;padding:0;margin:6px 0 12px;display:grid;gap:8px}.pt-list li{line-height:1.5}'; document.head.appendChild(st); })();
  Object.assign(K.STATUS_TONE, { changes_requested: 'warning', requested: 'warning', reversed: 'danger',
    signed_up: 'info', customer: 'success', affiliate: 'info', reseller: 'primary', partner: 'primary',
    qualified: 'info', payable: 'success', under_review: 'warning', subscription: 'primary', payment: 'primary',
    invalid: 'danger', partially_refunded: 'warning', clawback: 'danger' });
  Object.assign(K.PILL_LABEL, { changes_requested: 'Changes requested', signed_up: 'Signed up', under_review: 'Under review',
    subscription: 'Checkout started', payment: 'Payment received', partially_refunded: 'Partially refunded' });
  var COMMISSION_FILTER = [['', 'All statuses'], ['pending', 'Pending (qualifying)'], ['qualified', 'Qualified'], ['approved', 'Approved'],
    ['payable', 'Payable'], ['processing', 'Processing'], ['paid', 'Paid'], ['reversed', 'Reversed']];
  var STAGE_FILTER = [['', 'All stages'], ['signed_up', 'Signed up'], ['demo', 'Free trial'], ['subscription', 'Checkout started'],
    ['payment', 'Payment received'], ['customer', 'Customer']];

  async function meta() { if (!META) META = await api(B + '/meta'); return META; }
  function money(v, cur) { return K.fmtMoney(v, cur || 'USD'); }
  function balancesHtml(b) {
    var keys = Object.keys(b || {});
    if (!keys.length) return '<span class="sa-muted">No earnings yet</span>';
    return keys.map(function (c) { var x = b[c]; return '<div><b>' + esc(c) + '</b> · pending ' + esc(money(x.pending, c)) + ' · available ' + esc(money(x.available, c)) + ' · in payout ' + esc(money(x.reserved, c)) + ' · paid ' + esc(money(x.paid, c)) + '</div>'; }).join('');
  }
  function field(name, label, value, o) {
    o = o || {};
    var id = 'pf_' + name + Math.random().toString(36).slice(2, 5), input;
    if (o.options) input = '<select class="form-select" id="' + id + '" name="' + name + '">' + o.options.map(function (op) { var v = Array.isArray(op) ? op[0] : op, l = Array.isArray(op) ? op[1] : K.titleCase(op); return '<option value="' + esc(v) + '"' + (String(v) === String(value == null ? '' : value) ? ' selected' : '') + '>' + esc(l) + '</option>'; }).join('') + '</select>';
    else if (o.type === 'textarea') input = '<textarea class="form-textarea" id="' + id + '" name="' + name + '" rows="' + (o.rows || 3) + '" style="min-height:70px">' + esc(value == null ? '' : value) + '</textarea>';
    else if (o.type === 'checkbox') return '<label class="sa-check"><input type="checkbox" name="' + name + '"' + (value ? ' checked' : '') + '> ' + esc(label) + '</label>';
    else input = '<input class="form-input" id="' + id + '" name="' + name + '" type="' + (o.type || 'text') + '" value="' + esc(value == null ? '' : value) + '"' + (o.step ? ' step="' + o.step + '"' : '') + (o.min != null ? ' min="' + o.min + '"' : '') + (o.placeholder ? ' placeholder="' + esc(o.placeholder) + '"' : '') + ' autocomplete="off">';
    return '<div class="sa-field"><label for="' + id + '">' + esc(label) + '</label>' + input + (o.hint ? '<span class="hint">' + esc(o.hint) + '</span>' : '') + '</div>';
  }
  function permChecks(m, selected, type) {
    return '<div class="sa-form-grid">' + Object.keys(m.permissions).map(function (p) {
      var resellerOnly = m.reseller_only.indexOf(p) >= 0;
      return '<label class="sa-check"><input type="checkbox" name="perm" value="' + esc(p) + '"' + (selected.indexOf(p) >= 0 ? ' checked' : '') + '> ' + esc(m.permissions[p]) + (resellerOnly ? ' <span class="sa-small sa-muted">(resellers)</span>' : '') + '</label>';
    }).join('') + '</div>' + (type ? '' : '');
  }

  // ════════════════ main view (tabs) ════════════════
  async function viewPartners(root, q) {
    var m = await meta();
    var ov = (await api(B + '/overview')).overview;
    var totals = ov.commission_totals || {}, cur = Object.keys(totals)[0] || 'USD', t = totals[cur] || {};
    root.innerHTML = K.header('Partners & resellers', 'Applications, partners, commissions and payouts. Customers they bring are ordinary organizations; commissions come from confirmed subscription payments.') +
      '<div class="sa-grid sa-kpis">' +
      K.kpi('Applications to review', K.fmtN(ov.applications_pending), K.fmtN(ov.applications_changes) + ' awaiting changes', { href: '#/partners?tab=applications&status=pending', icon: 'list', tone: ov.applications_pending ? 'warn' : '' }) +
      K.kpi('Active partners', K.fmtN(ov.partners_active), K.fmtN(ov.affiliates) + ' affiliates · ' + K.fmtN(ov.resellers) + ' resellers', { href: '#/partners?tab=partners&status=active', icon: 'handshake' }) +
      K.kpi('Referred customers', K.fmtN(ov.customers), K.fmtN(ov.referrals) + ' referrals in total', { href: '#/partners?tab=referrals&stage=customer', icon: 'users' }) +
      K.kpi('Commission (' + cur + ')', money((t.pending || 0) + (t.qualified || 0) + (t.approved || 0) + (t.payable || 0) + (t.processing || 0), cur), 'unpaid · ' + money(t.paid, cur) + ' paid · ' + K.fmtN(ov.payouts_open) + ' payouts open', { href: '#/partners?tab=commissions', icon: 'coin' }) +
      '</div><div id="ptTabs" style="margin-top:14px"></div>';
    K.tabs($('#ptTabs', root), [['applications', 'Applications'], ['analytics', 'Analytics'], ['activity', 'Activity log'], ['partners', 'Partners'], ['tasks', 'Tasks'], ['deals', 'Deals'], ['referrals', 'Customers & referrals'],
      ['commissions', 'Commissions'], ['reversals', 'Refunds & reversals'], ['wallets', 'Wallets'], ['payouts', 'Payouts'],
      ['fraud', 'Fraud review'], ['rules', 'Commission rules'], ['tiers', 'Tiers'], ['pricing', 'Partner pricing'],
      ['coupons', 'Coupons'], ['assets', 'Marketing assets'], ['settings', 'Program settings']],
      q.tab || 'applications', function (key, el) { return TABS[key](el, q, m); });
  }

  var TABS = {};

  // ── Activity log: everything partners do ────────────────────
  var KIND_LABEL = { auth: 'Sign-in', request: 'Portal / API request', action: 'Action' };
  function activityColumns(withPartner) {
    return [{ label: 'When', render: function (a) { return esc(K.fmtDT(a.at)); } }]
      .concat(withPartner ? [{ label: 'Partner', render: function (a) { return a.partner_id ? '<a class="sa-link" href="#/partners/' + esc(a.partner_id) + '?tab=activity">' + esc(a.partner_name || K.short(a.partner_id)) + '</a>' : '<span class="sa-muted">' + esc(a.email || 'applicant') + '</span>'; } }] : [])
      .concat([
        { label: 'Type', render: function (a) { return K.badge(KIND_LABEL[a.kind] || a.kind, a.kind === 'action' ? 'info' : a.kind === 'auth' ? 'primary' : 'neutral'); } },
        { label: 'What', render: function (a) { return '<span class="sa-mono">' + esc(a.action) + '</span>' + (a.status ? ' <span class="sa-small sa-muted">' + esc(a.status) + '</span>' : '') + (a.success === false ? ' ' + pill('failed') : '') + (a.via_api_key ? ' ' + K.badge('API key', 'warning') : ''); } },
        { label: 'By', render: function (a) { return esc(a.actor || a.email || 'system'); } },
        { label: 'Details', render: function (a) { return '<span class="sa-small sa-mono">' + esc(K.short(JSON.stringify(a.details || {}), 140)) + '</span>' + (a.duration_ms != null ? ' <span class="sa-small sa-muted">' + esc(a.duration_ms) + ' ms</span>' : ''); } },
        { label: 'Network', render: function (a) { return '<span class="sa-small sa-mono" title="' + esc(a.user_agent || '') + '">' + esc(a.ip_hash || '—') + '</span>'; } }
      ]);
  }
  var ACT_FILTERS = [{ key: 'q', label: 'Search action / path…' }, { key: 'email', label: 'Email' },
    { key: 'kind', type: 'select', label: 'Type', options: [['', 'All types'], ['auth', 'Sign-ins'], ['request', 'Requests'], ['action', 'Actions']] },
    { key: 'success', type: 'select', label: 'Result', options: [['', 'Any result'], ['false', 'Failed / denied'], ['true', 'Succeeded']] },
    { key: 'via_api_key', type: 'select', label: 'Channel', options: [['', 'Portal & API'], ['true', 'API key only'], ['false', 'Portal only']] },
    { key: 'from', type: 'date', label: 'From' }, { key: 'to', type: 'date', label: 'To' }];
  function actInitial(q) {
    q = q || {};
    return { kind: ['auth', 'request', 'action'].indexOf(q.kind) >= 0 ? q.kind : '', success: q.success === 'true' || q.success === 'false' ? q.success : '',
      from: /^\d{4}-\d{2}-\d{2}$/.test(q.from || '') ? q.from : '' };
  }
  function actParams(p, extra) { return qs(Object.assign({ page: p.page, limit: p.limit, q: p.q, email: p.email, kind: p.kind, success: p.success, via_api_key: p.via_api_key, from: p.from, to: p.to }, extra || {})); }

  TABS.activity = function (el, q, m) {
    el.innerHTML = '<div id="acKpi"></div><div class="sa-row" style="justify-content:flex-end"><a class="btn btn-secondary btn-sm" id="acCsv" download>⤓ Export CSV</a></div><div id="acList"></div>' +
      '<div class="sa-card" style="margin-top:14px"><h3>Live partner sessions</h3><p class="sa-small sa-muted">Signed-in Partner Portal sessions. Ending one signs that device out immediately.</p><div id="acSess"></div></div>';
    var lv = K.listView($('#acList', el), {
      url: function (p) { $('#acCsv', el).href = B + '/activity.csv' + actParams(p, { page: null, limit: null }); return B + '/activity' + actParams(p); },
      limit: 50, filters: ACT_FILTERS, key: 'partner_activity', initial: actInitial(q),
      onData: function (d) { var s = d.summary_24h || {}, day = '#/partners?tab=activity&from=' + isoDay(new Date(Date.now() - 864e5));
        $('#acKpi', el).innerHTML = '<div class="sa-grid sa-kpis">' + K.kpi('Active partners · 24h', K.fmtN(s.active_partners), 'with any activity', { href: day }) + K.kpi('Sign-ins · 24h', K.fmtN(s.auth), K.fmtN(s.failed_logins) + ' failed', { href: day + '&kind=auth', tone: s.failed_logins ? 'warn' : '' }) +
          K.kpi('Requests · 24h', K.fmtN(s.request), K.fmtN(s.denied) + ' denied', { href: day + '&kind=request', tone: s.denied ? 'warn' : '' }) + K.kpi('Actions · 24h', K.fmtN(s.action), 'audited events', { href: day + '&kind=action' }) + '</div>';
        K.bindGo($('#acKpi', el)); },
      columns: activityColumns(true), empty: { title: 'No partner activity', desc: 'Sign-ins, portal and API requests and every partner action appear here.' } });
    sessionsList($('#acSess', el), null, m);
    return lv;
  };
  function sessionsList(el, partnerId, m) {
    return K.listView(el, { url: function (p) { return B + '/sessions' + qs({ page: p.page, limit: p.limit, partner_id: partnerId, active: p.active }); },
      initial: { active: 'true' }, filters: [{ key: 'active', type: 'select', label: 'Show', options: [['true', 'Active sessions'], ['false', 'All (incl. ended)']] }],
      columns: [{ label: 'Partner', render: function (s) { return s.partner_id ? '<a class="sa-link" href="#/partners/' + esc(s.partner_id) + '">' + esc(s.partner_name || '') + '</a>' : esc(s.email); } },
        { label: 'Signed in', render: function (s) { return esc(K.fmtDT(s.created_at)); } }, { label: 'Last active', render: function (s) { return esc(K.ago(s.last_active_at)); } },
        { label: 'Device', render: function (s) { return '<span class="sa-small">' + esc(K.short(s.user_agent || '', 60)) + '</span>'; } },
        { label: 'Network', render: function (s) { return '<span class="sa-small sa-mono">' + esc(s.ip_hash || '—') + '</span>'; } },
        { label: 'Status', render: function (s) { return s.revoked_at ? pill('revoked', 'Ended') + '<div class="sa-small sa-muted">' + esc(s.revoked_by || '') + '</div>' : pill('active'); } },
        { label: '', cls: 'num', render: function (s) { return !s.revoked_at && m.can_manage ? '<button type="button" class="btn btn-danger btn-xs" data-rv>End session</button>' : ''; } }],
      bindRow: function (tr, s, reload) { var b = $('[data-rv]', tr); if (b) b.onclick = async function () {
        var c = await K.confirmDialog({ title: 'End this session?', message: 'That device is signed out of the Partner Portal immediately.', confirmLabel: 'End session', reason: 'optional' });
        if (!c) return; try { await api(B + '/sessions/' + s.id + '/revoke', { method: 'POST', body: { reason: c.reason } }); K.toast('Session ended'); reload(); } catch (e) { K.toast(e.message, 'error'); } }; },
      empty: { title: 'No sessions' } });
  }

  // ── Deals: partner deal registrations ───────────────────────
  var DEAL_LABEL = { registered: 'Awaiting review', approved: 'Approved', rejected: 'Rejected', won: 'Won', lost: 'Lost', expired: 'Expired' };
  Object.assign(K.STATUS_TONE, { registered: 'warning', won: 'success', lost: 'neutral', expired: 'neutral' });
  function dealColumns(withPartner, m) {
    return [{ label: 'Prospect', render: function (d) { return '<span class="cell-main">' + esc(d.company) + '</span><div class="sa-small sa-muted">' + esc([d.contact_name, d.contact_email, d.contact_phone].filter(Boolean).join(' · ')) + '</div>'; } }]
      .concat(withPartner ? [{ label: 'Partner', render: function (d) { return '<a class="sa-link" href="#/partners/' + esc(d.partner_id) + '">' + esc(d.partner_name || '') + '</a>'; } }] : [])
      .concat([{ label: 'Plan / value', render: function (d) { return esc(d.expected_plan || '—') + (d.expected_value != null ? '<div class="sa-small">' + esc(money(d.expected_value, d.currency)) + '</div>' : ''); } },
        { label: 'Status', render: function (d) { return pill(d.status, DEAL_LABEL[d.status]) + (d.conflict ? ' ' + K.badge(d.conflict.kind === 'existing_account' ? 'Existing account' : 'Competing claim', 'danger') : '') + (d.protected_until && d.status === 'approved' ? '<div class="sa-small sa-muted">protected until ' + esc(K.fmtDate(d.protected_until)) + '</div>' : ''); } },
        { label: 'Signed up', render: function (d) { return d.referral_stage ? pill(d.referral_stage) : '<span class="sa-muted">—</span>'; } },
        { label: 'Notes', render: function (d) { return '<span class="sa-small">' + esc(K.short(d.notes || '', 90)) + '</span>' + (d.review_note ? '<div class="sa-small sa-muted">Review: ' + esc(d.review_note) + '</div>' : ''); } },
        { label: 'Registered', render: function (d) { return esc(K.fmtDT(d.created_at)); } },
        { label: '', cls: 'num', render: function (d) {
          if (!m.can_manage) return '';
          var b = [];
          if (d.status === 'registered') b.push('<button type="button" class="btn btn-primary btn-xs" data-dd="approve">Approve</button>');
          if (d.status === 'registered' || d.status === 'approved') b.push('<button type="button" class="btn btn-secondary btn-xs" data-dd="won">Won</button><button type="button" class="btn btn-secondary btn-xs" data-dd="lost">Lost</button><button type="button" class="btn btn-danger btn-xs" data-dd="reject">Reject</button>');
          return '<div class="row-actions">' + b.join('') + '</div>'; } }]);
  }
  function bindDeal(tr, d, reload) {
    $$('[data-dd]', tr).forEach(function (b) { b.onclick = async function () {
      var a = b.getAttribute('data-dd');
      var c = await K.confirmDialog({ title: { approve: 'Approve deal', reject: 'Reject deal', won: 'Mark deal won', lost: 'Mark deal lost' }[a] + ' — ' + d.company,
        message: { approve: 'The partner\'s claim on this prospect is protected for the configured period.', reject: 'The partner is told why.', won: 'Records the deal as won.', lost: 'Closes the deal.' }[a],
        confirmLabel: K.titleCase(a), danger: a === 'reject', reason: a === 'reject' ? 'required' : 'optional' });
      if (!c) return;
      try { await api(B + '/deals/' + d.id + '/' + a, { method: 'POST', body: { note: c.reason } }); K.toast('Deal updated'); reload(); } catch (e) { K.toast(e.message, 'error'); }
    }; });
  }
  TABS.deals = function (el, q, m) {
    el.innerHTML = '<div class="sa-chips" id="dlChips"></div><div id="dlList"></div>';
    var current = q.status || '', lv;
    lv = K.listView($('#dlList', el), {
      url: function (p) { return B + '/deals' + qs({ page: p.page, limit: p.limit, q: p.q, status: p.status, conflict: p.conflict }); },
      initial: { status: current }, filters: [{ key: 'q', label: 'Search company or contact…' }, { key: 'conflict', type: 'select', label: 'Conflicts', options: [['', 'All deals'], ['true', 'With a competing claim']] }],
      onData: function (d) {
        var c = d.counts || {}, pv = d.open_pipeline_value || {};
        $('#dlChips', el).innerHTML = [['', 'All']].concat(Object.keys(DEAL_LABEL).map(function (s) { return [s, DEAL_LABEL[s]]; })).map(function (x) {
          var n = x[0] ? (c[x[0]] || 0) : Object.keys(c).reduce(function (a, k) { return a + c[k]; }, 0);
          return '<button type="button" class="sa-chip' + (current === x[0] ? ' active' : '') + '" data-s="' + x[0] + '">' + esc(x[1]) + '<b>' + K.fmtN(n) + '</b></button>';
        }).join('') + (Object.keys(pv).length ? ' <span class="sa-small sa-muted">Open pipeline: ' + Object.keys(pv).map(function (k) { return esc(money(pv[k], k)); }).join(' · ') + '</span>' : '');
        $$('[data-s]', el).forEach(function (b) { b.onclick = function () { current = b.getAttribute('data-s'); lv.state.status = current; lv.state.page = 1; lv.reload(); }; });
      },
      columns: dealColumns(true, m), bindRow: bindDeal,
      empty: { title: 'No deals', desc: 'Prospects that partners register appear here for review.' } });
  };

  // ── Tasks: work assigned to partners ────────────────────────
  var TASK_LABEL = { open: 'To do', in_progress: 'In progress', submitted: 'Ready for review', done: 'Done', cancelled: 'Cancelled' };
  Object.assign(K.STATUS_TONE, { open: 'warning', in_progress: 'primary', submitted: 'info', done: 'success' });
  var SECTIONS = [['', 'None'], ['sell', 'Sell LeadAI'], ['deals', 'Deals'], ['referrals', 'Referrals'], ['customers', 'Customers'], ['campaigns', 'Campaigns'], ['coupons', 'Coupons'],
    ['marketing', 'Marketing center'], ['commissions', 'Commissions'], ['wallet', 'Wallet'], ['payouts', 'Payouts'], ['analytics', 'Analytics'], ['profile', 'Profile'], ['api', 'API access'], ['settings', 'Settings & security']];
  // the calendar day picked (no timezone shift)
  function dueDay(t) { if (!t.due_date) return t.due_at ? K.fmtDate(t.due_at) : '—'; var x = t.due_date.split('-'); return new Date(+x[0], +x[1] - 1, +x[2]).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }); }
  function taskFields(t) {
    t = t || {};
    return field('title', 'Task *', t.title || '', { placeholder: 'e.g. Sell 3 Starter plans this month' }) +
      field('details', 'Details', t.details || '', { type: 'textarea', rows: 4, hint: 'What to do and how you will judge it done. The partner sees this.' }) +
      '<div class="sa-form-grid">' + field('due_at', 'Due date', t.due_date || '', { type: 'date', min: isoDay(new Date()) }) +
      field('priority', 'Priority', t.priority || 'normal', { options: [['normal', 'Normal'], ['high', 'High'], ['low', 'Low']] }) +
      field('section', 'Link to portal page', t.section || '', { options: SECTIONS, hint: 'Shows an “Open …” button on the task.' }) + '</div>';
  }
  function taskBody(fd) { return { title: fd.get('title'), details: fd.get('details') || null, due_at: fd.get('due_at') || null, priority: fd.get('priority'), section: fd.get('section') || null }; }
  /** assign dialog; partner = {id, name} to lock the recipient */
  async function assignTask(partner) {
    var who = partner ? '<p class="sa-small">Assigning to <b>' + esc(partner.name) + '</b>.</p>' :
      field('audience', 'Assign to', 'partner', { options: [['partner', 'One partner'], ['all', 'All active partners'], ['affiliate', 'All active affiliates'], ['reseller', 'All active resellers']] }) +
      '<div id="tkPick"><div class="sa-field"><label for="tkQ">Partner *</label><input class="form-input" id="tkQ" type="search" placeholder="Search name, company or email…" autocomplete="off">' +
      '<select class="form-select" name="partner_id" id="tkSel" size="5" style="margin-top:6px;height:auto"></select></div></div>';
    return K.openModal({ title: 'Assign a task', size: 'lg', submitLabel: 'Assign task',
      body: who + taskFields() + field('email', 'Also email the partner(s)', true, { type: 'checkbox' }),
      onOpen: function (form) {
        if (partner) return;
        var sel = $('#tkSel', form), qn = $('#tkQ', form), seq = 0, timer;
        async function load() {
          var my = ++seq;
          try { var d = await api(B + qs({ status: 'active', q: qn.value.trim(), limit: 50, sort: 'company' }));
            if (my !== seq) return;
            sel.innerHTML = (d.items || []).map(function (p) { return '<option value="' + esc(p.id) + '">' + esc((p.company || p.name) + ' — ' + p.email + ' (' + p.partner_type + ')') + '</option>'; }).join('') || '<option value="" disabled>No active partners match</option>';
          } catch (e) { sel.innerHTML = '<option value="" disabled>' + esc(e.message) + '</option>'; }
        }
        qn.oninput = function () { clearTimeout(timer); timer = setTimeout(load, 250); };
        $('[name=audience]', form).onchange = function () { $('#tkPick', form).hidden = this.value !== 'partner'; };
        load();
      },
      onSubmit: function (f, fd) {
        var body = Object.assign(taskBody(fd), { email: !!fd.get('email') });
        if (partner) { body.audience = 'partner'; body.partner_id = partner.id; }
        else { body.audience = fd.get('audience'); body.partner_id = fd.get('partner_id') || null;
          if (body.audience === 'partner' && !body.partner_id) throw new Error('Choose a partner from the list.'); }
        if (!body.title || !body.title.trim()) throw new Error('Give the task a title.');
        return api(B + '/tasks', { method: 'POST', body: body });
      } });
  }
  function taskColumns(withPartner, m) {
    return [{ label: 'Task', render: function (t) { return '<span class="cell-main">' + esc(t.title) + '</span>' + (t.priority === 'high' ? ' ' + K.badge('High', 'danger') : t.priority === 'low' ? ' ' + K.badge('Low', 'neutral') : '') + (t.overdue ? ' ' + K.badge('Overdue', 'danger') : '') +
      (t.details ? '<div class="sa-small sa-muted">' + esc(K.short(t.details, 100)) + '</div>' : ''); } }]
      .concat(withPartner ? [{ label: 'Partner', render: function (t) { return '<a class="sa-link" href="#/partners/' + esc(t.partner_id) + '?tab=tasks">' + esc(t.partner_name || K.short(t.partner_id)) + '</a>'; } }] : [])
      .concat([{ label: 'Due', render: function (t) { return t.due_at ? esc(dueDay(t)) : '<span class="sa-muted">—</span>'; } },
        { label: 'Status', render: function (t) { return pill(t.status, TASK_LABEL[t.status]) + (t.review_note && t.status !== 'submitted' ? '<div class="sa-small sa-muted">' + esc(K.short(t.review_note, 80)) + '</div>' : ''); } },
        { label: 'Partner\'s report', render: function (t) { return t.submission_note ? '<span class="sa-small">' + esc(K.short(t.submission_note, 120)) + '</span>' + (t.submitted_at ? '<div class="sa-small sa-muted">' + esc(K.fmtDT(t.submitted_at)) + '</div>' : '') : '<span class="sa-muted">—</span>'; } },
        { label: 'Assigned', render: function (t) { return esc(K.fmtDate(t.created_at)) + '<div class="sa-small sa-muted">' + esc(t.assigned_by || '') + '</div>'; } },
        { label: '', cls: 'num', render: function (t) {
          var b = ['<button type="button" class="btn btn-ghost btn-xs" data-tk="view">View</button>'];
          if (m.can_manage) {
            if (t.status === 'submitted') b.push('<button type="button" class="btn btn-primary btn-xs" data-tk="approve">Approve</button><button type="button" class="btn btn-secondary btn-xs" data-tk="send_back">Send back</button>');
            if (t.status === 'open' || t.status === 'in_progress') b.push('<button type="button" class="btn btn-secondary btn-xs" data-tk="edit">Edit</button>');
            if (t.status === 'open' || t.status === 'in_progress' || t.status === 'submitted') b.push('<button type="button" class="btn btn-danger btn-xs" data-tk="cancel">Cancel</button>');
          }
          return '<div class="row-actions">' + b.join('') + '</div>'; } }]);
  }
  function bindTask(tr, t, reload) {
    $$('[data-tk]', tr).forEach(function (b) { b.onclick = async function () {
      var a = b.getAttribute('data-tk');
      try {
        if (a === 'view') {
          await K.openModal({ title: t.title, size: 'lg', submitLabel: null, cancelLabel: 'Close',
            body: '<dl class="sa-kv"><dt>Partner</dt><dd>' + esc(t.partner_name || t.partner_id) + '</dd><dt>Status</dt><dd>' + pill(t.status, TASK_LABEL[t.status]) + '</dd><dt>Due</dt><dd>' + esc(dueDay(t)) + '</dd><dt>Priority</dt><dd>' + esc(K.titleCase(t.priority || 'normal')) + '</dd>' +
              '<dt>Assigned</dt><dd>' + esc(K.fmtDT(t.created_at) + ' by ' + (t.assigned_by || '')) + (t.audience && t.audience !== 'partner' ? ' · ' + esc(K.titleCase(t.audience)) + ' broadcast' : '') + '</dd></dl>' +
              (t.details ? '<div class="sa-card" style="white-space:pre-wrap;margin:10px 0">' + esc(t.details) + '</div>' : '') +
              (t.submission_note ? '<h4 style="margin:10px 0 4px">Partner\'s report</h4><div class="sa-card" style="white-space:pre-wrap">' + esc(t.submission_note) + '</div>' : '') +
              '<h4 style="margin:12px 0 4px">History</h4><ul class="pt-list">' + (t.history || []).slice().reverse().map(function (h) { return '<li>' + pill(h.status, TASK_LABEL[h.status]) + ' <span class="sa-small sa-muted">' + esc(K.fmtDT(h.at) + ' · ' + (h.by || '')) + '</span>' + (h.note ? '<div class="sa-small" style="white-space:pre-wrap">' + esc(h.note) + '</div>' : '') + '</li>'; }).join('') + '</ul>' });
          return;
        }
        if (a === 'edit') {
          var ok = await K.openModal({ title: 'Edit task', size: 'lg', submitLabel: 'Save', body: taskFields(t),
            onSubmit: function (f, fd) { var body = taskBody(fd); if (!body.title || !body.title.trim()) throw new Error('Give the task a title.'); return api(B + '/tasks/' + t.id, { method: 'PATCH', body: body }); } });
          if (ok) { K.toast('Task updated — the partner was notified'); reload(); }
          return;
        }
        var c = await K.confirmDialog({ title: { approve: 'Approve task', send_back: 'Send task back', cancel: 'Cancel task' }[a] + ' — ' + t.title,
          message: { approve: 'Marks the task done. The partner is notified.', send_back: 'The task goes back to the partner with your feedback.', cancel: 'The task is withdrawn. The partner is told why.' }[a],
          confirmLabel: { approve: 'Approve', send_back: 'Send back', cancel: 'Cancel task' }[a], danger: a === 'cancel', reason: a === 'approve' ? 'optional' : 'required' });
        if (!c) return;
        await api(B + '/tasks/' + t.id + '/' + a, { method: 'POST', body: { note: c.reason } });
        K.toast({ approve: 'Task approved', send_back: 'Sent back to the partner', cancel: 'Task cancelled' }[a]); reload(); countPending();
      } catch (e) { K.toast(e.message, 'error'); }
    }; });
  }
  function taskList(el, m, partner, status) {
    el.innerHTML = '<div class="sa-row" style="justify-content:space-between;flex-wrap:wrap;gap:8px"><div class="sa-chips" id="tkChips"></div>' + (m.can_manage ? '<button type="button" class="btn btn-primary btn-sm" id="tkNew">+ Assign task</button>' : '') + '</div><div id="tkList"></div>';
    var current = status || '', lv;
    lv = K.listView($('#tkList', el), {
      url: function (p) { return B + '/tasks' + qs({ page: p.page, limit: p.limit, q: p.q, status: current === 'overdue' ? null : current, overdue: current === 'overdue' ? 'true' : null, partner_id: partner && partner.id }); },
      filters: [{ key: 'q', label: 'Search task title…' }],
      onData: function (d) {
        var c = d.counts || {};
        $('#tkChips', el).innerHTML = [['', 'All'], ['submitted', 'Ready for review'], ['open', 'To do'], ['in_progress', 'In progress'], ['overdue', 'Overdue'], ['done', 'Done'], ['cancelled', 'Cancelled']].map(function (x) {
          var n = x[0] ? (c[x[0]] || 0) : ['open', 'in_progress', 'submitted', 'done', 'cancelled'].reduce(function (a, k) { return a + (c[k] || 0); }, 0);
          return '<button type="button" class="sa-chip' + (current === x[0] ? ' active' : '') + '" data-s="' + x[0] + '">' + esc(x[1]) + '<b>' + K.fmtN(n) + '</b></button>';
        }).join('');
        $$('[data-s]', el).forEach(function (b) { b.onclick = function () { current = b.getAttribute('data-s'); lv.state.page = 1; lv.reload(); }; });
      },
      columns: taskColumns(!partner, m), bindRow: bindTask,
      empty: { title: 'No tasks', desc: partner ? 'Assign this partner a task — they see it in their portal and get a notification.' : 'Assign work to one partner or to all of them. Partners report back here for your review.' } });
    var nb = $('#tkNew', el); if (nb) nb.onclick = function () { assignTask(partner).then(function (r) { if (r) { K.toast(r.message || 'Task assigned'); lv.reload(); } }); };
    return lv;
  }
  TABS.tasks = function (el, q, m) { return taskList(el, m, null, q.status); };

  function isoDay(d) { return d.toISOString().slice(0, 10); }
  TABS.analytics = async function (el, q) {
    var st = el._st || (el._st = { from: isoDay(new Date(Date.now() - 29 * 864e5)), to: isoDay(new Date()), unit: 'day', partner_id: q.partner || '', campaign_id: '' });
    var partners = (await api(B + qs({ limit: 200, sort: 'name' }))).items || [];
    var params = function () { return qs({ from: st.from, to: st.to, unit: st.unit, partner_id: st.partner_id, campaign_id: st.campaign_id }); };
    el.innerHTML = '<div class="sa-row" style="flex-wrap:wrap;gap:8px;align-items:flex-end">' +
      '<div class="sa-field" style="margin:0"><label for="anP">Partner</label><select class="form-select" id="anP"><option value="">All partners</option>' + partners.map(function (p) { return '<option value="' + esc(p.id) + '"' + (st.partner_id === p.id ? ' selected' : '') + '>' + esc(p.company || p.name) + '</option>'; }).join('') + '</select></div>' +
      '<div class="sa-field" style="margin:0"><label for="anC">Campaign</label><select class="form-select" id="anC"><option value="">All campaigns</option></select></div>' +
      '<div class="sa-field" style="margin:0"><label for="anF">From</label><input class="form-input" type="date" id="anF" value="' + esc(st.from) + '"></div>' +
      '<div class="sa-field" style="margin:0"><label for="anT">To</label><input class="form-input" type="date" id="anT" value="' + esc(st.to) + '"></div>' +
      '<div class="sa-field" style="margin:0"><label for="anU">Group by</label><select class="form-select" id="anU">' + ['day', 'week', 'month'].map(function (u) { return '<option' + (st.unit === u ? ' selected' : '') + '>' + u + '</option>'; }).join('') + '</select></div>' +
      '<button type="button" class="btn btn-secondary btn-sm" id="anR">↻ Refresh</button><a class="btn btn-secondary btn-sm" id="anX" download>⤓ Export CSV</a></div><div id="anBody" style="margin-top:14px">' + K.skeleton('rows') + '</div>';
    var reload = function () { TABS.analytics(el, q); };
    $('#anP', el).onchange = function () { st.partner_id = this.value; st.campaign_id = ''; reload(); };
    $('#anC', el).onchange = function () { st.campaign_id = this.value; reload(); };
    $('#anF', el).onchange = function () { st.from = this.value; reload(); };
    $('#anT', el).onchange = function () { st.to = this.value; reload(); };
    $('#anU', el).onchange = function () { st.unit = this.value; reload(); };
    $('#anR', el).onclick = reload;
    $('#anX', el).href = B + '/analytics.csv' + params();
    var body = $('#anBody', el), a;
    try { a = (await api(B + '/analytics' + params())).analytics; } catch (e) { body.innerHTML = K.errorState(e, reload); return; }
    if (st.partner_id) {
      var cs = $('#anC', el);
      cs.innerHTML = '<option value="">All campaigns</option>' + a.campaigns.map(function (c) { return '<option value="' + esc(c.id) + '"' + (st.campaign_id === c.id ? ' selected' : '') + '>' + esc(c.name) + '</option>'; }).join('');
    } else { $('#anC', el).disabled = true; }
    var t = a.totals, cur = Object.keys(a.revenue_by_currency)[0] || Object.keys(a.commission_by_status)[0] || 'USD';
    var chart = function (key, title, money_) { return '<div class="sa-card"><h3>' + esc(title) + '</h3>' + K.lineChart(a.series[key], a.labels, { title: title, unit: a.unit, money: money_, currency: cur }) + '</div>'; };
    var sb = a.commission_by_status[cur] || {};
    var to = function (tab, extra) { return (st.partner_id ? '#/partners/' + encodeURIComponent(st.partner_id) + '?tab=' : '#/partners?tab=') + tab + (extra ? '&' + extra : ''); };
    body.innerHTML = '<div class="sa-grid sa-kpis">' + K.kpi('Clicks', K.fmtN(t.clicks), K.fmtN(t.visitors) + ' unique visitors', { href: st.partner_id ? to('clicks') : '#/partners?tab=analytics&section=pt-leaderboard' }) + K.kpi('Signups', K.fmtN(t.referrals), K.fmtN(t.demos) + ' demos · ' + t.conversion_rate + '% conversion', { href: to('referrals') }) +
      K.kpi('Customers', K.fmtN(t.customers), K.fmtN(t.active_subscriptions) + ' active subscriptions', { href: to('referrals', 'stage=customer') }) + K.kpi('Revenue', money(t.revenue, cur), 'paid invoices, net of refunds', { href: to('referrals', 'stage=customer') }) + '</div>' +
      '<div class="sa-grid sa-kpis">' + K.kpi('Commission earned', money(t.commission, cur), 'in period, excluding reversed', { href: to('commissions') }) + K.kpi('Pending / approved', money((sb.pending || 0) + (sb.qualified || 0) + (sb.approved || 0), cur), money(sb.payable || 0, cur) + ' payable', { href: to('commissions', 'status=approved') }) +
      K.kpi('Paid commission', money(sb.paid || 0, cur), money(sb.reversed || 0, cur) + ' reversed', { href: to('commissions', 'status=paid') }) + K.kpi('Payouts', K.fmtN(a.payouts.count), money(t.payouts, cur) + ' paid', { href: to('payouts') }) + '</div>' +
      '<div class="sa-grid sa-2" style="margin-top:14px">' + chart('clicks', 'Clicks') + chart('referrals', 'Signups') + chart('customers', 'Customers') + chart('revenue', 'Revenue', true) + chart('commission', 'Commission earned', true) + chart('payouts', 'Payouts paid', true) + '</div>' +
      '<div class="sa-card" style="margin-top:14px"><h3>Funnel</h3>' + K.barList(a.funnel.map(function (f) { return { label: K.titleCase(f.stage), value: f.count }; })) + '</div>' +
      '<div class="sa-card" style="margin-top:14px"><h3>Campaign performance</h3>' + (a.campaigns.length ? '<div class="sa-table-wrap"><table class="sa-table"><thead><tr><th>Campaign</th><th>Partner</th><th class="num">Clicks</th><th class="num">Visitors</th><th class="num">Signups</th><th class="num">Demos</th><th class="num">Customers</th><th class="num">Revenue</th><th class="num">Conv.</th></tr></thead><tbody>' +
        a.campaigns.map(function (c) { return '<tr><td><b>' + esc(c.name) + '</b> ' + pill(c.status) + '</td><td class="sa-small"><a class="sa-link" href="#/partners/' + esc(c.partner_id) + '">' + esc(K.short(c.partner_id)) + '</a></td><td class="num">' + K.fmtN(c.clicks) + '</td><td class="num">' + K.fmtN(c.visitors) + '</td><td class="num">' + K.fmtN(c.referrals) + '</td><td class="num">' + K.fmtN(c.demos) + '</td><td class="num">' + K.fmtN(c.customers) + '</td><td class="num">' + esc(money(c.revenue, cur)) + '</td><td class="num">' + esc(c.conversion_rate) + '%</td></tr>'; }).join('') + '</tbody></table></div>' : K.emptyState('No campaigns in this scope')) + '</div>' +
      '<div class="sa-card" style="margin-top:14px" data-section="pt-leaderboard"><h3>Partner leaderboard</h3><div id="anLb"></div></div>' +
      '<div class="sa-card" style="margin-top:14px"><div class="sa-row" style="justify-content:space-between"><h3 style="margin:0">Reconciliation with billing</h3><button type="button" class="btn btn-secondary btn-sm" id="anRec">Run check</button></div><div id="anRecOut" class="sa-small sa-muted" style="margin-top:8px">Cross-checks commissions, payouts, wallets and referral revenue against invoices and payments. Read-only.</div></div>';
    K.bindCharts(body); K.bindGo(body);
    K.listView($('#anLb', el), { url: function (x) { return B + '/leaderboard' + qs({ page: x.page, limit: x.limit, from: st.from, to: st.to }); },
      columns: [{ label: 'Partner', render: function (r) { return '<a class="sa-link" href="#/partners/' + esc(r.partner_id) + '">' + esc(r.name) + '</a> ' + pill(r.partner_type); } },
        { label: 'Clicks', cls: 'num', render: function (r) { return K.fmtN(r.clicks); } }, { label: 'Signups', cls: 'num', render: function (r) { return K.fmtN(r.referrals); } },
        { label: 'Customers', cls: 'num', render: function (r) { return K.fmtN(r.customers); } }, { label: 'Revenue', cls: 'num', render: function (r) { return esc(money(r.revenue, cur)); } },
        { label: 'Commission', cls: 'num', render: function (r) { return esc(money(r.commission, cur)); } }, { label: 'Status', render: function (r) { return pill(r.status); } }],
      empty: { title: 'No partners yet' } });
    $('#anRec', el).onclick = function () {
      var b2 = this;
      K.busy(b2, function () { return api(B + '/reconciliation' + qs({ partner_id: st.partner_id })); }).then(function (d) {
        var rc = d.reconciliation, out = $('#anRecOut', el);
        out.innerHTML = (rc.ok ? K.badge('All records reconcile', 'success') : K.badge(rc.issue_count + ' difference(s) to review', 'warning')) + ' <span class="sa-muted">checked ' + esc(Object.keys(rc.checked).map(function (k) { return rc.checked[k] + ' ' + k; }).join(', ')) + '</span>' +
          (rc.issues.length ? '<div class="sa-table-wrap" style="margin-top:8px"><table class="sa-table"><thead><tr><th>Type</th><th>Details</th></tr></thead><tbody>' + rc.issues.map(function (i) { var d2 = Object.assign({}, i); delete d2.type; return '<tr><td>' + esc(K.titleCase(i.type)) + '</td><td class="sa-mono sa-small">' + esc(JSON.stringify(d2)) + '</td></tr>'; }).join('') + '</tbody></table></div>' : '');
      }, function (e) { K.toast(e.message, 'error'); });
    };
  };

  TABS.applications = function (el, q, m) {
    el.innerHTML = '<div class="sa-chips" id="apChips"></div><div id="apList"></div>';
    var current = q.status || 'pending', lv;
    function chips(counts) {
      var all = Object.keys(counts || {}).reduce(function (a, k) { return a + counts[k]; }, 0);
      $('#apChips', el).innerHTML = [['', 'All', all], ['pending', 'Pending'], ['changes_requested', 'Changes requested'], ['approved', 'Approved'], ['rejected', 'Rejected']].map(function (c) {
        return '<button type="button" class="sa-chip' + (current === c[0] ? ' active' : '') + '" data-s="' + c[0] + '">' + esc(c[1]) + '<b>' + K.fmtN(c[2] != null ? c[2] : (counts || {})[c[0]] || 0) + '</b></button>';
      }).join('');
      $$('[data-s]', el).forEach(function (b) { b.onclick = function () { current = b.getAttribute('data-s'); lv.state.status = current; lv.state.page = 1; lv.reload(); }; });
    }
    lv = K.listView($('#apList', el), {
      url: function (p) { return B + '/applications' + qs({ page: p.page, limit: p.limit, sort: p.sort, q: p.q, status: p.status, partner_type: p.partner_type }); },
      sort: '-created_at', initial: { status: current }, key: 'partner_apps',
      filters: [{ key: 'q', label: 'Search name, email, company…' }, { key: 'partner_type', type: 'select', label: 'Type', options: [['', 'All types'], ['affiliate', 'Affiliate'], ['reseller', 'Reseller']] }],
      onData: function (d) { chips(d.counts); },
      columns: [
        { label: 'Applicant', sort: 'name', render: function (r) { return '<span class="cell-main">' + esc(r.name) + '</span><div class="sa-small sa-muted">' + esc(r.email) + '</div>'; } },
        { label: 'Company', sort: 'company', render: function (r) { return esc(r.company || '—'); } },
        { label: 'Type', render: function (r) { return pill(r.partner_type); } },
        { label: 'Location', render: function (r) { return esc([r.city, r.country].filter(Boolean).join(', ') || '—'); } },
        { label: 'Applied', sort: 'created_at', render: function (r) { return esc(K.fmtDT(r.created_at)); } },
        { label: 'Status', sort: 'status', render: function (r) { return pill(r.status); } },
        { label: '', cls: 'num', render: function () { return '<button type="button" class="btn btn-secondary btn-xs" data-a="review">Review</button>'; } }
      ],
      bindRow: function (tr, r, reload) { $('[data-a]', tr).onclick = function () { applicationDrawer(r.id, m, reload); }; },
      empty: { title: 'No applications', desc: 'Applications from the public /partners page appear here.' }
    });
  };

  function applicationDrawer(id, m, after) {
    K.openDrawer('Partner application', async function (body, close, rerun) {
      var a = (await api(B + '/applications/' + id)).application;
      var socials = Object.keys(a.social_profiles || {}).map(function (k) { return '<a class="sa-link" href="' + esc(a.social_profiles[k]) + '" target="_blank" rel="noopener noreferrer">' + esc(k) + ' ↗</a>'; }).join(' · ');
      var po = a.payout_info, tax = a.tax_info;
      var open = a.status === 'pending' || a.status === 'changes_requested';
      body.innerHTML = '<dl class="sa-kv"><dt>Status</dt><dd>' + pill(a.status) + '</dd><dt>Name</dt><dd>' + esc(a.name) + '</dd><dt>Email</dt><dd>' + esc(a.email) + '</dd>' +
        '<dt>Company</dt><dd>' + esc(a.company || '—') + '</dd><dt>Phone</dt><dd>' + esc(a.phone || '—') + '</dd><dt>Location</dt><dd>' + esc([a.city, a.country].filter(Boolean).join(', ') || '—') + '</dd>' +
        '<dt>Website</dt><dd>' + (a.website ? '<a class="sa-link" href="' + esc(a.website) + '" target="_blank" rel="noopener noreferrer">' + esc(a.website) + ' ↗</a>' : '—') + '</dd>' +
        '<dt>Business type</dt><dd>' + esc(a.business_type || '—') + '</dd><dt>Requested type</dt><dd>' + pill(a.partner_type) + '</dd>' +
        '<dt>Experience</dt><dd>' + esc(a.experience || '—') + '</dd><dt>Promotion plan</dt><dd>' + esc(a.promotion_plan || '—') + '</dd>' +
        '<dt>Social</dt><dd>' + (socials || '—') + '</dd>' +
        '<dt>Tax info</dt><dd>' + (tax ? esc(Object.keys(tax).map(function (k) { return K.titleCase(k) + ': ' + tax[k]; }).join(' · ')) : '—') + '</dd>' +
        '<dt>Payout info</dt><dd>' + (po ? esc(Object.keys(po).map(function (k) { return K.titleCase(k) + ': ' + po[k]; }).join(' · ')) : '—') + '</dd>' +
        '<dt>Existing customer</dt><dd>' + (a.is_existing_customer ? 'Yes — also an organization member' : 'No') + '</dd>' +
        '<dt>Previous applications</dt><dd>' + K.fmtN(a.previous_applications) + '</dd>' +
        '<dt>Terms accepted</dt><dd>' + esc(K.fmtDT(a.terms_accepted_at)) + ' (v' + esc(a.terms_version || '—') + ')</dd>' +
        (a.review_note ? '<dt>Review note</dt><dd>' + esc(a.review_note) + '</dd>' : '') + '</dl>' +
        '<h4>History</h4><ul class="pt-list">' + (a.history || []).map(function (h) { return '<li>' + pill(h.status) + ' <span class="sa-small sa-muted">' + esc(K.fmtDT(h.at)) + ' · ' + esc(h.by || '') + (h.note ? ' — ' + esc(h.note) : '') + '</span></li>'; }).join('') + '</ul>' +
        (open && m.can_manage ? '<div class="sa-row sa-section"><button type="button" class="btn btn-primary btn-sm" data-x="approve">Approve…</button>' + (a.status === 'pending' ? '<button type="button" class="btn btn-secondary btn-sm" data-x="changes">Request changes</button>' : '') + '<button type="button" class="btn btn-danger btn-sm" data-x="reject">Reject</button></div>' : '') +
        (a.partner_id ? '<div class="sa-row sa-section"><a class="btn btn-secondary btn-sm" href="#/partners/' + esc(a.partner_id) + '">Open partner</a></div>' : '');
      var done = function (msg) { K.toast(msg); rerun(); if (after) after(); K.setCount('partners'); };
      $$('[data-x]', body).forEach(function (b) { b.onclick = async function () {
        var x = b.getAttribute('data-x');
        try {
          if (x === 'approve') {
            var tiers = (await api(B + '/tiers')).items || [];
            var ok = await K.openModal({ title: 'Approve ' + a.name, size: 'lg', submitLabel: 'Approve partner',
              body: '<div class="sa-form-grid">' + field('partner_type', 'Partner type', a.partner_type, { options: [['affiliate', 'Affiliate'], ['reseller', 'Reseller']] }) +
                field('tier_id', 'Tier', (tiers.filter(function (t) { return t.is_default; })[0] || {}).id, { options: tiers.map(function (t) { return [t.id, t.name]; }) }) + '</div>' +
                '<h4 style="margin:10px 0 0">Permissions</h4><p class="sa-small sa-muted">Defaults for the selected type; adjust as needed.</p><div id="apPerms">' + permChecks(m, m.default_permissions[a.partner_type] || []) + '</div>' +
                field('note', 'Note (optional, sent to the audit log)', '', { type: 'textarea', rows: 2 }),
              onOpen: function (f) { $('[name=partner_type]', f).onchange = function () { $('#apPerms', f).innerHTML = permChecks(m, m.default_permissions[this.value] || []); }; },
              onSubmit: function (f, fd) { return api(B + '/applications/' + id + '/approve', { method: 'POST', body: { partner_type: fd.get('partner_type'), tier_id: fd.get('tier_id') || null, permissions: fd.getAll('perm'), note: fd.get('note') || null } }); } });
            if (ok) done('Partner approved — welcome email sent');
          } else if (x === 'changes') {
            var c = await K.openModal({ title: 'Request changes', submitLabel: 'Send request', body: field('note', 'What should the applicant change?', '', { type: 'textarea', rows: 4 }),
              onSubmit: function (f, fd) { if (!String(fd.get('note') || '').trim()) throw new Error('Describe the changes needed.'); return api(B + '/applications/' + id + '/request-changes', { method: 'POST', body: { note: fd.get('note') } }); } });
            if (c) done('Change request sent');
          } else {
            var r = await K.confirmDialog({ title: 'Reject application', message: 'The applicant is notified by email. They cannot sign in to the Partner Portal.', confirmLabel: 'Reject', reason: 'optional' });
            if (r) { await api(B + '/applications/' + id + '/reject', { method: 'POST', body: { reason: r.reason } }); done('Application rejected'); }
          }
        } catch (e) { K.toast(e.message, 'error'); }
      }; });
    });
  }

  TABS.partners = function (el, q) {
    el.innerHTML = '<div id="ptList"></div>';
    K.listView($('#ptList', el), {
      url: function (p) { return B + qs({ page: p.page, limit: p.limit, sort: p.sort, q: p.q, status: p.status, partner_type: p.partner_type }); },
      sort: '-created_at', initial: { status: q.status || '' }, key: 'partners',
      filters: [{ key: 'q', label: 'Search name, email, company, code…' },
        { key: 'status', type: 'select', label: 'Status', options: [['', 'All statuses'], ['active', 'Active'], ['suspended', 'Suspended']] },
        { key: 'partner_type', type: 'select', label: 'Type', options: [['', 'All types'], ['affiliate', 'Affiliates'], ['reseller', 'Resellers']] }],
      columns: [
        { label: 'Partner', sort: 'name', render: function (r) { return '<span class="cell-main">' + esc(r.company || r.name) + '</span><div class="sa-small sa-muted">' + esc(r.name) + ' · ' + esc(r.email) + '</div>'; } },
        { label: 'Type', render: function (r) { return pill(r.partner_type); } },
        { label: 'Code', render: function (r) { return '<span class="sa-mono">' + esc(r.referral_code) + '</span><div class="sa-small sa-muted">' + esc(r.partner_code) + '</div>'; } },
        { label: 'Referrals', cls: 'num', render: function (r) { return K.fmtN((r.stats || {}).referrals); } },
        { label: 'Customers', cls: 'num', render: function (r) { return K.fmtN((r.stats || {}).customers); } },
        { label: 'Revenue', cls: 'num', render: function (r) { return esc(money((r.stats || {}).revenue)); } },
        { label: 'Commission', cls: 'num', render: function (r) { return esc(money((r.stats || {}).commission)); } },
        { label: 'Status', sort: 'status', render: function (r) { return pill(r.status); } }
      ],
      rowClick: function (r) { K.go('#/partners/' + r.id); },
      bindRow: function (tr, r) { tr.style.cursor = 'pointer'; tr.onclick = function (e) { if (!e.target.closest('button,a,input')) K.go('#/partners/' + r.id); }; },
      empty: { title: 'No partners yet', desc: 'Approve an application to create a partner.' }
    });
  };

  function commissionColumns(withPartner) {
    return (withPartner ? [{ label: 'Partner', render: function (r) { return '<a class="sa-link" href="#/partners/' + esc(r.partner_id) + '">' + esc(r.partner_name || K.short(r.partner_id)) + '</a>'; } }] : []).concat([
      { label: 'Date', sort: 'created_at', render: function (r) { return esc(K.fmtDT(r.created_at)); } },
      { label: 'Customer', render: function (r) { return esc(r.company || (r.kind === 'adjustment' ? 'Adjustment' : '—')) + (r.note ? '<div class="sa-small sa-muted">' + esc(r.note) + '</div>' : ''); } },
      { label: 'Event', render: function (r) { return esc(K.titleCase(r.event)); } },
      { label: 'Payment', cls: 'num', render: function (r) { return r.base_amount ? esc(money(r.base_amount, r.currency)) : '—'; } },
      { label: 'Commission', cls: 'num', sort: 'amount', render: function (r) { return '<b>' + esc(money(r.amount, r.currency)) + '</b>' + (r.rate_type ? '<div class="sa-small sa-muted">' + esc(r.rate_type === 'hybrid' ? r.rate_value + '% + ' + money(r.rate_fixed, r.currency) : r.rate_type === 'percentage' ? r.rate_value + '%' : 'fixed') + (r.coupon_code ? ' · coupon ' + esc(r.coupon_code) : '') + '</div>' : '') + (r.reversed_amount ? '<div class="sa-small sa-muted">' + esc(money(r.reversed_amount, r.currency)) + ' reversed</div>' : '') + (r.clawed_back ? '<div class="sa-small sa-muted">' + esc(money(r.clawed_back, r.currency)) + ' clawed back</div>' : ''); } },
      { label: 'Status', sort: 'status', render: function (r) { return pill(r.status) + (r.requires_manual_approval && (r.status === 'pending' || r.status === 'qualified') ? ' ' + K.badge('Review', 'warning') : '') + (r.chargeback ? ' ' + K.badge('Chargeback', 'danger') : '') + (r.status === 'pending' ? '<div class="sa-small sa-muted">qualifies ' + esc(K.fmtDate(r.hold_until)) + '</div>' : ''); } },
      { label: '', cls: 'num', render: function (r) {
        var b = [];
        if (r.status === 'pending' || r.status === 'qualified') b.push('<button type="button" class="btn btn-secondary btn-xs" data-c="approve">Approve</button>');
        if (r.status === 'approved') b.push('<button type="button" class="btn btn-secondary btn-xs" data-c="payable">Make payable</button>');
        if (r.kind === 'commission' && r.status !== 'reversed') b.push('<button type="button" class="btn btn-danger btn-xs" data-c="reverse">Reverse</button>');
        if (r.kind === 'commission' && r.invoice_id) b.push('<button type="button" class="btn btn-ghost btn-xs" data-c="refund">Refund payment</button>');
        return '<div class="row-actions">' + b.join('') + '</div>'; } }
    ]);
  }
  function bindCommission(tr, r, reload) {
    $$('[data-c]', tr).forEach(function (b) { b.onclick = async function () {
      var a = b.getAttribute('data-c');
      try {
        if (a === 'approve') { await K.busy(b, function () { return api(B + '/commissions/' + r.id + '/approve', { method: 'POST' }); }); K.toast('Commission approved'); }
        else if (a === 'payable') { await K.busy(b, function () { return api(B + '/commissions/' + r.id + '/payable', { method: 'POST' }); }); K.toast('Commission is payable'); }
        else if (a === 'refund') { if (!(await refundDialog(r.invoice_id, r.paid_amount || r.base_amount, r.currency))) return; }
        else {
          var ok = await K.openModal({ title: 'Reverse commission', submitLabel: 'Reverse',  danger: true,
            body: '<p>' + (r.status === 'paid' ? 'This commission is paid: the amount is clawed back from the partner\'s next payout.' : 'The partner is notified and the wallet adjusted.') + '</p>' +
              field('amount', 'Amount (empty = all of ' + money(r.amount, r.currency) + ')', '', { type: 'number', step: '0.01', min: 0 }) + field('reason', 'Reason * (audited, shown to the partner)', '', { type: 'textarea', rows: 2 }),
            onSubmit: function (f, fd) { if (!String(fd.get('reason') || '').trim()) throw new Error('A reason is required.'); return api(B + '/commissions/' + r.id + '/reverse', { method: 'POST', body: { reason: fd.get('reason'), amount: fd.get('amount') ? parseFloat(fd.get('amount')) : null } }); } });
          if (!ok) return; K.toast('Commission reversed');
        }
        reload();
      } catch (e) { K.toast(e.message, 'error'); }
    }; });
  }

  async function refundDialog(invoiceId, paid, cur) {
    var ok = await K.openModal({ title: 'Refund customer payment', submitLabel: 'Record refund', danger: true,
      body: '<p>Records a refund (or chargeback) on the customer\'s invoice in billing. The related partner commission is reversed in proportion — or clawed back if already paid.</p>' +
        field('amount', 'Refund amount (empty = full ' + money(paid, cur) + ')', '', { type: 'number', step: '0.01', min: 0 }) +
        field('reason', 'Reason *', '', { type: 'textarea', rows: 2 }) + field('chargeback', 'This is a chargeback / dispute', false, { type: 'checkbox' }),
      onSubmit: function (f, fd) { if (!String(fd.get('reason') || '').trim()) throw new Error('A reason is required.');
        return api('/api/super-admin/invoices/' + encodeURIComponent(invoiceId) + '/refund', { method: 'POST', body: { amount: fd.get('amount') ? parseFloat(fd.get('amount')) : null, reason: fd.get('reason'), chargeback: !!fd.get('chargeback') } }); } });
    if (ok) { var pc = (ok.refund || {}).partner_commission || {}; K.toast('Refund recorded — commission reversed ' + money(pc.reversed) + ', clawed back ' + money(pc.clawback)); }
    return ok;
  }

  TABS.commissions = function (el, q) {
    el.innerHTML = '<div id="cmList"></div>';
    var lv = K.listView($('#cmList', el), {
      url: function (p) { return B + '/commissions' + qs({ page: p.page, limit: p.limit, sort: p.sort, status: p.status, manual: p.manual }); },
      sort: '-created_at', initial: { status: q.status || '', manual: q.manual === 'true' ? 'true' : '' }, key: 'partner_commissions',
      filters: [{ key: 'status', type: 'select', label: 'Status', options: COMMISSION_FILTER },
        { key: 'manual', type: 'select', label: 'Review', options: [['', 'All'], ['true', 'Needs manual review']] }],
      toolbar: '<button type="button" class="btn btn-secondary btn-sm" id="runLc">Run qualification now</button>',
      columns: commissionColumns(true), bindRow: bindCommission,
      empty: { title: 'No commissions', desc: 'Commissions are created when a referred organization\'s subscription payment is confirmed.' }
    });
    var rb = $('#runLc', el);
    if (rb) rb.onclick = function () { K.busy(rb, function () { return api(B + '/commissions/run-lifecycle', { method: 'POST' }); }).then(function (d) { K.toast(d.qualified + ' commission(s) qualified'); lv.reload(); }, function (e) { K.toast(e.message, 'error'); }); };
  };

  function payoutColumns(withPartner) {
    return (withPartner ? [{ label: 'Partner', render: function (r) { return '<a class="sa-link" href="#/partners/' + esc(r.partner_id) + '">' + esc(r.partner_name || K.short(r.partner_id)) + '</a><div class="sa-small sa-muted">' + esc(r.partner_email || '') + '</div>'; } }] : []).concat([
      { label: 'Requested', sort: 'created_at', render: function (r) { return esc(K.fmtDT(r.requested_at)); } },
      { label: 'Amount', cls: 'num', sort: 'amount', render: function (r) { return '<b>' + esc(money(r.amount, r.currency)) + '</b><div class="sa-small sa-muted">' + K.fmtN((r.commission_ids || []).length) + ' commission(s)</div>'; } },
      { label: 'Pay to', render: function (r) { var s = r.payout_snapshot || {}; return esc(K.titleCase(r.method)) + '<div class="sa-small sa-mono">' + esc(s.paypal_email || s.upi_id || s.iban || s.account_number || '') + (s.ifsc ? ' · ' + esc(s.ifsc) : '') + (s.swift ? ' · ' + esc(s.swift) : '') + '</div>' + (s.account_name ? '<div class="sa-small sa-muted">' + esc(s.account_name) + '</div>' : ''); } },
      { label: 'Status', sort: 'status', render: function (r) { return pill(r.status) + (r.reference ? '<div class="sa-small sa-muted">Ref ' + esc(r.reference) + '</div>' : ''); } },
      { label: '', cls: 'num', render: function (r) {
        var b = [], st = r.status;
        if (st === 'requested') b.push('<button type="button" class="btn btn-secondary btn-xs" data-p="review">Review</button>');
        if (st === 'requested' || st === 'under_review') b.push('<button type="button" class="btn btn-secondary btn-xs" data-p="approve">Approve</button>');
        if (st === 'approved') b.push('<button type="button" class="btn btn-secondary btn-xs" data-p="process">Start processing</button>');
        if (st === 'approved' || st === 'processing') b.push('<button type="button" class="btn btn-primary btn-xs" data-p="mark_paid">Mark paid</button><button type="button" class="btn btn-danger btn-xs" data-p="fail">Failed</button>');
        if (st === 'requested' || st === 'under_review' || st === 'approved') b.push('<button type="button" class="btn btn-danger btn-xs" data-p="reject">Reject</button>');
        return '<div class="row-actions">' + b.join('') + '</div>'; } }
    ]);
  }
  function bindPayout(tr, r, reload) {
    $$('[data-p]', tr).forEach(function (b) { b.onclick = async function () {
      var a = b.getAttribute('data-p'), body = {};
      try {
        if (a === 'mark_paid') {
          var ok = await K.openModal({ title: 'Mark payout as paid', submitLabel: 'Mark paid', body: '<p>Confirm you transferred <b>' + esc(money(r.amount, r.currency)) + '</b>. Its commissions become paid.</p>' + field('reference', 'Transfer reference *', '', { placeholder: 'UTR / transaction ID' }),
            onSubmit: function (f, fd) { if (!String(fd.get('reference') || '').trim()) throw new Error('Enter the transfer reference.'); return api(B + '/payouts/' + r.id + '/mark_paid', { method: 'POST', body: { reference: fd.get('reference') } }); } });
          if (!ok) return;
        } else if (a === 'reject' || a === 'fail') {
          var c = await K.confirmDialog({ title: a === 'reject' ? 'Reject payout' : 'Mark payout as failed', message: 'The commissions become payable again in the partner\'s wallet.', confirmLabel: a === 'reject' ? 'Reject' : 'Mark failed', reason: 'required' });
          if (!c) return; body.reason = c.reason;
          await api(B + '/payouts/' + r.id + '/' + a, { method: 'POST', body: body });
        } else { await K.busy(b, function () { return api(B + '/payouts/' + r.id + '/' + a, { method: 'POST', body: {} }); }); }
        K.toast('Payout updated'); reload(); K.setCount('partners');
      } catch (e) { K.toast(e.message, 'error'); }
    }; });
  }

  TABS.reversals = function (el) {
    el.innerHTML = '<p class="sa-small sa-muted">Refunds and chargebacks are recorded on the customer\'s invoice (Commissions → “Refund payment”, or automatically from provider webhooks). Unpaid commissions are reduced in proportion; paid ones get a clawback deducted from the next payout.</p><div id="rvList"></div>';
    K.listView($('#rvList', el), {
      url: function (p) { return B + '/reversals' + qs({ page: p.page, limit: p.limit, source: p.source }); },
      filters: [{ key: 'source', type: 'select', label: 'Source', options: [['', 'All sources'], ['refund', 'Refunds'], ['chargeback', 'Chargebacks'], ['cancellation', 'Cancellations'], ['manual', 'Manual'], ['fraud', 'Fraud review']] }],
      columns: [{ label: 'Partner', render: function (r) { return '<a class="sa-link" href="#/partners/' + esc(r.partner_id) + '">' + esc(r.partner_name || '') + '</a>'; } },
        { label: 'Customer', render: function (r) { return esc(r.company || '—'); } },
        { label: 'Type', render: function (r) { return r.kind === 'clawback' ? pill('clawback', 'Clawback') : pill(r.status); } },
        { label: 'Amount', cls: 'num', render: function (r) { return esc(money(r.kind === 'clawback' ? r.amount : -(r.reversed_amount || 0), r.currency)); } },
        { label: 'Source', render: function (r) { return esc(K.titleCase(r.reversal_source || (r.kind === 'clawback' ? 'refund of paid commission' : '—'))) + (r.chargeback ? ' ' + K.badge('Chargeback', 'danger') : ''); } },
        { label: 'Reason', render: function (r) { return '<span class="sa-small">' + esc(r.reversal_reason || r.note || '') + '</span>'; } },
        { label: 'When', render: function (r) { return esc(K.fmtDT(r.created_at)); } }],
      empty: { title: 'No reversals', desc: 'Refunds, chargebacks and cancellations that reduced a commission appear here.' } });
  };

  TABS.wallets = function (el) {
    el.innerHTML = '<p class="sa-small sa-muted">Balances are derived from commissions; partners can never edit them. Use a wallet adjustment (partner detail) for corrections.</p><div id="wlList"></div>';
    K.listView($('#wlList', el), {
      url: function (p) { return B + '/wallets' + qs({ page: p.page, limit: p.limit, q: p.q }); },
      filters: [{ key: 'q', label: 'Search partner…' }],
      columns: [{ label: 'Partner', render: function (w) { return '<a class="sa-link" href="#/partners/' + esc(w.id) + '?tab=ledger">' + esc(w.name) + '</a><div class="sa-small sa-muted">' + esc(w.email) + '</div>'; } },
        { label: 'Status', render: function (w) { return pill(w.status); } },
        { label: 'Balances', render: function (w) { return '<div class="sa-small">' + balancesHtml(w.balances) + '</div>'; } },
        { label: 'Payout details', render: function (w) { return w.has_payout_info ? pill('active', 'On file') : pill('pending', 'Missing'); } }],
      empty: { title: 'No partners yet' } });
  };

  TABS.referrals = function (el, q, m) {
    el.innerHTML = '<div class="sa-chips" id="rfChips"></div><div id="rfList"></div>';
    var lv = K.listView($('#rfList', el), {
      url: function (p) { return B + '/referrals' + qs({ page: p.page, limit: p.limit, q: p.q, stage: p.stage, status: p.status, suspicious: p.suspicious }); },
      filters: [{ key: 'q', label: 'Search company, email…' }, { key: 'stage', type: 'select', label: 'Stage', options: STAGE_FILTER },
        { key: 'status', type: 'select', label: 'Status', options: [['', 'All'], ['active', 'Active'], ['invalid', 'Invalidated']] },
        { key: 'suspicious', type: 'select', label: 'Flag', options: [['', 'All'], ['true', 'Flagged']] }],
      initial: { stage: STAGE_FILTER.some(function (x) { return x[0] && x[0] === q.stage; }) ? q.stage : '', suspicious: q.suspicious === 'true' ? 'true' : '' },
      onData: function (d) { var c = d.counts || {}; $('#rfChips', el).innerHTML = STAGE_FILTER.slice(1).map(function (s) { return '<span class="sa-chip">' + esc(s[1]) + '<b>' + K.fmtN(c[s[0]] || 0) + '</b></span>'; }).join(''); },
      columns: [
        { label: 'Customer', render: function (r) { return '<a class="sa-link" href="#/organizations/' + esc(r.organization_id) + '">' + esc(r.company || r.organization_id) + '</a><div class="sa-small sa-muted">' + esc(r.email || '') + '</div>'; } },
        { label: 'Partner', render: function (r) { return '<a class="sa-link" href="#/partners/' + esc(r.partner_id) + '">' + esc(r.partner_name || '') + '</a>'; } },
        { label: 'Source', render: function (r) { return esc(K.titleCase(r.source)) + (r.managed ? ' ' + K.badge('Managed', 'info') : ''); } },
        { label: 'Stage', render: function (r) { return pill(r.stage) + (r.churned_at ? ' ' + pill('cancelled', 'Churned') : ''); } },
        { label: 'Workspace', render: function (r) { return pill(r.organization_status); } },
        { label: 'Revenue', cls: 'num', render: function (r) { return esc(money(r.revenue_total)); } },
        { label: 'Status', render: function (r) { return pill(r.status) + (r.suspicious ? ' ' + K.badge('Flagged', 'warning') : ''); } },
        { label: '', cls: 'num', render: function (r) { return m.can_manage && r.status === 'active' ? '<div class="row-actions"><button type="button" class="btn btn-secondary btn-xs" data-r="reassign">Reassign</button>' + (r.suspicious ? '<button type="button" class="btn btn-secondary btn-xs" data-r="clear">Clear flag</button>' : '') + '<button type="button" class="btn btn-danger btn-xs" data-r="invalidate">Invalidate</button></div>' : ''; } }],
      bindRow: function (tr, r, reload) { $$('[data-r]', tr).forEach(function (b) { b.onclick = function () { referralAction(r, b.getAttribute('data-r')).then(function (ok) { if (ok) reload(); }); }; }); },
      empty: { title: 'No referred customers yet' } });
    return lv;
  };
  async function referralAction(r, a) {
    try {
      if (a === 'reassign') {
        var ok = await K.openModal({ title: 'Reassign ' + (r.company || 'customer'), submitLabel: 'Reassign',
          body: '<p class="sa-small sa-muted">Allowed while none of its commissions are in a payout or paid. Unpaid commissions move to the new partner.</p>' + field('partner_id', 'New partner ID *', '') + field('reason', 'Reason * (audited)', '', { type: 'textarea', rows: 2 }),
          onSubmit: function (f, fd) { return api(B + '/referrals/' + r.id + '/reassign', { method: 'POST', body: { partner_id: String(fd.get('partner_id') || '').trim(), reason: fd.get('reason') } }); } });
        if (ok) K.toast('Customer reassigned'); return ok;
      }
      var c = await K.confirmDialog({ title: a === 'clear' ? 'Clear the fraud flag' : 'Invalidate this referral',
        message: a === 'clear' ? 'Held commissions continue through the normal lifecycle.' : 'The referral stops earning; every commission is reversed and paid ones are clawed back.',
        confirmLabel: a === 'clear' ? 'Clear flag' : 'Invalidate', danger: a !== 'clear', reason: a === 'clear' ? 'optional' : 'required' });
      if (!c) return false;
      await api(B + '/referrals/' + r.id + '/review', { method: 'POST', body: { decision: a, reason: c.reason } });
      K.toast(a === 'clear' ? 'Flag cleared' : 'Referral invalidated'); return true;
    } catch (e) { K.toast(e.message, 'error'); return false; }
  }

  TABS.fraud = async function (el, q, m) {
    var d = await api(B + '/fraud');
    var tbl = function (head, rows) { return rows.length ? '<div class="sa-table-wrap"><table class="sa-table"><thead><tr>' + head.map(function (h) { return '<th>' + esc(h) + '</th>'; }).join('') + '</tr></thead><tbody>' + rows.join('') + '</tbody></table></div>' : K.emptyState('Nothing to review'); };
    el.innerHTML = '<div class="sa-grid sa-kpis">' + K.kpi('Open fraud flags', K.fmtN(d.open_flags), 'awaiting review — financial records are held, not changed', { href: '#/partners?tab=fraud&section=pt-fraud-queue', tone: d.open_flags ? 'warn' : '' }) + K.kpi('Flagged referrals', K.fmtN(d.flagged_referrals.length), 'same network, duplicates, conflicts', { href: '#/partners?tab=referrals&suspicious=true', tone: d.flagged_referrals.length ? 'warn' : '' }) +
      K.kpi('Commissions held', K.fmtN(d.held_commissions.length), 'need manual approval', { href: '#/partners?tab=commissions&manual=true' }) + K.kpi('Chargebacks', K.fmtN(d.chargebacks), 'on partner commissions', { href: '#/partners?tab=reversals' }) + K.kpi('Click floods', K.fmtN(d.click_floods.length), 'partners with rate-limited clicks', { href: '#/partners?tab=fraud&section=pt-click-floods' }) + '</div>' +
      '<div class="sa-card" style="margin-top:14px" data-section="pt-fraud-queue"><h3>Review queue</h3><div id="ffList"></div></div>' +
      '<div class="sa-card" style="margin-top:14px"><h3>Flagged referrals</h3>' + tbl(['Customer', 'Partner', 'Stage', 'Signal', ''], d.flagged_referrals.map(function (r, i) { return '<tr><td>' + esc(r.company || '—') + '</td><td><a class="sa-link" href="#/partners/' + esc(r.partner_id) + '">' + esc(r.partner_name || '') + '</a></td><td>' + pill(r.stage) + '</td><td>' + esc(K.titleCase(r.self_referral_flag || 'shared IP with partner')) + '</td><td class="num">' + (m.can_manage ? '<button type="button" class="btn btn-secondary btn-xs" data-f="clear" data-i="' + i + '">Clear</button><button type="button" class="btn btn-danger btn-xs" data-f="invalidate" data-i="' + i + '">Invalidate</button>' : '') + '</td></tr>'; })) + '</div>' +
      '<div class="sa-card" style="margin-top:14px"><h3>Commissions held for review</h3>' + tbl(['Partner', 'Customer', 'Amount', 'Status'], d.held_commissions.map(function (c) { return '<tr><td><a class="sa-link" href="#/partners/' + esc(c.partner_id) + '">' + esc(c.partner_name || '') + '</a></td><td>' + esc(c.company || '') + '</td><td>' + esc(money(c.amount, c.currency)) + '</td><td>' + pill(c.status) + '</td></tr>'; })) + '<p class="sa-small sa-muted">Approve them in the Commissions tab after reviewing the referral.</p></div>' +
      '<div class="sa-card" style="margin-top:14px" data-section="pt-click-floods"><h3>Click floods</h3>' + tbl(['Partner', 'Rate-limited clicks', 'Last'], d.click_floods.map(function (f) { return '<tr><td><a class="sa-link" href="#/partners/' + esc(f.partner_id) + '?tab=clicks">' + esc(f.partner_name || '') + '</a></td><td>' + K.fmtN(f.clicks) + '</td><td>' + esc(K.fmtDT(f.last)) + '</td></tr>'; })) + '</div>' +
      '<div class="sa-card" style="margin-top:14px"><h3>Blocked self-referrals & cross-partner probes</h3>' + tbl(['When', 'Actor', 'Details'], d.security_events.map(function (e) { return '<tr><td>' + esc(K.fmtDT(e.at || e.created_at)) + '</td><td>' + esc(e.actor_email || '—') + '</td><td class="sa-small sa-mono">' + esc(K.short(JSON.stringify(e.details || {}), 140)) + '</td></tr>'; })) + '</div>';
    $$('[data-f]', el).forEach(function (b) { b.onclick = function () { referralAction(d.flagged_referrals[+b.getAttribute('data-i')], b.getAttribute('data-f')).then(function (ok) { if (ok) TABS.fraud(el, q, m); }); }; });
    K.bindGo(el);
    var types = {};
    K.listView($('#ffList', el), {
      url: function (p) { return B + '/fraud-flags' + qs({ page: p.page, limit: p.limit, status: p.status, type: p.type, severity: p.severity }); },
      initial: { status: 'open' },
      filters: [{ key: 'status', type: 'select', label: 'Status', options: [['open', 'Open'], ['confirmed', 'Confirmed'], ['dismissed', 'Dismissed'], ['', 'All']] },
        { key: 'severity', type: 'select', label: 'Severity', options: [['', 'All severities'], ['high', 'High'], ['medium', 'Medium'], ['low', 'Low']] }],
      onData: function (dd) { types = dd.types || {}; },
      columns: [{ label: 'Signal', render: function (f) { return '<b>' + esc(f.label || types[f.type] || f.type) + '</b>' + (f.occurrences > 1 ? ' <span class="sa-small sa-muted">×' + K.fmtN(f.occurrences) + '</span>' : ''); } },
        { label: 'Severity', render: function (f) { return K.badge(K.titleCase(f.severity), f.severity === 'high' ? 'danger' : f.severity === 'medium' ? 'warning' : 'info'); } },
        { label: 'Partner', render: function (f) { return f.partner_id ? '<a class="sa-link" href="#/partners/' + esc(f.partner_id) + '">' + esc(f.partner_name || K.short(f.partner_id)) + '</a>' : '<span class="sa-muted">—</span>'; } },
        { label: 'Details', render: function (f) { return '<span class="sa-small sa-mono">' + esc(K.short(JSON.stringify(f.details || {}), 150)) + '</span>' + (f.referral_id ? '<div class="sa-small sa-muted">commissions held for review</div>' : ''); } },
        { label: 'When', render: function (f) { return esc(K.fmtDT(f.created_at)); } },
        { label: 'Status', render: function (f) { return pill(f.status === 'open' ? 'pending' : f.status === 'confirmed' ? 'failed' : 'cancelled', K.titleCase(f.status)) + (f.resolved_by ? '<div class="sa-small sa-muted">' + esc(f.resolved_by) + (f.note ? ': ' + esc(f.note) : '') + '</div>' : ''); } },
        { label: '', cls: 'num', render: function (f) { return f.status === 'open' && m.can_manage ? '<div class="row-actions"><button type="button" class="btn btn-secondary btn-xs" data-ff="dismiss">Dismiss</button><button type="button" class="btn btn-secondary btn-xs" data-ff="confirm">Confirm</button>' + (f.referral_id ? '<button type="button" class="btn btn-danger btn-xs" data-ff="invalidate_referral">Invalidate referral</button>' : '') + '</div>' : ''; } }],
      bindRow: function (tr, f, reload) { $$('[data-ff]', tr).forEach(function (b) { b.onclick = async function () {
        var dec = b.getAttribute('data-ff');
        var c = await K.confirmDialog({ title: { dismiss: 'Dismiss flag (false positive)', confirm: 'Confirm the issue', invalidate_referral: 'Invalidate the referral' }[dec],
          message: { dismiss: 'Held commissions are released when no other open flag concerns the referral.', confirm: 'The flag is recorded as confirmed; held commissions stay held.', invalidate_referral: 'The referral stops earning; its commissions are reversed (paid ones clawed back). This changes financial records.' }[dec],
          confirmLabel: K.titleCase(dec), danger: dec !== 'dismiss', reason: dec === 'dismiss' ? 'optional' : 'required' });
        if (!c) return;
        try { await api(B + '/fraud-flags/' + f.id + '/resolve', { method: 'POST', body: { decision: dec, note: c.reason } }); K.toast('Flag resolved'); reload(); } catch (e) { K.toast(e.message, 'error'); }
      }; }); },
      empty: { title: 'No flags', desc: 'Suspicious partner activity appears here for review.' } });
  };

  TABS.pricing = async function (el, q, m) {
    var items = (await api(B + '/pricing')).items || [];
    var tiers = (await api(B + '/tiers')).items || [];
    var tierName = {}; tiers.forEach(function (t) { tierName[t.id] = t.name; });
    el.innerHTML = '<div class="sa-row" style="justify-content:space-between"><p class="sa-small sa-muted" style="margin:0">Customers referred by a partner get this discount automatically at checkout. It never stacks with a coupon — the better one applies — and billing charges the discounted amount.</p>' +
      (m.can_manage ? '<button type="button" class="btn btn-primary btn-sm" id="newPr">+ New pricing rule</button>' : '') + '</div>' +
      (items.length ? '<div class="sa-table-wrap" style="margin-top:10px"><table class="sa-table"><thead><tr><th>Name</th><th>Applies to</th><th>Discount</th><th>Plans</th><th>First payment only</th><th>Status</th><th></th></tr></thead><tbody>' +
        items.map(function (x, i) { return '<tr><td><b>' + esc(x.name) + '</b></td><td>' + esc(x.scope === 'all' ? 'All referred customers' : x.scope === 'tier' ? 'Tier: ' + (tierName[x.tier_id] || x.tier_id) : 'Partner ' + K.short(x.partner_id)) + '</td><td>' + esc(x.discount_type === 'percentage' ? x.discount_value + '%' : money(x.discount_value, x.currency)) + '</td><td>' + esc((x.plan_slugs || []).join(', ') || 'All') + '</td><td>' + (x.first_payment_only ? 'Yes' : 'No') + '</td><td>' + pill(x.status === 'active' ? 'active' : 'disabled') + '</td><td class="num">' + (m.can_manage ? '<button type="button" class="btn btn-secondary btn-xs" data-x="' + i + '">Edit</button>' : '') + '</td></tr>'; }).join('') + '</tbody></table></div>' : K.emptyState('No partner pricing', 'Referred customers pay the normal plan price.'));
    var edit = async function (x) {
      x = x || { scope: 'partner', discount_type: 'percentage', status: 'active' };
      var ok = await K.openModal({ title: x.id ? 'Edit pricing rule' : 'New pricing rule', size: 'lg', submitLabel: 'Save',
        body: '<div class="sa-form-grid">' + field('name', 'Name', x.name || '') + field('scope', 'Applies to', x.scope, { options: [['partner', 'One partner'], ['tier', 'A tier'], ['all', 'All referred customers']] }) +
          field('partner_id', 'Partner ID (partner scope)', x.partner_id || '') + field('tier_id', 'Tier (tier scope)', x.tier_id || '', { options: [['', '—']].concat(tiers.map(function (t) { return [t.id, t.name]; })) }) +
          field('discount_type', 'Type', x.discount_type, { options: [['percentage', 'Percentage'], ['fixed', 'Fixed amount']] }) + field('discount_value', 'Value', x.discount_value || '', { type: 'number', step: '0.01', min: 0 }) +
          field('currency', 'Currency (fixed)', x.currency || 'USD') + field('plan_slugs', 'Only plans (comma-separated)', (x.plan_slugs || []).join(', ')) +
          field('status', 'Status', x.status, { options: [['active', 'Active'], ['disabled', 'Disabled']] }) + '</div>' + field('first_payment_only', 'First payment only', x.first_payment_only, { type: 'checkbox' }),
        onSubmit: function (f, fd) { return api(B + '/pricing' + (x.id ? '/' + x.id : ''), { method: x.id ? 'PATCH' : 'POST', body: { name: fd.get('name'), scope: fd.get('scope'), partner_id: fd.get('partner_id') || null, tier_id: fd.get('tier_id') || null, discount_type: fd.get('discount_type'), discount_value: parseFloat(fd.get('discount_value')) || 0, currency: fd.get('currency'), plan_slugs: String(fd.get('plan_slugs') || '').split(',').map(function (v) { return v.trim(); }).filter(Boolean), status: fd.get('status'), first_payment_only: !!fd.get('first_payment_only') } }); } });
      if (ok) { K.toast('Pricing rule saved'); TABS.pricing(el, q, m); }
    };
    var nb = $('#newPr', el); if (nb) nb.onclick = function () { edit(null); };
    $$('[data-x]', el).forEach(function (b) { b.onclick = function () { edit(items[+b.getAttribute('data-x')]); }; });
  };

  TABS.payouts = function (el, q) {
    el.innerHTML = '<div id="poList"></div>';
    K.listView($('#poList', el), {
      url: function (p) { return B + '/payouts' + qs({ page: p.page, limit: p.limit, sort: p.sort, status: p.status }); },
      sort: '-created_at', initial: { status: q.status || '' }, key: 'partner_payouts',
      filters: [{ key: 'status', type: 'select', label: 'Status', options: [['', 'All statuses'], ['requested', 'Requested'], ['under_review', 'Under review'], ['approved', 'Approved'], ['processing', 'Processing'], ['paid', 'Paid'], ['failed', 'Failed'], ['rejected', 'Rejected'], ['cancelled', 'Cancelled']] }],
      columns: payoutColumns(true), bindRow: bindPayout,
      empty: { title: 'No payouts', desc: 'Partners request payouts of their available balance from the Partner Portal.' }
    });
  };

  TABS.rules = async function (el, q, m) {
    var rules = (await api(B + '/commission-rules')).items || [];
    var tiers = (await api(B + '/tiers')).items || [];
    var tierName = {}; tiers.forEach(function (t) { tierName[t.id] = t.name; });
    el.innerHTML = '<div class="sa-row" style="justify-content:space-between"><p class="sa-small sa-muted" style="margin:0">The most specific active rule wins: partner override › tier › global. Recurring rules also pay on renewals for the duration (0 = lifetime).</p>' +
      (m.can_manage ? '<button type="button" class="btn btn-primary btn-sm" id="newRule">+ New rule</button>' : '') + '</div>' +
      (rules.length ? '<div class="sa-table-wrap" style="margin-top:10px"><table class="sa-table"><thead><tr><th>Name</th><th>Applies to</th><th>Commission</th><th>Recurring</th><th>Cap</th><th>Plans</th><th>Status</th><th></th></tr></thead><tbody>' +
        rules.map(function (r, i) { return '<tr><td><b>' + esc(r.name) + '</b></td><td>' + esc(r.scope === 'global' ? 'Everyone' : r.scope === 'tier' ? 'Tier: ' + (tierName[r.tier_id] || r.tier_id) : 'Partner ' + K.short(r.partner_id)) + '</td><td>' + esc(r.commission_type === 'percentage' ? r.value + '%' : r.commission_type === 'hybrid' ? r.value + '% + ' + money(r.fixed_amount) : money(r.value)) + (r.max_per_payment ? ' (max ' + esc(money(r.max_per_payment)) + ')' : '') + '</td><td>' + (r.recurring ? (r.duration_months ? esc(r.duration_months) + ' months' : 'Lifetime') : 'First payment') + '</td><td>' + esc(r.cap_amount ? money(r.cap_amount) : '—') + '</td><td>' + esc((r.plan_slugs || []).join(', ') || 'All') + '</td><td>' + pill(r.status === 'active' ? 'active' : 'disabled') + '</td><td class="num">' + (m.can_manage ? '<button type="button" class="btn btn-secondary btn-xs" data-r="' + i + '">Edit</button>' : '') + '</td></tr>'; }).join('') + '</tbody></table></div>' : K.emptyState('No rules', 'Create a global rule so partners earn commission.'));
    var reload = function () { TABS.rules(el, q, m); };
    var nb = $('#newRule', el); if (nb) nb.onclick = function () { ruleEditor(null, tiers).then(function (ok) { if (ok) reload(); }); };
    $$('[data-r]', el).forEach(function (b) { b.onclick = function () { ruleEditor(rules[+b.getAttribute('data-r')], tiers).then(function (ok) { if (ok) reload(); }); }; });
  };
  async function ruleEditor(r, tiers, fixedPartnerId) {
    r = r || { scope: fixedPartnerId ? 'partner' : 'global', partner_id: fixedPartnerId, commission_type: 'percentage', value: 20, recurring: true, duration_months: 12, status: 'active' };
    var ok = await K.openModal({ title: r.id ? 'Edit commission rule' : 'New commission rule', size: 'lg', submitLabel: 'Save rule',
      body: '<div class="sa-form-grid">' + field('name', 'Name', r.name || '') +
        (fixedPartnerId ? '<input type="hidden" name="scope" value="partner">' : field('scope', 'Applies to', r.scope, { options: [['global', 'Everyone (global)'], ['tier', 'A tier'], ['partner', 'One partner (ID)']] })) +
        (fixedPartnerId ? '' : field('tier_id', 'Tier (for tier rules)', r.tier_id || '', { options: [['', '—']].concat(tiers.map(function (t) { return [t.id, t.name]; })) }) + field('partner_id', 'Partner ID (for partner rules)', r.partner_id || '')) +
        field('commission_type', 'Type', r.commission_type, { options: [['percentage', 'Percentage of payment'], ['fixed', 'Fixed amount per payment'], ['hybrid', 'Hybrid: % + fixed']] }) +
        field('value', 'Value (% for percentage / hybrid, amount for fixed)', r.value, { type: 'number', step: '0.01', min: 0 }) +
        field('fixed_amount', 'Fixed part (hybrid only)', r.fixed_amount || '', { type: 'number', step: '0.01', min: 0 }) +
        field('max_per_payment', 'Max per payment (optional)', r.max_per_payment || '', { type: 'number', step: '0.01', min: 0 }) +
        field('qualification_days', 'Qualification days (empty = program default)', r.qualification_days == null ? '' : r.qualification_days, { type: 'number', min: 0 }) +
        field('duration_months', 'Duration (months, 0 = lifetime)', r.duration_months, { type: 'number', min: 0 }) +
        field('cap_amount', 'Cap per customer (optional)', r.cap_amount || '', { type: 'number', step: '0.01', min: 0 }) +
        field('plan_slugs', 'Only these plans (comma-separated, optional)', (r.plan_slugs || []).join(', ')) +
        field('status', 'Status', r.status, { options: [['active', 'Active'], ['inactive', 'Inactive']] }) + '</div>' +
        field('recurring', 'Recurring — also pay on renewals', r.recurring, { type: 'checkbox' }),
      onSubmit: function (f, fd) {
        var body = { name: fd.get('name'), scope: fd.get('scope'), tier_id: fd.get('tier_id') || null, partner_id: fixedPartnerId || fd.get('partner_id') || null,
          commission_type: fd.get('commission_type'), value: parseFloat(fd.get('value')) || 0, duration_months: parseInt(fd.get('duration_months'), 10) || 0,
          fixed_amount: parseFloat(fd.get('fixed_amount')) || 0, max_per_payment: fd.get('max_per_payment') ? parseFloat(fd.get('max_per_payment')) : null,
          qualification_days: fd.get('qualification_days') === '' ? null : parseInt(fd.get('qualification_days'), 10),
          cap_amount: fd.get('cap_amount') ? parseFloat(fd.get('cap_amount')) : null, recurring: !!fd.get('recurring'), status: fd.get('status'),
          plan_slugs: String(fd.get('plan_slugs') || '').split(',').map(function (s) { return s.trim(); }).filter(Boolean) };
        return api(B + '/commission-rules' + (r.id ? '/' + r.id : ''), { method: r.id ? 'PATCH' : 'POST', body: body });
      } });
    if (ok) K.toast('Commission rule saved');
    return ok;
  }

  TABS.tiers = async function (el, q, m) {
    var tiers = (await api(B + '/tiers')).items || [];
    var reqText = function (t) { var q2 = t.requirements || {}, parts = []; if (q2.min_customers) parts.push(q2.min_customers + ' customers'); if (q2.min_referrals) parts.push(q2.min_referrals + ' referrals'); if (q2.min_revenue) parts.push(money(q2.min_revenue) + ' revenue'); return parts.length ? parts.join(' · ') + (q2.period_days ? ' in ' + q2.period_days + ' days' : '') : 'Assigned by hand'; };
    el.innerHTML = (m.can_manage ? '<div class="sa-row" style="justify-content:flex-end"><button type="button" class="btn btn-secondary btn-sm" id="evalTiers">Evaluate tiers now</button><button type="button" class="btn btn-primary btn-sm" id="newTier">+ New tier</button></div>' : '') +
      '<div class="sa-table-wrap" style="margin-top:10px"><table class="sa-table"><thead><tr><th>Tier</th><th>Order</th><th>Requirements</th><th>Limits & permissions</th><th>Benefits</th><th class="num">Partners</th><th>Status</th><th></th></tr></thead><tbody>' +
      tiers.map(function (t, i) { return '<tr><td><b>' + esc(t.name) + '</b>' + (t.is_default ? ' ' + K.badge('Default', 'info') : '') + '<div class="sa-small sa-muted">' + esc(t.description || '') + '</div></td><td>' + esc(t.order) + '</td><td class="sa-small">' + esc(reqText(t)) + '</td><td class="sa-small">' + esc((t.max_customers ? 'max ' + t.max_customers + ' managed customers' : 'unlimited customers') + ' · coupons ' + (t.can_create_coupons === false ? 'no' : 'yes' + (t.max_coupon_percent != null ? ' (≤' + t.max_coupon_percent + '%)' : ''))) + '</td><td class="sa-small">' + esc((t.benefits || []).join(' · ') || '—') + '</td><td class="num">' + K.fmtN(t.partners) + '</td><td>' + pill(t.status) + '</td><td class="num">' + (m.can_manage ? '<button type="button" class="btn btn-secondary btn-xs" data-t="' + i + '">Edit</button>' : '') + '</td></tr>'; }).join('') + '</tbody></table></div>' +
      '<p class="sa-small sa-muted">Commission rates per tier: tier-scoped rules under Commission rules · Customer discounts per tier: Partner pricing · Automatic upgrades/downgrades: Program settings.</p>';
    var edit = async function (t) {
      t = t || { status: 'active', order: tiers.length + 1 };
      var ok = await K.openModal({ title: t.id ? 'Edit tier' : 'New tier', submitLabel: 'Save tier',
        body: '<div class="sa-form-grid">' + field('name', 'Name', t.name || '') + field('order', 'Order', t.order, { type: 'number' }) + field('status', 'Status', t.status, { options: ['active', 'inactive'] }) + '</div>' +
          field('description', 'Description', t.description || '', { type: 'textarea', rows: 2 }) + field('benefits', 'Benefits (one per line)', (t.benefits || []).join('\n'), { type: 'textarea', rows: 3 }) +
          '<h4 style="margin:10px 0 0">Requirements (automatic tiering; 0 = not required)</h4><div class="sa-form-grid">' +
          field('min_customers', 'Min paying customers', (t.requirements || {}).min_customers || 0, { type: 'number', min: 0 }) + field('min_referrals', 'Min referrals', (t.requirements || {}).min_referrals || 0, { type: 'number', min: 0 }) +
          field('min_revenue', 'Min revenue generated', (t.requirements || {}).min_revenue || 0, { type: 'number', min: 0, step: '0.01' }) + field('period_days', 'Within last N days (0 = lifetime)', (t.requirements || {}).period_days || 0, { type: 'number', min: 0 }) + '</div>' +
          '<h4 style="margin:10px 0 0">Limits & permissions</h4><div class="sa-form-grid">' +
          field('max_customers', 'Max managed customers (resellers, 0 = unlimited)', t.max_customers || 0, { type: 'number', min: 0 }) + field('max_coupon_percent', 'Max coupon % (empty = program cap)', t.max_coupon_percent == null ? '' : t.max_coupon_percent, { type: 'number', min: 0, step: '0.5' }) + '</div>' +
          field('can_create_coupons', 'Partners in this tier may create coupons', t.can_create_coupons !== false, { type: 'checkbox' }) +
          field('auto_assign', 'Assign automatically when requirements are met', t.auto_assign !== false, { type: 'checkbox' }) +
          field('is_default', 'Default tier for new partners', t.is_default, { type: 'checkbox' }),
        onSubmit: function (f, fd) { return api(B + '/tiers' + (t.id ? '/' + t.id : ''), { method: t.id ? 'PATCH' : 'POST', body: { name: fd.get('name'), order: parseInt(fd.get('order'), 10) || 1, status: fd.get('status'), description: fd.get('description'), benefits: String(fd.get('benefits') || '').split('\n').map(function (s) { return s.trim(); }).filter(Boolean), is_default: !!fd.get('is_default'),
          requirements: { min_customers: parseInt(fd.get('min_customers'), 10) || 0, min_referrals: parseInt(fd.get('min_referrals'), 10) || 0, min_revenue: parseFloat(fd.get('min_revenue')) || 0, period_days: parseInt(fd.get('period_days'), 10) || 0 },
          max_customers: parseInt(fd.get('max_customers'), 10) || 0, max_coupon_percent: fd.get('max_coupon_percent') === '' ? null : parseFloat(fd.get('max_coupon_percent')),
          can_create_coupons: !!fd.get('can_create_coupons'), auto_assign: !!fd.get('auto_assign') } }); } });
      if (ok) { K.toast('Tier saved'); TABS.tiers(el, q, m); }
    };
    var nb = $('#newTier', el); if (nb) nb.onclick = function () { edit(null); };
    var ev = $('#evalTiers', el); if (ev) ev.onclick = function () { K.busy(ev, function () { return api(B + '/tiers/evaluate', { method: 'POST' }); }).then(function (d) { K.toast((d.changes || []).length + ' tier change(s)'); TABS.tiers(el, q, m); }, function (e) { K.toast(e.message, 'error'); }); };
    $$('[data-t]', el).forEach(function (b) { b.onclick = function () { edit(tiers[+b.getAttribute('data-t')]); }; });
  };

  TABS.coupons = function (el, q, m) {
    el.innerHTML = (m.can_manage ? '<div class="sa-row" style="justify-content:flex-end"><button type="button" class="btn btn-primary btn-sm" id="newCoupon">+ New coupon</button></div>' : '') + '<div id="cpList"></div>';
    var lv = K.listView($('#cpList', el), {
      url: function (p) { return B + '/coupons' + qs({ page: p.page, limit: p.limit, q: p.q, status: p.status }); },
      filters: [{ key: 'q', label: 'Search code…' }, { key: 'status', type: 'select', label: 'Status', options: [['', 'All'], ['active', 'Active'], ['disabled', 'Disabled']] }],
      columns: [
        { label: 'Code', render: function (c) { return '<b class="sa-mono">' + esc(c.code) + '</b>'; } },
        { label: 'Partner', render: function (c) { return '<a class="sa-link" href="#/partners/' + esc(c.partner_id) + '">' + esc(K.short(c.partner_id)) + '</a>'; } },
        { label: 'Discount', render: function (c) { return esc(c.discount_type === 'percentage' ? c.discount_value + '%' : money(c.discount_value, c.currency)); } },
        { label: 'Redeemed', cls: 'num', render: function (c) { return K.fmtN(c.times_redeemed) + (c.max_redemptions ? ' / ' + K.fmtN(c.max_redemptions) : ''); } },
        { label: 'Expires', render: function (c) { return esc(K.fmtDate(c.expires_at)); } },
        { label: 'Rules', render: function (c) { return '<span class="sa-small">' + esc(K.titleCase(c.eligibility || 'any')) + ' · commission on ' + esc({ paid: 'paid amount', list: 'list price', none: 'nothing' }[c.commission_basis || 'paid']) + '</span>'; } },
        { label: 'Created by', render: function (c) { return esc(K.titleCase(c.created_by_role)); } },
        { label: 'Status', render: function (c) { return pill(c.status); } },
        { label: '', cls: 'num', render: function (c) { return m.can_manage ? '<button type="button" class="btn btn-secondary btn-xs" data-tg>' + (c.status === 'active' ? 'Disable' : 'Enable') + '</button>' : ''; } }
      ],
      bindRow: function (tr, c, reload) { var b = $('[data-tg]', tr); if (b) b.onclick = function () { K.busy(b, function () { return api(B + '/coupons/' + c.id, { method: 'PATCH', body: { status: c.status === 'active' ? 'disabled' : 'active' } }); }).then(function () { K.toast('Coupon updated'); reload(); }, function (e) { K.toast(e.message, 'error'); }); }; },
      empty: { title: 'No coupons', desc: 'Coupons give customers a discount and attribute them to the partner.' }
    });
    var nb = $('#newCoupon', el); if (nb) nb.onclick = function () { couponEditor(null).then(function (ok) { if (ok) lv.reload(); }); };
  };
  async function couponEditor(partnerId) {
    var ok = await K.openModal({ title: 'New partner coupon', submitLabel: 'Create coupon',
      body: '<div class="sa-form-grid">' + (partnerId ? '<input type="hidden" name="partner_id" value="' + esc(partnerId) + '">' : field('partner_id', 'Partner ID *', '')) +
        field('code', 'Code *', '', { placeholder: 'PARTNER20' }) + field('discount_type', 'Type', 'percentage', { options: [['percentage', 'Percentage'], ['fixed', 'Fixed amount']] }) +
        field('discount_value', 'Value *', '', { type: 'number', step: '0.01', min: 0 }) + field('currency', 'Currency (fixed only)', 'USD') +
        field('max_redemptions', 'Max redemptions', '', { type: 'number', min: 1 }) + field('expires_at', 'Expires', '', { type: 'date' }) + field('plan_slugs', 'Only plans (comma-separated)', '') +
        field('eligibility', 'Who may use it', 'any', { options: [['any', 'Any customer (once per organization)'], ['new_customers', 'New customers only'], ['referred_customers', 'Only customers referred by this partner']] }) +
        field('commission_basis', 'Partner commission is calculated on', 'paid', { options: [['paid', 'The discounted (paid) amount'], ['list', 'The list price'], ['none', 'No commission for coupon sales']] }) + '</div>',
      onSubmit: function (f, fd) { return api(B + '/coupons', { method: 'POST', body: { partner_id: fd.get('partner_id'), code: fd.get('code'), discount_type: fd.get('discount_type'), discount_value: parseFloat(fd.get('discount_value')) || 0, currency: fd.get('currency'), max_redemptions: fd.get('max_redemptions') ? parseInt(fd.get('max_redemptions'), 10) : null, expires_at: fd.get('expires_at') || null, plan_slugs: String(fd.get('plan_slugs') || '').split(',').map(function (s) { return s.trim(); }).filter(Boolean), eligibility: fd.get('eligibility'), commission_basis: fd.get('commission_basis') } }); } });
    if (ok) K.toast('Coupon created');
    return ok;
  }

  TABS.assets = async function (el, q, m) {
    var d = await api(B + '/assets'), items = d.items || [], cats = d.categories || {};
    el.innerHTML = (m.can_manage ? '<div class="sa-row" style="justify-content:space-between"><p class="sa-small sa-muted" style="margin:0">Files are stored privately and only served to partners allowed to see the asset. Text may use {referral_url}, {referral_code}, {partner_name} and {company}.</p><button type="button" class="btn btn-primary btn-sm" id="newAsset">+ New asset</button></div>' : '') +
      (items.length ? '<div class="sa-table-wrap" style="margin-top:10px"><table class="sa-table"><thead><tr><th>Asset</th><th>Category</th><th>Visible to</th><th>File</th><th class="num">Downloads</th><th>Status</th><th></th></tr></thead><tbody>' +
        items.map(function (a, i) { return '<tr><td><b>' + esc(a.title) + '</b><div class="sa-small sa-muted">' + esc(a.url || K.short(a.content, 80) || '') + '</div></td><td>' + esc(a.category_label) + '</td><td class="sa-small">' + esc((a.partner_types || []).join(', ') + ((a.partner_ids || []).length ? ' · ' + a.partner_ids.length + ' named partner(s)' : '') + ((a.tier_ids || []).length ? ' · ' + a.tier_ids.length + ' tier(s)' : '')) + '</td><td class="sa-small">' + (a.has_file ? '<a class="sa-link" href="' + esc(a.file_path) + '" target="_blank" rel="noopener">' + esc(a.file_name || 'file') + '</a> · ' + esc(Math.max(1, Math.round((a.file_size || 0) / 1024))) + ' KB' : '—') + '</td><td class="num">' + K.fmtN(a.download_count || 0) + '</td><td>' + pill(a.status) + '</td><td class="num">' + (m.can_manage ? '<button type="button" class="btn btn-secondary btn-xs" data-as="' + i + '">Edit</button>' : '') + '</td></tr>'; }).join('') + '</tbody></table></div>' : K.emptyState('No marketing assets', 'Publish logos, screenshots, brochures, videos, banners, email templates and copy for partners.'));
    var edit = async function (a) {
      a = a || { category: 'banners', status: 'published', partner_types: ['all'] };
      var ok = await K.openModal({ title: a.id ? 'Edit asset' : 'New marketing asset', size: 'lg', submitLabel: 'Save asset',
        body: '<div class="sa-form-grid">' + field('title', 'Title *', a.title || '') + field('category', 'Category', a.category, { options: Object.keys(cats).map(function (k) { return [k, cats[k]]; }) }) +
          field('partner_types', 'Partner types', (a.partner_types || ['all'])[0], { options: [['all', 'All partners'], ['affiliate', 'Affiliates'], ['reseller', 'Resellers']] }) + field('status', 'Status', a.status, { options: [['published', 'Published'], ['draft', 'Draft']] }) + '</div>' +
          field('partner_ids', 'Only these partner IDs (comma-separated, optional)', (a.partner_ids || []).join(', ')) +
          field('url', 'External link (optional, https://)', a.url || '') + field('subject', 'Email subject (email templates)', a.subject || '') +
          field('content', 'Text / email body / copy', a.content || '', { type: 'textarea', rows: 5 }) + field('description', 'Description', a.description || '', { type: 'textarea', rows: 2 }) +
          '<div class="sa-field"><label for="asFile">File (PNG, JPEG, GIF, WebP, PDF, MP4, WebM, ZIP · max 50 MB)</label><input class="form-input" type="file" id="asFile" name="file" accept=".png,.jpg,.jpeg,.gif,.webp,.pdf,.mp4,.webm,.zip">' + (a.has_file ? '<span class="hint">Current file: ' + esc(a.file_name || 'file') + ' — choose a new one to replace it.</span>' : '') + '</div>' +
          (a.has_file ? field('remove_file', 'Remove the current file', false, { type: 'checkbox' }) : ''),
        onSubmit: async function (f, fd) {
          var body = { title: fd.get('title'), category: fd.get('category'), partner_types: [fd.get('partner_types')], status: fd.get('status'),
            partner_ids: String(fd.get('partner_ids') || '').split(',').map(function (v) { return v.trim(); }).filter(Boolean),
            url: fd.get('url') || null, subject: fd.get('subject') || null, content: fd.get('content') || null, description: fd.get('description') || null, remove_file: !!fd.get('remove_file') };
          var saved = (await api(B + '/assets' + (a.id ? '/' + a.id : ''), { method: a.id ? 'PATCH' : 'POST', body: body })).asset;
          var file = fd.get('file');
          if (file && file.size) {
            var up = new FormData(); up.append('file', file);
            var res = await fetch(B + '/assets/' + saved.id + '/file', { method: 'POST', credentials: 'same-origin', body: up });
            if (!res.ok) { var j = {}; try { j = await res.json(); } catch (e2) {} throw new Error((j.detail && (j.detail.message || j.detail)) || 'Upload failed (' + res.status + ')'); }
          }
          return saved;
        } });
      if (ok) { K.toast('Asset saved'); TABS.assets(el, q, m); }
    };
    var nb = $('#newAsset', el); if (nb) nb.onclick = function () { edit(null); };
    $$('[data-as]', el).forEach(function (b) { b.onclick = function () { edit(items[+b.getAttribute('data-as')]); }; });
  };

  TABS.settings = async function (el, q, m) {
    var s = (await api(B + '/settings')).settings;
    var dis = m.can_manage ? '' : ' disabled';
    el.innerHTML = '<form class="sa-card" id="psForm" novalidate><h3>Partner program</h3><div class="sa-form-grid">' +
      field('attribution_model', 'Attribution model', s.attribution_model, { options: [['first_touch', 'First touch'], ['last_touch', 'Last touch']] }) +
      field('attribution_window_days', 'Attribution window (days)', s.attribution_window_days, { type: 'number', min: 1, hint: 'Common: 7, 30, 60, 90' }) +
      field('commission_hold_days', 'Qualification period (days)', s.commission_hold_days, { type: 'number', min: 0, hint: 'Refund window before a commission qualifies; rules may override' }) +
      field('payout_schedule', 'Payout schedule', s.payout_schedule, { options: [['on_request', 'On request (approved = payable at once)'], ['weekly', 'Weekly'], ['monthly', 'Monthly']] }) +
      field('payout_day', 'Payout day (weekly 0=Mon…6=Sun · monthly 1–28)', s.payout_day, { type: 'number', min: 0 }) +
      field('min_payout', 'Minimum payout', s.min_payout, { type: 'number', min: 0, step: '0.01' }) +
      field('payout_methods', 'Payout methods (comma-separated)', (s.payout_methods || []).join(', ')) +
      field('default_landing', 'Default landing page', s.default_landing) +
      field('max_partner_coupon_percent', 'Max partner coupon %', s.max_partner_coupon_percent, { type: 'number', min: 0, step: '0.5' }) +
      field('max_clicks_per_ip_per_hour', 'Max clicks per IP per hour', s.max_clicks_per_ip_per_hour, { type: 'number', min: 1 }) +
      field('terms_version', 'Terms version', s.terms_version) + '</div><div class="sa-row sa-section">' +
      field('applications_open', 'Accept new applications', s.applications_open, { type: 'checkbox' }) +
      field('allow_code_entry', 'Allow referral code entry without a click', s.allow_code_entry, { type: 'checkbox' }) +
      field('auto_approve_commissions', 'Approve commissions automatically after the hold', s.auto_approve_commissions, { type: 'checkbox' }) +
      field('partner_coupons_enabled', 'Partners may create their own coupons', s.partner_coupons_enabled, { type: 'checkbox' }) +
      field('block_self_referral', 'Block self-referrals', s.block_self_referral, { type: 'checkbox' }) +
      field('hold_suspicious_commissions', 'Hold suspicious commissions for manual review', s.hold_suspicious_commissions, { type: 'checkbox' }) +
      field('reverse_on_cancellation', 'Reverse commissions when a subscription is cancelled during qualification', s.reverse_on_cancellation, { type: 'checkbox' }) +
      field('auto_tiering', 'Assign tiers automatically from tier requirements', s.auto_tiering, { type: 'checkbox' }) +
      field('allow_tier_downgrade', 'Allow automatic tier downgrades', s.allow_tier_downgrade, { type: 'checkbox' }) +
      '</div><div class="sa-err" id="psErr" role="alert"></div><div class="sa-row sa-section"><button class="btn btn-primary btn-sm" type="submit"' + dis + '>Save settings</button><span class="sa-small sa-muted">Audited. Applies immediately.</span></div></form>' +
      '<form class="sa-card" id="bcForm" novalidate style="margin-top:14px"><h3>Announcement to partners</h3><div class="sa-form-grid">' + field('title', 'Title', '') + field('partner_type', 'Audience', '', { options: [['', 'All active partners'], ['affiliate', 'Affiliates'], ['reseller', 'Resellers']] }) + '</div>' +
      field('message', 'Message', '', { type: 'textarea', rows: 3 }) + field('email', 'Also send by email', false, { type: 'checkbox' }) +
      '<div class="sa-row sa-section"><button class="btn btn-secondary btn-sm" type="submit"' + dis + '>Send announcement</button></div></form>';
    $('#psForm', el).onsubmit = async function (e) {
      e.preventDefault();
      var fd = new FormData(this), body = {};
      ['attribution_model', 'default_landing', 'terms_version'].forEach(function (k) { body[k] = fd.get(k); });
      ['attribution_window_days', 'commission_hold_days', 'max_clicks_per_ip_per_hour', 'payout_day'].forEach(function (k) { body[k] = parseInt(fd.get(k), 10) || 0; });
      body.payout_schedule = fd.get('payout_schedule');
      ['min_payout', 'max_partner_coupon_percent'].forEach(function (k) { body[k] = parseFloat(fd.get(k)) || 0; });
      body.payout_methods = String(fd.get('payout_methods') || '').split(',').map(function (x) { return x.trim(); }).filter(Boolean);
      ['applications_open', 'allow_code_entry', 'auto_approve_commissions', 'partner_coupons_enabled', 'block_self_referral', 'hold_suspicious_commissions', 'reverse_on_cancellation', 'auto_tiering', 'allow_tier_downgrade'].forEach(function (k) { body[k] = !!fd.get(k); });
      $('#psErr', el).textContent = '';
      try { await K.busy($('button[type=submit]', this), function () { return api(B + '/settings', { method: 'PUT', body: body }); }); K.toast('Program settings saved'); }
      catch (err) { $('#psErr', el).textContent = err.message; }
    };
    $('#bcForm', el).onsubmit = async function (e) {
      e.preventDefault();
      var fd = new FormData(this), form = this;
      try { var r = await K.busy($('button[type=submit]', this), function () { return api(B + '/notify', { method: 'POST', body: { title: fd.get('title'), message: fd.get('message'), partner_type: fd.get('partner_type') || null, email: !!fd.get('email') } }); }); K.toast('Sent to ' + r.sent + ' partner(s)'); form.reset(); }
      catch (err) { K.toast(err.message, 'error'); }
    };
  };

  // ════════════════ partner detail ════════════════
  async function viewPartnerDetail(root, q, id) {
    var m = await meta();
    var p = (await api(B + '/' + encodeURIComponent(id))).partner;
    var st = p.stats || {};
    var pdTab = function (tab, extra) { return '#/partners/' + encodeURIComponent(id) + '?tab=' + tab + (extra ? '&' + extra : ''); };
    var actions = m.can_manage ? (p.status === 'active' ? '<button type="button" class="btn btn-primary btn-sm" id="pdTask">Assign task</button>' : '') + '<button type="button" class="btn btn-secondary btn-sm" id="pdEdit">Edit</button>' +
      '<button type="button" class="btn btn-secondary btn-sm" id="pdEmail">Change email</button>' +
      '<button type="button" class="btn btn-secondary btn-sm" id="pdPw">Set password</button>' +
      '<button type="button" class="btn btn-secondary btn-sm" id="pdViewAs">View as partner</button>' +
      '<button type="button" class="btn btn-secondary btn-sm" id="pdSignout">Force sign-out' + (p.active_sessions ? ' (' + p.active_sessions + ')' : '') + '</button>' +
      (p.status === 'active' ? '<button type="button" class="btn btn-danger btn-sm" id="pdSuspend">Suspend</button>' : '<button type="button" class="btn btn-primary btn-sm" id="pdReactivate">Reactivate</button>') : '';
    root.innerHTML = '<p><a class="sa-link" href="#/partners?tab=partners">← Partners</a></p>' + K.header(p.company || p.name, p.name + ' · ' + p.email + ' · partner since ' + K.fmtDate(p.approved_at), actions) +
      '<div class="sa-grid sa-kpis">' + K.kpi('Clicks', K.fmtN(st.clicks), '', { href: pdTab('clicks'), icon: 'bolt' }) + K.kpi('Referrals', K.fmtN(st.referrals), K.fmtN(st.customers) + ' customers', { href: pdTab('referrals'), icon: 'users' }) +
      K.kpi('Revenue generated', money(st.revenue), 'from referred customers', { href: pdTab('referrals', 'stage=customer'), icon: 'chart' }) + K.kpi('Commission', money(st.commission), 'excluding reversed', { href: pdTab('commissions'), icon: 'coin' }) + '</div>' +
      (function () { var a = p.activity || {}; return '<div class="sa-grid sa-kpis" style="margin-top:10px">' + K.kpi('Last sign-in', a.last_login_at ? K.ago(a.last_login_at) : 'Never', a.last_activity_at ? 'last active ' + K.ago(a.last_activity_at) : '', { href: '#/partners/' + id + '?tab=activity' }) +
        K.kpi('Requests · 30d', K.fmtN(a.requests_30d), K.fmtN(a.api_calls_30d) + ' via API key', { href: pdTab('activity', 'kind=request') }) + K.kpi('Failed sign-ins · 30d', K.fmtN(a.failed_logins_30d), K.fmtN(a.denied_30d) + ' denied requests', { href: pdTab('activity', 'success=false'), tone: (a.failed_logins_30d || a.denied_30d) ? 'warn' : '' }) +
        K.kpi('Deals', K.fmtN((p.deals || {}).registered || 0) + ' to review', K.fmtN((p.deals || {}).approved || 0) + ' approved · ' + K.fmtN((p.deals || {}).won || 0) + ' won', { href: '#/partners/' + id + '?tab=deals' }) + '</div>'; })() +
      (function () { var t = p.tasks || {}; return '<div class="sa-grid sa-kpis" style="margin-top:10px">' + K.kpi('Tasks to review', K.fmtN(t.submitted || 0), 'submitted by the partner', { href: '#/partners/' + id + '?tab=tasks&status=submitted', tone: t.submitted ? 'warn' : '' }) +
        K.kpi('Open tasks', K.fmtN((t.open || 0) + (t.in_progress || 0)), K.fmtN(t.in_progress || 0) + ' in progress', { href: '#/partners/' + id + '?tab=tasks' }) + K.kpi('Tasks done', K.fmtN(t.done || 0), 'approved by LeadAI', { href: '#/partners/' + id + '?tab=tasks&status=done' }) + '</div>'; })() +
      '<div id="pdTabs" style="margin-top:14px"></div>';
    var reload = function () { K.route(); };
    K.tabs($('#pdTabs', root), [['overview', 'Overview'], ['referrals', 'Referrals & customers'], ['commissions', 'Commissions'], ['ledger', 'Financial history'], ['payouts', 'Payouts'], ['clicks', 'Clicks'], ['access', 'Access & coupons'], ['tasks', 'Tasks'], ['deals', 'Deals'], ['activity', 'Activity'], ['sessions', 'Sessions'], ['audit', 'Audit log']], q.tab || 'overview', function (key, el) {
      if (key === 'activity') {
        el.innerHTML = '<div class="sa-row" style="justify-content:flex-end"><a class="btn btn-secondary btn-sm" id="pdCsv" download>⤓ Export CSV</a></div><div id="pdAct"></div>';
        return K.listView($('#pdAct', el), { url: function (x) { $('#pdCsv', el).href = B + '/activity.csv' + actParams(x, { page: null, limit: null, partner_id: id }); return B + '/activity' + actParams(x, { partner_id: id }); },
          limit: 50, filters: ACT_FILTERS, initial: actInitial(q), columns: activityColumns(false), empty: { title: 'No activity yet' } });
      }
      if (key === 'sessions') return sessionsList(el, id, m);
      if (key === 'tasks') return taskList(el, m, { id: id, name: p.company || p.name }, q.status);
      if (key === 'deals') {
        return K.listView(el, { url: function (x) { return B + '/deals' + qs({ page: x.page, limit: x.limit, partner_id: id, status: x.status }); },
          filters: [{ key: 'status', type: 'select', label: 'Status', options: [['', 'All deals']].concat(Object.keys(DEAL_LABEL).map(function (s) { return [s, DEAL_LABEL[s]]; })) }],
          columns: dealColumns(false, m), bindRow: bindDeal, empty: { title: 'No deals registered' } });
      }
      if (key === 'ledger') {
        return K.listView(el, { url: function (x) { return B + '/' + id + '/ledger' + qs({ page: x.page, limit: x.limit }); },
          columns: [{ label: 'When', render: function (t) { return esc(K.fmtDT(t.created_at)); } }, { label: 'Type', render: function (t) { return esc(K.titleCase(t.type)); } },
            { label: 'Customer', render: function (t) { return t.organization_id ? '<a class="sa-link" href="#/organizations/' + esc(t.organization_id) + '">' + esc(t.company || K.short(t.organization_id)) + '</a>' : '—'; } },
            { label: 'Amount', cls: 'num', render: function (t) { return '<b>' + esc(money(t.amount, t.currency)) + '</b>'; } },
            { label: 'Available after', cls: 'num', render: function (t) { return esc(money(t.available_after, t.currency)); } },
            { label: 'References', render: function (t) { return '<span class="sa-small sa-mono">' + esc([t.subscription_id && 'sub ' + K.short(t.subscription_id, 8), t.invoice_id && 'inv ' + K.short(t.invoice_id, 8), t.payout_id && 'payout ' + K.short(t.payout_id, 8)].filter(Boolean).join(' · ') || '—') + '</span>'; } },
            { label: 'By / note', render: function (t) { return '<span class="sa-small">' + esc(t.actor || '') + (t.note ? ' — ' + esc(t.note) : '') + '</span>'; } }],
          empty: { title: 'No financial activity yet' } });
      }
      if (key === 'overview') {
        var po = p.payout_info, tax = p.tax_info;
        el.innerHTML = '<div class="sa-grid sa-2"><div class="sa-card"><h3>Profile</h3><dl class="sa-kv"><dt>Status</dt><dd>' + pill(p.status) + (p.suspension_reason ? ' <span class="sa-small sa-muted">' + esc(p.suspension_reason) + '</span>' : '') + '</dd><dt>Type</dt><dd>' + pill(p.partner_type) + '</dd>' +
          '<dt>Partner ID</dt><dd class="sa-mono">' + esc(p.partner_code) + '</dd><dt>Referral code</dt><dd class="sa-mono">' + esc(p.referral_code) + '</dd><dt>Referral URL</dt><dd class="sa-mono">' + esc(p.referral_url) + '</dd>' +
          '<dt>Phone</dt><dd>' + esc(p.phone || '—') + '</dd><dt>Location</dt><dd>' + esc([p.city, p.country].filter(Boolean).join(', ') || '—') + '</dd><dt>Website</dt><dd>' + esc(p.website || '—') + '</dd><dt>Business type</dt><dd>' + esc(p.business_type || '—') + '</dd>' +
          '<dt>Experience</dt><dd>' + esc(p.experience || '—') + '</dd><dt>Promotion plan</dt><dd>' + esc(p.promotion_plan || '—') + '</dd><dt>Notes</dt><dd>' + esc(p.notes || '—') + '</dd></dl></div>' +
          '<div class="sa-card"><h3>Money</h3><dl class="sa-kv"><dt>Balances</dt><dd>' + balancesHtml(p.balances) + '</dd><dt>Commission rule</dt><dd>' + (p.rule ? esc(p.rule.name + ' — ' + (p.rule.commission_type === 'percentage' ? p.rule.value + '%' : money(p.rule.value)) + ', ' + (p.rule.recurring ? (p.rule.duration_months ? p.rule.duration_months + ' months recurring' : 'lifetime recurring') : 'first payment') + ' (' + p.rule.scope + ')') : '<span class="sa-muted">No matching rule — earns nothing</span>') + '</dd>' +
          '<dt>Payout details</dt><dd>' + (po ? esc(Object.keys(po).map(function (k) { return K.titleCase(k) + ': ' + po[k]; }).join(' · ')) : '<span class="sa-muted">Not provided</span>') + '</dd>' +
          '<dt>Tax</dt><dd>' + (tax ? esc(Object.keys(tax).map(function (k) { return K.titleCase(k) + ': ' + tax[k]; }).join(' · ')) : '—') + '</dd></dl>' +
          (m.can_manage ? '<div class="sa-row sa-section"><button type="button" class="btn btn-secondary btn-sm" id="pdRule">Partner-specific rule</button><button type="button" class="btn btn-secondary btn-sm" id="pdAdj">Wallet adjustment</button><button type="button" class="btn btn-secondary btn-sm" id="pdMsg">Send message</button></div>' : '') + '</div></div>';
        var bind = function (sel, fn) { var b = $(sel, el); if (b) b.onclick = fn; };
        bind('#pdRule', function () { api(B + '/tiers').then(function (t) { return ruleEditor(null, t.items || [], id); }).then(function (ok) { if (ok) reload(); }); });
        bind('#pdAdj', async function () {
          var ok = await K.openModal({ title: 'Wallet adjustment', submitLabel: 'Apply adjustment', body: '<div class="sa-form-grid">' + field('amount', 'Amount (negative = debit) *', '', { type: 'number', step: '0.01' }) + field('currency', 'Currency', Object.keys(p.balances || {})[0] || 'USD') + '</div>' + field('reason', 'Reason * (shown to the partner, audited)', '', { type: 'textarea', rows: 2 }),
            onSubmit: function (f, fd) { return api(B + '/' + id + '/adjustments', { method: 'POST', body: { amount: parseFloat(fd.get('amount')) || 0, currency: fd.get('currency'), reason: fd.get('reason') } }); } });
          if (ok) { K.toast('Adjustment applied'); reload(); }
        });
        bind('#pdMsg', async function () {
          var ok = await K.openModal({ title: 'Message ' + (p.company || p.name), submitLabel: 'Send', body: field('title', 'Title', '') + field('message', 'Message', '', { type: 'textarea', rows: 4 }) + field('email', 'Also send by email', false, { type: 'checkbox' }),
            onSubmit: function (f, fd) { return api(B + '/' + id + '/notify', { method: 'POST', body: { title: fd.get('title'), message: fd.get('message'), email: !!fd.get('email') } }); } });
          if (ok) K.toast('Message sent');
        });
      } else if (key === 'referrals') {
        K.listView(el, {
          url: function (x) { return B + '/' + id + '/referrals' + qs({ page: x.page, limit: x.limit, stage: x.stage }); },
          filters: [{ key: 'stage', type: 'select', label: 'Stage', options: STAGE_FILTER }],
          initial: { stage: STAGE_FILTER.some(function (x) { return x[0] && x[0] === q.stage; }) ? q.stage : '' },
          columns: [
            { label: 'Organization', render: function (r) { return '<a class="sa-link" href="#/organizations/' + esc(r.organization_id) + '">' + esc(r.company || r.organization_id) + '</a><div class="sa-small sa-muted">' + esc(r.email || '') + '</div>'; } },
            { label: 'Source', render: function (r) { return esc(K.titleCase(r.source)) + (r.managed ? ' ' + K.badge('Managed', 'info') : '') + (r.suspicious ? ' ' + K.badge('Suspicious', 'warning') : ''); } },
            { label: 'Stage', render: function (r) { return pill(r.stage); } },
            { label: 'Workspace', render: function (r) { return pill(r.organization_status); } },
            { label: 'Signed up', render: function (r) { return esc(K.fmtDate(r.signed_up_at)); } },
            { label: 'Revenue', cls: 'num', render: function (r) { return esc(money(r.revenue_total)); } },
            { label: 'Commission', cls: 'num', render: function (r) { return esc(money(r.commission_total)); } }
          ], empty: { title: 'No referrals yet' } });
      } else if (key === 'commissions') {
        K.listView(el, { url: function (x) { return B + '/' + id + '/commissions' + qs({ page: x.page, limit: x.limit, status: x.status }); },
          filters: [{ key: 'status', type: 'select', label: 'Status', options: COMMISSION_FILTER }], initial: { status: COMMISSION_FILTER.some(function (x) { return x[0] && x[0] === q.status; }) ? q.status : '' },
          columns: commissionColumns(false), bindRow: bindCommission, empty: { title: 'No commissions yet' } });
      } else if (key === 'payouts') {
        K.listView(el, { url: function (x) { return B + '/' + id + '/payouts' + qs({ page: x.page, limit: x.limit }); }, columns: payoutColumns(false), bindRow: bindPayout, empty: { title: 'No payouts yet' } });
      } else if (key === 'clicks') {
        K.listView(el, { url: function (x) { return B + '/' + id + '/clicks' + qs({ page: x.page, limit: x.limit, suspicious: x.suspicious }); },
          filters: [{ key: 'suspicious', type: 'select', label: 'Show', options: [['', 'All clicks'], ['true', 'Suspicious only']] }],
          columns: [{ label: 'When', render: function (c) { return esc(K.fmtDT(c.created_at)); } }, { label: 'Landing', render: function (c) { return '<span class="sa-mono">' + esc(c.landing) + '</span>'; } },
            { label: 'Referer', render: function (c) { return esc(K.short(c.referer || '—', 50)); } }, { label: 'Visitor', render: function (c) { return '<span class="sa-mono">' + esc(K.short(c.visitor_id, 10)) + '</span> · ip ' + esc(c.ip_hash || '—'); } },
            { label: 'Flags', render: function (c) { return (c.unique ? K.badge('Unique', 'info') : '') + (c.suspicious ? ' ' + K.badge('Rate limited', 'warning') : '') + (c.converted ? ' ' + K.badge('Converted', 'success') : ''); } }],
          empty: { title: 'No clicks yet' } });
      } else if (key === 'access') {
        el.innerHTML = '<div class="sa-grid sa-2"><div class="sa-card"><h3>Permissions</h3><div>' + (p.effective_permissions || []).map(function (x) { return K.badge(m.permissions[x] || x, 'info'); }).join(' ') + '</div>' +
          '<p class="sa-small sa-muted">Effective now (stored grants minus reseller-only rights for affiliates).</p></div>' +
          '<div class="sa-card"><h3>API keys</h3>' + ((p.api_keys || []).length ? '<ul class="pt-list">' + p.api_keys.map(function (k) { return '<li><b>' + esc(k.name) + '</b> <span class="sa-mono">' + esc(k.prefix) + '</span> ' + pill(k.is_active ? 'active' : 'revoked') + ' <span class="sa-small sa-muted">last used ' + esc(K.ago(k.last_used_at)) + '</span></li>'; }).join('') + '</ul>' : '<p class="sa-muted">None</p>') + '</div></div>' +
          '<div class="sa-card" style="margin-top:14px"><div class="sa-row" style="justify-content:space-between"><h3 style="margin:0">Coupons</h3>' + (m.can_manage ? '<button type="button" class="btn btn-secondary btn-sm" id="pdCoupon">+ Coupon</button>' : '') + '</div>' +
          ((p.coupons || []).length ? '<ul class="pt-list">' + p.coupons.map(function (c) { return '<li><b class="sa-mono">' + esc(c.code) + '</b> ' + esc(c.discount_type === 'percentage' ? c.discount_value + '%' : money(c.discount_value, c.currency)) + ' · ' + K.fmtN(c.times_redeemed) + ' redeemed ' + pill(c.status) + '</li>'; }).join('') + '</ul>' : '<p class="sa-muted">None</p>') +
          '<h3>Campaigns</h3>' + ((p.campaigns || []).length ? '<ul class="pt-list">' + p.campaigns.map(function (c) { return '<li>' + esc(c.name) + ' <span class="sa-mono">/' + esc(c.slug) + '</span> ' + pill(c.status) + '</li>'; }).join('') + '</ul>' : '<p class="sa-muted">None</p>') + '</div>';
        var cb = $('#pdCoupon', el); if (cb) cb.onclick = function () { couponEditor(id).then(function (ok) { if (ok) reload(); }); };
      } else {
        K.listView(el, { url: function (x) { return B + '/' + id + '/audit' + qs({ page: x.page, limit: x.limit }); },
          columns: [{ label: 'When', render: function (a) { return esc(K.fmtDT(a.at)); } }, { label: 'Action', render: function (a) { return '<span class="sa-mono">' + esc(a.action) + '</span>' + (a.success === false ? ' ' + pill('failed') : ''); } },
            { label: 'By', render: function (a) { return esc(a.actor_email || a.user || 'system'); } }, { label: 'Details', render: function (a) { var d = Object.assign({}, a.details || {}); delete d.partner_id; return '<span class="sa-small sa-mono">' + esc(K.short(JSON.stringify(d), 160)) + '</span>'; } }],
          empty: { title: 'No activity yet' } });
      }
    });
    var bind = function (sel, fn) { var b = $(sel, root); if (b) b.onclick = fn; };
    bind('#pdViewAs', async function () {
      var c = await K.confirmDialog({ title: 'View the Partner Portal as ' + (p.company || p.name), message: 'You get this partner\'s full portal with their permissions, for a limited time. Every request and action is recorded under your name. Use “Exit to Super Admin” in the portal to come back.', confirmLabel: 'Open Partner Portal', danger: false, reason: 'required' });
      if (!c) return;
      if (c.reason.length < 5) { K.toast('Give a reason of at least 5 characters', 'error'); return; }
      try { var r = await api(B + '/' + id + '/impersonate', { method: 'POST', body: { reason: c.reason } }); location.href = r.redirect; } catch (e) { K.toast(e.message, 'error'); }
    });
    bind('#pdEmail', function () { K.changeEmailDialog(B + '/' + id + '/email', p.email).then(function (r) { if (r) reload(); }, function (e) { K.toast(e.message, 'error'); }); });
    bind('#pdPw', function () { K.setPasswordDialog(B + '/' + id + '/password', p.email).then(function (r) { if (r) reload(); }, function (e) { K.toast(e.message, 'error'); }); });
    bind('#pdTask', function () { assignTask({ id: id, name: p.company || p.name }).then(function (r) { if (r) { K.toast(r.message || 'Task assigned'); location.hash = '#/partners/' + id + '?tab=tasks'; } }); });
    bind('#pdSignout', async function () {
      var c = await K.confirmDialog({ title: 'Force sign-out', message: 'Every Partner Portal session of this partner ends now. They can sign in again unless suspended.', confirmLabel: 'Sign out everywhere', reason: 'optional' });
      if (c) { try { var r = await api(B + '/' + id + '/sessions/revoke', { method: 'POST', body: { reason: c.reason } }); K.toast(r.revoked + ' session(s) ended'); reload(); } catch (e) { K.toast(e.message, 'error'); } }
    });
    bind('#pdSuspend', async function () {
      var c = await K.confirmDialog({ title: 'Suspend partner', message: 'The partner is signed out at once, API keys are revoked, links stop attributing, and no new commissions are earned.', confirmLabel: 'Suspend', reason: 'required' });
      if (c) { try { await api(B + '/' + id + '/suspend', { method: 'POST', body: { reason: c.reason } }); K.toast('Partner suspended'); reload(); } catch (e) { K.toast(e.message, 'error'); } }
    });
    bind('#pdReactivate', async function () {
      var c = await K.confirmDialog({ title: 'Reactivate partner', message: 'The partner can sign in and earn commission again.', confirmLabel: 'Reactivate', danger: false, reason: 'optional' });
      if (c) { try { await api(B + '/' + id + '/reactivate', { method: 'POST', body: { reason: c.reason } }); K.toast('Partner reactivated'); reload(); } catch (e) { K.toast(e.message, 'error'); } }
    });
    bind('#pdEdit', async function () {
      var tiers = (await api(B + '/tiers')).items || [];
      var ok = await K.openModal({ title: 'Edit partner', size: 'lg', submitLabel: 'Save changes',
        body: '<div class="sa-form-grid">' + field('partner_type', 'Partner type', p.partner_type, { options: [['affiliate', 'Affiliate'], ['reseller', 'Reseller']] }) +
          field('tier_id', 'Tier', p.tier_id || '', { options: [['', '—']].concat(tiers.map(function (t) { return [t.id, t.name]; })) }) +
          field('name', 'Name', p.name) + field('company', 'Company', p.company || '') + field('phone', 'Phone', p.phone || '') + field('website', 'Website', p.website || '') +
          field('country', 'Country', p.country || '') + field('city', 'City', p.city || '') + '</div>' +
          '<h4 style="margin:10px 0 0">Permissions</h4>' + permChecks(m, p.permissions || []) + field('notes', 'Internal notes', p.notes || '', { type: 'textarea', rows: 2 }),
        onSubmit: function (f, fd) {
          var body = { partner_type: fd.get('partner_type'), tier_id: fd.get('tier_id') || null, permissions: fd.getAll('perm'), notes: fd.get('notes') };
          ['name', 'company', 'phone', 'website', 'country', 'city'].forEach(function (k) { body[k] = fd.get(k); });
          return api(B + '/' + id, { method: 'PATCH', body: body });
        } });
      if (ok) { K.toast('Partner updated'); reload(); }
    });
  }

  function countPending() {
    api(B + '/overview').then(function (d) { K.setCount('partners', (d.overview.applications_pending || 0) + (d.overview.payouts_open || 0) + (d.overview.tasks_to_review || 0)); }, function () {});
  }

  K.register('Partners', 'Revenue', [['partners', 'Partners & Resellers', 'handshake', 'partners']],
    { partners: [viewPartners, 'Partners & Resellers', viewPartnerDetail] }, countPending);
})();
