"""Response shapes of the API limit endpoints (Super Admin, org admin, user).

Super Admin sees everything (provider numbers included). An organization
sees its own keys, tokens and plan meters, and only a level + plain message
for the LeadAI services it depends on (never LeadAI's own numbers). A user
sees what they can do (search / AI) and why not, without provider internals.
"""
import re
from datetime import timedelta
from typing import Any, Dict, List, Optional

from app.db.mongo import get_sync_db
from app.limits import checkers, store
from app.limits.model import (
    DISPLAY_RANK,
    LEVELS,
    PLATFORM,
    PLATFORM_PROVIDERS,
    aware,
    make_status,
    org_scope,
    worst,
)

SERVICE_LABELS = {"apify": "Scraping (collecting posts and comments)", "gemini": "AI analysis",
                  "email": "Email", "sms": "Text messages (SMS)"}
_SERVICE_TEXT = {
    "apify": {"ok": "Working normally.",
              "critical": "LeadAI's scraping service is reaching its limit — searches may pause.",
              "exhausted": "LeadAI's scraping service has reached its limit — new searches are paused until "
                           "it is restored. Our team has been notified.",
              "error": "LeadAI's scraping service is not available right now. Our team has been notified."},
    "gemini": {"ok": "Working normally.",
               "critical": "LeadAI's AI analysis is reaching its limit — comments may be scored by the keyword "
                           "rules for a while.",
               "exhausted": "LeadAI's AI analysis has reached its limit — comments are scored by the keyword "
                            "rules until it resets.",
               "error": "LeadAI's AI analysis is not available right now — comments are scored by the keyword "
                        "rules."},
    "email": {"ok": "Working normally.",
              "critical": "LeadAI's email service is close to its daily sending limit — some emails may be "
                          "delayed.",
              "exhausted": "LeadAI's email service reached its daily sending limit — emails are delayed until "
                           "it resets.",
              "error": "LeadAI's email service is not available right now."},
    "sms": {"ok": "Working normally.",
            "critical": "LeadAI's SMS service is close to its limit — some texts may be delayed.",
            "exhausted": "LeadAI's SMS service is out of credit — texts are not delivered until it is restored.",
            "error": "LeadAI's SMS service is not available right now."},
}


