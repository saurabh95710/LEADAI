/**
 * LeadAI UI helpers (design/ui.js) — dependency-free, optional.
 * ---------------------------------------------------------------------------
 * Load after (or before) theme.js:  <script src="/static/design/ui.js" defer></script>
 * Everything is merged into window.LeadAIUI (theme.js's helpers — api,
 * errorMessage, setBusy, fieldError, … — are kept untouched).
 *
 *   LeadAIUI.statusTone(status)            → 'success'|'info'|'warning'|'danger'|'neutral'|'suspended'|'primary'
 *   LeadAIUI.statusLabel(status)           → 'Pending payment'
 *   LeadAIUI.statusPill(status, label?, {size:'sm'|'lg'})
 *                                          → '<span class="status-pill" data-status="…">…</span>' (escaped HTML)
 *   LeadAIUI.confirm({title, message, confirmLabel, cancelLabel, danger, requireText})
 *                                          → Promise<boolean>. Accessible (alertdialog, focus trap, Esc = cancel).
 *                                            requireText: user must type it (e.g. the org name) to enable confirm.
 *   LeadAIUI.openDrawer(elOrId, {onClose}) / LeadAIUI.closeDrawer(elOrId)
 *                                          → for .drawer-backdrop > .drawer-panel markup (see components.css).
 *                                            Esc, backdrop click and [data-drawer-close] close it; focus is trapped
 *                                            and restored to the opener.
 *   LeadAIUI.dropdown(trigger, menu?)      → wires a menu button (aria-expanded, outside click, Esc, arrow keys).
 *                                            Auto-wired for <button data-dropdown-toggle aria-controls="menuId">.
 *   LeadAIUI.tooltip(el, text)             → floating tooltip that isn't clipped by overflow. Auto-wired for [data-tip].
 *   LeadAIUI.columnMenu(table, mount, {storageKey, exclude:[index…]})
 *                                          → renders a "Columns" dropdown into `mount`; hides columns by toggling
 *                                            .col-hidden on th/td; persists to localStorage when storageKey is given.
 *                                            Re-apply after re-rendering rows with the returned .apply().
 *   LeadAIUI.bulkSelect(table, bar, {onChange})
 *                                          → header checkbox [data-select-all] + row checkboxes [data-select-row]
 *                                            (value = row id). Shows/hides the .bulk-bar, updates .bulk-bar-count,
 *                                            marks rows .is-selected. Returns {selected(), clear(), refresh()}.
 *   LeadAIUI.icon(name, {size, cls, label}) → inline SVG string from the shared stroke icon set (LeadAIUI.icons).
 *   LeadAIUI.trapFocus(container)          → returns a release() function.
 */
