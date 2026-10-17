/* LeadAI interactive guide ("tour") — one engine for every portal.
 *
 *   LeadTour.setUser("owner@acme.com");          // remembers per person (optional)
 *   LeadTour.define("sa-main", { title: "Super Admin basics", steps: [
 *     { target: "#nav-dashboard", title: "Dashboard", body: "Platform health at a glance." },
 *     { route: "#/organizations", target: "[data-tour=org-list]", title: "Organizations",
 *       body: "Every customer workspace.", placement: "bottom" },
 *     { title: "That's it", body: "Reopen this guide any time from the Guide button." },  // no target = centred
 *   ]});
 *   LeadTour.mountLauncher({ container: headerEl, tours: [{ id: "sa-main", label: "Portal basics" }] });
 *   LeadTour.autoStart("sa-main");               // first visit only
 *
 * Step fields: target (selector | function → Element), title, body (text; "\n\n" = new
 * paragraph) or html (trusted, from code only), route (hash to open first, or a function
 * that navigates), before (async fn run first), placement (auto|top|bottom|left|right|center),
 * optional (skip silently when the target isn't on screen), wait (ms to wait for the target,
 * default 3000 when the step has a route/before, else 800), padding (px around the highlight).
 *
 * Accessible: role=dialog, focus kept in the guide, Esc closes, ←/→ move, focus is
 * restored at the end. Completion is remembered in localStorage (per person when
 * setUser was called); storage failures are ignored.
 */
