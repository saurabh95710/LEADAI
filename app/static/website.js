/* ============================================================
   LeadAI public website runtime (no dependencies).

   Every visible text block comes from the CMS (/api/public/*) and is
   inserted with textContent via h() — CMS content can never inject HTML.
   Only the constant SVG icons below are set as markup.

   Pages:  website.html → landing (/website, /features, /how-it-works,
           /pricing, /faq, /testimonials) and content pages (/about,
           /privacy, /terms, /cookies); contact.html → /contact.
   ============================================================ */
(function () {
  'use strict';

  // ── Tiny DOM helpers ──────────────────────────────────────────────────
  function h(tag, attrs) {
    const el = document.createElement(tag);
    if (attrs) {
      for (const k in attrs) {
        const v = attrs[k];
        if (v === null || v === undefined || v === false) continue;
        if (k === 'class') el.className = v;
        else if (k === 'text') el.textContent = v;
        else if (k.slice(0, 2) === 'on' && typeof v === 'function') el.addEventListener(k.slice(2), v);
        else el.setAttribute(k, v === true ? '' : String(v));
      }
    }
    for (let i = 2; i < arguments.length; i++) append(el, arguments[i]);
    return el;
  }
  function append(el, kid) {
    if (kid === null || kid === undefined || kid === false) return;
    if (Array.isArray(kid)) { kid.forEach(k => append(el, k)); return; }
    el.appendChild(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  function clear(el) { while (el && el.firstChild) el.removeChild(el.firstChild); return el; }
  const txt = v => (v === null || v === undefined) ? '' : String(v);

  function safeUrl(u) {
    u = txt(u).trim();
    if (!u) return '';
    if (/^(\/(?!\/)|#|https?:\/\/|mailto:|tel:)/i.test(u)) return u;
    return '';
  }
  function isExternal(u) { return /^https?:\/\//i.test(u) && u.indexOf(location.origin) !== 0; }

  // ── Icons (constant, trusted markup) ──────────────────────────────────
  const P = {
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    brain: '<path d="M9 3a3 3 0 0 0-3 3v.2A3 3 0 0 0 4 9a3 3 0 0 0 1 2.2A3 3 0 0 0 6 17a3 3 0 0 0 3 3h0V3z"/><path d="M15 3a3 3 0 0 1 3 3v.2A3 3 0 0 1 20 9a3 3 0 0 1-1 2.2A3 3 0 0 1 18 17a3 3 0 0 1-3 3h0V3z"/>',
    trophy: '<path d="M8 21h8M12 17v4M7 4h10v4a5 5 0 0 1-10 0z"/><path d="M17 5h3v2a3 3 0 0 1-3 3M7 5H4v2a3 3 0 0 0 3 3"/>',
    phone: '<path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2z"/>',
    refresh: '<path d="M21 12a9 9 0 0 1-15.5 6.3L3 16"/><path d="M3 12a9 9 0 0 1 15.5-6.3L21 8"/><path d="M21 3v5h-5M3 21v-5h5"/>',
    filter: '<path d="M3 5h18l-7 8v6l-4 2v-8z"/>',
    chart: '<path d="M3 3v18h18"/><path d="M7 15l4-4 3 3 5-6"/>',
    download: '<path d="M12 3v12M7 10l5 5 5-5"/><path d="M5 21h14"/>',
    users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 14a6 6 0 0 1 3.5 6"/>',
    shield: '<path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z"/><path d="m9 12 2 2 4-4"/>',
    zap: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.5"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    sparkles: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 17l.7 2 .3.3 2 .7-2 .7-.3.3-.7 2-.7-2-.3-.3-2-.7 2-.7.3-.3z"/>',
    map: '<path d="M12 21s-7-6.2-7-11.5A7 7 0 0 1 19 9.5C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>',
    lock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    file: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/>',
    cookie: '<path d="M21 12a9 9 0 1 1-9-9 3 3 0 0 0 3 3 3 3 0 0 0 3 3 3 3 0 0 0 3 3z"/><path d="M8.5 11.5h.01M15.5 15.5h.01M10.5 16.5h.01"/>',
    facebook: '<path d="M15 3h-2.5A4.5 4.5 0 0 0 8 7.5V10H5.5v4H8v7h4v-7h3l.5-4H12V7.5a.5.5 0 0 1 .5-.5H15z"/>',
    instagram: '<rect x="3" y="3" width="18" height="18" rx="5"/><circle cx="12" cy="12" r="4"/><path d="M17.5 6.5h.01"/>',
    youtube: '<rect x="2.5" y="5.5" width="19" height="13" rx="4"/><path d="m10 9.5 5 2.5-5 2.5z"/>',
    linkedin: '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M7.5 10.5V17M7.5 7.5h.01M11.5 17v-6.5M11.5 13.5a2.5 2.5 0 0 1 5 0V17"/>',
    x: '<path d="M4 4l16 16M20 4 4 20"/>',
    menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
    chevron: '<path d="m6 9 6 6 6-6"/>',
    star: '<path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1 6.2L12 17.3 6.5 20.2l1-6.2L3 9.6l6.2-.9z"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/>',
    moon: '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
  };
  const SVG_NS = 'http://www.w3.org/2000/svg';
  function svg(name, cls) {
    const s = document.createElementNS(SVG_NS, 'svg');
    s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('fill', name === 'star' ? 'currentColor' : 'none');
    s.setAttribute('stroke', 'currentColor'); s.setAttribute('stroke-width', '1.8');
    s.setAttribute('stroke-linecap', 'round'); s.setAttribute('stroke-linejoin', 'round');
    s.setAttribute('aria-hidden', 'true'); s.setAttribute('focusable', 'false');
    if (cls) s.setAttribute('class', cls);
    s.innerHTML = P[name] || '';
    return s;
  }
  /** Icon tile: a named icon, or the CMS text (an emoji) as plain text. */
  function iconTile(name) {
    const n = txt(name).trim();
    if (!n) return null;
    return h('span', { class: 'ico', 'aria-hidden': 'true' }, P[n] ? svg(n) : n);
  }
  const brandMark = () => h('span', { class: 'brand-mark', 'aria-hidden': 'true' },
    (function () { const s = svg('sparkles'); s.setAttribute('fill', 'currentColor'); s.setAttribute('stroke', 'none');
      s.innerHTML = '<path d="M12 2.5l2.1 6.4 6.4 2.1-6.4 2.1L12 19.5l-2.1-6.4L3.5 11l6.4-2.1z"/>'; return s; })());

  // ── Data ──────────────────────────────────────────────────────────────
  async function getJSON(url) {
    const r = await fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
    if (!r.ok) { const e = new Error('HTTP ' + r.status); e.status = r.status; throw e; }
    return r.json();
  }
  const memo = {};
  function api(path) { return memo[path] || (memo[path] = getJSON(path).catch(e => { delete memo[path]; throw e; })); }
  const soft = p => p.then(v => v, () => null);

  // ── Links / CTAs ──────────────────────────────────────────────────────
  function link(label, url, cls, extra) {
    const href = safeUrl(url);
    if (!label || !href) return null;
    const a = h('a', Object.assign({ href: href, class: cls || null, text: label }, extra || {}));
    if (isExternal(href)) { a.target = '_blank'; a.rel = 'noopener noreferrer'; }
    return a;
  }
  function ctas(sec, big) {
    const size = big ? ' btn-lg' : '';
    const p = sec.cta_primary || {}, s = sec.cta_secondary || {};
    const a = link(p.label, p.url, 'btn btn-primary' + size);
    const b = link(s.label, s.url, 'btn btn-secondary' + size);
    return (a || b) ? h('div', { class: 'cta-row' }, a, b) : null;
  }
  /** Title with the CMS "highlight" substring rendered as gradient text. */
  function titleNodes(title, highlight) {
    title = txt(title); highlight = txt(highlight);
    const i = highlight ? title.toLowerCase().indexOf(highlight.toLowerCase()) : -1;
    if (i < 0) return [title];
    return [title.slice(0, i), h('span', { class: 'text-gradient', text: title.slice(i, i + highlight.length) }),
      title.slice(i + highlight.length)];
  }
  function sectionHead(sec, level) {
    if (!sec.eyebrow && !sec.title && !sec.subtitle) return null;
    const id = 'h-' + sec.key;
    return h('div', { class: 'section-head' },
      sec.eyebrow ? h('p', { class: 'eyebrow', text: sec.eyebrow }) : null,
      sec.title ? h(level || 'h2', { class: 'section-title', id: id }, titleNodes(sec.title, sec.highlight)) : null,
      sec.subtitle ? h('p', { class: 'section-sub', text: sec.subtitle }) : null);
  }
  /** Plain-text body → paragraphs / "## " headings / "- " bullet lists. */
  function bodyNodes(body) {
    const out = []; let list = null; let para = [];
    const flush = () => { if (para.length) { out.push(h('p', { text: para.join(' ') })); para = []; } };
    txt(body).split(/\r?\n/).forEach(raw => {
      const line = raw.trim();
      if (!line) { flush(); list = null; return; }
      if (line.indexOf('## ') === 0) {
        flush(); list = null;
        const t = line.slice(3).trim();
        out.push(h('h2', { id: 's-' + t.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''), text: t }));
      } else if (/^[-*•]\s+/.test(line)) {
        flush();
        if (!list) { list = h('ul'); out.push(list); }
        list.appendChild(h('li', { text: line.replace(/^[-*•]\s+/, '') }));
      } else { list = null; para.push(line); }
    });
    flush();
    return out;
  }

  // ── Formatting ────────────────────────────────────────────────────────
  function money(v, cur) {
    const n = Number(v || 0);
    try {
      return new Intl.NumberFormat(undefined, { style: 'currency', currency: cur || 'USD',
        minimumFractionDigits: n % 1 ? 2 : 0, maximumFractionDigits: n % 1 ? 2 : 0 }).format(n);
    } catch (_) { return (n + ' ' + (cur || '')).trim(); }
  }
  function niceDate(iso) {
    const d = iso ? new Date(iso) : null;
    if (!d || isNaN(d)) return '';
    try { return d.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' }); } catch (_) { return iso.slice(0, 10); }
  }

  // ── SEO (client side; the server also renders it for crawlers) ────────
  function setMeta(attr, key, value) {
    let el = document.head.querySelector('meta[' + attr + '="' + key + '"]');
    if (!value) { if (el) el.remove(); return; }
    if (!el) { el = document.createElement('meta'); el.setAttribute(attr, key); document.head.appendChild(el); }
    el.setAttribute('content', value);
  }
  function applySeo(page, settings) {
    const seo = (page && page.seo) || {};
    const brand = (settings && settings.brand_name) || 'LeadAI';
    const title = seo.title || (page && page.title ? page.title + ' · ' + brand : brand);
    document.title = title;
    setMeta('name', 'description', seo.description || (settings && settings.tagline) || '');
    setMeta('name', 'robots', seo.robots || 'index,follow');
    setMeta('property', 'og:title', title);
    setMeta('property', 'og:description', seo.description || '');
    const base = (settings && settings.site_url) || location.origin;
    let img = seo.og_image || (settings && settings.og_image) || '';
    if (img && img.charAt(0) === '/') img = base + img;
    setMeta('property', 'og:image', safeUrl(img));
    const url = base + (location.pathname === '/' ? '/website' : location.pathname);
    setMeta('property', 'og:url', url);
    let canon = document.head.querySelector('link[rel="canonical"]');
    if (!canon) { canon = document.createElement('link'); canon.rel = 'canonical'; document.head.appendChild(canon); }
    canon.href = url;
  }

  // ── Site chrome: announcement, header, footer, cookie notice ─────────
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (_) {} },
  };
  function hash(s) { let x = 0; s = txt(s); for (let i = 0; i < s.length; i++) x = (x * 31 + s.charCodeAt(i)) | 0; return String(x); }
  const curPath = () => (location.pathname.replace(/\/+$/, '') || '/');

  function renderAnnouncement(s) {
    const host = document.getElementById('site-announcement');
    if (!host) return;
    clear(host);
    if (!s || !s.announcement_enabled || !s.announcement_text) { host.hidden = true; return; }
    const key = 'leadai_announce_' + hash(s.announcement_text);
    if (store.get(key)) { host.hidden = true; return; }
    host.className = 'announce'; host.hidden = false;
    host.setAttribute('role', 'region'); host.setAttribute('aria-label', 'Announcement');
    const close = h('button', { type: 'button', class: 'announce-close', 'aria-label': 'Dismiss announcement',
      onclick: () => { store.set(key, '1'); host.hidden = true; } }, svg('x'));
    host.appendChild(h('div', { class: 'announce-inner' },
      h('span', { text: s.announcement_text }),
      link(s.announcement_link_label, s.announcement_link_url)));
    host.appendChild(close);
  }

  function brandLink(s) {
    const name = (s && s.brand_name) || 'LeadAI';
    const logo = safeUrl(s && s.logo_url);
    return h('a', { class: 'site-brand', href: '/website', 'aria-label': name + ' home' },
      logo ? h('img', { src: logo, alt: '', width: 32, height: 32, decoding: 'async' }) : brandMark(),
      h('span', { class: 'brand-name', text: name }));
  }

  function navLinks(items, onNavigate) {
    const here = curPath();
    return (items || []).map(it => {
      const a = link(it.label, it.url);
      if (!a) return null;
      if (it.target === '_blank') { a.target = '_blank'; a.rel = 'noopener noreferrer'; }
      if (a.getAttribute('href') === here) a.setAttribute('aria-current', 'page');
      if (onNavigate) a.addEventListener('click', e => onNavigate(e, a.getAttribute('href')));
      return a;
    });
  }

  function themeToggle() {
    return h('button', { type: 'button', class: 'btn theme-toggle', 'data-theme-toggle': true, 'aria-label': 'Switch theme' },
      svg('sun', 'icon-sun'), svg('moon', 'icon-moon'));
  }

  function renderHeader(s, nav, onNavigate) {
    const host = document.getElementById('site-header');
    if (!host) return;
    clear(host);
    const menuId = 'mobile-menu';
    const menu = h('div', { class: 'mobile-menu', id: menuId });
    const btn = h('button', { type: 'button', class: 'menu-btn', 'aria-expanded': 'false', 'aria-controls': menuId, 'aria-label': 'Menu' },
      svg('menu', 'icon-open'), svg('x', 'icon-close'));
    const setOpen = open => { btn.setAttribute('aria-expanded', String(open)); menu.classList.toggle('open', open); };
    btn.addEventListener('click', () => setOpen(btn.getAttribute('aria-expanded') !== 'true'));
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && menu.classList.contains('open')) { setOpen(false); btn.focus(); } });
    const navClick = (e, href) => { setOpen(false); if (onNavigate) onNavigate(e, href); };

    host.appendChild(h('div', { class: 'wrap site-header-inner' },
      brandLink(s),
      h('nav', { class: 'site-nav', 'aria-label': 'Main' }, navLinks(nav.header, onNavigate)),
      h('div', { class: 'site-actions' },
        themeToggle(),
        h('a', { class: 'btn btn-ghost btn-sm hide-mobile', href: '/login', text: 'Sign in' }),
        h('a', { class: 'btn btn-primary btn-sm hide-xs', href: '/request-demo', text: 'Request demo' }),
        btn)));
    menu.appendChild(h('nav', { 'aria-label': 'Mobile' }, navLinks(nav.header, navClick),
      h('div', { class: 'mobile-cta' },
        h('a', { class: 'btn btn-secondary', href: '/login', text: 'Sign in' }),
        h('a', { class: 'btn btn-primary', href: '/request-demo', text: 'Request demo' }))));
    host.appendChild(menu);
    if (!host.dataset.scrollWatch) {
      host.dataset.scrollWatch = '1';
      let ticking = false;
      const onScroll = () => { if (ticking) return; ticking = true;
        requestAnimationFrame(() => { host.classList.toggle('is-scrolled', window.scrollY > 8); ticking = false; }); };
      window.addEventListener('scroll', onScroll, { passive: true });
      onScroll();
    }
  }

  const SOCIALS = [['social_linkedin', 'linkedin', 'LinkedIn'], ['social_twitter', 'x', 'X (Twitter)'],
    ['social_facebook', 'facebook', 'Facebook'], ['social_instagram', 'instagram', 'Instagram'], ['social_youtube', 'youtube', 'YouTube']];

  function renderFooter(s, nav, onNavigate) {
    const host = document.getElementById('site-footer');
    if (!host) return;
    clear(host);
    s = s || {};
    const groups = []; const byName = {};
    (nav.footer || []).forEach(it => {
      const g = it.group || '';
      if (!byName[g]) { byName[g] = []; groups.push(g); }
      byName[g].push(it);
    });
    const socials = SOCIALS.map(([k, ic, label]) => {
      const u = safeUrl(s[k]); if (!u) return null;
      return h('a', { href: u, target: '_blank', rel: 'noopener noreferrer', 'aria-label': label }, svg(ic));
    }).filter(Boolean);
    const email = txt(s.contact_email).trim(), phone = txt(s.contact_phone).trim();
    const year = String(new Date().getFullYear());
    const copyright = txt(s.footer_copyright).replace(/\{year\}/g, year) || ('© ' + year + ' ' + (s.brand_name || 'LeadAI'));

    host.appendChild(h('div', { class: 'wrap' },
      h('div', { class: 'footer-grid' },
        h('div', { class: 'footer-about' }, brandLink(s),
          s.footer_tagline ? h('p', { text: s.footer_tagline }) : null,
          (email || phone || s.contact_address) ? h('div', { class: 'footer-contact' },
            email ? h('a', { href: 'mailto:' + email, text: email }) : null,
            phone ? h('a', { href: 'tel:' + phone.replace(/[^\d+]/g, ''), text: phone }) : null,
            s.contact_address ? h('span', { text: s.contact_address }) : null) : null,
          socials.length ? h('div', { class: 'socials' }, socials) : null),
        groups.map((g, i) => h('nav', { class: 'footer-col', 'aria-labelledby': 'fcol-' + i },
          h('h2', { id: 'fcol-' + i, text: g || 'Links' }),
          h('ul', null, navLinks(byName[g], onNavigate).map(a => a && h('li', null, a)))))),
      h('div', { class: 'footer-bottom' }, h('p', { text: copyright }), themeToggle())));
  }

  function renderCookieNotice(s) {
    if (!s || !s.cookie_notice) return;
    const key = 'leadai_cookie_ok_' + hash(s.cookie_notice);
    if (store.get(key)) return;
    const box = h('div', { class: 'cookie-note', role: 'region', 'aria-label': 'Cookie notice' },
      h('p', null, s.cookie_notice, ' ', h('a', { class: 'link', href: '/cookies', text: 'Cookie policy' })));
    box.appendChild(h('button', { type: 'button', class: 'btn btn-primary btn-sm', text: 'OK',
      onclick: () => { store.set(key, '1'); box.remove(); } }));
    document.body.appendChild(box);
  }

  /** Load settings + navigation, render chrome. Resolves settings (or {}). */
  async function initChrome(onNavigate) {
    const [settings, nav] = await Promise.all([soft(api('/api/public/settings')), soft(api('/api/public/navigation'))]);
    const s = settings || {};
    const n = nav || { header: [], footer: [] };
    renderAnnouncement(s);
    renderHeader(s, n, onNavigate);
    renderFooter(s, n, onNavigate);
    renderCookieNotice(s);
    if (s.favicon_url && safeUrl(s.favicon_url)) {
      let l = document.querySelector("link[rel~='icon']");
      if (!l) { l = document.createElement('link'); l.rel = 'icon'; document.head.appendChild(l); }
      l.href = s.favicon_url;
    }
    syncThemeButtons();
    return s;
  }
  function syncThemeButtons() {
    const mode = window.LeadAITheme ? window.LeadAITheme.mode() : 'dark';
    document.querySelectorAll('[data-theme-toggle]').forEach(b => {
      const next = mode === 'dark' ? 'light' : 'dark';
      b.setAttribute('aria-label', 'Switch to ' + next + ' theme'); b.setAttribute('title', 'Switch to ' + next + ' theme');
    });
  }
  document.addEventListener('leadai:themechange', syncThemeButtons);

  // ── Reveal on scroll ──────────────────────────────────────────────────
  let io = null;
  function reveal(el) {
    if (!el) return el;
    const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduce || !('IntersectionObserver' in window)) return el;
    document.documentElement.classList.add('js-reveal');
    el.classList.add('reveal');
    if (!io) io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) { e.target.classList.add('is-visible'); io.unobserve(e.target); } }), { threshold: 0.08, rootMargin: '0px 0px -40px 0px' });
    io.observe(el);
    return el;
  }

  // ── Section renderers ─────────────────────────────────────────────────
  function sectionShell(sec, alt, kids) {
    return h('section', { class: 'section' + (alt ? ' section-alt' : ''), id: sec.key, 'aria-labelledby': sec.title ? 'h-' + sec.key : null },
      h('div', { class: 'wrap' }, kids));
  }

  const R = {};

  /** "94/100", "94 / 100" or "94%" → 0..1 (anything else → null: no ring). */
  function scoreFraction(v) {
    const m = /^\s*(\d+(?:\.\d+)?)\s*(?:\/\s*(\d+(?:\.\d+)?)|%)\s*$/.exec(txt(v));
    if (!m) return null;
    const max = m[2] ? Number(m[2]) : 100;
    return max > 0 ? Math.max(0, Math.min(1, Number(m[1]) / max)) : null;
  }
  /** Decorative progress ring (numbers only — no CMS text reaches markup). */
  function scoreRing(frac) {
    const s = document.createElementNS(SVG_NS, 'svg');
    s.setAttribute('viewBox', '0 0 64 64'); s.setAttribute('class', 'score-ring');
    s.setAttribute('aria-hidden', 'true'); s.setAttribute('focusable', 'false');
    const C = 2 * Math.PI * 27;
    s.innerHTML = '<defs><linearGradient id="ring-grad" x1="0" y1="0" x2="1" y2="1">' +
      '<stop offset="0" stop-color="#f0a531"/><stop offset=".55" style="stop-color:var(--primary)"/><stop offset="1" style="stop-color:var(--primary-dark)"/></linearGradient></defs>' +
      '<circle class="ring-track" cx="32" cy="32" r="27"/>' +
      '<circle class="ring-value" cx="32" cy="32" r="27" stroke-dasharray="' + C.toFixed(2) + '" ' +
      'style="--ring-c:' + C.toFixed(2) + ';--ring-off:' + (C * (1 - frac)).toFixed(2) + '" stroke-dashoffset="' + (C * (1 - frac)).toFixed(2) + '"/>';
    return s;
  }
  /** Decorative mini activity chart for the product window (constant markup). */
  function sparkChart() {
    const s = document.createElementNS(SVG_NS, 'svg');
    s.setAttribute('viewBox', '0 0 120 36'); s.setAttribute('class', 'app-spark'); s.setAttribute('preserveAspectRatio', 'none');
    s.setAttribute('aria-hidden', 'true'); s.setAttribute('focusable', 'false');
    s.innerHTML = '<defs><linearGradient id="spark-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" style="stop-color:var(--primary);stop-opacity:.35"/>' +
      '<stop offset="1" style="stop-color:var(--primary);stop-opacity:0"/></linearGradient></defs>' +
      '<path d="M0 30 L12 26 L24 28 L36 20 L48 22 L60 14 L72 17 L84 10 L96 12 L108 5 L120 7 L120 36 L0 36Z" fill="url(#spark-fill)"/>' +
      '<path class="spark-line" d="M0 30 L12 26 L24 28 L36 20 L48 22 L60 14 L72 17 L84 10 L96 12 L108 5 L120 7" fill="none" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>';
    return s;
  }

  R.hero = function (sec) {
    const items = sec.items || [];
    const stat = items[0];
    const rows = items.slice(1);
    const frac = stat ? scoreFraction(stat.value) : null;
    const dots = h('span', { class: 'app-dots', 'aria-hidden': 'true' }, h('i'), h('i'), h('i'));
    const card = (items.length || sec.card_title) ? h('div', { class: 'hero-visual' },
      h('div', { class: 'hero-card app-window', role: 'group', 'aria-label': sec.card_title || 'Example' },
        h('div', { class: 'app-bar', 'aria-hidden': 'true' }, dots, h('span', { class: 'app-url' }, svg('lock'), h('i')), svg('sparkles', 'app-bar-ico')),
        h('div', { class: 'app-body' },
          h('div', { class: 'app-head' },
            sec.card_title ? h('p', { class: 'hero-card-title', text: sec.card_title }) : null,
            sparkChart()),
          stat ? h('div', { class: 'hero-stat' + (frac !== null ? ' has-ring' : '') },
            frac !== null ? scoreRing(frac) : null,
            h('span', { class: 'hero-stat-text' },
              h('span', { class: 'hero-stat-value' + (stat.tone ? ' tone-' + stat.tone : ''), text: stat.value }),
              h('span', { class: 'hero-stat-label', text: stat.label }))) : null,
          rows.length ? h('dl', { class: 'hero-rows' }, rows.map((r, i) => h('div', { class: 'hero-row', style: '--i:' + i },
            h('dt', { text: r.label }), h('dd', { class: r.tone ? 'tone-' + r.tone : null, text: r.value })))) : null,
          sec.note ? h('p', { class: 'hero-note', text: sec.note }) : null)),
      h('span', { class: 'hero-orb hero-orb-a', 'aria-hidden': 'true' }),
      h('span', { class: 'hero-orb hero-orb-b', 'aria-hidden': 'true' })) : null;
    return h('section', { class: 'hero', id: sec.key, 'aria-labelledby': 'h-' + sec.key },
      h('div', { class: 'hero-grid-bg', 'aria-hidden': 'true' }),
      h('div', { class: 'wrap hero-grid' },
        h('div', { class: 'hero-copy' },
          sec.eyebrow ? h('p', { class: 'eyebrow' }, svg('sparkles'), sec.eyebrow) : null,
          h('h1', { class: 'hero-title', id: 'h-' + sec.key }, titleNodes(sec.title, sec.highlight)),
          sec.subtitle ? h('p', { class: 'hero-sub', text: sec.subtitle }) : null,
          ctas(sec, true)),
        card));
  };
  R.logos = function (sec) {
    return h('div', { class: 'logos', id: sec.key },
      h('div', { class: 'wrap logos-inner' },
        sec.title ? h('p', { class: 'logos-title', text: sec.title }) : null,
        h('ul', { class: 'logos-list', 'aria-label': sec.title || null },
          (sec.items || []).map(it => h('li', { class: 'logo-item' }, iconTile(it.icon), it.title)))));
  };
  function cardGrid(items) {
    return h('div', { class: 'grid-cards' }, (items || []).map(it =>
      h('article', { class: 'fcard' }, iconTile(it.icon),
        it.value ? h('p', { class: 'fcard-value', text: it.value }) : null,
        it.title ? h('h3', { text: it.title }) : null,
        it.description ? h('p', { text: it.description }) : null,
        link(it.label, it.url, 'link'))));
  }
  R.features = (sec, ctx, alt) => sectionShell(sec, alt, [sectionHead(sec), cardGrid(sec.items), ctas(sec)]);
  R.cards = R.features;
  function stepList(items) {
    return h('ol', { class: 'steps' }, (items || []).map(it => h('li', { class: 'step' },
      h('h3', { text: it.title }), it.description ? h('p', { text: it.description }) : null,
      link(it.label, it.url, 'link'))));
  }
  R.steps = (sec, ctx, alt) => sectionShell(sec, alt, [sectionHead(sec), stepList(sec.items), ctas(sec)]);

  R.pricing = function (sec, ctx, alt) {
    const plansHost = h('div', { class: 'plans-host' });
    const box = sectionShell(sec, alt, [sectionHead(sec), plansHost,
      h('p', { class: 'sr-only plans-status', role: 'status', 'aria-live': 'polite' }),
      (sec.items || []).length ? h('div', { class: 'buy-steps' },
        sec.card_title ? h('h3', { text: sec.card_title }) : null, stepList(sec.items), ctas(sec)) : null,
      sec.note ? h('p', { class: 'fine-print', text: sec.note }) : null]);
    renderPlans(plansHost, sec, ctx.plans);
    return box;
  };
  let cycle = 'monthly';
  function renderPlans(host, sec, plans) {
    clear(host);
    if (plans === null) {
      host.appendChild(h('div', { class: 'state-card', role: 'status' },
        h('h2', { text: 'Pricing is temporarily unavailable' }),
        h('p', { text: 'Please try again in a moment.' }),
        h('div', { class: 'cta-row' }, h('button', { type: 'button', class: 'btn btn-secondary', text: 'Retry',
          onclick: async () => { host.setAttribute('aria-busy', 'true'); renderPlans(host, sec, await soft(api('/api/public/pricing').then(d => d.plans || []))); host.removeAttribute('aria-busy'); } }),
          link((sec.cta_primary || {}).label, (sec.cta_primary || {}).url, 'btn btn-primary'))));
      return;
    }
    if (!plans.length) {
      host.appendChild(h('div', { class: 'state-card' },
        h('h2', { text: 'Plans are being updated' }),
        h('p', { text: 'Talk to us and we will recommend the right plan for your team.' }),
        h('div', { class: 'cta-row' }, link((sec.cta_primary || {}).label, (sec.cta_primary || {}).url, 'btn btn-primary'),
          h('a', { class: 'btn btn-secondary', href: '/contact', text: 'Contact us' }))));
      return;
    }
    const yearly = cycle === 'yearly';
    let bestSave = 0;
    plans.forEach(p => { const m = Number(p.price_monthly) || 0, y = Number(p.price_yearly) || 0;
      if (m > 0 && y > 0 && y < m * 12) bestSave = Math.max(bestSave, Math.round((1 - y / (m * 12)) * 100)); });
    const hasYearly = plans.some(p => Number(p.price_yearly) > 0);
    if (hasYearly) {
      const mk = (c, label) => h('button', { type: 'button', 'aria-pressed': String(cycle === c),
        onclick: () => {
          if (cycle === c) return;
          cycle = c; renderPlans(host, sec, plans);
          const b = host.querySelector('[aria-pressed="true"]'); if (b) b.focus();
          const st = host.parentNode && host.parentNode.querySelector('.plans-status'); if (st) st.textContent = (c === 'yearly' ? 'Showing yearly prices' : 'Showing monthly prices');
        } },
        label, c === 'yearly' && bestSave ? h('span', { class: 'save', text: 'Save ' + bestSave + '%' }) : null);
      host.appendChild(h('div', { class: 'pricing-center' },
        h('div', { class: 'billing-toggle' + (yearly ? ' is-yearly' : ''), role: 'group', 'aria-label': 'Billing period' }, mk('monthly', 'Monthly'), mk('yearly', 'Yearly'))));
    }
    const cta = sec.cta_primary && safeUrl(sec.cta_primary.url) ? sec.cta_primary : { label: 'Request a demo', url: '/request-demo' };
    host.appendChild(h('div', { class: 'plans' }, plans.map(p => {
      const useYear = yearly && Number(p.price_yearly) > 0;
      const price = Number(useYear ? p.price_yearly : p.price_monthly) || 0;
      const free = price === 0;
      const meta = [];
      if (useYear) meta.push('≈ ' + money(price / 12, p.currency) + ' / month, billed yearly');
      if (Number(p.trial_days) > 0) meta.push(p.trial_days + '-day trial');
      const href = safeUrl(cta.url) + (cta.url.indexOf('?') < 0 ? '?' : '&') + 'plan=' + encodeURIComponent(p.slug || '');
      return h('article', { class: 'plan' + (p.popular ? ' popular' : ''), 'aria-labelledby': 'plan-' + p.slug },
        p.popular ? h('span', { class: 'plan-badge', text: 'Most popular' }) : null,
        h('h3', { id: 'plan-' + p.slug, text: p.name }),
        h('p', { class: 'plan-desc', text: p.description || '' }),
        h('p', { class: 'plan-price' }, h('strong', { text: free ? 'Free' : money(price, p.currency) }),
          free ? null : h('span', { text: useYear ? '/ year' : '/ month' })),
        h('p', { class: 'plan-meta', text: meta.join(' · ') }),
        h('ul', { 'aria-label': 'Included' }, (p.highlights || []).map(f => h('li', null, svg('check'), f))),
        h('a', { class: 'btn w-full ' + (p.popular ? 'btn-primary' : 'btn-secondary'), href: href, text: cta.label,
          'aria-label': cta.label + ' — ' + p.name }));
    })));
  }

  R.testimonials = function (sec, ctx, alt) {
    const items = ctx.testimonials || [];
    if (!items.length) return null;                 // never show an empty testimonials block
    return sectionShell(sec, alt, [sectionHead(sec), h('div', { class: 'grid-cards' }, items.map(t => {
      const name = t.author_name || '';
      const role = [t.author_title, t.company].filter(Boolean).join(', ');
      const av = safeUrl(t.avatar_url);
      const rating = Math.max(0, Math.min(5, Number(t.rating) || 0));
      return h('figure', { class: 'quote-card' },
        rating ? h('div', { class: 'stars', role: 'img', 'aria-label': rating + ' out of 5 stars' },
          Array.from({ length: rating }, () => svg('star'))) : null,
        h('blockquote', { text: '“' + (t.quote || '') + '”' }),
        h('figcaption', null,
          av ? h('img', { class: 'avatar', src: av, alt: '', loading: 'lazy', decoding: 'async', width: 42, height: 42 })
            : h('span', { class: 'avatar', 'aria-hidden': 'true', text: (name.charAt(0) || '?').toUpperCase() }),
          h('span', null, h('span', { class: 'quote-name', text: name }), role ? h('br') : null, role ? h('span', { class: 'quote-role', text: role }) : null)));
    })), ctas(sec)]);
  };

  function faqList(items) {
    return h('div', { class: 'faq-list' }, items.map((f, i) => {
      const qid = 'faq-q-' + i, aid = 'faq-a-' + i;
      const ans = h('div', { class: 'faq-a', id: aid, role: 'region', 'aria-labelledby': qid, hidden: true, text: f.answer });
      const btn = h('button', { type: 'button', class: 'faq-q', id: qid, 'aria-expanded': 'false', 'aria-controls': aid,
        onclick: () => { const open = btn.getAttribute('aria-expanded') !== 'true'; btn.setAttribute('aria-expanded', String(open)); ans.hidden = !open; } },
        h('span', { text: f.question }), svg('chevron'));
      return h('div', { class: 'faq-item' }, h('h3', null, btn), ans);
    }));
  }
  R.faq = function (sec, ctx, alt) {
    const items = ctx.faq || [];
    if (!items.length) return null;
    const head = sectionHead(Object.assign({}, sec, { subtitle: '' }));
    return sectionShell(sec, alt, [head, h('div', { class: 'wrap-narrow', style: 'padding:0' }, faqList(items),
      (sec.subtitle || sec.cta_primary && sec.cta_primary.label) ? h('div', { class: 'section-head', style: 'margin:32px auto 0' },
        sec.subtitle ? h('p', { class: 'section-sub', text: sec.subtitle }) : null, ctas(sec)) : null)]);
  };
  R.cta = (sec) => h('section', { class: 'section', id: sec.key, 'aria-labelledby': sec.title ? 'h-' + sec.key : null },
    h('div', { class: 'wrap' }, h('div', { class: 'cta-banner' }, sectionHead(sec), ctas(sec, true))));
  R.page_header = (sec, ctx) => h('header', { class: 'page-hero', id: sec.key },
    h('div', { class: 'wrap' },
      sec.eyebrow ? h('p', { class: 'eyebrow', text: sec.eyebrow }) : null,
      h('h1', { id: 'h-' + sec.key }, titleNodes(sec.title, sec.highlight)),
      sec.subtitle ? h('p', { text: sec.subtitle }) : null,
      ctx.updated ? h('p', { class: 'page-meta', text: 'Last updated ' + ctx.updated }) : null,
      ctas(sec)));
  R.rich_text = (sec) => h('section', { class: 'section section-tight', id: sec.key, 'aria-labelledby': sec.title ? 'h-' + sec.key : null },
    h('div', { class: 'wrap-narrow' },
      sec.title ? h('h2', { class: 'prose-title', id: 'h-' + sec.key, text: sec.title }) : null,
      h('div', { class: 'prose' }, bodyNodes(sec.body))));

  /** Render a page's sections into host; alternating backgrounds on landing sections. */
  function renderSections(host, sections, ctx) {
    clear(host);
    let alt = false;
    (sections || []).forEach(sec => {
      const fn = R[sec.type];
      if (!fn || sec.type === 'contact_form') return;
      const el = fn(sec, ctx, alt);
      if (!el) return;
      if (el.classList.contains('section') && !el.querySelector('.cta-banner')) alt = !alt;
      host.appendChild(sec.type === 'hero' ? el : reveal(el));
    });
  }

  function stateCard(title, body, actions) {
    return h('div', { class: 'wrap', style: 'padding-top:72px;padding-bottom:72px' },
      h('div', { class: 'state-card', role: 'status' }, h('h2', { text: title }), body ? h('p', { text: body }) : null,
        actions ? h('div', { class: 'cta-row' }, actions) : null));
  }

  window.LeadAISite = { h, svg, clear, txt, safeUrl, link, ctas, titleNodes, sectionHead, bodyNodes, iconTile,
    api, soft, applySeo, initChrome, renderSections, stateCard, niceDate, reveal, faqList, curPath };
})();
