/* ═══════════════════════════════════════════════════════════════════════
   Runtime configuration provider (Global Settings).

   Fetches /api/public/config (public-safe values only — never secrets) and
   applies branding to the page: document title, favicon, CSS variable
   remapping (colors/theme/font), brand name / tagline / logo / footer text
   on every element carrying a data-brand-* attribute, plus SEO meta tags.

   Used by: index.html, admin.html, login.html, maintenance.html,
   url_report.html. Pages call AppConfig.load() once (fire-and-forget); the
   admin panel awaits it in boot() for nav overrides / widget config.
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
        primary: "#7c5cff", accent: "#f0a531", success: "#1fae6a",
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
    features: { url_search: true, exports: true },
    maintenance: { enabled: false, message: "" },
    localization: {
      timezone: "auto", date_format: "YYYY-MM-DD", time_format: "24h",
      currency: "USD", language: "en",
    },
    defaults: {
      comment_filter_mode: "all", keyword_preset: "", theme: "system",
      date_range: 30, page_size: 20,
    },
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

  function mergeDeep(base, extra) {
    var out = {};
    Object.keys(base).forEach(function (k) {
      var b = base[k], e = extra && extra[k];
      out[k] = (e !== undefined && e !== null && typeof b === "object" && !Array.isArray(b))
        ? mergeDeep(b, e)
        : (e !== undefined && e !== null ? e : b);
    });
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

  function applyVars() {
    var c = cfg.branding.colors;
    var vars = {
      "--violet": c.primary,
      "--violet-deep": shade(c.primary, 0.82),
      "--violet-soft": hexToRgba(c.primary, 0.12),
      "--amber": c.accent,
      "--amber-deep": shade(c.accent, 0.82),
      "--amber-soft": hexToRgba(c.accent, 0.12),
      "--green": c.success,
      "--green-soft": hexToRgba(c.success, 0.12),
      "--red": c.danger,
      "--red-soft": hexToRgba(c.danger, 0.12),
      "--blue": c.info,
      "--blue-soft": hexToRgba(c.info, 0.12),
      "--brand-primary": c.primary,
      "--brand-accent": c.accent,
      "--brand-success": c.success,
      "--brand-warning": c.warning,
      "--brand-danger": c.danger,
      "--brand-info": c.info,
    };
    var font = FONT_FAMILIES[cfg.branding.font] || FONT_FAMILIES.inter;
    vars["--font-ui"] = font;
    var css = ":root{" + Object.keys(vars).map(function (k) {
      return k + ":" + vars[k] + ";";
    }).join("") + "}" +
      ".brand-img{max-width:100%;max-height:100%;object-fit:contain;display:block}" +
      "[data-brand-logo].has-img{background:transparent!important;border:none!important;box-shadow:none!important}" +
      ".logo-badge.has-img{width:auto;height:56px;padding:4px 8px}" +
      "[data-brand-company]:empty,[data-brand-support]:empty{display:none}";
    var style = document.getElementById("brand-vars");
    if (!style) {
      style = document.createElement("style");
      style.id = "brand-vars";
      document.head.appendChild(style);
    }
    style.textContent = css;
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

  function applyBrandMarkers() {
    document.querySelectorAll("[data-brand-name]").forEach(function (el) {
      if (cfg.app.name) el.textContent = cfg.app.name;
    });
    document.querySelectorAll("[data-brand-sub]").forEach(function (el) {
      var kind = el.getAttribute("data-brand-sub");
      if (kind === "sidebar") {
        if (cfg.appearance.sidebar_title && document.querySelector("[data-brand-name]")) {
          el.textContent = cfg.appearance.sidebar_subtitle || "Admin Control Center";
        } else if (cfg.app.tagline) {
          el.textContent = cfg.app.tagline;
        }
      } else if (kind === "company") {
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
      if (cfg.contact.support_url) {
        var u = document.createElement("a");
        u.href = cfg.contact.support_url;
        u.textContent = cfg.contact.support_url;
        parts.push(u);
      }
      parts.forEach(function (node, i) {
        if (i > 0) el.appendChild(document.createTextNode(" · "));
        el.appendChild(node);
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
    applyTitle();
    applyFavicon();
    applyVars();
    applyFont();
    applyMeta();
    applyBrandMarkers();
  }

  function load() {
    if (loaded) return loaded;
    loaded = fetch("/api/public/config", {
      headers: { Accept: "application/json" },
      cache: "no-store",
    })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        cfg = mergeDeep(DEFAULTS, data || {});
        apply();
        return cfg;
      })
      .catch(function () {
        cfg = mergeDeep(DEFAULTS, {});
        apply();
        return cfg;
      });
    return loaded;
  }

  // Re-fetch after an admin saves settings and re-apply branding.
  function refresh() {
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