(function () {
  "use strict";
  if (window.LeadTour) return;

  var tours = {};
  var user = "";
  var active = null;          // { id, tour, index, root, spot, pop, block, prevFocus, onKey, onMove }
  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function key(id) { return "leadai.tour." + (user ? user + "." : "") + id; }
  function read(id) { try { return localStorage.getItem(key(id)); } catch (e) { return null; } }
  function write(id, v) { try { if (v == null) localStorage.removeItem(key(id)); else localStorage.setItem(key(id), v); } catch (e) { /* private mode */ } }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  function resolveTarget(t) {
    if (!t) return null;
    try {
      var el = typeof t === "function" ? t() : document.querySelector(t);
      if (!el || !el.getBoundingClientRect) return null;
      var r = el.getBoundingClientRect();
      var cs = window.getComputedStyle(el);
      if ((r.width === 0 && r.height === 0) || cs.visibility === "hidden" || cs.display === "none") return null;
      return el;
    } catch (e) { return null; }
  }

  /** Resolve once the target stops moving (smooth scrolling finished), at most ~0.8s. */
  async function settled(el) {
    if (reduceMotion) return;
    var lastTop = null, still = 0;
    for (var t = 0; t < 16 && still < 2; t++) {
      await sleep(50);
      var top = el.getBoundingClientRect().top;
      still = lastTop !== null && Math.abs(top - lastTop) < 1 ? still + 1 : 0;
      lastTop = top;
    }
  }

  async function waitFor(step) {
    // a step that opens a page first may need a moment to render; otherwise don't keep people waiting
    var limit = step.wait != null ? step.wait : (step.route || step.before ? 3000 : 800);
    var t0 = Date.now();
    var el = resolveTarget(step.target);
    while (!el && Date.now() - t0 < limit) { await sleep(120); el = resolveTarget(step.target); }
    return el;
  }

  function build() {
    var root = document.createElement("div");
    root.className = "lt-root" + (reduceMotion ? " lt-still" : "");
    root.innerHTML = '<div class="lt-block" aria-hidden="true"></div><div class="lt-spot" aria-hidden="true"></div>' +
      '<div class="lt-pop" role="dialog" aria-modal="true" aria-labelledby="ltTitle" aria-describedby="ltBody" tabindex="-1">' +
      '<div class="lt-head"><span class="lt-progress" id="ltProgress"></span>' +
      '<button type="button" class="lt-x" data-lt="close" aria-label="Close the guide">&times;</button></div>' +
      '<h2 class="lt-title" id="ltTitle"></h2><div class="lt-body" id="ltBody"></div>' +
      '<div class="lt-dots" aria-hidden="true"></div>' +
      '<div class="lt-actions"><button type="button" class="lt-btn lt-link" data-lt="skip">Skip guide</button>' +
      '<span class="lt-spacer"></span><button type="button" class="lt-btn" data-lt="back">Back</button>' +
      '<button type="button" class="lt-btn lt-primary" data-lt="next">Next</button></div></div>';
    document.body.appendChild(root);
    return { root: root, block: root.querySelector(".lt-block"), spot: root.querySelector(".lt-spot"), pop: root.querySelector(".lt-pop") };
  }

  function place(el, step) {
    var a = active; if (!a) return;
    var pop = a.pop, spot = a.spot;
    var vw = window.innerWidth, vh = window.innerHeight, m = 12;
    var narrow = vw < 640;
    pop.classList.toggle("lt-sheet", narrow);
    if (!narrow) pop.classList.remove("lt-sheet-top");
    if (!el || step.placement === "center") {
      spot.style.display = "none";
      a.block.classList.add("lt-dim");
      pop.classList.add("lt-center");
      pop.style.left = pop.style.top = "";
      return;
    }
    a.block.classList.remove("lt-dim");
    pop.classList.remove("lt-center");
    var pad = step.padding != null ? step.padding : 6;
    var r = el.getBoundingClientRect();
    spot.style.display = "block";
    spot.style.left = Math.max(0, r.left - pad) + "px";
    spot.style.top = Math.max(0, r.top - pad) + "px";
    spot.style.width = Math.min(vw, r.width + pad * 2) + "px";
    spot.style.height = Math.min(vh, r.height + pad * 2) + "px";
    if (narrow) {                       // sheet at the bottom, or the top when it would cover the target
      pop.style.left = pop.style.top = "";
      var sh = pop.offsetHeight;
      pop.classList.toggle("lt-sheet-top", r.bottom + pad > vh - sh - 24 && r.top - pad > sh + 24);
      return;
    }
    var pw = pop.offsetWidth, ph = pop.offsetHeight;
    var space = { bottom: vh - r.bottom, top: r.top, right: vw - r.right, left: r.left };
    var want = step.placement && step.placement !== "auto" ? step.placement : null;
    var fits = function (p) { return (p === "bottom" || p === "top") ? space[p] >= ph + m * 2 : space[p] >= pw + m * 2; };
    var p = want && fits(want) ? want : ["bottom", "right", "top", "left"].filter(fits)[0] || "corner";
    var left, top;
    if (p === "corner") {
      // a big target with no room beside it: a screen corner away from where most of it is
      var visTop = Math.max(0, r.top), visBottom = Math.min(vh, r.bottom);
      left = vw - pw - m;
      top = (visTop + visBottom) / 2 > vh / 2 ? m : vh - ph - m;
    } else if (p === "bottom" || p === "top") {
      left = r.left + r.width / 2 - pw / 2;
      top = p === "bottom" ? r.bottom + pad + m : r.top - pad - m - ph;
    } else {
      top = r.top + r.height / 2 - ph / 2;
      left = p === "right" ? r.right + pad + m : r.left - pad - m - pw;
    }
    pop.style.left = Math.round(Math.min(Math.max(m, left), vw - pw - m)) + "px";
    pop.style.top = Math.round(Math.min(Math.max(m, top), vh - ph - m)) + "px";
    pop.setAttribute("data-placement", p);
  }

  function render(step, el) {
    var a = active, i = a.index, steps = a.tour.steps;
    // steps skipped because they aren't on screen don't count — including later
    // optional steps on this same page whose target is already known to be missing
    var samePage = true;
    var shown = steps.map(function (st, k) {
      if (a.skipped[k]) return false;
      if (k <= i) return true;
      if (st.route || st.before) samePage = false;
      return !(samePage && st.optional && st.target && !resolveTarget(st.target));
    });
    var last = shown.lastIndexOf(true);
    a.lastShown = last;
    var n = shown.filter(Boolean).length, pos = shown.slice(0, i + 1).filter(Boolean).length;
    a.pop.querySelector("#ltProgress").textContent = (a.tour.title ? a.tour.title + " · " : "") + "Step " + pos + " of " + n;
    a.pop.querySelector("#ltTitle").textContent = step.title || "";
    var body = a.pop.querySelector("#ltBody");
    if (step.html) body.innerHTML = step.html;
    else body.innerHTML = String(step.body || "").split(/\n{2,}/).map(function (p) { return "<p>" + esc(p).replace(/\n/g, "<br>") + "</p>"; }).join("");
    if (!el && step.target && !step.optional) {
      body.insertAdjacentHTML("beforeend", '<p class="lt-note">This part isn\'t on screen right now (it may depend on your role or data).</p>');
    }
    a.pop.querySelector(".lt-dots").innerHTML = a.tour.steps.map(function (_, k) {
      return shown[k] ? '<span class="' + (k === i ? "on" : k < i ? "done" : "") + '"></span>' : "";
    }).join("");
    a.pop.querySelector('[data-lt="back"]').disabled = i === 0;
    var next = a.pop.querySelector('[data-lt="next"]');
    next.textContent = i >= last ? (a.tour.doneLabel || "Done") : "Next";
    a.pop.querySelector('[data-lt="skip"]').hidden = i >= last;
    place(el, step);
    next.focus({ preventScroll: true });
  }

  async function go(i, dir) {
    var a = active; if (!a) return;
    var steps = a.tour.steps;
    if (i < 0) i = 0;
    if (i >= steps.length) return end(true);
    a.index = i;
    var step = steps[i];
    a.root.classList.add("lt-busy");
    try {
      if (typeof step.route === "function") await step.route();
      else if (step.route && location.hash !== step.route) location.hash = step.route;
      if (step.before) await step.before();
    } catch (e) { /* a failed preparation never breaks the guide */ }
    if (!active || active !== a || a.index !== i) return;
    var el = step.target ? await waitFor(step) : null;
    if (!active || active !== a || a.index !== i) return;
    a.root.classList.remove("lt-busy");
    if (!el && step.target && step.optional) { a.skipped[i] = true; return go(i + (dir || 1), dir); }
    delete a.skipped[i];
    a.el = el;
    if (el) {
      // a target taller than most of the screen: show its top (below a sticky header), not its middle
      var tall = el.getBoundingClientRect().height > window.innerHeight * 0.7;
      var prevMargin = el.style.scrollMarginTop;
      if (tall) el.style.scrollMarginTop = "88px";
      el.scrollIntoView({ block: tall ? "start" : "center", inline: "nearest", behavior: reduceMotion ? "auto" : "smooth" });
      if (tall) el.style.scrollMarginTop = prevMargin;
      await settled(el);
      if (!active || active !== a || a.index !== i) return;
    }
    render(step, el);
  }

  function end(finished) {
    var a = active; if (!a) return;
    active = null;
    write(a.id, finished ? "done" : "dismissed");
    window.removeEventListener("keydown", a.onKey, true);
    window.removeEventListener("resize", a.onMove);
    window.removeEventListener("scroll", a.onMove, true);
    a.root.remove();
    document.documentElement.classList.remove("lt-open");
    var back = a.prevFocus && document.contains(a.prevFocus) && a.prevFocus.offsetParent !== null ? a.prevFocus : a.returnFocus;
    if (!back || !document.contains(back)) back = document.querySelector(".lt-launch-btn");
    if (back && document.contains(back)) { try { back.focus({ preventScroll: true }); } catch (e) { /* ignore */ } }
    try { if (a.tour.onEnd) a.tour.onEnd(finished); } catch (e) { /* ignore */ }
    try { window.dispatchEvent(new CustomEvent("leadtour:end", { detail: { id: a.id, finished: finished } })); } catch (e) { /* ignore */ }
    refreshLaunchers();
  }

  function start(id, opts) {
    var tour = tours[id];
    if (!tour || !tour.steps || !tour.steps.length) return false;
    if (active) end(false);
    opts = opts || {};
    var dom = build();
    var a = active = { id: id, tour: tour, index: 0, root: dom.root, block: dom.block, spot: dom.spot, pop: dom.pop,
      prevFocus: document.activeElement, returnFocus: opts.returnFocus || null, el: null, skipped: {} };
    document.documentElement.classList.add("lt-open");
    dom.root.addEventListener("click", function (e) {
      var b = e.target.closest("[data-lt]");
      if (!b) return;
      var act = b.getAttribute("data-lt");
      if (act === "next") { if (a.lastShown != null && a.index >= a.lastShown) end(true); else go(a.index + 1, 1); }
      else if (act === "back") go(a.index - 1, -1);
      else end(false);
    });
    a.onKey = function (e) {
      if (active !== a) return;
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); end(false); return; }
      var inField = /input|textarea|select/i.test((e.target && e.target.tagName) || "");
      if (!inField && e.key === "ArrowRight") {
        e.preventDefault();
        if (a.lastShown != null && a.index >= a.lastShown) end(true); else go(a.index + 1, 1);
      }
      else if (!inField && e.key === "ArrowLeft" && a.index > 0) { e.preventDefault(); go(a.index - 1, -1); }
      else if (e.key === "Tab") {        // keep focus inside the guide
        var f = Array.prototype.filter.call(a.pop.querySelectorAll("button:not([disabled]):not([hidden]), a[href]"),
          function (x) { return x.offsetParent !== null; });
        if (!f.length) return;
        var first = f[0], last = f[f.length - 1];
        if (!a.pop.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
        else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    };
    var raf = 0;
    a.onMove = function () {
      if (raf) return;
      raf = requestAnimationFrame(function () { raf = 0; if (active === a) place(resolveTarget(a.tour.steps[a.index].target) || a.el, a.tour.steps[a.index]); });
    };
    window.addEventListener("keydown", a.onKey, true);
    window.addEventListener("resize", a.onMove);
    window.addEventListener("scroll", a.onMove, true);
    go(opts.step || 0, 1);
    return true;
  }

  /** Start once per person: not if they finished or skipped it, nor while a dialog is open. */
  function autoStart(id, opts) {
    opts = opts || {};
    if (read(id) || active || !tours[id]) return false;
    setTimeout(function () {
      if (read(id) || active) return;
      var modalOpen = document.querySelector('[role="dialog"][aria-modal="true"]:not(.lt-pop), dialog[open], .modal.show, .modal-backdrop:not(.hidden)');
      if (modalOpen && modalOpen.offsetParent !== null) return;
      start(id);
    }, opts.delay != null ? opts.delay : 900);
    return true;
  }

  // ── Guide button + menu ───────────────────────────────────────────────────
  var launchers = [];

  function refreshLaunchers() {
    launchers.forEach(function (l) { if (l.menu && !l.menu.hidden) fillMenu(l); });
  }

  function fillMenu(l) {
    var list = typeof l.tours === "function" ? l.tours() : l.tours;
    l.menu.innerHTML = '<div class="lt-menu-title">' + esc(l.heading || "Interactive guides") + '</div>' +
      list.filter(function (t) { return tours[t.id]; }).map(function (t) {
        var st = read(t.id);
        return '<button type="button" role="menuitem" class="lt-menu-item" data-tour-id="' + esc(t.id) + '">' +
          '<span class="lt-menu-check" aria-hidden="true">' + (st === "done" ? "✓" : "") + '</span>' +
          '<span><b>' + esc(t.label || tours[t.id].title || t.id) + '</b>' +
          (t.description ? '<small>' + esc(t.description) + '</small>' : '') + '</span>' +
          '<span class="lt-sr">' + (st === "done" ? " (completed)" : "") + '</span></button>';
      }).join("") +
      '<button type="button" role="menuitem" class="lt-menu-reset" data-tour-reset="1">Show the welcome guide again next time</button>';
  }

  function mountLauncher(opts) {
    opts = opts || {};
    var wrap = document.createElement("span");
    wrap.className = "lt-launch" + (opts.container ? "" : " lt-floating") + (opts.className ? " " + opts.className : "");
    wrap.innerHTML = '<button type="button" class="lt-launch-btn" aria-haspopup="menu" aria-expanded="false" title="' +
      esc(opts.title || "Interactive guide: how this portal works") + '">' +
      '<span class="lt-launch-ico" aria-hidden="true">?</span><span class="lt-launch-label">' + esc(opts.label || "Guide") + '</span></button>' +
      '<div class="lt-menu" role="menu" hidden></div>';
    var btn = wrap.querySelector(".lt-launch-btn"), menu = wrap.querySelector(".lt-menu");
    var l = { wrap: wrap, menu: menu, tours: opts.tours || [], heading: opts.heading };
    launchers.push(l);
    var close = function () { menu.hidden = true; btn.setAttribute("aria-expanded", "false"); };
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      var list = typeof l.tours === "function" ? l.tours() : l.tours;
      if (list.length === 1 && !opts.alwaysMenu) { start(list[0].id, { returnFocus: btn }); return; }
      if (!menu.hidden) return close();
      fillMenu(l);
      menu.hidden = false;
      btn.setAttribute("aria-expanded", "true");
      var first = menu.querySelector(".lt-menu-item"); if (first) first.focus();
    });
    menu.addEventListener("click", function (e) {
      var item = e.target.closest("[data-tour-id]");
      if (item) { close(); start(item.getAttribute("data-tour-id"), { returnFocus: btn }); return; }
      if (e.target.closest("[data-tour-reset]")) {
        var list = typeof l.tours === "function" ? l.tours() : l.tours;
        list.forEach(function (t) { write(t.id, null); });
        fillMenu(l);
      }
    });
    menu.addEventListener("keydown", function (e) {
      var items = Array.prototype.slice.call(menu.querySelectorAll("button"));
      var k = items.indexOf(document.activeElement);
      if (e.key === "Escape") { close(); btn.focus(); }
      else if (e.key === "ArrowDown") { e.preventDefault(); (items[k + 1] || items[0]).focus(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); (items[k - 1] || items[items.length - 1]).focus(); }
    });
    document.addEventListener("click", function (e) { if (!wrap.contains(e.target)) close(); });
    if (opts.container) {
      var c = typeof opts.container === "string" ? document.querySelector(opts.container) : opts.container;
      if (c) { if (opts.prepend) c.insertBefore(wrap, c.firstChild); else c.appendChild(wrap); } else document.body.appendChild(wrap);
    } else document.body.appendChild(wrap);
    return wrap;
  }

  window.LeadTour = {
    define: function (id, tour) { tours[id] = tour; return tour; },
    start: start,
    autoStart: autoStart,
    stop: function () { end(false); },
    isActive: function () { return !!active; },
    isDone: function (id) { return read(id) === "done"; },
    reset: function (id) { write(id, null); },
    setUser: function (u) { user = String(u || "").toLowerCase().replace(/[^a-z0-9@._-]/g, "").slice(0, 120); },
    mountLauncher: mountLauncher,
    has: function (id) { return !!tours[id]; },
  };
})();
