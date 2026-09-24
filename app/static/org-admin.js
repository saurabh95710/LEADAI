/* LeadAI — Organization Admin Portal (own-organization management).
 * Vanilla JS, hash routing. Every value rendered into HTML goes through esc().
 * Frontend checks are cosmetic: the backend enforces every permission. */
(function () {
  'use strict';

  // ════════════════════════════════════════════════════════════════════
  // Utilities
  // ════════════════════════════════════════════════════════════════════
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"'`]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;', '`': '&#96;' }[c]));
  const attr = esc;
  const S = { me: null, ctx: null, org: null, members: null, catalog: null, transitions: null };

  class ApiError extends Error {
    constructor(status, data, network) {
      super(errMsg(data, network ? 'Network error — check your connection and try again.' : 'Request failed (' + status + ').'));
      this.status = status; this.data = data || {}; this.network = !!network;
      this.code = data && data.detail && typeof data.detail === 'object' ? data.detail.code : '';
    }
  }
  function errMsg(data, fallback) {
    const d = data && (data.detail !== undefined ? data.detail : (data.message || data.error));
    if (typeof d === 'string' && d.trim()) return d;
    if (d && typeof d === 'object' && !Array.isArray(d) && d.message) return String(d.message);
    if (Array.isArray(d) && d.length) {
      const f = d[0] || {}; const field = Array.isArray(f.loc) ? f.loc[f.loc.length - 1] : '';
      return (field ? String(field).replace(/_/g, ' ') + ': ' : '') + String(f.msg || '').replace(/^Value error,\s*/i, '');
    }
    return fallback || 'Something went wrong. Please try again.';
  }
  async function api(url, opts) {
    opts = opts || {};
    const init = { method: opts.method || 'GET', credentials: 'same-origin', headers: { Accept: 'application/json' } };
    if (opts.body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.body); }
    let res;
    try { res = await fetch(url, init); } catch (_) { throw new ApiError(0, {}, true); }
    let data = {};
    try { data = await res.json(); } catch (_) { data = {}; }
    if (res.status === 401 && !opts.noRedirect) { location.href = '/login?next=' + encodeURIComponent('/org-admin' + location.hash); throw new ApiError(401, data); }
    if (!res.ok) {
      const e = new ApiError(res.status, data);
      if (e.code === 'admin_portal_disabled' && !opts.noGate) showLockedGate();
      throw e;
    }
    return data;
  }
  function qs(obj) {
    const p = new URLSearchParams();
    Object.entries(obj || {}).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') p.set(k, v); });
    const s = p.toString();
    return s ? '?' + s : '';
  }
  const nf = new Intl.NumberFormat();
  const num = (n) => (n === null || n === undefined || n === '' ? '—' : nf.format(Number(n) || 0));
  function toDate(v) { if (!v) return null; const d = new Date(v); return isNaN(d) ? null : d; }
  function fmtDate(v, withTime) {
    const d = toDate(v); if (!d) return '—';
    return withTime ? d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : d.toLocaleDateString(undefined, { dateStyle: 'medium' });
  }
  function ago(v) {
    const d = toDate(v); if (!d) return '—';
    const s = Math.round((Date.now() - d.getTime()) / 1000);
    if (s < 45) return 'just now';
    const m = Math.round(s / 60); if (m < 60) return m + ' min ago';
    const h = Math.round(m / 60); if (h < 24) return h + ' h ago';
    const dd = Math.round(h / 24); if (dd < 30) return dd + ' d ago';
    return fmtDate(v);
  }
  const timeTag = (v) => v ? `<time datetime="${attr(v)}" title="${attr(fmtDate(v, true))}">${esc(ago(v))}</time>` : '<span class="oa-muted">—</span>';
  const label = (s) => String(s || '').replace(/_/g, ' ');
  const cap = (s) => String(s || '').charAt(0).toUpperCase() + String(s || '').slice(1);
  // Canonical status → tone mapping (shared LeadAI palette, see STEP3 context).
  // One table so the whole portal switches together; CSS styles .oa-pill[data-tone].
  const STATUS_ALIAS = { error: 'failed', canceled: 'cancelled' };
  const TONES = {
    success: ['active', 'completed', 'success', 'succeeded', 'paid', 'confirmed', 'converted', 'accepted', 'resolved', 'healthy', 'ok'],
    info: ['running', 'processing', 'in_progress', 'open', 'new', 'contacted', 'qualified', 'info', 'queued'],
    warning: ['pending', 'pending_payment', 'payment_received', 'pending_admin_confirmation', 'draft', 'invited', 'follow_up', 'waiting', 'degraded'],
    danger: ['failed', 'rejected', 'refund_due', 'failure', 'lost', 'disqualified', 'past_due', 'unavailable'],
    suspended: ['suspended'],
    neutral: ['deactivated', 'inactive', 'archived', 'cancelled', 'expired', 'revoked', 'closed', 'removed', 'aborted', 'paused', 'none', 'unknown'],
    brand: ['demo', 'trial', 'trialing'],
  };
  const TONE_OF = {};
  Object.keys(TONES).forEach(t => TONES[t].forEach(s => { TONE_OF[s] = t; }));
  const LIVE = { running: 1, processing: 1, in_progress: 1 };
  function pill(status, text) {
    const raw = String(status || 'unknown').toLowerCase();
    const s = STATUS_ALIAS[raw] || raw;
    return `<span class="oa-pill s-${attr(s)}" data-status="${attr(s)}" data-tone="${TONE_OF[s] || 'neutral'}"${LIVE[s] ? ' data-live' : ''}>${esc(text || label(s))}</span>`;
  }
  const ROLE_LABELS = { owner: 'Admin (Owner)', admin: 'Admin', manager: 'Manager', member: 'User', viewer: 'Viewer' };
  const roleTag = (r) => `<span class="oa-role r-${attr(r)}">${esc(ROLE_LABELS[r] || r || '—')}</span>`;
  function csvCell(v) {
    let s = v == null ? '' : String(v);
    if (/^[=+\-@\t\r]/.test(s)) s = "'" + s; // neutralise spreadsheet formulas
    return '"' + s.replace(/"/g, '""') + '"';
  }
  function htmlText(html) {
    const d = new DOMParser().parseFromString('<body>' + String(html == null ? '' : html) + '</body>', 'text/html');
    return (d.body.textContent || '').replace(/\s+/g, ' ').trim();
  }
  function saveCsv(name, header, rows) {
    const csv = '﻿' + [header].concat(rows).map(r => r.map(csvCell).join(',')).join('\r\n');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    a.download = String(name || 'export').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') + '-' + new Date().toISOString().slice(0, 10) + '.csv';
    document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  }
  function store(k, v) { try { if (v === undefined) return JSON.parse(localStorage.getItem(k) || 'null'); localStorage.setItem(k, JSON.stringify(v)); } catch (_) { return null; } return null; }
  function scoreTag(n) { if (n === null || n === undefined) return '<span class="oa-muted">—</span>'; const c = n >= 70 ? 'hi' : n >= 40 ? 'mid' : ''; return `<span class="oa-score ${c}">${esc(n)}</span>`; }
  function initials(name) { return String(name || '?').split(/[\s@.]+/).filter(Boolean).slice(0, 2).map(p => p[0]).join('').toUpperCase() || '?'; }
  const avatar = (name) => `<span class="oa-avatar" aria-hidden="true">${esc(initials(name))}</span>`;
  function userCell(name, email, tag) { return `<div class="oa-user-cell">${avatar(name || email)}<div style="min-width:0"><b class="oa-trunc">${esc(name || email || 'Unknown')}${tag ? ' <span class="oa-you">' + esc(tag) + '</span>' : ''}</b><span class="oa-small oa-muted oa-trunc" style="display:block">${esc(email || '')}</span></div></div>`; }
  function safeUrl(u) { try { const x = new URL(String(u || ''), location.origin); return /^https?:$/.test(x.protocol) ? x.href : ''; } catch (_) { return ''; } }
  function extLink(u, text) { const s = safeUrl(u); return s ? `<a class="oa-link" href="${attr(s)}" target="_blank" rel="noopener noreferrer">${esc(text || u)}</a>` : esc(text || u || '—'); }
  const can = (p) => !!(S.ctx && S.ctx.me.permissions.indexOf(p) >= 0);
  const isOwner = () => S.ctx && S.ctx.me.role === 'owner';
  const PLATFORMS = [['facebook', 'Facebook'], ['instagram', 'Instagram'], ['youtube', 'YouTube'], ['linkedin', 'LinkedIn']];
  const PLAT_NAME = Object.fromEntries(PLATFORMS);
  const plat = (p) => PLAT_NAME[String(p || '').toLowerCase()] || label(p);
  const LEAD_STATUSES = ['new', 'contacted', 'qualified', 'follow_up', 'converted', 'lost', 'disqualified', 'archived'];

  // ── Icons (stroke SVG paths) ────────────────────────────────────────
  const IC = {
    home: '<path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/>',
    building: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M9 7h2M13 7h2M9 11h2M13 11h2M9 15h2M13 15h2"/>',
    users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c0-3.5 3-5.5 6.5-5.5s6.5 2 6.5 5.5"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 14.8c2 .6 3.5 2.3 3.5 5.2"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>',
    list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    file: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/>',
    message: '<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M7 21l4-4"/>',
    chat: '<path d="M21 12a8 8 0 0 1-11.6 7.1L3 21l1.9-6.4A8 8 0 1 1 21 12z"/>',
    target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
    userCheck: '<circle cx="9" cy="8" r="4"/><path d="M2 21c0-4 3-6 7-6s7 2 7 6"/><path d="M16 11l2 2 4-4"/>',
    flow: '<rect x="3" y="4" width="6" height="6" rx="1"/><rect x="15" y="4" width="6" height="6" rx="1"/><rect x="9" y="14" width="6" height="6" rx="1"/><path d="M6 10v2h12v-2M12 12v2"/>',
    filter: '<path d="M3 4h18l-7 9v6l-4 2v-8z"/>',
    cpu: '<rect x="5" y="5" width="14" height="14" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 1v4M15 1v4M9 19v4M15 19v4M1 9h4M1 15h4M19 9h4M19 15h4"/>',
    chart: '<path d="M3 3v18h18"/><path d="M7 15v3M12 10v8M17 6v12"/>',
    download: '<path d="M12 3v12M7 10l5 5 5-5"/><path d="M5 21h14"/>',
    card: '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M2 10h20"/>',
    bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>',
    shield: '<path d="M12 2l8 4v6c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10V6z"/>',
    help: '<circle cx="12" cy="12" r="9"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3M12 17h.01"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/>',
    lock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    alert: '<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18h.01"/>',
    inbox: '<path d="M3 13l3-9h12l3 9v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M3 13h5l1 3h6l1-3h5"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    refresh: '<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    token: '<circle cx="12" cy="12" r="9"/><path d="M12 7v10M9 9.5c0-1 1.3-2 3-2s3 .8 3 2-1.3 1.8-3 2.3-3 1-3 2.2 1.3 2 3 2 3-1 3-2"/>',
    arrowLeft: '<path d="M19 12H5M12 19l-7-7 7-7"/>',
    columns: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16M15 4v16"/>',
    more: '<circle cx="5" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="19" cy="12" r="1.3"/>',
    table: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M3 15h18M9 10v10"/>',
    x: '<path d="M18 6L6 18M6 6l12 12"/>',
    chevron: '<path d="M9 6l6 6-6 6"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  };
  const ico = (n, cls) => `<svg class="${cls || 'oa-ico'}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${IC[n] || IC.info}</svg>`;

  // ── Toasts / busy / modal ───────────────────────────────────────────
  function toast(msg, type) {
    const box = $('#oa-toasts'); const el = document.createElement('div');
    el.className = 'oa-toast ' + (type || 'info');
    const span = document.createElement('span'); span.textContent = msg; el.appendChild(span);
    const b = document.createElement('button'); b.type = 'button'; b.setAttribute('aria-label', 'Dismiss'); b.textContent = '✕'; b.onclick = () => el.remove(); el.appendChild(b);
    box.appendChild(el); setTimeout(() => el.remove(), type === 'error' ? 7000 : 4500);
  }
  function busy(btn, on, text) {
    if (!btn) return;
    if (on) { btn.dataset.label = btn.innerHTML; btn.disabled = true; btn.setAttribute('aria-busy', 'true'); btn.innerHTML = '<span class="spinner" aria-hidden="true" style="display:inline-block"></span> ' + esc(text || 'Working…'); }
    else { btn.disabled = false; btn.removeAttribute('aria-busy'); if (btn.dataset.label !== undefined) { btn.innerHTML = btn.dataset.label; delete btn.dataset.label; } }
  }
  function openModal(o) {
    const root = $('#oa-modal-root'); const prev = document.activeElement;
    const back = document.createElement('div'); back.className = 'oa-modal-back';
    const id = 'm' + Math.random().toString(36).slice(2);
    back.innerHTML = `<div class="oa-modal ${o.wide ? 'wide' : ''}" role="dialog" aria-modal="true" aria-labelledby="${id}">
      <div class="oa-modal-head"><h2 id="${id}">${esc(o.title)}</h2><button type="button" class="modal-close" data-close aria-label="Close">✕</button></div>
      <div class="oa-modal-body">${o.body || ''}</div>${o.foot ? `<div class="oa-modal-foot">${o.foot}</div>` : ''}</div>`;
    root.appendChild(back);
    const close = () => { back.remove(); document.removeEventListener('keydown', onKey); if (prev && prev.focus) prev.focus(); };
    const onKey = (e) => {
      if (e.key === 'Escape') close();
      if (e.key === 'Tab') {
        const f = $$('button:not([disabled]),input:not([disabled]),select,textarea,a[href]', back).filter(x => x.offsetParent !== null);
        if (!f.length) return; const first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener('keydown', onKey);
    back.addEventListener('click', (e) => { if (e.target === back || e.target.closest('[data-close]')) close(); });
    const first = $('input,select,textarea', $('.oa-modal-body', back)) || $('.oa-modal-foot .btn-primary, .oa-modal-foot .btn-danger', back) || $('[data-close]', back);
    setTimeout(() => first && first.focus(), 30);
    if (o.onMount) o.onMount(back, close);
    return close;
  }
  function confirmDialog(title, message, opts) {
    opts = opts || {};
    return new Promise((resolve) => {
      let done = false;
      const close = openModal({
        title, body: `<p class="oa-muted" style="color:var(--text-secondary)">${esc(message)}</p>${opts.extra || ''}`,
        foot: `<button type="button" class="btn btn-secondary" data-close>Cancel</button><button type="button" class="btn ${opts.danger ? 'btn-danger' : 'btn-primary'}" data-ok>${esc(opts.confirm || 'Confirm')}</button>`,
        onMount(root, closeFn) {
          $('[data-ok]', root).onclick = () => { done = true; const extra = opts.collect ? opts.collect(root) : true; closeFn(); resolve(extra || true); };
          const obs = new MutationObserver(() => { if (!root.isConnected) { obs.disconnect(); if (!done) resolve(false); } });
          obs.observe($('#oa-modal-root'), { childList: true });
        },
      });
      void close;
    });
  }
  async function download(url, btn) {
    busy(btn, true, 'Preparing…');
    try {
      const res = await fetch(url, { credentials: 'same-origin' });
      if (!res.ok) { let d = {}; try { d = await res.json(); } catch (_) { /* */ } throw new ApiError(res.status, d); }
      const blob = await res.blob();
      const cd = res.headers.get('Content-Disposition') || '';
      const m = /filename="?([^"]+)"?/.exec(cd);
      const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = m ? m[1] : 'export.csv';
      document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 4000);
      toast(res.headers.get('X-Export-Truncated') ? 'Export downloaded (truncated to the row limit).' : 'Export downloaded.', 'success');
    } catch (e) { toast(e.message || 'Export failed', 'error'); }
    finally { busy(btn, false); }
  }

  // ── Shared view states ──────────────────────────────────────────────
  function stateHtml(kind, title, desc, actions) {
    const icon = { error: ['alert', 'danger'], denied: ['lock', 'warning'], empty: ['inbox', ''], info: ['info', ''] }[kind] || ['info', ''];
    return `<div class="oa-state">${`<div class="oa-state-icon ${icon[1]}">${ico(icon[0])}</div>`}<h3>${esc(title)}</h3>${desc ? `<p>${esc(desc)}</p>` : ''}${actions ? `<div class="oa-actions">${actions}</div>` : ''}</div>`;
  }
  function renderError(el, err, retry) {
    if (err && err.status === 403) {
      el.innerHTML = `<div class="oa-card">${stateHtml('denied', 'You don\'t have access to this', err.message || 'Your role does not include the permission required for this section. Ask the organization owner.', '<a class="btn btn-secondary" href="#dashboard">Back to dashboard</a><a class="btn btn-ghost" href="#support">Get help</a>')}</div>`;
      return;
    }
    if (err && err.status === 404) {
      el.innerHTML = `<div class="oa-card">${stateHtml('empty', 'Not found', 'This item does not exist in your organization or was removed.', '<a class="btn btn-secondary" href="#dashboard">Back to dashboard</a>')}</div>`;
      return;
    }
    el.innerHTML = `<div class="oa-card">${stateHtml('error', 'Could not load this section', err ? err.message : '', '<button type="button" class="btn btn-primary" data-retry>' + ico('refresh') + ' Retry</button>')}</div>`;
    const b = $('[data-retry]', el); if (b) b.onclick = retry;
  }
  function skeleton(el, kind) {
    const card = (h) => `<div class="oa-card"><div class="oa-skel oa-skel-line" style="width:40%"></div><div class="oa-skel" style="height:${h}px;margin-top:14px"></div></div>`;
    el.innerHTML = `<div class="oa-head"><div><div class="oa-skel" style="height:26px;width:220px"></div><div class="oa-skel oa-skel-line" style="width:320px"></div></div></div>` +
      (kind === 'table' ? card(260) : `<div class="oa-grid oa-grid-4">${card(40)}${card(40)}${card(40)}${card(40)}</div><div class="oa-grid oa-grid-2" style="margin-top:16px">${card(180)}${card(180)}</div>`);
    el.setAttribute('aria-busy', 'true');
  }
  // Page header with breadcrumbs: Home › section group › [parent] › current page.
  function crumbs(title, back) {
    const c = S.crumb || {};
    if (c.key === 'dashboard' && !back) return '';
    const items = [['#dashboard', 'Home']];
    if (c.group) items.push([c.groupHref || '', c.group]);
    if (back) items.push([back[0], back[1]]);
    else if (c.section && c.section !== title) items.push([c.sectionHref || '', c.section]);
    items.push(['', title]);
    return `<nav class="oa-crumbs" aria-label="Breadcrumb"><ol>${items.map(([h, t], i) => {
      const last = i === items.length - 1;
      return `<li>${last ? `<span aria-current="page">${esc(t)}</span>` : h ? `<a href="${attr(h)}">${esc(t)}</a>` : `<span>${esc(t)}</span>`}</li>`;
    }).join('')}</ol></nav>`;
  }
  function head(title, desc, actions, back) {
    return `${crumbs(title, back)}<div class="oa-head"><div class="oa-head-text"><h1>${esc(title)}</h1>${desc ? `<p>${esc(desc)}</p>` : ''}</div>${actions ? `<div class="oa-actions">${actions}</div>` : ''}</div>`;
  }
  // Inline validation: validates on blur, clears as the user types. Uses native constraints.
  function fieldMsg(i) {
    const v = i.validity; const lbl = ((i.labels && i.labels[0] && i.labels[0].textContent) || 'This field').replace(/\(optional\)/i, '').trim();
    if (v.valueMissing) return lbl + ' is required.';
    if (v.typeMismatch) return i.type === 'email' ? 'Enter a valid email address.' : i.type === 'url' ? 'Enter a full URL starting with https://' : 'Check this value.';
    if (v.rangeUnderflow) return 'Must be at least ' + i.min + '.';
    if (v.rangeOverflow) return 'Must be at most ' + i.max + '.';
    if (v.stepMismatch || v.badInput) return 'Enter a whole number.';
    if (v.tooLong) return 'Too long (max ' + i.maxLength + ' characters).';
    return '';
  }
  function setFieldError(form, i, msg) {
    const er = $('#e-' + (i.name || '').replace(/[^\w-]/g, ''), form);
    if (msg) i.setAttribute('aria-invalid', 'true'); else i.removeAttribute('aria-invalid');
    if (er) er.textContent = msg || '';
  }
  function liveValidate(form) {
    if (!form || form.dataset.lv) return; form.dataset.lv = '1';
    form.addEventListener('blur', (e) => {
      const i = e.target; if (!i.matches || !i.matches('input.form-input:not([readonly]):not([disabled]),textarea,select') || !i.name) return;
      if (i.value !== '' || i.required) setFieldError(form, i, fieldMsg(i));
    }, true);
    form.addEventListener('input', (e) => { const i = e.target; if (i.getAttribute && i.getAttribute('aria-invalid') === 'true' && !fieldMsg(i)) setFieldError(form, i, ''); });
  }
  function tabs(items, active) {
    return `<nav class="oa-tabs" aria-label="Section tabs">${items.map(([href, text]) => `<a href="${attr(href)}" ${href === active ? 'aria-current="page"' : ''}>${esc(text)}</a>`).join('')}</nav>`;
  }
  function kv(rows) { return `<dl class="oa-kv">${rows.filter(Boolean).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join('')}</dl>`; }
  function meter(name, used, limit, pct) {
    const p = pct !== undefined && pct !== null ? pct : (limit ? Math.min(100, used / limit * 100) : 0);
    const cls = p >= 100 ? 'danger' : p >= 80 ? 'warn' : '';
    return `<div class="oa-meter"><div class="oa-meter-top"><span>${esc(name)}</span><b>${num(used)}${limit ? ' / ' + num(limit) : ''}</b></div>
      <div class="oa-bar ${cls}" role="progressbar" aria-label="${attr(name)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(p)}"><span style="width:${Math.max(0, Math.min(100, p))}%"></span></div></div>`;
  }
  function switchRow(name, title, desc, checked, disabled) {
    return `<label class="oa-switch"><input type="checkbox" name="${attr(name)}" ${checked ? 'checked' : ''} ${disabled ? 'disabled' : ''}/><span class="oa-switch-text"><b>${esc(title)}</b>${desc ? `<span>${esc(desc)}</span>` : ''}</span></label>`;
  }
  function selectOpts(opts, value) { return opts.map(([v, t]) => `<option value="${attr(v)}" ${String(v) === String(value == null ? '' : value) ? 'selected' : ''}>${esc(t)}</option>`).join(''); }

  // ════════════════════════════════════════════════════════════════════
  // Data table — search / filters / sort / pagination (server-side, or client-side
  // with cfg.local), sticky header, column visibility, CSV export, bulk selection
  // with a floating action bar, and per-row action menus.
  //   cfg.exportUrl(params) → audited server CSV; otherwise a client CSV of the
  //   filtered rows (visible columns, formula-safe). cfg.export === false hides it.
  //   cfg.bulk = { rowId, selectable?, actions:[{id,label,danger}], run(id, rows, btn) }
  //   cfg.rowMenu(r) → [{act,label,danger}] rendered as a ⋯ menu → cfg.onAction.
  // ════════════════════════════════════════════════════════════════════
  function dataTable(host, cfg) {
    const st = Object.assign({ page: 1, limit: cfg.limit || 20 }, cfg.defaults || {}, cfg.initial || {});
    if (cfg.defaultSort && !st.sort) st.sort = cfg.defaultSort;
    const filters = cfg.filters || [];
    const tid = 't' + Math.random().toString(36).slice(2, 8);
    const cols = cfg.columns;
    cols.forEach((c, i) => { c._id = c.id || (c.label ? c.label.toLowerCase().replace(/[^a-z0-9]+/g, '_') : '_act' + i); });
    const hideable = cols.filter(c => c.label);
    const colKey = 'oa_cols:' + (cfg.key || cfg.caption || cfg.url);
    const saved = store(colKey);
    let hidden = new Set(Array.isArray(saved) ? saved : cols.filter(c => c.hidden).map(c => c._id));
    const bulk = cfg.bulk && cfg.bulk.actions && cfg.bulk.actions.length ? cfg.bulk : null;
    const selected = new Map();
    const showExport = cfg.export !== false;
    const exportLabel = cfg.exportUrl ? 'Export CSV' : 'Export';
    host.innerHTML = `<div class="oa-toolbar" data-tbwrap><form class="oa-toolbar-filters" role="search" aria-label="${attr('Filter ' + (cfg.caption || 'results').toLowerCase())}" data-tb>${filters.map(f => filterHtml(f, st, tid)).join('')}${cfg.toolbarExtra || ''}</form>
      <div class="oa-toolbar-tools">
        ${hideable.length > 1 ? `<div class="oa-rel oa-colmenu"><button type="button" class="btn btn-secondary btn-sm" data-colbtn aria-haspopup="true" aria-expanded="false" aria-controls="${tid}-cols">${ico('columns')}<span>Columns</span></button>
          <div class="oa-pop oa-colpop" id="${tid}-cols" hidden role="group" aria-label="Visible columns"><div class="oa-pop-head"><span>Show columns</span><button type="button" class="btn btn-ghost btn-sm" data-colreset>Reset</button></div>
          <div class="oa-colpop-list">${hideable.map(c => `<label class="oa-check"><input type="checkbox" data-col="${attr(c._id)}" ${hidden.has(c._id) ? '' : 'checked'}/><span>${esc(c.label)}</span></label>`).join('')}</div></div></div>` : ''}
        ${showExport ? `<button type="button" class="btn btn-secondary btn-sm" data-export title="${attr(cfg.exportUrl ? 'Download every matching row as CSV (logged)' : 'Download the filtered rows as CSV')}">${ico('download')}<span>${exportLabel}</span></button>` : ''}
      </div></div>
      <div data-body></div>${bulk ? `<div class="oa-bulkbar" data-bulkbar hidden role="region" aria-label="Bulk actions"><span class="oa-bulk-count" aria-live="polite" data-bulkcount></span><div class="oa-bulk-actions">${bulk.actions.map(a => `<button type="button" class="btn ${a.danger ? 'btn-danger' : 'btn-secondary'} btn-sm" data-bulk="${attr(a.id)}">${esc(a.label)}</button>`).join('')}</div><button type="button" class="oa-icon-btn oa-bulk-x" data-bulkclear aria-label="Clear selection">${ico('x')}</button></div>` : ''}`;
    const tb = $('[data-tb]', host), body = $('[data-body]', host);
    if (cfg.local && !cfg.local.length) $('[data-tbwrap]', host).hidden = true; // nothing to filter or export
    let timer = null, seq = 0, rows = [], total = 0, menus = [];
    tb.addEventListener('submit', (e) => e.preventDefault());
    tb.addEventListener('input', (e) => {
      const f = e.target; if (!f.name || f.dataset.noFilter !== undefined) return;
      clearTimeout(timer);
      timer = setTimeout(() => { st[f.name] = f.value; st.page = 1; load(); }, f.type === 'search' || f.type === 'text' || f.type === 'number' ? 350 : 0);
    });
    function filterHtml(f, s, t) {
      const id = t + '-' + f.name;
      const lbl = `<label class="sr-only" for="${id}">${esc(f.label)}</label>`;
      if (f.type === 'select') return `${lbl}<select class="form-select" id="${id}" name="${attr(f.name)}">${selectOpts([['', f.all || ('All ' + f.label.toLowerCase())]].concat(f.options), s[f.name])}</select>`;
      if (f.type === 'date') return `<span class="oa-datef">${lbl}<span class="oa-datef-l" aria-hidden="true">${esc(f.label.replace(/ date$/i, ''))}</span><input class="form-input" type="date" id="${id}" name="${attr(f.name)}" value="${attr(s[f.name] || '')}" title="${attr(f.label)}"/></span>`;
      if (f.type === 'number') return `${lbl}<input class="form-input oa-numf" type="number" min="0" max="100" id="${id}" name="${attr(f.name)}" value="${attr(s[f.name] || '')}" placeholder="${attr(f.label)}"/>`;
      return `${lbl}<span class="oa-searchf oa-grow">${ico('search')}<input class="form-input" type="search" id="${id}" name="${attr(f.name)}" value="${attr(s[f.name] || '')}" placeholder="${attr(f.placeholder || f.label)}" maxlength="120"/></span>`;
    }
    function params() {
      const p = {}; Object.keys(st).forEach(k => { if (st[k] !== '' && st[k] != null) p[k] = st[k]; });
      return Object.assign({}, cfg.fixed || {}, p);
    }
    function sortState(col) {
      if (cfg.local) return col.sortVal && st.sort === col._id ? (st.order === 'desc' ? 'descending' : 'ascending') : null;
      if (!col.sort) return null;
      if (cfg.orderParam) return st.sort === col.sort ? (st.order === 'desc' ? 'descending' : 'ascending') : null;
      return st.sort === col.sort ? (col.dir || 'descending') : null;
    }
    const sortable = (c) => cfg.local ? !!c.sortVal : !!c.sort;
    const cellText = (c, r) => c.csv ? c.csv(r) : htmlText(c.render ? c.render(r) : r[c.key]);
    function localData(p) {
      let list = cfg.local.slice();
      filters.forEach(f => {
        const v = p[f.name]; if (!v) return;
        if (f.type === 'select') list = list.filter(r => String(f.match ? f.match(r) : r[f.name]) === String(v));
        else { const q = String(v).toLowerCase(); list = list.filter(r => cols.some(c => c.label && cellText(c, r).toLowerCase().indexOf(q) >= 0)); }
      });
      const sc = cols.find(c => c._id === p.sort && c.sortVal);
      if (sc) { const dir = p.order === 'desc' ? -1 : 1; list.sort((a, b) => { const x = sc.sortVal(a), y = sc.sortVal(b); return (x > y ? 1 : x < y ? -1 : 0) * dir; }); }
      return { items: list.slice((p.page - 1) * p.limit, p.page * p.limit), total: list.length, all: list };
    }
    const visCols = () => cols.filter(c => !hidden.has(c._id));
    const rowKey = (r) => String(bulk.rowId(r));
    const selectable = (r) => !bulk.selectable || bulk.selectable(r);
    async function load() {
      const my = ++seq;
      const n = visCols().length + (bulk ? 1 : 0) + (cfg.rowMenu ? 1 : 0);
      if (!rows.length) {
        body.innerHTML = `<div class="oa-table-wrap"><table class="oa-table"><tbody>${Array.from({ length: 5 }).map(() => `<tr class="oa-skel-row">${Array.from({ length: n }).map(() => '<td><div class="oa-skel" style="height:12px"></div></td>').join('')}</tr>`).join('')}</tbody></table></div>`;
      } else { const w = $('.oa-table-wrap', body); if (w) w.classList.add('is-loading'); }
      body.setAttribute('aria-busy', 'true');
      let data;
      try { data = cfg.local ? localData(params()) : await api(cfg.url + qs(params())); }
      catch (e) { if (my !== seq) return; body.removeAttribute('aria-busy'); rows = []; renderError(body, e, load); return; }
      if (my !== seq || !host.isConnected) return;
      body.removeAttribute('aria-busy');
      rows = (cfg.pick ? cfg.pick(data) : data.items) || [];
      total = cfg.total ? cfg.total(data) : (data.total || 0);
      if (cfg.onData) cfg.onData(data);
      if (bulk) { selected.clear(); syncBulk(); }
      draw();
    }
    function draw() {
      const pages = Math.max(1, Math.ceil(total / st.limit));
      if (!rows.length) {
        const filtered = filters.some(f => st[f.name] && !(cfg.defaults && cfg.defaults[f.name] === st[f.name]));
        body.innerHTML = `<div class="oa-card">${filtered ? stateHtml('empty', 'No matches', 'Nothing matches these filters. Try clearing some of them.', '<button type="button" class="btn btn-secondary" data-clear>Clear filters</button>') : stateHtml('empty', (cfg.empty || {}).title || 'Nothing here yet', (cfg.empty || {}).desc || '', (cfg.empty || {}).action || '')}</div>`;
        const c = $('[data-clear]', body); if (c) c.onclick = () => { filters.forEach(f => { st[f.name] = (cfg.defaults || {})[f.name] || ''; const inp = tb.elements[f.name]; if (inp) inp.value = st[f.name]; }); st.page = 1; load(); };
        return;
      }
      const vc = visCols();
      const selAble = bulk ? rows.filter(selectable) : [];
      const allOn = bulk && selAble.length && selAble.every(r => selected.has(rowKey(r)));
      body.innerHTML = `<div class="oa-table-wrap"><table class="oa-table" aria-label="${attr(cfg.caption || 'Results')}"><thead><tr>${bulk ? `<th scope="col" class="oa-sel"><input type="checkbox" data-selall aria-label="Select all rows on this page" ${allOn ? 'checked' : ''} ${selAble.length ? '' : 'disabled'}/></th>` : ''}${vc.map(c => {
        const ss = sortState(c);
        return `<th scope="col" class="${c.num ? 'oa-num' : ''}" ${ss ? `aria-sort="${ss}"` : ''}>${sortable(c) ? `<button type="button" data-sort="${attr(cfg.local ? c._id : c.sort)}">${esc(c.label)}<span class="oa-sort-ico" aria-hidden="true"></span></button>` : (c.label ? esc(c.label) : '<span class="sr-only">Actions</span>')}</th>`;
      }).join('')}${cfg.rowMenu ? '<th scope="col" class="oa-rowact"><span class="sr-only">Actions</span></th>' : ''}</tr></thead><tbody>${rows.map((r, i) => {
        const href = cfg.rowHref ? cfg.rowHref(r) : '';
        const on = bulk && selected.has(rowKey(r));
        const menu = cfg.rowMenu ? (cfg.rowMenu(r) || []) : []; menus[i] = menu;
        return `<tr ${href ? `class="clickable${on ? ' is-selected' : ''}" tabindex="0" data-href="${attr(href)}"` : (on ? 'class="is-selected"' : '')} data-i="${i}">${bulk ? `<td class="oa-sel">${selectable(r) ? `<input type="checkbox" data-sel aria-label="${attr('Select ' + htmlText(vc[0] && vc[0].render ? vc[0].render(r) : '').slice(0, 60))}" ${on ? 'checked' : ''}/>` : ''}</td>` : ''}${vc.map(c => `<td class="${c.num ? 'oa-num' : ''} ${c.cls || ''}">${c.render ? c.render(r) : esc(r[c.key] == null ? '—' : r[c.key])}</td>`).join('')}${cfg.rowMenu ? `<td class="oa-rowact">${menu.length ? rowMenuBtn() : ''}</td>` : ''}</tr>`;
      }).join('')}</tbody></table></div>
      <div class="oa-pager"><span>${num((st.page - 1) * st.limit + 1)}–${num(Math.min(total, st.page * st.limit))} of ${num(total)}</span>
        <div class="oa-pager-btns"><label class="sr-only" for="${tid}-lim">Rows per page</label><select class="form-select" id="${tid}-lim" data-limit data-no-filter>${selectOpts([[10, '10 / page'], [20, '20 / page'], [50, '50 / page'], [100, '100 / page']], st.limit)}</select>
        <button type="button" class="btn btn-secondary btn-sm" data-prev ${st.page <= 1 ? 'disabled' : ''} aria-label="Previous page">‹ Prev</button>
        <span class="oa-small" aria-live="polite">Page ${st.page} / ${pages}</span>
        <button type="button" class="btn btn-secondary btn-sm" data-next ${st.page >= pages ? 'disabled' : ''} aria-label="Next page">Next ›</button></div></div>`;
      const sa = $('[data-selall]', body); if (sa && !allOn && selAble.some(r => selected.has(rowKey(r)))) sa.indeterminate = true;
      $('[data-prev]', body).onclick = () => { st.page--; load(); };
      $('[data-next]', body).onclick = () => { st.page++; load(); };
      $('[data-limit]', body).onchange = (e) => { st.limit = Number(e.target.value); st.page = 1; load(); };
      $$('th [data-sort]', body).forEach(b => b.onclick = () => {
        const v = b.dataset.sort;
        if (cfg.orderParam || cfg.local) { if (st.sort === v) st.order = st.order === 'desc' ? 'asc' : 'desc'; else { st.sort = v; st.order = 'asc'; } }
        else st.sort = v;
        st.page = 1; load();
      });
    }
    function rowMenuBtn() {
      return `<button type="button" class="oa-kebab" data-rowmenu aria-haspopup="menu" aria-expanded="false" aria-label="Row actions">${ico('more')}</button>`;
    }
    function syncBulk() {
      if (!bulk) return;
      const bar = $('[data-bulkbar]', host); const n = selected.size;
      bar.hidden = !n; $('[data-bulkcount]', host).innerHTML = `<b>${num(n)}</b> selected`;
    }
    body.addEventListener('change', (e) => {
      if (!bulk) return;
      if (e.target.matches('[data-selall]')) { rows.filter(selectable).forEach(r => { if (e.target.checked) selected.set(rowKey(r), r); else selected.delete(rowKey(r)); }); draw(); syncBulk(); }
      else if (e.target.matches('[data-sel]')) {
        const r = rows[Number(e.target.closest('tr').dataset.i)];
        if (e.target.checked) selected.set(rowKey(r), r); else selected.delete(rowKey(r));
        e.target.closest('tr').classList.toggle('is-selected', e.target.checked);
        const sa2 = $('[data-selall]', body); const selAble = rows.filter(selectable);
        if (sa2) { const k = selAble.filter(x => selected.has(rowKey(x))).length; sa2.checked = k === selAble.length; sa2.indeterminate = k > 0 && k < selAble.length; }
        syncBulk();
      }
    });
    if (bulk) {
      $$('[data-bulk]', host).forEach(b => b.onclick = async () => {
        const list = Array.from(selected.values()); if (!list.length) return;
        const done = await bulk.run(b.dataset.bulk, list, b);
        if (done !== false) load();
      });
      $('[data-bulkclear]', host).onclick = () => { selected.clear(); draw(); syncBulk(); };
    }
    body.addEventListener('click', (e) => {
      const km = e.target.closest('[data-rowmenu]');
      if (km) { e.stopPropagation(); const tr = km.closest('tr'); openRowMenu(km, menus[Number(tr.dataset.i)] || [], (act) => cfg.onAction && cfg.onAction(act, rows[Number(tr.dataset.i)], km)); return; }
      const act = e.target.closest('[data-act]');
      if (act) { e.stopPropagation(); const tr = act.closest('tr'); if (cfg.onAction) cfg.onAction(act.dataset.act, rows[Number(tr.dataset.i)], act); return; }
      if (e.target.closest('a,button,input,select,label,.oa-sel')) return;
      const tr = e.target.closest('tr[data-href]'); if (tr) location.hash = tr.dataset.href;
    });
    body.addEventListener('keydown', (e) => { if (e.key === 'Enter' && e.target.matches('tr[data-href]')) location.hash = e.target.dataset.href; });
    // column visibility
    const cb = $('[data-colbtn]', host);
    if (cb) {
      const pop = $('.oa-colpop', host);
      cb.onclick = (e) => { e.stopPropagation(); const open = pop.hidden; closeFloating(); pop.hidden = !open; cb.setAttribute('aria-expanded', String(open)); if (open) { const f = $('input', pop); if (f) f.focus(); } };
      pop.addEventListener('click', (e) => e.stopPropagation());
      pop.addEventListener('keydown', (e) => { if (e.key === 'Escape') { pop.hidden = true; cb.setAttribute('aria-expanded', 'false'); cb.focus(); } });
      pop.addEventListener('change', (e) => {
        const i = e.target; if (!i.dataset.col) return;
        if (!i.checked && hideable.filter(c => !hidden.has(c._id)).length <= 1) { i.checked = true; toast('At least one column must stay visible.', 'info'); return; }
        if (i.checked) hidden.delete(i.dataset.col); else hidden.add(i.dataset.col);
        store(colKey, Array.from(hidden)); if (rows.length) draw();
      });
      $('[data-colreset]', pop).onclick = () => { hidden = new Set(cols.filter(c => c.hidden).map(c => c._id)); store(colKey, Array.from(hidden)); $$('input[data-col]', pop).forEach(i => { i.checked = !hidden.has(i.dataset.col); }); if (rows.length) draw(); };
    }
    // export
    const xb = $('[data-export]', host);
    if (xb) xb.onclick = async () => {
      if (cfg.exportUrl) return download(cfg.exportUrl(params()), xb);
      busy(xb, true, 'Preparing…');
      try {
        let all;
        if (cfg.local) all = localData(Object.assign(params(), { page: 1, limit: 1e9 })).all;
        else {
          all = []; const p = Object.assign(params(), { limit: 100 });
          for (let pg = 1; pg <= 20; pg++) {
            const d = await api(cfg.url + qs(Object.assign(p, { page: pg })));
            const items = (cfg.pick ? cfg.pick(d) : d.items) || []; all = all.concat(items);
            const t = cfg.total ? cfg.total(d) : (d.total || 0);
            if (!items.length || all.length >= t) break;
          }
        }
        exportRows(all, xb);
      } catch (e) { toast(e.message || 'Export failed', 'error'); }
      finally { busy(xb, false); }
    };
    function exportRows(list, btn) {
      const vc = visCols().filter(c => c.label);
      if (!list.length) { toast('Nothing to export.', 'info'); return; }
      saveCsv(cfg.caption || 'export', vc.map(c => c.label), list.map(r => vc.map(c => cellText(c, r))));
      toast(`Exported ${num(list.length)} row${list.length === 1 ? '' : 's'}.`, 'success'); void btn;
      logClientExport(cfg.key || cfg.caption || 'table', list.length, vc.map(c => c.label), params());
    }
    load();
    return { reload: load, state: st, params, exportSelected: () => exportRows(Array.from(selected.values())), selected: () => Array.from(selected.values()), clearSelection: () => { selected.clear(); syncBulk(); } };
  }
  // Shared row action menu (fixed-position so table overflow never clips it)
  function openRowMenu(btn, items, onPick) {
    closeFloating();
    const m = document.createElement('div'); m.className = 'oa-pop oa-menu oa-rowmenu'; m.setAttribute('role', 'menu'); m.id = 'oa-rowmenu';
    m.innerHTML = items.map(it => `<button type="button" role="menuitem" class="oa-pop-item ${it.danger ? 'danger' : ''}" data-pick="${attr(it.act)}">${esc(it.label)}</button>`).join('');
    document.body.appendChild(m);
    const r = btn.getBoundingClientRect(); const w = m.offsetWidth, h = m.offsetHeight;
    m.style.left = Math.max(8, Math.min(innerWidth - w - 8, r.right - w)) + 'px';
    m.style.top = (r.bottom + h + 8 > innerHeight ? Math.max(8, r.top - h - 6) : r.bottom + 6) + 'px';
    btn.setAttribute('aria-expanded', 'true');
    const itemsEl = $$('[data-pick]', m); if (itemsEl[0]) itemsEl[0].focus();
    const close = (refocus) => { m.remove(); btn.setAttribute('aria-expanded', 'false'); window.removeEventListener('scroll', onScroll, true); if (refocus && btn.isConnected) btn.focus(); };
    const onScroll = () => close(false);
    window.addEventListener('scroll', onScroll, true);
    m._close = close;
    m.addEventListener('click', (e) => { e.stopPropagation(); const b = e.target.closest('[data-pick]'); if (b) { close(false); onPick(b.dataset.pick); } });
    m.addEventListener('keydown', (e) => {
      const i = itemsEl.indexOf(document.activeElement);
      if (e.key === 'ArrowDown') { e.preventDefault(); itemsEl[(i + 1) % itemsEl.length].focus(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); itemsEl[(i - 1 + itemsEl.length) % itemsEl.length].focus(); }
      else if (e.key === 'Escape' || e.key === 'Tab') { e.preventDefault(); close(true); }
    });
  }
  function closeFloating() {
    const m = $('#oa-rowmenu'); if (m && m._close) m._close(false);
    $$('.oa-colpop').forEach(p => { if (!p.hidden) { p.hidden = true; const b = p.parentElement && $('[data-colbtn]', p.parentElement); if (b) b.setAttribute('aria-expanded', 'false'); } });
  }
  // Client-side (in-browser) CSV exports are recorded + audited server side.
  // Fire-and-forget: never blocks or fails the download.
  function logClientExport(table, rows, columns, filters) {
    if (!can('exports.create')) return;
    const f = {}; Object.keys(filters || {}).forEach(k => { if (['page', 'limit'].indexOf(k) < 0 && filters[k] !== '' && filters[k] != null) f[k] = filters[k]; });
    try { api('/api/org-admin/exports/client-log', { method: 'POST', body: { table: String(table).slice(0, 80), rows, columns: (columns || []).slice(0, 100), filters: f }, noRedirect: true, noGate: true }).catch(() => {}); } catch (_) { /* never block the export */ }
  }
  // Bulk actions: one atomic request (all ids validated server side, one audit
  // entry). Reports updated + skipped-with-reason. Returns the updated count.
  async function runBulk(url, body, verb, noun) {
    noun = noun || 'item';
    const plural = (n) => `${num(n)} ${noun}${n === 1 ? '' : 's'}`;
    try {
      const r = await api(url, { method: 'POST', body });
      const skipped = r.skipped || [];
      if (!skipped.length) toast(`${verb} ${plural(r.updated || 0)}.`, 'success');
      else toast(`${verb} ${plural(r.updated || 0)}. ${num(skipped.length)} skipped: ${skipped[0].reason}${skipped.length > 1 ? ' (and others)' : ''}`, r.updated ? 'warning' : 'info');
      return r.updated || 0;
    } catch (e) { toast(e.message || 'Bulk action failed', 'error'); return 0; }
  }

  // ════════════════════════════════════════════════════════════════════
  // Charts — single-series column chart (y gridlines + labels, hover AND keyboard
  // tooltips, "Table" toggle as the accessible fallback) and horizontal bars.
  // One hue (--primary) everywhere; values carry their own labels.
  // ════════════════════════════════════════════════════════════════════
  function niceMax(v) {
    if (v <= 4) return 4;
    const p = Math.pow(10, Math.floor(Math.log10(v))); const n = v / p;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
  }
  const compact = (n) => { try { return new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(n); } catch (_) { return num(n); } };
  function columnChart(title, series, days, fmt, opts) {
    opts = opts || {};
    const vals = days.map(d => series[d] || 0);
    const peak = Math.max(0, ...vals);
    const max = niceMax(peak || 1);
    const W = 600, H = 160, n = vals.length, gap = n > 60 ? 1 : n > 20 ? 2 : 4, bw = Math.max(1, (W - gap * (n - 1)) / n);
    const bars = vals.map((v, i) => {
      const h = v ? Math.max(2, v / max * H) : 0; const x = i * (bw + gap);
      return `<rect class="hit" x="${(x - gap / 2).toFixed(1)}" y="0" width="${(bw + gap).toFixed(1)}" height="${H}" fill="transparent" data-i="${i}"/><rect class="bar" data-b="${i}" x="${x.toFixed(1)}" y="${(H - h).toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" rx="${Math.min(3, bw / 2).toFixed(1)}" pointer-events="none"/>`;
    }).join('');
    const grid = [0.5, 1].map(f => `<line class="grid" x1="0" x2="${W}" y1="${(H - f * H + 0.5).toFixed(1)}" y2="${(H - f * H + 0.5).toFixed(1)}" vector-effect="non-scaling-stroke"/>`).join('');
    const total = vals.reduce((a, b) => a + b, 0);
    const id = 'c' + Math.random().toString(36).slice(2, 8);
    const f = fmt || num;
    const mid = days.length > 2 ? days[Math.floor((days.length - 1) / 2)] : '';
    return `<div class="oa-card oa-chart-card"><div class="oa-card-head"><div><div class="oa-card-title">${esc(title)}</div><div class="oa-card-sub">Total <b>${esc(f(total))}</b> · peak ${esc(f(peak))}/day</div></div>
      <button type="button" class="btn btn-ghost btn-sm" data-chart-table="${id}" aria-expanded="false" aria-controls="${id}-t">${ico('table')}<span>Table</span></button></div>
      <div class="oa-chart-wrap" data-chart="${id}">
        <div class="oa-chart-y" aria-hidden="true"><span style="top:0">${esc(compact(max))}</span><span style="top:50%">${esc(compact(max / 2))}</span><span style="top:100%">0</span></div>
        <div class="oa-chart-plot" tabindex="0" role="img" aria-label="${attr(`${title}: ${days.length} days, total ${f(total)}, peak ${f(peak)} per day. Use the left and right arrow keys to read each day.`)}">
          <svg class="oa-chart ${opts.small ? 'sm' : ''}" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true" focusable="false">${grid}${bars}<line class="axis" x1="0" y1="${H - 0.5}" x2="${W}" y2="${H - 0.5}" vector-effect="non-scaling-stroke"/></svg>
          <div class="oa-tip" hidden aria-hidden="true"></div>
        </div><div class="sr-only" aria-live="polite" data-live></div>
        <div class="oa-chart-x" aria-hidden="true"><span>${esc(fmtDate(days[0]))}</span>${mid ? `<span>${esc(fmtDate(mid))}</span>` : ''}<span>${esc(fmtDate(days[days.length - 1]))}</span></div>
      </div>
      <div class="oa-chart-table" id="${id}-t" hidden><div class="oa-table-wrap"><table class="oa-table"><caption class="sr-only">${esc(title)}</caption><thead><tr><th scope="col">Date</th><th scope="col" class="oa-num">Value</th></tr></thead><tbody>${days.map((d, i) => `<tr><th scope="row" style="font-weight:500">${esc(fmtDate(d))}</th><td class="oa-num">${esc(f(vals[i]))}</td></tr>`).join('')}</tbody></table></div></div>
      <script type="application/json" data-series="${id}">${JSON.stringify(days.map((d, i) => [d, vals[i]])).replace(/</g, '\\u003c')}</script></div>`;
  }
  function wireCharts(root, fmt) {
    $$('[data-chart]', root).forEach(w => {
      const data = JSON.parse(($(`[data-series="${w.dataset.chart}"]`, root) || {}).textContent || '[]');
      const plot = $('.oa-chart-plot', w), tip = $('.oa-tip', w), svg = $('svg', w);
      let cur = -1;
      const show = (i, x) => {
        const d = data[i]; if (!d) return;
        $$('.bar.on', svg).forEach(b => b.classList.remove('on'));
        const bar = $(`.bar[data-b="${i}"]`, svg); if (bar) bar.classList.add('on');
        const r = plot.getBoundingClientRect();
        const px = x != null ? x : ((i + 0.5) / data.length) * r.width;
        tip.hidden = false; tip.innerHTML = `<b>${esc((fmt || num)(d[1]))}</b><span>${esc(fmtDate(d[0]))}</span>`;
        if (x == null) { const lv = $('[data-live]', w); if (lv) lv.textContent = fmtDate(d[0]) + ': ' + (fmt || num)(d[1]); }
        tip.style.left = Math.min(r.width - 50, Math.max(50, px)) + 'px';
        cur = i;
      };
      const hide = () => { tip.hidden = true; $$('.bar.on', svg).forEach(b => b.classList.remove('on')); };
      svg.addEventListener('mousemove', (e) => { const t = e.target.closest('.hit'); if (!t) { hide(); return; } show(Number(t.dataset.i), e.clientX - plot.getBoundingClientRect().left); });
      svg.addEventListener('mouseleave', hide);
      plot.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { e.preventDefault(); show(Math.max(0, Math.min(data.length - 1, cur < 0 ? data.length - 1 : cur + (e.key === 'ArrowRight' ? 1 : -1)))); }
        else if (e.key === 'Home') { e.preventDefault(); show(0); } else if (e.key === 'End') { e.preventDefault(); show(data.length - 1); }
        else if (e.key === 'Escape') hide();
      });
      plot.addEventListener('blur', hide);
    });
    $$('[data-chart-table]', root).forEach(b => b.onclick = () => {
      const t = $('#' + b.dataset.chartTable + '-t', root); const w = $(`[data-chart="${b.dataset.chartTable}"]`, root);
      const open = t.hidden; t.hidden = !open; w.hidden = open; b.setAttribute('aria-expanded', String(open));
      $('span', b).textContent = open ? 'Chart' : 'Table';
    });
  }
  function hbars(title, obj, opts) {
    opts = opts || {};
    const entries = Object.entries(obj || {}).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).slice(0, opts.max || 8);
    const max = Math.max(1, ...entries.map(e => e[1]));
    const sum = entries.reduce((a, e) => a + e[1], 0);
    return `<div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">${esc(title)}</div>${entries.length ? `<span class="oa-card-sub">${num(sum)} total</span>` : ''}</div>${entries.length ? `<ul class="oa-hbars" aria-label="${attr(title)}">${entries.map(([k, v]) => {
      const name = label(k || (opts.emptyKey || 'none')); const pct = sum ? Math.round(v / sum * 100) : 0;
      return `<li class="oa-hbar" title="${attr(name + ': ' + num(v) + ' (' + pct + '%)')}"><span class="oa-hbar-label">${esc(name)}</span><span class="oa-hbar-track" aria-hidden="true"><span style="width:${(v / max * 100).toFixed(1)}%"></span></span><span class="oa-hbar-val">${num(v)}<span class="sr-only"> (${pct}%)</span></span></li>`;
    }).join('')}</ul>` : `<div class="oa-mini-empty">${ico('chart')}<span>No data for this period.</span></div>`}</div>`;
  }

  // ════════════════════════════════════════════════════════════════════
  // Navigation / router
  // ════════════════════════════════════════════════════════════════════
  const NAV = [
    { group: 'Overview', items: [['dashboard', 'Dashboard', 'home']] },
    { group: 'Organization', items: [['organization', 'Organization', 'building'], ['team', 'Team', 'users']] },
    { group: 'LeadAI', items: [['search', 'URL Search', 'search'], ['searches', 'Searches', 'list'], ['pages', 'Pages', 'file'], ['posts', 'Posts', 'message'], ['comments', 'Comments', 'chat']] },
    { group: 'Lead management', items: [['leads', 'All leads', 'target'], ['leads/assigned', 'Assigned leads', 'userCheck'], ['pipeline', 'Lifecycle', 'flow'], ['rules', 'Lead rules', 'filter']] },
    { group: 'Operations', items: [['apify', 'Apify jobs', 'cpu'], ['analytics', 'Analytics & reports', 'chart'], ['exports', 'Exports', 'download']] },
    { group: 'Account', items: [['subscription', 'Subscription', 'card'], ['notifications', 'Notifications', 'bell'], ['audit', 'Audit logs', 'shield'], ['support', 'Support', 'help'], ['profile', 'Profile & security', 'user']] },
  ];
  const TITLES = {};
  NAV.forEach(g => g.items.forEach(([k, t]) => { TITLES[k] = t; }));
  function renderNav() {
    $('#oa-nav').innerHTML = NAV.map(g => `<div class="oa-nav-group"><div class="oa-nav-label">${esc(g.group)}</div>${g.items.map(([k, t, i]) =>
      `<a href="#${attr(k)}" data-nav="${attr(k)}">${ico(i)}<span>${esc(t)}</span>${k === 'notifications' ? '<span class="oa-count" data-nav-count hidden></span>' : ''}</a>`).join('')}</div>`).join('');
  }
  function setActiveNav(key) {
    $$('#oa-nav a').forEach(a => { if (a.dataset.nav === key) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
  }
  function parseHash() {
    const raw = decodeURIComponent((location.hash || '#dashboard').slice(1));
    const [path, query] = raw.split('?');
    const parts = path.split('/').filter(Boolean);
    return { parts: parts.length ? parts : ['dashboard'], query: Object.fromEntries(new URLSearchParams(query || '')) };
  }
  let routeSeq = 0;
  const ROUTES = {};
  async function route() {
    const { parts, query } = parseHash();
    const my = ++routeSeq;
    const main = $('#oa-content');
    const r = ROUTES[parts[0]];
    closeSidebar(); closePops(); closeFloating();
    const navKey = r ? (r.nav ? r.nav(parts) : parts[0]) : '';
    setActiveNav(navKey);
    const grp = NAV.find(g => g.items.some(([k]) => k === navKey));
    S.crumb = { key: parts[0], group: grp && grp.group !== 'Overview' ? grp.group : '', section: '', sectionHref: '' };
    const title = r ? (r.title ? r.title(parts) : TITLES[parts[0]]) : 'Page not found';
    $('#oa-crumb-title').textContent = title || 'Admin';
    document.title = (title || 'Admin') + ' · ' + (S.ctx ? S.ctx.organization.name : 'LeadAI') + ' Admin';
    const view = { el: main, parts, query, alive: () => my === routeSeq };
    if (!r) { main.innerHTML = notFound(); main.focus(); return; }
    skeleton(main, r.skel || 'cards');
    try { await r.render(view); }
    catch (e) { if (view.alive()) renderError(main, e, route); }
    finally { if (view.alive()) { main.removeAttribute('aria-busy'); $$('.oa-tabs', main).forEach(n => { const cur = $('[aria-current="page"]', n); if (cur && n.scrollWidth > n.clientWidth) n.scrollLeft = Math.max(0, cur.offsetLeft - n.offsetLeft - 24); }); } }
    if (view.alive() && !parts._keepFocus) { main.focus({ preventScroll: true }); window.scrollTo(0, 0); }
  }
  function notFound() {
    return `<div class="oa-card" style="max-width:640px;margin:40px auto"><div class="oa-state"><div class="error-code">404</div><h2>This page doesn't exist</h2><p>The link may be broken, or the section was moved. Use the menu to find what you need.</p><div class="oa-actions"><a class="btn btn-primary" href="#dashboard">Go to dashboard</a><a class="btn btn-secondary" href="#support">Get help</a></div></div></div>`;
  }

  // ════════════════════════════════════════════════════════════════════
  // Cached lookups
  // ════════════════════════════════════════════════════════════════════
  async function getOrg(force) {
    if (!S.org || force) S.org = (await api('/api/organizations/current')).organization;
    return S.org;
  }
  async function getMembers(force) {
    if (!S.members || force) {
      try { S.members = (await api('/api/org-admin/users?limit=100&sort=name')).items; } catch (_) { S.members = []; }
    }
    return S.members;
  }
  async function getTransitions() {
    if (!S.transitions) { try { S.transitions = (await api('/api/org-admin/leads/pipeline')).transitions; } catch (_) { S.transitions = {}; } }
    return S.transitions;
  }
  const memberOptions = (list, activeOnly) => (list || []).filter(m => !activeOnly || m.status === 'active').map(m => [m.user_id, (m.name || m.email) + (m.name ? ' · ' + m.email : '')]);

  // ════════════════════════════════════════════════════════════════════
  // Views
  // ════════════════════════════════════════════════════════════════════

  // ── Dashboard ──────────────────────────────────────────────────────
  ROUTES.dashboard = {
    async render(v) {
      const d = await api('/api/org-admin/overview');
      if (!v.alive()) return;
      const sub = d.subscription || {};
      const planStatus = sub.has_subscription ? sub.status : (sub.is_demo || (d.plan && d.plan.is_demo) ? 'demo' : 'none');
      const tok = d.tokens;
      const metrics = (d.usage && d.usage.metrics) || {};
      const meterNames = { monthly_searches: 'Searches', team_members: 'Users', monthly_exports: 'Exports', monthly_ai_analyses: 'AI analyses', monthly_posts: 'Posts', monthly_comments: 'Comments' };
      const alertHtml = (d.alerts || []).map(a => `<div class="alert alert-${attr(a.severity === 'danger' ? 'danger' : a.severity === 'info' ? 'info' : 'warning')}">${ico('alert', 'alert-icon')}<div class="alert-body">${esc(a.message)}</div></div>`).join('');
      const stat = (href, icon, lbl, value, meta) => `<a class="oa-card oa-stat" href="${attr(href)}"><span class="oa-stat-label">${ico(icon)}${esc(lbl)}</span><span class="oa-stat-value">${num(value)}</span><span class="oa-stat-meta">${meta}</span></a>`;
      const miniRuns = (list, empty) => list.length ? `<ul class="oa-feed">${list.map(r => `<li class="${r.status === 'error' || r.status === 'failed' ? 'fail' : ''}"><div class="oa-feed-main"><a class="oa-link" href="#searches/${attr(r.run_id)}">${esc(r.url || r.run_id)}</a><div class="oa-small oa-muted">${esc(r.user_email || '')} · ${pill(r.status)} ${r.error ? '· ' + esc(String(r.error).slice(0, 90)) : ''}</div></div>${timeTag(r.created_at)}</li>`).join('')}</ul>` : `<p class="oa-muted oa-small">${esc(empty)}</p>`;
      v.el.innerHTML = head('Dashboard', `Everything happening in ${S.ctx.organization.name}.`, `<a class="btn btn-primary" href="#search">${ico('search')} New search</a>`) +
        (alertHtml ? `<div class="oa-alerts" role="region" aria-label="Usage alerts">${alertHtml}</div>` : '') +
        `<div class="oa-grid oa-grid-4">
          ${stat('#team/users', 'users', 'Users', d.users.total, `<span>${num(d.users.active)} active</span><span>${num(d.users.inactive)} inactive</span><span>${num(d.users.pending_invitations)} invited</span>`)}
          ${stat('#searches', 'search', 'Searches', d.searches.total, `<span>${num(d.searches.this_month)} this month</span><span>${num(d.searches.failed)} failed</span>`)}
          ${stat('#leads', 'target', 'Leads', d.leads.total, `<span>${num(d.leads.this_month)} this month</span><span>${num(d.leads.unassigned)} unassigned</span>`)}
          ${stat('#apify', 'cpu', 'Apify jobs', d.searches.total, `<span>${num(d.searches.running)} running</span><span>${num(d.searches.completed)} completed</span><span>${num(d.searches.failed)} failed</span>`)}
        </div>
        <div class="oa-grid oa-grid-2" data-trend aria-busy="true">${['Searches', 'Leads'].map(() => '<div class="oa-card"><div class="oa-skel oa-skel-line" style="width:40%"></div><div class="oa-skel" style="height:190px;margin-top:14px"></div></div>').join('')}</div>
        <div class="oa-grid oa-grid-3">
          <div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">Token usage</div>${ico('token')}</div>${tok ? `${meter('Tokens used', tok.used, tok.allocated, tok.percentage)}<p class="oa-small oa-muted" style="margin-top:10px"><b style="color:var(--text-primary)">${num(tok.remaining)}</b> tokens remaining${tok.expires_at ? ' · expire ' + esc(fmtDate(tok.expires_at)) : ''}</p>` : '<p class="oa-muted oa-small">Your plan is not token-metered.</p>'}</div>
          <div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">Subscription</div><a class="oa-link oa-small" href="#subscription">Manage</a></div>
            <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap"><span style="font-size:1.25rem;font-weight:800">${esc((d.plan && d.plan.name) || 'No plan')}</span>${pill(planStatus, planStatus === 'none' ? 'No subscription' : planStatus === 'active' ? 'Active' : label(planStatus))}</div>
            <p class="oa-small oa-muted" style="margin-top:8px">${sub.current_period_end ? (sub.cancel_at_period_end ? 'Ends ' : 'Renews ') + esc(fmtDate(sub.current_period_end)) : (d.plan && d.plan.is_demo ? 'Demo plan' : 'No renewal date')}</p>
            ${d.pending_subscription ? '<p class="oa-small" style="margin-top:6px">' + pill('pending', 'Plan change pending confirmation') + '</p>' : ''}</div>
          <div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">Usage this period</div><a class="oa-link oa-small" href="#analytics">Details</a></div>
            ${Object.keys(meterNames).filter(k => metrics[k] && metrics[k].limit).slice(0, 3).map(k => meter(meterNames[k], metrics[k].used, metrics[k].limit, metrics[k].percentage)).join('') || '<p class="oa-muted oa-small">No metered limits on your plan.</p>'}</div>
        </div>
        <div class="oa-grid oa-grid-2">
          <div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">Recent searches</div><a class="oa-link oa-small" href="#searches">View all</a></div>${miniRuns(d.recent_searches || [], 'No searches yet — start one from URL Search.')}</div>
          <div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">Recent leads</div><a class="oa-link oa-small" href="#leads">View all</a></div>${(d.recent_leads || []).length ? `<ul class="oa-feed">${d.recent_leads.map(l => `<li><div class="oa-feed-main"><a class="oa-link" href="#lead/${attr(l.id)}">${esc(l.name)}</a> ${scoreTag(l.score)}<div class="oa-small oa-muted oa-trunc">${esc(l.text)}</div></div>${timeTag(l.created_at)}</li>`).join('')}</ul>` : '<p class="oa-muted oa-small">No leads yet.</p>'}</div>
        </div>
        <div class="oa-grid oa-grid-2">
          <div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">Recent users</div><a class="oa-link oa-small" href="#team/users">Manage team</a></div>${(d.recent_users || []).length ? `<ul class="oa-feed">${d.recent_users.map(u => `<li><div class="oa-feed-main"><a class="oa-link" href="#team/user/${attr(u.user_id)}">${esc(u.name || u.email || u.user_id)}</a> ${roleTag(u.role)} ${pill(u.status)}</div>${timeTag(u.joined_at)}</li>`).join('')}</ul>` : '<p class="oa-muted oa-small">No users yet.</p>'}</div>
          <div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">Failed searches</div><a class="oa-link oa-small" href="#searches?status=error">View all</a></div>${miniRuns(d.failed_searches || [], 'No failed searches — everything ran cleanly.')}</div>
        </div>
        ${can('org_audit.view') ? `<div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">Recent organization activity</div><a class="oa-link oa-small" href="#audit">Audit logs</a></div>${(d.activity || []).length ? `<ul class="oa-feed">${d.activity.map(a => `<li class="${a.status === 'failure' ? 'fail' : ''}"><div class="oa-feed-main"><b>${esc(a.action)}</b> <span class="oa-muted oa-small">by ${esc(a.actor || 'system')}</span></div>${timeTag(a.at)}</li>`).join('')}</ul>` : '<p class="oa-muted oa-small">No activity recorded yet.</p>'}</div>` : ''}`;
      loadTrend(v);
    },
  };
  // Dashboard trend: last 14 days of searches and leads (best effort, never blocks the page).
  async function loadTrend(v) {
    const box = $('[data-trend]', v.el); if (!box) return;
    const iso = (x) => x.toISOString().slice(0, 10); const to = new Date(); const from = new Date(); from.setDate(from.getDate() - 13);
    try {
      const a = await api('/api/org-admin/analytics' + qs({ from: iso(from), to: iso(to) }));
      if (!v.alive() || !box.isConnected) return;
      box.innerHTML = columnChart('Searches · last 14 days', a.series.searches, a.days) + columnChart('Leads · last 14 days', a.series.leads, a.days);
      wireCharts(box);
    } catch (e) {
      if (!v.alive() || !box.isConnected) return;
      if (e.status === 403) { box.remove(); return; }
      box.innerHTML = `<div class="oa-card" style="grid-column:1/-1">${stateHtml('error', 'Trends unavailable', e.message, '<button type="button" class="btn btn-secondary btn-sm" data-retry>' + ico('refresh') + ' Retry</button>')}</div>`;
      $('[data-retry]', box).onclick = () => { box.innerHTML = '<div class="oa-card" style="grid-column:1/-1"><div class="oa-skel" style="height:200px"></div></div>'; loadTrend(v); };
    } finally { box.removeAttribute('aria-busy'); }
  }

  // ── Organization ───────────────────────────────────────────────────
  ROUTES.organization = {
    title: (p) => 'Organization · ' + ({ branding: 'Branding', settings: 'Settings' }[p[1]] || 'Profile'),
    async render(v) {
      const tab = ['profile', 'branding', 'settings'].indexOf(v.parts[1]) >= 0 ? v.parts[1] : 'profile';
      const org = await getOrg(true);
      if (!v.alive()) return;
      const editable = can('settings.manage');
      const s = org.settings || {};
      const b = org.branding || {};
      const ro = editable ? '' : 'disabled';
      let body = '';
      if (tab === 'profile') {
        let tzs = []; try { tzs = Intl.supportedValuesOf('timeZone'); } catch (_) { tzs = ['UTC']; }
        if (org.timezone && tzs.indexOf(org.timezone) < 0) tzs.unshift(org.timezone);
        body = `<form class="oa-card oa-form" data-form novalidate><div class="oa-form-grid">
          ${fld('name', 'Organization name', org.name, 'text', ro, 'required maxlength="120"')}
          ${fld('industry', 'Industry', org.industry, 'text', ro, 'maxlength="120" placeholder="e.g. Real estate"')}
          ${fld('website', 'Website', org.website, 'url', ro, 'placeholder="https://example.com"')}
          ${fld('logo_url', 'Logo URL', org.logo_url, 'url', ro, 'placeholder="https://…/logo.png"')}
          ${fld('contact_email', 'Contact email', org.contact_email, 'email', ro, '')}
          ${fld('contact_phone', 'Contact phone', org.contact_phone, 'tel', ro, 'maxlength="40"')}
          ${fld('country', 'Country', org.country, 'text', ro, 'maxlength="80"')}
          <div class="oa-field"><label for="f-timezone">Timezone</label><select class="form-select" id="f-timezone" name="timezone" ${ro}>${selectOpts([['', 'Select timezone']].concat(tzs.map(t => [t, t])), org.timezone || '')}</select></div>
          <div class="oa-field span-2"><label for="f-description">Description</label><textarea class="form-textarea" id="f-description" name="description" maxlength="500" rows="3" ${ro}>${esc(org.description || '')}</textarea><span class="oa-hint">Shown to your team. Max 500 characters.</span></div>
        </div>${editable ? '<div class="oa-form-foot"><button type="submit" class="btn btn-primary">Save profile</button></div>' : readonlyNote()}</form>`;
      } else if (tab === 'branding') {
        body = `<div class="oa-note">${ico('info')}<span>This branding applies inside your organization's workspace. The public LeadAI website and global branding are managed by LeadAI.</span></div>
        <form class="oa-card oa-form" data-form novalidate><div class="oa-form-grid">
          ${fld('company_name', 'Company display name', b.company_name || org.name, 'text', ro, 'maxlength="120"')}
          ${fld('logo_url', 'Organization logo URL', org.logo_url, 'url', ro, 'placeholder="https://…/logo.png"')}
          ${colorFld('primary_color', 'Primary colour', b.primary_color || '#8b5cf6', ro)}
          ${colorFld('accent_color', 'Accent colour', b.accent_color || '#a78bfa', ro)}
          <div class="oa-field span-2"><span class="oa-label">Preview</span><div class="oa-brand-preview" data-preview></div></div>
        </div>${editable ? '<div class="oa-form-foot"><button type="submit" class="btn btn-primary">Save branding</button></div>' : readonlyNote()}</form>`;
      } else {
        const caps = S.ctx.caps || {};
        const capPosts = [caps.posts_per_search, caps.platform_max_posts].filter(Boolean);
        const capComments = [caps.comments_per_post, caps.platform_max_comments_per_post].filter(Boolean);
        const maxPosts = capPosts.length ? Math.min(...capPosts) : '';
        const maxComments = capComments.length ? Math.min(...capComments) : '';
        body = `<form class="oa-form" data-form novalidate>
          <div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">Search defaults</div><span class="oa-card-sub">Within your plan limits</span></div><div class="oa-form-grid">
            ${fld('default_posts_per_search', 'Posts per search', s.default_posts_per_search || '', 'number', ro, `min="1" ${maxPosts ? 'max="' + maxPosts + '"' : ''} placeholder="${attr(maxPosts ? Math.min(maxPosts, caps.default_posts || maxPosts) : (caps.default_posts || ''))}"`, maxPosts ? 'Plan maximum: ' + maxPosts : '')}
            ${fld('default_comments_per_post', 'Comments per post', s.default_comments_per_post || '', 'number', ro, `min="1" ${maxComments ? 'max="' + maxComments + '"' : ''} placeholder="${attr(maxComments ? Math.min(maxComments, caps.default_comments_per_post || maxComments) : (caps.default_comments_per_post || ''))}"`, maxComments ? 'Plan maximum: ' + maxComments : '')}
          </div></div>
          <div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">Lead settings</div><a class="oa-link oa-small" href="#rules">Lead keywords →</a></div><div class="oa-form-grid">
            ${fld('min_lead_score', 'Minimum lead score', s.min_lead_score == null ? '' : s.min_lead_score, 'number', ro, 'min="0" max="100" placeholder="0"', 'Leads below this score are de-emphasised.')}
            <div class="oa-field"><label for="f-lead_assignment">Lead assignment</label><select class="form-select" id="f-lead_assignment" name="lead_assignment" ${ro}>${selectOpts([['manual', 'Manual (Admin assigns)'], ['creator', 'Whoever ran the search'], ['round_robin', 'Round robin']], s.lead_assignment === 'search_owner' ? 'creator' : (s.lead_assignment || 'manual'))}</select><span class="oa-hint">Applies to new leads. Round robin rotates across active members who can view leads (viewers are skipped).</span></div>
          </div>${switchRow('auto_export', 'Auto-export leads', 'Prepare a CSV automatically when a search completes.', s.auto_export, !editable)}</div>
          <div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">Notifications</div></div>
            ${switchRow('email_notifications', 'Email notifications', 'Send organization alerts by email as well as in-app.', s.email_notifications !== false, !editable)}
            ${switchRow('notify_on_leads', 'New leads', 'Notify admins when searches find new leads.', s.notify_on_leads, !editable)}
            ${switchRow('notify_job_completion', 'Job completion', 'Notify when a search or scrape job finishes or fails.', s.notify_job_completion !== false, !editable)}
            ${switchRow('notify_lead_assigned', 'Lead assigned', 'Notify members when a lead is assigned to them.', s.notify_lead_assigned !== false, !editable)}
            ${switchRow('notify_usage_warnings', 'Usage warnings', 'Warn before plan limits or tokens run out.', s.notify_usage_warnings !== false, !editable)}
            <div class="oa-form-grid" style="margin-top:10px">${fld('usage_warning_percent', 'Warn at usage (%)', s.usage_warning_percent || 80, 'number', ro, 'min="1" max="100"')}</div></div>
          <div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">Team & invitations</div></div><div class="oa-form-grid">
            ${fld('invite_expiry_days', 'Invitation expiry (days)', s.invite_expiry_days || 7, 'number', ro, 'min="1" max="30"')}
            <div class="oa-field"><label for="f-default_member_role">Default role for new members</label><select class="form-select" id="f-default_member_role" name="default_member_role" ${ro}>${selectOpts([['member', 'User'], ['manager', 'Manager'], ['viewer', 'Viewer']], s.default_member_role || 'member')}</select></div>
          </div></div>
          <div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">Data visibility</div></div>
            ${switchRow('shared_workspace', 'Shared workspace', 'When ON, every member of your organization can see ALL searches, pages, posts, comments and leads — not only their own or those assigned to them. Leave OFF to keep each member\'s data private (Admins always see everything).', s.shared_workspace, !editable)}
            ${s.shared_workspace ? '<div class="alert alert-warning" style="margin-top:8px">' + ico('alert', 'alert-icon') + '<div class="alert-body">Shared workspace is ON — all members currently see all organization data.</div></div>' : ''}
          </div>
          ${editable ? '<div class="oa-form-foot" style="border:0"><button type="submit" class="btn btn-primary">Save settings</button></div>' : readonlyNote()}
        </form>`;
      }
      v.el.innerHTML = head('Organization', 'Your organization profile, workspace branding and settings.') +
        tabs([['#organization/profile', 'Profile'], ['#organization/branding', 'Branding'], ['#organization/settings', 'Settings']], '#organization/' + tab) + body;
      const form = $('[data-form]', v.el);
      liveValidate(form);
      if (tab === 'branding') {
        const prev = $('[data-preview]', v.el);
        const upd = () => {
          const f = form.elements; const logo = safeUrl(f.logo_url.value);
          prev.innerHTML = `${logo ? `<img src="${attr(logo)}" alt="" onerror="this.remove()"/>` : `<span class="brand-mark" aria-hidden="true" style="width:48px;height:48px;border-radius:12px;background:${attr(f.primary_color.value)}"></span>`}<div><b style="font-size:1.1rem">${esc(f.company_name.value || org.name)}</b><div class="oa-small" style="margin-top:6px;display:flex;gap:8px"><span class="btn btn-sm" style="background:${attr(f.primary_color.value)};color:#fff">Primary</span><span class="btn btn-sm" style="background:${attr(f.accent_color.value)};color:#fff">Accent</span></div></div>`;
        };
        $$('input[type=color]', form).forEach(c => c.addEventListener('input', () => { $('input[name="' + c.dataset.for + '"]', form).value = c.value; upd(); }));
        $$('input[data-hex]', form).forEach(t => t.addEventListener('input', () => { if (/^#[0-9a-f]{6}$/i.test(t.value)) { $('input[data-for="' + t.name + '"]', form).value = t.value; } upd(); }));
        form.addEventListener('input', upd); upd();
      }
      if (!editable) return;
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const btn = $('button[type=submit]', form);
        const f = form.elements; let payload = {};
        $$('.oa-err', form).forEach(x => { x.textContent = ''; });
        const bad = (name, msg) => { const er = $('#e-' + name, form); if (er) er.textContent = msg; const i = f[name]; if (i) { i.setAttribute('aria-invalid', 'true'); i.focus(); } return false; };
        if (tab === 'profile') {
          if (!f.name.value.trim()) return bad('name', 'Organization name is required.');
          for (const u of ['website', 'logo_url']) if (f[u].value.trim() && !safeUrl(f[u].value.trim())) return bad(u, 'Enter a full http(s):// URL.');
          if (f.contact_email.value.trim() && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(f.contact_email.value.trim())) return bad('contact_email', 'Enter a valid email.');
          ['name', 'industry', 'website', 'logo_url', 'contact_email', 'contact_phone', 'country', 'timezone', 'description'].forEach(k => { payload[k] = f[k].value.trim(); });
        } else if (tab === 'branding') {
          if (f.logo_url.value.trim() && !safeUrl(f.logo_url.value.trim())) return bad('logo_url', 'Enter a full http(s):// URL.');
          for (const c of ['primary_color', 'accent_color']) if (!/^#[0-9a-f]{6}$/i.test(f[c].value.trim())) return bad(c, 'Use a #RRGGBB colour.');
          payload = { company_name: f.company_name.value.trim(), logo_url: f.logo_url.value.trim(), primary_color: f.primary_color.value.trim(), accent_color: f.accent_color.value.trim() };
        } else {
          const settings = {};
          ['auto_export', 'email_notifications', 'notify_on_leads', 'notify_job_completion', 'notify_lead_assigned', 'notify_usage_warnings', 'shared_workspace'].forEach(k => { settings[k] = f[k].checked; });
          ['default_posts_per_search', 'default_comments_per_post', 'min_lead_score', 'usage_warning_percent', 'invite_expiry_days'].forEach(k => {
            const val = f[k].value.trim(); if (val !== '') settings[k] = Number(val);
          });
          for (const k of Object.keys(settings)) {
            const i = f[k]; if (i && i.type === 'number' && ((i.max && settings[k] > Number(i.max)) || (i.min && settings[k] < Number(i.min)))) return bad(k, `Must be between ${i.min || 0} and ${i.max}.`);
          }
          settings.lead_assignment = f.lead_assignment.value; settings.default_member_role = f.default_member_role.value;
          if (settings.shared_workspace && !(org.settings || {}).shared_workspace) {
            const ok = await confirmDialog('Turn on shared workspace?', 'Every member of your organization will be able to see all searches, pages, posts, comments and leads — including other members\' data. You can turn this off at any time.', { confirm: 'Turn on' });
            if (!ok) return;
          }
          payload = { settings };
        }
        busy(btn, true, 'Saving…');
        try { await api('/api/organizations/current', { method: 'PATCH', body: payload }); toast('Saved.', 'success'); await getOrg(true); refreshBrand(); if (tab === 'settings') route(); }
        catch (err) { toast(err.message, 'error'); }
        finally { busy(btn, false); }
      });
    },
  };
  function fld(name, lbl, val, type, ro, extra, hint) {
    return `<div class="oa-field"><label for="f-${attr(name)}">${esc(lbl)}</label><input class="form-input" id="f-${attr(name)}" name="${attr(name)}" type="${attr(type)}" value="${attr(val == null ? '' : val)}" ${ro || ''} ${extra || ''} aria-describedby="e-${attr(name)}"/>${hint ? `<span class="oa-hint">${esc(hint)}</span>` : ''}<span class="oa-err" id="e-${attr(name)}" role="alert"></span></div>`;
  }
  function colorFld(name, lbl, val, ro) {
    return `<div class="oa-field"><label for="f-${attr(name)}">${esc(lbl)}</label><div class="oa-color"><input type="color" data-for="${attr(name)}" value="${attr(val)}" aria-label="${attr(lbl)} picker" ${ro}/><input class="form-input" data-hex id="f-${attr(name)}" name="${attr(name)}" value="${attr(val)}" maxlength="7" ${ro} aria-describedby="e-${attr(name)}"/></div><span class="oa-err" id="e-${attr(name)}" role="alert"></span></div>`;
  }
  const readonlyNote = () => '<p class="oa-hint" style="margin-top:12px">You can view these settings. Changing them requires the “Org settings” permission.</p>';

  // ── Team ───────────────────────────────────────────────────────────
  ROUTES.team = {
    title: (p) => p[1] === 'user' ? 'Team · User' : 'Team · ' + ({ invitations: 'Invitations', roles: 'Roles & permissions' }[p[1]] || 'Users'),
    skel: 'table',
    async render(v) {
      const sub = v.parts[1] || 'users';
      if (sub === 'user') return renderUserDetail(v, v.parts[2]);
      const tabBar = tabs([['#team/users', 'Users'], ['#team/invitations', 'Invitations'], ['#team/roles', 'Roles & permissions']], '#team/' + sub);
      const actions = can('members.invite') ? `<button type="button" class="btn btn-primary" data-invite>${ico('plus')} Invite user</button>` : '';
      v.el.innerHTML = head('Team', 'Manage who can access your organization and what they can do.', actions) + tabBar + '<div data-host></div>';
      const inv = $('[data-invite]', v.el); if (inv) inv.onclick = () => inviteModal(() => { if (sub !== 'roles') t && t.reload(); });
      const host = $('[data-host]', v.el);
      let t = null;
      if (sub === 'users') {
        t = dataTable(host, {
          url: '/api/org-admin/users', caption: 'Team members', orderParam: true, defaultSort: 'name', defaults: { order: 'asc' }, initial: v.query,
          filters: [{ name: 'q', label: 'Search', placeholder: 'Search name or email' },
            { name: 'role', label: 'Role', type: 'select', all: 'All roles', options: [['owner', 'Admin (Owner)'], ['admin', 'Admin'], ['manager', 'Manager'], ['member', 'User'], ['viewer', 'Viewer']] },
            { name: 'status', label: 'Status', type: 'select', all: 'All statuses', options: [['active', 'Active'], ['inactive', 'Inactive'], ['suspended', 'Suspended']] }],
          columns: [
            { label: 'User', sort: 'name', render: r => userCell(r.name, r.email, r.is_me ? 'You' : ''), csv: r => r.name || r.email },
            { label: 'Email', id: 'email', hidden: true, render: r => esc(r.email || '') },
            { label: 'Role', sort: 'role', render: r => roleTag(r.role) },
            { label: 'Status', sort: 'status', render: r => pill(r.status) },
            { label: 'Last login', sort: 'last_login', render: r => timeTag(r.last_login), csv: r => r.last_login || '' },
            { label: 'Searches', sort: 'searches', num: true, render: r => num(r.searches) },
            { label: 'Leads', sort: 'leads', num: true, render: r => num(r.leads) },
            { label: 'Assigned', num: true, render: r => num(r.assigned_leads) },
          ],
          rowHref: r => '#team/user/' + r.user_id,
          exportUrl: can('exports.create') ? () => '/api/org-admin/exports/users.csv' : null,
          bulk: can('members.suspend') ? {
            rowId: r => r.user_id, selectable: memberManageable,
            actions: [{ id: 'active', label: 'Restore access' }, { id: 'inactive', label: 'Deactivate' }, { id: 'suspended', label: 'Suspend', danger: true }],
            run: (act, list, btn) => bulkMemberStatus(act, list, btn),
          } : null,
          rowMenu: r => memberMenu(r),
          async onAction(act, r) {
            if (act === 'view') { location.hash = '#team/user/' + r.user_id; return; }
            if (act === 'role') { location.hash = '#team/user/' + r.user_id; return; }
            if (['active', 'inactive', 'suspended'].indexOf(act) >= 0) { if (await bulkMemberStatus(act, [r])) t.reload(); return; }
            if (act === 'remove') {
              if (!(await confirmDialog('Remove from organization?', `${r.email} will lose access to ${S.ctx.organization.name}. Their searches and leads stay in the organization.`, { danger: true, confirm: 'Remove user' }))) return;
              try { await api('/api/organizations/current/members/' + encodeURIComponent(r.user_id), { method: 'DELETE' }); toast('User removed.', 'success'); S.members = null; t.reload(); }
              catch (e) { toast(e.message, 'error'); }
            }
          },
          empty: { title: 'No team members', desc: 'Invite your first team member to get started.' },
        });
      } else if (sub === 'invitations') {
        if (!can('members.invite')) { renderError(host, { status: 403, message: 'Managing invitations requires the “Invite users” permission.' }); return; }
        t = dataTable(host, {
          url: '/api/org-admin/invitations', caption: 'Invitations', defaults: { status: 'pending' }, initial: v.query,
          filters: [{ name: 'q', label: 'Search', placeholder: 'Search email' },
            { name: 'status', label: 'Status', type: 'select', all: 'All', options: [['pending', 'Pending'], ['accepted', 'Accepted'], ['expired', 'Expired'], ['cancelled', 'Revoked']] }],
          columns: [
            { label: 'Email', render: r => `<b>${esc(r.email)}</b>` }, { label: 'Role', render: r => roleTag(r.role) },
            { label: 'Status', render: r => pill(r.status === 'cancelled' ? 'cancelled' : r.status, r.status === 'cancelled' ? 'revoked' : r.status) },
            { label: 'Invited by', render: r => esc(r.invited_by || '—') }, { label: 'Sent', render: r => timeTag(r.created_at) },
            { label: 'Expires', render: r => esc(fmtDate(r.expires_at)) },
            { label: '', render: r => (r.status === 'pending' || r.status === 'expired') ? `<div class="oa-actions" style="justify-content:flex-end"><button type="button" class="btn btn-secondary btn-sm" data-act="resend">Resend</button>${r.status === 'pending' ? '<button type="button" class="btn btn-danger btn-sm" data-act="revoke">Revoke</button>' : ''}</div>` : '' },
          ],
          empty: { title: 'No invitations', desc: 'Invitations you send appear here until they are accepted.' },
          async onAction(act, r, btn) {
            if (act === 'revoke' && !(await confirmDialog('Revoke invitation?', `The link sent to ${r.email} will stop working.`, { danger: true, confirm: 'Revoke' }))) return;
            busy(btn, true, act === 'resend' ? 'Sending…' : 'Revoking…');
            try {
              const res = await api(`/api/organizations/current/invitations/${encodeURIComponent(r.id)}${act === 'resend' ? '/resend' : ''}`, { method: act === 'resend' ? 'POST' : 'DELETE' });
              toast(res.message || 'Done', 'success'); t.reload();
            } catch (e) { toast(e.message, 'error'); busy(btn, false); }
          },
        });
      } else if (sub === 'roles') {
        await renderRoles(host, v);
      } else { v.el.innerHTML = notFound(); }
    },
  };
  // Same rules as the user detail page: never yourself, never the owner, Admins only by the owner.
  const memberManageable = (r) => !r.is_me && r.role !== 'owner' && !(r.role === 'admin' && !isOwner());
  function memberMenu(r) {
    const m = [{ act: 'view', label: 'View profile & usage' }];
    if (!memberManageable(r)) return m;
    if (can('members.update')) m.push({ act: 'role', label: 'Change role…' });
    if (can('members.suspend')) {
      if (r.status === 'active') m.push({ act: 'inactive', label: 'Deactivate' }, { act: 'suspended', label: 'Suspend', danger: true });
      else m.push({ act: 'active', label: 'Restore access' });
    }
    if (can('members.delete')) m.push({ act: 'remove', label: 'Remove from organization', danger: true });
    return m;
  }
  async function bulkMemberStatus(st, list, btn) {
    const targets = list.filter(memberManageable).filter(r => r.status !== st);
    if (!targets.length) { toast(st === 'active' ? 'Those users already have access.' : 'Nothing to change for the selected users.', 'info'); return false; }
    const who = targets.length === 1 ? targets[0].email : targets.length + ' users';
    const text = { inactive: ['Deactivate ' + who + '?', 'They lose access immediately and free up seats. You can restore them later.', 'Deactivate'], suspended: ['Suspend ' + who + '?', 'They lose access immediately (e.g. for a security concern). You can restore them later.', 'Suspend'], active: ['Restore access for ' + who + '?', 'They can sign in again with their existing account.', 'Restore'] }[st];
    if (!(await confirmDialog(text[0], text[1], { danger: st !== 'active', confirm: text[2] }))) return false;
    busy(btn, true, 'Working…');
    const action = { active: 'restore', inactive: 'deactivate', suspended: 'suspend' }[st];
    try { return await runBulk('/api/org-admin/members/bulk', { ids: targets.map(r => r.user_id), action }, st === 'active' ? 'Restored' : st === 'inactive' ? 'Deactivated' : 'Suspended', 'user'); }
    finally { busy(btn, false); S.members = null; }
  }
  function inviteModal(done) {
    const org = S.org || {}; const def = ((org.settings || {}).default_member_role) || 'member';
    const roles = [['member', 'User — run searches and work their leads'], ['manager', 'Manager — also assign leads and see the team'], ['viewer', 'Viewer — read-only access']];
    if (isOwner()) roles.push(['admin', 'Admin — full organization management']);
    openModal({
      title: 'Invite a team member',
      body: `<form class="oa-form" data-f novalidate><div class="oa-field"><label for="inv-email">Email address</label><input class="form-input" id="inv-email" name="email" type="email" required autocomplete="off" aria-describedby="inv-err"/><span class="oa-err" id="inv-err" role="alert"></span></div>
        <div class="oa-field"><label for="inv-role">Role</label><select class="form-select" id="inv-role" name="role">${selectOpts(roles, def)}</select><span class="oa-hint">They'll receive an email with a secure one-time link to join. No password is ever sent.</span></div><div data-result></div></form>`,
      foot: '<button type="button" class="btn btn-secondary" data-close>Cancel</button><button type="button" class="btn btn-primary" data-send>Send invitation</button>',
      onMount(root) {
        const f = $('[data-f]', root), btn = $('[data-send]', root);
        const send = async () => {
          const email = f.email.value.trim();
          if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) { $('#inv-err', root).textContent = 'Enter a valid email address.'; f.email.focus(); return; }
          $('#inv-err', root).textContent = ''; busy(btn, true, 'Sending…');
          try {
            const res = await api('/api/organizations/current/invitations', { method: 'POST', body: { email, role: f.role.value } });
            toast(res.message || 'Invitation sent', 'success');
            const link = safeUrl(res.invite_url);
            $('[data-result]', root).innerHTML = `<div class="alert alert-success">${ico('info', 'alert-icon')}<div class="alert-body"><div class="alert-title">Invitation sent to ${esc(email)}</div>${link ? `<div class="oa-small">If email delivery isn't set up yet you can share this one-time link securely:</div><div class="oa-color" style="margin-top:6px"><input class="form-input oa-mono" readonly value="${attr(link)}" aria-label="Invitation link"/><button type="button" class="btn btn-secondary btn-sm" data-copy>Copy</button></div>` : ''}</div></div>`;
            const cp = $('[data-copy]', root); if (cp) cp.onclick = () => { navigator.clipboard && navigator.clipboard.writeText(link).then(() => toast('Link copied', 'success')); };
            f.email.value = ''; S.members = null; if (done) done();
          } catch (e) { $('#inv-err', root).textContent = e.message; }
          finally { busy(btn, false); }
        };
        btn.onclick = send; f.addEventListener('submit', (e) => { e.preventDefault(); send(); });
      },
    });
  }
  async function renderRoles(host, v) {
    const d = await api('/api/org-admin/roles');
    if (!v.alive()) return;
    const roles = d.roles; const editable = d.can_manage;
    const eff = {}; roles.forEach(r => { eff[r.role] = new Set(r.effective); });
    host.innerHTML = `<div class="oa-note" style="margin-bottom:14px">${ico('info')}<span><b>Admin (Owner)</b> and <b>Admin</b> always have full organization access and cannot be changed here. Configure what Managers, Users and Viewers can do. Only organization-level permissions can be delegated — platform administration is never available to organizations.${d.shared_workspace ? ' <b>Shared workspace is ON</b>, so every role can currently see all organization data.' : ''}</span></div>
      <div class="oa-table-wrap"><table class="oa-table oa-matrix"><thead><tr><th scope="col">Permission</th>${roles.map(r => `<th scope="col">${esc(r.label)}<div class="oa-small oa-muted" style="text-transform:none;letter-spacing:0;font-weight:500">${num(r.members)} member${r.members === 1 ? '' : 's'}</div></th>`).join('')}</tr></thead>
      <tbody>${d.catalog.map(g => `<tr class="grp"><td colspan="${roles.length + 1}">${esc(g.group)}</td></tr>` + g.permissions.map(p => `<tr><td><b>${esc(p.label)}</b><div class="oa-small oa-muted">${esc(p.description)}</div></td>${roles.map(r => `<td><input type="checkbox" data-role="${attr(r.role)}" data-perm="${attr(p.key)}" ${eff[r.role].has(p.key) ? 'checked' : ''} ${editable ? '' : 'disabled'} aria-label="${attr(r.label + ': ' + p.label)}"/></td>`).join('')}</tr>`).join('')).join('')}</tbody></table></div>
      ${editable ? `<div class="oa-form-foot" style="border:0;flex-wrap:wrap">${roles.map(r => `<button type="button" class="btn btn-ghost btn-sm" data-reset="${attr(r.role)}">Reset ${esc(r.label)} to defaults</button>`).join('')}<button type="button" class="btn btn-primary" data-save>Save permissions</button></div>` : '<p class="oa-hint" style="margin-top:10px">Changing role permissions requires the owner or an Admin.</p>'}`;
    if (!editable) return;
    const catalogKeys = d.catalog.flatMap(g => g.permissions.map(p => p.key));
    $('[data-save]', host).onclick = async (e) => {
      const rp = {};
      roles.forEach(r => {
        const defaults = new Set(r.defaults); rp[r.role] = {};
        catalogKeys.forEach(k => { const on = $(`input[data-role="${r.role}"][data-perm="${k}"]`, host).checked; if (on !== defaults.has(k)) rp[r.role][k] = on; });
      });
      busy(e.currentTarget, true, 'Saving…');
      try { await api('/api/organizations/current', { method: 'PATCH', body: { role_permissions: rp } }); toast('Role permissions saved. Affected members get the new permissions on their next request.', 'success'); renderRoles(host, v); }
      catch (err) { toast(err.message, 'error'); busy(e.currentTarget, false); }
    };
    $$('[data-reset]', host).forEach(b => b.onclick = async () => {
      if (!(await confirmDialog('Reset to defaults?', 'This role goes back to LeadAI\'s default permissions.', { confirm: 'Reset' }))) return;
      try { await api('/api/organizations/current', { method: 'PATCH', body: { role_permissions: { [b.dataset.reset]: {} } } }); toast('Reset to defaults.', 'success'); renderRoles(host, v); }
      catch (err) { toast(err.message, 'error'); }
    });
  }
  async function renderUserDetail(v, uid) {
    const [d, roles] = await Promise.all([api('/api/org-admin/users/' + encodeURIComponent(uid || '')), api('/api/org-admin/roles').catch(() => null)]);
    if (!v.alive()) return;
    const u = d.user; const us = d.usage;
    const manageable = !u.is_me && u.role !== 'owner' && !(u.role === 'admin' && !isOwner());
    const roleOpts = [['member', 'User'], ['manager', 'Manager'], ['viewer', 'Viewer']].concat(isOwner() ? [['admin', 'Admin']] : []);
    const reason = u.is_me ? 'This is your own account — manage it from Profile & security.' : u.role === 'owner' ? 'The organization owner cannot be modified.' : (!manageable ? 'Only the organization owner can modify another Admin.' : '');
    const actions = manageable ? [
      can('members.update') ? `<button type="button" class="btn btn-secondary" data-reset>${ico('lock')} Reset access</button>` : '',
      can('members.suspend') && u.status === 'active' ? '<button type="button" class="btn btn-secondary" data-status="inactive">Deactivate</button><button type="button" class="btn btn-danger" data-status="suspended">Suspend</button>' : '',
      can('members.suspend') && u.status !== 'active' ? '<button type="button" class="btn btn-primary" data-status="active">Restore access</button>' : '',
      can('members.delete') ? '<button type="button" class="btn btn-danger" data-remove>Remove</button>' : '',
    ].join('') : '';
    const catalog = roles ? roles.catalog : [];
    const ov = u.permissions_override || {};
    v.el.innerHTML = head(u.name || u.email, u.email, actions, ['#team/users', 'Team']) +
      (reason ? `<div class="oa-note" style="margin-bottom:16px">${ico('info')}<span>${esc(reason)}</span></div>` : '') +
      `<div class="oa-grid oa-grid-4">
        <div class="oa-card oa-stat"><span class="oa-stat-label">Searches</span><span class="oa-stat-value">${num(us.searches)}</span><span class="oa-stat-meta">${num(us.failed_searches)} failed</span></div>
        <div class="oa-card oa-stat"><span class="oa-stat-label">Leads found</span><span class="oa-stat-value">${num(us.leads)}</span><span class="oa-stat-meta">${num(us.assigned_leads)} assigned to them</span></div>
        <div class="oa-card oa-stat"><span class="oa-stat-label">Exports</span><span class="oa-stat-value">${num(us.exports)}</span></div>
        <div class="oa-card oa-stat"><span class="oa-stat-label">Tokens used</span><span class="oa-stat-value">${num(us.tokens_used)}</span></div>
      </div>
      <div class="oa-grid oa-grid-2">
        <div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">Account</div></div>${kv([['Role', roleTag(u.role)], ['Status', pill(u.status)], ['Last login', timeTag(u.last_login)], ['Joined', esc(fmtDate(u.joined_at))]])}
          ${manageable && can('members.update') ? `<form class="oa-form" data-role-form style="margin-top:16px"><div class="oa-field"><label for="ud-role">Change role</label><div class="oa-color"><select class="form-select" id="ud-role" name="role">${selectOpts(roleOpts, u.role)}</select><button type="submit" class="btn btn-secondary">Update role</button></div><span class="oa-hint">Changing the role signs the user out so the new access applies immediately.</span></div></form>` : ''}</div>
        <div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">Permissions</div><span class="oa-card-sub">${u.configurable ? 'Per-user overrides of the role' : 'Fixed for this role'}</span></div>
          ${u.configurable && manageable && can('roles.manage') && catalog.length ? `<form data-ov><div class="oa-table-wrap" style="max-height:340px;overflow:auto"><table class="oa-table"><tbody>${catalog.flatMap(g => g.permissions).map(p => `<tr><td><b>${esc(p.label)}</b><div class="oa-small oa-muted">${u.permissions.indexOf(p.key) >= 0 ? 'Currently allowed' : 'Currently not allowed'}</div></td><td style="text-align:right"><label class="sr-only" for="ov-${attr(p.key)}">${esc(p.label)}</label><select class="oa-tri" id="ov-${attr(p.key)}" data-perm="${attr(p.key)}">${selectOpts([['', 'Role default'], ['1', 'Allow'], ['0', 'Deny']], p.key in ov ? (ov[p.key] ? '1' : '0') : '')}</select></td></tr>`).join('')}</tbody></table></div><div class="oa-form-foot" style="border:0"><button type="submit" class="btn btn-primary btn-sm">Save overrides</button></div></form>`
            : `<div style="display:flex;flex-wrap:wrap;gap:6px">${u.permissions.map(p => `<span class="badge badge-muted">${esc(p)}</span>`).join('') || '<span class="oa-muted">None</span>'}</div>`}</div>
      </div>
      <div class="oa-grid oa-grid-2">
        <div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">Recent searches</div><a class="oa-link oa-small" href="#searches?user_id=${attr(u.user_id)}">All</a></div>${d.searches.length ? `<ul class="oa-feed">${d.searches.map(r => `<li class="${r.status === 'error' ? 'fail' : ''}"><div class="oa-feed-main"><a class="oa-link" href="#searches/${attr(r.run_id)}">${esc(r.url || r.run_id)}</a> ${pill(r.status)}</div>${timeTag(r.created_at)}</li>`).join('')}</ul>` : '<p class="oa-muted oa-small">No searches yet.</p>'}</div>
        <div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">Leads</div><a class="oa-link oa-small" href="#leads?assignee=${attr(u.user_id)}">Assigned</a></div>${d.leads.length ? `<ul class="oa-feed">${d.leads.map(l => `<li><div class="oa-feed-main"><a class="oa-link" href="#lead/${attr(l.id)}">${esc(l.name)}</a> ${scoreTag(l.score)} ${pill(l.status)}</div>${timeTag(l.created_at)}</li>`).join('')}</ul>` : '<p class="oa-muted oa-small">No leads yet.</p>'}</div>
      </div>
      <div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">Activity</div></div>${d.activity.length ? `<ul class="oa-feed">${d.activity.map(a => `<li class="${a.status === 'failure' ? 'fail' : ''}"><div class="oa-feed-main"><b>${esc(a.action)}</b> <span class="oa-small oa-muted">${esc(a.resource_type || '')} ${a.ip ? '· ' + esc(a.ip) : ''}</span></div>${timeTag(a.at)}</li>`).join('')}</ul>` : '<p class="oa-muted oa-small">No recorded activity.</p>'}</div>`;
    const patch = async (body, btn, msg) => {
      busy(btn, true, 'Saving…');
      try { await api('/api/organizations/current/members/' + encodeURIComponent(u.user_id), { method: 'PATCH', body }); toast(msg, 'success'); S.members = null; route(); }
      catch (e) { toast(e.message, 'error'); busy(btn, false); }
    };
    const rf = $('[data-role-form]', v.el);
    if (rf) rf.onsubmit = async (e) => { e.preventDefault(); const role = rf.role.value; if (role === u.role) return; if (!(await confirmDialog('Change role?', `${u.email} will become ${ROLE_LABELS[role]} and will be signed out.`, { confirm: 'Change role' }))) return; patch({ role }, $('button', rf), 'Role updated.'); };
    $$('[data-status]', v.el).forEach(b => b.onclick = async () => {
      const st = b.dataset.status;
      const text = { inactive: ['Deactivate user?', 'They lose access immediately and free up a seat. You can restore them later.', 'Deactivate'], suspended: ['Suspend user?', 'They lose access immediately (e.g. for a security concern). You can restore them later.', 'Suspend'], active: ['Restore access?', 'They can sign in again with their existing account.', 'Restore'] }[st];
      if (!(await confirmDialog(text[0], text[1], { danger: st !== 'active', confirm: text[2] }))) return;
      patch({ status: st }, b, st === 'active' ? 'Access restored.' : 'User ' + (st === 'inactive' ? 'deactivated.' : 'suspended.'));
    });
    const rs = $('[data-reset]', v.el);
    if (rs) rs.onclick = async () => {
      const res = await confirmDialog('Reset access?', `We'll email ${u.email} a secure one-time link to set a new password. No password is shown or sent.`, { confirm: 'Send reset link', extra: '<label class="oa-switch" style="border:0"><input type="checkbox" data-rev checked/><span class="oa-switch-text"><b>Sign them out everywhere</b><span>Ends all of their current sessions now.</span></span></label>', collect: (root) => ({ revoke: $('[data-rev]', root).checked }) });
      if (!res) return;
      busy(rs, true, 'Sending…');
      try { const r = await api(`/api/org-admin/users/${encodeURIComponent(u.user_id)}/reset-access`, { method: 'POST', body: { revoke_sessions: !!res.revoke } }); toast(r.message, 'success'); }
      catch (e) { toast(e.message, 'error'); } finally { busy(rs, false); }
    };
    const rm = $('[data-remove]', v.el);
    if (rm) rm.onclick = async () => {
      if (!(await confirmDialog('Remove from organization?', `${u.email} will lose access to ${S.ctx.organization.name}. Their searches and leads stay in the organization.`, { danger: true, confirm: 'Remove user' }))) return;
      busy(rm, true, 'Removing…');
      try { await api('/api/organizations/current/members/' + encodeURIComponent(u.user_id), { method: 'DELETE' }); toast('User removed.', 'success'); S.members = null; location.hash = '#team/users'; }
      catch (e) { toast(e.message, 'error'); busy(rm, false); }
    };
    const ovf = $('[data-ov]', v.el);
    if (ovf) ovf.onsubmit = (e) => {
      e.preventDefault(); const o = {};
      $$('select[data-perm]', ovf).forEach(s => { if (s.value !== '') o[s.dataset.perm] = s.value === '1'; });
      patch({ permissions_override: o }, $('button[type=submit]', ovf), 'Permission overrides saved.');
    };
  }

  // ── URL search ─────────────────────────────────────────────────────
  ROUTES.search = {
    title: () => 'URL Search',
    async render(v) {
      if (!can('search.create')) { renderError(v.el, { status: 403, message: 'Running searches requires the “Run searches” permission.' }); return; }
      const org = await getOrg(); if (!v.alive()) return;
      const s = org.settings || {}; const caps = S.ctx.caps || {};
      const maxP = Math.min(...[caps.posts_per_search, caps.platform_max_posts].filter(Boolean).concat([100000]));
      const maxC = Math.min(...[caps.comments_per_post, caps.platform_max_comments_per_post].filter(Boolean).concat([100000]));
      v.el.innerHTML = head('URL Search', 'Paste a Facebook page, Instagram profile, YouTube channel or LinkedIn company URL. LeadAI collects posts and comments and finds leads.') +
        `<form class="oa-card oa-form" data-f novalidate><div class="oa-form-grid">
          <div class="oa-field span-2"><label for="s-url">Profile / page URL</label><input class="form-input" id="s-url" name="url" type="url" required placeholder="https://www.facebook.com/yourcompetitor" maxlength="300" aria-describedby="e-url"/><span class="oa-err" id="e-url" role="alert"></span></div>
          ${fld('max_posts', 'Posts to scan', Math.min(s.default_posts_per_search || caps.default_posts || maxP, maxP), 'number', '', `min="1" ${maxP < 100000 ? 'max="' + maxP + '"' : ''}`, maxP < 100000 ? 'Plan maximum: ' + maxP : '')}
          ${fld('max_comments_per_post', 'Comments per post', Math.min(s.default_comments_per_post || caps.default_comments_per_post || maxC, maxC), 'number', '', `min="1" ${maxC < 100000 ? 'max="' + maxC + '"' : ''}`, maxC < 100000 ? 'Plan maximum: ' + maxC : '')}
          <div class="oa-field span-2"><label for="s-kw">Only analyse comments containing <span class="optional">(optional)</span></label><input class="form-input" id="s-kw" name="include_keywords" placeholder="e.g. price, interested, buy — leave empty to use your organization's lead rules"/><span class="oa-hint">Comma separated. Leave empty to apply your <a class="oa-link" href="#rules">lead rules</a>.</span></div>
        </div><div class="oa-form-foot"><button type="submit" class="btn btn-primary">${ico('search')} Start search</button></div></form><div data-status></div>`;
      const f = $('[data-f]', v.el);
      liveValidate(f);
      f.onsubmit = async (e) => {
        e.preventDefault(); const btn = $('button[type=submit]', f);
        const url = f.url.value.trim(); $$('.oa-err', f).forEach(x => { x.textContent = ''; });
        if (!safeUrl(url)) { $('#e-url', f).textContent = 'Enter the full URL, starting with https://'; f.url.focus(); return; }
        const p = { url };
        const mp = Number(f.max_posts.value), mc = Number(f.max_comments_per_post.value);
        if (mp) { if (mp > maxP) { $('#e-max_posts', f).textContent = 'Your plan allows up to ' + maxP; return; } p.max_posts = mp; }
        if (mc) { if (mc > maxC) { $('#e-max_comments_per_post', f).textContent = 'Your plan allows up to ' + maxC; return; } p.max_comments_per_post = mc; }
        if (f.include_keywords.value.trim()) { p.filter_mode = 'custom'; p.include_keywords = f.include_keywords.value.trim(); }
        busy(btn, true, 'Starting…');
        try {
          const r = await api('/api/url/search' + qs(p), { method: 'POST' });
          toast(r.message || 'Search started', 'success'); pollRun(v, r.run_id);
        } catch (err) { toast(err.message, 'error'); }
        finally { busy(btn, false); }
      };
    },
  };
  function pollRun(v, runId) {
    const box = $('[data-status]', v.el);
    const tick = async () => {
      if (!v.alive() || !box.isConnected) return;
      let s;
      try { s = (await api('/api/org-admin/searches/' + encodeURIComponent(runId))).search; } catch (e) { box.innerHTML = `<div class="alert alert-danger" style="margin-top:16px">${ico('alert', 'alert-icon')}<div class="alert-body">${esc(e.message)}</div></div>`; return; }
      box.innerHTML = `<div class="oa-card" style="margin-top:16px"><div class="oa-card-head"><div class="oa-card-title">Search progress</div>${pill(s.status)}</div>${kv([['URL', extLink(s.url)], ['Phase', esc(label(s.phase || '—'))], ['Message', esc(s.message || '—')], ['Pages', num(s.pages)], ['Posts', num(s.posts)], ['Comments', num(s.comments)], ['Leads', num(s.leads)]])}
        <div class="oa-actions" style="margin-top:14px"><a class="btn btn-secondary btn-sm" href="#searches/${attr(runId)}">Open search</a>${s.leads ? `<a class="btn btn-primary btn-sm" href="#leads?run_id=${attr(runId)}">View leads</a>` : ''}</div></div>`;
      if (s.status === 'running') setTimeout(tick, 4000);
    };
    tick();
  }

  // ── Searches ───────────────────────────────────────────────────────
  ROUTES.searches = {
    skel: 'table',
    title: (p) => p[1] ? 'Search details' : 'Searches',
    async render(v) {
      if (v.parts[1]) return renderSearchDetail(v, v.parts[1]);
      const members = await getMembers(); if (!v.alive()) return;
      v.el.innerHTML = head('Searches', 'Every search run in your organization, by any member.', `<a class="btn btn-primary" href="#search">${ico('search')} New search</a>`) + '<div data-host></div>';
      dataTable($('[data-host]', v.el), {
        url: '/api/org-admin/searches', caption: 'Searches', defaultSort: 'newest', initial: v.query,
        exportUrl: can('exports.create') && can('search.export') ? (p) => '/api/org-admin/exports/searches.csv' + qs(p) : null,
        filters: [{ name: 'q', label: 'Search', placeholder: 'Search URL or run id' },
          { name: 'user_id', label: 'User', type: 'select', all: 'All users', options: memberOptions(members) },
          { name: 'platform', label: 'Platform', type: 'select', all: 'All platforms', options: PLATFORMS },
          { name: 'status', label: 'Status', type: 'select', all: 'All statuses', options: [['running', 'Running'], ['completed', 'Completed'], ['error', 'Failed'], ['cancelled', 'Cancelled']] },
          { name: 'from', label: 'From date', type: 'date' }, { name: 'to', label: 'To date', type: 'date' }],
        columns: [
          { label: 'Started', sort: 'newest', render: r => timeTag(r.created_at) },
          { label: 'URL', cls: 'oa-trunc', render: r => `<span title="${attr(r.url)}">${esc(r.url || '—')}</span>` },
          { label: 'Platform', render: r => esc(plat(r.platform)) },
          { label: 'User', render: r => esc(r.user_name || r.user_email || '—') },
          { label: 'Status', sort: 'status', dir: 'ascending', render: r => pill(r.status) + (r.error ? `<div class="oa-small oa-muted oa-trunc" style="max-width:200px" title="${attr(r.error)}">${esc(r.error)}</div>` : '') },
          { label: 'Pages', sort: 'results', num: true, render: r => num(r.pages_found) },
          { label: 'Leads', num: true, render: r => num(r.leads) },
        ],
        rowHref: r => '#searches/' + r.run_id,
        empty: { title: 'No searches yet', desc: 'Searches run by anyone in your organization appear here.', action: '<a class="btn btn-primary" href="#search">Start a search</a>' },
      });
    },
  };
  async function renderSearchDetail(v, runId) {
    const s = (await api('/api/org-admin/searches/' + encodeURIComponent(runId))).search;
    if (!v.alive()) return;
    const cancel = s.status === 'running' && can('search.cancel') ? '<button type="button" class="btn btn-danger" data-cancel>Cancel search</button>' : '';
    v.el.innerHTML = head('Search details', s.url, cancel + `<a class="btn btn-secondary" href="#pages?run_id=${attr(s.run_id)}">Pages</a><a class="btn btn-primary" href="#leads?run_id=${attr(s.run_id)}">Leads (${num(s.leads)})</a>`, ['#searches', 'Searches']) +
      `<div class="oa-grid oa-grid-4">${[['Pages', s.pages], ['Posts', s.posts], ['Comments', s.comments], ['Leads', s.leads]].map(([k, n]) => `<div class="oa-card oa-stat"><span class="oa-stat-label">${esc(k)}</span><span class="oa-stat-value">${num(n)}</span></div>`).join('')}</div>
      <div class="oa-grid oa-grid-2"><div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">Run</div>${pill(s.status)}</div>${kv([['URL', extLink(s.url)], ['Platform', esc(plat(s.platform))], ['Run by', esc(s.user_name || s.user_email || '—')], ['Started', esc(fmtDate(s.created_at, true))], ['Finished', esc(fmtDate(s.completed_at, true))], ['Duration', s.duration_seconds != null ? esc(s.duration_seconds + ' s') : '—'], ['Phase', esc(label(s.phase || '—'))], ['Message', esc(s.message || '—')]])}</div>
      <div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">Scrape job</div></div>${kv([['Max posts', num(s.max_posts)], ['Comments / post', num(s.max_comments_per_post)], ['Comment filter', esc((s.comment_filter && s.comment_filter.mode) || 'Organization rules / defaults')], ['Apify run', s.apify_run_id ? `<span class="oa-mono">${esc(s.apify_run_id)}</span>` : '—'], ['Dataset', s.dataset_id ? `<span class="oa-mono">${esc(s.dataset_id)}</span>` : '—']])}
        ${s.error ? `<div class="alert alert-danger" style="margin-top:14px">${ico('alert', 'alert-icon')}<div class="alert-body"><div class="alert-title">Error</div>${esc(s.error)}</div></div>` : ''}</div></div>`;
    const c = $('[data-cancel]', v.el);
    if (c) c.onclick = async () => {
      if (!(await confirmDialog('Cancel this search?', 'The running scrape stops at its next checkpoint.', { danger: true, confirm: 'Cancel search' }))) return;
      busy(c, true, 'Cancelling…');
      try { const r = await api('/api/search/' + encodeURIComponent(runId) + '/cancel', { method: 'POST' }); toast(r.message, 'success'); setTimeout(route, 1500); }
      catch (e) { toast(e.message, 'error'); busy(c, false); }
    };
  }

  // ── Pages / Posts / Comments ───────────────────────────────────────
  function dataView(kind, title, desc, columns, sorts, rowHref, exportUrl) {
    return {
      skel: 'table',
      async render(v) {
        const members = await getMembers(); if (!v.alive()) return;
        const extra = [];
        if (v.query.run_id) extra.push('search ' + v.query.run_id);
        if (v.query.page_id) extra.push('one page');
        if (v.query.post_id) extra.push('one post');
        v.el.innerHTML = head(title, desc + (extra.length ? ' Filtered to ' + extra.join(', ') + '.' : ''), (extra.length ? `<a class="btn btn-ghost" href="#${kind}">${ico('x')} Clear filter</a>` : '')) + '<div data-host></div>';
        dataTable($('[data-host]', v.el), {
          url: '/api/org-admin/data/' + kind, caption: title, defaultSort: 'newest', initial: v.query,
          exportUrl: exportUrl && can('exports.create') && can('search.export') ? (p) => exportUrl(v.query, p) : null,
          fixed: { run_id: v.query.run_id, page_id: v.query.page_id, post_id: v.query.post_id },
          filters: [{ name: 'q', label: 'Search' }, { name: 'platform', label: 'Platform', type: 'select', all: 'All platforms', options: PLATFORMS },
            { name: 'user_id', label: 'User', type: 'select', all: 'All users', options: memberOptions(members) },
            { name: 'sort', label: 'Sort', type: 'select', all: 'Newest first', options: sorts }],
          columns, rowHref,
          empty: { title: 'No ' + kind + ' yet', desc: 'Scraped ' + kind + ' from your organization\'s searches appear here.' },
        });
      },
    };
  }
  ROUTES.pages = dataView('pages', 'Pages', 'Social pages and profiles found by your organization\'s searches.', [
    { label: 'Page', render: r => `<b>${esc(r.name || '—')}</b><div class="oa-small oa-muted">${esc(r.category || '')}</div>` },
    { label: 'Platform', render: r => esc(plat(r.platform)) },
    { label: 'Followers', num: true, render: r => num(r.followers) },
    { label: 'Contact', render: r => esc([r.phone, r.email].filter(Boolean).join(' · ') || '—') },
    { label: 'Score', render: r => scoreTag(r.lead_score) },
    { label: '', render: r => `<div class="oa-actions" style="justify-content:flex-end">${r.url ? extLink(r.url, 'Open') : ''}<a class="btn btn-secondary btn-sm" href="#posts?page_id=${attr(r.id)}">Posts</a></div>` },
  ], [['followers', 'Most followers'], ['score', 'Highest score'], ['name', 'Name'], ['oldest', 'Oldest first']], null,
  (q, p) => '/api/export/pages.csv' + qs({ run_id: p.run_id }));
  ROUTES.posts = dataView('posts', 'Posts', 'Posts collected from pages in your organization.', [
    { label: 'Post', cls: 'oa-trunc', render: r => `<span title="${attr(r.caption)}">${esc(r.caption || '(no text)')}</span>` },
    { label: 'Platform', render: r => esc(plat(r.platform)) },
    { label: 'Published', render: r => esc(r.published ? fmtDate(r.published) : '—') },
    { label: 'Likes', num: true, render: r => num(r.likes) },
    { label: 'Comments', num: true, render: r => num(r.comments) },
    { label: 'Scraped', render: r => r.comments_status ? pill(r.comments_status) : '<span class="oa-muted">—</span>' },
    { label: '', render: r => `<div class="oa-actions" style="justify-content:flex-end">${r.url ? extLink(r.url, 'Open') : ''}<a class="btn btn-secondary btn-sm" href="#comments?post_id=${attr(r.id)}">Comments</a></div>` },
  ], [['published', 'Recently published'], ['comments', 'Most comments'], ['likes', 'Most likes'], ['oldest', 'Oldest first']], null,
  (q, p) => '/api/org-admin/exports/posts.csv' + qs({ platform: p.platform, run_id: p.run_id }));
  ROUTES.comments = dataView('comments', 'Comments', 'Comments collected and analysed for leads.', [
    { label: 'Author', render: r => `<b>${esc(r.author || 'Unknown')}</b>` },
    { label: 'Comment', cls: 'oa-trunc', render: r => `<span title="${attr(r.text)}">${esc(r.text || '')}</span>` },
    { label: 'Platform', render: r => esc(plat(r.platform)) },
    { label: 'Lead', render: r => r.is_lead ? `${scoreTag(r.lead_score)} <a class="oa-link oa-small" href="#lead/${attr(r.lead_id)}">Open lead</a>` : (r.analysed ? '<span class="oa-muted oa-small">Not a lead</span>' : '<span class="oa-muted oa-small">Not analysed</span>') },
    { label: 'Published', render: r => esc(r.published ? fmtDate(r.published) : '—') },
  ], [['published', 'Recently published'], ['reactions', 'Most reactions'], ['oldest', 'Oldest first']], null,
  (q, p) => '/api/org-admin/exports/comments.csv' + qs({ platform: p.platform, run_id: p.run_id }));

  // ── Leads ──────────────────────────────────────────────────────────
  ROUTES.leads = {
    skel: 'table',
    nav: (p) => p[1] === 'assigned' ? 'leads/assigned' : 'leads',
    title: (p) => p[1] === 'assigned' ? 'Assigned leads' : 'All leads',
    async render(v) {
      const assignedView = v.parts[1] === 'assigned';
      const members = await getMembers(); if (!v.alive()) return;
      const assigneeOpts = (assignedView ? [] : [['unassigned', 'Unassigned'], ['assigned', 'Any assignee']]).concat(memberOptions(members));
      v.el.innerHTML = head(assignedView ? 'Assigned leads' : 'All leads', assignedView ? 'Leads assigned to members — they appear in each member\'s User Portal.' : 'Every lead found in your organization. Select leads to assign or move them in bulk, or open one for notes and history.',
        `<a class="btn btn-primary" href="#search">${ico('search')} New search</a>`) +
        tabs([['#leads', 'All leads'], ['#leads/assigned', 'Assigned'], ['#pipeline', 'Lifecycle'], ['#rules', 'Rules']], assignedView ? '#leads/assigned' : '#leads') + '<div data-host></div>';
      const init = Object.assign({}, v.query);
      if (assignedView && !init.assignee) init.assignee = 'assigned';
      const t = dataTable($('[data-host]', v.el), {
        url: '/api/org-admin/leads', caption: 'Leads', defaultSort: 'newest', initial: init,
        exportUrl: can('leads.export') && can('exports.create') ? (p) => '/api/org-admin/exports/leads.csv' + qs(Object.assign({}, p, { page: '', limit: '', sort: '' })) : null,
        export: can('leads.export'),
        bulk: can('leads.assign') || can('leads.manage') ? {
          rowId: r => r.id,
          actions: [].concat(can('leads.assign') ? [{ id: 'assign', label: 'Assign…' }] : [], can('leads.manage') ? [{ id: 'status', label: 'Change status…' }, { id: 'priority', label: 'Priority…' }] : [], can('leads.export') ? [{ id: 'export', label: 'Export selected' }] : []),
          run: (act, list, btn) => { if (act === 'export') { t.exportSelected(); return false; } return leadBulk(act, list, btn, members); },
        } : null,
        rowMenu: r => [{ act: 'open', label: 'Open lead' }].concat(can('leads.assign') ? [{ act: 'assign', label: r.assigned_user_id ? 'Reassign…' : 'Assign…' }] : [], can('leads.manage') ? [{ act: 'status', label: 'Change status…' }, { act: 'priority', label: 'Change priority…' }] : []),
        async onAction(act, r) { if (act === 'open') { location.hash = '#lead/' + r.id; return; } if (await leadBulk(act, [r], null, members)) t.reload(); },
        defaults: assignedView ? { assignee: 'assigned' } : {},
        fixed: { run_id: v.query.run_id },
        filters: [{ name: 'q', label: 'Search', placeholder: 'Search name, text, phone, email' },
          { name: 'status', label: 'Status', type: 'select', all: 'All statuses', options: LEAD_STATUSES.map(s => [s, label(s)]) },
          { name: 'assignee', label: 'Assignee', type: 'select', all: assignedView ? 'Any assignee' : 'Anyone', options: assigneeOpts },
          { name: 'platform', label: 'Platform', type: 'select', all: 'All platforms', options: PLATFORMS },
          { name: 'quality', label: 'Quality', type: 'select', all: 'Any quality', options: [['hot', 'Hot'], ['warm', 'Warm'], ['cold', 'Cold']] },
          { name: 'min_score', label: 'Min score', type: 'number' },
          { name: 'from', label: 'From date', type: 'date' }, { name: 'to', label: 'To date', type: 'date' }],
        columns: [
          { label: 'Lead', sort: 'name', dir: 'ascending', render: r => `<b>${esc(r.name)}</b><div class="oa-small oa-muted oa-trunc" style="max-width:280px">${esc(r.text)}</div>` },
          { label: 'Score', sort: 'score', render: r => scoreTag(r.score) },
          { label: 'Status', render: r => pill(r.status) },
          { label: 'Source', render: r => `${esc(plat(r.platform))}<div class="oa-small oa-muted oa-trunc" style="max-width:160px">${esc(r.page_name || '')}</div>` },
          { label: 'Assignee', render: r => r.assigned_user_id ? esc(r.assigned_name || r.assigned_email) : '<span class="oa-muted">Unassigned</span>' },
          { label: 'Found', sort: 'newest', render: r => timeTag(r.created_at) },
          { label: 'Updated', sort: 'updated', render: r => timeTag(r.updated_at) },
        ],
        rowHref: r => '#lead/' + r.id,
        empty: { title: assignedView ? 'No assigned leads' : 'No leads yet', desc: assignedView ? 'Open a lead and assign it to a member.' : 'Leads appear here as searches analyse comments.' },
      });
    },
  };
  // Assign / change status / change priority for one or many leads
  // (modal → one atomic POST /api/org-admin/leads/bulk; skipped leads are reported).
  async function leadBulk(act, list, btn, members) {
    if (['assign', 'status', 'priority'].indexOf(act) < 0) return false;
    const many = list.length > 1; const who = many ? num(list.length) + ' leads' : '“' + (list[0].name || 'this lead') + '”';
    const transitions = act === 'status' ? await getTransitions() : {};
    const choices = act === 'assign' ? [['', 'Unassigned']].concat(memberOptions(members, true))
      : act === 'priority' ? [['high', 'High'], ['medium', 'Medium'], ['low', 'Low']]
        : LEAD_STATUSES.filter(s => list.some(l => (transitions[l.status || 'new'] || []).indexOf(s) >= 0)).map(s => [s, cap(label(s))]);
    if (act === 'status' && !choices.length) { toast(many ? 'None of the selected leads can move to another status.' : 'This lead is in a final status.', 'info'); return false; }
    const title = { assign: 'Assign ', status: 'Change status of ', priority: 'Change priority of ' }[act] + who;
    const fieldLabel = { assign: 'Assign to', status: 'New status', priority: 'New priority' }[act];
    const hint = act === 'assign' ? 'Assigned leads appear in that member\'s User Portal.' : act === 'priority' ? 'Leads that already have this priority are skipped.' : (many ? 'Leads where this is not a valid next step are skipped.' : 'Only valid next steps are listed.');
    const current = !many ? (act === 'assign' ? (list[0].assigned_user_id || '') : act === 'priority' ? (list[0].priority || '') : '') : '';
    const res = await new Promise((resolve) => {
      let picked = null;
      openModal({
        title,
        body: `<form class="oa-form" data-f novalidate><div class="oa-field"><label for="bk-v">${fieldLabel}</label><select class="form-select" id="bk-v" name="v">${selectOpts(choices, current)}</select>
          <span class="oa-hint">${hint}</span></div>
          ${act === 'status' ? '<div class="oa-field"><label for="bk-r">Reason <span class="optional">(optional)</span></label><input class="form-input" id="bk-r" name="r" maxlength="300"/></div>' : ''}</form>`,
        foot: `<button type="button" class="btn btn-secondary" data-close>Cancel</button><button type="button" class="btn btn-primary" data-ok>${{ assign: 'Assign', status: 'Update status', priority: 'Update priority' }[act]}</button>`,
        onMount(root, close) {
          const f = $('[data-f]', root);
          const ok = () => { picked = { v: f.v.value, r: f.r ? f.r.value.trim() : '' }; close(); };
          $('[data-ok]', root).onclick = ok; f.onsubmit = (e) => { e.preventDefault(); ok(); };
          const obs = new MutationObserver(() => { if (!root.isConnected) { obs.disconnect(); resolve(picked); } });
          obs.observe($('#oa-modal-root'), { childList: true });
        },
      });
    });
    if (!res) return false;
    busy(btn, true, 'Updating…');
    try {
      return await runBulk('/api/org-admin/leads/bulk', { ids: list.map(l => l.id), action: act, value: res.v || null, reason: res.r || '' }, 'Updated', 'lead');
    } finally { busy(btn, false); }
  }
  ROUTES.lead = {
    nav: () => 'leads', title: () => 'Lead',
    async render(v) {
      const id = v.parts[1];
      const [lead, members, transitions] = await Promise.all([api('/api/leads/' + encodeURIComponent(id || '')), getMembers(), getTransitions()]);
      if (!v.alive()) return;
      const status = lead.lead_status || 'new';
      const next = (transitions[status] || []);
      const src = lead.source_post || {}; const page = lead.source_page || {};
      const notes = lead.notes || []; const hist = (lead.status_history || []).map(h => Object.assign({ kind: 'status' }, h))
        .concat((lead.assignment_history || []).map(h => Object.assign({ kind: 'assign' }, h)))
        .sort((a, b) => String(b.changed_at || '').localeCompare(String(a.changed_at || '')));
      const memberName = (uid, email) => { if (!uid) return 'Unassigned'; const m = (members || []).find(x => x.user_id === uid); return (m && (m.name || m.email)) || email || 'a member'; };
      const histMethod = (m) => String(m || '').indexOf('auto:') === 0 ? 'automatically (' + label(String(m).slice(5)) + ')' : m === 'bulk' ? 'bulk action' : '';
      const canManage = can('leads.manage'); const canAssign = can('leads.assign');
      const asg = (members || []).find(m => m.user_id === lead.assigned_user_id);
      const assigneeName = lead.assigned_user_id ? ((asg && (asg.name || asg.email)) || lead.assigned_to || 'Assigned') : 'Unassigned';
      v.el.innerHTML = head(lead.commenter_name || 'Lead', `${plat(lead.platform || '')} lead${page.page_name ? ' from ' + page.page_name : ''}`, '', ['#leads', 'Leads']) +
        `<div class="oa-grid oa-grid-3">
          <div class="oa-card oa-stat"><span class="oa-stat-label">Lead score</span><span class="oa-stat-value">${esc(lead.lead_score == null ? '—' : lead.lead_score)}</span><span class="oa-stat-meta">${esc(label(lead.lead_quality || ''))} ${lead.confidence != null ? '· ' + Math.round(lead.confidence * 100) + '% confidence' : ''}</span></div>
          <div class="oa-card oa-stat"><span class="oa-stat-label">Status</span><span style="margin-top:6px">${pill(status)}</span><span class="oa-stat-meta">Priority: ${esc(lead.lead_priority || lead.priority || '—')}</span></div>
          <div class="oa-card oa-stat"><span class="oa-stat-label">Assignee</span><span style="font-weight:700;font-size:1.1rem">${esc(assigneeName)}</span></div>
        </div>
        <div class="oa-grid oa-grid-2">
          <div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">Comment</div></div><p style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(lead.comment_text || '')}</p>
            <div style="margin-top:14px">${kv([['Intent', esc(label(lead.intent || '—'))], ['Requirement', esc(lead.requirement || '—')], ['Budget', esc(lead.budget || '—')], ['Location', esc(lead.location || '—')], ['Phone', esc(lead.phone || '—')], ['Email', esc(lead.email || '—')], ['WhatsApp', esc(lead.whatsapp || '—')], ['AI reason', esc(lead.reason || '—')]])}</div></div>
          <div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">Source</div></div>${kv([['Platform', esc(plat(lead.platform || '—'))], ['Page', esc(page.page_name || lead.page_name || '—')], ['Post', lead.post_url ? extLink(lead.post_url, 'Open post') : '—'], ['Post text', `<span class="oa-small">${esc((src.caption || '').slice(0, 240) || '—')}</span>`], ['Search', lead.search_run_id ? `<a class="oa-link" href="#searches/${attr(lead.search_run_id)}">View search</a>` : '—'], ['Found by', esc(lead.created_by || '—')], ['Found', esc(fmtDate(lead.lead_created_at || lead.analyzed_at, true))]])}</div>
        </div>
        ${canManage || canAssign ? `<form class="oa-card oa-form" data-manage><div class="oa-card-head"><div class="oa-card-title">Manage lead</div></div><div class="oa-form-grid">
          ${canManage ? `<div class="oa-field"><label for="l-status">Status</label><select class="form-select" id="l-status" name="lead_status">${selectOpts([[status, cap(label(status)) + ' (current)']].concat(next.map(s => [s, cap(label(s))])), status)}</select><span class="oa-hint">${next.length ? 'Only valid next steps are listed.' : 'This is a final status.'}</span></div>
          <div class="oa-field"><label for="l-priority">Priority</label><select class="form-select" id="l-priority" name="lead_priority">${selectOpts([['', '—'], ['high', 'High'], ['medium', 'Medium'], ['low', 'Low']], lead.lead_priority || '')}</select></div>
          <div class="oa-field span-2"><label for="l-reason">Reason for status change <span class="optional">(optional)</span></label><input class="form-input" id="l-reason" name="reason" maxlength="300"/></div>` : ''}
          ${canAssign ? `<div class="oa-field span-2"><label for="l-assignee">Assign to</label><select class="form-select" id="l-assignee" name="assigned_user_id">${selectOpts([['', 'Unassigned']].concat(memberOptions(members, true)), lead.assigned_user_id || '')}</select><span class="oa-hint">Assigned leads show up in that member's User Portal.</span></div>` : ''}
        </div><div class="oa-form-foot"><button type="submit" class="btn btn-primary">Save changes</button></div></form>` : ''}
        <div class="oa-grid oa-grid-2">
          <div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">Notes</div><span class="oa-card-sub">${num(notes.length)}</span></div>
            ${notes.length ? `<div class="oa-thread">${notes.map((n, i) => `<div class="oa-msg"><div class="oa-msg-head"><span>${esc(n.author || '')}</span><span>${esc(fmtDate(n.created_at, true))}${canManage ? ` · <button type="button" class="btn btn-ghost btn-sm" data-del-note="${i}" aria-label="Delete note">Delete</button>` : ''}</span></div><div class="oa-msg-body">${esc(n.text)}</div></div>`).join('')}</div>` : '<p class="oa-muted oa-small">No notes yet.</p>'}
            ${canManage ? `<form class="oa-form" data-note style="margin-top:14px"><label class="sr-only" for="l-note">Add a note</label><textarea class="form-textarea" id="l-note" name="text" rows="3" maxlength="2000" placeholder="Add a note for your team…" style="min-height:80px"></textarea><div class="oa-actions" style="justify-content:flex-end"><button type="submit" class="btn btn-secondary btn-sm">Add note</button></div></form>` : ''}</div>
          <div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">History</div></div>${hist.length ? `<ul class="oa-feed">${hist.map(h => h.kind === 'assign'
            ? `<li><div class="oa-feed-main">${h.to_user_id ? 'Assigned to <b>' + esc(memberName(h.to_user_id, h.to_email)) + '</b>' : 'Unassigned'} <span class="oa-small oa-muted">${h.changed_by === 'system' ? '' : 'by ' + esc(h.changed_by || '')}${histMethod(h.method) ? ' · ' + esc(histMethod(h.method)) : ''}</span></div>${timeTag(h.changed_at)}</li>`
            : `<li><div class="oa-feed-main">${pill(h.from_status)} → ${pill(h.to_status)} <span class="oa-small oa-muted">by ${esc(h.changed_by || '')}${h.reason ? ' — ' + esc(h.reason) : ''}</span></div>${timeTag(h.changed_at)}</li>`).join('')}</ul>` : '<p class="oa-muted oa-small">No status or assignment changes yet.</p>'}</div>
        </div>`;
      const mf = $('[data-manage]', v.el);
      if (mf) mf.onsubmit = async (e) => {
        e.preventDefault(); const body = {}; const f = mf.elements;
        if (f.lead_status && f.lead_status.value !== status) { body.lead_status = f.lead_status.value; body.reason = f.reason.value.trim(); }
        if (f.lead_priority && f.lead_priority.value && f.lead_priority.value !== (lead.lead_priority || '')) body.lead_priority = f.lead_priority.value;
        if (f.assigned_user_id && f.assigned_user_id.value !== (lead.assigned_user_id || '')) body.assigned_user_id = f.assigned_user_id.value || null;
        if (!Object.keys(body).length) { toast('Nothing changed.', 'info'); return; }
        const btn = $('button[type=submit]', mf); busy(btn, true, 'Saving…');
        try { await api('/api/leads/' + encodeURIComponent(id), { method: 'PATCH', body }); toast('Lead updated.', 'success'); route(); }
        catch (err) { toast(err.message, 'error'); busy(btn, false); }
      };
      const nf2 = $('[data-note]', v.el);
      if (nf2) nf2.onsubmit = async (e) => {
        e.preventDefault(); const text = nf2.text.value.trim(); if (!text) { nf2.text.focus(); return; }
        const btn = $('button', nf2); busy(btn, true, 'Adding…');
        try { await api('/api/leads/' + encodeURIComponent(id) + '/notes', { method: 'POST', body: { text } }); toast('Note added.', 'success'); route(); }
        catch (err) { toast(err.message, 'error'); busy(btn, false); }
      };
      $$('[data-del-note]', v.el).forEach(b => b.onclick = async () => {
        if (!(await confirmDialog('Delete note?', 'This note is removed for everyone.', { danger: true, confirm: 'Delete' }))) return;
        try { await api('/api/leads/' + encodeURIComponent(id) + '/notes/' + b.dataset.delNote, { method: 'DELETE' }); route(); }
        catch (err) { toast(err.message, 'error'); }
      });
    },
  };
  ROUTES.pipeline = {
    title: () => 'Lead lifecycle',
    async render(v) {
      const d = await api('/api/org-admin/leads/pipeline'); if (!v.alive()) return;
      S.transitions = d.transitions;
      v.el.innerHTML = head('Lead lifecycle', 'Where your leads are in the pipeline. Click a stage to see its leads.') +
        tabs([['#leads', 'All leads'], ['#leads/assigned', 'Assigned'], ['#pipeline', 'Lifecycle'], ['#rules', 'Rules']], '#pipeline') +
        (d.total ? `<div class="oa-pipeline">${Object.entries(d.statuses).map(([s, n]) => `<a class="oa-stage" href="#leads?status=${attr(s)}">${pill(s)}<b>${num(n)}</b><span class="oa-small oa-muted">${d.total ? Math.round(n / d.total * 100) : 0}% of leads</span></a>`).join('')}</div>
        <div class="oa-grid oa-grid-2">${hbars('Leads by owner', Object.fromEntries(d.owners.map(o => [o.name || o.email || 'Unknown', o.leads])))}
        <div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">Allowed transitions</div></div><ul class="oa-feed">${Object.entries(d.transitions).map(([k, arr]) => `<li><div class="oa-feed-main">${pill(k)} → ${arr.length ? arr.map(a => pill(a)).join(' ') : '<span class="oa-muted oa-small">final</span>'}</div><span></span></li>`).join('')}</ul></div></div>`
          : `<div class="oa-card">${stateHtml('empty', 'No leads yet', 'Once searches find leads you will see them move through the pipeline here.', '<a class="btn btn-primary" href="#search">Start a search</a>')}</div>`);
    },
  };
  ROUTES.rules = {
    title: () => 'Lead rules',
    async render(v) {
      const d = await api('/api/org-admin/lead-rules'); if (!v.alive()) return;
      const edit = d.can_edit;
      const g = d.global_rule;
      v.el.innerHTML = head('Lead rules', 'Keywords that decide which comments are analysed as potential leads for your organization.') +
        tabs([['#leads', 'All leads'], ['#leads/assigned', 'Assigned'], ['#pipeline', 'Lifecycle'], ['#rules', 'Rules']], '#rules') +
        `<div class="alert ${d.using_defaults ? 'alert-info' : 'alert-success'}" style="margin-bottom:16px">${ico('info', 'alert-icon')}<div class="alert-body">${d.using_defaults ? `<div class="alert-title">Using LeadAI's global defaults</div>${g ? 'Default rule “' + esc(g.name || 'Active rule') + '”' + (g.include_keywords.length ? ': ' + esc(g.include_keywords.slice(0, 15).join(', ')) + (g.include_keywords.length > 15 ? '…' : '') : '') : 'No global filter is active, so every comment is analysed.'} Add your own keywords below to tailor lead detection to your business.` : '<div class="alert-title">Your organization\'s keywords are active</div>New searches only analyse comments containing at least one of these keywords (plus comments with contact details). Remove all keywords to go back to the global defaults.'}</div></div>
        <div class="oa-grid oa-grid-2"><form class="oa-card oa-form" data-f>
          <div class="oa-field"><span class="oa-label" id="kw-l">Lead keywords</span><div class="oa-chips" data-chips="keywords" aria-labelledby="kw-l"></div><span class="oa-hint">Press Enter or comma to add. Example: property, buy, rent, house, apartment, price, interested.</span></div>
          <div class="oa-field"><span class="oa-label" id="ex-l">Exclude keywords <span class="optional">(optional)</span></span><div class="oa-chips" data-chips="exclude" aria-labelledby="ex-l"></div><span class="oa-hint">Comments containing any of these are never treated as leads (e.g. spam, giveaway).</span></div>
          ${edit ? '<div class="oa-form-foot"><button type="button" class="btn btn-ghost" data-clear>Use global defaults</button><button type="submit" class="btn btn-primary">Save rules</button></div>' : readonlyNote()}
        </form>
        <form class="oa-card oa-form" data-test><div class="oa-card-head"><div class="oa-card-title">Test a comment</div></div><label class="sr-only" for="rt">Comment text</label><textarea class="form-textarea" id="rt" name="text" rows="4" maxlength="2000" placeholder="e.g. Is this apartment still available? What's the price?" style="min-height:100px"></textarea><div class="oa-actions" style="justify-content:flex-end"><button type="submit" class="btn btn-secondary">Test against saved rules</button></div><div data-res aria-live="polite"></div></form></div>`;
      const chips = {};
      $$('[data-chips]', v.el).forEach(box => { chips[box.dataset.chips] = chipEditor(box, box.dataset.chips === 'keywords' ? d.keywords : d.exclude_keywords, !edit, box.dataset.chips === 'exclude'); });
      const f = $('[data-f]', v.el);
      const save = async (kws, ex, btn) => {
        busy(btn, true, 'Saving…');
        try { const r = await api('/api/org-admin/lead-rules', { method: 'PUT', body: { keywords: kws, exclude_keywords: ex } }); toast(r.message, 'success'); route(); }
        catch (e) { toast(e.message, 'error'); busy(btn, false); }
      };
      if (edit) {
        f.onsubmit = (e) => { e.preventDefault(); save(chips.keywords.values(), chips.exclude.values(), $('button[type=submit]', f)); };
        $('[data-clear]', f).onclick = async (e) => { if (await confirmDialog('Use global defaults?', 'Your organization keywords are removed and LeadAI\'s default lead detection applies.', { confirm: 'Use defaults' })) save([], [], e.currentTarget); };
      }
      const tf = $('[data-test]', v.el);
      tf.onsubmit = async (e) => {
        e.preventDefault(); const text = tf.text.value.trim(); if (!text) { tf.text.focus(); return; }
        try {
          const r = await api('/api/org-admin/lead-rules/test', { method: 'POST', body: { text } });
          $('[data-res]', tf).innerHTML = `<div class="alert ${r.matched ? 'alert-success' : 'alert-warning'}">${ico('info', 'alert-icon')}<div class="alert-body"><div class="alert-title">${r.matched ? 'Would be analysed as a potential lead' : 'Would be skipped'}</div><div class="oa-small">Rules: ${esc(r.source)}${r.matched_keywords && r.matched_keywords.length ? ' · matched: ' + esc(r.matched_keywords.join(', ')) : ''}${r.excluded_keywords && r.excluded_keywords.length ? ' · excluded by: ' + esc(r.excluded_keywords.join(', ')) : ''}</div></div></div>`;
        } catch (err) { toast(err.message, 'error'); }
      };
    },
  };
  function chipEditor(box, initial, readonly, danger) {
    let vals = (initial || []).slice();
    const draw = () => {
      box.innerHTML = vals.map((k, i) => `<span class="oa-chip ${danger ? 'x' : ''}">${esc(k)}${readonly ? '' : `<button type="button" data-rm="${i}" aria-label="Remove ${attr(k)}">×</button>`}</span>`).join('') +
        (readonly ? (vals.length ? '' : '<span class="oa-muted oa-small">None</span>') : `<input type="text" maxlength="80" aria-label="Add keyword" placeholder="${vals.length ? 'Add…' : 'Type a keyword and press Enter'}"/>`);
      const inp = $('input', box);
      if (inp) {
        inp.addEventListener('keydown', (e) => {
          if ((e.key === 'Enter' || e.key === ',') && inp.value.trim()) { e.preventDefault(); add(inp.value); }
          else if (e.key === 'Backspace' && !inp.value && vals.length) { vals.pop(); draw(); $('input', box).focus(); }
        });
        inp.addEventListener('paste', (e) => { const t = (e.clipboardData || window.clipboardData).getData('text'); if (t && t.indexOf(',') >= 0) { e.preventDefault(); t.split(',').forEach(x => add(x, true)); draw(); $('input', box).focus(); } });
        inp.addEventListener('blur', () => { if (inp.value.trim()) add(inp.value); });
      }
    };
    const add = (raw, silent) => {
      const k = raw.replace(/,/g, ' ').trim().toLowerCase().slice(0, 80);
      if (k && vals.indexOf(k) < 0 && vals.length < 200) vals.push(k);
      if (!silent) { draw(); const i = $('input', box); if (i) i.focus(); }
    };
    box.addEventListener('click', (e) => { const b = e.target.closest('[data-rm]'); if (b) { vals.splice(Number(b.dataset.rm), 1); draw(); const i = $('input', box); if (i) i.focus(); } else if (e.target === box) { const i = $('input', box); if (i) i.focus(); } });
    draw();
    return { values: () => { const i = $('input', box); if (i && i.value.trim()) add(i.value, true); return vals.slice(); } };
  }

  // ── Apify ──────────────────────────────────────────────────────────
  ROUTES.apify = {
    title: () => 'Apify jobs',
    async render(v) {
      const tab = v.parts[1] === 'runs' ? 'runs' : 'jobs';
      const s = await api('/api/org-admin/apify/summary'); if (!v.alive()) return;
      const j = s.jobs || {};
      v.el.innerHTML = head('Apify jobs', 'Scraping activity for your organization. View only — scraping infrastructure is managed by LeadAI.') +
        `<div class="oa-grid oa-grid-4">${[['Running', j.running || 0, 'running'], ['Completed', j.completed || 0, 'completed'], ['Failed', (j.error || 0) + (j.failed || 0), 'error'], ['Cancelled', j.cancelled || 0, 'cancelled']].map(([k, n, st]) =>
          `<a class="oa-card oa-stat" href="#apify?status=${attr(st)}"><span class="oa-stat-label">${pill(st, k)}</span><span class="oa-stat-value">${num(n)}</span></a>`).join('')}</div>
        <div class="oa-grid oa-grid-3">${[['Pages scraped', s.pages, '#pages'], ['Posts scraped', s.posts, '#posts'], ['Comments scraped', s.comments, '#comments']].map(([k, n, h]) => `<a class="oa-card oa-stat" href="${h}"><span class="oa-stat-label">${esc(k)}</span><span class="oa-stat-value">${num(n)}</span><span class="oa-stat-meta">Browse →</span></a>`).join('')}</div>
        <div style="margin-top:20px">${tabs([['#apify', 'Scrape jobs'], ['#apify/runs', 'Actor runs']], tab === 'runs' ? '#apify/runs' : '#apify')}</div><div data-host></div>`;
      if (tab === 'jobs') {
        dataTable($('[data-host]', v.el), {
          url: '/api/org-admin/apify/jobs', caption: 'Scrape jobs', initial: v.query,
          filters: [{ name: 'q', label: 'Search', placeholder: 'Search URL or run id' },
            { name: 'status', label: 'Status', type: 'select', all: 'All statuses', options: [['running', 'Running'], ['completed', 'Completed'], ['error', 'Failed'], ['cancelled', 'Cancelled']] },
            { name: 'platform', label: 'Platform', type: 'select', all: 'All platforms', options: PLATFORMS },
            { name: 'from', label: 'From date', type: 'date' }, { name: 'to', label: 'To date', type: 'date' }],
          columns: [
            { label: 'Started', render: r => timeTag(r.created_at) },
            { label: 'Target', cls: 'oa-trunc', render: r => `<span title="${attr(r.url)}">${esc(r.url || '—')}</span>` },
            { label: 'Platform', render: r => esc(plat(r.platform)) },
            { label: 'Status', render: r => pill(r.status) },
            { label: 'Phase', render: r => esc(label(r.phase || '—')) },
            { label: 'Apify run', render: r => r.apify_run_id ? `<span class="oa-mono">${esc(r.apify_run_id)}</span>` : '<span class="oa-muted">—</span>' },
            { label: 'Duration', num: true, render: r => r.duration_seconds != null ? esc(r.duration_seconds + ' s') : '—' },
            { label: 'Error', cls: 'oa-trunc', render: r => r.error ? `<span class="oa-small" style="color:var(--danger-text)" title="${attr(r.error)}">${esc(r.error)}</span>` : '' },
          ],
          rowHref: r => '#searches/' + r.run_id,
          empty: { title: 'No scrape jobs yet', desc: 'Jobs start when a member runs a URL search.' },
        });
      } else {
        dataTable($('[data-host]', v.el), {
          url: '/api/org-admin/apify/runs', caption: 'Actor runs',
          filters: [{ name: 'status', label: 'Status', type: 'select', all: 'All statuses', options: [['running', 'Running'], ['succeeded', 'Succeeded'], ['failed', 'Failed'], ['aborted', 'Aborted']] }],
          columns: [
            { label: 'Created', render: r => timeTag(r.created_at) }, { label: 'Actor', render: r => `<span class="oa-mono">${esc(r.actor_id || '—')}</span>` },
            { label: 'Platform', render: r => esc(plat(r.platform || '—')) }, { label: 'Status', render: r => pill(r.status) },
            { label: 'Items', num: true, render: r => num(r.items_count != null ? r.items_count : r.items) },
            { label: 'Search', render: r => r.search_run_id ? `<a class="oa-link" href="#searches/${attr(r.search_run_id)}">View</a>` : '—' },
          ],
          empty: { title: 'No actor runs recorded', desc: 'Low-level Apify actor runs for your organization appear here when recorded.' },
        });
      }
    },
  };

  // ── Analytics ──────────────────────────────────────────────────────
  ROUTES.analytics = {
    title: () => 'Analytics & reports',
    async render(v) {
      const today = new Date(); const iso = (d) => d.toISOString().slice(0, 10);
      const preset = v.query.range || '30';
      let from = v.query.from, to = v.query.to;
      if (!from || preset !== 'custom') { const f = new Date(today); f.setDate(f.getDate() - (Number(preset) || 30) + 1); from = iso(f); to = iso(today); }
      const d = await api('/api/org-admin/analytics' + qs({ from, to })); if (!v.alive()) return;
      const t = d.totals;
      const seg = [['7', '7 days'], ['30', '30 days'], ['90', '90 days'], ['365', '12 months']];
      v.el.innerHTML = head('Analytics & reports', `${fmtDate(d.from)} – ${fmtDate(d.to)}`, `<button type="button" class="btn btn-secondary" data-usage>${ico('download')} Usage report</button>`) +
        `<form class="oa-toolbar" data-range><div class="oa-seg" role="group" aria-label="Date range">${seg.map(([k, l]) => `<button type="button" data-p="${k}" aria-pressed="${preset === k}">${l}</button>`).join('')}</div>
          <label class="sr-only" for="an-from">From</label><input class="form-input" type="date" id="an-from" name="from" value="${attr(d.from)}"/><label class="sr-only" for="an-to">To</label><input class="form-input" type="date" id="an-to" name="to" value="${attr(d.to)}"/><button type="submit" class="btn btn-secondary">Apply</button></form>
        <div class="oa-grid oa-grid-4">${[['Searches', t.searches], ['Leads', t.leads], ['Tokens used', t.tokens], ['New users', t.new_users]].map(([k, n]) => `<div class="oa-card oa-stat"><span class="oa-stat-label">${esc(k)}</span><span class="oa-stat-value">${num(n)}</span></div>`).join('')}</div>
        <div class="oa-grid oa-grid-2">${columnChart('Searches per day', d.series.searches, d.days)}${columnChart('Leads per day', d.series.leads, d.days)}</div>
        <div class="oa-grid oa-grid-2">${columnChart('Tokens consumed per day', d.series.tokens, d.days)}${columnChart('AI requests per day', d.series.ai_requests, d.days)}</div>
        <h2 class="oa-section-title" style="margin:24px 0 12px">Leads</h2>
        <div class="oa-grid oa-grid-3">${hbars('By source platform', d.leads.by_platform)}${hbars('By lifecycle status', d.leads.by_status, { emptyKey: 'new' })}${hbars('By owner (assignee)', d.leads.by_owner)}</div>
        <h2 class="oa-section-title" style="margin:24px 0 12px">Searches & users</h2>
        <div class="oa-grid oa-grid-3">${hbars('Searches by status', d.searches.by_status)}${hbars('Searches by platform', d.searches.by_platform, { emptyKey: 'unknown' })}${hbars('Users by role', Object.fromEntries(Object.entries(d.users.by_role).map(([k, n]) => [ROLE_LABELS[k] || k, n])))}</div>
        <div class="oa-grid oa-grid-2"><div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">Usage in period</div></div>${kv([['Posts scraped', num(d.usage.posts)], ['Comments scraped', num(d.usage.comments)], ['AI analyses', num(d.usage.ai_analyses)], ['AI tokens', num(d.usage.ai_tokens)], ['Tokens consumed', num(d.usage.tokens_consumed)], ['Active users', num(d.users.active) + ' of ' + num(d.users.total)]])}</div>
        <div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">Top users</div><span class="oa-card-sub">By searches in this period</span></div><div data-top></div></div></div>`;
      wireCharts(v.el);
      dataTable($('[data-top]', v.el), {
        local: d.top_users || [], caption: 'Top users', limit: 10, key: 'an-top', defaultSort: 'searches', defaults: { order: 'desc' },
        columns: [
          { label: 'User', sortVal: u => String(u.name || '').toLowerCase(), render: u => `<a class="oa-link" href="#team/user/${attr(u.user_id)}">${esc(u.name)}</a>`, csv: u => u.name },
          { label: 'Searches', num: true, sortVal: u => u.searches || 0, render: u => num(u.searches) },
          { label: 'Leads', num: true, sortVal: u => u.leads || 0, render: u => num(u.leads) },
        ],
        rowHref: u => '#team/user/' + u.user_id,
        empty: { title: 'No activity in this period', desc: 'Try a longer date range.' },
      });
      const rf = $('[data-range]', v.el);
      $$('[data-p]', rf).forEach(b => b.onclick = () => { location.hash = '#analytics?range=' + b.dataset.p; });
      rf.onsubmit = (e) => { e.preventDefault(); if (rf.from.value && rf.to.value) location.hash = '#analytics' + qs({ range: 'custom', from: rf.from.value, to: rf.to.value }); };
      $('[data-usage]', v.el).onclick = (e) => download('/api/org-admin/exports/usage.csv', e.currentTarget);
    },
  };

  // ── Subscription ───────────────────────────────────────────────────
  ROUTES.subscription = {
    title: () => 'Subscription',
    async render(v) {
      const res = await Promise.allSettled([api('/api/billing/subscription'), api('/api/billing/usage'), api('/api/billing/plans'), api('/api/billing/invoices'), api('/api/org-admin/billing/history')]);
      if (!v.alive()) return;
      if (res[0].status === 'rejected') throw res[0].reason;
      const sub = res[0].value.subscription || {};
      const usage = res[1].status === 'fulfilled' ? res[1].value.usage : null;
      const plans = res[2].status === 'fulfilled' ? res[2].value.plans : [];
      const invoices = res[3].status === 'fulfilled' ? res[3].value.invoices : [];
      const hist = res[4].status === 'fulfilled' ? res[4].value : { items: [], payments: [] };
      const plan = sub.plan || (usage && usage.plan) || {};
      const active = !!sub.has_subscription && sub.status === 'active';
      const pending = sub.pending;
      const manage = can('org_billing.manage');
      const cycleKey = 'oa_cycle';
      let cycle = 'monthly'; try { cycle = sessionStorage.getItem(cycleKey) || 'monthly'; } catch (_) { /* */ }
      const metricNames = { team_members: 'Users', monthly_searches: 'Searches', monthly_posts: 'Posts', monthly_comments: 'Comments', monthly_ai_analyses: 'AI analyses', monthly_exports: 'Exports', api_requests: 'API requests' };
      const money = (a, c) => a == null ? '—' : (Number(a) === 0 ? 'Free' : new Intl.NumberFormat(undefined, { style: 'currency', currency: (c || 'USD').toUpperCase() }).format(a));
      const subState = sub.has_subscription ? (sub.status || 'none') : (sub.is_demo ? 'demo' : 'none');
      const statusText = active ? 'Active' : subState === 'none' ? 'No subscription' : label(subState);
      v.el.innerHTML = head('Subscription', 'Your plan, usage against its limits, invoices and billing history.') +
        (pending ? `<div class="alert alert-warning" style="margin-bottom:16px">${ico('alert', 'alert-icon')}<div class="alert-body"><div class="alert-title">Plan change pending confirmation</div>${esc((pending.plan && pending.plan.name) || pending.plan_id)} (${esc(pending.billing_cycle || '')}) — status: ${pill(pending.status)}. ${pending.status === 'pending_payment' ? 'Complete the payment to continue.' : 'Our team confirms payments and activates your new plan shortly.'}${pending.checkout_session_id ? ` <a href="/billing/status?session=${encodeURIComponent(pending.checkout_session_id)}">View payment status</a>` : ''}</div></div>` : '') +
        `<div class="oa-grid oa-grid-3">
          <div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">Current plan</div>${pill(subState === 'active' && !active ? 'pending' : subState, statusText)}</div>
            <div style="font-size:1.5rem;font-weight:800">${esc(plan.name || 'No plan')}</div>
            <div style="margin-top:12px">${kv([['Billing', esc(label(sub.billing_cycle || (sub.is_demo ? 'demo' : '—')))], ['Started', esc(fmtDate(sub.started_at || sub.current_period_start))], [sub.cancel_at_period_end ? 'Ends' : 'Renews', esc(fmtDate(sub.current_period_end))], ['Amount', esc(sub.amount != null ? money(sub.amount, sub.currency) : '—')]])}</div>
            ${sub.cancel_at_period_end ? `<div class="alert alert-warning" style="margin-top:12px">${ico('alert', 'alert-icon')}<div class="alert-body">Cancellation scheduled for the end of this period.</div></div>` : ''}
            ${manage && active ? `<div class="oa-actions" style="margin-top:14px">${sub.cancel_at_period_end ? '<button type="button" class="btn btn-secondary btn-sm" data-reactivate>Keep my subscription</button>' : (isOwner() ? '<button type="button" class="btn btn-danger btn-sm" data-cancel>Request cancellation</button>' : '<span class="oa-hint">Only the organization owner can cancel.</span>')}</div>` : ''}</div>
          <div class="oa-card" style="grid-column:span 2"><div class="oa-card-head"><div class="oa-card-title">Usage vs limits</div><span class="oa-card-sub">${usage && usage.period && usage.period.end ? 'Resets ' + esc(fmtDate(usage.period.end)) : ''}</span></div>
            ${usage ? Object.entries(usage.metrics || {}).filter(([, m]) => m.limit).map(([k, m]) => meter(metricNames[k] || label(k), m.used, m.limit, m.percentage)).join('') || '<p class="oa-muted oa-small">Your plan has no metered limits.</p>' : '<p class="oa-muted oa-small">Usage unavailable.</p>'}
            ${usage && usage.tokens ? meter('Tokens', usage.tokens.used, usage.tokens.allocated) + `<p class="oa-hint" style="margin-top:6px">${num(usage.tokens.remaining)} tokens remaining</p>` : ''}</div>
        </div>
        <div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">Plans</div><div class="oa-seg" role="group" aria-label="Billing cycle"><button type="button" data-cycle="monthly" aria-pressed="${cycle === 'monthly'}">Monthly</button><button type="button" data-cycle="yearly" aria-pressed="${cycle === 'yearly'}">Yearly</button></div></div>
          ${plans.length ? `<div class="oa-plans">${plans.map(p => {
            const price = cycle === 'yearly' ? p.price_yearly : p.price_monthly;
            const isCur = active && (p.slug === sub.plan_id || p.slug === plan.slug);
            const isPend = pending && pending.plan_id === p.slug;
            const hl = Array.isArray(p.highlights) && p.highlights.length ? p.highlights : Object.entries(p.limits || {}).filter(([, n]) => n).slice(0, 6).map(([k, n]) => num(n) + ' ' + label(k).replace(/^monthly /, '') + (k.indexOf('monthly') === 0 ? ' / month' : ''));
            return `<div class="oa-plan ${isCur ? 'current' : ''}"><div style="display:flex;justify-content:space-between;gap:8px;align-items:center"><b>${esc(p.name)}</b>${isCur ? pill('active', 'Current') : isPend ? pill('pending', 'Pending') : ''}</div>
              <div class="oa-plan-price">${esc(money(price, p.currency))}${price ? `<small> / ${cycle === 'yearly' ? 'year' : 'month'}</small>` : ''}</div>${p.description ? `<p class="oa-small oa-muted">${esc(p.description)}</p>` : ''}
              <ul>${hl.map(h => `<li>${esc(h)}</li>`).join('')}</ul>
              ${manage ? (isCur ? '<button type="button" class="btn btn-secondary" disabled>Current plan</button>' : isPend ? '<button type="button" class="btn btn-secondary" disabled>Awaiting confirmation</button>' : price ? `<button type="button" class="btn btn-primary" data-plan="${attr(p.slug)}" data-name="${attr(p.name)}">${active ? 'Switch to ' + esc(p.name) : 'Choose ' + esc(p.name)}</button>` : '<button type="button" class="btn btn-secondary" disabled>Not purchasable</button>') : ''}</div>`;
          }).join('')}</div>${manage ? '' : '<p class="oa-hint" style="margin-top:10px">Changing the plan requires the billing permission.</p>'}` : stateHtml('empty', 'No plans available', 'Contact support to change your plan.')}</div>
        <h2 class="oa-section-title" style="margin:24px 0 12px">Invoices</h2><div data-inv></div>
        <h2 class="oa-section-title" style="margin:24px 0 12px">Payments</h2><div data-pay></div>
        <h2 class="oa-section-title" style="margin:24px 0 12px">Billing history</h2><div data-hist></div>`;
      const ts = (d) => { const x = toDate(d); return x ? x.getTime() : 0; };
      dataTable($('[data-inv]', v.el), {
        local: invoices, caption: 'Invoices', limit: 10, key: 'sub-invoices', defaultSort: 'date', defaults: { order: 'desc' },
        filters: [{ name: 'q', label: 'Search', placeholder: 'Search invoices' }],
        columns: [
          { label: 'Invoice', render: i => safeUrl(i.pdf_url || i.hosted_url) ? extLink(i.pdf_url || i.hosted_url, i.number || i.invoice_number || 'Invoice') : esc(i.number || i.invoice_number || i.id), csv: i => i.number || i.invoice_number || i.id },
          { label: 'Date', id: 'date', sortVal: i => ts(i.invoice_date || i.created_at), render: i => esc(fmtDate(i.invoice_date || i.created_at)) },
          { label: 'Amount', num: true, sortVal: i => Number(i.amount || i.total) || 0, render: i => esc(money(i.amount || i.total, i.currency)) },
          { label: 'Status', sortVal: i => i.status || '', render: i => pill(i.status) },
        ],
        empty: { title: 'No invoices yet', desc: 'Invoices appear here once a paid plan is confirmed.' },
      });
      dataTable($('[data-pay]', v.el), {
        local: hist.payments || [], caption: 'Payments', limit: 10, key: 'sub-payments', defaultSort: 'date', defaults: { order: 'desc' },
        filters: [{ name: 'status', label: 'Status', type: 'select', all: 'All statuses', options: Array.from(new Set((hist.payments || []).map(p => p.status).filter(Boolean))).map(s => [s, cap(label(s))]) }],
        columns: [
          { label: 'Date', id: 'date', sortVal: p => ts(p.created_at), render: p => esc(fmtDate(p.created_at, true)) },
          { label: 'Amount', num: true, sortVal: p => Number(p.amount) || 0, render: p => esc(money(p.amount, p.currency)) },
          { label: 'Status', sortVal: p => p.status || '', render: p => pill(p.status) },
        ],
        empty: { title: 'No payments yet', desc: 'Payments you make for plan changes are listed here.' },
      });
      dataTable($('[data-hist]', v.el), {
        local: hist.items || [], caption: 'Billing history', limit: 10, key: 'sub-history', defaultSort: 'requested', defaults: { order: 'desc' },
        filters: [{ name: 'q', label: 'Search', placeholder: 'Search plan' }, { name: 'status', label: 'Status', type: 'select', all: 'All statuses', options: Array.from(new Set((hist.items || []).map(h => h.status).filter(Boolean))).map(s => [s, cap(label(s))]) }],
        columns: [
          { label: 'Requested', id: 'requested', sortVal: h => ts(h.created_at), render: h => esc(fmtDate(h.created_at, true)) },
          { label: 'Plan', sortVal: h => h.plan_id || '', render: h => esc(h.plan_id) },
          { label: 'Cycle', render: h => esc(label(h.billing_cycle || '—')) },
          { label: 'Amount', num: true, sortVal: h => Number(h.amount) || 0, render: h => esc(money(h.amount, h.currency)) },
          { label: 'Status', sortVal: h => h.status || '', render: h => pill(h.status) },
          { label: 'Period end', sortVal: h => ts(h.current_period_end), render: h => esc(fmtDate(h.current_period_end)) },
        ],
        empty: { title: 'No subscription changes yet', desc: 'Plan requests, upgrades and renewals are recorded here.' },
      });
      $$('[data-cycle]', v.el).forEach(b => b.onclick = () => { try { sessionStorage.setItem(cycleKey, b.dataset.cycle); } catch (_) { /* */ } route(); });
      $$('[data-plan]', v.el).forEach(b => b.onclick = async () => {
        if (!(await confirmDialog(`Choose ${b.dataset.name}?`, `You'll be taken to checkout for the ${cycle} ${b.dataset.name} plan. Your new plan activates after payment is confirmed by our team.`, { confirm: 'Continue to checkout' }))) return;
        busy(b, true, 'Starting checkout…');
        try {
          const r = await api('/api/billing/checkout', { method: 'POST', body: { plan_slug: b.dataset.plan, billing_cycle: cycle } });
          const dest = (r.checkout && (r.checkout.redirect_url || r.checkout.url)) || '';
          const safe = safeUrl(dest);
          if (safe) location.href = safe; else { toast('Checkout created — awaiting confirmation.', 'success'); route(); }
        } catch (e) { toast(e.message, 'error'); busy(b, false); }
      });
      const c = $('[data-cancel]', v.el);
      if (c) c.onclick = async () => {
        if (!(await confirmDialog('Request cancellation?', 'Your subscription stays active until the end of the current period, then it ends. You can undo this before then.', { danger: true, confirm: 'Request cancellation' }))) return;
        busy(c, true, 'Requesting…');
        try { const r = await api('/api/billing/cancel', { method: 'POST' }); toast(r.message, 'success'); route(); } catch (e) { toast(e.message, 'error'); busy(c, false); }
      };
      const re = $('[data-reactivate]', v.el);
      if (re) re.onclick = async () => { busy(re, true, 'Saving…'); try { const r = await api('/api/billing/reactivate', { method: 'POST' }); toast(r.message, 'success'); route(); } catch (e) { toast(e.message, 'error'); busy(re, false); } };
    },
  };

  // ── Exports ────────────────────────────────────────────────────────
  ROUTES.exports = {
    skel: 'table',
    async render(v) {
      const items = [
        ['leads', 'Leads', 'All leads with score, status, source, assignee and contact details.', 'leads.export', true],
        ['searches', 'Search results', 'Every search run with status, platform, results and errors.', 'search.export', true],
        ['pages', 'Pages', 'All scraped pages and profiles.', 'search.export', false, '/api/export/pages.csv'],
        ['posts', 'Posts', 'All scraped posts.', 'search.export', false],
        ['comments', 'Comments', 'All scraped comments with lead flags.', 'search.export', false],
        ['users', 'Users', 'Team members with role, status, last login and usage.', 'members.view', false],
        ['activity', 'User activity', 'The organization audit trail for the date range.', 'org_audit.view', true],
        ['usage', 'Usage report', 'Current usage against every plan limit and tokens.', 'org_billing.view', false],
      ];
      v.el.innerHTML = head('Exports', 'Download your organization\'s data as CSV. Every export is logged.') +
        `<div class="oa-card"><form class="oa-toolbar" data-range style="margin:0"><span class="oa-label">Date range for leads, searches and activity:</span><label class="sr-only" for="ex-from">From</label><input class="form-input" type="date" id="ex-from" name="from"/><label class="sr-only" for="ex-to">To</label><input class="form-input" type="date" id="ex-to" name="to"/><span class="oa-hint">Leave empty for all time.</span></form></div>
        <div class="oa-grid oa-grid-4">${items.map(([k, t, d, perm, ranged]) => `<div class="oa-card" style="display:flex;flex-direction:column;gap:8px"><div class="oa-card-title">${esc(t)}</div><p class="oa-small oa-muted" style="flex:1">${esc(d)}${ranged ? ' Uses the date range.' : ''}</p><button type="button" class="btn btn-secondary btn-sm" data-dl="${attr(k)}" ${can(perm) && can('exports.create') ? '' : 'disabled title="You don\'t have permission for this export"'}>${ico('download')} Download CSV</button></div>`).join('')}</div>
        <h2 class="oa-section-title" style="margin:24px 0 12px">Export history</h2><div data-host></div>`;
      const rf = $('[data-range]', v.el);
      $$('[data-dl]', v.el).forEach(b => b.onclick = () => {
        const it = items.find(i => i[0] === b.dataset.dl);
        const range = it[4] ? { from: rf.from.value, to: rf.to.value } : {};
        const url = it[5] || '/api/org-admin/exports/' + it[0] + '.csv';
        download(url + qs(range), b).then(() => hist.reload());
      });
      const hist = dataTable($('[data-host]', v.el), {
        url: '/api/org-admin/exports', caption: 'Export history',
        filters: [{ name: 'scope', label: 'Type', type: 'select', all: 'All types', options: items.map(i => [i[0], i[1]]).concat([['audit_logs', 'Audit logs']]) }],
        columns: [{ label: 'When', render: r => timeTag(r.created_at) }, { label: 'Type', render: r => esc(label(r.scope)) }, { label: 'By', render: r => esc(r.user_email || '—') },
          { label: 'Rows', num: true, render: r => num(r.rows) }, { label: 'Source', render: r => esc(label(r.source)) }, { label: 'Status', render: r => pill(r.status) }],
        empty: { title: 'No exports yet', desc: 'Downloads from this portal and the User Portal appear here.' },
      });
    },
  };

  // ── Notifications ──────────────────────────────────────────────────
  ROUTES.notifications = {
    skel: 'table',
    async render(v) {
      const org = await getOrg(); if (!v.alive()) return;
      const s = org.settings || {}; const edit = can('settings.manage');
      v.el.innerHTML = head('Notifications', 'Alerts for your organization and for you.', '<button type="button" class="btn btn-secondary" data-readall>Mark all as read</button>') +
        `<div class="oa-grid" style="grid-template-columns:minmax(0,2fr) minmax(0,1fr);align-items:start" data-cols><div data-host></div>
        <form class="oa-card" data-settings><div class="oa-card-head"><div class="oa-card-title">Organization notification settings</div></div>
          ${switchRow('email_notifications', 'Email notifications', 'Also send alerts by email.', s.email_notifications !== false, !edit)}
          ${switchRow('notify_usage_warnings', 'Usage warnings', 'Warn before limits or tokens run out.', s.notify_usage_warnings !== false, !edit)}
          ${switchRow('notify_job_completion', 'Job completion', 'Notify when searches finish or fail.', s.notify_job_completion !== false, !edit)}
          ${switchRow('notify_on_leads', 'New leads', 'Notify when new leads are found.', s.notify_on_leads, !edit)}
          <div class="oa-field" style="margin-top:10px"><label for="n-pct">Usage warning threshold (%)</label><input class="form-input" id="n-pct" name="usage_warning_percent" type="number" min="1" max="100" value="${attr(s.usage_warning_percent || 80)}" ${edit ? '' : 'disabled'}/></div>
          ${edit ? '<div class="oa-form-foot" style="margin-top:14px"><button type="submit" class="btn btn-primary btn-sm">Save</button></div>' : readonlyNote()}
          <p class="oa-hint" style="margin-top:10px">Personal preferences are in <a class="oa-link" href="#profile">Profile &amp; security</a>.</p></form></div>`;
      if (window.matchMedia('(max-width:1024px)').matches) $('[data-cols]', v.el).style.gridTemplateColumns = 'minmax(0,1fr)';
      const t = dataTable($('[data-host]', v.el), {
        url: '/api/notifications', caption: 'Notifications', limit: 20,
        filters: [{ name: 'unread', label: 'Show', type: 'select', all: 'All notifications', options: [['true', 'Unread only']] }],
        columns: [
          { label: 'Notification', render: r => `<b>${esc(r.title)}</b>${r.read ? '' : ' <span class="badge badge-primary">New</span>'}<div class="oa-small oa-muted">${esc(r.message || '')}</div>` },
          { label: 'Type', render: r => pill(r.severity === 'danger' ? 'failed' : r.severity === 'warning' ? 'pending' : r.severity === 'success' ? 'completed' : 'info', label(r.type)) },
          { label: 'When', render: r => timeTag(r.created_at) },
          { label: '', render: r => r.read ? '' : '<button type="button" class="btn btn-ghost btn-sm" data-act="read">Mark read</button>' },
        ],
        empty: { title: 'You\'re all caught up', desc: 'New notifications for your organization will appear here.' },
        async onAction(act, r) { try { await api('/api/notifications/read', { method: 'POST', body: { id: r.id } }); t.reload(); refreshBell(); } catch (e) { toast(e.message, 'error'); } },
      });
      $('[data-readall]', v.el).onclick = async () => { try { await api('/api/notifications/read', { method: 'POST', body: {} }); t.reload(); refreshBell(); toast('All notifications marked as read.', 'success'); } catch (e) { toast(e.message, 'error'); } };
      const sf = $('[data-settings]', v.el);
      if (edit) sf.onsubmit = async (e) => {
        e.preventDefault(); const f = sf.elements; const pct = Number(f.usage_warning_percent.value);
        if (!(pct >= 1 && pct <= 100)) { toast('Threshold must be between 1 and 100.', 'error'); return; }
        const btn = $('button[type=submit]', sf); busy(btn, true, 'Saving…');
        try { await api('/api/organizations/current', { method: 'PATCH', body: { settings: { email_notifications: f.email_notifications.checked, notify_usage_warnings: f.notify_usage_warnings.checked, notify_job_completion: f.notify_job_completion.checked, notify_on_leads: f.notify_on_leads.checked, usage_warning_percent: pct } } }); await getOrg(true); toast('Notification settings saved.', 'success'); }
        catch (err) { toast(err.message, 'error'); } finally { busy(btn, false); }
      };
    },
  };

  // ── Audit logs ─────────────────────────────────────────────────────
  ROUTES.audit = {
    skel: 'table', title: () => 'Audit logs',
    async render(v) {
      if (!can('org_audit.view')) { renderError(v.el, { status: 403, message: 'Viewing audit logs requires the “View audit logs” permission.' }); return; }
      const f = await api('/api/org-admin/audit-logs/facets'); if (!v.alive()) return;
      v.el.innerHTML = head('Audit logs', 'Every important action in your organization — who did what, when and from where.') + '<div data-host></div>';
      dataTable($('[data-host]', v.el), {
        url: '/api/org-admin/audit-logs', caption: 'Audit logs', limit: 25, initial: v.query,
        exportUrl: (p) => '/api/org-admin/audit-logs.csv' + qs(Object.assign({}, p, { page: '', limit: '' })),
        filters: [{ name: 'q', label: 'Search', placeholder: 'Search action, user or resource' },
          { name: 'action', label: 'Action', type: 'select', all: 'All actions', options: f.actions.map(a => [a, a]) },
          { name: 'category', label: 'Category', type: 'select', all: 'All categories', options: f.categories.map(a => [a, label(a)]) },
          { name: 'user', label: 'User', placeholder: 'User email' },
          { name: 'status', label: 'Result', type: 'select', all: 'Any result', options: [['success', 'Success'], ['failure', 'Failure']] },
          { name: 'from', label: 'From date', type: 'date' }, { name: 'to', label: 'To date', type: 'date' }],
        columns: [
          { label: 'When', render: r => `<span title="${attr(fmtDate(r.at, true))}">${esc(fmtDate(r.at, true))}</span>` },
          { label: 'User', render: r => `${esc(r.actor_email || 'system')}<div class="oa-small oa-muted">${esc(ROLE_LABELS[r.actor_role] || r.actor_role || '')}</div>` },
          { label: 'Action', render: r => `<b>${esc(r.action)}</b><div class="oa-small oa-muted">${esc(label(r.category || ''))}</div>` },
          { label: 'Resource', render: r => `${esc(r.resource_type || '—')}${r.resource_id ? `<div class="oa-mono oa-muted oa-trunc" style="max-width:160px">${esc(r.resource_id)}</div>` : ''}` },
          { label: 'Result', render: r => pill(r.status === 'failure' ? 'failed' : 'completed', r.status || 'success') },
          { label: 'IP', render: r => `<span class="oa-mono">${esc(r.ip || '—')}</span>` },
          { label: '', render: () => '<button type="button" class="btn btn-ghost btn-sm" data-act="view">Details</button>' },
        ],
        empty: { title: 'No audit entries', desc: 'Actions in your organization are recorded here.' },
        onAction(act, r) {
          openModal({ title: r.action, wide: true, body: kv([['When', esc(fmtDate(r.at, true))], ['User', esc(r.actor_email || 'system')], ['Role', esc(r.actor_role || '—')], ['Result', esc(r.status || '—')], ['Resource', esc((r.resource_type || '—') + (r.resource_id ? ' · ' + r.resource_id : ''))], ['IP', esc(r.ip || '—')], ['Browser', esc(r.user_agent || '—')]]) + `<h3 class="oa-section-title" style="margin:18px 0 8px">Details</h3><pre class="oa-pre">${esc(JSON.stringify(r.details || {}, null, 2))}</pre>`, foot: '<button type="button" class="btn btn-secondary" data-close>Close</button>' });
        },
      });
    },
  };

  // ── Support ────────────────────────────────────────────────────────
  ROUTES.support = {
    skel: 'table', title: (p) => p[1] ? 'Support ticket' : 'Support',
    async render(v) {
      if (v.parts[1]) return renderTicket(v, v.parts[1]);
      v.el.innerHTML = head('Support', 'Get help, report an issue and follow your tickets.', `<button type="button" class="btn btn-primary" data-new>${ico('plus')} New ticket</button>`) +
        `<div class="oa-grid oa-grid-3">
          <div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">Help center</div>${ico('help')}</div><ul class="oa-feed"><li><div class="oa-feed-main"><a class="oa-link" href="/faq">Frequently asked questions</a></div><span></span></li><li><div class="oa-feed-main"><a class="oa-link" href="/features">Product features &amp; guides</a></div><span></span></li><li><div class="oa-feed-main"><a class="oa-link" href="/pricing">Plans &amp; pricing</a></div><span></span></li></ul></div>
          <div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">Contact support</div>${ico('chat')}</div><p class="oa-small oa-muted">Open a ticket and our team replies here and by email. For sales questions use the contact form.</p><div class="oa-actions" style="margin-top:12px"><button type="button" class="btn btn-secondary btn-sm" data-new>Report an issue</button><a class="btn btn-ghost btn-sm" href="/contact">Contact form</a></div></div>
          <div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">System status</div>${ico('cpu')}</div><div data-health><div class="oa-skel oa-skel-line"></div></div></div>
        </div><h2 class="oa-section-title" style="margin:24px 0 12px">Your tickets</h2><div data-host></div>`;
      const t = dataTable($('[data-host]', v.el), {
        url: '/api/org-admin/support/tickets', caption: 'Support tickets',
        filters: [{ name: 'q', label: 'Search', placeholder: 'Search subject' }, { name: 'status', label: 'Status', type: 'select', all: 'All statuses', options: [['open', 'Open'], ['in_progress', 'In progress'], ['waiting', 'Waiting on you'], ['resolved', 'Resolved'], ['closed', 'Closed']] }],
        columns: [{ label: '#', render: r => `<span class="oa-mono">#${esc(r.number)}</span>` }, { label: 'Subject', render: r => `<b>${esc(r.subject)}</b>${r.last_reply_from_staff ? ' <span class="badge badge-info">Support replied</span>' : ''}` },
          { label: 'Category', render: r => esc(label(r.category)) }, { label: 'Priority', render: r => esc(label(r.priority)) }, { label: 'Status', render: r => pill(r.status) },
          { label: 'Opened by', render: r => esc(r.created_by || '') }, { label: 'Updated', render: r => timeTag(r.updated_at) }],
        rowHref: r => '#support/' + r.id,
        empty: { title: 'No tickets', desc: 'When you report an issue it appears here with our replies.' },
      });
      $$('[data-new]', v.el).forEach(b => b.onclick = () => ticketModal(() => t.reload()));
      try {
        const res = await fetch('/health', { credentials: 'same-origin' }); const h = await res.json().catch(() => ({}));
        const ok = res.ok && (h.status === 'ok' || h.status === 'healthy');
        const box = $('[data-health]', v.el);
        if (box) box.innerHTML = `<div style="display:flex;align-items:center;gap:10px">${pill(ok ? 'active' : 'pending', ok ? 'All systems operational' : 'Degraded performance')}</div><p class="oa-small oa-muted" style="margin-top:8px">Checked ${esc(new Date().toLocaleTimeString())}${h.version ? ' · v' + esc(h.version) : ''}</p>`;
      } catch (_) { const box = $('[data-health]', v.el); if (box) box.innerHTML = pill('failed', 'Status unavailable'); }
    },
  };
  function ticketModal(done) {
    openModal({
      title: 'Report an issue',
      body: `<form class="oa-form" data-f novalidate>
        <div class="oa-field"><label for="tk-subject">Subject</label><input class="form-input" id="tk-subject" name="subject" maxlength="200" required/></div>
        <div class="oa-form-grid"><div class="oa-field"><label for="tk-cat">Category</label><select class="form-select" id="tk-cat" name="category">${selectOpts([['question', 'Question'], ['bug', 'Something is broken'], ['billing', 'Billing'], ['account', 'Account & access'], ['feature', 'Feature request'], ['other', 'Other']], 'question')}</select></div>
        <div class="oa-field"><label for="tk-pri">Priority</label><select class="form-select" id="tk-pri" name="priority">${selectOpts([['low', 'Low'], ['normal', 'Normal'], ['high', 'High'], ['urgent', 'Urgent — work is blocked']], 'normal')}</select></div></div>
        <div class="oa-field"><label for="tk-msg">Describe the issue</label><textarea class="form-textarea" id="tk-msg" name="message" rows="5" maxlength="5000" required placeholder="What happened, what you expected, and any search or lead links."></textarea></div>
        <span class="oa-err" id="tk-err" role="alert"></span></form>`,
      foot: '<button type="button" class="btn btn-secondary" data-close>Cancel</button><button type="button" class="btn btn-primary" data-send>Submit ticket</button>',
      onMount(root, close) {
        const f = $('[data-f]', root), btn = $('[data-send]', root);
        btn.onclick = async () => {
          const body = { subject: f.subject.value.trim(), category: f.category.value, priority: f.priority.value, message: f.message.value.trim() };
          if (body.subject.length < 3) { $('#tk-err', root).textContent = 'Add a short subject.'; f.subject.focus(); return; }
          if (body.message.length < 5) { $('#tk-err', root).textContent = 'Please describe the issue.'; f.message.focus(); return; }
          busy(btn, true, 'Submitting…');
          try { const r = await api('/api/org-admin/support/tickets', { method: 'POST', body }); toast(r.message, 'success'); close(); if (done) done(); }
          catch (e) { $('#tk-err', root).textContent = e.message; busy(btn, false); }
        };
      },
    });
  }
  async function renderTicket(v, id) {
    const t = (await api('/api/org-admin/support/tickets/' + encodeURIComponent(id))).ticket; if (!v.alive()) return;
    const closed = t.status === 'closed';
    v.el.innerHTML = head(`#${t.number} · ${t.subject}`, `${label(t.category)} · ${label(t.priority)} priority · opened ${fmtDate(t.created_at, true)}`, `${pill(t.status)}<button type="button" class="btn btn-secondary" data-toggle>${closed ? 'Reopen ticket' : 'Close ticket'}</button>`, ['#support', 'Support']) +
      `<div class="oa-card"><div class="oa-thread">${t.messages.map(m => `<div class="oa-msg ${m.from_staff ? 'staff' : ''}"><div class="oa-msg-head"><span><b>${esc(m.author || '')}</b>${m.from_staff ? ' · LeadAI support' : ''}</span><span>${esc(fmtDate(m.at, true))}</span></div><div class="oa-msg-body">${esc(m.body)}</div></div>`).join('')}</div>
      <form class="oa-form" data-reply style="margin-top:16px"><label class="oa-label" for="tk-reply">Reply</label><textarea class="form-textarea" id="tk-reply" name="message" rows="4" maxlength="5000" style="min-height:90px" placeholder="${closed ? 'Replying reopens the ticket.' : 'Add more details…'}"></textarea><div class="oa-actions" style="justify-content:flex-end"><button type="submit" class="btn btn-primary">Send reply</button></div></form></div>`;
    const rf = $('[data-reply]', v.el);
    rf.onsubmit = async (e) => {
      e.preventDefault(); const msg = rf.message.value.trim(); if (!msg) { rf.message.focus(); return; }
      const btn = $('button', rf); busy(btn, true, 'Sending…');
      try { await api(`/api/org-admin/support/tickets/${encodeURIComponent(id)}/messages`, { method: 'POST', body: { message: msg } }); toast('Reply sent.', 'success'); route(); }
      catch (err) { toast(err.message, 'error'); busy(btn, false); }
    };
    $('[data-toggle]', v.el).onclick = async (e) => {
      busy(e.currentTarget, true);
      try { await api(`/api/org-admin/support/tickets/${encodeURIComponent(id)}/status`, { method: 'POST', body: { status: closed ? 'open' : 'closed' } }); toast(closed ? 'Ticket reopened.' : 'Ticket closed.', 'success'); route(); }
      catch (err) { toast(err.message, 'error'); busy(e.currentTarget, false); }
    };
  }

  // ── Profile & security ─────────────────────────────────────────────
  ROUTES.profile = {
    title: () => 'Profile & security',
    async render(v) {
      const [p, sess] = await Promise.all([api('/api/org-admin/profile'), api('/api/auth/sessions').catch(() => ({ sessions: [] }))]);
      if (!v.alive()) return;
      const pr = p.profile; const prefs = pr.notification_preferences || {};
      const imp = !!S.ctx.me.impersonated_by;
      v.el.innerHTML = head('Profile & security', 'Your personal details, password, sessions and notification preferences.') +
        (imp ? `<div class="alert alert-warning" style="margin-bottom:16px">${ico('alert', 'alert-icon')}<div class="alert-body">You are viewing this organization as LeadAI support. Personal settings are read-only.</div></div>` : '') +
        `<div class="oa-grid oa-grid-2">
          <form class="oa-card oa-form" data-profile novalidate><div class="oa-card-head"><div class="oa-card-title">Profile</div></div>
            ${fld('name', 'Full name', pr.name, 'text', imp ? 'disabled' : '', 'maxlength="120" required autocomplete="name"')}
            ${fld('email', 'Email', pr.email, 'email', 'readonly', '', 'Contact support to change your sign-in email.')}
            ${kv([['Role', roleTag(pr.role)], ['Organization', esc(pr.organization)], ['Last sign-in', timeTag(pr.last_login)]])}
            ${imp ? '' : '<div class="oa-form-foot"><button type="submit" class="btn btn-primary">Save profile</button></div>'}</form>
          <form class="oa-card oa-form" data-pw novalidate autocomplete="off"><div class="oa-card-head"><div class="oa-card-title">Change password</div><span class="oa-card-sub">${pr.password_changed_at ? 'Last changed ' + esc(fmtDate(pr.password_changed_at)) : ''}</span></div>
            ${fld('current_password', 'Current password', '', 'password', imp ? 'disabled' : '', 'autocomplete="current-password" required')}
            ${fld('new_password', 'New password', '', 'password', imp ? 'disabled' : '', 'autocomplete="new-password" required')}
            <div class="pw-meter" data-meter aria-hidden="true"><span></span><span></span><span></span><span></span></div><span class="oa-hint">At least 8 characters with an uppercase letter and a number.</span>
            ${fld('confirm_password', 'Confirm new password', '', 'password', imp ? 'disabled' : '', 'autocomplete="new-password" required')}
            ${imp ? '' : '<div class="oa-form-foot"><button type="submit" class="btn btn-primary">Update password</button></div>'}</form>
        </div>
        <div class="oa-grid oa-grid-2">
          <div class="oa-card"><div class="oa-card-head"><div class="oa-card-title">Active sessions</div>${imp ? '' : '<button type="button" class="btn btn-secondary btn-sm" data-others>Sign out other sessions</button>'}</div>
            ${(sess.sessions || []).length ? `<ul class="oa-feed">${sess.sessions.map(s => `<li><div class="oa-feed-main"><b>${esc(browserName(s.user_agent))}</b>${s.current ? ' <span class="badge badge-success">This device</span>' : ''}<div class="oa-small oa-muted oa-trunc">${esc(s.user_agent || '')}</div><div class="oa-small oa-muted">Signed in ${esc(fmtDate(s.created_at, true))} · expires ${esc(fmtDate(s.expires_at))}</div></div>${s.current || imp ? '<span></span>' : `<button type="button" class="btn btn-ghost btn-sm" data-revoke="${attr(s.id)}">Revoke</button>`}</li>`).join('')}</ul>` : '<p class="oa-muted oa-small">No active sessions found.</p>'}</div>
          <form class="oa-card" data-prefs><div class="oa-card-head"><div class="oa-card-title">My notification preferences</div></div>
            ${switchRow('email_notifications', 'Email me', 'Receive notifications by email as well as in the portal.', prefs.email_notifications, imp)}
            ${switchRow('lead_assigned', 'Lead activity', 'When leads are assigned or change status.', prefs.lead_assigned, imp)}
            ${switchRow('search_completed', 'Search results', 'When searches finish or fail.', prefs.search_completed, imp)}
            ${switchRow('usage_warnings', 'Usage warnings', 'When the organization nears its limits.', prefs.usage_warnings, imp)}
            ${switchRow('weekly_summary', 'Weekly summary', 'A weekly email with your organization\'s results.', prefs.weekly_summary, imp)}
            ${switchRow('product_updates', 'Product updates', 'News about new LeadAI features.', prefs.product_updates, imp)}
            ${imp ? '' : '<div class="oa-form-foot" style="margin-top:10px"><button type="submit" class="btn btn-primary btn-sm">Save preferences</button></div>'}</form>
        </div>`;
      if (imp) return;
      const pf = $('[data-profile]', v.el);
      liveValidate(pf);
      pf.onsubmit = async (e) => {
        e.preventDefault(); const name = pf.name.value.trim();
        if (name.length < 2) { $('#e-name', pf).textContent = 'Enter your name.'; pf.name.focus(); return; }
        const btn = $('button[type=submit]', pf); busy(btn, true, 'Saving…');
        try { await api('/api/org-admin/profile', { method: 'PATCH', body: { name } }); toast('Profile saved.', 'success'); S.ctx.me.name = name; paintUser(); }
        catch (err) { toast(err.message, 'error'); } finally { busy(btn, false); }
      };
      const pw = $('[data-pw]', v.el);
      const meterEl = $('[data-meter]', pw);
      pw.new_password.addEventListener('input', () => { const c = window.LeadAIUI ? window.LeadAIUI.passwordChecks(pw.new_password.value) : { score: 0 }; meterEl.setAttribute('data-score', String(pw.new_password.value ? c.score : 0)); });
      pw.onsubmit = async (e) => {
        e.preventDefault(); $$('.oa-err', pw).forEach(x => { x.textContent = ''; });
        const cur = pw.current_password.value, nw = pw.new_password.value, cf = pw.confirm_password.value;
        if (!cur) { $('#e-current_password', pw).textContent = 'Enter your current password.'; pw.current_password.focus(); return; }
        const prob = window.LeadAIUI ? window.LeadAIUI.passwordProblem(nw) : (nw.length < 8 ? 'Use at least 8 characters.' : '');
        if (prob) { $('#e-new_password', pw).textContent = prob; pw.new_password.focus(); return; }
        if (nw !== cf) { $('#e-confirm_password', pw).textContent = 'Passwords don\'t match.'; pw.confirm_password.focus(); return; }
        const btn = $('button[type=submit]', pw); busy(btn, true, 'Updating…');
        try { const r = await api('/api/auth/password/change', { method: 'POST', body: { current_password: cur, new_password: nw } }); toast(r.message || 'Password updated.', 'success'); pw.reset(); meterEl.setAttribute('data-score', '0'); }
        catch (err) { $('#e-current_password', pw).textContent = err.message; } finally { busy(btn, false); }
      };
      const oth = $('[data-others]', v.el);
      if (oth) oth.onclick = async () => {
        if (!(await confirmDialog('Sign out other sessions?', 'Every other browser and device signed in to your account is signed out.', { confirm: 'Sign out others' }))) return;
        try { const r = await api('/api/auth/sessions/others', { method: 'DELETE' }); toast(`${r.revoked} session(s) signed out.`, 'success'); route(); } catch (e) { toast(e.message, 'error'); }
      };
      $$('[data-revoke]', v.el).forEach(b => b.onclick = async () => { busy(b, true, '…'); try { await api('/api/auth/sessions/' + encodeURIComponent(b.dataset.revoke), { method: 'DELETE' }); toast('Session revoked.', 'success'); route(); } catch (e) { toast(e.message, 'error'); busy(b, false); } });
      const pref = $('[data-prefs]', v.el);
      pref.onsubmit = async (e) => {
        e.preventDefault(); const o = {}; $$('input[type=checkbox]', pref).forEach(i => { o[i.name] = i.checked; });
        const btn = $('button[type=submit]', pref); busy(btn, true, 'Saving…');
        try { await api('/api/org-admin/profile', { method: 'PATCH', body: { notification_preferences: o } }); toast('Preferences saved.', 'success'); } catch (err) { toast(err.message, 'error'); } finally { busy(btn, false); }
      };
    },
  };
  function browserName(ua) {
    ua = String(ua || '');
    const b = /Edg\//.test(ua) ? 'Edge' : /Chrome\//.test(ua) ? 'Chrome' : /Firefox\//.test(ua) ? 'Firefox' : /Safari\//.test(ua) ? 'Safari' : 'Browser';
    const os = /Windows/.test(ua) ? 'Windows' : /Mac OS/.test(ua) ? 'macOS' : /Android/.test(ua) ? 'Android' : /iPhone|iPad/.test(ua) ? 'iOS' : /Linux/.test(ua) ? 'Linux' : '';
    return b + (os ? ' on ' + os : '');
  }

  // ── Global find ────────────────────────────────────────────────────
  ROUTES.find = {
    nav: () => '', title: () => 'Search results',
    async render(v) {
      const q = (v.query.q || '').trim();
      if (!q) { v.el.innerHTML = head('Search', 'Type in the search box to find users, leads and searches.'); return; }
      const [u, l, s] = await Promise.allSettled([
        can('members.view') ? api('/api/org-admin/users' + qs({ q, limit: 5 })) : Promise.reject(new Error('x')),
        api('/api/org-admin/leads' + qs({ q, limit: 5 })), api('/api/org-admin/searches' + qs({ q, limit: 5 }))]);
      if (!v.alive()) return;
      const grp = (title, res, row, more) => {
        if (res.status !== 'fulfilled') return '';
        const items = res.value.items || [];
        return `<div class="oa-card oa-find-group"><div class="oa-card-head"><div class="oa-card-title">${esc(title)} <span class="oa-muted oa-small">(${num(res.value.total)})</span></div>${res.value.total > items.length ? `<a class="oa-link oa-small" href="${attr(more)}">See all</a>` : ''}</div>${items.length ? `<ul class="oa-feed">${items.map(row).join('')}</ul>` : '<p class="oa-muted oa-small">No matches.</p>'}</div>`;
      };
      v.el.innerHTML = head(`Results for “${q}”`, 'Users, leads and searches in your organization.') +
        grp('Users', u, r => `<li><div class="oa-feed-main"><a class="oa-link" href="#team/user/${attr(r.user_id)}">${esc(r.name || r.email)}</a> <span class="oa-small oa-muted">${esc(r.email)}</span></div>${roleTag(r.role)}</li>`, '#team/users?q=' + encodeURIComponent(q)) +
        grp('Leads', l, r => `<li><div class="oa-feed-main"><a class="oa-link" href="#lead/${attr(r.id)}">${esc(r.name)}</a> <span class="oa-small oa-muted oa-trunc">${esc(r.text)}</span></div>${scoreTag(r.score)}</li>`, '#leads?q=' + encodeURIComponent(q)) +
        grp('Searches', s, r => `<li><div class="oa-feed-main"><a class="oa-link" href="#searches/${attr(r.run_id)}">${esc(r.url)}</a> ${pill(r.status)}</div>${timeTag(r.created_at)}</li>`, '#searches?q=' + encodeURIComponent(q));
    },
  };

  // ════════════════════════════════════════════════════════════════════
  // Shell: sidebar, popovers, notifications, gate
  // ════════════════════════════════════════════════════════════════════
  function closeSidebar() { $('#oa-sidebar').classList.remove('open'); $('#oa-scrim').classList.remove('open'); $('#oa-menu-btn').setAttribute('aria-expanded', 'false'); }
  function closePops(except) {
    [['#oa-notif-pop', '#oa-bell'], ['#oa-profile-pop', '#oa-profile-btn']].forEach(([p, b]) => { if (p !== except) { $(p).hidden = true; $(b).setAttribute('aria-expanded', 'false'); } });
  }
  function togglePop(pop, btn, onOpen) {
    const open = $(pop).hidden; closePops(pop);
    $(pop).hidden = !open; $(btn).setAttribute('aria-expanded', String(open));
    if (open && onOpen) onOpen();
  }
  async function refreshBell() {
    try {
      const d = await api('/api/notifications?limit=8', { noRedirect: true });
      const c = $('#oa-bell-count'); c.hidden = !d.unread; c.textContent = d.unread > 99 ? '99+' : String(d.unread);
      const nc = $('[data-nav-count]'); if (nc) { nc.hidden = !d.unread; nc.textContent = String(d.unread); }
      $('#oa-bell').setAttribute('aria-label', 'Notifications' + (d.unread ? ` (${d.unread} unread)` : ''));
      $('#oa-notif-list').innerHTML = (d.items || []).length ? d.items.map(n => `<button type="button" class="oa-pop-item ${n.read ? '' : 'unread'}" data-nid="${attr(n.id)}"><b>${esc(n.title)}</b><small>${esc(n.message || '')}</small><small>${esc(ago(n.created_at))}</small></button>`).join('') : `<div class="oa-state" style="padding:24px">${ico('bell')}<p>No notifications yet.</p></div>`;
    } catch (_) { /* bell is best effort */ }
  }
  function paintUser() {
    const me = S.ctx.me; const nm = me.name || me.email;
    $('#oa-avatar').textContent = initials(nm); $('#oa-profile-name').textContent = nm;
    $('#oa-menu-name').textContent = nm; $('#oa-menu-email').textContent = me.email;
  }
  function refreshBrand() {
    const o = S.ctx.organization; const name = (S.org && S.org.name) || o.name;
    $('#oa-org-name').textContent = name; $('#oa-crumb-org').textContent = name + ' · Admin';
    const logo = safeUrl((S.org && S.org.logo_url) || o.logo_url);
    const img = $('#oa-org-logo');
    if (logo) { img.src = logo; img.hidden = false; $('#oa-org-mark').hidden = true; img.onerror = () => { img.hidden = true; $('#oa-org-mark').hidden = false; }; }
    else { img.hidden = true; $('#oa-org-mark').hidden = false; }
  }
  function gate(html) {
    closeFloating(); $('#oa-modal-root').innerHTML = '';
    $('#oa-app').hidden = true; const g = $('#oa-gate'); g.hidden = false;
    g.innerHTML = `<div class="oa-gate-inner"><div class="oa-gate-brand"><span class="brand-mark" aria-hidden="true"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2.5l2.1 6.4 6.4 2.1-6.4 2.1L12 19.5l-2.1-6.4L3.5 11l6.4-2.1z"/></svg></span><span>LeadAI <b>Admin</b></span>
      <button type="button" class="oa-icon-btn theme-toggle" data-theme-toggle aria-label="Switch theme">${ico('sun')}</button></div><div class="oa-card">${html}</div></div>`;
    const lo = $('[data-logout]', g); if (lo) lo.onclick = logout;
    const h = $('h1', g); if (h) { h.setAttribute('tabindex', '-1'); h.focus(); }
  }
  function showLockedGate() {
    gate(`<div class="oa-state"><div class="oa-state-icon brand">${ico('lock')}</div><span class="oa-pill" data-tone="brand" data-status="demo">Demo organization</span><h1 class="oa-gate-title">Your Admin portal unlocks with a confirmed plan</h1>
      <p>Team management, roles, analytics, exports and lead rules become available as soon as your subscription is confirmed. Your data and searches are safe in the meantime.</p>
      <ol class="oa-steps"><li><b>Choose a plan</b><span>In the User Portal under Billing.</span></li><li><b>Complete payment</b><span>Card or bank transfer.</span></li><li><b>We confirm it</b><span>The Admin portal switches on automatically.</span></li></ol>
      <div class="oa-actions"><a class="btn btn-primary" href="/dashboard#billing">Choose a plan</a><a class="btn btn-secondary" href="/dashboard">Open User Portal</a><button type="button" class="btn btn-ghost" data-logout>Sign out</button></div></div>`);
  }
  async function logout() {
    try { await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' }); } catch (_) { /* */ }
    location.href = '/login';
  }

  async function boot() {
    let me;
    try { me = (await api('/api/auth/me', { noGate: true })).user; }
    catch (e) {
      if (e.status === 401) return;
      if (e.status === 403) {
        gate(`<div class="oa-state"><div class="oa-state-icon danger">${ico('lock')}</div><h1 class="oa-gate-title">Access unavailable</h1><p>${esc(e.message)}</p><div class="oa-actions"><a class="btn btn-secondary" href="/dashboard">User Portal</a><button type="button" class="btn btn-ghost" data-logout>Sign out</button></div></div>`);
        return;
      }
      gate(`${stateHtml('error', 'Could not load the Admin portal', e.message, '<button type="button" class="btn btn-primary" data-retry>Retry</button>')}`);
      $('[data-retry]', $('#oa-gate')).onclick = () => location.reload();
      return;
    }
    S.me = me;
    if (['owner', 'admin'].indexOf(me.org_role) < 0) {
      gate(`<div class="oa-state"><div class="oa-state-icon warning">${ico('lock')}</div><h1 class="oa-gate-title">Admins only</h1><p>The Admin portal is available to your organization's owner and Admins. Your work lives in the User Portal.</p><div class="oa-actions"><a class="btn btn-primary" href="/dashboard">Open User Portal</a><button type="button" class="btn btn-ghost" data-logout>Sign out</button></div></div>`);
      return;
    }
    if (!me.admin_portal_enabled) { showLockedGate(); return; }
    try { S.ctx = await api('/api/org-admin/context'); }
    catch (e) {
      if (e.code === 'admin_portal_disabled') return;
      gate(stateHtml(e.status === 403 ? 'denied' : 'error', e.status === 403 ? 'Access denied' : 'Could not load the Admin portal', e.message, '<button type="button" class="btn btn-primary" data-retry>Retry</button>'));
      $('[data-retry]', $('#oa-gate')).onclick = () => location.reload();
      return;
    }
    $('#oa-gate').hidden = true; $('#oa-app').hidden = false;
    renderNav(); paintUser(); refreshBrand();
    getOrg().then(refreshBrand).catch(() => { /* */ });
    wireShell();
    refreshBell(); setInterval(() => { if (!document.hidden) refreshBell(); }, 60000);
    window.addEventListener('hashchange', route);
    if (!location.hash) history.replaceState(null, '', '#dashboard');
    route();
  }
  function wireShell() {
    $('#oa-menu-btn').onclick = () => { const s = $('#oa-sidebar'); const open = !s.classList.contains('open'); s.classList.toggle('open', open); $('#oa-scrim').classList.toggle('open', open); $('#oa-menu-btn').setAttribute('aria-expanded', String(open)); if (open) { const a = $('#oa-nav a'); if (a) a.focus(); } };
    $('#oa-scrim').onclick = closeSidebar;
    $('#oa-bell').onclick = (e) => { e.stopPropagation(); togglePop('#oa-notif-pop', '#oa-bell', refreshBell); };
    $('#oa-profile-btn').onclick = (e) => { e.stopPropagation(); togglePop('#oa-profile-pop', '#oa-profile-btn'); };
    document.addEventListener('click', (e) => { if (!e.target.closest('.oa-pop')) { closePops(); closeFloating(); } });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { closePops(); closeSidebar(); closeFloating(); }
      if (e.key === '/' && !e.target.closest('input,textarea,select,[contenteditable]')) { e.preventDefault(); const f = $('#oa-search'); f.classList.add('open'); $('#oa-search-input').focus(); }
    });
    $('#oa-logout').onclick = logout;
    $('#oa-notif-readall').onclick = async (e) => { e.stopPropagation(); try { await api('/api/notifications/read', { method: 'POST', body: {} }); refreshBell(); } catch (err) { toast(err.message, 'error'); } };
    $('#oa-notif-list').addEventListener('click', async (e) => {
      const b = e.target.closest('[data-nid]'); if (!b) return;
      try { await api('/api/notifications/read', { method: 'POST', body: { id: b.dataset.nid } }); } catch (_) { /* */ }
      closePops(); refreshBell(); location.hash = '#notifications';
    });
    $('#oa-search').onsubmit = (e) => { e.preventDefault(); const q = $('#oa-search-input').value.trim(); if (q) { location.hash = '#find?q=' + encodeURIComponent(q); $('#oa-search').classList.remove('open'); } };
    $('#oa-search-toggle').onclick = () => { const f = $('#oa-search'); f.classList.toggle('open'); if (f.classList.contains('open')) $('#oa-search-input').focus(); };
    $('#oa-search-input').addEventListener('blur', () => setTimeout(() => $('#oa-search').classList.remove('open'), 150));
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