def _sort_platform(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    order = {p: i for i, p in enumerate(PLATFORM_PROVIDERS)}
    return sorted(items, key=lambda s: order.get(s.get("provider"), 99))


def _sort_org(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    order = {p: i for i, p in enumerate(("apify", "gemini", "tokens", "plan"))}
    return sorted(items, key=lambda s: order.get(s.get("provider"), 99))


def platform_statuses() -> List[Dict[str, Any]]:
    return _sort_platform([s for s in (store.public(d) for d in store.list_statuses({"scope": PLATFORM})) if s])


def platform_level(provider: str) -> Optional[str]:
    doc = store.get_status(PLATFORM, provider)
    return (doc or {}).get("level")


def run_info() -> Dict[str, Any]:
    from app.limits.service import check_minutes
    last = aware((store.last_run() or {}).get("last_run_at"))
    return {"checked_at": last.isoformat() if last else None,
            "next_check_at": (last + timedelta(minutes=check_minutes())).isoformat() if last else None}


def _org_names(ids: List[str]) -> Dict[str, Dict[str, Any]]:
    from bson import ObjectId
    db = get_sync_db()
    if db is None or not ids:
        return {}
    oids = [ObjectId(i) for i in ids if ObjectId.is_valid(i)]
    return {str(o["_id"]): o for o in db.organizations.find({"_id": {"$in": oids}},
                                                            {"name": 1, "status": 1})}


def super_admin_view(*, level: Optional[str] = None, q: Optional[str] = None, page: int = 1,
                     limit: int = 25) -> Dict[str, Any]:
    from app.limits.service import limit_settings
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for d in store.list_statuses({"scope": {"$regex": "^org:"}}):
        s = store.public(d)
        if s and s.get("organization_id"):
            grouped.setdefault(str(s["organization_id"]), []).append(s)
    names = _org_names(list(grouped))
    rows = []
    summary = {lv: 0 for lv in LEVELS}
    for org_id, statuses in grouped.items():
        org = names.get(org_id)
        if org is None:
            continue
        lv = worst(s.get("level") or "unknown" for s in statuses)
        summary[lv] = summary.get(lv, 0) + 1
        rows.append({"organization_id": org_id, "name": org.get("name") or org_id,
                     "organization_status": org.get("status"), "level": lv,
                     "statuses": _sort_org(statuses)})
    if level:
        rows = [r for r in rows if r["level"] == level]
    if q:
        rx = re.compile(re.escape(q.strip()[:100]), re.IGNORECASE)
        rows = [r for r in rows if rx.search(r["name"] or "") or q.strip() == r["organization_id"]]
    rows.sort(key=lambda r: (-DISPLAY_RANK.get(r["level"], 0), str(r["name"]).lower()))
    total = len(rows)
    start = (page - 1) * limit
    return {"success": True, **run_info(), "platform": platform_statuses(),
            "organizations": {"summary": summary, "items": rows[start:start + limit], "total": total,
                              "page": page, "limit": limit, "pages": (total + limit - 1) // limit if limit else 0},
            "settings": limit_settings()}


# ── organization ────────────────────────────────────────────────────────────

def service_view(provider: str) -> Optional[Dict[str, Any]]:
    """A LeadAI service as an organization sees it: level + plain message only."""
    doc = store.get_status(PLATFORM, provider)
    lv = (doc or {}).get("level")
    if lv is None:
        lv, text = "unknown", "Not checked yet."
    elif lv == "not_configured":
        if provider in ("email", "sms"):
            return None
        lv = "error"
        text = _SERVICE_TEXT[provider]["error"]
    elif lv in ("ok", "warning"):
        lv, text = "ok", _SERVICE_TEXT[provider]["ok"]
    elif lv == "unknown":
        text = "Status not available right now."
    else:
        text = _SERVICE_TEXT[provider].get(lv) or _SERVICE_TEXT[provider]["critical"]
    return {"provider": provider, "label": SERVICE_LABELS[provider], "level": lv, "message": text,
            "checked_at": store.public(doc).get("checked_at") if doc else None}


def org_admin_view(organization_id: str) -> Dict[str, Any]:
    """Own keys + LeadAI tokens + plan meters (computed live from the database;
    the own Apify account comes from its last check) + LeadAI services."""
    from app.services.tenant_api_keys import _org_sync, coverage_of
    org = _org_sync(organization_id) or {}
    coverage = coverage_of(org)
    scope = org_scope(organization_id)
    own: List[Dict[str, Any]] = []
    if coverage.get("apify") == "own":
        prev = store.public(store.get_status(scope, "apify"))
        own.append(prev or make_status("apify", scope, configured=True, level="unknown",
                                       message="Not checked yet. Use Refresh to read your Apify account's "
                                               "usage and limits."))
    if coverage.get("gemini") == "own":
        g = checkers.check_gemini(scope, store.public(store.get_status(scope, "gemini")))
        if g:
            own.append(g)
    tokens = checkers.check_tokens(organization_id, org)
    plan = checkers.check_plan(organization_id, org)
    services = []
    for p in ("apify", "gemini"):
        if coverage.get(p) == "leadai":
            sv = service_view(p)
            if sv:
                services.append(sv)
    for p in ("email", "sms"):
        sv = service_view(p)
        if sv:
            services.append(sv)
    levels = [s.get("level") or "unknown" for s in own + [x for x in (tokens, plan) if x]]
    levels += [s["level"] for s in services]
    return {"success": True, "organization_id": str(organization_id), "coverage": coverage,
            "level": worst(levels) if levels else "ok", "own_keys": own, "tokens": tokens, "plan": plan,
            "services": services, "can_refresh": bool(own),
            "links": {"integrations": "/org-admin#integrations", "subscription": "/org-admin#subscription"}}


def refresh_org(organization_id: str) -> Dict[str, Any]:
    """Own keys only (Apify network call + Gemini), stored and alerted."""
    from app.limits.service import check_org
    from app.services.tenant_api_keys import _org_sync
    org = _org_sync(organization_id)
    if org:
        check_org(org, force=True, providers=["apify", "gemini"])
    return org_admin_view(organization_id)


# ── user ────────────────────────────────────────────────────────────────────

def _blocking(doc: Optional[Dict[str, Any]], *, ai: bool = False) -> Optional[str]:
    lv = (doc or {}).get("level")
    if lv == "exhausted" or lv == "error" or (lv == "not_configured" and not ai):
        return lv
    return None


def service_issues(organization_id: str) -> Dict[str, Optional[Dict[str, str]]]:
    """Whether scraping / AI can run for this organization right now, from
    the stored statuses (no provider numbers): {"search": issue|None, "ai": issue|None}."""
    from app.services.tenant_api_keys import _org_sync, coverage_of
    coverage = coverage_of(_org_sync(organization_id))
    scope = org_scope(organization_id)
    out: Dict[str, Optional[Dict[str, str]]] = {"search": None, "ai": None}
    own_a = coverage.get("apify") == "own"
    lv = _blocking(store.get_status(scope if own_a else PLATFORM, "apify"))
    if lv:
        out["search"] = {"level": "danger", "code": "SCRAPING_UNAVAILABLE", "action": "contact_admin" if own_a else "wait",
                         "message": ("Searches are paused: your organization's scraping account is out of credit "
                                     "or its key needs attention. Ask an admin." if own_a else
                                     "Searches are paused: LeadAI's scraping service has reached its limit. "
                                     "Our team is on it — try again later.")}
    own_g = coverage.get("gemini") == "own"
    g_doc = store.get_status(scope if own_g else PLATFORM, "gemini")
    if g_doc and g_doc.get("block") and not checkers._block_active(g_doc.get("block"), scope if own_g else PLATFORM):
        g_doc = {**g_doc, "level": "ok"}
    lv = _blocking(g_doc, ai=True)
    if lv:
        out["ai"] = {"level": "warning", "code": "AI_UNAVAILABLE", "action": "contact_admin" if own_g else "wait",
                     "message": ("AI analysis is paused: your organization's AI key reached its limit or needs "
                                 "attention, so comments are scored by the keyword rules. Ask an admin." if own_g else
                                 "AI analysis is paused: LeadAI's AI service reached its limit, so comments are "
                                 "scored by the keyword rules until it resets.")}
    return out


def plan_meter_levels(metrics: Dict[str, Any], warn_percent: Optional[int] = None) -> Dict[str, Any]:
    """Plan meters from a usage summary with a level each (user view)."""
    from app.limits.model import level_for_percent, thresholds
    warn, crit = thresholds(warn_percent)
    out = {}
    for key, m in (metrics or {}).items():
        if not isinstance(m, dict) or not m.get("limit"):
            continue
        used, limit = m.get("used") or 0, m.get("limit") or 0
        pct = round(used * 100.0 / limit, 1) if limit else None
        out[key] = {"label": checkers.PLAN_METRICS.get(key, ("Team members" if key == "team_members" else key, ""))[0],
                    "used": used, "limit": limit, "remaining": max(0, limit - used), "percent": pct,
                    "level": "ok" if key == "team_members" else level_for_percent(pct, warn, crit)}
    return out

