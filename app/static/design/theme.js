/**
 * LeadAI Theme Loader
 * Fetches branding settings from /api/admin/settings/theme (if admin session
 * exists) or /api/public/theme (public fallback) and injects CSS variables
 * into :root, allowing Super-Admin to change branding without code changes.
 */
(async function LeadAITheme() {
  const CACHE_KEY = 'leadai_theme_v1';
  const CACHE_TTL = 60000; // 1 min
  const MODE_KEY = 'leadai_color_mode'; // 'light' | 'dark' (absent = follow OS)
  const BG_VARS = ['--bg-primary', '--bg-secondary', '--bg-tertiary'];
  let lastBranding = null;

  // ── Colour mode (light / dark) ──────────────────────────────────────────
  function storedMode() {
    try {
      const v = localStorage.getItem(MODE_KEY);
      return v === 'light' || v === 'dark' ? v : null;
    } catch (_) { return null; }
  }
  function systemMode() {
    try {
      return window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    } catch (_) { return 'dark'; }
  }
  function effectiveMode() {
    const attr = document.documentElement.getAttribute('data-theme');
    if (attr === 'light' || attr === 'dark') return attr;
    return systemMode();
  }
  function syncToggles() {
    const mode = effectiveMode();
    document.querySelectorAll('[data-theme-toggle]').forEach(btn => {
      const next = mode === 'dark' ? 'light' : 'dark';
      btn.setAttribute('aria-label', 'Switch to ' + next + ' theme');
      btn.setAttribute('title', 'Switch to ' + next + ' theme');
      btn.setAttribute('data-mode', mode);
    });
  }
  function setMode(mode) {
    const root = document.documentElement;
    if (mode === 'light' || mode === 'dark') {
      root.setAttribute('data-theme', mode);
      try { localStorage.setItem(MODE_KEY, mode); } catch (_) {}
    } else {
      root.removeAttribute('data-theme');
      try { localStorage.removeItem(MODE_KEY); } catch (_) {}
    }
    // Branding background overrides are dark-palette values: only apply them in dark.
    if (lastBranding) applyTheme(lastBranding);
    syncToggles();
    try { document.dispatchEvent(new CustomEvent('leadai:themechange', { detail: { mode: effectiveMode() } })); } catch (_) {}
  }
  // Apply the saved preference as early as possible (avoids a flash when
  // theme.js is loaded in <head>). A page may pin data-theme itself; the
  // user's explicit choice wins.
  (function initMode() {
    const saved = storedMode();
    if (saved) document.documentElement.setAttribute('data-theme', saved);
  })();

  function applyTheme(data) {
    if (!data || typeof data !== 'object') return;
    lastBranding = data;
    const root = document.documentElement;
    const map = {
      primary:       '--primary',
      primary_hover: '--primary-hover',
      primary_light: '--primary-light',
      primary_dark:  '--primary-dark',
      accent:        '--accent',
      bg_primary:    '--bg-primary',
      bg_secondary:  '--bg-secondary',
      bg_tertiary:   '--bg-tertiary',
      radius_md:     '--radius-md',
      radius_lg:     '--radius-lg',
      font_family:   '--font-sans',
    };
    const isLight = effectiveMode() === 'light';
    Object.entries(map).forEach(([key, cssVar]) => {
      if (BG_VARS.includes(cssVar) && isLight) { root.style.removeProperty(cssVar); return; }
      if (data[key]) root.style.setProperty(cssVar, data[key]);
    });
    if (data.brand_name) {
      document.querySelectorAll('[data-brand-name]').forEach(el => {
        el.textContent = data.brand_name;
      });
    }
    if (data.logo_url) {
      document.querySelectorAll('[data-brand-logo]').forEach(el => {
        el.src = data.logo_url;
      });
    }
    if (data.favicon_url) {
      let link = document.querySelector("link[rel~='icon']");
      if (!link) { link = document.createElement('link'); link.rel = 'icon'; document.head.appendChild(link); }
      link.href = data.favicon_url;
    }
  }

  async function loadTheme() {
    // Try cache first
    try {
      const cached = sessionStorage.getItem(CACHE_KEY);
      if (cached) {
        const { ts, data } = JSON.parse(cached);
        if (Date.now() - ts < CACHE_TTL) { applyTheme(data); return; }
      }
    } catch (_) {}

    // Try public theme endpoint
    try {
      const resp = await fetch('/api/public/theme', { credentials: 'same-origin' });
      if (resp.ok) {
        const data = await resp.json();
        applyTheme(data);
        try { sessionStorage.setItem(CACHE_KEY, JSON.stringify({ ts: Date.now(), data })); } catch (_) {}
      }
    } catch (_) {
      // Silently fall back to token defaults
    }
  }

  // Apply immediately on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', loadTheme);
  } else {
    loadTheme();
  }

  // Expose refresh method for admin panel to call after saving branding,
  // plus the colour-mode helpers.
  window.LeadAITheme = {
    refresh: () => {
      try { sessionStorage.removeItem(CACHE_KEY); } catch (_) {}
      loadTheme();
    },
    /** Current effective mode: 'light' | 'dark'. */
    mode: effectiveMode,
    /** Force 'light' | 'dark', or pass null to follow the OS again. */
    set: setMode,
    /** Flip between light and dark and persist the choice. */
    toggle: () => { setMode(effectiveMode() === 'dark' ? 'light' : 'dark'); return effectiveMode(); },
  };

  // Any element with [data-theme-toggle] becomes a theme switch.
  document.addEventListener('click', e => {
    const btn = e.target && e.target.closest ? e.target.closest('[data-theme-toggle]') : null;
    if (btn) { e.preventDefault(); window.LeadAITheme.toggle(); }
  });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', syncToggles);
  else syncToggles();
  try {
    const mq = window.matchMedia('(prefers-color-scheme: light)');
    const onChange = () => { if (!storedMode()) { if (lastBranding) applyTheme(lastBranding); syncToggles(); } };
    if (mq.addEventListener) mq.addEventListener('change', onChange);
    else if (mq.addListener) mq.addListener(onChange);
  } catch (_) {}

  // Toast utility used across all pages
  window.toast = function(msg, type = 'info', duration = 4000) {
    let container = document.getElementById('toast-container');
    if (!container) {
      container = document.createElement('div');
      container.id = 'toast-container';
      document.body.appendChild(container);
    }
    if (!container.hasAttribute('aria-live')) container.setAttribute('aria-live', 'polite');
    const icons = { success: '✅', error: '❌', warning: '⚠️', info: 'ℹ️' };
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.setAttribute('role', type === 'error' ? 'alert' : 'status');
    // Message is rendered as text (never HTML) so server strings can't inject markup.
    el.innerHTML = `<span class="toast-icon" aria-hidden="true">${icons[type]||'ℹ️'}</span><span class="toast-msg"></span><button type="button" class="toast-close" aria-label="Dismiss">✕</button>`;
    el.querySelector('.toast-msg').textContent = msg == null ? '' : String(msg);
    el.querySelector('.toast-close').addEventListener('click', () => el.remove());
    container.appendChild(el);
    setTimeout(() => el.remove(), duration);
  };

  // Modal helpers
  window.openModal = function(id) {
    const el = document.getElementById(id);
    if (el) { el.classList.remove('hidden'); el.setAttribute('aria-hidden','false'); }
  };
  window.closeModal = function(id) {
    const el = document.getElementById(id);
    if (el) { el.classList.add('hidden'); el.setAttribute('aria-hidden','true'); }
  };
  // Close modal on Escape
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      document.querySelectorAll('.modal-overlay:not(.hidden)').forEach(el => el.classList.add('hidden'));
    }
  });
  // Close modal on overlay click
  document.addEventListener('click', e => {
    if (e.target.classList.contains('modal-overlay')) e.target.classList.add('hidden');
  });

  // Confirm dialog utility
  window.confirm2 = function(title, msg, onConfirm, danger = false) {
    const existing = document.getElementById('_confirm_dialog');
    if (existing) existing.remove();
    const d = document.createElement('div');
    d.id = '_confirm_dialog';
    d.className = 'modal-overlay confirm-dialog';
    const esc = v => String(v == null ? '' : v).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    d.setAttribute('role', 'alertdialog');
    d.setAttribute('aria-modal', 'true');
    d.innerHTML = `<div class="modal" style="max-width:400px">
      <div class="modal-header"><span class="modal-title">${esc(title)}</span></div>
      <div class="modal-body"><p class="text-center" style="color:var(--text-secondary)">${esc(msg)}</p></div>
      <div class="modal-footer">
        <button class="btn btn-secondary" onclick="document.getElementById('_confirm_dialog').remove()">Cancel</button>
        <button class="btn ${danger?'btn-danger':'btn-primary'}" id="_confirm_ok">${danger?'Delete':'Confirm'}</button>
      </div>
    </div>`;
    document.body.appendChild(d);
    document.getElementById('_confirm_ok').onclick = () => { d.remove(); onConfirm(); };
    try { document.getElementById('_confirm_ok').focus(); } catch (_) {}
    d.onclick = e => { if(e.target===d) d.remove(); };
  };

  // ── Small UI helpers shared by the auth / lifecycle pages ───────────────
  // Merged (not replaced) so design/ui.js can load before or after this file.
  window.LeadAIUI = Object.assign(window.LeadAIUI || {}, {
    /**
     * fetch() wrapper: same-origin credentials, JSON in/out.
     * Resolves {ok, status, data, networkError} and never throws.
     */
    async api(url, opts = {}) {
      const init = { credentials: 'same-origin', method: opts.method || 'GET', headers: { Accept: 'application/json' } };
      if (opts.body !== undefined) {
        init.headers['Content-Type'] = 'application/json';
        init.body = JSON.stringify(opts.body);
      }
      try {
        const res = await fetch(url, init);
        let data = {};
        try { data = await res.json(); } catch (_) { data = {}; }
        return { ok: res.ok, status: res.status, data: data || {}, networkError: false };
      } catch (_) {
        return { ok: false, status: 0, data: {}, networkError: true };
      }
    },
    /** Turn a FastAPI error body into a readable sentence. */
    errorMessage(data, fallback) {
      const d = data && (data.detail !== undefined ? data.detail : (data.message || data.error));
      if (typeof d === 'string' && d.trim()) return d;
      if (d && typeof d === 'object' && !Array.isArray(d) && typeof d.message === 'string') return d.message;
      if (Array.isArray(d) && d.length) {
        const first = d[0] || {};
        const field = Array.isArray(first.loc) ? first.loc[first.loc.length - 1] : '';
        const msg = String(first.msg || '').replace(/^Value error,\s*/i, '');
        return field && typeof field === 'string' ? (field.charAt(0).toUpperCase() + field.slice(1).replace(/_/g, ' ') + ': ' + msg) : (msg || fallback);
      }
      return fallback || 'Something went wrong. Please try again.';
    },
    /** Machine-readable error code from {detail:{code}} (or ''). */
    errorCode(data) {
      const d = data && data.detail;
      return d && typeof d === 'object' && !Array.isArray(d) && d.code ? String(d.code) : '';
    },
    /** Put a .btn into / out of its busy state. Expects a .btn-label child. */
    setBusy(btn, busy, busyLabel) {
      if (!btn) return;
      const label = btn.querySelector('.btn-label');
      if (busy) {
        if (label && btn.dataset.idleLabel === undefined) btn.dataset.idleLabel = label.textContent;
        if (label && busyLabel) label.textContent = busyLabel;
        btn.classList.add('is-loading');
        btn.disabled = true;
        btn.setAttribute('aria-busy', 'true');
      } else {
        if (label && btn.dataset.idleLabel !== undefined) { label.textContent = btn.dataset.idleLabel; delete btn.dataset.idleLabel; }
        btn.classList.remove('is-loading');
        btn.disabled = false;
        btn.removeAttribute('aria-busy');
      }
    },
    /** Show / clear an inline field error (element id = input.getAttribute('data-error')). */
    fieldError(input, msg) {
      if (!input) return;
      const el = document.getElementById(input.getAttribute('data-error') || '');
      if (msg) { input.setAttribute('aria-invalid', 'true'); if (el) el.textContent = msg; }
      else { input.removeAttribute('aria-invalid'); if (el) el.textContent = ''; }
    },
    isEmail(v) { return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(v || '').trim()); },
    /** Password policy (mirrors the server): 8+ chars, an uppercase letter, a number. */
    passwordChecks(pw) {
      pw = String(pw || '');
      const length = pw.length >= 8;
      const upper = pw !== pw.toLowerCase();
      const number = /\d/.test(pw);
      let score = 0;
      if (pw) score = [length, upper, number].filter(Boolean).length;
      if (score === 3 && (pw.length >= 12 || /[^A-Za-z0-9]/.test(pw))) score = 4;
      if (pw && score === 0) score = 1;
      return { length, upper, number, valid: length && upper && number, score };
    },
    /** First unmet password rule as a sentence, or '' when valid. */
    passwordProblem(pw) {
      const c = this.passwordChecks(pw);
      if (!pw) return 'Choose a password.';
      if (!c.length) return 'Use at least 8 characters.';
      if (!c.upper) return 'Add at least one uppercase letter.';
      if (!c.number) return 'Add at least one number.';
      return '';
    },
    /**
     * Wire a password input to a .pw-meter (4 <span>s), a label element and
     * a .pw-rules list whose <li>s carry data-rule="length|upper|number".
     */
    bindPasswordMeter(input, meter, label, rules) {
      const names = ['', 'Weak', 'Fair', 'Good', 'Strong'];
      const update = () => {
        const c = this.passwordChecks(input.value);
        if (meter) meter.setAttribute('data-score', String(input.value ? c.score : 0));
        if (label) label.textContent = input.value ? ('Strength: ' + names[c.score]) : '';
        if (rules) rules.querySelectorAll('[data-rule]').forEach(li => {
          const met = !!c[li.getAttribute('data-rule')];
          li.classList.toggle('met', met);
          const sr = li.querySelector('.sr-only');
          if (sr) sr.textContent = met ? ' (met)' : ' (not met)';
        });
      };
      input.addEventListener('input', update);
      update();
      return update;
    },
    /** Show/hide password buttons: <button class="input-affix" data-toggle-password="inputId">. */
    bindPasswordToggles(root) {
      (root || document).querySelectorAll('[data-toggle-password]').forEach(btn => {
        btn.addEventListener('click', () => {
          const input = document.getElementById(btn.getAttribute('data-toggle-password'));
          if (!input) return;
          const show = input.type === 'password';
          input.type = show ? 'text' : 'password';
          btn.setAttribute('aria-pressed', String(show));
          btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
        });
      });
    },
    /** Only same-site relative paths ("/x", never "//x" or "/\x"). */
    safeNext(path) {
      if (typeof path !== 'string') return '';
      if (!path.startsWith('/') || path.startsWith('//') || path.startsWith('/\\')) return '';
      if (/[\u0000-\u001f]/.test(path)) return '';
      return path;
    },
    escape(s) {
      return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    },
  });
})();
