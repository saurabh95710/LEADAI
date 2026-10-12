/* Razorpay pay page (/billing/razorpay/<order_id>).
 *
 * Loads the checkout options from /api/billing/razorpay/<order_id>, opens
 * Razorpay Checkout and sends its success handler's result to
 * /api/billing/razorpay/verify, which checks the signature on the server.
 * Then the checkout status page shows where the payment stands. Nothing
 * here can activate a plan: a verified payment waits for our team.
 * No inline script: this page has its own Content-Security-Policy. */
(function () {
  "use strict";
  var UI = window.LeadAIUI || {};
  var $ = function (id) { return document.getElementById(id); };

  var seg = location.pathname.slice("/billing/razorpay/".length).replace(/\/+$/, "");
  var orderId = "";
  try { orderId = decodeURIComponent(seg); } catch (_) { orderId = seg; }
  var statusUrl = "/billing/status?session=" + encodeURIComponent(orderId);
  var cfg = null;
  var lastFailure = "";
  var verifying = false;

  function show(view, focusSel) {
    document.querySelectorAll("[data-view]").forEach(function (el) {
      el.hidden = el.getAttribute("data-view") !== view;
    });
    $("card").setAttribute("aria-busy", view === "loading" ? "true" : "false");
    if (focusSel) { var h = $(focusSel); if (h) h.focus({ preventScroll: true }); }
  }

  function showError(title, desc, canRetry) {
    $("errTitle").textContent = title;
    $("errDesc").textContent = desc;
    $("retryBtn").classList.toggle("hidden", !canRetry);
    show("error", "errTitle");
  }

  function alertBox(title, text) {
    var box = $("payAlert");
    if (!title) { box.classList.add("hidden"); return; }
    $("payAlertTitle").textContent = title;
    $("payAlertText").textContent = text || "";
    box.classList.remove("hidden");
  }

  function setBusy(on, label) {
    var btn = $("payBtn");
    if (UI.setBusy) UI.setBusy(btn, on, label);
    else btn.disabled = !!on;
  }

  function money(minor, currency) {
    var n = Number(minor) / 100;
    if (!isFinite(n)) return "";
    var cur = String(currency || "INR").toUpperCase();
    try { return new Intl.NumberFormat(undefined, { style: "currency", currency: cur }).format(n); }
    catch (_) { return cur + " " + n.toFixed(2); }
  }

  function goLogin() {
    location.href = "/login?next=" + encodeURIComponent(location.pathname);
  }

  function api(url, opts) {
    if (UI.api) return UI.api(url, opts);
    return Promise.resolve({ ok: false, status: 0, data: {}, networkError: true });
  }

  function errText(res, fallback) {
    return UI.errorMessage ? UI.errorMessage(res.data, fallback) : fallback;
  }

  async function load() {
    if (!orderId) { showError("No checkout reference", "Start a plan change from Billing in your dashboard.", false); return; }
    show("loading");
    var res = await api("/api/billing/razorpay/" + encodeURIComponent(orderId));
    if (res.status === 401) { goLogin(); return; }
    if (!res.ok || !res.data.checkout) {
      if (res.networkError) showError("We couldn't reach LeadAI", "Check your connection and try again.", true);
      else if (res.status === 404 || res.status === 403) showError("We couldn't find this checkout", "This payment link doesn't match an open checkout of your workspace. Start a plan change from Billing.", false);
      else showError("Payments are unavailable right now", errText(res, "Please try again in a moment."), true);
      return;
    }
    cfg = res.data.checkout;
    if (cfg.provider !== "razorpay" || cfg.status !== "pending_payment") { location.replace(statusUrl); return; }
    var amount = money(cfg.amount, cfg.currency);
    $("kvPlan").textContent = cfg.description || "—";
    $("kvAmount").textContent = amount || "—";
    $("kvRef").textContent = orderId;
    $("payLabel").textContent = amount ? "Pay " + amount : "Pay now";
    show("pay", "payTitle");
    openCheckout();
  }

  function openCheckout() {
    if (!cfg || verifying) return;
    if (typeof window.Razorpay !== "function") {
      alertBox("The payment window didn't load", "Your browser couldn't load Razorpay. Check your connection or turn off content blockers for this page, then try again.");
      return;
    }
    alertBox("");
    lastFailure = "";
    var rzp;
    try {
      rzp = new window.Razorpay({
        key: cfg.key_id,
        order_id: cfg.order_id,
        amount: cfg.amount,
        currency: cfg.currency,
        name: cfg.name,
        description: cfg.description,
        prefill: cfg.prefill || {},
        notes: cfg.notes || {},
        theme: { color: cfg.theme_color },
        handler: onPaid,
        modal: { ondismiss: onDismiss, confirm_close: true }
      });
    } catch (e) {
      alertBox("The payment window didn't open", "Please try again. If it keeps happening, contact support.");
      return;
    }
    rzp.on("payment.failed", function (resp) {
      var err = (resp && resp.error) || {};
      lastFailure = String(err.description || err.reason || "The payment didn't go through.");
    });
    rzp.open();
  }

  function onDismiss() {
    if (verifying) return;
    if (lastFailure) alertBox("Payment failed", lastFailure + " No money was taken for this attempt — you can try again.");
    else alertBox("Payment cancelled", "You closed the payment window before paying. Your plan hasn't changed — pay now or go back to billing.");
  }

  async function onPaid(resp) {
    verifying = true;
    alertBox("");
    setBusy(true, "Verifying payment…");
    var res = await api("/api/billing/razorpay/verify", {
      method: "POST",
      body: {
        razorpay_order_id: String((resp && resp.razorpay_order_id) || cfg.order_id),
        razorpay_payment_id: String((resp && resp.razorpay_payment_id) || ""),
        razorpay_signature: String((resp && resp.razorpay_signature) || "")
      }
    });
    if (res.ok) { location.replace(statusUrl); return; }
    verifying = false;
    setBusy(false);
    if (res.status === 401) { goLogin(); return; }
    // the webhook still reports a real payment, so point at the status page
    var why = res.networkError ? "We couldn't reach LeadAI." : errText(res, "");
    if (why && !/[.!?]$/.test(why)) why += ".";
    alertBox("We couldn't confirm the payment here", (why ? why + " " : "") +
      "If money was taken, it is recorded automatically within a few minutes — check the payment status.");
    var back = $("backLink");
    back.textContent = "View payment status";
    back.href = statusUrl;
  }

  $("payBtn").addEventListener("click", openCheckout);
  $("retryBtn").addEventListener("click", load);
  load();
})();
