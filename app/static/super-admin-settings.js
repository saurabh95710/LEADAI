/* ============================================================
   LeadAI — Super Admin › Settings (merged from the old /admin console)
   Two pages, registered through window.SAKit like super-admin-partners.js:
     #/settings     Global Settings (every registry tab, group and key; search,
                    save, reset, version history + rollback, import/export,
                    live brand preview, logo uploads)
     #/environment  Environment variables behind the guard-password lock
   Backend: /api/admin/settings* and /api/admin/env*. Viewers read, managers
   write, the guard password is Super Admin only. The server checks every call;
   the role checks here only hide controls a role cannot use.
   ============================================================ */
(function () {
  'use strict';
  var K = window.SAKit;
  if (!K) return;
  var esc = K.esc, $ = K.$, $$ = K.$$;
  var UI = window.LeadAIUI || {};
  var SB = '/api/admin/settings', EB = '/api/admin/env';

  (function () {
    var st = document.createElement('style');
    st.textContent = [
      '.gs-layout{display:grid;grid-template-columns:224px minmax(0,1fr);gap:16px;align-items:start}',
      '.gs-tabs{display:flex;flex-direction:column;gap:2px;position:sticky;top:76px;background:var(--glass-bg);border:1px solid var(--glass-border);border-radius:14px;padding:6px}',
      '.gs-tab{display:flex;align-items:center;gap:9px;width:100%;padding:8px 10px;border:0;border-radius:9px;background:none;color:var(--text-secondary);font-size:13px;font-weight:600;text-align:left;cursor:pointer}',
      '.gs-tab svg{width:16px;height:16px;flex-shrink:0}',
      '.gs-tab:hover{background:var(--surface-hover);color:var(--text-primary)}',
      '.gs-tab[aria-selected=true]{background:var(--primary-light);color:var(--text-primary)}',
      '.gs-tab:focus-visible{outline:2px solid var(--primary);outline-offset:-2px}',
      '.gs-tab .gs-dot{margin-left:auto;width:8px;height:8px;border-radius:50%;background:var(--warning);flex-shrink:0}',
      '.gs-tab-sep{height:1px;background:var(--border-subtle);margin:4px 6px}',
      '.gs-sec{font-size:10.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--sa-muted);padding:12px 10px 4px}',
      '.gs-sec:first-child{padding-top:4px}',
      '.gs-tab .gs-n{margin-left:auto;font-size:11px;font-weight:700;color:var(--sa-muted);background:var(--bg-tertiary);border-radius:999px;padding:1px 7px}',
      '.gs-tab .gs-n + .gs-dot{margin-left:6px}',
      '.gs-intro{display:flex;gap:10px 16px;align-items:center;justify-content:space-between;flex-wrap:wrap;padding:14px 16px;border:1px solid var(--glass-border);border-radius:14px;background:var(--bg-tertiary)}',
      '.gs-intro h2{margin:0 0 2px;font-size:16px}',
      '.gs-intro p{margin:0;font-size:13px;color:var(--text-secondary);max-width:760px}',
      '.gs-links{display:flex;gap:6px;flex-wrap:wrap;align-items:center}',
      '.gs-gdesc{margin:0 0 4px;font-size:12.5px;color:var(--text-secondary)}',
      '.gs-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));column-gap:28px}',
      '.gs-fields > .gs-wide{grid-column:1/-1}',
      '.gs-kv{display:grid;grid-template-columns:minmax(120px,170px) minmax(0,1fr);gap:8px 14px;margin:10px 0;font-size:13px}',
      '.gs-kv dt{color:var(--text-secondary)}',
      '.gs-kv dd{margin:0;min-width:0;overflow-wrap:anywhere}',
      '.gs-kv dd code{display:inline-block;margin:0 6px 4px 0;font-size:12px}',
      '.gs-alert{margin:8px 0}',
      '.gs-setup{margin:10px 0;border:1px solid var(--border-subtle);border-radius:12px;padding:10px 14px;background:var(--bg-tertiary)}',
      '.gs-setup summary{cursor:pointer;font-weight:650;font-size:13px}',
      '.gs-steps{margin:10px 0;padding-left:20px;font-size:13px;display:grid;gap:4px}',
      '.gs-preset{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:10px 0;font-size:13px}',
      '.gs-preset .form-select{max-width:280px}',
      '.gs-snip{display:flex;gap:8px;align-items:flex-start}',
      '.gs-snip pre{flex:1;min-width:0;margin:0;padding:10px 12px;border-radius:10px;background:var(--surface);border:1px solid var(--border);font-family:var(--font-mono);font-size:12.5px;white-space:pre-wrap;overflow-wrap:anywhere}',
      '.gs-test{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:10px;font-size:13px}',
      '.gs-test .form-input{flex:0 1 280px;min-width:0}',
      '.gs-test-out{font-size:12.5px;font-weight:600}',
      '.gs-test-out.good{color:var(--success-text)}',
      '.gs-test-out.bad{color:var(--danger-text)}',
      '.gs-provs{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}',
      '.gs-prov{border:1px solid var(--border-subtle);border-radius:12px;padding:12px 14px;min-width:0}',
      '.gs-prov h4{margin:0;font-size:14px}',
      '.gs-url{font-size:12px;overflow-wrap:anywhere}',
      '@media (max-width:1100px){.gs-provs{grid-template-columns:minmax(0,1fr)}}',
      '@media (max-width:640px){.gs-kv{grid-template-columns:minmax(0,1fr)}.gs-kv dd{margin-bottom:6px}.gs-snip{flex-direction:column}}',
      '.gs-toolbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:12px}',
      '.gs-toolbar .sa-q{flex:1 1 240px;max-width:440px}',
      '.gs-toolbar .sa-q .form-input{width:100%;padding-left:32px}',
      '.gs-stack{display:grid;gap:12px}',
      '.gs-card-h{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px}',
      '.gs-card-h h3{margin:0}',
      '.gs-card-h .gs-r{margin-left:auto;display:flex;gap:6px;flex-wrap:wrap}',
      '.gs-field{padding:12px 0;border-bottom:1px solid var(--border-subtle);display:grid;gap:6px;min-width:0}',
      '.gs-field:last-child{border-bottom:0;padding-bottom:2px}',
      '.gs-fields > .gs-field:nth-last-child(2):not(.gs-wide):nth-child(odd){border-bottom:0}',
      '.gs-lbl{display:flex;align-items:center;gap:6px;flex-wrap:wrap;font-size:13px;font-weight:600;color:var(--text-primary)}',
      '.gs-key{font-family:var(--font-mono);font-size:11px;color:var(--sa-muted);font-weight:500;overflow-wrap:anywhere}',
      '.gs-desc{font-size:12px;color:var(--sa-muted);margin:0;max-width:760px}',
      '.gs-ctl{display:flex;align-items:center;gap:8px;flex-wrap:wrap;max-width:600px;min-width:0}',
      '.gs-ctl .form-input,.gs-ctl .form-select{flex:1 1 200px;min-width:0}',
      '.gs-ctl .gs-hex{flex:0 1 150px}',
      '.gs-bool{display:flex;gap:12px;align-items:flex-start}',
      '.gs-bool > div{min-width:0;display:grid;gap:4px}',
      '.gs-pick{width:44px;height:36px;padding:2px;border:1px solid var(--border);border-radius:8px;background:var(--surface);cursor:pointer;flex:0 0 auto}',
      '.gs-pick:disabled{cursor:not-allowed;opacity:.6}',
      '.gs-json{font-family:var(--font-mono);font-size:12px;min-height:120px;width:100%;max-width:760px}',
      '.gs-ferr{font-size:12px;color:var(--danger-text);font-weight:600}',
      '.gs-ferr:empty{display:none}',
      '.gs-field.bad .form-input,.gs-field.bad .form-textarea,.gs-field.bad .form-select{border-color:var(--danger)}',
      '.gs-thumb{height:36px;max-width:120px;object-fit:contain;border:1px solid var(--border);border-radius:8px;background:var(--bg-tertiary);padding:3px}',
      '.gs-savebar{position:sticky;bottom:12px;z-index:20;margin-top:14px;display:flex;align-items:center;gap:8px 12px;flex-wrap:wrap;background:var(--bg-secondary);border:1px solid var(--primary-border);border-radius:14px;padding:10px 14px;box-shadow:var(--shadow-lg)}',
      '.gs-savebar[hidden]{display:none}',
      '.gs-savebar .gs-count{font-weight:700}',
      '.gs-savebar .gs-note{flex:1 1 200px;min-width:0}',
      '.gs-prev{display:flex;gap:18px;flex-wrap:wrap;align-items:center;justify-content:space-between;padding:14px;border:1px dashed var(--border);border-radius:12px;background:var(--bg-tertiary)}',
      '.gs-prev-head{display:flex;align-items:center;gap:12px;min-width:0}',
      '.gs-prev-logo{height:42px;max-width:170px;object-fit:contain}',
      '.gs-prev-mark{width:42px;height:42px;border-radius:11px;display:grid;place-items:center;color:var(--on-primary);font-weight:800;font-size:18px;flex-shrink:0}',
      '.gs-prev-name{font-weight:800;font-size:17px;overflow-wrap:anywhere}',
      '.gs-prev-tag{font-size:12.5px;color:var(--text-secondary);overflow-wrap:anywhere}',
      '.gs-prev-acts{display:flex;gap:8px;flex-wrap:wrap}',
      '.gs-prev-btn{display:inline-flex;padding:7px 14px;border-radius:9px;color:var(--on-primary);font-weight:650;font-size:12.5px}',
      '.gs-sw{display:flex;gap:10px 14px;flex-wrap:wrap;margin-top:10px;font-size:12px;color:var(--text-secondary)}',
      '.gs-sw span{display:inline-flex;align-items:center;gap:6px}',
      '.gs-sw i{width:14px;height:14px;border-radius:4px;border:1px solid var(--border)}',
      '.gs-hist{list-style:none;margin:0;padding:0}',
      '.gs-hist li{display:flex;gap:8px 12px;align-items:center;flex-wrap:wrap;padding:10px 0;border-bottom:1px solid var(--border-subtle)}',
      '.gs-hist li:last-child{border-bottom:0}',
      '.gs-hist .gs-grow{flex:1 1 260px;min-width:0}',
      '.gs-diff td{vertical-align:top;overflow-wrap:anywhere;font-size:12.5px}',
      '.gs-diff .old{color:var(--danger-text)}',
      '.gs-diff .new{color:var(--success-text)}',
      '.gs-scroll{max-height:340px;overflow:auto}',
      '.ev-row{display:flex;gap:10px 14px;align-items:flex-start;justify-content:space-between;flex-wrap:wrap;padding:12px 0;border-bottom:1px solid var(--border-subtle)}',
      '.ev-row:last-child{border-bottom:0;padding-bottom:2px}',
      '.ev-main{flex:1 1 320px;min-width:0;display:grid;gap:4px}',
      '.ev-name{display:flex;align-items:center;gap:6px;flex-wrap:wrap}',
      '.ev-name b{font-family:var(--font-mono);font-size:13px;overflow-wrap:anywhere}',
      '.ev-val{font-family:var(--font-mono);font-size:12.5px;overflow-wrap:anywhere;color:var(--text-secondary)}',
      '.ev-acts{display:flex;gap:6px;flex-wrap:wrap;align-items:center}',
      '.ev-edit{display:flex;gap:6px;flex-wrap:wrap;width:100%;align-items:center}',
      '.ev-edit .form-input,.ev-edit .form-select{flex:1 1 240px;min-width:0}',
      '.ev-form{display:grid;gap:10px;max-width:460px}',
      '.ev-cards{display:grid;gap:12px}',
      '@media (max-width:1180px){.gs-fields{grid-template-columns:minmax(0,1fr)}.gs-fields > .gs-field:nth-last-child(2):not(.gs-wide):nth-child(odd){border-bottom:1px solid var(--border-subtle)}}',
      '@media (max-width:900px){.gs-layout{grid-template-columns:minmax(0,1fr)}.gs-tabs{position:static;flex-direction:row;overflow-x:auto;padding:4px}.gs-tab{width:auto;white-space:nowrap}.gs-tab-sep,.gs-sec{display:none}}',
      '@media (max-width:640px){.gs-savebar{bottom:8px;padding:10px}.gs-savebar .gs-acts{width:100%;display:flex;gap:8px}.gs-savebar .gs-acts .btn{flex:1;justify-content:center}.ev-acts{width:100%}}'
    ].join('\n');
    document.head.appendChild(st);
  })();

  // ── shared helpers ─────────────────────────────────────────
  function canWrite() { return K.can('manager'); }
  function isSuper() { return K.can('super_admin'); }
  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : (many || one + 's')); }
  function svg(paths) { return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + paths + '</svg>'; }
  /** Server datetimes come back as ISO strings, often without a zone (Mongo stores UTC):
      read them as UTC. (The old console did `new Date(created_at * 1000)` → Invalid Date.) */
  function utc(v) {
    if (v == null || v === '') return null;
    if (typeof v === 'string' && /^\d{4}-\d{2}-\d{2}T[\d:.]+$/.test(v)) v += 'Z';
    var d = typeof v === 'number' ? new Date(v < 1e12 ? v * 1000 : v) : new Date(v);
    return isNaN(d.getTime()) ? null : d;
  }
  function when(v) { var d = utc(v); return d ? '<time datetime="' + esc(d.toISOString()) + '" title="' + esc(K.fmtDT(d)) + '">' + esc(K.fmtDT(d)) + ' · ' + esc(K.ago(d)) + '</time>' : '—'; }
  function errMsg(data, fallback) {
    if (UI.errorMessage) return UI.errorMessage(data, fallback);
    var d = data && data.detail;
    return typeof d === 'string' ? d : (d && d.message) || fallback;
  }
  /** K.api sends every 401 to the sign-in page. The guard-password calls answer a WRONG
      password with 401, so they go through this variant, which only signs out on a real
      "Sign in required". */
  async function call(method, url, body) {
    var init = { method: method, credentials: 'same-origin', headers: { Accept: 'application/json' } };
    if (body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
    var res;
    try { res = await fetch(url, init); } catch (e) { var ne = new Error('Network error — check your connection and try again.'); ne.status = 0; throw ne; }
    var data = {};
    try { data = await res.json(); } catch (e) { data = {}; }
    if (!res.ok) {
      var msg = errMsg(data, 'Request failed (' + res.status + ')');
      if (res.status === 401 && /sign in required|session/i.test(msg)) location.href = '/login?superadmin=1';
      var err = new Error(msg); err.status = res.status; err.data = data; throw err;
    }
    return data || {};
  }

  // ════════════════════════════════════════════════════════
  //  GLOBAL SETTINGS
  // ════════════════════════════════════════════════════════
  var CAT_IC = {
    general: '<path d="M3 21h18"/><path d="M5 21V7l8-4v18"/><path d="M19 21V11l-6-3"/>',
    branding: '<circle cx="12" cy="12" r="9"/><circle cx="8" cy="10" r="1.2"/><circle cx="12" cy="7.5" r="1.2"/><circle cx="16" cy="10" r="1.2"/><path d="M12 21a2.5 2.5 0 0 1 0-5h1.5a2.5 2.5 0 0 0 2.5-2.5"/>',
    appearance: '<rect x="3" y="4" width="18" height="16" rx="2"/><line x1="9" y1="4" x2="9" y2="20"/>',
    ai: '<path d="M9 3a3 3 0 0 0-3 3v1a3 3 0 0 0-2 5 3 3 0 0 0 2 5v1a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3z"/><path d="M15 3a3 3 0 0 1 3 3v1a3 3 0 0 1 2 5 3 3 0 0 1-2 5v1a3 3 0 0 1-6 0"/>',
    limits: '<circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/>',
    platforms: '<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15 15 0 0 1 0 20 15 15 0 0 1 0-20z"/>',
    features: '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><line x1="4" y1="22" x2="4" y2="15"/>',
    notifications: '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-10 6L2 7"/>',
    leads: '<polygon points="12 2 15 9 22 9.3 16.5 14 18.2 21 12 17.3 5.8 21 7.5 14 2 9.3 9 9"/>',
    seo: '<circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    payments: '<rect x="2" y="5" width="20" height="14" rx="2"/><line x1="2" y1="10" x2="22" y2="10"/><line x1="6" y1="15" x2="10" y2="15"/>',
    scoring: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.5"/>',
    detection: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/><line x1="8" y1="9" x2="16" y2="9"/><line x1="8" y1="13" x2="13" y2="13"/>',
    maintenance: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94z"/>',
    security: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    system: '<path d="M3 3v5h5"/><path d="M3.05 13A9 9 0 1 0 6 5.3L3 8"/><polyline points="12 7 12 12 15 14"/>',
    _: '<circle cx="12" cy="12" r="3"/><circle cx="12" cy="12" r="9"/>'
  };
  // json keys: allowed sub-keys per entry (registry json_shape; not sent in the schema)
  var SHAPES = {
    'appearance.dashboard_widgets': { show: 'bool', order: 'int' }
  };
  // old tab ids (bookmarks, guides) → the tab those settings moved to
  var TAB_ALIAS = { appearance: 'maintenance', features: 'platforms' };
  var HEX = /^#[0-9a-fA-F]{3,8}$/, URLRE = /^(https?:\/\/|\/static\/)\S+$/, EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  var UPLOAD_ACCEPT = '.png,.jpg,.jpeg,.svg,.webp,.ico';

  var ST = { root: null, cats: [], spec: {}, where: {}, values: {}, original: {}, meta: {}, version: 0,
    errors: {}, tab: 'general', search: '', active: false, leaving: false, bounceTo: null };

  function copy(o) { var r = {}; Object.keys(o || {}).forEach(function (k) { r[k] = o[k]; }); return r; }
  function same(a, b) { return JSON.stringify(a) === JSON.stringify(b); }
  function isDirty(k) { return !same(ST.values[k], ST.original[k]); }
  function dirtyKeys() { return Object.keys(ST.values).filter(isDirty); }
  function fid(k) { return 'gs_' + String(k).replace(/[^a-zA-Z0-9]/g, '_'); }
  function fmtVal(v) {
    if (v === undefined || v === null) return '—';
    if (v === '') return '(empty)';
    if (typeof v === 'object') return JSON.stringify(v);
    return String(v);
  }

  var PAGE_LINK_MSG = 'This is a page address, not an image — upload the file or press Clear';
  function isImageRef(v) { var s = String(v || '').trim(); return !!s && URLRE.test(s) && s.indexOf('#') < 0; }
  function validate(spec, raw) {
    var t = spec.type, s;
    switch (t) {
      case 'bool': return { v: !!raw };
      case 'int': case 'float': {
        s = String(raw == null ? '' : raw).trim();
        var n = Number(s);
        if (s === '' || !isFinite(n)) return { err: t === 'int' ? 'Expected a whole number' : 'Expected a number', v: s };
        if (t === 'int' && !Number.isInteger(n)) return { err: 'Expected a whole number', v: s };
        if (spec.min != null && n < spec.min) return { err: 'Minimum is ' + (t === 'int' ? Math.trunc(spec.min) : spec.min), v: n };
        if (spec.max != null && n > spec.max) return { err: 'Maximum is ' + (t === 'int' ? Math.trunc(spec.max) : spec.max), v: n };
        return { v: n };
      }
      case 'select':
        s = String(raw == null ? '' : raw);
        if (spec.options && spec.options.length && spec.options.indexOf(s) < 0) return { err: 'Must be one of: ' + spec.options.join(', '), v: s };
        return { v: s };
      case 'color':
        s = String(raw == null ? '' : raw).trim();
        return s && !HEX.test(s) ? { err: 'Must be a hex color like #2e9573', v: s } : { v: s };
      case 'url': case 'path':
        s = String(raw == null ? '' : raw).trim();
        if (s && !URLRE.test(s)) return { err: 'Must be an https:// URL or a /static/ path', v: s };
        if (t === 'path' && s.indexOf('#') >= 0) return { err: PAGE_LINK_MSG, v: s };
        return { v: s };
      case 'email':
        s = String(raw == null ? '' : raw).trim();
        return s && !EMAIL.test(s) ? { err: 'Must be a valid email address', v: s } : { v: s };
      case 'json': return validateJson(spec, raw);
      case 'textarea':
        s = String(raw == null ? '' : raw);
        return s.length > 4000 ? { err: 'Too long (max 4000 characters)', v: s } : { v: s };
      default:
        s = String(raw == null ? '' : raw);
        return s.length > 500 ? { err: 'Too long (max 500 characters)', v: s } : { v: s };
    }
  }
  function validateJson(spec, raw) {
    var v = raw;
    if (typeof raw === 'string') {
      try { v = JSON.parse(raw); } catch (e) { return { err: 'Invalid JSON — ' + (raw.trim() ? e.message : 'use {} for none'), v: raw }; }
    }
    if (!v || typeof v !== 'object' || Array.isArray(v)) return { err: 'Expected a JSON object', v: raw };
    var shape = SHAPES[spec.key];
    var keys = Object.keys(v);
    for (var i = 0; i < keys.length; i++) {
      var k = keys[i], e = v[k];
      if (!e || typeof e !== 'object' || Array.isArray(e)) return { err: "Entry '" + k + "' must be an object", v: raw };
      if (!shape) continue;
      var unknown = Object.keys(e).filter(function (x) { return !shape[x]; }).sort();
      if (unknown.length) return { err: "Entry '" + k + "' has unknown keys: " + unknown.join(', '), v: raw };
      var subs = Object.keys(e);
      for (var j = 0; j < subs.length; j++) {
        var sub = subs[j], val = e[sub], want = shape[sub];
        if (want === 'bool' && typeof val !== 'boolean') return { err: "'" + k + '.' + sub + "' must be true or false", v: raw };
        if (want === 'int' && !(typeof val === 'boolean' || (typeof val === 'number' && Number.isInteger(val)))) return { err: "'" + k + '.' + sub + "' must be a number", v: raw };
        if (want === 'str' && typeof val !== 'string') return { err: "'" + k + '.' + sub + "' must be text", v: raw };
      }
    }
    return { v: v };
  }

  function indexSchema(cats) {
    ST.cats = cats || []; ST.spec = {}; ST.where = {};
    ST.cats.forEach(function (c) {
      (c.groups || []).forEach(function (g) {
        (g.specs || []).forEach(function (s) { ST.spec[s.key] = s; ST.where[s.key] = { cat: c, group: g }; });
      });
    });
  }
  function apply(data, keepDirty, except) {
    var carry = {};
    if (keepDirty) dirtyKeys().forEach(function (k) { if (!except || except.indexOf(k) < 0) carry[k] = ST.values[k]; });
    indexSchema(data.categories);
    ST.values = copy(data.settings); ST.original = copy(data.settings);
    ST.meta = data.meta || {}; ST.version = data.version || 0; ST.errors = {};
    Object.keys(carry).forEach(function (k) {
      if (!ST.spec[k]) return;
      ST.values[k] = carry[k];
      var r = validate(ST.spec[k], carry[k]);
      if (r.err) ST.errors[k] = r.err;
    });
  }
  async function reload(keepDirty, except) {
    apply(await K.api(SB), keepDirty, except);
    renderBody(); chrome();
  }

  async function viewSettings(root, q) {
    var data = await K.api(SB);
    var bounce = ST.bounceTo;
    apply(data, !!bounce);
    ST.root = root; ST.active = true; ST.leaving = false; ST.bounceTo = null;
    var tabIds = ST.cats.map(function (c) { return c.id; }).concat(['system']);
    var want = TAB_ALIAS[q.tab] || q.tab;
    ST.tab = tabIds.indexOf(want) >= 0 ? want : (bounce && tabIds.indexOf(ST.tab) >= 0 ? ST.tab : 'general');
    ST.search = q.q || '';
    var ro = !canWrite();
    root.innerHTML = '<div data-tour="head-settings">' + K.header('Global Settings',
      'Every configurable platform setting in one place. Saves apply right away (within a few seconds on every server), are audited, and are versioned so you can roll back. Secrets and server variables live in Environment.',
      (ro ? K.badge('Read-only', 'warning') + ' ' : '') + '<span id="gsVer">' + K.badge('v' + ST.version, 'neutral') + '</span>' +
      '<a class="btn btn-secondary btn-sm" href="#/environment">Environment</a>') + '</div>' +
      (ro ? '<div class="alert alert-info" style="margin-bottom:14px"><div class="alert-body">You can view every setting and its history. Changing settings needs the manager role.</div></div>' : '') +
      '<div class="gs-layout"><nav class="gs-tabs" role="tablist" aria-orientation="vertical" aria-label="Settings sections" data-tour="gs-tabs">' +
      navHtml() + '</nav>' +
      '<div class="gs-main" style="min-width:0"><div class="gs-toolbar"><div class="sa-q" data-tour="gs-search"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>' +
      '<label class="sr-only" for="gsSearch">Search settings</label><input class="form-input" type="search" id="gsSearch" placeholder="Search settings…" autocomplete="off" value="' + esc(ST.search) + '"></div>' +
      '<span class="sa-small sa-muted" id="gsHint" aria-live="polite"></span></div>' +
      '<div id="gsBody" role="tabpanel" tabindex="-1"></div></div></div>' +
      (ro ? '' : '<div class="gs-savebar" id="gsBar" role="region" aria-label="Unsaved changes" data-tour="gs-savebar" hidden><span class="gs-count" id="gsCount"></span>' +
        '<input class="form-input gs-note" id="gsNote" maxlength="300" placeholder="Change note (optional, shown in history)" aria-label="Change note">' +
        '<span class="gs-acts"><button type="button" class="btn btn-secondary btn-sm" id="gsDiscard">Discard</button><button type="button" class="btn btn-primary btn-sm" id="gsSave">Save changes</button></span></div>');
    bindSettings(root);
    renderBody(); chrome();
    if (bounce) askDiscard(bounce);
  }
  // tabs under their section headings (Brand & website, Leads engine, …)
  function navHtml() {
    var out = '', sec = null;
    ST.cats.forEach(function (c) {
      if (c.section && c.section !== sec) { sec = c.section; out += '<div class="gs-sec" role="presentation">' + esc(sec) + '</div>'; }
      out += tabBtn(c.id, c.label);
    });
    return out + '<div class="gs-tab-sep" role="presentation"></div>' + tabBtn('system', 'System & history');
  }
  function customCount(cat) {
    var n = 0;
    (cat.groups || []).forEach(function (g) { (g.specs || []).forEach(function (s) { if ((ST.meta[s.key] || {}).is_default === false) n++; }); });
    return n;
  }
  function tabBtn(id, label) {
    var on = id === ST.tab;
    return '<button type="button" role="tab" class="gs-tab" id="gsTab_' + esc(id) + '" data-gs-tab="' + esc(id) + '" aria-selected="' + on + '" aria-controls="gsBody" tabindex="' + (on ? 0 : -1) + '">' +
      svg(CAT_IC[id] || CAT_IC._) + '<span>' + esc(label) + '</span>' +
      (id === 'system' ? '' : '<span class="gs-n" data-n="' + esc(id) + '" hidden></span>') +
      '<span class="gs-dot" data-dot="' + esc(id) + '" hidden title="Unsaved changes"></span></button>';
  }
  function setTab(id, focus) {
    ST.tab = id; ST.search = '';
    var s = $('#gsSearch', ST.root); if (s) s.value = '';
    $$('[data-gs-tab]', ST.root).forEach(function (b) { var on = b.getAttribute('data-gs-tab') === id; b.setAttribute('aria-selected', on); b.tabIndex = on ? 0 : -1; if (on && focus) b.focus(); });
    try {
      var h = location.hash.split('?'), p = new URLSearchParams(h[1] || '');
      if (id === 'general') p.delete('tab'); else p.set('tab', id);
      p.delete('q'); p.delete('section');
      var qs = p.toString(); history.replaceState(null, '', h[0] + (qs ? '?' + qs : ''));
    } catch (e) { /* ignore */ }
    renderBody(); chrome();
  }

  // ── fields ──
  function stateChip(k) {
    if (isDirty(k)) return K.badge('Unsaved', 'warning');
    var m = ST.meta[k] || {}, spec = ST.spec[k] || {};
    var dflt = 'Built-in default: ' + fmtVal(spec.default);
    if (m.is_default !== false) return '<span title="' + esc(dflt) + '">' + K.badge('Default', 'neutral') + '</span>';
    var u = utc(m.updated_at);
    return '<span title="' + esc('Changed from the default' + (u ? ' · ' + K.fmtDT(u) : '') + ' · ' + dflt) + '">' + K.badge('Custom', 'primary') + '</span>';
  }
  function fieldHtml(spec) {
    var k = spec.key, v = ST.values[k], id = fid(k), ro = !canWrite(), dis = ro ? ' disabled' : '';
    var data = ' data-k="' + esc(k) + '"';
    var head = '<div class="gs-lbl"><label for="' + id + '">' + esc(spec.label || k) + '</label><span data-chip="' + esc(k) + '">' + stateChip(k) + '</span><span class="gs-key">' + esc(k) + '</span></div>';
    var desc = spec.description ? '<p class="gs-desc" id="' + id + '_d">' + esc(spec.description) + '</p>' : '';
    var aria = ' aria-describedby="' + id + '_e' + (spec.description ? ' ' + id + '_d' : '') + '"';
    var errEl = '<div class="gs-ferr" id="' + id + '_e" data-ferr="' + esc(k) + '" role="alert">' + esc(ST.errors[k] && spec.type !== 'json' ? ST.errors[k] : '') + '</div>';
    var ph = spec.placeholder ? ' placeholder="' + esc(spec.placeholder) + '"' : '';
    var ctl;
    switch (spec.type) {
      case 'bool':
        return '<div class="gs-field" data-field="' + esc(k) + '"><div class="gs-bool"><label class="sa-toggle"><input type="checkbox" id="' + id + '"' + data + (v ? ' checked' : '') + dis + aria + '><span></span></label>' +
          '<div>' + head + desc + errEl + '</div></div></div>';
      case 'select': {
        var opts = (spec.options || []).slice();
        if (v != null && v !== '' && opts.indexOf(String(v)) < 0) opts.unshift(String(v));
        ctl = '<div class="gs-ctl"><select class="form-select" id="' + id + '"' + data + dis + aria + '>' + opts.map(function (o) {
          return '<option value="' + esc(o) + '"' + (String(v) === String(o) ? ' selected' : '') + '>' + esc(o) + '</option>'; }).join('') + '</select></div>';
        break;
      }
      case 'color': {
        var hex = String(v == null ? '' : v), pick = HEX.test(hex) && hex.length === 7 ? hex : (HEX.test(hex) && hex.length === 4 ? '#' + hex[1] + hex[1] + hex[2] + hex[2] + hex[3] + hex[3] : '#2e9573');
        ctl = '<div class="gs-ctl"><input type="color" class="gs-pick" data-pick="' + esc(k) + '" value="' + esc(pick.toLowerCase()) + '" aria-label="' + esc((spec.label || k) + ' colour picker') + '"' + dis + '>' +
          '<input class="form-input gs-hex" id="' + id + '"' + data + ' value="' + esc(hex) + '" placeholder="' + esc(spec.placeholder || '#2e9573') + '" maxlength="9" autocomplete="off" spellcheck="false"' + dis + aria + '></div>';
        break;
      }
      case 'json':
        ctl = '<textarea class="form-textarea gs-json" id="' + id + '"' + data + ' rows="5" spellcheck="false"' + dis + aria + '>' + esc(typeof v === 'object' && v !== null ? JSON.stringify(v, null, 2) : (v == null ? '' : v)) + '</textarea>';
        errEl = '<div class="gs-ferr" id="' + id + '_e" data-ferr="' + esc(k) + '" role="alert">' + esc(ST.errors[k] || '') + '</div>';
        break;
      case 'textarea':
        ctl = '<textarea class="form-textarea" id="' + id + '"' + data + ' rows="3" maxlength="4000" style="max-width:760px;min-height:76px"' + ph + dis + aria + '>' + esc(v == null ? '' : v) + '</textarea>';
        break;
      default: {
        var t = spec.type, num = t === 'int' || t === 'float', file = t === 'path';
        var attrs = num ? ' type="number" inputmode="' + (t === 'int' ? 'numeric' : 'decimal') + '" step="' + (t === 'int' ? '1' : 'any') + '"' + (spec.min != null ? ' min="' + esc(spec.min) + '"' : '') + (spec.max != null ? ' max="' + esc(spec.max) + '"' : '')
          : t === 'email' ? ' type="email"' : t === 'url' ? ' type="url"' : ' type="text" maxlength="500"';
        var thumb = t === 'path' && isImageRef(v) ? '<img class="gs-thumb" data-thumb="' + esc(k) + '" src="' + esc(v) + '" alt="' + esc((spec.label || k) + ' preview') + '">' : '';
        var pageLink = t === 'path' && v && !isImageRef(v) && !ST.errors[k];
        ctl = '<div class="gs-ctl">' + thumb + '<input class="form-input" id="' + id + '"' + data + attrs + ' value="' + esc(v == null ? '' : v) + '"' + ph + ' autocomplete="off"' + dis + aria + '>' +
          (file && !ro ? '<button type="button" class="btn btn-secondary btn-sm" data-upload="' + esc(k) + '">Upload</button><input type="file" hidden data-upload-file="' + esc(k) + '" accept="' + UPLOAD_ACCEPT + '">' : '') +
          (t === 'path' && !ro && v ? '<button type="button" class="btn btn-ghost btn-sm" data-clear="' + esc(k) + '" title="Empty this field (the built-in default is used), then save">Clear</button>' : '') + '</div>' +
          (pageLink ? '<div class="gs-ferr" data-pagelink="' + esc(k) + '" role="note">' + esc(PAGE_LINK_MSG) + '. It is ignored until then.</div>' : '') +
          (num && (spec.min != null || spec.max != null) ? '<span class="sa-small sa-muted">' + (spec.min != null && spec.max != null ? 'Between ' + esc(spec.min) + ' and ' + esc(spec.max) : spec.min != null ? 'At least ' + esc(spec.min) : 'At most ' + esc(spec.max)) + (t === 'int' ? ', whole numbers' : '') + '</span>' : '') +
          (t === 'path' && !ro ? '<span class="sa-small sa-muted">Upload: PNG, JPG, SVG, WEBP or ICO, max 2 MB. Save afterwards.</span>' : '');
      }
    }
    return '<div class="gs-field' + (isWide(spec) ? ' gs-wide' : '') + (ST.errors[k] ? ' bad' : '') + '" data-field="' + esc(k) + '">' + head + desc + ctl + errEl + '</div>';
  }
  function isWide(spec) { return spec.type === 'textarea' || spec.type === 'json' || spec.type === 'path'; }
  function groupCard(cat, g, opts) {
    opts = opts || {};
    var specs = opts.specs || g.specs || [];
    var custom = specs.filter(function (s) { return (ST.meta[s.key] || {}).is_default === false; }).length;
    return '<section class="sa-card" data-group="' + esc(g.id) + '"' + (opts.tour ? ' data-tour="gs-group"' : '') + '><div class="gs-card-h"><h3>' + esc(opts.title || g.label) + '</h3>' +
      '<span class="sa-small sa-muted">' + plural(specs.length, 'setting') + (custom ? ' · ' + custom + ' custom' : '') + '</span>' +
      (canWrite() && !opts.search ? '<span class="gs-r"><button type="button" class="btn btn-ghost btn-sm" data-reset-group="' + esc(g.id) + '">Reset group</button></span>' : '') + '</div>' +
      (g.description && !opts.search ? '<p class="gs-gdesc">' + esc(g.description) + '</p>' : '') +
      '<div class="gs-fields">' + specs.map(fieldHtml).join('') + '</div></section>';
  }

  // ── brand preview ──
  function okColor(k, d) { var v = ST.values[k]; return !ST.errors[k] && v && HEX.test(v) ? v : d; }
  function previewHtml() {
    var logoV = ST.values['branding.logo_primary'], logo = !ST.errors['branding.logo_primary'] && logoV && URLRE.test(String(logoV)) ? logoV : '';
    var primary = okColor('branding.colors.primary', '#2e9573'), accent = okColor('branding.colors.accent', '#d9a441');
    var sw = [['success', 'Success', '#1fae6a'], ['warning', 'Warning', '#f0a531'], ['danger', 'Danger', '#e5484d'], ['info', 'Info', '#3b82f6']];
    return '<div class="gs-prev"><div class="gs-prev-head">' +
      (logo ? '<img class="gs-prev-logo" data-prev-logo src="' + esc(logo) + '" alt="Logo preview">' : '') +
      '<div class="gs-prev-mark" data-prev-mark style="background:' + esc(primary) + (logo ? ';display:none' : '') + '" aria-hidden="true">✦</div>' +
      '<div style="min-width:0"><div class="gs-prev-name" style="color:' + esc(primary) + '">' + esc(ST.values['general.app.name'] || 'LeadAI') + '</div>' +
      '<div class="gs-prev-tag">' + esc(ST.values['general.app.tagline'] || 'AI Lead Intelligence') + '</div></div></div>' +
      '<div class="gs-prev-acts"><span class="gs-prev-btn" style="background:' + esc(primary) + '">Primary</span><span class="gs-prev-btn" style="background:' + esc(accent) + '">Accent</span></div></div>' +
      '<div class="gs-sw">' + sw.map(function (s) { return '<span><i style="background:' + esc(okColor('branding.colors.' + s[0], s[2])) + '"></i>' + s[1] + '</span>'; }).join('') + '</div>';
  }
  function bindPreview(box) {
    var img = $('[data-prev-logo]', box);
    if (img) img.addEventListener('error', function () { img.style.display = 'none'; var m = $('[data-prev-mark]', box); if (m) m.style.display = ''; });
  }
  function refreshPreview() {
    var box = $('#gsPreview', ST.root);
    if (box) { box.innerHTML = previewHtml(); bindPreview(box); }
  }

  // ── body ──
  function renderBody() {
    var box = $('#gsBody', ST.root); if (!box) return;
    var hint = $('#gsHint', ST.root);
    var q = ST.search.trim().toLowerCase();
    box.setAttribute('aria-labelledby', q ? 'gsSearch' : 'gsTab_' + ST.tab);
    if (q) {
      var groups = [], count = 0;
      ST.cats.forEach(function (c) {
        (c.groups || []).forEach(function (g) {
          var specs = (g.specs || []).filter(function (s) {
            return (s.key + ' ' + (s.label || '') + ' ' + (s.description || '') + ' ' + g.label + ' ' + c.label).toLowerCase().indexOf(q) >= 0;
          });
          if (specs.length) { groups.push([c, g, specs]); count += specs.length; }
        });
      });
      hint.textContent = plural(count, 'matching setting');
      box.innerHTML = count ? '<div class="gs-stack">' + groups.map(function (x) { return groupCard(x[0], x[1], { specs: x[2], title: x[0].label + ' › ' + x[1].label, search: true }); }).join('') + '</div>'
        : '<div class="sa-card">' + K.emptyState('No settings match “' + ST.search + '”', 'Try a different word: a key such as branding.colors, a label, or a section name.') + '</div>';
      return;
    }
    hint.textContent = '';
    if (ST.tab === 'system') { box.innerHTML = systemHtml(); loadHistory(); return; }
    var cat = ST.cats.filter(function (c) { return c.id === ST.tab; })[0];
    if (!cat) { box.innerHTML = K.emptyState('Unknown section'); return; }
    box.innerHTML = '<div class="gs-stack">' + introHtml(cat) + setupSlot(cat) +
      (cat.id === 'branding' ? '<section class="sa-card" data-tour="gs-preview"><div class="gs-card-h"><h3>Live preview</h3><span class="sa-small sa-muted">Updates as you type. Save to apply it everywhere.</span></div><div id="gsPreview"></div></section>' : '') +
      (cat.groups || []).map(function (g, i) { return groupCard(cat, g, { tour: i === 0 }); }).join('') + '</div>';
    if (cat.id === 'branding') refreshPreview();
    loadSetup(cat.id);
    $$('[data-thumb]', box).forEach(function (im) { im.addEventListener('error', function () { im.hidden = true; }); });
  }
  // ── setup panels: mail server (Email & alerts) and payment providers (Payments) ──
  // Credentials stay in the server's .env (locked there on purpose); these panels
  // show what is set, what is missing, the exact lines to add, and live tests.
  var MAIL_PRESETS = {
    hostinger: { label: 'Hostinger email', host: 'smtp.hostinger.com', port: 465, security: 'ssl', user: 'you@yourdomain.com', pass: 'your-mailbox-password' },
    gmail: { label: 'Gmail / Google Workspace', host: 'smtp.gmail.com', port: 587, security: 'starttls', user: 'you@gmail.com', pass: 'your-16-letter-app-password' },
    zoho: { label: 'Zoho Mail (India)', host: 'smtp.zoho.in', port: 465, security: 'ssl', user: 'you@yourdomain.com', pass: 'your-mailbox-password' },
    brevo: { label: 'Brevo / SendGrid / other', host: 'smtp-relay.brevo.com', port: 587, security: 'starttls', user: 'your-smtp-login', pass: 'your-smtp-key' }
  };
  var RESTART_STEPS = '<ol class="gs-steps"><li>Open <code>~/leadai/.env</code> on the server (CloudPanel → File Manager, or SSH).</li>' +
    '<li>Paste the lines below and fill in the real values. Keep every secret only in this file.</li>' +
    '<li>Restart the app: <code>cd ~/leadai &amp;&amp; bash scripts/deploy_vps.sh</code> (pushing any commit also restarts it).</li>' +
    '<li>Come back here and run the test.</li></ol>';
  function kv(rows) {
    return '<dl class="gs-kv">' + rows.map(function (r) { return '<dt>' + esc(r[0]) + '</dt><dd>' + r[1] + '</dd>'; }).join('') + '</dl>';
  }
  function okBadge(ok, yes, no) { return ok ? K.badge(yes || 'Set', 'success') : K.badge(no || 'Not set', 'warning'); }
  function snippet(id, text) {
    return '<div class="gs-snip"><pre id="' + esc(id) + '">' + esc(text) + '</pre><button type="button" class="btn btn-secondary btn-sm" data-copy="' + esc(id) + '">Copy</button></div>';
  }
  function setupSlot(cat) {
    if (!isSuper()) return '';
    if (cat.id === 'notifications') return '<section class="sa-card" id="gsMail" aria-busy="true" data-tour="gs-mail"><div class="gs-card-h"><h3>Mail server</h3></div><p class="sa-small sa-muted">Checking the mail server…</p></section>';
    if (cat.id === 'payments') return '<section class="sa-card" id="gsPay" aria-busy="true" data-tour="gs-pay"><div class="gs-card-h"><h3>Payment providers</h3></div><p class="sa-small sa-muted">Checking the payment providers…</p></section>';
    return '';
  }
  function loadSetup(catId) {
    if (!isSuper()) return;
    if (catId === 'notifications') loadMail();
    if (catId === 'payments') loadPay();
  }

  // mail
  function mailSnippet(preset, em) {
    var p = MAIL_PRESETS[preset] || MAIL_PRESETS.hostinger;
    var brand = String(ST.values['general.app.name'] || 'LeadAI').replace(/[<>"\r\n]/g, '');
    var user = em && em.configured && em.user ? em.user : p.user;
    return ['SMTP_HOST=' + p.host, 'SMTP_PORT=' + p.port, 'SMTP_SECURITY=' + p.security, 'SMTP_USER=' + user,
      'SMTP_PASSWORD=' + p.pass, 'SMTP_FROM=' + brand + ' <' + (user.indexOf('@') > 0 ? user : 'no-reply@yourdomain.com') + '>',
      'PUBLIC_BASE_URL=' + location.origin].join('\n');
  }
  async function loadMail() {
    var box = $('#gsMail', ST.root); if (!box) return;
    var em;
    try { em = (await K.api('/api/super-admin/messaging')).email || {}; }
    catch (err) { box.innerHTML = '<div class="gs-card-h"><h3>Mail server</h3></div>' + K.errorState(err.message); box.removeAttribute('aria-busy'); return; }
    if (!$('#gsMail', ST.root)) return;
    ST.mail = em;
    var ob = em.outbox || {}, missing = em.missing || [];
    var preset = ST.mailPreset || (/gmail|google/.test(em.host || '') ? 'gmail' : /zoho/.test(em.host || '') ? 'zoho' : /brevo|sendgrid|mailgun|ses/.test(em.host || '') ? 'brevo' : 'hostinger');
    ST.mailPreset = preset;
    var me = (K.me && K.me()) || {};
    var brand = String(ST.values['general.app.name'] || 'LeadAI').replace(/[<>"\r\n]/g, '');
    var defFrom = em.from || (brand + ' <no-reply@' + (location.hostname || 'yourdomain.com') + '>');
    var defUrl = em.public_base_url || location.origin;

    box.innerHTML = '<div class="gs-card-h"><h3>Mail server (SMTP Gateway)</h3>' + (em.configured ? K.badge('Sending email', 'success') : K.badge('Not configured: emails wait in outbox', 'warning')) +
      '<span class="gs-r"><button type="button" class="btn btn-ghost btn-sm" data-setup-refresh="mail">Re-check</button></span></div>' +
      '<p class="gs-gdesc">Every email (sign-ups, password resets, invitations, and alerts) goes out through this SMTP gateway. You can type and save your credentials directly below, or use the quick provider presets.</p>' +
      kv([
        ['Status', em.configured ? '<span style="color:var(--success-text);font-weight:600">Active and sending</span>' : '<span class="sa-muted">Waiting for SMTP server configuration</span>'],
        ['Server', em.configured ? esc(em.host + ':' + em.port) + ' · ' + esc(em.security === 'ssl' ? 'SSL' : em.security === 'starttls' ? 'STARTTLS' : em.security) : okBadge(false, '', 'SMTP_HOST not set')],
        ['Login', em.auth ? esc(em.user) + ' · ' + okBadge(em.password_set, 'Password saved ✓', 'Password missing') : '<span class="sa-muted">None / local relay</span>'],
        ['Sender From', em.from_set ? esc(em.from) : okBadge(false, '', 'Defaulting to no-reply')],
        ['Links Base URL', em.public_base_url ? esc(em.public_base_url) : okBadge(false, '', 'PUBLIC_BASE_URL missing')],
        ['Outbox', esc(K.fmtN(ob.queued || 0)) + ' waiting · ' + esc(K.fmtN(ob.sent || 0)) + ' sent · ' + esc(K.fmtN(ob.failed || 0)) + ' failed' +
          (ob.last_sent_at ? ' <span class="sa-muted">· last sent ' + when(ob.last_sent_at) + '</span>' : '')]
      ]) +
      (ob.last_error ? '<div class="alert alert-danger gs-alert"><div class="alert-body"><b>Last failure:</b> ' + esc(ob.last_error) + '</div></div>' : '') +
      '<div class="sa-card sa-section" style="background:var(--surface);margin-top:14px;padding:14px;border-radius:12px;border:1px solid var(--border)">' +
      '<div class="gs-card-h"><h4>Configure SMTP Credentials</h4></div>' +
      '<p class="sa-small sa-muted" style="margin:4px 0 10px">Choose a quick preset or type your custom SMTP server credentials. Passwords are saved securely and encrypted at rest.</p>' +
      '<div class="sa-field" style="margin-bottom:12px"><label for="gsMailPresetSelect">Provider preset</label>' +
      '<select class="form-select" id="gsMailPresetSelect" data-mail-preset>' +
      Object.keys(MAIL_PRESETS).map(function (k) { return '<option value="' + k + '"' + (k === preset ? ' selected' : '') + '>' + esc(MAIL_PRESETS[k].label) + '</option>'; }).join('') +
      '<option value="custom">Custom SMTP</option></select></div>' +
      '<div class="gs-field-grid">' +
      '<div class="sa-field"><label for="gsMailHost">SMTP Host</label><input id="gsMailHost" class="form-input sa-mono" placeholder="smtp.gmail.com" value="' + esc(em.host || (MAIL_PRESETS[preset] ? MAIL_PRESETS[preset].host : '')) + '"><span class="hint">e.g. smtp.gmail.com, smtp.hostinger.com, smtp.zoho.in</span></div>' +
      '<div class="sa-field"><label for="gsMailPort">SMTP Port</label><input id="gsMailPort" type="number" class="form-input sa-mono" min="1" max="65535" placeholder="587" value="' + esc(em.port || (MAIL_PRESETS[preset] ? MAIL_PRESETS[preset].port : 587)) + '"><span class="hint">Usually 587 (STARTTLS) or 465 (SSL)</span></div>' +
      '<div class="sa-field"><label for="gsMailSec">Encryption Mode</label><select id="gsMailSec" class="form-select">' +
      ['auto', 'ssl', 'starttls', 'none'].map(function (s) {
        var label = { auto: 'Auto (Port 465 = SSL, else STARTTLS)', ssl: 'SSL / TLS (Port 465)', starttls: 'STARTTLS (Port 587)', none: 'None (local relay only)' }[s];
        var curSec = em.security || (MAIL_PRESETS[preset] ? MAIL_PRESETS[preset].security : 'auto');
        return '<option value="' + s + '"' + (s === curSec ? ' selected' : '') + '>' + esc(label) + '</option>';
      }).join('') + '</select><span class="hint">Encryption protocol required by your host</span></div>' +
      '<div class="sa-field"><label for="gsMailUser">SMTP Username / Email</label><input id="gsMailUser" class="form-input" placeholder="you@yourdomain.com" value="' + esc(em.user || '') + '"><span class="hint">Full email address or mailbox login</span></div>' +
      '<div class="sa-field"><label for="gsMailPass">SMTP Password' + (em.password_set ? ' <span class="sa-msg-secret">saved ✓</span>' : '') + '</label>' +
      '<div class="sa-keyin"><input type="password" id="gsMailPass" class="form-input sa-mono" placeholder="' + (em.password_set ? 'Saved ✓ (leave empty to keep)' : 'Mailbox password or App Password') + '">' +
      '<button type="button" class="btn btn-secondary btn-sm" data-reveal="gsMailPass">Show</button></div>' +
      '<span class="hint">' + (preset === 'gmail' ? 'For Gmail/Google Workspace, generate a 16-letter App Password in your Google Account Security settings.' : 'Stored encrypted at rest.') + '</span></div>' +
      '<div class="sa-field"><label for="gsMailFrom">Sender "From" Address</label><input id="gsMailFrom" class="form-input" placeholder="Brand <you@yourdomain.com>" value="' + esc(defFrom) + '"><span class="hint">Display name & sender mailbox</span></div>' +
      '<div class="sa-field" style="grid-column:1 / -1"><label for="gsMailBaseUrl">Public Base URL</label><input id="gsMailBaseUrl" class="form-input sa-mono" placeholder="https://app.yourdomain.com" value="' + esc(defUrl) + '"><span class="hint">Base URL used for clickable links in emails (password resets, invitation links)</span></div>' +
      '</div>' +
      '<div class="sa-row" style="margin-top:12px"><button type="button" class="btn btn-primary btn-sm" data-save-mail>Save Email Gateway Settings</button></div>' +
      '</div>' +
      '<form class="gs-test" data-mail-test novalidate style="margin-top:16px"><label for="gsMailTo"><b>Test Delivery:</b> Send a test email to</label>' +
      '<input class="form-input" type="email" id="gsMailTo" value="' + esc(me.email || '') + '" placeholder="you@example.com" autocomplete="email">' +
      '<button type="submit" class="btn btn-secondary btn-sm">Send test email</button><span class="gs-test-out' + (ST.mailOut ? ' ' + ST.mailOut[0] : '') + '" id="gsMailOut" role="status" aria-live="polite">' + esc(ST.mailOut ? ST.mailOut[1] : '') + '</span></form>';
    box.removeAttribute('aria-busy');
  }

  async function saveMail(btn) {
    var host = ($('#gsMailHost', ST.root).value || '').trim();
    var port = parseInt($('#gsMailPort', ST.root).value || '587', 10);
    var sec = ($('#gsMailSec', ST.root).value || 'auto').trim();
    var user = ($('#gsMailUser', ST.root).value || '').trim();
    var pass = ($('#gsMailPass', ST.root).value || '').trim();
    var fromAddr = ($('#gsMailFrom', ST.root).value || '').trim();
    var baseUrl = ($('#gsMailBaseUrl', ST.root).value || '').trim();

    if (!host) {
      K.toast('Please enter the SMTP Host', 'error');
      var elH = $('#gsMailHost', ST.root); if (elH) elH.focus();
      return;
    }

    var body = {
      smtp_host: host,
      smtp_port: port,
      smtp_security: sec,
      smtp_user: user,
      smtp_from: fromAddr,
      public_base_url: baseUrl
    };
    if (pass) body.smtp_password = pass;

    await K.busy(btn, async function () {
      try {
        await K.api('/api/super-admin/messaging/email', { method: 'PUT', body: body });
        K.toast('Mail server settings saved successfully!', 'success');
        loadMail();
      } catch (err) {
        K.toast(err.message || 'Failed to save email settings', 'error');
      }
    });
  }

  async function mailTest(form) {
    var to = ($('#gsMailTo', form).value || '').trim(), out = $('#gsMailOut', form), btn = $('button[type=submit]', form);
    if (!EMAIL.test(to)) { out.className = 'gs-test-out bad'; out.textContent = 'Enter an email address'; return; }
    await K.busy(btn, async function () {
      try {
        var r = await K.api('/api/super-admin/messaging/test', { method: 'POST', body: { channel: 'email', to: to } });
        var ok = r.status === 'sent';
        ST.mailOut = [ok ? 'good' : 'bad', ok ? 'Sent. Check the inbox (and spam folder) of ' + to + '.' : r.status === 'queued' ? 'Not sent: no mail server is set up yet, so it waits in the outbox.' : (r.error || r.message || 'Failed')];
      } catch (err) { ST.mailOut = ['bad', err.message]; }
      out.className = 'gs-test-out ' + ST.mailOut[0]; out.textContent = ST.mailOut[1];
    });
    loadMail();  // outbox counts and the last error follow the test
  }

  // payments
  var PAY_INFO = {
    razorpay: { label: 'Razorpay', where: 'Razorpay Dashboard → Account & Settings → API keys (key id + secret); Developers → Webhooks (URL, events, secret).',
      lines: ['RAZORPAY_KEY_ID=rzp_live_xxxxxxxxxxxx', 'RAZORPAY_KEY_SECRET=your-key-secret', 'RAZORPAY_WEBHOOK_SECRET=the-secret-you-type-in-the-webhook'] },
    stripe: { label: 'Stripe', where: 'Stripe Dashboard → Developers → API keys (secret key); Developers → Webhooks → Add endpoint (URL, events) → Signing secret.',
      lines: ['STRIPE_SECRET_KEY=sk_live_xxxxxxxxxxxx', 'STRIPE_WEBHOOK_SECRET=whsec_xxxxxxxxxxxx'] }
  };
  async function loadPay() {
    var box = $('#gsPay', ST.root); if (!box) return;
    var st;
    try { st = await K.api('/api/super-admin/payments/setup'); st = st.payments || st; }
    catch (err) { box.innerHTML = '<div class="gs-card-h"><h3>Payment providers</h3></div>' + K.errorState(err.message); box.removeAttribute('aria-busy'); return; }
    if (!$('#gsPay', ST.root)) return;
    var provs = st.providers || {}, active = st.active || 'mock';
    var activeName = active === 'mock' ? 'Test mode (mock page, no real money)' : (PAY_INFO[active] || {}).label || active;
    var choice = st.choice || 'auto';

    box.innerHTML = '<div class="gs-card-h"><h3>Payment Gateways</h3>' + K.badge('Taking payments with: ' + activeName, active === 'mock' ? 'warning' : 'success') +
      '<span class="gs-r"><button type="button" class="btn btn-ghost btn-sm" data-setup-refresh="pay">Re-check</button></span></div>' +
      '<div class="sa-row" style="margin:10px 0 14px;background:var(--surface);padding:10px 14px;border-radius:10px;border:1px solid var(--border);align-items:center">' +
      '<label for="gsPayChoice" style="font-weight:600;margin-right:8px">Primary Provider:</label>' +
      '<select id="gsPayChoice" class="form-select" style="max-width:260px">' +
      '<option value="auto"' + (choice === 'auto' ? ' selected' : '') + '>Auto (Razorpay if set, else Stripe)</option>' +
      '<option value="razorpay"' + (choice === 'razorpay' ? ' selected' : '') + '>Razorpay (UPI, Cards, INR)</option>' +
      '<option value="stripe"' + (choice === 'stripe' ? ' selected' : '') + '>Stripe (Global Cards, USD/EUR)</option>' +
      '</select><span class="sa-small sa-muted" style="margin-left:auto">' + (active === 'mock' ? 'Currently using mock payments until keys are entered' : 'Active: ' + activeName) + '</span></div>' +
      (active === 'mock' ? '<div class="alert alert-warning gs-alert"><div class="alert-body">No live payment credentials configured yet. Enter your Razorpay or Stripe API keys below to start accepting customer payments.</div></div>' : '') +
      (st.fallback_reason ? '<div class="alert alert-warning gs-alert"><div class="alert-body">' + esc(st.fallback_reason) + '</div></div>' : '') +
      '<p class="gs-gdesc">Every verified payment still waits for your confirmation in Subscriptions before a plan starts. Keys are stored encrypted at rest.</p>' +
      '<div class="gs-provs">' + ['razorpay', 'stripe'].map(function (name) { return provHtml(name, provs[name] || {}, active, st); }).join('') + '</div>';
    box.removeAttribute('aria-busy');
  }

  function provHtml(name, p, active, st) {
    var info = PAY_INFO[name], events = ((st.webhook_events || {})[name]) || [];
    var url = p.webhook_url || '';
    if (url && url.charAt(0) === '/') url = location.origin + url;

    var isRzp = name === 'razorpay';
    return '<section class="gs-prov" aria-label="' + esc(info.label) + '"><div class="gs-card-h"><h4>' + esc(info.label) + '</h4>' +
      (name === active ? K.badge('Active', 'primary') : '') +
      (p.configured ? K.badge('Configured', 'success') : K.badge('Keys Needed', 'warning')) +
      (p.mode ? K.badge(p.mode === 'live' ? 'Live mode' : 'Test mode', p.mode === 'live' ? 'success' : 'neutral') : '') + '</div>' +
      '<p class="sa-small sa-muted" style="margin:4px 0 10px">' + esc(info.where) + '</p>' +
      '<div class="sa-field-grid single" style="margin:10px 0">' +
      (isRzp ?
        '<div class="sa-field"><label for="gsRzpKeyId">Key ID' + (p.key_id_configured ? ' <span class="sa-msg-secret">saved (' + esc(p.key_id_hint || '✓') + ')</span>' : '') + '</label>' +
        '<input id="gsRzpKeyId" class="form-input sa-mono" placeholder="' + (p.key_id_configured ? 'Saved (' + esc(p.key_id_hint || '✓') + ') — type to replace' : 'rzp_live_... or rzp_test_...') + '" value="">' +
        '<span class="hint">Razorpay Key ID (public)' + (p.key_id_hint ? ' · Currently: <code>' + esc(p.key_id_hint) + '</code>' : '') + '</span></div>' +
        '<div class="sa-field"><label for="gsRzpKeySec">Key Secret' + (p.key_secret_configured ? ' <span class="sa-msg-secret">saved ✓</span>' : '') + '</label>' +
        '<div class="sa-keyin"><input type="password" id="gsRzpKeySec" class="form-input sa-mono" placeholder="' + (p.key_secret_configured ? 'Saved ✓ (leave empty to keep)' : 'Your Razorpay Key Secret') + '">' +
        '<button type="button" class="btn btn-secondary btn-sm" data-reveal="gsRzpKeySec">Show</button></div>' +
        '<span class="hint">Stored encrypted at rest. Never shown again.</span></div>' +
        '<div class="sa-field"><label for="gsRzpWebhook">Webhook Secret' + (p.webhook_secret_configured ? ' <span class="sa-msg-secret">saved ✓</span>' : '') + '</label>' +
        '<div class="sa-keyin"><input type="password" id="gsRzpWebhook" class="form-input sa-mono" placeholder="' + (p.webhook_secret_configured ? 'Saved ✓ (leave empty to keep)' : 'Secret you type in Razorpay Webhooks') + '">' +
        '<button type="button" class="btn btn-secondary btn-sm" data-reveal="gsRzpWebhook">Show</button></div>' +
        '<span class="hint">Secret configured in Razorpay Dashboard → Webhooks.</span></div>'
        :
        '<div class="sa-field"><label for="gsStripeSec">Secret Key' + (p.key_secret_configured ? ' <span class="sa-msg-secret">saved ✓</span>' : '') + '</label>' +
        '<div class="sa-keyin"><input type="password" id="gsStripeSec" class="form-input sa-mono" placeholder="' + (p.key_secret_configured ? 'Saved ✓ (leave empty to keep)' : 'sk_live_... or sk_test_...') + '">' +
        '<button type="button" class="btn btn-secondary btn-sm" data-reveal="gsStripeSec">Show</button></div>' +
        '<span class="hint">Stripe Secret Key (sk_live_... / sk_test_...). Stored encrypted.</span></div>' +
        '<div class="sa-field"><label for="gsStripePub">Publishable Key' + (p.publishable_key_configured ? ' <span class="sa-msg-secret">saved (' + esc(p.publishable_key_hint || '✓') + ')</span>' : '') + '</label>' +
        '<input id="gsStripePub" class="form-input sa-mono" placeholder="' + (p.publishable_key_configured ? 'Saved (' + esc(p.publishable_key_hint || '✓') + ') — type to replace' : 'pk_live_... or pk_test_...') + '" value="">' +
        '<span class="hint">Stripe Publishable Key for checkout' + (p.publishable_key_hint ? ' · Currently: <code>' + esc(p.publishable_key_hint) + '</code>' : '') + '</span></div>' +
        '<div class="sa-field"><label for="gsStripeWebhook">Webhook Signing Secret' + (p.webhook_secret_configured ? ' <span class="sa-msg-secret">saved ✓</span>' : '') + '</label>' +
        '<div class="sa-keyin"><input type="password" id="gsStripeWebhook" class="form-input sa-mono" placeholder="' + (p.webhook_secret_configured ? 'Saved ✓ (leave empty to keep)' : 'whsec_...') + '">' +
        '<button type="button" class="btn btn-secondary btn-sm" data-reveal="gsStripeWebhook">Show</button></div>' +
        '<span class="hint">Signing secret from Stripe Dashboard → Developers → Webhooks.</span></div>'
      ) +
      '</div>' +
      '<div class="sa-row" style="margin-bottom:12px">' +
      '<button type="button" class="btn btn-primary btn-sm" data-save-pay="' + name + '">Save ' + esc(info.label) + ' Keys</button>' +
      '<button type="button" class="btn btn-secondary btn-sm" data-pay-check="' + name + '"' + (p.configured ? '' : ' disabled') + '>Test Connection</button>' +
      '<span class="gs-test-out" id="gsPayOut_' + name + '" role="status" aria-live="polite"></span>' +
      '</div>' +
      '<div style="background:var(--surface);padding:10px 12px;border-radius:10px;border:1px solid var(--border-subtle);font-size:12px">' +
      '<div style="font-weight:600;margin-bottom:4px">Webhook Setup in ' + esc(info.label) + ' Dashboard:</div>' +
      '<div style="margin-bottom:6px"><b>Endpoint URL:</b> ' + (url ? '<code class="gs-url" id="gsHook_' + name + '">' + esc(url) + '</code> <button type="button" class="btn btn-ghost btn-sm" data-copy="gsHook_' + name + '">Copy</button>' : '—') + '</div>' +
      '<div><b>Events to listen for:</b> ' + (events.length ? events.map(function (e) { return '<code style="margin:2px 3px 2px 0;display:inline-block">' + esc(e) + '</code>'; }).join(' ') : '—') + '</div>' +
      '</div>' +
      '</section>';
  }

  async function savePay(btn, provName) {
    var b = {};
    if (provName === 'razorpay') {
      var rKey = ($('#gsRzpKeyId', ST.root).value || '').trim();
      var rSec = ($('#gsRzpKeySec', ST.root).value || '').trim();
      var rHook = ($('#gsRzpWebhook', ST.root).value || '').trim();
      if (rKey) b.razorpay_key_id = rKey;
      if (rSec) b.razorpay_key_secret = rSec;
      if (rHook) b.razorpay_webhook_secret = rHook;
    } else if (provName === 'stripe') {
      var sSec = ($('#gsStripeSec', ST.root).value || '').trim();
      var sPub = ($('#gsStripePub', ST.root).value || '').trim();
      var sHook = ($('#gsStripeWebhook', ST.root).value || '').trim();
      if (sPub) b.stripe_publishable_key = sPub;
      if (sSec) b.stripe_secret_key = sSec;
      if (sHook) b.stripe_webhook_secret = sHook;
    }
    var sel = $('#gsPayChoice', ST.root);
    if (sel && sel.value) b.billing_provider = sel.value;

    await K.busy(btn, async function () {
      try {
        await K.api('/api/super-admin/payments/setup', { method: 'PUT', body: b });
        K.toast((provName === 'razorpay' ? 'Razorpay' : 'Stripe') + ' credentials saved successfully!', 'success');
        loadPay();
      } catch (err) {
        K.toast(err.message || 'Failed to save payment credentials', 'error');
      }
    });
  }

  async function payCheck(btn) {
    var name = btn.getAttribute('data-pay-check'), out = $('#gsPayOut_' + name, ST.root);
    await K.busy(btn, async function () {
      try {
        var r = await K.api('/api/super-admin/payments/check', { method: 'POST', body: { provider: name } });
        r = r.result || r;
        out.className = 'gs-test-out ' + (r.ok ? 'good' : 'bad'); out.textContent = r.message || (r.ok ? 'Keys work' : 'Check failed');
      } catch (err) { out.className = 'gs-test-out bad'; out.textContent = err.message; }
    });
  }
  function copyFrom(id) {
    var el = document.getElementById(id); if (!el) return;
    var text = el.textContent || '';
    var done = function () { K.toast('Copied', 'success'); };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, function () { selectText(el); });
    else selectText(el);
  }
  function selectText(el) { try { var r = document.createRange(); r.selectNodeContents(el); var s = window.getSelection(); s.removeAllRanges(); s.addRange(r); K.toast('Selected: press Ctrl+C to copy', 'info'); } catch (e) { /* ignore */ } }
  function introHtml(cat) {
    var links = (cat.links || []).filter(function (l) { return l && /^#\/[a-z0-9-]+/.test(l[1] || ''); });
    if (!cat.description && !links.length) return '';
    return '<div class="gs-intro"><div style="min-width:0"><h2>' + esc(cat.label) + '</h2>' + (cat.description ? '<p>' + esc(cat.description) + '</p>' : '') + '</div>' +
      (links.length ? '<div class="gs-links" aria-label="Related pages"><span class="sa-small sa-muted">Related:</span>' + links.map(function (l) {
        return '<a class="btn btn-secondary btn-sm" href="' + esc(l[1]) + '">' + esc(l[0]) + '</a>'; }).join('') + '</div>' : '') + '</div>';
  }
  function chrome() {
    if (!ST.root) return;
    var n = dirtyKeys().length;
    var bar = $('#gsBar', ST.root);
    if (bar) { bar.hidden = !n; $('#gsCount', bar).textContent = plural(n, 'unsaved change'); }
    var ver = $('#gsVer', ST.root); if (ver) ver.innerHTML = K.badge('v' + ST.version, 'neutral');
    var dirtyCats = {};
    dirtyKeys().forEach(function (k) { var w = ST.where[k]; if (w) dirtyCats[w.cat.id] = true; });
    $$('[data-dot]', ST.root).forEach(function (d) { d.hidden = !dirtyCats[d.getAttribute('data-dot')]; });
    ST.cats.forEach(function (c) {
      var el = $('[data-n="' + c.id + '"]', ST.root); if (!el) return;
      var n = customCount(c); el.hidden = !n; el.textContent = n;
      el.title = n ? plural(n, 'setting') + ' changed from the default' : '';
    });
  }
  function showFieldState(k, showErr) {
    var f = $('[data-field="' + k + '"]', ST.root);
    var chip = $('[data-chip="' + k + '"]', ST.root);
    if (chip) chip.innerHTML = stateChip(k);
    if (!f) return;
    var e = $('[data-ferr="' + k + '"]', f);
    var err = ST.errors[k] || '';
    if (showErr !== false) { f.classList.toggle('bad', !!err); if (e) e.textContent = err; }
    else if (!err) { f.classList.remove('bad'); if (e) e.textContent = ''; }
  }
  function setValue(k, raw, showErr) {
    var spec = ST.spec[k]; if (!spec) return;
    var r = validate(spec, raw);
    ST.values[k] = r.err ? (r.v !== undefined ? r.v : raw) : r.v;
    if (r.err) ST.errors[k] = r.err; else delete ST.errors[k];
    // an invalid value that equals the saved one is not a change
    if (r.err && spec.type === 'json' && typeof raw === 'string') { try { if (same(JSON.parse(raw), ST.original[k])) ST.values[k] = ST.original[k]; } catch (e) { /* stays invalid */ } }
    showFieldState(k, showErr);
    if (spec.type === 'path') syncImage(k);
    chrome();
    if (/^(branding\.|general\.app\.(name|tagline))/.test(k)) refreshPreview();
  }
  // an image field: preview only a real image, drop the page-address note once fixed
  function syncImage(k) {
    var root = ST.root, v = ST.values[k], ok = !ST.errors[k] && isImageRef(v);
    var inp = root && $('[data-k="' + k + '"]', root); if (!inp) return;
    var th = $('[data-thumb="' + k + '"]', root);
    if (!th && ok) {
      th = document.createElement('img'); th.className = 'gs-thumb'; th.setAttribute('data-thumb', k);
      th.alt = ((ST.spec[k] || {}).label || k) + ' preview'; inp.parentNode.insertBefore(th, inp);
    }
    if (th) { th.hidden = !ok; if (ok) th.src = v; }
    if (v && !inp.disabled && !$('[data-clear="' + k + '"]', root)) {
      inp.parentNode.insertAdjacentHTML('beforeend', '<button type="button" class="btn btn-ghost btn-sm" data-clear="' + esc(k) + '" title="Empty this field (the built-in default is used), then save">Clear</button>');
    }
    var pl = $('[data-pagelink="' + k + '"]', root); if (pl && (ok || !v)) pl.remove();
  }

  // ── events (delegated once per render of the page) ──
  function bindSettings(root) {
    root.addEventListener('input', function (e) {
      var el = e.target;
      if (el.id === 'gsSearch') { ST.search = el.value; renderBody(); return; }
      var pk = el.getAttribute && el.getAttribute('data-pick');
      if (pk) { var hx = $('[data-k="' + pk + '"]', root); if (hx) hx.value = el.value; setValue(pk, el.value); return; }
      var k = el.getAttribute && el.getAttribute('data-k'); if (!k || el.type === 'checkbox' || el.tagName === 'SELECT') return;
      var spec = ST.spec[k];
      setValue(k, el.value, spec && spec.type !== 'json');
      if (spec && spec.type === 'color') { var p = $('[data-pick="' + k + '"]', root); if (p && HEX.test(el.value.trim()) && el.value.trim().length === 7) p.value = el.value.trim().toLowerCase(); }
    });
    root.addEventListener('change', function (e) {
      var el = e.target, k = el.getAttribute && el.getAttribute('data-k');
      if (el.matches && el.matches('[data-upload-file]')) { upload(el); return; }
      if (!k) return;
      if (el.type === 'checkbox') setValue(k, el.checked);
      else if (el.tagName === 'SELECT') setValue(k, el.value);
    });
    root.addEventListener('focusout', function (e) {
      var k = e.target.getAttribute && e.target.getAttribute('data-k');
      if (k) showFieldState(k, true);
    });
    root.addEventListener('keydown', function (e) {
      var b = e.target.closest && e.target.closest('[data-gs-tab]'); if (!b) return;
      var btns = $$('[data-gs-tab]', root), i = btns.indexOf(b);
      var j = /^(ArrowDown|ArrowRight)$/.test(e.key) ? i + 1 : /^(ArrowUp|ArrowLeft)$/.test(e.key) ? i - 1 : e.key === 'Home' ? 0 : e.key === 'End' ? btns.length - 1 : null;
      if (j == null) return;
      e.preventDefault(); j = (j + btns.length) % btns.length;
      setTab(btns[j].getAttribute('data-gs-tab'), true);
    });
    root.addEventListener('click', function (e) {
      var t = e.target.closest ? e.target : null; if (!t) return;
      var b;
      if ((b = t.closest('[data-gs-tab]'))) { setTab(b.getAttribute('data-gs-tab')); return; }
      if ((b = t.closest('[data-upload]'))) { var fi = $('[data-upload-file="' + b.getAttribute('data-upload') + '"]', root); if (fi) fi.click(); return; }
      if ((b = t.closest('[data-clear]'))) {
        var ck = b.getAttribute('data-clear'), inp = $('[data-k="' + ck + '"]', root);
        if (inp) { inp.value = ''; setValue(ck, ''); }
        b.remove();
        return;
      }
      if ((b = t.closest('[data-reveal]'))) {
        var rid = b.getAttribute('data-reveal'), rInp = document.getElementById(rid);
        if (rInp) {
          var isPw = rInp.type === 'password';
          rInp.type = isPw ? 'text' : 'password';
          b.textContent = isPw ? 'Hide' : 'Show';
          b.setAttribute('aria-pressed', isPw ? 'true' : 'false');
        }
        return;
      }
      if ((b = t.closest('[data-save-pay]'))) { savePay(b, b.getAttribute('data-save-pay')); return; }
      if ((b = t.closest('[data-save-mail]'))) { saveMail(b); return; }
      if ((b = t.closest('[data-reset-group]'))) { resetGroup(b); return; }
      if ((b = t.closest('[data-copy]'))) { copyFrom(b.getAttribute('data-copy')); return; }
      if ((b = t.closest('[data-pay-check]'))) { payCheck(b); return; }
      if ((b = t.closest('[data-setup-refresh]'))) { if (b.getAttribute('data-setup-refresh') === 'mail') loadMail(); else loadPay(); return; }
      if ((b = t.closest('[data-hist-view]'))) { historyDetail(Number(b.getAttribute('data-hist-view'))); return; }
      if ((b = t.closest('[data-hist-restore]'))) { restore(Number(b.getAttribute('data-hist-restore')), b); return; }
      if ((b = t.closest('#gsHistRefresh'))) { loadHistory(b); return; }
      if ((b = t.closest('#gsImportPick'))) { var f2 = $('#gsImportFile', root); if (f2) f2.click(); return; }
      if ((b = t.closest('#gsResetAll'))) { resetAll(b); return; }
      if ((b = t.closest('#gsSave'))) { save(b); return; }
      if ((b = t.closest('#gsDiscard'))) { discard(); K.toast('Changes discarded', 'info'); return; }
    });
    root.addEventListener('change', function (e) {
      if (e.target.id === 'gsImportFile') importFile(e.target);
      if (e.target.id === 'gsPayChoice') {
        var provChoice = e.target.value;
        K.api('/api/super-admin/payments/setup', { method: 'PUT', body: { billing_provider: provChoice } })
          .then(function () { K.toast('Active payment provider set to ' + provChoice, 'success'); loadPay(); })
          .catch(function (err) { K.toast(err.message, 'error'); });
      }
      if (e.target.matches && e.target.matches('[data-mail-preset]')) {
        ST.mailPreset = e.target.value;
        var pObj = MAIL_PRESETS[e.target.value];
        if (pObj) {
          var elH = $('#gsMailHost', root); if (elH) elH.value = pObj.host;
          var elP = $('#gsMailPort', root); if (elP) elP.value = pObj.port;
          var elS = $('#gsMailSec', root); if (elS) elS.value = pObj.security;
          var elU = $('#gsMailUser', root);
          if (elU && (!elU.value || /you@|your-/.test(elU.value))) elU.placeholder = pObj.user;
        }
      }
    });
    root.addEventListener('submit', function (e) {
      var f = e.target.closest && e.target.closest('[data-mail-test]');
      if (f) { e.preventDefault(); mailTest(f); }
    });
  }

  function discard() {
    ST.values = copy(ST.original); ST.errors = {};
    var n = $('#gsNote', ST.root); if (n) n.value = '';
    renderBody(); chrome();
  }

  async function save(btn) {
    var keys = dirtyKeys();
    if (!keys.length) return;
    var bad = keys.filter(function (k) { return ST.errors[k]; });
    if (bad.length) {
      var first = bad[0], w = ST.where[first];
      if (!$('[data-field="' + first + '"]', ST.root) && w) setTab(w.cat.id);
      bad.forEach(function (k) { showFieldState(k, true); });
      var el = $('[data-k="' + first + '"]', ST.root); if (el) el.focus();
      K.toast('Fix ' + plural(bad.length, 'invalid value') + ' before saving: ' + bad.map(function (k) { return (ST.spec[k] || {}).label || k; }).join(', '), 'error');
      return;
    }
    var changed = {};
    keys.forEach(function (k) { changed[k] = ST.values[k]; });
    if (changed['maintenance.enabled'] === true && ST.original['maintenance.enabled'] !== true) {
      var ok = await K.confirmDialog({ title: 'Enable maintenance mode?', confirmLabel: 'Enable maintenance', danger: true,
        message: 'The user app will be blocked for everyone except signed-in staff. You can keep working here, and switch it off again from this page or Maintenance.' });
      if (!ok) return;
    }
    var noteEl = $('#gsNote', ST.root), note = noteEl ? noteEl.value.trim() : '';
    await K.busy(btn, async function () {
      var res;
      try {
        res = await K.api(SB, { method: 'PUT', body: { values: changed, reason: note || undefined } });
      } catch (err) {
        var errs = err.data && err.data.detail && err.data.detail.errors;
        if (errs && typeof errs === 'object') {
          Object.keys(errs).forEach(function (k) { ST.errors[k] = errs[k]; showFieldState(k, true); });
        }
        K.toast(err.message, 'error');
        return;
      }
      var n = (res.changed || []).length;
      K.toast(n ? plural(n, 'setting') + ' saved' + (res.version ? ' (v' + res.version + ')' : '') : 'Nothing changed: the values were already in effect', n ? 'success' : 'info');
      if (noteEl) noteEl.value = '';
      await reload(false);
    });
  }

  async function upload(input) {
    var k = input.getAttribute('data-upload-file'), file = input.files && input.files[0];
    if (!file) return;
    var btn = $('[data-upload="' + k + '"]', ST.root);
    if (file.size > 2 * 1024 * 1024) { K.toast('File too large (max 2 MB)', 'error'); input.value = ''; return; }
    var ext = (file.name.split('.').pop() || '').toLowerCase();
    if (UPLOAD_ACCEPT.indexOf('.' + ext) < 0) { K.toast('Unsupported file type .' + ext + ' — allowed: png, jpg, jpeg, svg, webp, ico', 'error'); input.value = ''; return; }
    var fd = new FormData(); fd.append('file', file);
    await K.busy(btn, async function () {
      try {
        var res = await fetch(SB + '/upload', { method: 'POST', body: fd, credentials: 'same-origin', headers: { Accept: 'application/json' } });
        var data = {}; try { data = await res.json(); } catch (e) { data = {}; }
        if (res.status === 401) { location.href = '/login?superadmin=1'; return; }
        if (!res.ok) throw new Error(errMsg(data, 'Upload failed (' + res.status + ')'));
        var el = $('[data-k="' + k + '"]', ST.root);
        if (el) el.value = data.url;
        setValue(k, data.url);
        K.toast('Uploaded — save to apply', 'success');
      } catch (err) { K.toast(err.message, 'error'); }
    });
    input.value = '';
  }

  async function resetGroup(btn) {
    var gid = btn.getAttribute('data-reset-group');
    var cat = ST.cats.filter(function (c) { return c.id === ST.tab; })[0];
    var g = cat && (cat.groups || []).filter(function (x) { return x.id === gid; })[0];
    if (!g) return;
    var keys = g.specs.map(function (s) { return s.key; });
    var custom = keys.filter(function (k) { return (ST.meta[k] || {}).is_default === false; }).length;
    var r = await K.confirmDialog({ title: 'Reset “' + g.label + '”?', confirmLabel: 'Reset group', reason: 'optional',
      message: 'Reset the ' + plural(keys.length, 'setting') + ' in this group to their built-in defaults (' + custom + ' currently custom). A new version is recorded, so you can roll back from System & history.' });
    if (!r) return;
    await K.busy(btn, async function () {
      try {
        var res = await K.api(SB + '/reset', { method: 'POST', body: { keys: keys, reason: r.reason || undefined } });
        K.toast(plural((res.reset || []).length, 'setting') + ' reset' + (res.version ? ' (v' + res.version + ')' : ''), 'success');
        await reload(true, keys);
      } catch (err) { K.toast(err.message, 'error'); }
    });
  }

  // ── System & history ──
  function systemHtml() {
    var w = canWrite();
    return '<div class="gs-stack">' +
      '<section class="sa-card" data-tour="gs-history" data-section="history"><div class="gs-card-h"><h3>Version history</h3><span class="sa-small sa-muted">Every save, reset, import and restore · current version v' + esc(ST.version) + '</span>' +
      '<span class="gs-r"><button type="button" class="btn btn-secondary btn-sm" id="gsHistRefresh">Refresh</button></span></div>' +
      '<div id="gsHist">' + K.skeleton('rows') + '</div></section>' +
      '<section class="sa-card" data-tour="gs-io" data-section="import"><div class="gs-card-h"><h3>Export & import</h3></div>' +
      '<p class="gs-desc">Export downloads every registered setting as JSON (no secrets). Import checks a file first and shows what will change; unknown or invalid keys are skipped, and keys missing from the file are left alone.</p>' +
      '<div class="sa-row" style="margin-top:12px"><a class="btn btn-secondary btn-sm" href="' + SB + '/export?download=1" download="settings-export.json">Export JSON</a>' +
      '<button type="button" class="btn btn-secondary btn-sm" id="gsImportPick">' + (w ? 'Import JSON' : 'Check an import file') + '</button>' +
      '<input type="file" id="gsImportFile" accept=".json,application/json" hidden></div></section>' +
      (w ? '<section class="sa-card" data-section="danger"><div class="gs-card-h"><h3>Danger zone</h3></div>' +
        '<p class="gs-desc">Reset every registered setting back to its built-in default: branding, limits, AI, features, everything. A version is recorded first, so you can restore it from the history above.</p>' +
        '<div class="sa-row" style="margin-top:12px"><button type="button" class="btn btn-danger btn-sm" id="gsResetAll">Reset all settings</button></div></section>' : '') +
      '</div>';
  }
  async function loadHistory(btn) {
    var box = $('#gsHist', ST.root); if (!box) return;
    await K.busy(btn, async function () {
      try {
        var d = await K.api(SB + '/history?limit=40');
        var vs = d.versions || [];
        if (!vs.length) { box.innerHTML = K.emptyState('No versions yet', 'Your first save, reset or import appears here.'); return; }
        box.innerHTML = '<ul class="gs-hist">' + vs.map(function (v) {
          var n = Object.keys(v.changed || {}).length;
          return '<li><span>' + K.badge('v' + v.version, v.version === ST.version ? 'success' : 'primary') + '</span><div class="gs-grow"><div><b>' + esc(v.reason || 'settings change') + '</b> <span class="sa-muted">· ' + plural(n, 'change') + '</span>' + (v.version === ST.version ? ' <span class="sa-small sa-muted">(current)</span>' : '') + '</div>' +
            '<div class="sa-small sa-muted">by ' + esc(v.changed_by || '—') + (v.ip ? ' · ' + esc(v.ip) : '') + ' · ' + when(v.created_at) + '</div></div>' +
            '<div class="sa-row"><button type="button" class="btn btn-secondary btn-xs" data-hist-view="' + esc(v.version) + '" aria-label="View version ' + esc(v.version) + '">View</button>' +
            (canWrite() ? '<button type="button" class="btn btn-ghost btn-xs" data-hist-restore="' + esc(v.version) + '" aria-label="Restore version ' + esc(v.version) + '">Restore</button>' : '') + '</div></li>';
        }).join('') + '</ul>' + (vs.length >= 40 ? '<p class="sa-small sa-muted" style="margin:8px 0 0">The 40 newest versions are shown.</p>' : '');
      } catch (err) { box.innerHTML = K.errorState(err, function () { loadHistory(); }); }
    });
  }
  function diffTable(changed) {
    var keys = Object.keys(changed || {});
    if (!keys.length) return '<p class="sa-muted">No field-level changes were recorded for this version.</p>';
    return '<div class="sa-table-wrap gs-scroll"><table class="sa-table gs-diff"><thead><tr><th>Setting</th><th>Before</th><th>After</th></tr></thead><tbody>' + keys.map(function (k) {
      var c = changed[k] || {}, s = ST.spec[k];
      return '<tr><td><span class="cell-main">' + esc(s ? s.label : k) + '</span><span class="cell-sub sa-mono">' + esc(k) + '</span></td><td class="old sa-mono">' + esc(fmtVal(c.old)) + '</td><td class="new sa-mono">' + esc(fmtVal(c.new)) + '</td></tr>';
    }).join('') + '</tbody></table></div>';
  }
  async function historyDetail(version) {
    var d;
    try { d = await K.api(SB + '/history/' + encodeURIComponent(version)); } catch (err) { K.toast(err.message, 'error'); return; }
    var e = d.entry || {}, n = Object.keys(e.changed || {}).length;
    var r = await K.openModal({ title: 'v' + version + ' — ' + (e.reason || 'settings change'), size: 'lg', cancelLabel: 'Close',
      submitLabel: canWrite() ? 'Restore this version…' : null, danger: false,
      body: '<p class="sa-small sa-muted" style="margin:0 0 10px">by ' + esc(e.changed_by || '—') + (e.ip ? ' · ' + esc(e.ip) : '') + ' · ' + when(e.created_at) + ' · ' + plural(n, 'change') + '</p>' + diffTable(e.changed) +
        '<p class="sa-small sa-muted" style="margin:10px 0 0">Restoring reapplies the full snapshot saved with this version (all ' + esc(Object.keys(e.snapshot || {}).length) + ' settings) as a new version.</p>' });
    if (r) restore(version);
  }
  async function restore(version, btn) {
    var r = await K.confirmDialog({ title: 'Restore v' + version + '?', confirmLabel: 'Restore', danger: false, reason: 'optional',
      message: 'Every setting goes back to the value it had right after v' + version + '. This is recorded as a NEW version, so you can undo it the same way.' });
    if (!r) return;
    await K.busy(btn, async function () {
      try {
        var res = await K.api(SB + '/history/' + encodeURIComponent(version) + '/restore', { method: 'POST', body: r.reason ? { reason: r.reason } : {} });
        var n = (res.applied || []).length;
        K.toast(n ? 'Restored ' + plural(n, 'setting') + ' from v' + version + ' (now v' + res.version + ')' : 'Nothing to restore: the settings already match v' + version, n ? 'success' : 'info');
        await reload(true, res.applied || []);
      } catch (err) { K.toast(err.message, 'error'); }
    });
  }
  async function resetAll(btn) {
    var r = await K.confirmDialog({ title: 'Reset ALL settings?', confirmLabel: 'Reset everything', reason: 'optional', typeToConfirm: 'RESET',
      message: 'Every registered setting returns to its built-in default: branding, limits, AI, features, everything. A version is recorded so you can restore it.' });
    if (!r) return;
    await K.busy(btn, async function () {
      try {
        var res = await K.api(SB + '/reset', { method: 'POST', body: { all: true, reason: r.reason || undefined } });
        K.toast(plural((res.reset || []).length, 'setting') + ' reset to defaults' + (res.version ? ' (v' + res.version + ')' : ''), 'success');
        await reload(false);
      } catch (err) { K.toast(err.message, 'error'); }
    });
  }
  async function importFile(input) {
    var file = input.files && input.files[0];
    if (!file) return;
    input.value = '';
    var payload;
    try { payload = JSON.parse(await file.text()); } catch (e) { K.toast('That file is not valid JSON', 'error'); return; }
    if (!payload || typeof payload !== 'object' || !payload.settings || typeof payload.settings !== 'object' || Array.isArray(payload.settings)) {
      K.toast('The file must contain a "settings" object (use a file made by Export JSON)', 'error'); return;
    }
    var pv;
    try { pv = await K.api(SB + '/import/preview', { method: 'POST', body: { payload: payload } }); } catch (err) { K.toast(err.message, 'error'); return; }
    var inv = Object.keys(pv.invalid || {}), count = pv.count || 0, w = canWrite();
    var changes = (pv.valid_keys || []).filter(function (k) { return !same(payload.settings[k], ST.original[k]); });
    var body = '<p style="margin:0 0 8px"><b>' + esc(file.name) + '</b>' + (payload.exported_at ? ' <span class="sa-muted">· exported ' + esc(K.fmtDT(utc(payload.exported_at))) + (payload.version ? ' at v' + esc(payload.version) : '') + '</span>' : '') + '</p>' +
      '<div class="sa-row" style="margin-bottom:10px">' + K.badge(count + ' valid', 'success') + K.badge(changes.length + ' differ from now', changes.length ? 'warning' : 'neutral') + (inv.length ? K.badge(inv.length + ' invalid (skipped)', 'danger') : '') + '</div>' +
      (changes.length ? '<h4 style="margin:6px 0">What will change</h4>' + diffTable(changes.reduce(function (m, k) { m[k] = { old: ST.original[k], new: payload.settings[k] }; return m; }, {})) : '') +
      (inv.length ? '<h4 style="margin:12px 0 6px">Skipped</h4><div class="sa-table-wrap gs-scroll"><table class="sa-table"><thead><tr><th>Key</th><th>Why</th></tr></thead><tbody>' +
        inv.map(function (k) { return '<tr><td class="sa-mono">' + esc(k) + '</td><td style="color:var(--danger-text)">' + esc(pv.invalid[k]) + '</td></tr>'; }).join('') + '</tbody></table></div>' : '') +
      (w ? (count ? '<div class="sa-field" style="margin-top:12px"><label for="gsImpReason">Change note <span class="sa-muted">(optional)</span></label><input class="form-input" id="gsImpReason" name="reason" maxlength="300" placeholder="settings import"></div>' : '')
        : '<div class="alert alert-info" style="margin-top:12px"><div class="alert-body">Only managers and the Super Admin can apply an import.</div></div>');
    var res = await K.openModal({ title: 'Import preview', size: 'lg', body: body, cancelLabel: w && count ? 'Cancel' : 'Close',
      submitLabel: w && count ? 'Apply ' + count : null,
      onSubmit: async function (form, fd) {
        var reason = String(fd.get('reason') || '').trim();
        return await K.api(SB + '/import', { method: 'POST', body: { payload: payload, reason: reason || undefined } });
      } });
    if (!res || res === true) return;
    var n = (res.applied || []).length, sk = Object.keys(res.skipped || {}).length;
    K.toast(n ? plural(n, 'setting') + ' imported (v' + res.version + ')' + (sk ? ' · ' + sk + ' skipped' : '') : 'Nothing changed: the file matches the current settings' + (sk ? ' · ' + sk + ' skipped' : ''), n ? 'success' : 'info');
    await reload(true, res.applied || []);
  }

  // ── unsaved-changes guard (links, back button, the guide, closing the tab) ──
  function keyOf(hash) { return String(hash || '').replace(/^#\/?/, '').split('?')[0].split('/')[0]; }
  function onSettings() { return ST.active && keyOf(location.hash) === 'settings'; }
  async function askDiscard(dest) {
    var n = dirtyKeys().length;
    if (!n) { K.go(dest); return; }
    var ok = await K.openModal({ title: 'Discard unsaved changes?', submitLabel: 'Discard and leave', cancelLabel: 'Stay here', danger: true,
      body: '<p style="margin:0;color:var(--text-secondary)">You have ' + esc(plural(n, 'unsaved change')) + ' in Global Settings. Leave this page and lose ' + (n === 1 ? 'it' : 'them') + '?</p>' });
    if (!ok) return;
    ST.values = copy(ST.original); ST.errors = {}; ST.leaving = true;
    K.go(dest);
  }
  document.addEventListener('click', function (e) {
    if (!onSettings() || !dirtyKeys().length || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
    var a = e.target.closest && e.target.closest('a[href^="#"],[data-go]');
    if (!a || a.target === '_blank') return;
    var dest = a.getAttribute('data-go') || a.getAttribute('href');
    if (!dest || keyOf(dest) === 'settings' || /^\/admin/.test(dest)) return;
    if (dest.charAt(0) !== '#') return;
    e.preventDefault(); e.stopPropagation();
    askDiscard(dest);
  }, true);
  window.addEventListener('hashchange', function () {
    if (keyOf(location.hash) === 'settings') return;
    if (ST.active && !ST.leaving && dirtyKeys().length) {
      // back button / typed URL / guide: come back with the edits kept, then ask
      ST.bounceTo = location.hash;
      location.replace('#/settings' + (ST.tab !== 'general' ? '?tab=' + encodeURIComponent(ST.tab) : ''));
      return;
    }
    ST.active = false;
  });
  window.addEventListener('beforeunload', function (e) {
    if (onSettings() && dirtyKeys().length) { e.preventDefault(); e.returnValue = ''; }
  });

  // ════════════════════════════════════════════════════════
  //  ENVIRONMENT
  // ════════════════════════════════════════════════════════
  var ENV = { root: null, vars: {} };
  var PROVIDER_KEYS = { APIFY_API_TOKEN: 'Apify', GEMINI_API_KEY: 'Gemini' };
  function keysLink(label) {
    return isSuper() ? '<a href="#/provider-keys">' + esc(label || 'API keys') + '</a>' : 'the Super Admin\'s API keys page';
  }
  async function viewEnvironment(root) {
    ENV.root = root;
    await renderEnv();
  }
  async function renderEnv() {
    var root = ENV.root;
    var lock = await K.api(EB + '/lock-status');
    var data = null;
    if (!lock.locked) {
      try { data = await K.api(EB); } catch (err) { if (err.status === 403) lock.locked = true; else throw err; }
    }
    var locked = !!lock.locked, w = canWrite(), sup = isSuper(), mins = lock.unlock_minutes || 15;
    var groups = [], byGroup = {};
    ENV.vars = {};
    ((data && data.vars) || []).forEach(function (v) {
      ENV.vars[v.name] = v;
      var g = v.group || 'Other';
      if (!byGroup[g]) { byGroup[g] = []; groups.push(g); }
      byGroup[g].push(v);
    });
    root.innerHTML = K.header('Environment',
      'Server variables: masked secrets, database overrides, where each value comes from, and which ones need a restart. The page is locked behind a guard password that is separate from your sign-in password.',
      (w ? '' : K.badge('Read-only', 'warning') + ' ') + (locked ? K.badge('Locked', 'neutral') : K.badge('Unlocked', 'success')) +
      (locked ? '' : '<button type="button" class="btn btn-secondary btn-sm" id="evLock">Lock now</button>') +
      '<button type="button" class="btn btn-ghost btn-sm" id="evReload">Reload</button>') +
      '<div class="ev-cards">' +
      '<div class="alert alert-' + (locked ? 'warning' : 'success') + '" data-tour="env-lock"><div class="alert-body"><div class="alert-title">' + (locked ? 'The environment is locked' : 'Unlocked') + '</div><div>' +
      (locked ? (lock.guard_set === false ? 'No guard password has been set yet, so this page cannot be unlocked.' : 'Enter the guard password to see and edit the variables for ' + esc(mins) + ' minutes.')
        : 'Edits are saved as database overrides; the .env value stays as the fallback. The unlock lasts ' + esc(mins) + ' minutes, or until you lock it.') + '</div></div></div>' +
      '<div class="alert alert-info" data-tour="env-keys"><div class="alert-body">LeadAI\'s own <b>Apify</b> and <b>Gemini</b> keys (<span class="sa-mono">APIFY_API_TOKEN</span>, <span class="sa-mono">GEMINI_API_KEY</span>) can also be replaced, tested and reset without unlocking this page, on ' + keysLink() + '. Keys saved there are stored encrypted and tested before they are saved.</div></div>' +
      (locked ? lockedHtml(lock, sup) : unlockedHtml(groups, byGroup, w, sup, lock)) + '</div>';
    bindEnv(root, lock);
  }
  function guardForm(change) {
    return '<form class="ev-form" data-guard-form novalidate>' +
      (change ? '<div class="sa-field"><label for="evGCur">Current guard password</label><input class="form-input" id="evGCur" name="current" type="password" autocomplete="current-password" required></div>' : '') +
      '<div class="sa-field"><label for="evGNew">New guard password</label><input class="form-input" id="evGNew" name="new" type="password" autocomplete="new-password" minlength="8" placeholder="8+ characters" required></div>' +
      '<div class="sa-field"><label for="evGNew2">Repeat it</label><input class="form-input" id="evGNew2" name="new2" type="password" autocomplete="new-password" required></div>' +
      '<div class="sa-err" data-guard-err role="alert"></div>' +
      '<div class="sa-row"><button type="submit" class="btn btn-primary btn-sm">' + (change ? 'Change guard password' : 'Set guard password') + '</button></div></form>';
  }
  function guardCard(change) {
    return '<section class="sa-card" data-tour="env-guard"><div class="gs-card-h"><h3>' + (change ? 'Change the guard password' : 'Set the guard password') + '</h3>' + K.badge('Super Admin', 'primary') + '</div>' +
      '<p class="gs-desc" style="margin-bottom:10px">' + (change ? 'The password that unlocks this page (not a sign-in password). You need the current one to change it.'
        : 'Choose one with 8 or more characters, separate from your sign-in password. Staff need it to unlock this page.') + '</p>' + guardForm(change) + '</section>';
  }
  function lockedHtml(lock, sup) {
    if (lock.guard_set === false) {
      return sup ? '<div class="sa-2 sa-grid" data-tour="env-unlock">' + guardCard(false) + '</div>'
        : '<section class="sa-card" data-tour="env-unlock" style="max-width:520px"><h3>Set the guard password</h3><p class="gs-desc">No guard password has been set yet. Ask the Super Admin to set one; you will need it to unlock this page.</p></section>';
    }
    return '<div class="sa-grid sa-2"><section class="sa-card" data-tour="env-unlock"><h3>Unlock environment</h3>' +
      '<form class="ev-form" id="evUnlockForm" novalidate><div class="sa-field"><label for="evPw">Guard password</label><input class="form-input" id="evPw" type="password" autocomplete="off" placeholder="••••••••" required></div>' +
      '<div class="sa-err" id="evUnlockErr" role="alert"></div><div class="sa-row"><button type="submit" class="btn btn-primary btn-sm">Unlock</button></div></form>' +
      '<p class="sa-small sa-muted" style="margin:10px 0 0">Unlocking lasts ' + esc(lock.unlock_minutes || 15) + ' minutes. Failed attempts are recorded in the audit log.</p></section>' +
      (sup ? guardCard(true) : '') + '</div>';
  }
  function srcBadge(v) {
    if (v.managed_elsewhere) return K.badge('Hosting environment', 'neutral');
    if (v.overridden) return K.badge('Override', 'primary');
    return K.badge(v.source === 'env' ? 'From .env' : v.source === 'default' || !v.source ? 'Default' : K.titleCase(v.source), 'neutral');
  }
  function shownValue(v) {
    if (v.secret) {
      if (v.managed_elsewhere) return v.set ? '••••••••' : 'not set';
      return v.masked || (v.set ? '••••••••' : 'not set');
    }
    var s = v.value == null || v.value === '' ? '' : String(v.value);
    if (!s) return v.set === false ? 'not set' : '(empty)';
    return s.length > 60 ? s.slice(0, 60) + '…' : s;
  }
  function envRow(v, w) {
    var pk = PROVIDER_KEYS[v.name];
    var acts;
    if (v.managed_elsewhere) acts = '<span class="sa-small sa-muted">Set in the hosting environment (e.g. Render) — read-only here</span>';
    else if (!w) acts = '';
    else acts = '<button type="button" class="btn btn-secondary btn-xs" data-ev-edit="' + esc(v.name) + '">Edit</button>' +
      (v.overridden ? '<button type="button" class="btn btn-danger btn-xs" data-ev-reset="' + esc(v.name) + '">Reset</button>' : '');
    if (pk && isSuper()) acts += '<a class="btn btn-ghost btn-xs" href="#/provider-keys">Manage on API keys</a>';
    return '<div class="ev-row" data-env="' + esc(v.name) + '"><div class="ev-main"><div class="ev-name"><b>' + esc(v.name) + '</b>' +
      (v.secret ? K.badge('Secret', 'neutral') : '') + srcBadge(v) + (v.restart ? K.badge('Restart needed', 'warning') : '') + '</div>' +
      '<div class="ev-val" title="' + esc(v.secret ? 'Secret values are never shown' : String(v.value == null ? '' : v.value)) + '">' + esc(shownValue(v)) + '</div>' +
      (v.description ? '<div class="sa-small sa-muted">' + esc(v.description) + '</div>' : '') +
      (pk ? '<div class="sa-small">Same key as on ' + keysLink('API keys') + ' (' + pk + ').</div>' : '') +
      (v.overridden && (v.updated_by || v.updated_at) ? '<div class="sa-small sa-muted">Override set' + (v.updated_by ? ' by ' + esc(v.updated_by) : '') + (v.updated_at ? ' · ' + when(v.updated_at) : '') + '</div>' : '') +
      (v.secret && !v.managed_elsewhere ? '<div class="sa-small sa-muted">The value is never shown, only its last characters.</div>' : '') +
      '</div><div class="ev-acts">' + acts + '</div></div>';
  }
  function unlockedHtml(groups, byGroup, w, sup) {
    return groups.map(function (g, i) {
      return '<section class="sa-card"' + (i === 0 ? ' data-tour="env-vars"' : '') + ' data-section="' + esc(g.toLowerCase()) + '"><div class="gs-card-h"><h3>' + esc(g) + '</h3><span class="sa-small sa-muted">' + plural(byGroup[g].length, 'variable') + '</span></div>' +
        byGroup[g].map(function (v) { return envRow(v, w); }).join('') + '</section>';
    }).join('') +
      '<div class="sa-grid sa-2"><section class="sa-card" data-section="recovery"><h3>Recovery sign-in password</h3>' +
      '<p class="gs-desc" style="margin-bottom:10px">Sets the legacy recovery admin password (<span class="sa-mono">PANEL_ADMIN_PASSWORD_HASH</span>). It is hashed on the server, stored as an override and applies at the next staff sign-in. It does <b>not</b> change this page\'s guard password, and it is refused while <span class="sa-mono">SUPERADMIN_PASSWORD</span> in the hosting environment defines the Super Admin.</p>' +
      (w ? '<form class="ev-form" id="evRecForm" novalidate><div class="sa-field"><label for="evRec">New password</label><input class="form-input" id="evRec" type="password" autocomplete="new-password" minlength="8" placeholder="8+ characters" required></div>' +
        '<div class="sa-field"><label for="evRec2">Repeat it</label><input class="form-input" id="evRec2" type="password" autocomplete="new-password" required></div>' +
        '<div class="sa-err" id="evRecErr" role="alert"></div><div class="sa-row"><button type="submit" class="btn btn-primary btn-sm">Set recovery password</button></div></form>'
        : '<p class="sa-small sa-muted">Managers and the Super Admin can set it.</p>') + '</section>' +
      (sup ? guardCard(true) : '') + '</div>';
  }
  function bindEnv(root, lock) {
    var rl = $('#evReload', root); if (rl) rl.onclick = function () { K.busy(rl, renderEnv).catch(function (e) { K.toast(e.message, 'error'); }); };
    var lk = $('#evLock', root);
    if (lk) lk.onclick = function () {
      K.busy(lk, async function () {
        try { await K.api(EB + '/unlock', { method: 'DELETE' }); K.toast('Environment locked', 'success'); await renderEnv(); }
        catch (e) { K.toast(e.message, 'error'); }
      });
    };
    var uf = $('#evUnlockForm', root);
    if (uf) {
      uf.onsubmit = function (e) {
        e.preventDefault();
        var pw = $('#evPw', uf).value, err = $('#evUnlockErr', uf);
        err.textContent = '';
        if (!pw.trim()) { err.textContent = 'Enter the guard password.'; $('#evPw', uf).focus(); return; }
        K.busy($('button[type=submit]', uf), async function () {
          try { await call('POST', EB + '/unlock', { password: pw }); K.toast('Environment unlocked for ' + (lock.unlock_minutes || 15) + ' minutes', 'success'); await renderEnv(); }
          catch (ex) { err.textContent = ex.status === 401 ? 'Wrong guard password.' : ex.message; var i = $('#evPw', uf); if (i) { i.select(); i.focus(); } }
        });
      };
      setTimeout(function () { var i = $('#evPw', uf); if (i && !(window.LeadTour && window.LeadTour.isActive && window.LeadTour.isActive())) i.focus({ preventScroll: true }); }, 0);
    }
    $$('[data-guard-form]', root).forEach(function (f) {
      f.onsubmit = function (e) {
        e.preventDefault();
        var cur = f.elements.current ? f.elements.current.value : undefined, pw = f.elements.new.value, pw2 = f.elements.new2.value, err = $('[data-guard-err]', f);
        err.textContent = '';
        if (f.elements.current && !cur) { err.textContent = 'Enter the current guard password.'; return; }
        if (pw.length < 8) { err.textContent = 'Use at least 8 characters.'; return; }
        if (pw !== pw2) { err.textContent = 'The two passwords don\'t match.'; return; }
        K.busy($('button[type=submit]', f), async function () {
          try {
            await call('PUT', EB + '/guard', { new_password: pw, current_password: cur });
            K.toast(cur !== undefined ? 'Guard password changed' : 'Guard password set', 'success');
            await renderEnv();
          } catch (ex) { err.textContent = ex.message; }
        });
      };
    });
    var rf = $('#evRecForm', root);
    if (rf) rf.onsubmit = function (e) {
      e.preventDefault();
      var pw = $('#evRec', rf).value, pw2 = $('#evRec2', rf).value, err = $('#evRecErr', rf);
      err.textContent = '';
      if (pw.trim().length < 8) { err.textContent = 'Use at least 8 characters.'; return; }
      if (pw !== pw2) { err.textContent = 'The two passwords don\'t match.'; return; }
      K.busy($('button[type=submit]', rf), async function () {
        try { await call('POST', EB + '/password', { new_password: pw.trim() }); K.toast('Recovery sign-in password changed', 'success'); rf.reset(); }
        catch (ex) { err.textContent = ex.message; }
      });
    };
    $$('[data-ev-edit]', root).forEach(function (b) { b.onclick = function () { editVar(b); }; });
    $$('[data-ev-reset]', root).forEach(function (b) {
      b.onclick = async function () {
        var name = b.getAttribute('data-ev-reset');
        var restart = !!(ENV.vars[name] || {}).restart;
        var ok = await K.confirmDialog({ title: 'Reset ' + name + '?', confirmLabel: 'Reset',
          message: 'Remove the database override for ' + name + '? The .env value (or the built-in default) applies again.' + (restart ? ' This variable needs a restart to take effect.' : '') });
        if (!ok) return;
        K.busy(b, async function () {
          try { await call('DELETE', EB + '/' + encodeURIComponent(name)); K.toast(name + ' reset', 'success'); await renderEnv(); }
          catch (ex) { K.toast(ex.message, 'error'); }
        });
      };
    });
  }
  function editVar(btn) {
    var name = btn.getAttribute('data-ev-edit'), row = btn.closest('.ev-row');
    if (!row || $('.ev-edit', row)) return;
    var v = ENV.vars[name] || {};
    var secret = !!v.secret, restart = !!v.restart, kind = v.kind || 'str';
    var cur = secret || v.value == null ? '' : String(v.value);
    var id = 'evE_' + name;
    var input = kind === 'bool'
      ? '<select class="form-select" id="' + id + '"><option value="true"' + (/^(1|true|yes|on)$/i.test(cur) ? ' selected' : '') + '>true</option><option value="false"' + (/^(1|true|yes|on)$/i.test(cur) ? '' : ' selected') + '>false</option></select>'
      : '<input class="form-input" id="' + id + '" type="' + (secret ? 'password' : kind === 'int' ? 'number' : 'text') + '"' + (kind === 'int' ? ' step="1"' : '') + ' value="' + esc(cur) + '" placeholder="' + (secret ? 'New secret value' : 'New value') + '" autocomplete="' + (secret ? 'new-password' : 'off') + '" spellcheck="false">';
    var acts = $('.ev-acts', row);
    acts.hidden = true;
    var f = document.createElement('form');
    f.className = 'ev-edit'; f.noValidate = true;
    f.innerHTML = '<label class="sr-only" for="' + esc(id) + '">New value for ' + esc(name) + '</label>' + input +
      '<button type="submit" class="btn btn-primary btn-xs">Save</button><button type="button" class="btn btn-ghost btn-xs" data-cancel>Cancel</button>' +
      (restart ? '<span class="sa-small sa-muted" style="flex-basis:100%">Takes effect after the server restarts.</span>' : '');
    row.appendChild(f);
    var el = f.querySelector('#' + CSS.escape(id));
    el.focus();
    function cancel() { f.remove(); acts.hidden = false; btn.focus(); }
    $('[data-cancel]', f).onclick = cancel;
    f.addEventListener('keydown', function (e) { if (e.key === 'Escape') { e.stopPropagation(); cancel(); } });
    f.onsubmit = function (e) {
      e.preventDefault();
      var value = String(el.value).trim();
      if (!value) { K.toast('The value cannot be empty. Use Reset to go back to the .env value.', 'error'); el.focus(); return; }
      K.busy($('button[type=submit]', f), async function () {
        try {
          await call('PUT', EB + '/' + encodeURIComponent(name), { value: value });
          K.toast(name + ' saved' + (restart ? ' — restart the server to apply it' : ''), 'success');
          await renderEnv();
        } catch (ex) { K.toast(ex.message, 'error'); }
      });
    };
  }

  // ════════════════════════════════════════════════════════
  //  GUIDES + REGISTRATION
  // ════════════════════════════════════════════════════════
  function defineGuides() {
    if (!window.LeadTour) return;
    LeadTour.define('gs-settings', { title: 'Global Settings', steps: [
      { route: '#/settings', target: '[data-tour="gs-tabs"]', title: 'Settings by topic',
        body: 'Settings are grouped by topic: Brand & website (name, logos, colours, SEO), Leads engine (AI, scoring, detection, limits, platforms, downloads), Communication (email and alerts) and Operations (security, maintenance).\n\nThe number on a tab counts settings changed from the default; a dot means unsaved changes.' },
      { route: '#/settings', target: '[data-tour="gs-search"]', title: 'Search every setting',
        body: 'Type part of a name, a description or a key (for example "colors" or "timeout") to find matching settings in every section at once.' },
      { route: '#/settings', target: '[data-tour="gs-group"]', optional: true, title: 'Change, then save',
        body: 'Each setting shows whether it uses the built-in Default or a Custom value. Values are checked as you type: limits, colours, links and JSON.\n\nWhen you change something, a bar appears at the bottom with Save and Discard. Reset group puts a whole card back to its defaults.' },
      { route: '#/settings?tab=system', target: '[data-tour="gs-history"]', title: 'Version history and rollback',
        body: 'Every save, reset, import and restore creates a version. View shows exactly what changed; Restore brings back the settings as they were, as a new version.' },
      { route: '#/settings?tab=system', target: '[data-tour="gs-io"]', title: 'Export and import',
        body: 'Export downloads all settings as a JSON file (no secrets). Import shows a preview of what would change before anything is applied.' }
    ] });
    LeadTour.define('gs-env', { title: 'Environment & secrets', steps: [
      { route: '#/environment', target: '[data-tour="env-lock"]', title: 'The guard-password lock',
        body: 'Server variables and secrets are behind a second password, the guard password. It is separate from your sign-in password, and an unlock lasts a few minutes.' },
      { route: '#/environment', target: '[data-tour="env-keys"]', optional: true, title: 'Apify and Gemini keys',
        body: 'LeadAI\'s own Apify and Gemini keys are easier to change on API keys: they are tested and stored encrypted there, without unlocking this page.' },
      { route: '#/environment', target: '[data-tour="env-unlock"]', optional: true, title: 'Unlock to see the variables',
        body: 'Enter the guard password here. Every unlock, and every failed attempt, is recorded in the audit log.' },
      { route: '#/environment', target: '[data-tour="env-vars"]', optional: true, title: 'Variables',
        body: 'Each variable shows where its value comes from (.env, default or an override saved here) and whether it needs a restart. Secrets only show their last characters.\n\nEdit saves an override; Reset removes it so the .env value applies again. Variables set in the hosting environment are read-only.' },
      { route: '#/environment', target: '[data-tour="env-guard"]', optional: true, title: 'The guard password',
        body: 'Only the Super Admin can set or change the guard password. Changing it needs the current one.' },
      { route: '#/settings', target: '[data-tour="gs-tabs"]', title: 'Everything else: Global Settings',
        body: 'Branding, limits, features, email and the rest of the platform\'s settings are in Global Settings.' }
    ] });
    if (K.addGuide) {
      K.addGuide({ id: 'gs-settings', label: 'Global Settings', description: 'Sections, search, saving, history and rollback', route: 'settings' });
      K.addGuide({ id: 'gs-env', label: 'Environment & secrets', description: 'The guard password, keys and variables', route: 'environment' });
    }
  }

  K.register('Settings', 'System', [
    ['settings', 'Global Settings', 'gear', null, 'viewer'],
    ['environment', 'Environment', 'lock', null, 'viewer']
  ], {
    settings: [viewSettings, 'Global Settings', null, 'viewer'],
    environment: [viewEnvironment, 'Environment', null, 'viewer']
  });
  defineGuides();
})();
