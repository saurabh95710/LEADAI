/* LeadAI — Super Admin › engine module (merged from the old /admin console).
   Plugs into super-admin.js through window.SAKit.register (see super-admin-partners.js).
   Lead engine: Apify, Actors, Limits, Usage & cost, Lead scoring, Keyword rules.
   System: Database, Logs.
   Backend: /api/admin/* and /api/comment-filters/* (the server checks every role:
   viewer reads, manager writes, Super Admin only where noted). */
(function () {
  'use strict';
  var K = window.SAKit;
  if (!K) return;
  var api = K.api, qs = K.qs, esc = K.esc, pill = K.pill, badge = K.badge, fmtN = K.fmtN, toast = K.toast, $ = K.$, $$ = K.$$;
  var A = '/api/admin', CF = '/api/comment-filters';
  var PLATFORMS = ['facebook', 'instagram', 'linkedin', 'youtube'];

  (function () {
    var st = document.createElement('style');
    st.textContent = [
      '.eg-set{display:grid;grid-template-columns:minmax(0,1fr) 160px;gap:6px 16px;align-items:center;padding:10px 0;border-bottom:1px solid var(--border-subtle)}',
      '.eg-set:last-child{border-bottom:0}.eg-set label{font-weight:600;font-size:13px}.eg-set .form-input{width:100%}',
      '.eg-saved{display:block;font-size:11.5px;min-height:16px;margin-top:2px;color:var(--success-text)}.eg-saved.bad{color:var(--danger-text)}',
      '.eg-kw{display:inline-flex;flex-wrap:wrap;gap:4px;vertical-align:middle}',
      '.eg-kw span{padding:1px 8px;border-radius:999px;font-size:11.5px;line-height:18px;border:1px solid var(--border);background:var(--surface);color:var(--text-secondary);white-space:nowrap;max-width:220px;overflow:hidden;text-overflow:ellipsis}',
      '.eg-kw .w{background:var(--warning-soft);border-color:var(--warning-border);color:var(--warning-text)}',
      '.eg-kw .p{background:var(--primary-soft);border-color:var(--primary-border);color:var(--primary-text)}',
      '.eg-kw .d{background:var(--danger-soft);border-color:var(--danger-border);color:var(--danger-text)}',
      '.eg-actors{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(min(100%,300px),1fr))}',
      '.eg-actor h3{justify-content:flex-start;flex-wrap:wrap}.eg-actor h3 .sa-mono{overflow-wrap:anywhere}',
      '.eg-acts{display:flex;gap:6px;flex-wrap:wrap;margin-top:12px;align-items:center}',
      '.eg-cats{display:flex;flex-wrap:wrap;gap:6px;max-height:230px;overflow:auto;padding:2px}',
      '.eg-cat{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border:1px solid var(--border);border-radius:999px;font-size:12.5px;cursor:pointer;background:var(--surface);color:var(--text-primary)}',
      '.eg-cat:has(input:checked){background:var(--primary-light);border-color:var(--primary)}.eg-cat:has(input:focus-visible){outline:2px solid var(--primary);outline-offset:2px}',
      '.eg-cat input{accent-color:var(--primary);margin:0}.eg-cat.pre{border-style:dashed}',
      '.eg-banner{display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap}',
      '.eg-lsum{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px}',
      '.eg-lsum .sa-kpi[aria-pressed=true]{border-color:var(--primary);box-shadow:0 0 0 2px var(--primary-soft)}',
      '.eg-logwrap{position:relative;border:1px solid var(--border-subtle);border-radius:12px;overflow:hidden;background:var(--glass-bg)}',
      '.eg-log{max-height:62vh;overflow:auto;font-size:12.5px}',
      '.eg-lrow{display:grid;grid-template-columns:70px 92px minmax(90px,170px) minmax(0,1fr) 128px;gap:10px;align-items:center;padding:6px 12px;border-bottom:1px solid var(--border-subtle);cursor:pointer}',
      '.eg-lrow:hover{background:var(--surface-hover)}.eg-lrow:focus-visible{outline:2px solid var(--primary);outline-offset:-2px}',
      '.eg-lrow .t{font-family:var(--font-mono);color:var(--sa-muted, var(--text-muted))}.eg-lrow .mod{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:var(--font-mono);font-size:12px}',
      '.eg-lrow .msg{overflow-wrap:anywhere;min-width:0}',
      '.eg-jump{display:flex;gap:8px;align-items:center;justify-content:space-between;flex-wrap:wrap;padding:6px 12px;background:var(--info-soft);color:var(--info-text);border-bottom:1px solid var(--info-border)}',
      '.eg-logfoot{display:flex;gap:8px;align-items:center;justify-content:space-between;flex-wrap:wrap;padding:8px 12px;border-top:1px solid var(--border-subtle);font-size:12.5px}',
      '.eg-older{padding:6px 12px;border-bottom:1px solid var(--border-subtle);text-align:center}',
      '.eg-pre{white-space:pre-wrap;word-break:break-word;font-family:var(--font-mono);font-size:12px;background:var(--bg-tertiary);color:var(--text-primary);border:1px solid var(--border-subtle);border-radius:8px;padding:10px;max-height:260px;overflow:auto;margin:6px 0 12px}',
      '.eg-h4{margin:12px 0 0;font-size:12.5px;font-weight:700}',
      '.sa-actions .eg-hsel,.sa-card h3 .eg-hsel{width:auto;min-height:0;height:32px;padding-top:0;padding-bottom:0;font-size:13px}',
      '.eg-kwbtn{background:none;border:0;padding:0;font:inherit;color:inherit;text-align:left;cursor:pointer;width:100%;display:contents}',
      '.eg-kwlist li{cursor:pointer;border-radius:8px}.eg-kwlist li:hover{background:var(--surface-hover)}',
      '@media (max-width:900px){.eg-lsum{grid-template-columns:repeat(3,minmax(0,1fr))}}',
      '@media (max-width:640px){.eg-set{grid-template-columns:minmax(0,1fr)}',
      '.eg-lrow{grid-template-columns:auto auto minmax(0,1fr);gap:4px 8px}.eg-lrow .mod{display:none}.eg-lrow .src{justify-self:end;grid-column:3;grid-row:1}.eg-lrow .msg{grid-column:1/-1}',
      '.eg-lsum{grid-template-columns:repeat(2,minmax(0,1fr))}}'
    ].join('\n');
    document.head.appendChild(st);
  })();

  // ── helpers ─────────────────────────────────────────────────
  function canManage() { return K.can('manager'); }
  function isSuper() { return K.can('super_admin'); }
  /** naive ISO timestamps from the API are UTC */
  function utc(v) { return typeof v === 'string' && /^\d{4}-\d{2}-\d{2}T[\d:.]+$/.test(v) ? v + 'Z' : v; }
  function dt(v) { return v ? K.fmtDT(utc(v)) : '—'; }
  function usd(v) { var n = Number(v); return isFinite(n) ? '$' + n.toFixed(4) : '—'; }
  function fmtBytes(v) {
    var n = Number(v);
    if (v == null || v === '' || isNaN(n)) return '—';
    if (n < 1024) return n + ' B';
    var u = ['KB', 'MB', 'GB', 'TB'], i = -1;
    do { n /= 1024; i++; } while (n >= 1024 && i < u.length - 1);
    return n.toFixed(n >= 10 ? 1 : 2) + ' ' + u[i];
  }
  function tourHead(html, id) { return html.replace('<div class="sa-head"', '<div class="sa-head" data-tour="' + id + '"'); }
  function roleNote(what) {
    return '<div class="alert alert-info" role="note" style="margin-bottom:14px"><div class="alert-body">Read-only: your role can see ' + esc(what) + ' but not change ' + (/s$/.test(what) ? 'them' : 'it') + '. A manager or the Super Admin can.</div></div>';
  }
  function chips(list, cls, max) {
    list = Array.isArray(list) ? list : [];
    if (!list.length) return '<span class="sa-muted">—</span>';
    max = max || 8;
    return '<span class="eg-kw">' + list.slice(0, max).map(function (v) { return '<span class="' + (cls || '') + '" title="' + esc(v) + '">' + esc(v) + '</span>'; }).join('') +
      (list.length > max ? '<span title="' + esc(list.slice(max).join(', ')) + '">+' + (list.length - max) + '</span>' : '') + '</span>';
  }
  var PLAT_TONE = { facebook: 'info', instagram: 'primary', linkedin: 'info', youtube: 'danger' };
  function platformBadge(p) { return p ? badge(K.titleCase(p), PLAT_TONE[String(p).toLowerCase()] || 'neutral') : '<span class="sa-muted">—</span>'; }
  function csvCell(v) {
    var s = String(v == null ? '' : v).replace(/\s+/g, ' ').trim();
    if (/^[=+\-@\t\r]/.test(s)) s = "'" + s; // spreadsheet formula-injection guard
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }
  function downloadCsv(name, header, rows) {
    var body = '﻿' + [header].concat(rows).map(function (r) { return r.map(csvCell).join(','); }).join('\r\n');
    var url = URL.createObjectURL(new Blob([body], { type: 'text/csv;charset=utf-8' }));
    var a = document.createElement('a'); a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
  }
  function copyText(text, label) {
    var done = function () { toast(label + ' copied'); };
    if (navigator.clipboard && window.isSecureContext) { navigator.clipboard.writeText(text).then(done, function () { fallback(); }); return; }
    fallback();
    function fallback() {
      var ta = document.createElement('textarea'); ta.value = text; ta.setAttribute('readonly', ''); ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); done(); } catch (e) { toast('Could not copy — select the text instead', 'warning'); }
      ta.remove();
    }
  }
  function field(id, label, input, hint) {
    return '<div class="sa-field"><label for="' + id + '">' + label + '</label>' + input + (hint ? '<span class="hint">' + hint + '</span>' : '') + '</div>';
  }
  function toggleRow(id, key, label, hint, on, disabled) {
    return '<div class="sa-flag"><label class="sa-toggle"><input type="checkbox" id="' + id + '" data-key="' + esc(key) + '"' + (on ? ' checked' : '') + (disabled ? ' disabled' : '') + '><span></span></label>' +
      '<div><label for="' + id + '" style="font-weight:600;font-size:13px">' + esc(label) + '</label><div class="sa-small sa-muted">' + esc(hint) + '</div><span class="eg-saved" data-saved aria-live="polite"></span></div></div>';
  }
  function numRow(key, m, value, disabled, extra) {
    var id = 'eg_' + key.replace(/\W/g, '_');
    return '<div class="eg-set" data-key="' + esc(key) + '"><div><label for="' + id + '">' + esc(m.label) + '</label><div class="sa-small sa-muted">' + esc(m.hint) + ' <span class="sa-mono">(' + m.min + '–' + m.max + ')</span></div>' + (extra || '') + '</div>' +
      '<div><input class="form-input" type="number" inputmode="numeric" id="' + id + '" min="' + m.min + '" max="' + m.max + '" step="' + (m.step || 1) + '" value="' + esc(value == null ? m.min : value) + '"' + (disabled ? ' disabled' : '') + '>' +
      '<span class="eg-saved" data-saved aria-live="polite"></span></div></div>';
  }
  /** Number inputs save 600 ms after typing stops (or on change); toggles save at once. Same as the old console. */
  function bindAutoSave(root, url, metaOf) {
    $$('.eg-set input[type=number]', root).forEach(function (input) {
      var key = input.closest('.eg-set').getAttribute('data-key'), m = metaOf(key), timer, last = input.value;
      var out = $('[data-saved]', input.parentNode);
      async function commit() {
        clearTimeout(timer);
        if (input.value === last) return;
        var val = Number(input.value);
        if (input.value.trim() === '' || isNaN(val) || val < m.min || val > m.max || (m.step || 1) >= 1 && val % 1) {
          input.setAttribute('aria-invalid', 'true'); out.className = 'eg-saved bad'; out.textContent = 'Enter ' + m.min + '–' + m.max;
          toast(m.label + ' out of range (' + m.min + '–' + m.max + ')', 'warning'); return;
        }
        input.removeAttribute('aria-invalid'); out.className = 'eg-saved'; out.textContent = 'Saving…';
        try {
          var body = {}; body[key] = val;
          await api(url, { method: 'PUT', body: body });
          last = input.value; out.textContent = 'Saved ✓'; toast(m.label + ' → ' + val);
        } catch (err) { out.className = 'eg-saved bad'; out.textContent = 'Not saved'; toast(err.message, 'error'); }
      }
      input.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(commit, 600); });
      input.addEventListener('change', commit);
    });
    $$('.sa-toggle input[data-key]', root).forEach(function (el) {
      el.addEventListener('change', async function () {
        var key = el.getAttribute('data-key'), m = metaOf(key), out = $('[data-saved]', el.closest('.sa-flag'));
        var body = {}; body[key] = el.checked;
        el.disabled = true;
        try { await api(url, { method: 'PUT', body: body }); out.className = 'eg-saved'; out.textContent = 'Saved ✓'; toast(m.label + ' → ' + (el.checked ? 'on' : 'off')); }
        catch (err) { el.checked = !el.checked; out.className = 'eg-saved bad'; out.textContent = 'Not saved'; toast(err.message, 'error'); }
        finally { el.disabled = false; }
      });
    });
  }

  // ════════════════ APIFY ════════════════
  async function viewApify(root) {
    var sup = isSuper(), man = canManage();
    // /apify/test is a real Apify probe: it never runs on page load, only on demand
    var res = await Promise.all([
      api(A + '/apify'),
      api(A + '/usage?days=30').catch(function () { return null; }),
      api(A + '/apify/stats?days=30').catch(function () { return null; }),
      sup ? api('/api/super-admin/provider-keys?show=problems').catch(function () { return null; }) : null
    ]);
    var s = res[0], u = res[1] || {}, js = res[2], keys = res[3];
    var pk = keys && keys.platform && keys.platform.apify;
    var source = pk
      ? (pk.source === 'saved' ? 'Saved in LeadAI (encrypted) — overrides the server environment' : pk.source === 'environment' ? 'Server environment (APIFY_API_TOKEN)' : 'Not set')
      : s.env_token_configured ? 'The saved key if one is set, otherwise the server environment (APIFY_API_TOKEN)'
        : s.token_configured ? 'Saved in LeadAI (encrypted)' : '—';
    var lastTest = function () {
      return s.last_test_at ? pill(s.last_test_ok ? 'active' : 'failed', s.last_test_ok ? 'Passed' : 'Failed') + ' <span class="sa-small sa-muted">' + esc(dt(s.last_test_at)) + '</span>' : '<span class="sa-muted">Never</span>';
    };
    var tokenCard;
    if (sup) {
      tokenCard = '<p class="sa-small" style="margin-top:0">This is the same key as <b>Apify</b> on <a href="#/provider-keys">API keys</a>. Replace, test or reset it there; customers\' own Apify keys are listed there too.</p>' +
        '<p class="sa-small sa-muted">To drop the saved key and use the server\'s <span class="sa-mono">APIFY_API_TOKEN</span> again, use <b>Use the server key</b> on that page.</p>' +
        '<div class="eg-acts"><a class="btn btn-primary btn-sm" href="#/provider-keys">Manage the key on API keys →</a></div>';
    } else if (man) {
      tokenCard = '<p class="sa-small" style="margin-top:0">LeadAI\'s Apify and Gemini keys are managed by the Super Admin on the API keys page. As a manager you can still replace the Apify token: it is saved encrypted as an override of the server\'s <span class="sa-mono">APIFY_API_TOKEN</span> and tested at once.</p>' +
        '<div class="eg-acts"><button type="button" class="btn btn-secondary btn-sm" data-replace>Replace token…</button></div>';
    } else {
      tokenCard = '<p class="sa-small" style="margin-top:0">Your role cannot change the token. LeadAI\'s Apify and Gemini keys are managed by the Super Admin on the API keys page.</p>';
    }
    var acts = u.actors || [];
    root.innerHTML = K.header('Apify', 'Connection status and live test of the Apify token LeadAI provides to customers, and what it cost. Tokens are never shown — only a masked hint.',
      '<a class="btn btn-secondary btn-sm" href="#/actors">Actors →</a><a class="btn btn-secondary btn-sm" href="#/usage">Usage &amp; cost →</a>') +
      '<div class="sa-grid sa-2">' +
      '<div class="sa-card" data-tour="apify-connection"><h3>Connection ' + (s.token_configured ? pill('active', 'Token set') : pill('failed', 'No token')) + '</h3><dl class="sa-kv">' +
      '<dt>Token</dt><dd>' + (s.token_configured ? '<span class="sa-mono">' + esc(s.token_hint) + '</span>' : pill('failed', 'Not configured')) + '</dd>' +
      '<dt>Source</dt><dd>' + esc(source) + '</dd>' +
      '<dt>Server environment</dt><dd>' + (s.env_token_configured ? pill('active', 'APIFY_API_TOKEN set') : pill('inactive', 'APIFY_API_TOKEN not set')) + '</dd>' +
      '<dt>Last live test</dt><dd data-lasttest>' + lastTest() + '</dd>' +
      (pk ? '<dt>Customers using it</dt><dd>' + fmtN(pk.organizations_using) + ' organization' + (pk.organizations_using === 1 ? '' : 's') + ' on LeadAI-provided Apify</dd>' +
        (pk.updated_at ? '<dt>Key updated</dt><dd>' + esc(dt(pk.updated_at)) + (pk.updated_by ? ' <span class="sa-small sa-muted">by ' + esc(pk.updated_by) + '</span>' : '') + '</dd>' : '') +
        '<dt>Key test (API keys)</dt><dd>' + (pk.last_test ? pill(pk.last_test.valid ? 'active' : 'failed', pk.last_test.valid ? 'Valid' : 'Invalid') + ' <span class="sa-small sa-muted">' + esc(dt(pk.last_test.tested_at)) + '</span>' : '<span class="sa-muted">Not tested</span>') + '</dd>' : '') +
      '</dl><div class="eg-acts">' + (man ? '<button type="button" class="btn btn-primary btn-sm" data-probe>↻ Run live test</button>' : '<span class="sa-small sa-muted">Running the live test needs the manager role.</span>') + '</div>' +
      '<div data-probe-out aria-live="polite" style="margin-top:10px" hidden></div></div>' +
      '<div class="sa-card" data-tour="apify-token"><h3>Token</h3>' + tokenCard + '</div></div>' +
      (js ? '<div class="sa-card sa-section"><h3>Jobs in the last 30 days <a class="sa-link" href="#/jobs">All jobs →</a></h3><div class="sa-grid sa-kpis">' +
        K.kpi('Jobs', fmtN(js.total_jobs), 'search runs', { href: '#/jobs', icon: 'cpu' }) +
        K.kpi('Completed', fmtN(js.completed), js.success_rate == null ? 'no runs yet' : js.success_rate + '% success rate', { href: '#/jobs', icon: 'chart' }) +
        K.kpi('Failed', fmtN(js.failed), 'open the failed jobs', { href: '#/jobs?tab=failed', icon: 'flag', tone: js.failed ? 'warn' : '' }) +
        K.kpi('Running', fmtN(js.running), fmtN(js.cancelled) + ' cancelled', { href: '#/jobs', icon: 'bolt' }) + '</div></div>' : '') +
      '<div class="sa-card sa-section"><h3>Usage in the last 30 days <span class="sa-small sa-muted">real usageUsd from run records</span></h3>' +
      (acts.length ? '<div class="sa-grid sa-kpis">' +
        K.kpi('Total cost', usd(u.total_cost), 'reported by Apify', { href: '#/usage', icon: 'coin' }) +
        K.kpi('Runs with usage', fmtN(u.runs_with_usage), 'runs that reported a cost', { href: '#/usage', icon: 'chart' }) +
        K.kpi('Actors used', fmtN(acts.length), 'see the actors', { href: '#/actors', icon: 'cpu' }) + '</div>' +
        '<div class="sa-section">' + K.barList(acts.map(function (a) { return { label: a.actor + ' · ' + fmtN(a.runs) + ' runs · ' + usd(a.cost), value: a.runs }; })) + '</div>'
        : K.emptyState('No usage yet', 'No Apify runs with usage data in the last 30 days — costs appear here as Apify reports them.')) + '</div>';
    var pb = $('[data-probe]', root);
    if (pb) pb.onclick = async function () {
      var box = $('[data-probe-out]', root);
      try {
        var r = await K.busy(pb, function () { pb.textContent = 'Testing…'; return api(A + '/apify/test', { method: 'POST' }); });
        box.hidden = false;
        box.innerHTML = r.ok ? '<div class="alert alert-success"><div class="alert-body">Actor reachable: <span class="sa-mono">' + esc(r.actor) + '</span>' + (r.title ? ' (' + esc(r.title) + ')' : '') + '</div></div>'
          : '<div class="alert alert-danger"><div class="alert-body">' + esc(r.error || 'The test failed') + '</div></div>';
        s.last_test_ok = !!r.ok; s.last_test_at = new Date().toISOString();
        $('[data-lasttest]', root).innerHTML = lastTest();
        toast(r.ok ? 'Apify connection OK' : 'Apify test failed', r.ok ? 'success' : 'error');
      } catch (err) { toast(err.message, 'error'); }
      finally { if (pb.isConnected) pb.textContent = '↻ Run live test'; }
    };
    var rb = $('[data-replace]', root);
    if (rb) rb.onclick = async function () {
      var ok = await K.openModal({ title: 'Replace the Apify token', submitLabel: 'Save & test',
        body: field('egTok', 'New token', '<input class="form-input" id="egTok" name="token" type="password" autocomplete="off" placeholder="apify_api_…">',
          'Stored encrypted as an override; the server environment\'s APIFY_API_TOKEN stays as the fallback. The token is tested at once.'),
        onSubmit: async function (form, fd) {
          var token = String(fd.get('token') || '').trim();
          if (token.length < 10) throw new Error('The token looks too short.');
          var r = await api(A + '/apify/token', { method: 'POST', body: { token: token } });
          toast(r.test && r.test.ok ? 'Token saved and verified' : 'Saved, but the test failed: ' + ((r.test && r.test.error) || 'unknown error'), r.test && r.test.ok ? 'success' : 'warning');
          return true;
        } });
      if (ok) K.route();
    };
  }

  // ════════════════ ACTORS ════════════════
  function actorKind(key) { return String(key).split('.')[2] || 'main'; }
  async function viewActors(root) {
    var man = canManage();
    var d = await api(A + '/actors');
    var list = d.actors || [];
    root.innerHTML = K.header('Actors', 'Every Apify actor the scrapers call, its current value and whether it overrides the default.',
      '<button type="button" class="btn btn-secondary btn-sm" data-refresh>↻ Refresh</button><a class="btn btn-secondary btn-sm" href="#/platforms">Platforms →</a>') +
      (man ? '' : roleNote('the actors')) +
      (list.length ? '<div class="eg-actors" data-tour="actors-grid">' + list.map(function (a, i) {
        return '<div class="sa-card eg-actor" data-i="' + i + '"><h3><span class="sa-mono">' + esc(a.key) + '</span> ' + platformBadge(a.platform) + '</h3><dl class="sa-kv">' +
          '<dt>Actor</dt><dd><span class="sa-mono">' + esc(a.value) + '</span></dd>' +
          '<dt>Value</dt><dd>' + (a.overridden ? badge('Override', 'warning') + ' <span class="sa-small sa-muted">default: <span class="sa-mono">' + esc(a.default) + '</span></span>' : badge('Default', 'muted')) + '</dd>' +
          (a.updated_at ? '<dt>Updated</dt><dd>' + esc(dt(a.updated_at)) + '</dd>' : '') +
          '<dt>Last test</dt><dd data-res><span class="sa-muted">Not tested here</span></dd></dl>' +
          '<div class="eg-acts">' + (man ? '<button type="button" class="btn btn-primary btn-xs" data-test>Test</button><button type="button" class="btn btn-secondary btn-xs" data-edit>Edit</button>' +
            (a.overridden ? '<button type="button" class="btn btn-ghost btn-xs" data-reset>Reset to default</button>' : '') : '') +
          '<a class="btn btn-ghost btn-xs" href="https://apify.com/' + esc(a.value) + '" target="_blank" rel="noopener">Open in Apify ↗</a></div></div>';
      }).join('') + '</div>' : K.emptyState('No actors', 'The actor registry is empty.'));
    $('[data-refresh]', root).onclick = function () { K.route(); };
    $$('.eg-actor', root).forEach(function (card) {
      var a = list[+card.getAttribute('data-i')];
      var t = $('[data-test]', card), e = $('[data-edit]', card), r = $('[data-reset]', card);
      if (t) t.onclick = async function () {
        try {
          var x = await K.busy(t, function () { return api(A + '/actors/test', { method: 'POST', body: { key: a.key } }); });
          $('[data-res]', card).innerHTML = x.ok ? pill('active', 'Reachable') + ' <span class="sa-small sa-muted">' + esc(dt(new Date().toISOString())) + '</span>' : pill('failed', 'Failed') + ' <span class="sa-small">' + esc(x.error || '') + '</span>';
          toast(x.ok ? 'Actor ' + x.actor_id + ' reachable' : 'Test failed: ' + (x.error || 'unknown error'), x.ok ? 'success' : 'error');
        } catch (err) { toast(err.message, 'error'); }
      };
      if (e) e.onclick = async function () {
        var ok = await K.openModal({ title: 'Edit actor · ' + a.key, submitLabel: 'Save',
          body: field('egAct', 'Apify actor id', '<input class="form-input sa-mono" id="egAct" name="actor_id" value="' + esc(a.value) + '" autocomplete="off" placeholder="username/actor-name" required>',
            'Format <span class="sa-mono">username/actor-name</span>. Default: <span class="sa-mono">' + esc(a.default) + '</span>. Used by the next scrape on ' + esc(K.titleCase(a.platform)) + '.') +
            '<label class="sa-check" style="margin-top:10px"><input type="checkbox" name="test" checked> Test the actor after saving</label>',
          onSubmit: async function (form, fd) {
            var v = String(fd.get('actor_id') || '').trim();
            if (!v) throw new Error('Enter an actor id.');
            if (!/^[\w.\-]+[\/~][\w.\-]+$/.test(v)) throw new Error('Use the form username/actor-name.');
            await api(A + '/platforms/' + encodeURIComponent(a.platform) + '/actor', { method: 'POST', body: { kind: actorKind(a.key), actor_id: v } });
            toast('Actor saved');
            if (fd.get('test')) {
              try { var x = await api(A + '/actors/test', { method: 'POST', body: { key: a.key } }); toast(x.ok ? 'Actor ' + x.actor_id + ' reachable' : 'Saved, but the test failed: ' + (x.error || ''), x.ok ? 'success' : 'warning'); }
              catch (err) { toast(err.message, 'error'); }
            }
            return true;
          } });
        if (ok) K.route();
      };
      if (r) r.onclick = async function () {
        if (!(await K.confirmDialog({ title: 'Reset ' + a.key + '?', message: 'The actor goes back to the default ' + a.default + '. The next scrape uses it.', confirmLabel: 'Reset', danger: false }))) return;
        try { await api(A + '/platforms/' + encodeURIComponent(a.platform) + '/actor', { method: 'POST', body: { kind: actorKind(a.key), actor_id: a.default } }); toast('Actor reset to the default'); K.route(); }
        catch (err) { toast(err.message, 'error'); }
      };
    });
  }

  // ════════════════ USAGE & COST ════════════════
  var DAYS = [7, 30, 90, 365];
  // what people read for an actor: what LeadAI uses it for, else its Apify title / name, else the id
  function actorLabel(a) { return a.actor_label || a.actor_title || a.actor_name || a.actor; }

  async function viewUsage(root, q) {
    var days = DAYS.indexOf(+q.days) >= 0 ? +q.days : 30;
    var d = await api(A + '/usage?days=' + days);
    var acts = d.actors || [];
    var avg = d.runs_with_usage ? d.total_cost / d.runs_with_usage : null;
    root.innerHTML = tourHead(K.header('Usage & cost', 'What each Apify actor (scraping tool) cost, added up from the real cost Apify reported for every run. Nothing is estimated.',
      badge(usd(d.total_cost) + ' total · ' + fmtN(d.runs_with_usage) + ' runs with usage', d.total_cost > 0 ? 'warning' : 'muted') +
      '<label class="sr-only" for="egDays">Period</label><select class="form-select eg-hsel" id="egDays">' + DAYS.map(function (n) { return '<option value="' + n + '"' + (n === days ? ' selected' : '') + '>Last ' + n + ' days</option>'; }).join('') + '</select>'), 'head-usage') +
      '<div class="alert alert-info"><div class="alert-body">' + esc(d.note || '') + '</div></div>' +
      '<div class="sa-grid sa-kpis sa-section">' +
      K.kpi('Total cost', usd(d.total_cost), 'last ' + days + ' days', { icon: 'coin' }) +
      K.kpi('Runs with usage', fmtN(d.runs_with_usage), 'runs that reported a cost', { icon: 'chart' }) +
      K.kpi('Actors', fmtN(acts.length), 'see the registry', { href: '#/actors', icon: 'cpu' }) +
      K.kpi('Average per run', avg == null ? '—' : usd(avg), 'runs with usage only', { icon: 'tag' }) + '</div>' +
      '<div class="sa-card sa-section"><h3>Cost by actor <span class="sa-small sa-muted">last ' + days + ' days</span></h3>' +
      (acts.length ? K.barList(acts.map(function (a) { return { label: actorLabel(a) + ' · ' + fmtN(a.runs) + ' runs · ' + (a.cost > 0 ? usd(a.cost) : 'cost not reported'), value: a.runs }; }))
        : K.emptyState('No usage', 'No Apify runs with usage data in this period.')) + '</div>' +
      '<div class="sa-card sa-section"><h3>By actor</h3><div data-list></div></div>';
    $('#egDays', root).onchange = function () { K.go('#/usage?days=' + this.value); };
    var total = acts.reduce(function (s, a) { return s + (Number(a.cost) || 0); }, 0);
    K.listView($('[data-list]', root), { data: function () { return Promise.resolve(acts); }, key: 'eng_usage', exportName: 'usage', sort: '-cost',
      filters: [{ key: 'q', label: 'Search actor…' }],
      columns: [
        { label: 'Actor', sort: 'actor_label', render: function (a) {
          var sub = [a.actor_name, a.actor_name && a.actor_title && a.actor_title !== actorLabel(a) ? a.actor_title : null].filter(Boolean).join(' · ');
          return '<b>' + esc(actorLabel(a)) + '</b><div class="sa-small sa-muted">' + (sub ? esc(sub) + ' · ' : '') + '<span class="sa-mono" title="Apify actor id">' + esc(a.actor) + '</span></div>';
        }, csv: function (a) { return actorLabel(a) + (a.actor_name ? ' (' + a.actor_name + ')' : '') + ' [' + a.actor + ']'; } },
        { label: 'Runs', sort: 'runs', cls: 'num', render: function (a) { return fmtN(a.runs); }, csv: function (a) { return a.runs; } },
        { label: 'Cost (USD)', sort: 'cost', cls: 'num', render: function (a) { return a.cost > 0 ? usd(a.cost) : badge('not reported', 'muted'); }, csv: function (a) { return a.cost; } },
        { label: 'Share', sort: 'cost', cls: 'num', render: function (a) { return total > 0 ? (a.cost * 100 / total).toFixed(1) + '%' : '—'; } }],
      empty: { title: 'No usage', desc: 'No Apify runs with usage data in this period.' } });
  }

  // ════════════════ LIMITS ════════════════
  var LIMIT_META = {
    'limits.min_comments': { label: 'Min comments', hint: 'Ignore comments shorter than this many characters.', min: 0, max: 500 },
    'limits.max_posts_default': { label: 'Max posts (default)', hint: 'Post URLs collected per query by default.', min: 1, max: 100 },
    'limits.max_posts_cap': { label: 'Max posts (cap)', hint: 'Hard ceiling for the post limit on any query.', min: 1, max: 500 },
    'limits.max_comments_per_post_default': { label: 'Comments / post (default)', hint: 'Comments collected per post by default.', min: 1, max: 500 },
    'limits.max_comments_per_post_cap': { label: 'Comments / post (cap)', hint: 'Hard ceiling per post.', min: 1, max: 2000 },
    'limits.global_max_comments': { label: 'Global comment budget', hint: 'Total comments collected per query.', min: 1, max: 20000, step: 10 }
  };
  var COST_META = {
    'cost.stop_on_limit': { label: 'Stop on limit', hint: 'Abort a run when the comment budget is reached instead of over-collecting.' },
    'cost.warn_before_expensive': { label: 'Warn before expensive runs', hint: 'Show a cost warning before starting very large scrapes.' }
  };
  async function viewLimits(root) {
    var man = canManage();
    var d = await api(A + '/limits');
    var set = d.settings || {}, use = d.usage || {};
    root.innerHTML = K.header('Scraping & global limits', 'Scrape quotas and cost protection. Saved at once — running scrapers respect them on the next query.') +
      (man ? '' : roleNote('the limits')) +
      '<div class="sa-grid sa-kpis">' +
      K.kpi('Pages stored', fmtN(use.pages), 'collected pages', { href: '#/pages', icon: 'file' }) +
      K.kpi('Posts stored', fmtN(use.posts), 'collected posts', { href: '#/posts', icon: 'msg' }) +
      K.kpi('Comments stored', fmtN(use.comments), 'all platforms', { href: '#/ci', icon: 'msg' }) +
      K.kpi('Leads', fmtN(use.leads), 'analyzed as leads', { href: '#/leads', icon: 'star' }) + '</div>' +
      '<div class="sa-grid sa-2 sa-section"><div class="sa-card" data-tour="limits-scrape"><h3>Scrape limits <span class="sa-small sa-muted">saved as you type</span></h3>' +
      Object.keys(LIMIT_META).map(function (k) {
        return numRow(k, LIMIT_META[k], set[k], !man, k === 'limits.global_max_comments' ? '<div class="sa-small sa-muted">Currently stored: <b>' + fmtN(use.comments) + '</b> comments</div>' : '');
      }).join('') + '</div>' +
      '<div class="sa-card"><h3>Cost protection</h3>' + Object.keys(COST_META).map(function (k, i) { return toggleRow('egCost' + i, k, COST_META[k].label, COST_META[k].hint, set[k], !man); }).join('') + '</div></div>';
    if (man) bindAutoSave(root, A + '/limits', function (k) { return LIMIT_META[k] || COST_META[k]; });
  }

  // ════════════════ LEAD SCORING ════════════════
  var SCORING_GROUPS = [
    ['Weights', [
      ['scoring.confidence_weight', { label: 'Confidence', hint: 'AI confidence in the analysis.', min: 0, max: 100 }],
      ['scoring.priority_weight', { label: 'Priority', hint: 'Priority detected in the comment.', min: 0, max: 100 }],
      ['scoring.quality_weight', { label: 'Quality', hint: 'Overall comment quality.', min: 0, max: 100 }],
      ['scoring.contact_phone', { label: 'Phone', hint: 'Points when a phone number is present.', min: 0, max: 50 }],
      ['scoring.contact_email', { label: 'Email', hint: 'Points when an email is present.', min: 0, max: 50 }],
      ['scoring.spam_penalty', { label: 'Spam penalty', hint: 'Points subtracted for spam signals.', min: 0, max: 100 }]]],
    ['Signals', [
      ['scoring.phone', { label: 'Phone signal', hint: 'Signal points for a phone.', min: 0, max: 50 }],
      ['scoring.email', { label: 'Email signal', hint: 'Signal points for an email.', min: 0, max: 50 }],
      ['scoring.budget', { label: 'Budget', hint: 'Signal points for a stated budget.', min: 0, max: 50 }],
      ['scoring.urgency', { label: 'Urgency', hint: 'Signal points for urgency.', min: 0, max: 50 }],
      ['scoring.location', { label: 'Location', hint: 'Signal points for a location.', min: 0, max: 50 }],
      ['scoring.buying_intent', { label: 'Buying intent', hint: 'Signal points for buying intent.', min: 0, max: 50 }]]],
    ['Thresholds', [
      ['scoring.hot_min', { label: 'Hot minimum', hint: 'A score at or above this is a hot lead.', min: 0, max: 100 }],
      ['scoring.warm_min', { label: 'Warm minimum', hint: 'A score at or above this is a warm lead.', min: 0, max: 100 }]]]
  ];
  var SCORING_TOGGLES = { 'scoring.derive_quality': { label: 'Derive quality', hint: 'Infer quality from the signal score when no explicit quality is reported.' } };
  var SCORING_META = {};
  SCORING_GROUPS.forEach(function (g) { g[1].forEach(function (k) { SCORING_META[k[0]] = k[1]; }); });
  async function viewScoring(root) {
    var man = canManage();
    var set = (await api(A + '/scoring')).settings || {};
    root.innerHTML = K.header('Lead scoring', 'Weights, signals and thresholds of the deterministic score. Changes apply to the next analysis — collected leads are untouched.') +
      (man ? '' : roleNote('the scoring')) +
      '<div class="sa-grid sa-2" data-tour="scoring-groups">' + SCORING_GROUPS.map(function (g) {
        return '<div class="sa-card"><h3>' + esc(g[0]) + '</h3>' + g[1].map(function (k) { return numRow(k[0], k[1], set[k[0]], !man); }).join('') +
          (g[0] === 'Thresholds' ? '<div data-thr aria-live="polite"></div>' : '') + '</div>';
      }).join('') +
      '<div class="sa-card"><h3>Behavior</h3>' + Object.keys(SCORING_TOGGLES).map(function (k, i) { return toggleRow('egSc' + i, k, SCORING_TOGGLES[k].label, SCORING_TOGGLES[k].hint, set[k], !man); }).join('') + '</div></div>';
    var thr = function () {
      var hot = Number($('#eg_scoring_hot_min', root).value), warm = Number($('#eg_scoring_warm_min', root).value);
      $('[data-thr]', root).innerHTML = warm >= hot ? '<div class="alert alert-warning" style="margin-top:10px"><div class="alert-body">Warm minimum (' + esc(warm) + ') should be below hot minimum (' + esc(hot) + '), otherwise no lead is ever warm.</div></div>' : '';
    };
    thr();
    $$('#eg_scoring_hot_min,#eg_scoring_warm_min', root).forEach(function (i) { i.addEventListener('input', thr); });
    if (man) bindAutoSave(root, A + '/scoring', function (k) { return SCORING_META[k] || SCORING_TOGGLES[k]; });
  }

  // ════════════════ KEYWORD RULES ════════════════
  var KR_STATUS = [['all', 'All'], ['MATCHED', 'Matched'], ['NOT_MATCHED', 'Not matched'], ['NO_FILTER', 'No filter']];
  var MODE_LABEL = { any: 'Any keyword', all: 'All keywords', category: 'Any category keyword', advanced: 'Keyword groups' };
  function ruleId(r) { return String(r.id || r._id || ''); }
  function catName(key, cat) {
    var c = (cat.categories || []).filter(function (x) { return x.key === key; })[0] || (cat.presets || []).filter(function (x) { return x.key === key; })[0];
    if (c) return (c.icon ? c.icon + ' ' : '') + c.name;
    var cc = (cat.custom_categories || []).filter(function (x) { return x.key === key || String(x._id) === key; })[0];
    return cc ? (cc.icon || '🏷️') + ' ' + cc.name : key;
  }
  function filterPill(s) {
    return s === 'MATCHED' ? pill('active', 'Matched') : s === 'NOT_MATCHED' ? pill('failed', 'Skipped') : s === 'NO_FILTER' ? pill('inactive', 'No filter') : '<span class="sa-muted">—</span>';
  }
  async function viewKeywordRules(root, q) {
    var man = canManage();
    var res = await Promise.all([api(CF + '/rules'), api(CF + '/stats').catch(function () { return null; })]);
    var d = res[0], stats = res[1];
    var rules = d.rules || [], cat = d.catalog || { categories: [], presets: [], custom_categories: [] };
    var t = (stats && stats.totals) || {}, ai = (stats && stats.ai_saved) || {};
    var ctx = { rules: rules, cat: cat, stats: stats, man: man,
      cf: { status: KR_STATUS.some(function (s) { return s[0] === q.status; }) ? q.status : 'all', kw: q.kw || '' } };
    var active = d.active_rule;
    root.innerHTML = K.header('Keyword rules', 'Keyword and category filtering runs before AI analysis: only matching comments enter the lead pipeline. Filtered-out comments stay stored and skip the AI stage.',
      man ? '<button type="button" class="btn btn-primary btn-sm" data-new>+ New rule</button>' : '') +
      (man ? '' : roleNote('the keyword rules')) +
      (d.active_rule_id ? '<div class="alert alert-info" data-tour="kr-active"><div class="alert-body eg-banner"><div><div>Active rule — <b>' + esc((active || {}).name || '—') + '</b> applies to new scrapes that don\'t set their own filter.</div><div style="margin-top:4px">' + chips((active || {}).include_keywords, 'w') + '</div></div>' +
        (man ? '<button type="button" class="btn btn-secondary btn-sm" data-deact="' + esc(d.active_rule_id) + '">Deactivate</button>' : '') + '</div></div>'
        : '<div class="alert alert-warning" data-tour="kr-active"><div class="alert-body eg-banner"><div><div><b>No active rule</b> — every comment is processed (NO_FILTER).</div><div class="sa-small">Create a rule and activate it to filter comments before AI analysis.</div></div>' +
        (man ? '<button type="button" class="btn btn-secondary btn-sm" data-new>+ New rule</button>' : '') + '</div></div>') +
      '<div class="sa-grid sa-kpis sa-section">' +
      K.kpi('Comments filtered', fmtN(t.filtered || 0), 'of ' + fmtN(t.comments || 0) + ' total' + (t.coverage_pct != null ? ' · ' + t.coverage_pct + '%' : ''), { href: '#/keyword-rules?tab=comments&status=all', icon: 'msg' }) +
      K.kpi('Matched → AI', fmtN(t.matched || 0), t.match_pct != null ? t.match_pct + '% of filtered' : 'sent to the AI', { href: '#/keyword-rules?tab=comments&status=MATCHED', icon: 'star' }) +
      K.kpi('Skipped', fmtN(t.not_matched || 0), 'not matched · kept stored, no AI call', { href: '#/keyword-rules?tab=comments&status=NOT_MATCHED', icon: 'flag' }) +
      K.kpi('AI calls saved', fmtN(ai.comment_count || 0), 'counts only — costs are never estimated', { href: '#/ai', icon: 'brain' }) + '</div>' +
      '<div class="sa-section" data-tour="kr-tabs" data-krtabs></div>';
    $$('[data-new]', root).forEach(function (b) { b.onclick = function () { ruleEditor(null, ctx); }; });
    var db = $('[data-deact]', root);
    if (db) db.onclick = function () { ruleAction(db, 'deactivate', db.getAttribute('data-deact')); };
    K.tabs($('[data-krtabs]', root), [['rules', 'Rules (' + rules.length + ')'], ['comments', 'Filtered comments'], ['keywords', 'Top keywords'], ['categories', 'Categories']],
      q.tab || 'rules', function (key, el) { return KR_TABS[key](el, ctx); });
  }
  async function ruleAction(btn, action, id) {
    try {
      var r = await K.busy(btn, function () { return api(CF + '/rules/' + encodeURIComponent(id) + '/' + action, { method: 'POST' }); });
      toast(r.message || (action === 'activate' ? 'Rule activated' : 'Rule deactivated'));
      K.route();
    } catch (err) { toast(err.message, 'error'); }
  }
  var KR_TABS = {};
  KR_TABS.rules = function (el, ctx) {
    var perRule = {};
    ((ctx.stats || {}).per_rule || []).forEach(function (p) { if (p.rule_id) perRule[p.rule_id] = p; });
    el.innerHTML = '<div data-list></div>';
    return K.listView($('[data-list]', el), {
      data: function () { return Promise.resolve(ctx.rules); }, key: 'eng_kr_rules', exportName: 'keyword-rules', rowId: ruleId,
      filters: [{ key: 'q', label: 'Search rules…' },
        { key: 'state', type: 'select', label: 'Status', options: [['', 'Any status'], ['active', 'Active'], ['inactive', 'Inactive']], match: function (r, v) { return v === 'active' ? !!r.active : !r.active; } },
        { key: 'match_mode', type: 'select', label: 'Match mode', options: [['', 'Any match mode']].concat(Object.keys(MODE_LABEL).map(function (k) { return [k, MODE_LABEL[k]]; })) },
        { key: 'platform', type: 'select', label: 'Platform', options: [['', 'Any platform'], ['all', 'All platforms']].concat(PLATFORMS.map(function (p) { return [p, K.titleCase(p)]; })) }],
      columns: [
        { label: 'Rule', sort: 'name', render: function (r) { return '<div class="cell-main">' + esc(r.name || 'Untitled rule') + '</div><div class="cell-sub">' + esc(K.short(r.description || '', 80)) + '</div>'; }, csv: function (r) { return r.name; } },
        { label: 'Match', render: function (r) { return badge(MODE_LABEL[r.match_mode] || r.match_mode || 'any', 'primary'); }, csv: function (r) { return r.match_mode; } },
        { label: 'Platform', render: function (r) { return r.platform && r.platform !== 'all' ? platformBadge(r.platform) : '<span class="sa-muted">All</span>'; }, csv: function (r) { return r.platform || 'all'; } },
        { label: 'Keywords', render: function (r) { return chips(r.include_keywords, 'w') + ((r.exclude_keywords || []).length ? '<div style="margin-top:4px">' + chips(r.exclude_keywords.map(function (k) { return 'not ' + k; }), 'd', 4) + '</div>' : ''); }, csv: function (r) { return (r.include_keywords || []).join('; '); } },
        { label: 'Categories', render: function (r) { return chips((r.categories || []).map(function (k) { return catName(k, ctx.cat); }), 'p', 4); }, csv: function (r) { return (r.categories || []).join('; '); } },
        { label: 'Results', render: function (r) { var p = perRule[ruleId(r)]; return p ? '<span class="sa-small">' + fmtN(p.count) + ' checked<br>' + fmtN(p.matched) + ' matched · ' + fmtN(p.not_matched) + ' skipped</span>' : '<span class="sa-muted">—</span>'; }, hidden: false },
        { label: 'Created', sort: 'created_at', render: function (r) { return '<span class="sa-small">' + esc(dt(r.created_at)) + '</span>'; }, csv: function (r) { return r.created_at; } },
        { label: 'Status', sort: 'active', sortVal: function (r) { return r.active ? 1 : 0; }, render: function (r) { return r.active ? pill('active') : pill('inactive'); }, csv: function (r) { return r.active ? 'active' : 'inactive'; } },
        { label: '', cls: 'num', render: function (r) {
          var id = esc(ruleId(r));
          if (!ctx.man) return '<button type="button" class="btn btn-secondary btn-xs" data-a="view">View</button>';
          return '<span class="sa-row" style="justify-content:flex-end;flex-wrap:wrap;gap:4px;min-width:200px">' + (r.active ? '<button type="button" class="btn btn-secondary btn-xs" data-a="deactivate" data-id="' + id + '">Deactivate</button>' : '<button type="button" class="btn btn-primary btn-xs" data-a="activate" data-id="' + id + '">Activate</button>') +
            '<button type="button" class="btn btn-secondary btn-xs" data-a="edit">Edit</button><button type="button" class="btn btn-secondary btn-xs" data-a="reapply" title="Re-run this rule over stored comments">Reapply</button>' +
            '<button type="button" class="btn btn-danger btn-xs" data-a="delete" aria-label="Delete rule ' + esc(r.name || '') + '">Delete</button></span>';
        } }],
      bindRow: function (tr, r) {
        $$('[data-a]', tr).forEach(function (b) {
          b.onclick = function () {
            var a = b.getAttribute('data-a');
            if (a === 'view' || a === 'edit') return ruleEditor(r, ctx);
            if (a === 'activate' || a === 'deactivate') return ruleAction(b, a, ruleId(r));
            if (a === 'reapply') return reapplyRule(b, r);
            if (a === 'delete') return deleteRule(b, r);
          };
        });
      },
      empty: { title: 'No rules yet', desc: ctx.man ? 'Create one to filter comments before AI analysis.' : 'A manager can create one to filter comments before AI analysis.' } });
  };
  async function reapplyRule(btn, r) {
    if (!(await K.confirmDialog({ title: 'Reapply "' + (r.name || 'rule') + '" to stored comments?', message: 'Re-runs this rule over stored comments (up to 2,000) and refreshes every comment\'s filter status. Comments are never deleted. AI analysis is not re-run here.', confirmLabel: 'Reapply', danger: false }))) return;
    try {
      var res = await K.busy(btn, function () { return api(CF + '/rules/' + encodeURIComponent(ruleId(r)) + '/reapply', { method: 'POST' }); });
      var s = res.summary || {};
      toast('Reapplied: ' + fmtN(s.processed) + ' comments, ' + fmtN(s.matched) + ' matched, ' + fmtN(s.not_matched) + ' skipped');
      K.route();
    } catch (err) { toast(err.message, 'error'); }
  }
  async function deleteRule(btn, r) {
    if (!(await K.confirmDialog({ title: 'Delete "' + (r.name || 'rule') + '"?', message: 'The rule is deleted. Comments already stored are never deleted; they keep their filter status.' + (r.active ? ' This is the active rule: after deleting it no filter is active (every comment is processed).' : ''), confirmLabel: 'Delete rule' }))) return;
    try {
      var res = await K.busy(btn, function () { return api(CF + '/rules/' + encodeURIComponent(ruleId(r)), { method: 'DELETE' }); });
      toast(res.message || 'Rule deleted'); K.route();
    } catch (err) { toast(err.message, 'error'); }
  }
  function splitList(v) { return String(v || '').split(',').map(function (k) { return k.trim(); }).filter(Boolean); }
  async function ruleEditor(rule, ctx) {
    var man = ctx.man, st = rule || {}, cats = st.categories || [], cat = ctx.cat;
    var pillHtml = function (key, label, cls) {
      return '<label class="eg-cat' + (cls ? ' ' + cls : '') + '"><input type="checkbox" name="cat" value="' + esc(key) + '"' + (cats.indexOf(key) >= 0 ? ' checked' : '') + '><span>' + esc(label) + '</span></label>';
    };
    var body = '<fieldset style="border:0;margin:0;padding:0;min-width:0"' + (man ? '' : ' disabled') + '><div class="sa-form-grid">' +
      field('krName', 'Rule name', '<input class="form-input" id="krName" name="name" maxlength="120" value="' + esc(st.name || '') + '" placeholder="e.g. High-intent buyers — automotive">') +
      field('krDesc', 'Description', '<input class="form-input" id="krDesc" name="description" maxlength="500" value="' + esc(st.description || '') + '" placeholder="What does this rule capture?">') +
      field('krMode', 'Match mode', '<select class="form-select" id="krMode" name="match_mode">' + Object.keys(MODE_LABEL).map(function (k) { return '<option value="' + k + '"' + ((st.match_mode || 'any') === k ? ' selected' : '') + '>' + esc(MODE_LABEL[k]) + '</option>'; }).join('') + '</select>') +
      field('krPlat', 'Platform', '<select class="form-select" id="krPlat" name="platform"><option value="all">All platforms</option>' + PLATFORMS.map(function (p) { return '<option value="' + p + '"' + (st.platform === p ? ' selected' : '') + '>' + esc(K.titleCase(p)) + '</option>'; }).join('') + '</select>') +
      '</div><div class="sa-field" style="margin-top:12px"><span style="font-weight:600;font-size:13px" id="krCatsL">Categories &amp; presets <span class="sa-small sa-muted">(their keywords join your includes — click to toggle; dashed = preset)</span></span><div class="eg-cats" role="group" aria-labelledby="krCatsL">' +
      (cat.categories || []).map(function (c) { return pillHtml(c.key, (c.icon || '•') + ' ' + c.name); }).join('') +
      (cat.presets || []).map(function (p) { return pillHtml(p.key, (p.icon || '•') + ' ' + p.name, 'pre'); }).join('') +
      (cat.custom_categories || []).map(function (c) { return pillHtml(String(c._id), (c.icon || '🏷️') + ' ' + c.name); }).join('') + '</div></div>' +
      '<div class="sa-form-grid" style="margin-top:12px">' +
      field('krInc', 'Include keywords', '<textarea class="form-textarea" id="krInc" name="include" rows="4" placeholder="price, site visit, interested, booking">' + esc((st.include_keywords || []).join(', ')) + '</textarea>', 'Comma separated — “price” also matches “prices”.') +
      field('krExc', 'Exclude keywords', '<textarea class="form-textarea" id="krExc" name="exclude" rows="4" placeholder="spam, giveaway, follow back">' + esc((st.exclude_keywords || []).join(', ')) + '</textarea>', 'Always veto a comment, e.g. spam, follow back.') +
      '</div><div data-groups' + (st.match_mode === 'advanced' ? '' : ' hidden') + ' style="margin-top:12px">' +
      field('krGroups', 'Keyword groups', '<textarea class="form-textarea" id="krGroups" name="groups" rows="3" placeholder="price, cost, budget">' + esc((st.groups || []).map(function (g) { return g.join(', '); }).join('\n')) + '</textarea>', 'One group per line, comma separated — a comment must match one keyword in EVERY group.') + '</div>' +
      '<div class="sa-flag" style="margin-top:6px"><label class="sa-toggle"><input type="checkbox" id="krDet" name="detect_contacts"' + (st.detect_contacts ? ' checked' : '') + '><span></span></label><div><label for="krDet" style="font-weight:600;font-size:13px">Detect contact details by regex</label><div class="sa-small sa-muted">Phone numbers and emails in the comment also count as a match (the contact-signal preset behavior).</div></div></div></fieldset>';
    var saved = await K.openModal({ title: rule ? (man ? 'Edit rule — ' : 'Rule — ') + (rule.name || '') : 'New keyword rule', size: 'lg', body: body,
      submitLabel: man ? (rule ? 'Save changes' : 'Create rule') : null, cancelLabel: man ? 'Cancel' : 'Close',
      onOpen: function (form) {
        $('#krMode', form).addEventListener('change', function () { $('[data-groups]', form).hidden = this.value !== 'advanced'; });
        // read-only: every field is disabled, so put focus on Close (Esc and Tab then work inside the dialog)
        if (!man) setTimeout(function () { var x = $('footer [data-x]', form); if (x) x.focus(); }, 0);
      },
      onSubmit: async function (form, fd) {
        var payload = { name: String(fd.get('name') || '').trim() || 'Untitled rule', description: String(fd.get('description') || '').trim(),
          match_mode: fd.get('match_mode'), platform: fd.get('platform'), categories: fd.getAll('cat'),
          include_keywords: splitList(fd.get('include')), exclude_keywords: splitList(fd.get('exclude')),
          groups: String(fd.get('groups') || '').split('\n').map(splitList).filter(function (g) { return g.length; }),
          detect_contacts: !!fd.get('detect_contacts') };
        if (payload.match_mode === 'advanced' && !payload.groups.length) throw new Error('Keyword groups mode needs at least one group.');
        if (!payload.include_keywords.length && !payload.categories.length && !payload.groups.length && !payload.detect_contacts) throw new Error('Add keywords, a category or contact detection — otherwise the rule matches nothing.');
        var r = rule ? await api(CF + '/rules/' + encodeURIComponent(ruleId(rule)), { method: 'PUT', body: payload }) : await api(CF + '/rules', { method: 'POST', body: payload });
        toast(r.success ? (rule ? 'Rule updated' : 'Rule created') : 'Saved');
        return true;
      } });
    if (saved && man) K.route();
  }
  KR_TABS.comments = function (el, ctx) {
    var cf = ctx.cf, totals = {};
    el.innerHTML = '<div class="sa-chips" role="group" aria-label="Filter status" data-st></div><div data-kw></div><div data-list></div>';
    var lv;
    function renderChips() {
      $('[data-st]', el).innerHTML = KR_STATUS.map(function (s) {
        return '<button type="button" class="sa-chip' + (cf.status === s[0] ? ' active' : '') + '" aria-pressed="' + (cf.status === s[0]) + '" data-s="' + s[0] + '">' + esc(s[1]) + ' <b>' + fmtN(totals[s[0]] || 0) + '</b></button>';
      }).join('');
      $$('[data-s]', el).forEach(function (b) { b.onclick = function () { cf.status = b.getAttribute('data-s'); syncUrl(); renderChips(); lv.state.page = 1; lv.reload(); }; });
      $('[data-kw]', el).innerHTML = cf.kw ? '<div class="sa-row" style="margin-bottom:10px"><button type="button" class="btn btn-secondary btn-sm" data-clearkw>Clear keyword · ' + esc(cf.kw) + ' ✕</button></div>' : '';
      var ck = $('[data-clearkw]', el);
      if (ck) ck.onclick = function () { cf.kw = ''; syncUrl(); renderChips(); lv.state.page = 1; lv.reload(); };
    }
    function syncUrl() {
      try {
        var h = location.hash.split('?'), p = new URLSearchParams(h[1] || '');
        p.set('tab', 'comments');
        if (cf.status && cf.status !== 'all') p.set('status', cf.status); else p.delete('status');
        if (cf.kw) p.set('kw', cf.kw); else p.delete('kw');
        history.replaceState(null, '', h[0] + '?' + p.toString());
      } catch (e) { /* ignore */ }
    }
    renderChips();
    lv = K.listView($('[data-list]', el), {
      url: function (p) {
        return CF + '/comments' + qs({ status: cf.status, matched_keyword: cf.kw, q: p.q, platform: p.platform, rule_id: p.rule_id, contact: p.contact === 'true' ? 'true' : '',
          offset: (p.page - 1) * p.limit, limit: p.limit });
      },
      rowsKey: 'items', key: 'eng_kr_comments', exportName: 'filtered-comments',
      onData: function (d) { totals = d.totals || {}; renderChips(); },
      filters: [{ key: 'q', label: 'Search comment text…' },
        { key: 'platform', type: 'select', label: 'Platform', options: [['', 'All platforms']].concat(PLATFORMS.map(function (p) { return [p, K.titleCase(p)]; })) },
        { key: 'rule_id', type: 'select', label: 'Rule', options: [['', 'Any rule']].concat(ctx.rules.map(function (r) { return [ruleId(r), r.name || 'Untitled rule']; })) },
        { key: 'contact', type: 'select', label: 'Contact', options: [['', 'With or without contact'], ['true', 'Has contact details']] }],
      columns: [
        { label: 'Commenter', render: function (c) { return '<div class="cell-main">' + esc(c.commenter_name || '—') + '</div><div class="cell-sub">' + esc(dt(c.filter_timestamp || c.created_at)) + '</div>'; }, csv: function (c) { return c.commenter_name; } },
        { label: 'Comment', render: function (c) { return '<div style="max-width:360px">' + esc(K.short(c.comment_text || '', 160)) + '</div>' + ((c.excluded_keywords || []).length ? '<div style="margin-top:4px">' + chips(c.excluded_keywords, 'd') + '</div>' : ''); }, csv: function (c) { return c.comment_text; } },
        { label: 'Matched', render: function (c) { return chips(c.matched_keywords, 'w') + ((c.matched_categories || []).length ? '<div style="margin-top:4px">' + chips(c.matched_categories.map(function (k) { return catName(k, ctx.cat); }), 'p') + '</div>' : ''); }, csv: function (c) { return (c.matched_keywords || []).concat(c.matched_categories || []).join('; '); } },
        { label: 'Status', render: function (c) { return filterPill(c.keyword_filter_status); }, csv: function (c) { return c.keyword_filter_status; } },
        { label: 'Lead', render: function (c) { return c.is_lead ? badge('Lead · ' + (c.lead_score != null ? c.lead_score : '—'), 'warning') : c.lead_score != null ? badge(String(c.lead_score), 'muted') : '<span class="sa-small sa-muted">not analyzed</span>'; }, csv: function (c) { return c.is_lead ? 'lead ' + (c.lead_score != null ? c.lead_score : '') : c.lead_score != null ? c.lead_score : 'not analyzed'; } },
        { label: 'Platform', render: function (c) { return platformBadge(c.platform); }, csv: function (c) { return c.platform; } }],
      empty: { title: 'No comments', desc: 'No comments match these filters.' } });
    return lv;
  };
  KR_TABS.keywords = function (el, ctx) {
    var s = ctx.stats || {}, top = s.top_keywords || [], per = s.per_rule || [], ai = s.ai_saved || {};
    var max = Math.max.apply(null, top.map(function (k) { return k.count; }).concat([1]));
    el.innerHTML = '<div class="sa-grid sa-2"><div class="sa-card"><h3>Top keywords</h3><p class="sa-small sa-muted" style="margin-top:0">The keywords that matched comments most often. Click one to see its comments.</p>' +
      (top.length ? '<ul class="sa-barlist eg-kwlist">' + top.map(function (k) {
        return '<li tabindex="0" role="link" data-kw="' + esc(k.keyword) + '" title="Show comments matching ' + esc(k.keyword) + '"><span class="lbl"><span class="eg-kw"><span class="w">' + esc(k.keyword) + '</span></span></span>' +
          '<div class="sa-bar" aria-hidden="true"><i style="width:' + Math.max(2, k.count * 100 / max).toFixed(1) + '%"></i></div><span class="sa-mono">' + fmtN(k.count) + '</span></li>';
      }).join('') + '</ul>' : K.emptyState('No matched keywords yet', 'The filter has not matched any comments.')) + '</div>' +
      '<div class="sa-card"><h3>Results by rule</h3>' + (per.length ? '<div class="sa-table-wrap"><table class="sa-table"><thead><tr><th>Rule</th><th class="num">Checked</th><th class="num">Matched</th><th class="num">Skipped</th></tr></thead><tbody>' +
        per.map(function (p) { return '<tr><td>' + esc(p.name) + (p.active ? ' ' + pill('active') : '') + '</td><td class="num">' + fmtN(p.count) + '</td><td class="num">' + fmtN(p.matched) + '</td><td class="num">' + fmtN(p.not_matched) + '</td></tr>'; }).join('') +
        '</tbody></table></div>' : K.emptyState('No results yet', 'Rules have not been applied to any comment.')) +
      '<p class="sa-small sa-muted">' + esc(ai.note || '') + '</p></div></div>';
    $$('[data-kw]', el).forEach(function (li) {
      var open = function () { K.go('#/keyword-rules?tab=comments&kw=' + encodeURIComponent(li.getAttribute('data-kw'))); };
      li.onclick = open; li.onkeydown = function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } };
    });
  };
  KR_TABS.categories = async function (el, ctx) {
    var d = await api(CF + '/categories');
    var custom = d.categories || [], builtin = d.builtin || [], presets = ctx.cat.presets || [];
    el.innerHTML = '<div class="sa-card"><h3>Custom categories ' + (ctx.man ? '<button type="button" class="btn btn-primary btn-sm" data-newcat>+ New category</button>' : '') + '</h3>' +
      '<p class="sa-small sa-muted" style="margin-top:0">Your own keyword sets. Active ones can be picked in a rule like the built-in categories.</p><div data-custom></div></div>' +
      '<div class="sa-card sa-section"><h3>Built-in categories</h3><div data-builtin></div></div>' +
      '<div class="sa-card sa-section"><h3>Presets</h3><div data-presets></div></div>';
    var nc = $('[data-newcat]', el);
    if (nc) nc.onclick = function () { categoryEditor(null); };
    K.listView($('[data-custom]', el), { data: function () { return Promise.resolve(custom); }, key: 'eng_kr_custom', exportName: 'custom-categories', rowId: function (c) { return String(c._id); },
      filters: [{ key: 'q', label: 'Search categories…' }],
      columns: [
        { label: 'Category', sort: 'name', render: function (c) { return '<div class="cell-main">' + esc((c.icon || '🏷️') + ' ' + c.name) + '</div><div class="cell-sub">' + esc(c.description || '') + '</div>'; }, csv: function (c) { return c.name; } },
        { label: 'Keywords', render: function (c) { return chips(c.keywords, 'w', 10); }, csv: function (c) { return (c.keywords || []).join('; '); } },
        { label: 'Status', render: function (c) { return c.active === false ? pill('inactive') : pill('active'); }, csv: function (c) { return c.active === false ? 'inactive' : 'active'; } },
        { label: 'Created', sort: 'created_at', render: function (c) { return '<span class="sa-small">' + esc(dt(c.created_at)) + (c.created_by ? '<br><span class="sa-muted">' + esc(c.created_by) + '</span>' : '') + '</span>'; } },
        { label: '', cls: 'num', render: function (c) {
          return ctx.man ? '<span class="sa-row" style="justify-content:flex-end;gap:4px;flex-wrap:wrap"><button type="button" class="btn btn-secondary btn-xs" data-c="edit">Edit</button><button type="button" class="btn btn-secondary btn-xs" data-c="toggle">' + (c.active === false ? 'Activate' : 'Deactivate') + '</button><button type="button" class="btn btn-danger btn-xs" data-c="delete">Delete</button></span>' : '';
        } }],
      bindRow: function (tr, c) {
        $$('[data-c]', tr).forEach(function (b) {
          b.onclick = async function () {
            var a = b.getAttribute('data-c'), url = CF + '/categories/' + encodeURIComponent(c._id);
            if (a === 'edit') return categoryEditor(c);
            try {
              if (a === 'toggle') { await K.busy(b, function () { return api(url, { method: 'PUT', body: { active: c.active === false } }); }); toast(c.active === false ? 'Category activated' : 'Category deactivated'); }
              else {
                if (!(await K.confirmDialog({ title: 'Delete "' + c.name + '"?', message: 'The category is deleted. Rules that picked it no longer match its keywords; stored comments keep their filter status.', confirmLabel: 'Delete' }))) return;
                await K.busy(b, function () { return api(url, { method: 'DELETE' }); }); toast('Category deleted');
              }
              K.route();
            } catch (err) { toast(err.message, 'error'); }
          };
        });
      },
      empty: { title: 'No custom categories', desc: ctx.man ? 'Create one to group your own keywords.' : 'A manager can create one.' } });
    var catCols = [
      { label: 'Category', sort: 'name', render: function (c) { return '<div class="cell-main">' + esc((c.icon || '•') + ' ' + c.name) + '</div><div class="cell-sub">' + esc(c.description || '') + '</div>'; }, csv: function (c) { return c.name; } },
      { label: 'Key', render: function (c) { return '<span class="sa-mono sa-small">' + esc(c.key) + '</span>'; }, csv: function (c) { return c.key; } },
      { label: 'Keywords', render: function (c) { return chips(c.keywords, '', 10); }, csv: function (c) { return (c.keywords || []).join('; '); } }];
    K.listView($('[data-builtin]', el), { data: function () { return Promise.resolve(builtin); }, key: 'eng_kr_builtin', exportName: 'categories', limit: 10, filters: [{ key: 'q', label: 'Search…' }], columns: catCols });
    K.listView($('[data-presets]', el), { data: function () { return Promise.resolve(presets); }, key: 'eng_kr_presets', exportName: 'presets', limit: 10, filters: [{ key: 'q', label: 'Search…' }], columns: catCols });
  };
  async function categoryEditor(c) {
    c = c || null;
    var st = c || {};
    var ok = await K.openModal({ title: c ? 'Edit category — ' + c.name : 'New category', submitLabel: c ? 'Save changes' : 'Create category',
      body: '<div class="sa-form-grid">' + field('catName', 'Name', '<input class="form-input" id="catName" name="name" maxlength="120" required value="' + esc(st.name || '') + '">') +
        field('catIcon', 'Icon <span class="sa-muted">(optional)</span>', '<input class="form-input" id="catIcon" name="icon" maxlength="4" value="' + esc(st.icon || '') + '" placeholder="🏷️">', 'One emoji.') + '</div>' +
        field('catDesc', 'Description', '<input class="form-input" id="catDesc" name="description" maxlength="500" value="' + esc(st.description || '') + '">') +
        field('catKw', 'Keywords', '<textarea class="form-textarea" id="catKw" name="keywords" rows="4" placeholder="venue, decor, wedding">' + esc((st.keywords || []).join(', ')) + '</textarea>', 'Comma separated.'),
      onSubmit: async function (form, fd) {
        var body = { name: String(fd.get('name') || '').trim(), icon: String(fd.get('icon') || '').trim(), description: String(fd.get('description') || '').trim(), keywords: splitList(fd.get('keywords')) };
        if (!body.name) throw new Error('Enter a name.');
        if (!body.keywords.length) throw new Error('Add at least one keyword.');
        await api(c ? CF + '/categories/' + encodeURIComponent(c._id) : CF + '/categories', { method: c ? 'PUT' : 'POST', body: body });
        toast(c ? 'Category updated' : 'Category created');
        return true;
      } });
    if (ok) K.route();
  }

  // ════════════════ DATABASE ════════════════
  async function viewDatabase(root) {
    var d = await api(A + '/database');
    var st = d.db_stats || {}, cols = d.collections || [];
    var maxB = Math.max.apply(null, cols.map(function (c) { return c.size || 0; }).concat([1]));
    var docs = cols.reduce(function (s, c) { return s + (c.documents || 0); }, 0);
    root.innerHTML = K.header('Database', 'MongoDB state behind the scrapers — documents, indexes and size. Nothing here can be deleted.', '<button type="button" class="btn btn-secondary btn-sm" data-refresh>↻ Refresh</button>') +
      '<div class="sa-grid sa-2"><div class="sa-card" data-tour="db-conn"><h3>Connection <span class="sa-small sa-muted">dbStats</span></h3>' +
      (st.error ? '<div class="alert alert-warning"><div class="alert-body">' + esc(st.error) + '. The collection counts below are still live.</div></div>'
        : '<dl class="sa-kv"><dt>Status</dt><dd>' + pill('active', 'Connected') + '</dd><dt>Database</dt><dd class="sa-mono">' + esc(st.db || '—') + '</dd>' +
        '<dt>Collections</dt><dd>' + fmtN(st.collections != null ? st.collections : cols.length) + '</dd><dt>Documents</dt><dd>' + fmtN(st.objects) + '</dd>' +
        '<dt>Data size</dt><dd>' + fmtBytes(st.dataSize) + '</dd><dt>Storage size</dt><dd>' + fmtBytes(st.storageSize) + '</dd>' +
        '<dt>Indexes</dt><dd>' + fmtN(st.indexes) + ' (' + fmtBytes(st.indexSize) + ')</dd><dt>Average object</dt><dd>' + fmtBytes(st.avgObjSize) + '</dd></dl>') + '</div>' +
      '<div class="sa-card"><h3>Main collections</h3>' + K.barList(cols.map(function (c) { return { label: c.name, value: c.documents || 0 }; })) +
      '<p class="sa-small sa-muted">' + fmtN(docs) + ' documents in the ' + cols.length + ' collections the console tracks. <a href="#/health">System health →</a></p></div></div>' +
      '<div class="sa-card sa-section"><h3>Collections <span class="sa-small sa-muted">click a row for its indexes</span></h3><div data-list></div></div>';
    $('[data-refresh]', root).onclick = function () { K.route(); };
    K.listView($('[data-list]', root), { data: function () { return Promise.resolve(cols); }, key: 'eng_db', exportName: 'collections', sort: '-documents',
      filters: [{ key: 'q', label: 'Search collections…' }],
      columns: [
        { label: 'Collection', sort: 'name', render: function (c) { return '<span class="sa-mono">' + esc(c.name) + '</span>'; }, csv: function (c) { return c.name; } },
        { label: 'Documents', sort: 'documents', cls: 'num', render: function (c) { return fmtN(c.documents); }, csv: function (c) { return c.documents; } },
        { label: 'Size', sort: 'size', sortVal: function (c) { return c.size || 0; }, render: function (c) { return '<div class="sa-row" style="flex-wrap:nowrap;min-width:150px"><div class="sa-bar" style="flex:1" aria-hidden="true"><i style="width:' + Math.max(2, (c.size || 0) * 100 / maxB).toFixed(1) + '%"></i></div><span class="sa-mono sa-small">' + fmtBytes(c.size) + '</span></div>'; }, csv: function (c) { return c.size; } },
        { label: 'Indexes', sort: 'idx', sortVal: function (c) { return (c.indexes || []).length; }, cls: 'num', render: function (c) { var n = (c.indexes || []).length, u = (c.indexes || []).filter(function (i) { return i.unique; }).length; return fmtN(n) + (u ? ' <span class="sa-small sa-muted">(' + u + ' unique)</span>' : ''); }, csv: function (c) { return (c.indexes || []).length; } }],
      rowClick: function (c) {
        K.openDrawer('Indexes · ' + c.name, function (body) {
          var ix = c.indexes || [];
          body.innerHTML = '<dl class="sa-kv"><dt>Documents</dt><dd>' + fmtN(c.documents) + '</dd><dt>Size</dt><dd>' + fmtBytes(c.size) + '</dd><dt>Indexes</dt><dd>' + fmtN(ix.length) + '</dd></dl>' +
            (ix.length ? '<div class="sa-table-wrap sa-section"><table class="sa-table"><thead><tr><th>Index</th><th>Keys</th><th>Unique</th></tr></thead><tbody>' + ix.map(function (i) {
              return '<tr><td class="sa-mono sa-small">' + esc(i.name) + '</td><td>' + chips(i.keys, '', 6) + '</td><td>' + (i.unique ? pill('active', 'Unique') : '<span class="sa-muted">—</span>') + '</td></tr>';
            }).join('') + '</tbody></table></div>' : K.emptyState('No indexes reported'));
        });
      },
      empty: { title: 'No collections' } });
  }

  // ════════════════ LOGS ════════════════
  var LEVELS = [['CRITICAL', 'critical', 'Critical', 'danger'], ['ERROR', 'error', 'Errors', 'danger'], ['WARNING', 'warning', 'Warnings', 'warning'], ['INFO', 'info', 'Info', 'info'], ['DEBUG', 'debug', 'Debug', 'muted']];
  var LEVEL_TONE = { CRITICAL: 'danger', ERROR: 'danger', WARNING: 'warning', INFO: 'info', DEBUG: 'muted' };
  var SOURCE_TONE = { Database: 'info', Apify: 'primary', AI: 'primary', Search: 'success', Authentication: 'warning', System: 'neutral', API: 'info', Application: 'neutral' };
  var LOG_PAGE = 300; // the server returns at most 300 entries per call
  function levelBadge(l) { return badge(l || '—', LEVEL_TONE[l] || 'neutral'); }
  function sourceBadge(s) { return s ? badge(s, SOURCE_TONE[s] || 'neutral') : '<span class="sa-muted">—</span>'; }

  async function viewLogs(root, q) {
    var man = canManage();
    var lvl0 = String(q.level || '').toUpperCase();
    root.innerHTML = K.header('Logs', 'Structured application log viewer: search, filters and the details of every line.',
      '<label class="sa-check"><input type="checkbox" id="lgAuto" checked> Autoscroll</label>' +
      '<label class="sr-only" for="lgEvery">Auto-refresh</label><select class="form-select eg-hsel" id="lgEvery"><option value="0">Auto-refresh off</option><option value="10">Every 10 s</option><option value="30" selected>Every 30 s</option><option value="60">Every 60 s</option></select>' +
      '<button type="button" class="btn btn-secondary btn-sm" id="lgRefresh">↻ Refresh</button><button type="button" class="btn btn-secondary btn-sm" id="lgExport">⤓ Export</button>' +
      (man ? '<a class="btn btn-secondary btn-sm" href="' + A + '/logs/download" download>⤓ Download app.log</a>' : '')) +
      '<div class="eg-lsum" role="group" aria-label="Filter by level">' + LEVELS.map(function (l) {
        return '<button type="button" class="sa-kpi' + (l[3] === 'danger' ? ' warn' : '') + '" data-lv="' + l[0] + '" aria-pressed="' + (lvl0 === l[0]) + '" title="Show ' + l[2].toLowerCase() + ' only">' +
          '<div class="k"><span>' + esc(l[2]) + '</span></div><div class="v" data-n="' + l[1] + '">—</div></button>';
      }).join('') + '</div>' +
      '<div class="sa-filters sa-section" data-tour="log-filters"><div class="sa-q sa-grow"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg><label class="sr-only" for="lgQ">Search logs</label><input class="form-input" type="search" id="lgQ" placeholder="Search logs… (Enter)" value="' + esc(q.q || '') + '"></div>' +
      '<label class="sr-only" for="lgLevel">Level</label><select class="form-select" id="lgLevel"><option value="">All levels</option>' + LEVELS.map(function (l) { return '<option value="' + l[0] + '"' + (lvl0 === l[0] ? ' selected' : '') + '>' + l[0] + '</option>'; }).join('') + '</select>' +
      '<label class="sr-only" for="lgSource">Source</label><select class="form-select" id="lgSource"><option value="">All sources</option>' + (q.source ? '<option selected>' + esc(q.source) + '</option>' : '') + '</select>' +
      '<label class="sr-only" for="lgModule">Module</label><select class="form-select" id="lgModule"><option value="">All modules</option>' + (q.module ? '<option selected>' + esc(q.module) + '</option>' : '') + '</select>' +
      '<button type="button" class="btn btn-primary btn-sm" id="lgApply">Apply</button><button type="button" class="btn btn-ghost btn-sm" id="lgClear">Clear</button></div>' +
      '<div class="eg-logwrap" data-tour="log-table"><div class="eg-jump" id="lgJump" hidden><span>New logs may be available below.</span><button type="button" class="btn btn-primary btn-xs" id="lgLatest">Jump to latest</button></div>' +
      '<div class="eg-log" id="lgBox" tabindex="-1" aria-label="Log lines" aria-live="off"></div><div class="eg-logfoot" id="lgFoot"></div></div>' +
      '<div class="sa-card sa-section" data-tour="log-stats"><h3>Log statistics <span class="sa-row" style="gap:6px"><label class="sr-only" for="lgStatN">Lines</label><select class="form-select eg-hsel" id="lgStatN"><option value="500">Last 500 lines</option><option value="1000">Last 1,000 lines</option><option value="2000">Last 2,000 lines</option><option value="5000">Last 5,000 lines</option></select></span></h3><div id="lgStats">' + K.skeleton('rows') + '</div></div>';

    var box = $('#lgBox', root), foot = $('#lgFoot', root), jump = $('#lgJump', root);
    var rows = [], win = { start: 0, total: 0 }, userScrolled = false, seq = 0, timer = null;
    function filters() {
      return { q: $('#lgQ', root).value.trim(), level: $('#lgLevel', root).value, source: $('#lgSource', root).value, module: $('#lgModule', root).value };
    }
    function fetchAt(offset, lines) { return api(A + '/logs' + qs(Object.assign({ lines: lines || 1000, structured: 'true', offset: offset || '' }, filters()))); }
    function fillSelect(sel, list, all) {
      var cur = sel.value;
      if (!list || !list.length) return;
      if (cur && list.indexOf(cur) < 0) list = list.concat([cur]);
      sel.innerHTML = '<option value="">' + all + '</option>' + list.map(function (x) { return '<option' + (x === cur ? ' selected' : '') + '>' + esc(x) + '</option>'; }).join('');
    }
    function rowHtml(e, i) {
      // what happened, in plain words (the server explains common errors); raw text on hover / in the detail
      var msg = e.summary || e.event || e.message || '';
      var extra = e.stack_trace ? ' <span class="sa-small sa-muted">· details</span>' : '';
      return '<div class="eg-lrow" role="button" tabindex="0" data-i="' + i + '" aria-label="' + esc((e.level || '') + ' ' + (e.timestamp || '') + ': ' + K.short(msg, 80)) + '">' +
        '<span class="t">' + esc(e.timestamp || '—') + '</span><span>' + levelBadge(e.level) + '</span><span class="mod" title="' + esc(e.module || '') + '">' + esc((e.module || '').split('.').pop() || '—') + '</span>' +
        '<span class="msg" title="' + esc(K.short(e.message || '', 400)) + '">' + esc(K.short(msg, 140)) + extra + '</span><span class="src">' + sourceBadge(e.source) + '</span></div>';
    }
    function renderRows() {
      box.innerHTML = (win.start > 0 ? '<div class="eg-older"><button type="button" class="btn btn-secondary btn-xs" id="lgOlder">Load ' + fmtN(Math.min(LOG_PAGE, win.start)) + ' older entries</button></div>' : '') +
        rows.map(rowHtml).join('');
      var ob = $('#lgOlder', box);
      if (ob) ob.onclick = function () { loadOlder(ob); };
      foot.innerHTML = '<span>Showing <b>' + fmtN(rows.length ? win.start + 1 : 0) + '–' + fmtN(win.start + rows.length) + '</b> of <b>' + fmtN(win.total) + '</b> entries <span class="sa-muted">(newest at the bottom · from the last 1,000 log lines)</span></span>' +
        '<button type="button" class="btn btn-secondary btn-xs" id="lgCsv">⤓ Export CSV</button>';
      $('#lgCsv', foot).onclick = exportCsv;
    }
    function showState(html) { box.innerHTML = html; foot.innerHTML = ''; }
    function applySummary(d) {
      var sm = d.summary || {};
      LEVELS.forEach(function (l) { var n = $('[data-n="' + l[1] + '"]', root); if (n) n.textContent = fmtN(sm[l[1]] || 0); });
      fillSelect($('#lgSource', root), d.sources, 'All sources');
      fillSelect($('#lgModule', root), d.modules, 'All modules');
      var lv = $('#lgLevel', root).value;
      $$('[data-lv]', root).forEach(function (b) { b.setAttribute('aria-pressed', String(b.getAttribute('data-lv') === lv)); });
    }
    async function load(silent) {
      var my = ++seq;
      if (!silent) showState(K.skeleton('rows'));
      try {
        var d = await fetchAt(0);
        var total = d.total || 0, start = Math.max(0, total - LOG_PAGE);
        if (start > 0) d = await fetchAt(start);
        if (my !== seq || !root.isConnected) return;
        applySummary(d);
        rows = d.entries || []; win = { start: start, total: total };
        if (!rows.length) {
          var filtered = Object.values(filters()).some(Boolean);
          showState(K.emptyState('No logs found', filtered ? 'Nothing matches these filters.' : 'The log file is empty or missing.', filtered ? '<button type="button" class="btn btn-secondary btn-sm" id="lgEmptyClear">Clear filters</button>' : ''));
          var ec = $('#lgEmptyClear', box); if (ec) ec.onclick = clearFilters;
          return;
        }
        renderRows();
        if ($('#lgAuto', root).checked && !userScrolled) box.scrollTop = box.scrollHeight;
      } catch (err) {
        if (my !== seq || !root.isConnected) return;
        if (silent) return; // an auto-refresh hiccup keeps the lines on screen
        showState(K.errorState(err, function () { load(); }));
      }
    }
    async function loadOlder(btn) {
      var from = Math.max(0, win.start - LOG_PAGE);
      try {
        var d = await K.busy(btn, function () { return fetchAt(from); });
        var older = (d.entries || []).slice(0, win.start - from);
        var h0 = box.scrollHeight, top0 = box.scrollTop;
        rows = older.concat(rows); win.start = from;
        renderRows();
        box.scrollTop = top0 + (box.scrollHeight - h0);
      } catch (err) { toast(err.message, 'error'); }
    }
    function apply() { userScrolled = false; jump.hidden = true; load(); }
    function clearFilters() { $('#lgQ', root).value = ''; $('#lgLevel', root).value = ''; $('#lgSource', root).value = ''; $('#lgModule', root).value = ''; apply(); }
    function openDetail(e) {
      if (!e) return;
      var f = [['Timestamp', e.timestamp], ['Level', e.level], ['Module', e.module], ['Source', e.source], ['Where in LeadAI', e.where], ['Error type', e.error_type], ['Error message', e.error_message], ['Error code', e.error_code],
        ['Platform', e.platform], ['Run ID', e.run_id], ['Request ID', e.request_id], ['Job ID', e.job_id], ['Endpoint', e.endpoint], ['HTTP method', e.http_method],
        ['HTTP status', e.http_status], ['Duration', e.duration], ['Collection', e.collection]].filter(function (x) { return x[1] != null && x[1] !== ''; });
      K.openModal({ title: 'Log detail — ' + (e.level || 'Unknown'), size: 'lg', submitLabel: null, cancelLabel: 'Close',
        body: '<div class="alert alert-' + (/ERROR|CRITICAL/.test(e.level || '') ? 'danger' : e.level === 'WARNING' ? 'warning' : 'info') + '"><div class="alert-body">' +
          '<b>What happened:</b> ' + esc(e.summary || e.event || e.message || '') +
          (e.hint ? '<br><b>What to do:</b> ' + esc(e.hint) : '') +
          (e.where ? '<br><b>Where:</b> <span class="sa-mono">' + esc(e.where) + '</span>' : '') + '</div></div>' +
          '<h4 class="eg-h4">Technical details</h4><dl class="sa-kv">' + f.map(function (x) {
          var v = x[0] === 'Run ID' ? '<a class="sa-link" href="#/jobs/' + encodeURIComponent(x[1]) + '">' + esc(x[1]) + '</a>' : x[0] === 'Level' ? levelBadge(x[1]) : x[0] === 'Source' ? sourceBadge(x[1]) : esc(String(x[1]));
          return '<dt>' + esc(x[0]) + '</dt><dd class="' + (/ID|Endpoint|Module/.test(x[0]) ? 'sa-mono' : '') + '">' + v + '</dd>';
        }).join('') + '</dl>' +
          '<h4 class="eg-h4">Full message</h4><pre class="eg-pre">' + esc(e.message || '') + '</pre>' +
          (e.stack_trace ? '<h4 class="eg-h4">Stack trace</h4><pre class="eg-pre">' + esc(e.stack_trace) + '</pre>' : '') +
          '<h4 class="eg-h4">Raw log</h4><pre class="eg-pre">' + esc(e.raw || '') + '</pre>' +
          '<div class="sa-row"><button type="button" class="btn btn-secondary btn-sm" data-cp="json">Copy JSON</button><button type="button" class="btn btn-secondary btn-sm" data-cp="raw">Copy raw</button><button type="button" class="btn btn-secondary btn-sm" data-cp="msg">Copy message</button></div>',
        onOpen: function (form) {
          $$('[data-cp]', form).forEach(function (b) {
            b.onclick = function () {
              var k = b.getAttribute('data-cp');
              copyText(k === 'json' ? JSON.stringify(e, null, 2) : k === 'raw' ? (e.raw || '') : (e.message || ''), k === 'json' ? 'JSON' : k === 'raw' ? 'Raw log' : 'Message');
            };
          });
        } });
    }
    async function exportCsv() {
      try {
        var d = await api(A + '/logs' + qs(Object.assign({ lines: 5000, structured: 'true' }, filters())));
        var all = d.entries || [];
        if (!all.length) { toast('No logs to export', 'warning'); return; }
        var cols = ['timestamp', 'level', 'module', 'source', 'summary', 'hint', 'where', 'message', 'error_type', 'error_message', 'run_id', 'raw'];
        downloadCsv('leadai-logs-' + new Date().toISOString().slice(0, 10) + '.csv', cols, all.map(function (e) { return cols.map(function (c) { return e[c] == null ? '' : e[c]; }); }));
        toast('Exported ' + fmtN(all.length) + ' log entries' + (d.total > all.length ? ' (the first ' + fmtN(all.length) + ' of ' + fmtN(d.total) + ')' : ''));
      } catch (err) { toast('Export failed: ' + err.message, 'error'); }
    }
    async function loadStats() {
      var el = $('#lgStats', root);
      el.innerHTML = K.skeleton('rows');
      try {
        var d = await api(A + '/logs/stats?lines=' + encodeURIComponent($('#lgStatN', root).value));
        var s = d.summary || {}, tot = s.total || 0, bad = (s.critical || 0) + (s.error || 0);
        el.innerHTML = '<div class="sa-grid sa-kpis">' + K.kpi('Entries', fmtN(tot), 'parsed log entries') +
          K.kpi('Errors & critical', fmtN(bad), tot ? (bad * 100 / tot).toFixed(1) + '% of entries' : '—', { tone: bad ? 'warn' : '' }) +
          K.kpi('Modules', fmtN((d.modules || []).length), 'that wrote a line') + K.kpi('Sources', fmtN((d.sources || []).length), 'areas of the app') + '</div>' +
          '<div class="sa-grid sa-2 sa-section"><div><h4 class="eg-h4" style="margin:0 0 8px">By level</h4>' +
          K.barList(LEVELS.map(function (l) { return { label: l[2], value: s[l[1]] || 0 }; }).concat(s.other ? [{ label: 'Other', value: s.other }] : [])) + '</div>' +
          '<div><h4 class="eg-h4" style="margin:0 0 8px">Sources</h4><div class="sa-chips">' + (d.sources || []).map(function (x) { return '<button type="button" class="sa-chip" data-fsrc="' + esc(x) + '">' + esc(x) + '</button>'; }).join('') + '</div>' +
          '<h4 class="eg-h4" style="margin:8px 0">Modules</h4><div class="sa-chips">' + (d.modules || []).map(function (x) { return '<button type="button" class="sa-chip" data-fmod="' + esc(x) + '">' + esc(x) + '</button>'; }).join('') + '</div>' +
          '<p class="sa-small sa-muted">Click a source or module to filter the log above.</p></div></div>';
        $$('[data-fsrc]', el).forEach(function (b) { b.onclick = function () { setSel('#lgSource', b.getAttribute('data-fsrc')); }; });
        $$('[data-fmod]', el).forEach(function (b) { b.onclick = function () { setSel('#lgModule', b.getAttribute('data-fmod')); }; });
      } catch (err) { el.innerHTML = K.errorState(err, loadStats); }
    }
    function setSel(sel, v) {
      var s = $(sel, root);
      if (!$$('option', s).some(function (o) { return o.value === v; })) s.insertAdjacentHTML('beforeend', '<option>' + esc(v) + '</option>');
      s.value = v; apply();
      root.querySelector('[data-tour="log-filters"]').scrollIntoView({ block: 'nearest' });
    }

    box.addEventListener('scroll', function () {
      var atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 30;
      if (atBottom) { userScrolled = false; jump.hidden = true; }
      else if ($('#lgAuto', root).checked) { userScrolled = true; jump.hidden = false; }
    });
    box.addEventListener('click', function (ev) { var r = ev.target.closest('.eg-lrow'); if (r) openDetail(rows[+r.getAttribute('data-i')]); });
    box.addEventListener('keydown', function (ev) {
      var r = ev.target.closest && ev.target.closest('.eg-lrow');
      if (!r) return;
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); openDetail(rows[+r.getAttribute('data-i')]); }
      if (ev.key === 'ArrowDown' && r.nextElementSibling) { ev.preventDefault(); r.nextElementSibling.focus(); }
      if (ev.key === 'ArrowUp' && r.previousElementSibling && r.previousElementSibling.classList.contains('eg-lrow')) { ev.preventDefault(); r.previousElementSibling.focus(); }
    });
    $('#lgRefresh', root).onclick = apply;
    $('#lgApply', root).onclick = apply;
    $('#lgClear', root).onclick = clearFilters;
    $('#lgLevel', root).onchange = apply;
    $('#lgQ', root).addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); apply(); } });
    $('#lgLatest', root).onclick = function () { apply(); };
    $('#lgExport', root).onclick = exportCsv;
    $('#lgStatN', root).onchange = loadStats;
    $$('[data-lv]', root).forEach(function (b) {
      b.onclick = function () { var sel = $('#lgLevel', root), lv = b.getAttribute('data-lv'); sel.value = sel.value === lv ? '' : lv; apply(); };
    });
    $('#lgAuto', root).onchange = function () { if (this.checked) { userScrolled = false; jump.hidden = true; box.scrollTop = box.scrollHeight; } else jump.hidden = true; };
    var every = $('#lgEvery', root);
    every.onchange = function () {
      if (timer) clearInterval(timer);
      timer = null;
      var sec = +every.value;
      if (sec > 0) timer = setInterval(function () {
        // stops once the user has left the page (this view is gone); pauses while scrolled up or hidden
        if (!root.isConnected) { clearInterval(timer); timer = null; return; }
        if (!userScrolled && !document.hidden && !$('#saLayer .sa-overlay')) load(true);
      }, sec * 1000);
    };
    every.onchange();
    loadStats();
    await load();
  }

  // ════════════════ registration ════════════════
  var V = 'viewer';
  K.register('Lead engine', 'Operations', [
    ['apify', 'Apify', 'plug', null, V], ['actors', 'Actors', 'cpu', null, V], ['limits', 'Limits', 'shield', null, V],
    ['usage', 'Usage & cost', 'coin', null, V], ['scoring', 'Lead scoring', 'star', null, V], ['keyword-rules', 'Keyword rules', 'tag', null, V]
  ], {
    apify: [viewApify, 'Apify', null, V], actors: [viewActors, 'Actors', null, V], limits: [viewLimits, 'Scraping & global limits', null, V],
    usage: [viewUsage, 'Usage & cost', null, V], scoring: [viewScoring, 'Lead scoring', null, V], 'keyword-rules': [viewKeywordRules, 'Keyword rules', null, V]
  });
  K.register('System', 'Platform', [['database', 'Database', 'layers', null, V], ['logs', 'Logs', 'list', null, V]],
    { database: [viewDatabase, 'Database', null, V], logs: [viewLogs, 'Logs', null, V] });

  // ════════════════ guides ════════════════
  if (window.LeadTour) {
    var end = function () { $$('#saLayer .sa-overlay [data-x]').forEach(function (x) { x.click(); }); };
    window.LeadTour.define('sa-engine', { title: 'Apify & scraping', onEnd: end, steps: [
      { route: '#/apify', target: '[data-tour="apify-connection"]', title: 'The Apify connection',
        body: 'The masked token LeadAI uses for every scrape, where it comes from and when it was last tested.\n\nRun live test asks Apify whether the token can reach a scraper. It never runs by itself.' },
      { route: '#/apify', target: '[data-tour="apify-token"]', optional: true, title: 'One key, two places',
        body: 'The Apify token is the same key as Apify on the API keys page, where the Super Admin replaces, tests or resets it.' },
      { route: '#/actors', target: '[data-tour="actors-grid"]', title: 'Actors',
        body: 'Each Apify actor the scrapers call. An amber Override means it differs from the default; Test checks it can be reached, Edit changes it.' },
      { route: '#/limits', target: '[data-tour="limits-scrape"]', title: 'Scraping limits',
        body: 'Default and maximum posts and comments per query, and the global comment budget. Changes save as you type and apply to the next query.' },
      { route: '#/usage', target: '[data-tour="head-usage"]', title: 'Usage & cost',
        body: 'What Apify charged, added up from real run records. Nothing is estimated.' },
      { route: '#/keyword-rules', target: '[data-tour="kr-tabs"]', title: 'Keyword rules',
        body: 'Rules filter comments before the AI sees them. Only matching comments become leads; the rest stay stored and save AI calls.\n\nReapply re-runs a rule over stored comments.' }
    ] });
    window.LeadTour.define('sa-system', { title: 'Database & logs', onEnd: end, steps: [
      { route: '#/database', target: '[data-tour="db-conn"]', title: 'Database',
        body: 'MongoDB status, sizes and the main collections. Click a collection to see its indexes.' },
      { route: '#/logs', target: '[data-tour="log-filters"]', title: 'Search the logs',
        body: 'Search text, or filter by level, source or module. The level boxes above toggle a level with one click.' },
      { route: '#/logs', target: '[data-tour="log-table"]', title: 'Read a line',
        body: 'Newest lines are at the bottom and refresh on their own. Click a line for every detail, the stack trace and copy buttons.' },
      { route: '#/logs', target: '[data-tour="log-stats"]', title: 'Log statistics',
        body: 'How many errors, which modules and which parts of the app wrote to the log recently.' }
    ] });
    if (K.addGuide) {
      K.addGuide({ id: 'sa-engine', label: 'Apify & scraping', description: 'Apify connection, actors, limits, cost and keyword rules', route: 'apify' });
      K.addGuide({ id: 'sa-system', label: 'Database & logs', description: 'Database status and the log viewer', route: 'logs' });
    }
  }
})();
