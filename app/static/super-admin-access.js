/* LeadAI — Super Admin › API keys & webhooks (every organization and partner).
   Registered into the Super Admin portal through window.SAKit. */
(function () {
  'use strict';
  var K = window.SAKit;
  if (!K) return;
  var api = K.api, qs = K.qs, esc = K.esc, pill = K.pill, $ = K.$, $$ = K.$$;

  function ownerLink(r) {
    if (r.owner_type === 'partner') return '<a class="sa-link" href="#/partners/' + esc(r.owner_id) + '">' + esc(r.owner_name || K.short(r.owner_id)) + '</a> ' + K.badge('Partner', 'primary');
    return r.owner_id ? '<a class="sa-link" href="#/organizations/' + esc(r.owner_id) + '">' + esc(r.owner_name || K.short(r.owner_id)) + '</a>' : '<span class="sa-muted">—</span>';
  }
  function fld(name, label, html) { return '<div class="sa-field"><label for="ik_' + name + '">' + esc(label) + '</label>' + html + '</div>'; }
  async function issueKey(after) {
    var orgs = [], partners = [];
    var loadOrgs = async function (term) {
      try { return ((await api('/api/super-admin/organizations' + qs({ limit: 100, sort: 'name', search: term || '' }))).organizations || []); }
      catch (e) { K.toast(e.message, 'error'); return []; }
    };
    orgs = await loadOrgs('');
    try { partners = ((await api('/api/super-admin/partners' + qs({ limit: 200, status: 'active' }))).items || []); } catch (e) { partners = []; }
    var opt = function (list, idk, namek) { return list.map(function (o) { return '<option value="' + esc(o[idk] || o.id || o._id) + '">' + esc(namek(o)) + '</option>'; }).join(''); };
    var res = await K.openModal({ title: 'Issue an API key', submitLabel: 'Issue key', size: 'lg',
      body: '<div class="sa-form-grid">' + fld('type', 'For', '<select class="form-select" id="ik_type" name="owner_type"><option value="organization">An organization (Customer API /api/v1)</option><option value="partner">A partner (Partner API /api/partner/v1, read-only)</option></select>') +
        fld('name', 'Key name', '<input class="form-input" id="ik_name" name="name" value="Super Admin key" maxlength="60">') + '</div>' +
        '<div id="ikOrg">' + fld('orgq', 'Find organization', '<input class="form-input" id="ik_orgq" type="search" placeholder="Type a name to search…">') + fld('org', 'Organization', '<select class="form-select" id="ik_org" name="org">' + opt(orgs, 'id', function (o) { return o.name + (o.status ? ' · ' + o.status : ''); }) + '</select>') +
        '<fieldset class="sa-field" style="border:0;margin:0;padding:0;min-width:0"><legend style="font-size:12.5px;font-weight:600;color:var(--text-secondary);padding:0;margin-bottom:4px">What the key may do</legend><div class="sa-row">' +
        [['leads:read', 'Read leads'], ['leads:write', 'Update leads'], ['search:create', 'Start searches'], ['webhooks:manage', 'Manage webhooks']].map(function (s) {
          return '<label class="sa-check" title="' + s[0] + '"><input type="checkbox" name="scope" value="' + s[0] + '"' + (s[0] === 'leads:read' ? ' checked' : '') + '> ' + s[1] + ' <span class="sa-mono sa-muted">' + s[0] + '</span></label>'; }).join('') + '</div></fieldset></div>' +
        '<div id="ikPartner" hidden>' + fld('partner', 'Partner', '<select class="form-select" id="ik_partner" name="partner">' + opt(partners, 'id', function (p) { return (p.company || p.name) + ' · ' + p.partner_type; }) + '</select>') +
        '<label class="sa-check"><input type="checkbox" name="grant_api_access" checked> Also grant the partner API access if it is not enabled</label></div>' +
        fld('reason', 'Reason * (audited, shown to the owner)', '<textarea class="form-textarea" id="ik_reason" name="reason" rows="2" style="min-height:60px"></textarea>'),
      onOpen: function (f) {
        $('#ik_type', f).onchange = function () { $('#ikOrg', f).hidden = this.value !== 'organization'; $('#ikPartner', f).hidden = this.value !== 'partner'; };
        var t; $('#ik_orgq', f).addEventListener('input', function () { var term = this.value.trim(); clearTimeout(t); t = setTimeout(async function () {
          var list = await loadOrgs(term); $('#ik_org', f).innerHTML = list.length ? opt(list, 'id', function (o) { return o.name + (o.status ? ' · ' + o.status : ''); }) : '<option value="">No match</option>'; }, 250); });
      },
      onSubmit: function (f, fd) {
        var t = fd.get('owner_type'), id = t === 'partner' ? fd.get('partner') : fd.get('org');
        if (!id) throw new Error('Choose who the key is for.');
        if (String(fd.get('reason') || '').trim().length < 5) throw new Error('Give a reason of at least 5 characters.');
        return api('/api/super-admin/api-keys', { method: 'POST', body: { owner_type: t, owner_id: id, name: fd.get('name'), scopes: fd.getAll('scope'), reason: fd.get('reason'), grant_api_access: !!fd.get('grant_api_access') } });
      } });
    if (!res) return;
    await K.openModal({ title: 'Your new API key', submitLabel: null, cancelLabel: 'Done',
      body: '<p>' + esc(res.message) + '</p><div class="sa-row" style="flex-wrap:nowrap"><input class="form-input sa-mono" readonly value="' + esc(res.api_key) + '" id="ikRaw"><button type="button" class="btn btn-secondary btn-sm" id="ikCopy">Copy</button></div>',
      onOpen: function (f) { $('#ikCopy', f).onclick = function () { try { navigator.clipboard.writeText(res.api_key); K.toast('Copied'); } catch (e) { $('#ikRaw', f).select(); } }; } });
    if (after) after();
  }

  async function viewAccess(root, q) {
    root.innerHTML = K.header('Developer API & webhooks', 'Keys that organizations and partners use to call LeadAI\'s own APIs (Customer API /api/v1, Partner API), and the webhook addresses they register. Revoking a key takes effect at once and the owner is told. The Apify and Gemini keys are on the API keys page.', '<a class="btn btn-secondary btn-sm" href="#/provider-keys">Apify &amp; Gemini keys</a>') + '<div id="acTabs"></div>';
    K.tabs($('#acTabs', root), [['keys', 'API keys'], ['webhooks', 'Webhooks']], q.tab || 'keys', function (key, el) {
      if (key === 'keys') {
        el.innerHTML = '<div id="keyList"></div>';
        var lv = K.listView($('#keyList', el), {
          toolbar: '<button type="button" class="btn btn-primary btn-sm" id="issueKey" data-tour="access-issue">+ Issue API key</button>',
          url: function (p) { return '/api/super-admin/api-keys' + qs({ page: p.page, limit: p.limit, owner_type: p.owner_type, active: p.active, q: p.q }); },
          limit: 50, key: 'sa_api_keys', initial: { active: 'true' },
          filters: [{ key: 'q', label: 'Search name or prefix…' }, { key: 'owner_type', type: 'select', label: 'Owner', options: [['', 'All owners'], ['organization', 'Organizations'], ['partner', 'Partners']] },
            { key: 'active', type: 'select', label: 'Status', options: [['true', 'Active'], ['false', 'Revoked'], ['', 'All']] }],
          columns: [{ label: 'Key', render: function (k) { return '<b>' + esc(k.name) + '</b><div class="sa-mono sa-small">' + esc(k.prefix) + '</div>'; } },
            { label: 'Owner', render: ownerLink },
            { label: 'Scopes', render: function (k) { return '<span class="sa-small">' + esc((k.scopes || []).join(', ')) + '</span>'; } },
            { label: 'Usage', cls: 'num', render: function (k) { return K.fmtN(k.usage_count) + '<div class="sa-small sa-muted">' + esc(k.last_used_at ? 'last ' + K.ago(k.last_used_at) : 'never used') + '</div>'; } },
            { label: 'Created', render: function (k) { return esc(K.fmtDate(k.created_at)) + '<div class="sa-small sa-muted">' + esc(k.created_by || '') + '</div>'; } },
            { label: 'Status', render: function (k) { return k.is_active ? pill('active') : pill('revoked', 'Revoked') + (k.revoked_reason ? '<div class="sa-small sa-muted">' + esc(k.revoked_reason) + '</div>' : ''); } },
            { label: '', cls: 'num', render: function (k) { return k.is_active ? '<button type="button" class="btn btn-danger btn-xs" data-rk>Revoke</button>' : ''; } }],
          bindRow: function (tr, k, reload) { var b = $('[data-rk]', tr); if (b) b.onclick = async function () {
            var c = await K.confirmDialog({ title: 'Revoke API key “' + k.name + '”', message: 'Applications using it stop working immediately. The owner is notified.', confirmLabel: 'Revoke key', reason: 'required' });
            if (!c) return; try { await api('/api/super-admin/api-keys/' + encodeURIComponent(k.key_id) + '/revoke', { method: 'POST', body: { reason: c.reason } }); K.toast('Key revoked'); reload(); } catch (e) { K.toast(e.message, 'error'); } }; },
          empty: { title: 'No API keys', desc: 'Keys created by organizations and partners appear here.' } });
        $('#issueKey', el).onclick = issueKey.bind(null, function () { lv.reload(); });
        return lv;
      }
      // outbound delivery is not built yet: endpoints are stored, nothing is sent — say so instead of implying deliveries
      el.innerHTML = '<div class="alert alert-info" style="margin-bottom:12px"><div class="alert-body"><div class="alert-title">Webhook delivery is coming soon</div>' +
        '<div>Organizations and partners can already register webhook addresses, and you can disable or enable them here. LeadAI does not send events to them yet, so “Last delivery” stays empty until delivery is switched on.</div></div></div><div id="whList"></div>';
      return K.listView($('#whList', el), {
        url: function (p) { return '/api/super-admin/webhooks' + qs({ page: p.page, limit: p.limit, active: p.active }); },
        limit: 50, key: 'sa_webhooks',
        filters: [{ key: 'active', type: 'select', label: 'Status', options: [['', 'All statuses'], ['true', 'Active'], ['false', 'Disabled']] }],
        columns: [{ label: 'Endpoint', render: function (w) { return '<span class="sa-mono sa-small">' + esc(w.url) + '</span>'; } },
          { label: 'Organization', render: function (w) { return w.organization_id ? '<a class="sa-link" href="#/organizations/' + esc(w.organization_id) + '">' + esc(w.organization_name || K.short(w.organization_id)) + '</a>' : '—'; } },
          { label: 'Events', render: function (w) { return '<span class="sa-small">' + esc((w.events || []).join(', ')) + '</span>'; } },
          { label: 'Last delivery', render: function (w) { return w.last_delivery ? pill(w.last_delivery.status) + '<div class="sa-small sa-muted">' + esc(K.ago(w.last_delivery.at)) + '</div>' : '<span class="sa-muted">—</span>'; } },
          { label: 'Status', render: function (w) { return w.is_active ? pill('active') : pill('disabled') + (w.disabled_reason ? '<div class="sa-small sa-muted">' + esc(w.disabled_reason) + '</div>' : ''); } },
          { label: '', cls: 'num', render: function (w) { return '<button type="button" class="btn btn-' + (w.is_active ? 'danger' : 'secondary') + ' btn-xs" data-tw>' + (w.is_active ? 'Disable' : 'Enable') + '</button>'; } }],
        bindRow: function (tr, w, reload) { $('[data-tw]', tr).onclick = async function () {
          var a = w.is_active ? 'disable' : 'enable';
          var c = await K.confirmDialog({ title: K.titleCase(a) + ' webhook', message: w.is_active ? 'This address will not receive events until it is enabled again (delivery itself is coming soon).' : 'This address will receive events again once webhook delivery is switched on.', confirmLabel: K.titleCase(a), danger: w.is_active, reason: 'optional' });
          if (!c) return; try { await api('/api/super-admin/webhooks/' + encodeURIComponent(w.webhook_id) + '/' + a, { method: 'POST', body: { reason: c.reason } }); K.toast('Webhook ' + a + 'd'); reload(); } catch (e) { K.toast(e.message, 'error'); } }; },
        empty: { title: 'No webhooks yet', desc: 'Webhook addresses that organizations and partners register appear here.' } });
    });
  }
  K.register('Access', 'Partners', [['access', 'Developer API & webhooks', 'plug']], { access: [viewAccess, 'Developer API & webhooks'] });
})();
