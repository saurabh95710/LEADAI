/* ═══════════════════════════════════════════════════════════════════════
   Runtime configuration provider (Global Settings).

   Fetches /api/public/config (public-safe values only — never secrets) and
   applies branding to the page: document title, favicon, CSS variable
   remapping (colors/theme/font), brand name / tagline / logo / footer text
   on every element carrying a data-brand-* attribute, plus SEO meta tags.

   Used by: index.html, login.html, maintenance.html, url_report.html, the
   public website pages and (brand-only) the admin consoles. Pages call
   AppConfig.load() once (fire-and-forget). A page with
   <html data-appconfig="brand-only"> (the public website, which renders its
   own titles and SEO server-side, and the admin consoles, whose name / logo
   come from design/theme.js) only takes the brand colours, default colour
   mode, favicon and font.

   AppConfig.get() returns the whole payload: DEFAULTS fill the gaps, and keys
   DEFAULTS doesn't list (features.demo_registration, trial, cost, ...) are kept.

   Freshness: the last config is shown at once (sessionStorage), then the
   current one is always fetched and applied, so a Global Settings change shows
   on the next page load.
   ═══════════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  var DEFAULTS = {
    app: {
      name: "LeadAI",
      short_name: "LeadAI",
      tagline: "AI Lead Intelligence",
      description: "AI-powered social lead intelligence platform.",
    },
    company: { name: "", website: "" },
    contact: { support_email: "", support_phone: "", support_url: "" },
    links: { privacy_url: "", terms_url: "", docs_url: "", help_url: "" },
    branding: {
      logo_primary: "", logo_dark: "", logo_light: "", logo_compact: "",
      logo_login: "", logo_email: "", favicon: "", apple_touch_icon: "",
      colors: {
        primary: "#2e9573", accent: "#d9a441", success: "#1fae6a",
        warning: "#f0a531", danger: "#e5484d", info: "#3b82f6",
      },
      theme: "light", font: "inter", white_label: false,
      login_heading: "Welcome Back",
      login_subtext: "Sign in to continue to your AI-powered lead intelligence dashboard.",
      footer_text: "Protected by secure authentication",
    },
    appearance: {
      sidebar_title: "", sidebar_subtitle: "Admin Control Center",
      sidebar_collapsed_default: false, show_icons: true,
      show_section_labels: true, show_footer_links: true,
      nav_overrides: {}, dashboard_widgets: {},
    },
    seo: { meta_description: "", og_title: "", og_image: "" },
    features: { url_search: true, exports: true, demo_registration: true, ai_analysis: true },
    trial: {},
    cost: { warn_before_expensive: false },   // off until the server sends it
    maintenance: { enabled: false, message: "" },
    localization: {
      timezone: "auto", date_format: "YYYY-MM-DD", time_format: "24h",
      currency: "USD", language: "en",
    },
    defaults: { date_range: 30, page_size: 20 },
  };

  var FONT_LINKS = {
    inter: "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap",
    roboto: "https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;600;700&display=swap",
    manrope: "https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap",
    "plus-jakarta-sans": "https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap",
    system: "",
  };
  var FONT_FAMILIES = {
    inter: '"Inter", system-ui, -apple-system, sans-serif',
    roboto: '"Roboto", system-ui, -apple-system, sans-serif',
    manrope: '"Manrope", "Inter", system-ui, sans-serif',
    "plus-jakarta-sans": '"Plus Jakarta Sans", "Inter", system-ui, sans-serif',
    system: "system-ui, -apple-system, sans-serif",
  };

  var cfg = null;
  var loaded = null;

  function isPlainObject(v) { return v !== null && typeof v === "object" && !Array.isArray(v); }

  // DEFAULTS fill what the payload lacks; payload keys DEFAULTS doesn't know are kept
  function mergeDeep(base, extra) {
    var out = {};
    Object.keys(base).forEach(function (k) {
      var b = base[k], e = isPlainObject(extra) ? extra[k] : undefined;
      out[k] = (e !== undefined && e !== null && isPlainObject(b))
        ? mergeDeep(b, e)
        : (e !== undefined && e !== null ? e : b);
    });
    if (isPlainObject(extra)) {
      Object.keys(extra).forEach(function (k) {
        if (!(k in out) && extra[k] !== undefined && extra[k] !== null) out[k] = extra[k];
      });
    }
    return out;
  }

  function hexToRgba(hex, alpha) {
    var m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(hex || "").trim());
    if (!m) return null;
    var h = m[1];
    if (h.length === 3) h = h.split("").map(function (c) { return c + c; }).join("");
    var n = parseInt(h, 16);
    return "rgba(" + ((n >> 16) & 255) + ", " + ((n >> 8) & 255) + ", " + (n & 255) + ", " + alpha + ")";
  }

  function shade(hex, factor) {
    var m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(hex || "").trim());
    if (!m) return hex;
    var h = m[1];
    if (h.length === 3) h = h.split("").map(function (c) { return c + c; }).join("");
    var n = parseInt(h, 16);
    var r = Math.round(((n >> 16) & 255) * factor);
    var g = Math.round(((n >> 8) & 255) * factor);
    var b = Math.round((n & 255) * factor);
    return "#" + ((1 << 24) | (r << 16) | (g << 8) | b).toString(16).slice(1);
  }

  function applyTitle() {
    if (!cfg.app.name || cfg.app.name === "LeadAI") return;
    var title = document.title || "";
    if (title.indexOf("LeadAI") !== -1) {
      document.title = title.replace(/LeadAI/g, cfg.app.name);
    } else {
      document.title = cfg.app.name + (title ? " — " + title : "");
    }
  }

  function applyFavicon() {
    var fav = cfg.branding.favicon;
    if (fav) {
      var links = document.querySelectorAll('link[rel~="icon"]');
      if (links.length) links[0].setAttribute("href", fav);
      else {
        var l = document.createElement("link");
        l.rel = "icon"; l.href = fav;
        document.head.appendChild(l);
      }
    }
    if (cfg.branding.apple_touch_icon) {
      var at = document.querySelector('link[rel="apple-touch-icon"]');
      if (!at) { at = document.createElement("link"); at.rel = "apple-touch-icon"; document.head.appendChild(at); }
      at.href = cfg.branding.apple_touch_icon;
    }
  }

  function tint(hex, amount) {          // mix with white (dark-theme variant)
    var h = String(hex).replace("#", "");
    var n = parseInt(h, 16), r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
    var mix = function (v) { return Math.round(v + (255 - v) * amount); };
    return "#" + [mix(r), mix(g), mix(b)].map(function (v) { return ("0" + v.toString(16)).slice(-2); }).join("");
  }
  function tokenSet(p) {
    return {
      "--primary": p, "--primary-hover": shade(p, 0.88), "--primary-dark": shade(p, 0.8),
      "--primary-fill": p, "--primary-fill-hover": shade(p, 0.88), "--primary-text": shade(p, 0.72),
      "--primary-soft": hexToRgba(p, 0.13), "--primary-light": hexToRgba(p, 0.15),
      "--primary-border": hexToRgba(p, 0.36),
    };
  }
  // one colour role (--success, --success-text, -soft, -border) as tokens.css
  // names it; dark mode gets a lighter base / text so it stays readable
  function roleSet(name, c, dark) {
    var set = {};
    set["--" + name] = dark ? tint(c, 0.15) : c;
    set["--" + name + "-text"] = dark ? tint(c, 0.4) : shade(c, 0.62);
    set["--" + name + "-soft"] = hexToRgba(c, dark ? 0.13 : 0.1);
    set["--" + name + "-border"] = hexToRgba(c, dark ? 0.34 : 0.3);
    return set;
  }
  function assign(target, set) { Object.keys(set).forEach(function (k) { target[k] = set[k]; }); }
  function decl(set) { return Object.keys(set).map(function (k) { return k + ":" + set[k] + ";"; }).join(""); }

  function applyVars() {
    var c = cfg.branding.colors;
    // raw brand values (for custom CSS); the pages themselves read the design tokens below
    var vars = {
      "--brand-primary": c.primary,
      "--brand-accent": c.accent,
      "--brand-success": c.success,
      "--brand-warning": c.warning,
      "--brand-danger": c.danger,
      "--brand-info": c.info,
    };
    // The design tokens (design/tokens.css; styles.css and login.html alias
    // --violet / --amber / --green / --red / --blue to them) follow every
    // colour CHANGED from its default, in light and dark; a default keeps the
    // designed palette exactly. accent = the gold --secondary role.
    var changed = function (k) {
      return /^#[0-9a-f]{6}$/i.test(c[k] || "") && c[k].toLowerCase() !== DEFAULTS.branding.colors[k];
    };
    var light = {}, dark = {};
    if (changed("primary")) { assign(light, tokenSet(c.primary)); assign(dark, tokenSet(tint(c.primary, 0.45))); }
    if (changed("accent")) {
      assign(light, roleSet("secondary", c.accent, false));
      assign(dark, roleSet("secondary", c.accent, true));
      light["--secondary-hover"] = shade(c.accent, 0.92);
      dark["--secondary-hover"] = tint(c.accent, 0.3);
    }
    ["success", "warning", "danger", "info"].forEach(function (k) {
      if (!changed(k)) return;
      assign(light, roleSet(k, c[k], false));
      assign(dark, roleSet(k, c[k], true));
      light["--" + k + "-fill"] = dark["--" + k + "-fill"] = shade(c[k], 0.9);
    });
    var tokenCss = "";
    if (Object.keys(light).length) {
      // same selectors as design/tokens.css (dark is the base, light is
      // [data-theme="light"] or the system preference), declared after it
      tokenCss = ":root,:root[data-theme=\"dark\"]{" + decl(dark) + "}" +
        ":root[data-theme=\"light\"]{" + decl(light) + "}" +
        "@media (prefers-color-scheme: light){:root:not([data-theme=\"dark\"]){" + decl(light) + "}}";
    }
    // a changed UI font beats the pages' own :root:root font aliases
    var fontKey = cfg.branding.font || "inter";
    var fontCss = (fontKey !== "inter" && FONT_FAMILIES[fontKey])
      ? ":root:root:root{--font-ui:" + FONT_FAMILIES[fontKey] + ";--font-sans:" + FONT_FAMILIES[fontKey] + ";}"
      : "";
    var css = ":root{" + decl(vars) + "}" + tokenCss + fontCss +
      ".brand-img{max-width:100%;max-height:100%;object-fit:contain;display:block}" +
      "[data-brand-logo].has-img{background:transparent!important;border:none!important;box-shadow:none!important}" +
      ".logo-badge.has-img{width:auto;height:56px;padding:4px 8px}" +
      "[data-brand-company]:empty,[data-brand-support]:empty,[data-brand-links]:empty{display:none}" +
      "[data-brand-links] a+a::before{content:\"\\00b7\";display:inline-block;margin:0 .5em;opacity:.6}";
    var style = document.getElementById("brand-vars");
    if (!style) {
      style = document.createElement("style");
      style.id = "brand-vars";
      document.head.appendChild(style);
    }
    style.textContent = css;
  }

  // branding.theme is the colour mode for visitors who haven't picked one
  // (a stored choice from the theme toggle still wins). design/theme.js owns
  // the mode; the value is remembered so the next page paints in it at once.
  function applyThemeDefault() {
    var t = cfg.branding.theme;
    if (t !== "light" && t !== "dark" && t !== "system") return;
    try { localStorage.setItem("leadai_brand_theme", t); } catch (_) {}
    if (window.LeadAITheme && window.LeadAITheme.setDefault) window.LeadAITheme.setDefault(t);
  }

  function applyFont() {
    var name = cfg.branding.font || "inter";
    var url = FONT_LINKS[name];
    if (!url) return;
    if (document.querySelector('link[data-brand-font]')) return;
    var link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = url;
    link.setAttribute("data-brand-font", "1");
    document.head.appendChild(link);
  }

  function applyMeta() {
    var desc = cfg.seo.meta_description;
    if (desc) {
      var m = document.querySelector('meta[name="description"]');
      if (m) m.setAttribute("content", desc);
    }
    if (cfg.seo.og_title) setOg("og:title", cfg.seo.og_title);
    if (desc) setOg("og:description", desc);
    if (cfg.seo.og_image) setOg("og:image", cfg.seo.og_image);
  }

  function setOg(prop, content) {
    var el = document.querySelector('meta[property="' + prop + '"]');
    if (!el) { el = document.createElement("meta"); el.setAttribute("property", prop); document.head.appendChild(el); }
    el.setAttribute("content", content);
  }

  // http(s), mailto: or a same-site path; anything else (javascript:, data:) is dropped
  function safeLink(url) {
    var u = String(url || "").trim();
    if (!u) return "";
    if (/^(https?:\/\/|mailto:)/i.test(u)) return u;
    if (u.charAt(0) === "/" && u.charAt(1) !== "/" && u.charAt(1) !== "\\") return u;
    return "";
  }

  function applyBrandMarkers() {
    document.querySelectorAll("[data-brand-name]").forEach(function (el) {
      if (cfg.app.name) el.textContent = cfg.app.name;
    });
    document.querySelectorAll("[data-brand-sub]").forEach(function (el) {
      var kind = el.getAttribute("data-brand-sub");
      if (kind === "company") {
        if (cfg.company.name) el.textContent = cfg.company.name;
      } else if (cfg.app.tagline) {
        el.textContent = cfg.app.tagline;
      }
    });
    document.querySelectorAll("[data-brand-logo]").forEach(function (el) {
      var slot = el.getAttribute("data-brand-logo") || "primary";
      var url = cfg.branding["logo_" + slot] || cfg.branding.logo_primary;
      if (!url) return;
      if (!el.classList.contains("has-img")) {
        el.classList.add("has-img");
        el.innerHTML = "";
      }
      var img = el.querySelector("img");
      if (!img) {
        img = document.createElement("img");
        img.className = "brand-img";
        img.alt = cfg.app.name || "Logo";
        img.loading = "eager";
        el.appendChild(img);
      }
      img.src = url;
    });
    document.querySelectorAll("[data-brand-footer]").forEach(function (el) {
      if (cfg.branding.footer_text) el.textContent = cfg.branding.footer_text;
    });
    document.querySelectorAll("[data-brand-company]").forEach(function (el) {
      el.textContent = cfg.company.name ? "© " + cfg.company.name : "";
    });
    document.querySelectorAll("[data-brand-support]").forEach(function (el) {
      el.textContent = "";
      var parts = [];
      if (cfg.contact.support_email) {
        var a = document.createElement("a");
        a.href = "mailto:" + cfg.contact.support_email;
        a.textContent = cfg.contact.support_email;
        parts.push(a);
      }
      if (cfg.contact.support_phone) {
        var p = document.createElement("a");
        p.href = "tel:" + String(cfg.contact.support_phone).replace(/[^\d+]/g, "");
        p.textContent = cfg.contact.support_phone;
        parts.push(p);
      }
      parts.forEach(function (node, i) {
        if (i > 0) el.appendChild(document.createTextNode(" · "));
        el.appendChild(node);
      });
    });
    // Help / Docs / Support / Privacy / Terms links (General settings); each
    // shows only when set, and the row hides when none is
    document.querySelectorAll("[data-brand-links]").forEach(function (el) {
      el.textContent = "";
      if (cfg.appearance.show_footer_links === false) return;
      var links = cfg.links || {};
      [[links.help_url, "Help"], [links.docs_url, "Docs"], [cfg.contact.support_url, "Support"],
       [links.privacy_url, "Privacy"], [links.terms_url, "Terms"]].forEach(function (pair) {
        var href = safeLink(pair[0]);
        if (!href) return;
        var a = document.createElement("a");
        a.href = href;
        a.textContent = pair[1];
        if (/^https?:/i.test(href)) { a.target = "_blank"; a.rel = "noopener"; }
        el.appendChild(a);
      });
    });
    document.querySelectorAll("[data-brand-login-heading]").forEach(function (el) {
      if (cfg.branding.login_heading) el.textContent = cfg.branding.login_heading;
    });
    document.querySelectorAll("[data-brand-login-subtext]").forEach(function (el) {
      if (cfg.branding.login_subtext) el.textContent = cfg.branding.login_subtext;
    });
    document.querySelectorAll("[data-brand-maintenance-message]").forEach(function (el) {
      if (cfg.maintenance.message) el.textContent = cfg.maintenance.message;
    });
  }

  function apply() {
    var brandOnly = document.documentElement.getAttribute("data-appconfig") === "brand-only";
    if (!brandOnly) applyTitle();
    applyFavicon();
    applyVars();
    applyThemeDefault();
    applyFont();
    if (!brandOnly) {
      applyMeta();
      applyBrandMarkers();
    }
  }

  var CONFIG_CACHE_KEY = "leadai_public_config_v1";
  var CONFIG_CACHE_TTL = 300000; // 5 minutes

  function load() {
    if (loaded) return loaded;

    // Show the last known config at once (no flash of the default brand)…
    var shown = null;
    try {
      var cached = sessionStorage.getItem(CONFIG_CACHE_KEY);
      if (cached) {
        var parsed = JSON.parse(cached);
        if (parsed && parsed.ts && (Date.now() - parsed.ts < CONFIG_CACHE_TTL) && parsed.data) {
          shown = JSON.stringify(parsed.data);
          cfg = mergeDeep(DEFAULTS, parsed.data);
          apply();
        }
      }
    } catch (_) {}

    // …then always fetch the current one, so a saved change shows on the next page load
    loaded = fetch("/api/public/config", {
      headers: { Accept: "application/json" }, cache: "no-cache",
    })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data && cfg) return cfg;                     // offline: keep what is shown
        cfg = mergeDeep(DEFAULTS, data || {});
        try {
          sessionStorage.setItem(CONFIG_CACHE_KEY, JSON.stringify({ ts: Date.now(), data: data }));
        } catch (_) {}
        if (JSON.stringify(data) !== shown) apply();
        return cfg;
      })
      .catch(function () {
        if (cfg) return cfg;
        cfg = mergeDeep(DEFAULTS, {});
        apply();
        return cfg;
      });
    return loaded;
  }

  // Re-fetch after an admin saves settings and re-apply branding.
  function refresh() {
    try { sessionStorage.removeItem(CONFIG_CACHE_KEY); } catch (_) {}
    loaded = null;
    cfg = null;
    return load();
  }

  window.AppConfig = {
    load: load,
    get: function () { return cfg || mergeDeep(DEFAULTS, {}); },
    apply: apply,
    refresh: refresh,
  };
})();