(function () {
  'use strict';

  var UI = window.LeadAIUI = window.LeadAIUI || {};

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function byId(x) { return typeof x === 'string' ? document.getElementById(x) : x; }
  var FOCUSABLE = 'a[href],area[href],button:not([disabled]),input:not([disabled]):not([type="hidden"]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"]),[contenteditable="true"]';
  function focusables(root) {
    return Array.prototype.filter.call(root.querySelectorAll(FOCUSABLE), function (el) {
      return el.offsetParent !== null || el === document.activeElement;
    });
  }

  // ── Canonical status → tone mapping (keep in sync with components.css) ──
  var TONES = {
    success: ['active', 'completed', 'success', 'paid', 'confirmed', 'succeeded', 'approved', 'converted', 'published', 'healthy', 'ok'],
    info: ['running', 'processing', 'in_progress', 'in-progress'],
    warning: ['pending', 'pending_payment', 'payment_received', 'pending_admin_confirmation', 'draft', 'invited', 'queued'],
    danger: ['failed', 'error', 'rejected', 'refund_due'],
    neutral: ['deactivated', 'archived', 'cancelled', 'canceled', 'expired', 'revoked', 'inactive', 'disabled'],
    suspended: ['suspended'],
    primary: ['demo', 'trial', 'trialing']
  };
  var STATUS_TONE = {};
  Object.keys(TONES).forEach(function (t) { TONES[t].forEach(function (s) { STATUS_TONE[s] = t; }); });
  function normStatus(s) { return String(s == null ? '' : s).trim().toLowerCase().replace(/\s+/g, '_'); }

  UI.statusTones = TONES;
  UI.statusTone = function (status) { return STATUS_TONE[normStatus(status)] || 'neutral'; };
  UI.statusLabel = function (status) {
    var s = normStatus(status).replace(/[_-]+/g, ' ');
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : '—';
  };
  UI.statusPill = function (status, label, opts) {
    opts = opts || {};
    var key = normStatus(status);
    var cls = 'status-pill' + (opts.size ? ' status-pill-' + opts.size : '') + (STATUS_TONE[key] ? '' : ' tone-neutral');
    return '<span class="' + cls + '" data-status="' + esc(key) + '">' + esc(label != null ? label : UI.statusLabel(status)) + '</span>';
  };

  // ── Focus trap ──
  UI.trapFocus = function (container) {
    function onKey(e) {
      if (e.key !== 'Tab') return;
      var list = focusables(container);
      if (!list.length) { e.preventDefault(); return; }
      var first = list[0], last = list[list.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
    container.addEventListener('keydown', onKey);
    return function release() { container.removeEventListener('keydown', onKey); };
  };

  // ── Confirm dialog (Promise) ──
  UI.confirm = function (o) {
    o = o || {};
    return new Promise(function (resolve) {
      var opener = document.activeElement;
      var id = 'ui-confirm-' + Math.random().toString(36).slice(2, 8);
      var danger = !!o.danger;
      var wrap = document.createElement('div');
      wrap.className = 'modal-overlay confirm-dialog' + (danger ? ' is-danger' : '');
      wrap.setAttribute('role', 'alertdialog');
      wrap.setAttribute('aria-modal', 'true');
      wrap.setAttribute('aria-labelledby', id + '-t');
      wrap.setAttribute('aria-describedby', id + '-d');
      var need = o.requireText ? String(o.requireText) : '';
      wrap.innerHTML =
        '<div class="modal modal-sm">' +
          '<div class="modal-body text-center" style="margin-bottom:0">' +
            '<div class="confirm-icon" aria-hidden="true">' + UI.icon(danger ? 'alert' : 'help', { size: 22 }) + '</div>' +
            '<h2 class="modal-title" id="' + id + '-t">' + esc(o.title || 'Are you sure?') + '</h2>' +
            '<p class="confirm-text" id="' + id + '-d" style="margin-top:8px">' + esc(o.message || '') + '</p>' +
            (need ? '<div class="confirm-typed form-group"><label class="form-label" for="' + id + '-i">Type <strong>' + esc(need) + '</strong> to confirm</label>' +
              '<input class="form-input" id="' + id + '-i" autocomplete="off" spellcheck="false"></div>' : '') +
          '</div>' +
          '<div class="modal-footer" style="border-top:0;padding-top:var(--spacing-lg)">' +
            '<button type="button" class="btn btn-secondary" data-act="cancel">' + esc(o.cancelLabel || 'Cancel') + '</button>' +
            '<button type="button" class="btn ' + (danger ? 'btn-danger-solid' : 'btn-primary') + '" data-act="ok"' + (need ? ' disabled' : '') + '>' +
              esc(o.confirmLabel || (danger ? 'Delete' : 'Confirm')) + '</button>' +
          '</div>' +
        '</div>';
      document.body.appendChild(wrap);
      document.body.classList.add('has-overlay');
      var release = UI.trapFocus(wrap);
      var ok = wrap.querySelector('[data-act="ok"]');
      var input = need ? wrap.querySelector('input') : null;
      if (input) input.addEventListener('input', function () { ok.disabled = input.value.trim() !== need; });
      function done(v) {
        release();
        document.removeEventListener('keydown', onKey, true);
        wrap.remove();
        if (!document.querySelector('.modal-overlay:not(.hidden), .drawer-backdrop:not([hidden])')) document.body.classList.remove('has-overlay');
        try { if (opener && opener.focus) opener.focus(); } catch (_) {}
        resolve(v);
      }
      function onKey(e) {
        if (e.key === 'Escape') { e.stopPropagation(); e.preventDefault(); done(false); }
        else if (e.key === 'Enter' && input && document.activeElement === input && !ok.disabled) { e.preventDefault(); done(true); }
      }
      document.addEventListener('keydown', onKey, true);
      wrap.addEventListener('click', function (e) {
        var act = e.target.closest && e.target.closest('[data-act]');
        if (e.target === wrap) return done(false);
        if (!act) return;
        done(act.getAttribute('data-act') === 'ok');
      });
      // Destructive: focus Cancel first so Enter never deletes by accident.
      (input || (danger ? wrap.querySelector('[data-act="cancel"]') : ok)).focus();
    });
  };

  // ── Drawers ──
  var drawerState = new WeakMap();
  UI.openDrawer = function (target, opts) {
    var el = byId(target);
    if (!el) return;
    opts = opts || {};
    var opener = document.activeElement;
    el.hidden = false;
    el.classList.remove('hidden');
    document.body.classList.add('has-overlay');
    var panel = el.querySelector('.drawer-panel') || el;
    if (!panel.hasAttribute('role')) panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'true');
    var release = UI.trapFocus(panel);
    function onKey(e) { if (e.key === 'Escape') { e.stopPropagation(); UI.closeDrawer(el); } }
    function onClick(e) {
      if (e.target === el || (e.target.closest && e.target.closest('[data-drawer-close]'))) UI.closeDrawer(el);
    }
    document.addEventListener('keydown', onKey, true);
    el.addEventListener('click', onClick);
    drawerState.set(el, { opener: opener, release: release, onKey: onKey, onClick: onClick, onClose: opts.onClose });
    var first = panel.querySelector('[autofocus]') || focusables(panel)[0];
    if (first) first.focus(); else { panel.setAttribute('tabindex', '-1'); panel.focus(); }
  };
  UI.closeDrawer = function (target) {
    var el = byId(target);
    if (!el) return;
    var st = drawerState.get(el);
    el.hidden = true;
    if (st) {
      st.release();
      document.removeEventListener('keydown', st.onKey, true);
      el.removeEventListener('click', st.onClick);
      drawerState.delete(el);
      try { if (st.opener && st.opener.focus) st.opener.focus(); } catch (_) {}
      if (typeof st.onClose === 'function') st.onClose();
    }
    if (!document.querySelector('.modal-overlay:not(.hidden), .drawer-backdrop:not([hidden])')) document.body.classList.remove('has-overlay');
  };

  // ── Dropdown menus ──
  function menuItems(menu) {
    return Array.prototype.filter.call(menu.querySelectorAll('.dropdown-item, .dropdown-check input, [role="menuitem"], [role="menuitemcheckbox"]'),
      function (el) { return !el.disabled && el.getAttribute('aria-disabled') !== 'true' && el.offsetParent !== null; });
  }
  UI.dropdown = function (trigger, menu) {
    trigger = byId(trigger);
    menu = byId(menu) || (trigger && byId(trigger.getAttribute('aria-controls')));
    if (!trigger || !menu || trigger._uiDropdown) return trigger && trigger._uiDropdown;
    trigger.setAttribute('aria-haspopup', 'true');
    trigger.setAttribute('aria-expanded', 'false');
    if (!menu.id) menu.id = 'ui-menu-' + Math.random().toString(36).slice(2, 8);
    trigger.setAttribute('aria-controls', menu.id);
    menu.classList.add('hidden');
    function isOpen() { return !menu.classList.contains('hidden'); }
    function open(focusFirst) {
      menu.classList.remove('hidden');
      trigger.setAttribute('aria-expanded', 'true');
      if (focusFirst) { var it = menuItems(menu)[0]; if (it) it.focus(); }
    }
    function close(refocus) {
      if (!isOpen()) return;
      menu.classList.add('hidden');
      trigger.setAttribute('aria-expanded', 'false');
      if (refocus) trigger.focus();
    }
    trigger.addEventListener('click', function (e) { e.preventDefault(); e.stopPropagation(); isOpen() ? close() : open(false); });
    trigger.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(true); }
    });
    menu.addEventListener('keydown', function (e) {
      var items = menuItems(menu), i = items.indexOf(document.activeElement);
      if (e.key === 'ArrowDown') { e.preventDefault(); (items[i + 1] || items[0]).focus(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); (items[i - 1] || items[items.length - 1]).focus(); }
      else if (e.key === 'Home') { e.preventDefault(); items[0] && items[0].focus(); }
      else if (e.key === 'End') { e.preventDefault(); items[items.length - 1] && items[items.length - 1].focus(); }
      else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(true); }
      else if (e.key === 'Tab') { close(false); }
    });
    menu.addEventListener('click', function (e) {
      // Menus of checkboxes (column pickers) stay open; action items close the menu.
      if (e.target.closest && e.target.closest('.dropdown-item') && !menu.hasAttribute('data-keep-open')) close(true);
    });
    document.addEventListener('click', function (e) {
      if (isOpen() && !menu.contains(e.target) && !trigger.contains(e.target)) close(false);
    });
    var api = { open: open, close: close, isOpen: isOpen };
    trigger._uiDropdown = api;
    return api;
  };

  // ── Floating tooltip ──
  var tipEl = null, tipFor = null;
  function showTip(el, text) {
    if (!text) return;
    if (!tipEl) {
      tipEl = document.createElement('div');
      tipEl.className = 'ui-tooltip';
      tipEl.id = 'ui-tooltip';
      tipEl.setAttribute('role', 'tooltip');
      document.body.appendChild(tipEl);
    }
    tipEl.textContent = text;
    tipFor = el;
    el.setAttribute('aria-describedby', 'ui-tooltip');
    var r = el.getBoundingClientRect();
    tipEl.style.left = '0px'; tipEl.style.top = '0px';
    tipEl.classList.add('is-visible');
    var tw = tipEl.offsetWidth, th = tipEl.offsetHeight;
    var left = Math.min(Math.max(8, r.left + r.width / 2 - tw / 2), window.innerWidth - tw - 8);
    var top = r.top - th - 8;
    if (top < 8) top = r.bottom + 8;
    tipEl.style.left = left + 'px';
    tipEl.style.top = top + 'px';
  }
  function hideTip() {
    if (tipEl) tipEl.classList.remove('is-visible');
    if (tipFor) { tipFor.removeAttribute('aria-describedby'); tipFor = null; }
  }
  UI.tooltip = function (el, text) {
    el = byId(el);
    if (!el) return;
    el.setAttribute('data-tip', text);
  };
  function tipTarget(e) { return e.target && e.target.closest ? e.target.closest('[data-tip]') : null; }
  document.addEventListener('mouseover', function (e) { var t = tipTarget(e); if (t) showTip(t, t.getAttribute('data-tip')); });
  document.addEventListener('mouseout', function (e) { var t = tipTarget(e); if (t && !t.contains(e.relatedTarget)) hideTip(); });
  document.addEventListener('focusin', function (e) { var t = tipTarget(e); if (t) showTip(t, t.getAttribute('data-tip')); });
  document.addEventListener('focusout', function (e) { if (tipTarget(e)) hideTip(); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') hideTip(); });
  window.addEventListener('scroll', hideTip, true);

  // ── Column visibility ──
  UI.columnMenu = function (table, mount, opts) {
    table = byId(table); mount = byId(mount); opts = opts || {};
    if (!table || !mount) return null;
    var exclude = opts.exclude || [];
    var heads = Array.prototype.slice.call(table.querySelectorAll('thead th'));
    var hidden = {};
    if (opts.storageKey) {
      try { hidden = JSON.parse(localStorage.getItem(opts.storageKey) || '{}') || {}; } catch (_) { hidden = {}; }
    }
    function keyOf(th, i) { return th.getAttribute('data-col') || String(i); }
    function apply() {
      heads.forEach(function (th, i) {
        var off = !!hidden[keyOf(th, i)];
        th.classList.toggle('col-hidden', off);
        Array.prototype.forEach.call(table.querySelectorAll('tbody tr'), function (tr) {
          var cell = tr.children[i];
          if (cell && !cell.hasAttribute('colspan')) cell.classList.toggle('col-hidden', off);
        });
      });
    }
    var uid = 'cols-' + Math.random().toString(36).slice(2, 8);
    mount.classList.add('dropdown');
    mount.innerHTML = '<button type="button" class="btn btn-secondary btn-sm" id="' + uid + '-btn">' + UI.icon('columns', { size: 16 }) + '<span>Columns</span></button>' +
      '<div class="dropdown-menu col-menu" id="' + uid + '" role="menu" data-keep-open aria-label="Show or hide columns">' +
      '<div class="dropdown-header">Show columns</div>' +
      heads.map(function (th, i) {
        if (exclude.indexOf(i) !== -1) return '';
        var label = (th.getAttribute('data-col-label') || th.textContent || '').trim() || ('Column ' + (i + 1));
        var k = keyOf(th, i);
        return '<label class="dropdown-check"><input type="checkbox" data-col-key="' + esc(k) + '"' + (hidden[k] ? '' : ' checked') + '> ' + esc(label) + '</label>';
      }).join('') + '</div>';
    mount.querySelector('.col-menu').addEventListener('change', function (e) {
      var k = e.target.getAttribute('data-col-key');
      if (k == null) return;
      if (e.target.checked) delete hidden[k]; else hidden[k] = true;
      if (opts.storageKey) { try { localStorage.setItem(opts.storageKey, JSON.stringify(hidden)); } catch (_) {} }
      apply();
    });
    UI.dropdown(document.getElementById(uid + '-btn'), document.getElementById(uid));
    apply();
    return { apply: apply, hidden: function () { return Object.keys(hidden); } };
  };

  // ── Bulk selection ──
  UI.bulkSelect = function (table, bar, opts) {
    table = byId(table); bar = byId(bar); opts = opts || {};
    if (!table) return null;
    function boxes() { return Array.prototype.slice.call(table.querySelectorAll('input[data-select-row]')); }
    function selected() { return boxes().filter(function (b) { return b.checked; }).map(function (b) { return b.value; }); }
    function refresh() {
      var all = boxes(), sel = all.filter(function (b) { return b.checked; });
      all.forEach(function (b) { var tr = b.closest('tr'); if (tr) tr.classList.toggle('is-selected', b.checked); });
      var head = table.querySelector('input[data-select-all]');
      if (head) {
        head.checked = !!all.length && sel.length === all.length;
        head.indeterminate = sel.length > 0 && sel.length < all.length;
      }
      if (bar) {
        bar.hidden = sel.length === 0;
        var c = bar.querySelector('.bulk-bar-count');
        if (c) c.textContent = sel.length + ' selected';
      }
      if (typeof opts.onChange === 'function') opts.onChange(selected());
    }
    table.addEventListener('change', function (e) {
      if (e.target.matches && e.target.matches('input[data-select-all]')) {
        boxes().forEach(function (b) { b.checked = e.target.checked; });
      }
      if (e.target.matches && e.target.matches('input[data-select-all], input[data-select-row]')) refresh();
    });
    function clear() { boxes().forEach(function (b) { b.checked = false; }); refresh(); }
    if (bar) bar.addEventListener('click', function (e) { if (e.target.closest && e.target.closest('[data-bulk-clear]')) clear(); });
    refresh();
    return { selected: selected, clear: clear, refresh: refresh };
  };

  // ── Icon set (24×24 stroke icons; Feather-style geometry) ──
  var P = {
    alert: '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    info: '<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>',
    help: '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    check: '<polyline points="20 6 9 17 4 12"/>',
    'check-circle': '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>',
    x: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    'x-circle': '<circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>',
    plus: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    search: '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    filter: '<polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/>',
    download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
    upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
    refresh: '<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>',
    more: '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
    'more-vertical': '<circle cx="12" cy="12" r="1"/><circle cx="12" cy="5" r="1"/><circle cx="12" cy="19" r="1"/>',
    edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
    trash: '<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
    eye: '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
    'eye-off': '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/>',
    columns: '<rect x="3" y="3" width="18" height="18" rx="2"/><line x1="9" y1="3" x2="9" y2="21"/><line x1="15" y1="3" x2="15" y2="21"/>',
    'chevron-down': '<polyline points="6 9 12 15 18 9"/>',
    'chevron-right': '<polyline points="9 18 15 12 9 6"/>',
    'chevron-left': '<polyline points="15 18 9 12 15 6"/>',
    'arrow-right': '<line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/>',
    'external-link': '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>',
    copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    bell: '<path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/>',
    user: '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    users: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    building: '<rect x="4" y="2" width="16" height="20" rx="2"/><path d="M9 22v-4h6v4"/><path d="M8 6h.01M16 6h.01M12 6h.01M12 10h.01M12 14h.01M16 10h.01M16 14h.01M8 10h.01M8 14h.01"/>',
    home: '<path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>',
    grid: '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>',
    chart: '<line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>',
    activity: '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
    lock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    mail: '<path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/>',
    'credit-card': '<rect x="1" y="4" width="22" height="16" rx="2"/><line x1="1" y1="10" x2="23" y2="10"/>',
    coins: '<circle cx="8" cy="8" r="6"/><path d="M18.09 10.37A6 6 0 1 1 10.34 18"/><path d="M7 6h1v4"/><path d="m16.71 13.88.7.71-2.82 2.82"/>',
    clock: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    calendar: '<rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>',
    link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    sparkles: '<path d="M12 2.5l2.1 6.4 6.4 2.1-6.4 2.1L12 19.5l-2.1-6.4L3.5 11l6.4-2.1z"/>',
    target: '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
    zap: '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
    globe: '<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
    server: '<rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/>',
    database: '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>',
    'file-text': '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>',
    inbox: '<polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
    'log-out': '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>',
    menu: '<line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="18" x2="21" y2="18"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/>',
    moon: '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>'
  };
  UI.icons = P;
  UI.icon = function (name, opts) {
    opts = opts || {};
    var body = P[name] || P.info;
    var size = opts.size ? ' width="' + Number(opts.size) + '" height="' + Number(opts.size) + '"' : '';
    var a11y = opts.label ? ' role="img" aria-label="' + esc(opts.label) + '"' : ' aria-hidden="true" focusable="false"';
    return '<svg class="icon' + (opts.cls ? ' ' + esc(opts.cls) : '') + '" viewBox="0 0 24 24"' + size + a11y + '>' + body + '</svg>';
  };

  // ── Auto-wiring ──
  function autowire(root) {
    (root || document).querySelectorAll('[data-dropdown-toggle]').forEach(function (btn) { UI.dropdown(btn); });
  }
  UI.autowire = autowire;
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { autowire(); });
  else autowire();
})();
