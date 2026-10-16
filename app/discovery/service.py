"""Keyword search service — shared by the app (session) and the public API
(API key) so neither can skip a rule.

  capabilities_for(org)        matrix + suggested filters + this org's caps
  prepare(org, body)           validate + clamp a request (422 / 402)
  compute_estimate(org, clean) worst-case tokens + rough Apify USD
  admit_keyword_search(...)    every admission check, then create + start the run
  reapply_run(...)             recompute statuses from stored metrics (no scraping)
  run_summary(db, run)         passed_count + rejection_summary (why profiles were rejected)
  continue_run(...)            sample / deep-scrape what re-apply (or a stop) left

Admission order: feature switch -> platform switches -> plan feature +
platform entitlement (also blocks expired trials) -> plan / Global caps ->
filter validation -> concurrency -> estimate (``confirm`` required, else 409)
-> token availability for the discovery stage -> monthly search quota -> run.
"""
from __future__ import annotations

import asyncio
import logging
import math
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import HTTPException, Request

from app.db.models import utcnow
from app.discovery import capabilities as C
from app.discovery import filters as F
from app.discovery.pipeline import CANDIDATES, STAGES

logger = logging.getLogger(__name__)

MAX_CONCURRENT_RUNS_PER_ORG = 5
_STATUSES = ("pending", "passed", "rejected", "needs_sampling", "error")


def new_keyword_run_id(prefix: str = "KW") -> str:
    """Unguessable, sortable run id."""
    return f"{prefix}{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:12]}"


def _err(status: int, error_type: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status, detail={"success": False, "errorType": error_type,
                                                     "message": message, **extra})


# ── switches, caps, defaults ─────────────────────────────────────────────────

def feature_enabled() -> bool:
    from app.admin.settings import get_bool
    return get_bool("features.keyword_search.enabled")


def platform_enabled(platform: str) -> bool:
    from app.admin.settings import get_bool, is_platform_enabled
    return get_bool(f"keyword.platform.{platform}.enabled") and is_platform_enabled(platform)


def global_caps() -> Dict[str, int]:
    from app.admin.settings import effective_limits, get_int
    lim = effective_limits()
    return {
        "max_keywords": max(1, get_int("keyword.max_keywords", 5)),
        "max_candidates": max(1, get_int("keyword.max_candidates", 200)),
        "max_recent_posts": max(1, get_int("keyword.max_recent_posts", 30)),
        "comments_per_post": max(1, min(lim["max_comments_per_post_cap"], lim["global_max_comments"])),
        "comments_per_post_default": max(1, lim["max_comments_per_post_default"]),
    }


def effective_caps(organization_id: Optional[str]) -> Dict[str, Any]:
    """min(plan / trial limit, Global Settings cap). A plan WITHOUT a keyword
    key has no plan cap of its own; a keyword key explicitly set to 0 means
    "not allowed" (searches are refused with 402 PLAN_LIMIT), like a monthly
    quota of 0. ``comments_per_post`` keeps the existing meaning (0 = no cap)."""
    from app.billing.entitlements import EntitlementService
    g = global_caps()
    plan = EntitlementService.get_effective_plan_sync(str(organization_id)) if organization_id else {}
    limits = (plan or {}).get("limits") or {}

    def cap(plan_key: str, glob: int, zero_blocks: bool = True) -> int:
        if limits.get(plan_key) is None:
            return glob
        try:
            v = int(limits.get(plan_key))
        except (TypeError, ValueError):
            return glob
        if v <= 0:
            return 0 if zero_blocks else glob
        return min(v, glob)
    out: Dict[str, Any] = {
        "max_keywords": cap("keyword_max_keywords", g["max_keywords"]),
        "max_candidates": cap("keyword_max_candidates", g["max_candidates"]),
        "max_recent_posts": cap("keyword_recent_posts", g["max_recent_posts"]),
        "comments_per_post": cap("comments_per_post", g["comments_per_post"], zero_blocks=False),
    }
    out["comments_per_post_default"] = min(g["comments_per_post_default"], out["comments_per_post"])
    out["plan"] = {"slug": (plan or {}).get("slug"), "name": (plan or {}).get("name"),
                   "has_keyword_search": "keyword_search" in ((plan or {}).get("features") or [])}
    return out


def default_filters() -> Dict[str, Any]:
    from app.admin.settings import get_int
    return {
        "min_followers": get_int("keyword.default.min_followers", 1000),
        "min_posts": get_int("keyword.default.min_posts", 10),
        "recent_posts_to_analyze": max(1, get_int("keyword.default.recent_posts", 10)),
        "min_avg_comments": get_int("keyword.default.min_avg_comments", 2),
        "active_within_days": get_int("keyword.default.active_within_days", 90),
    }


def capabilities_for(organization_id: Optional[str]) -> Dict[str, Any]:
    from app.lifecycle.config import token_cost
    payload = C.matrix_payload()
    payload.update({
        "enabled": feature_enabled(),
        "platform_enabled": {p: platform_enabled(p) for p in C.PLATFORMS},
        "default_filters": default_filters(),
        "caps": effective_caps(organization_id),
        "token_costs": {a: token_cost(a) for a in ("discover", "enrich", "sample", "collect")},
    })
    return payload


# ── request validation ──────────────────────────────────────────────────────

def _clean_keywords(raw: List[str]) -> List[str]:
    out: List[str] = []
    errors: List[str] = []
    for kw in raw or []:
        kw = " ".join(str(kw or "").split())
        if not kw:
            continue
        if len(kw) > 100:
            errors.append(f"'{kw[:20]}…' is longer than 100 characters")
            continue
        if kw.lower() not in (k.lower() for k in out):
            out.append(kw)
    if errors:
        raise _err(422, "invalid_request", "Some keywords are invalid.", errors={"keywords": "; ".join(errors)})
    if not out:
        raise _err(422, "invalid_request", "Enter at least one keyword.", errors={"keywords": "required"})
    return out


def comment_filter_dict(spec: Any) -> Optional[Dict[str, Any]]:
    """Same per-run comment filter shape URL search stores."""
    if spec is None:
        return None
    spec = spec.model_dump() if hasattr(spec, "model_dump") else dict(spec)
    mode = spec.get("mode") or "all"
    if mode == "preset" and spec.get("preset"):
        preset = str(spec["preset"])
        return ({"mode": "preset", "rule_id": preset} if ObjectId.is_valid(preset)
                else {"mode": "preset", "preset": preset})
    if mode == "custom":
        return {"mode": "custom",
                "include_keywords": [k.strip() for k in spec.get("include_keywords") or [] if k.strip()],
                "exclude_keywords": [k.strip() for k in spec.get("exclude_keywords") or [] if k.strip()],
                "categories": [c.strip() for c in spec.get("categories") or [] if c.strip()],
                "match_mode": spec.get("match_mode") or "any"}
    return None


def validate_filters_or_422(raw: Dict[str, Any], caps: Dict[str, Any],
                            recent_override: Optional[int] = None) -> tuple[Dict[str, Any], List[str]]:
    raw = {k: v for k, v in (raw or {}).items() if v is not None}
    if recent_override and not raw.get("recent_posts_to_analyze"):
        raw["recent_posts_to_analyze"] = recent_override
    try:
        return F.validate_filters(raw, max_recent_posts=caps["max_recent_posts"],
                                  default_recent_posts=default_filters()["recent_posts_to_analyze"])
    except F.FilterValidationError as e:
        raise _err(422, "invalid_filters", "Some filter values are invalid.", errors=e.errors)


def prepare(organization_id: Optional[str], body: Any) -> Dict[str, Any]:
    """Validate + clamp a KeywordSearchRequest. Raises 422 / 402."""
    caps = effective_caps(organization_id)
    keywords = _clean_keywords(body.keywords)
    platforms = list(dict.fromkeys(body.platforms))
    for key, metric in (("max_keywords", "keyword_max_keywords"), ("max_candidates", "keyword_max_candidates"),
                        ("max_recent_posts", "keyword_recent_posts")):
        if caps[key] <= 0:
            raise HTTPException(status_code=402, detail={
                "success": False, "code": "PLAN_LIMIT", "error": "PLAN_LIMIT", "metric": metric, "limit": 0,
                "upgrade_available": True,
                "message": "Your plan doesn't include keyword searches. Upgrade to use them."})
    if len(keywords) > caps["max_keywords"]:
        raise HTTPException(status_code=402, detail={
            "success": False, "code": "PLAN_LIMIT", "error": "PLAN_LIMIT", "metric": "keyword_max_keywords",
            "limit": caps["max_keywords"], "requested": len(keywords), "upgrade_available": True,
            "message": f"Your plan allows up to {caps['max_keywords']} keyword"
                       f"{'s' if caps['max_keywords'] != 1 else ''} per search."})
    limits_in = body.limits.model_dump() if hasattr(body.limits, "model_dump") else dict(body.limits or {})
    filters, notes = validate_filters_or_422(
        body.filters.model_dump(exclude_none=True) if hasattr(body.filters, "model_dump") else dict(body.filters),
        caps, recent_override=limits_in.get("recent_posts"))
    max_c = int(limits_in.get("max_candidates") or caps["max_candidates"])
    if max_c > caps["max_candidates"]:
        notes.append(f"max_candidates lowered from {max_c} to {caps['max_candidates']} (your limit).")
        max_c = caps["max_candidates"]
    cpp = int(limits_in.get("comments_per_post") or caps["comments_per_post_default"])
    if cpp > caps["comments_per_post"]:
        notes.append(f"comments_per_post lowered from {cpp} to {caps['comments_per_post']} (your limit).")
        cpp = caps["comments_per_post"]
    return {
        "keywords": keywords, "platforms": platforms, "filters": filters, "strict": bool(body.strict),
        "limits": {"max_candidates": max_c, "recent_posts": filters["recent_posts_to_analyze"],
                   "comments_per_post": cpp},
        "max_tokens": getattr(body, "max_tokens", None), "notes": notes, "caps": caps,
    }


# ── cost estimate ───────────────────────────────────────────────────────────

def _tokens(organization_id: Optional[str], action: str, units: int) -> int:
    if units <= 0:
        return 0
    from app.billing.api_coverage import token_cost_for
    from app.lifecycle.config import token_cost
    return int(token_cost_for(str(organization_id) if organization_id else None, action,
                              token_cost(action) * units) or 0)


def _needs_enrichment(platform: str) -> bool:
    if platform == "facebook":
        return True
    if platform == "youtube":
        return True
    return False


def compute_estimate(organization_id: Optional[str], clean: Dict[str, Any]) -> Dict[str, Any]:
    """Worst case: every discovered candidate passes every filter."""
    kws, plats = clean["keywords"], clean["platforms"]
    n = int(clean["limits"]["recent_posts"])
    max_c = int(clean["limits"]["max_candidates"])
    cpp = int(clean["limits"]["comments_per_post"])
    pairs = len(kws) * len(plats)
    per_call = max(1, math.ceil(max_c / max(1, pairs)))
    raw_pp = {p: min(max_c, per_call * len(kws)) for p in plats}
    scale = min(1.0, max_c / max(1, sum(raw_pp.values())))
    per_platform: Dict[str, Dict[str, Any]] = {}
    usd = 0.0
    enrich_units = sample_units = 0
    for p in plats:
        cands = int(math.ceil(raw_pp[p] * scale))
        price = C.PRICING_USD[p]
        results = per_call * len(kws) * (2 if p == "youtube" and not C.resolve_actor(
            "discovery.actor.youtube.channel_search") else 1)
        # worst case: every candidate needs its posts fetched — even on Instagram
        # a profile whose search result carried fewer than n posts is sampled
        posts_may_come_free = C.DISCOVERY_POSTS.get(p, 0) >= n
        enrich = _needs_enrichment(p)
        p_usd = (price["discovery"] * results + (price["enrichment"] * cands if enrich else 0)
                 + price["post"] * cands * n + price["comment"] * cands * n * cpp)
        usd += p_usd
        enrich_units += cands if enrich else 0
        sample_units += cands
        per_platform[p] = {"candidates_max": cands, "discovery_calls": len(kws),
                           "needs_enrichment": enrich, "posts_from_discovery": posts_may_come_free,
                           "apify_usd_estimate": round(p_usd, 2),
                           "unsupported_filters": C.unsupported_filters(p, clean["filters"]),
                           "search_queries": discovery_preview(p, kws, per_call, clean["filters"])}
    enrich_units, sample_units = min(enrich_units, max_c), min(sample_units, max_c)
    tokens = {"discover": _tokens(organization_id, "discover", pairs),
              "enrich": _tokens(organization_id, "enrich", enrich_units),
              "sample": _tokens(organization_id, "sample", sample_units),
              "collect": _tokens(organization_id, "collect", max_c)}
    tokens["total_max"] = sum(tokens.values())
    notes = list(clean.get("notes") or [])
    notes.append("Worst case: assumes every discovered profile passes your filters. Profiles rejected "
                 "early are not sampled or deep-scraped, and profiles cached in the last "
                 "hours are free.")
    narrowing = narrowing_note(clean["filters"], per_platform)
    if narrowing:
        notes.append(narrowing)
    from app.services.tenant_api_keys import _org_sync, coverage_of
    if organization_id and coverage_of(_org_sync(organization_id)).get("apify") == "own":
        notes.append("Your organization uses its own Apify key: Apify usage is billed to your Apify "
                     "account and isn't charged in tokens.")
    for p, info in per_platform.items():
        if info["unsupported_filters"]:
            from app.discovery.capabilities import FILTERS
            labels = [str((FILTERS.get(k) or {}).get("label") or k).lower() for k in info["unsupported_filters"]]
            msg = f"{p.capitalize()} can't measure: {', '.join(labels)}"
            msg += (" — strict mode will reject every candidate there." if clean.get("strict")
                    else " — those filters are reported as unsupported, not applied.")
            notes.append(msg)
    if "linkedin" in plats:
        notes.append("LinkedIn uses a third-party actor; LinkedIn's terms forbid scraping.")
    if clean.get("max_tokens"):
        notes.append(f"The run stops once it has spent {clean['max_tokens']} tokens.")
    from app.billing.tokens import get_balance
    bal = get_balance(str(organization_id)) if organization_id else None
    return {
        "tokens": tokens,
        "apify_usd_estimate": round(usd, 2),
        "candidates_max": max_c,
        "per_platform": per_platform,
        "limits": dict(clean["limits"]),
        "filters": dict(clean["filters"]),
        "notes": notes,
        "tokens_available": (bal or {}).get("remaining") if bal else None,
    }


def discovery_preview(platform: str, keywords: List[str], per_call: int,
                      filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    """What each keyword search would send to Apify on this platform."""
    from app.discovery.adapters import get_adapter
    out: List[Dict[str, Any]] = []
    try:
        adapter = get_adapter(platform, None)
        for kw in keywords:
            out.append(adapter.discovery_request(kw, per_call, filters)[2])
    except Exception:          # preview only: never fail an estimate over it
        logger.debug("[KEYWORD] discovery preview failed for %s", platform, exc_info=True)
    return out


def describe_query(rec: Dict[str, Any]) -> str:
    """'developer job kota' (Instagram) / 'developer job' near 'kota, Rajasthan' (Facebook)."""
    text = f"'{rec.get('query_text') or rec.get('keyword')}'"
    if rec.get("locations"):
        text += " near " + ", ".join(f"'{loc}'" for loc in rec["locations"])
    return f"{text} ({str(rec.get('platform') or '').capitalize()})"


def narrowing_note(filters: Dict[str, Any], per_platform: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """Estimate note: the location / business type narrow the Apify search itself."""
    if not (filters.get("location_keyword") or filters.get("category_keyword")):
        return None
    what = " and ".join(x for x in ("location" if filters.get("location_keyword") else "",
                                    "business type" if filters.get("category_keyword") else "") if x)
    sent = [describe_query(q) for info in per_platform.values() for q in (info.get("search_queries") or [])]
    note = (f"Your {what} narrow{'s' if ' and ' not in what else ''} the Apify search itself, so fewer "
            "unrelated profiles are found and paid for")
    if sent:
        note += ": " + "; ".join(sent[:6]) + ("…" if len(sent) > 6 else "")
    return note + ". Profiles must still match every filter to pass."


def check_tokens_available(organization_id: Optional[str], need: int) -> None:
    """402 when the organization can't pay ``need`` tokens (no spending)."""
    if not organization_id or need <= 0:
        return
    from app.billing.tokens import TokensExhaustedException, get_balance
    bal = get_balance(str(organization_id))
    if bal is None:
        return
    if bal.get("expired"):
        raise TokensExhaustedException(need, int(bal.get("remaining", 0)), expired=True)
    if int(bal.get("remaining", 0)) < need:
        raise TokensExhaustedException(need, int(bal.get("remaining", 0)))


# ── admission ───────────────────────────────────────────────────────────────

async def check_access(ctx: Any, platforms: List[str], db=None) -> None:
    """Feature + platform switches and plan entitlement (no quota used)."""
    if not feature_enabled():
        raise _err(403, "feature_disabled", "Keyword search is currently disabled by the administrator.")
    off = [p for p in platforms if not platform_enabled(p)]
    if off:
        raise _err(403, "platform_disabled",
                   f"Keyword search on {', '.join(off)} is currently disabled by the administrator.",
                   platforms=off)
    from app.billing.entitlements import EntitlementService
    await EntitlementService.enforce_quota_and_consume(organization_id=ctx.organization_id,
                                                       feature_key="keyword_search", metric=None,
                                                       user_id=ctx.user_id, db=db)
    for p in platforms:
        await EntitlementService.enforce_quota_and_consume(organization_id=ctx.organization_id,
                                                           feature_key=p, metric=None,
                                                           user_id=ctx.user_id, db=db)


async def estimate_request(ctx: Any, body: Any, db=None) -> Dict[str, Any]:
    await check_access(ctx, list(dict.fromkeys(body.platforms)), db=db)
    clean = await asyncio.to_thread(prepare, ctx.organization_id, body)
    return await asyncio.to_thread(compute_estimate, ctx.organization_id, clean)


async def _check_concurrency(db, ctx: Any) -> None:
    from app.admin.settings import effective_limits
    from app.auth.tenant import org_match
    if not effective_limits().get("stop_on_limit"):
        return
    running = await db.search_history.count_documents(
        {"status": "running", "organization_id": org_match(ctx.organization_id)})
    if running >= MAX_CONCURRENT_RUNS_PER_ORG:
        raise _err(429, "cost_limit", "Too many concurrent jobs. Wait for running jobs to finish.")


def launch(run_id: str, ctx: Any, job_id: Optional[str] = None) -> None:
    """Start the background thread (patched in tests)."""
    from app.api.routes.search import _start
    from app.discovery import pipeline
    thread = pipeline.KeywordSearchThread(run_id, job_id=job_id, organization_id=ctx.organization_id,
                                          created_by=ctx.email, user_id=ctx.user_id)
    _start(f"keyword_search:{job_id or run_id}", thread.run)


async def admit_keyword_search(ctx: Any, request: Request, body: Any, *, db,
                               source: str = "app", run_id: Optional[str] = None) -> Dict[str, Any]:
    from app.api.routes.search import _audit
    from app.auth.tenant import stamp
    from app.billing.entitlements import EntitlementService

    platforms = list(dict.fromkeys(body.platforms))
    await check_access(ctx, platforms, db=db)
    clean = await asyncio.to_thread(prepare, ctx.organization_id, body)
    await _check_concurrency(db, ctx)
    estimate = await asyncio.to_thread(compute_estimate, ctx.organization_id, clean)
    if not body.confirm:
        raise HTTPException(status_code=409, detail={
            "success": False, "code": "CONFIRM_REQUIRED", "errorType": "confirm_required",
            "message": "Review the estimated cost, then start again with confirm: true.",
            "estimate": estimate})
    discover_cost = estimate["tokens"]["discover"]
    if clean.get("max_tokens") and discover_cost and clean["max_tokens"] < discover_cost:
        raise _err(422, "invalid_request",
                   f"max_tokens ({clean['max_tokens']}) is below the discovery cost ({discover_cost}).",
                   errors={"max_tokens": f"must be at least {discover_cost}"})
    await asyncio.to_thread(check_tokens_available, ctx.organization_id, discover_cost)
    await EntitlementService.enforce_quota_and_consume(
        organization_id=ctx.organization_id, feature_key="keyword_search", metric="monthly_searches",
        quantity=1, user_id=ctx.user_id, source=f"keyword_search_{source}", db=db)

    run_id = run_id or new_keyword_run_id()
    biz = body.business_context.model_dump(exclude_none=True) if body.business_context else None
    if biz is not None:
        biz = {k: v for k, v in biz.items() if v not in ("", [], None)} or None
    now = utcnow()
    run_doc = stamp(ctx, {
        "run_id": run_id, "search_type": "keyword", "url_search": False, "source": source,
        "query": ", ".join(clean["keywords"]),
        "intent": {"keyword": ", ".join(clean["keywords"]), "type": "keyword",
                   "platform": platforms[0] if len(platforms) == 1 else "multiple",
                   "platforms": platforms, "limit": clean["limits"]["max_candidates"]},
        "keyword_search": {"keywords": clean["keywords"], "platforms": platforms,
                           "filters": clean["filters"], "strict": clean["strict"],
                           "limits": clean["limits"], "estimate": estimate,
                           "max_tokens": clean.get("max_tokens")},
        "comment_filter": comment_filter_dict(body.comment_filter),
        "business_context": biz,
        "limit": clean["limits"]["max_candidates"], "provider": "apify",
        "status": "running", "phase": "queued", "message": "Starting keyword search…",
        "stages": {s: "pending" for s in STAGES},
        "stage_counts": {k: 0 for k in ("discovered", "enriched", "sampled", "passed", "rejected",
                                        "needs_sampling", "errors", "comments_scraped", "leads")},
        "tokens_charged": {s: 0 for s in STAGES},
        "platform_errors": {}, "discovery_pairs_done": [], "discovery_queries": [],
        "pages_found": 0, "pages_stored": 0,
        "created_at": now, "updated_at": now, "completed_at": None,
    })
    try:
        await db.search_history.insert_one(run_doc)
        launch(run_id, ctx)
    except Exception as e:
        # nothing was started: give the monthly search back and leave no half-made run
        logger.exception("[KEYWORD] could not start run %s", run_id)
        await _undo_start(db, ctx, run_id)
        raise _err(503, "start_failed", "The search could not be started. Nothing was charged — please retry.",
                   reason=str(e)[:200])
    await _audit(ctx, request, "search.keyword_started", "search", resource_type="search_run",
                 resource_id=run_id,
                 details={"keywords": clean["keywords"], "platforms": platforms, "filters": clean["filters"],
                          "strict": clean["strict"], "limits": clean["limits"],
                          "estimate_tokens": estimate["tokens"]["total_max"], "source": source})
    return {"run_id": run_id, "status": "running", "estimate": estimate,
            "message": "Keyword search started — candidates appear as they are discovered."}


async def _undo_start(db, ctx: Any, run_id: str) -> None:
    try:
        from app.billing.usage import record_usage_atomic
        await record_usage_atomic(organization_id=str(ctx.organization_id), metric="monthly_searches",
                                  quantity=-1, user_id=ctx.user_id, source="keyword_search_start_failed", db=db)
    except Exception:
        logger.warning("[KEYWORD] could not give back the monthly search of %s", run_id, exc_info=True)
    try:
        await db.search_history.delete_one({"run_id": run_id, "status": "running", "phase": "queued"})
    except Exception:
        pass


# ── reading ─────────────────────────────────────────────────────────────────

def _iso(v: Any) -> Optional[str]:
    if isinstance(v, datetime):
        from datetime import timezone
        return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).isoformat()
    return v if isinstance(v, str) else None


def run_status(doc: Dict[str, Any]) -> Dict[str, Any]:
    ks = doc.get("keyword_search") or {}
    return {
        "run_id": doc["run_id"], "status": doc.get("status") or "unknown", "phase": doc.get("phase"),
        "message": doc.get("message"), "error": doc.get("error"),
        "keywords": ks.get("keywords") or [], "platforms": ks.get("platforms") or [],
        "filters": ks.get("filters") or {}, "strict": bool(ks.get("strict")),
        "limits": ks.get("limits") or {}, "max_tokens": ks.get("max_tokens"),
        "estimate": ks.get("estimate"),
        "stages": {s: (doc.get("stages") or {}).get(s, "pending") for s in STAGES},
        "stage_counts": {k: int(v or 0) for k, v in (doc.get("stage_counts") or {}).items()},
        "tokens_charged": {s: int((doc.get("tokens_charged") or {}).get(s) or 0) for s in STAGES},
        "platform_errors": doc.get("platform_errors") or {},
        "discovery_queries": [q for q in doc.get("discovery_queries") or [] if isinstance(q, dict)],
        "passed_count": int((doc.get("stage_counts") or {}).get("passed") or 0),
        "rejection_summary": [],
        "source": doc.get("source"),
        "created_at": _iso(doc.get("created_at")), "updated_at": _iso(doc.get("updated_at")),
        "completed_at": _iso(doc.get("completed_at")),
    }


# generic wording of a failed filter in the rejection summary ({t} = threshold)
_FAIL_SUMMARY = {
    "min_followers": "fewer than {t} followers",
    "max_followers": "more than {t} followers",
    "min_posts": "fewer than {t} posts",
    "min_avg_comments": "fewer than {t} comments per post on average",
    "min_total_comments": "fewer than {t} comments on the analyzed posts",
    "min_engagement_rate": "engagement below {t}%",
    "active_within_days": "no post in the last {t} days",
    "business_account_only": "not a business account",
    "verified_only": "not verified",
    "has_contact_info": "no contact info found",
    "private_account": "private account",
}
SUMMARY_LIMIT = 8


def _fmt_threshold(v: Any) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return f"{v:,}" if isinstance(v, int) and not isinstance(v, bool) else str(v)


async def rejection_summary(db, run: Dict[str, Any], base: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Top reasons rejected candidates failed: [{filter, label, count, outcome,
    message}] — failed filters, plus unknown / unsupported values when the run
    is strict (those reject too). One aggregation over the candidates' reasons."""
    ks = run.get("keyword_search") or {}
    strict = bool(ks.get("strict"))
    filters = ks.get("filters") or {}
    outcomes = [F.FAIL] + ([F.UNKNOWN, F.UNSUPPORTED_OUTCOME] if strict else [])
    match = dict(base or {"search_run_id": run["run_id"]})
    pipeline = [
        {"$match": {"$and": [match, {"status": "rejected"}]}},
        {"$unwind": "$reasons"},
        {"$match": {"reasons.outcome": {"$in": outcomes}}},
        {"$group": {"_id": {"filter": "$reasons.filter", "outcome": "$reasons.outcome",
                            "message": "$reasons.message"}, "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 500},
    ]
    groups: Dict[tuple, Dict[str, Any]] = {}
    async for row in db[CANDIDATES].aggregate(pipeline):
        key = row.get("_id") or {}
        flt, outcome = key.get("filter"), key.get("outcome")
        if not flt:
            continue
        kind = "fail" if outcome == F.FAIL else "unknown"
        g = groups.setdefault((flt, kind), {"filter": flt, "outcome": kind, "count": 0, "top": None, "top_n": 0})
        g["count"] += int(row.get("count") or 0)
        if int(row.get("count") or 0) > g["top_n"]:
            g["top"], g["top_n"] = key.get("message"), int(row.get("count") or 0)
    out: List[Dict[str, Any]] = []
    for g in sorted(groups.values(), key=lambda x: (-x["count"], x["filter"])):
        flt = g["filter"]
        label = (C.FILTERS.get(flt) or {}).get("label") or flt.replace("_", " ").capitalize()
        if g["outcome"] == "unknown":
            message = f"{label.lower()} not known (strict mode rejects unknown values)"
        elif flt in _FAIL_SUMMARY and (filters.get(flt) is not None or "{t}" not in _FAIL_SUMMARY[flt]):
            message = _FAIL_SUMMARY[flt].format(t=_fmt_threshold(filters.get(flt)))
        else:
            message = g["top"] or label
        out.append({"filter": flt, "label": label, "count": g["count"], "outcome": g["outcome"],
                    "message": message})
    return out[:SUMMARY_LIMIT]


async def run_summary(db, run: Dict[str, Any], base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """passed_count + rejection_summary of a run (GET /{run_id})."""
    match = dict(base or {"search_run_id": run["run_id"]})
    passed = await db[CANDIDATES].count_documents({"$and": [match, {"status": "passed"}]})
    return {"passed_count": int(passed), "rejection_summary": await rejection_summary(db, run, match)}


async def run_status_full(db, run: Dict[str, Any], base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {**run_status(run), **(await run_summary(db, run, base))}


def candidate_out(doc: Dict[str, Any]) -> Dict[str, Any]:
    from app.api.routes.search import _serialize
    out = _serialize({k: v for k, v in doc.items() if k not in ("posts_raw",)})
    out["id"] = str(doc["_id"])
    for k in ("organization_id", "user_id", "created_by", "search_run_id"):
        out.pop(k, None)
    out.setdefault("keywords", [])
    out["stage_reached"] = out.get("stage_reached") or "discovered"
    out["profile_id"] = str(out.get("profile_id") or "")
    return out


# ── re-apply / continue ─────────────────────────────────────────────────────

# A run whose worker went silent this long (no progress, no live job lease)
# is considered dead — longer than one Apify actor run may block (8 min).
WORKER_STALE_MINUTES = 15


def _aware(dt: Any) -> Any:
    from datetime import timezone
    if isinstance(dt, datetime) and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _job_ids_query(run_id: str) -> Dict[str, Any]:
    import re
    return {"$or": [{"job_id": run_id}, {"job_id": {"$regex": "^" + re.escape(run_id) + r"_C\d+$"}}]}


def _finished_filter(run_id: str) -> Dict[str, Any]:
    """Run doc filter: not running AND its worker acknowledged the end
    (completed_at set) or went silent. ``POST /cancel`` flips status to
    cancelled at once while the thread still runs, so status alone isn't enough."""
    from datetime import timedelta
    stale = utcnow() - timedelta(minutes=WORKER_STALE_MINUTES)
    return {"run_id": run_id, "status": {"$ne": "running"},
            "$or": [{"completed_at": {"$ne": None}}, {"updated_at": {"$lt": stale}}]}


def worker_finished(db, run: Dict[str, Any]) -> bool:
    """True when no pipeline thread / queue job is (or may still be) working
    on the run — the precondition of re-apply and continue."""
    from datetime import timedelta
    if run.get("status") == "running":
        return False
    live = db.job_queue.find_one({**_job_ids_query(run["run_id"]), "status": "running",
                                  "locked_until": {"$gt": utcnow()}}, {"_id": 1})
    if live:
        return False
    if run.get("completed_at"):
        return True
    upd = _aware(run.get("updated_at"))
    return bool(upd) and upd < utcnow() - timedelta(minutes=WORKER_STALE_MINUTES)


def ensure_finished(db, run: Dict[str, Any]) -> None:
    if not worker_finished(db, run):
        raise _err(409, "run_running", "This search is still running or stopping — try again in a moment.")


def run_recent_posts(run: Dict[str, Any]) -> Optional[int]:
    """The run's own recent_posts_to_analyze (re-apply keeps it unless changed)."""
    ks = run.get("keyword_search") or {}
    n = (ks.get("filters") or {}).get("recent_posts_to_analyze") or (ks.get("limits") or {}).get("recent_posts")
    try:
        return int(n) if n else None
    except (TypeError, ValueError):
        return None

def reapply_run(db, run: Dict[str, Any], filters: Dict[str, Any], strict: bool,
                actor: Optional[str] = None) -> Dict[str, Any]:
    """Recompute every candidate's status from what is stored — never scrapes.
    Profile-stage passes without sampled posts become ``needs_sampling``."""
    from app.discovery.pipeline import candidate_metrics
    run_id = run["run_id"]
    changed = 0
    old_n = int(((run.get("keyword_search") or {}).get("filters") or {}).get("recent_posts_to_analyze") or 0)
    for cand in db[CANDIDATES].find({"search_run_id": run_id, "status": {"$ne": "error"}}):
        stage = cand.get("stage_reached") or "discovered"
        if stage == "discovered":
            continue        # never profiled (stopped early): continue handles it
        profile = {**(cand.get("profile") or {}), "recent_posts": cand.get("recent_posts") or []}
        p_out = F.evaluate_profile(profile, filters)
        d = F.decide(p_out, strict)
        fields: Dict[str, Any]
        if not d["passed"]:
            fields = {"status": "rejected", "reasons": p_out, "unsupported": d["unsupported"],
                      "unknown": d["unknown"]}
        elif stage in ("sampled", "scraped"):
            post_out, m = F.evaluate_posts(profile, cand.get("recent_posts") or [], filters)
            outcomes = F.merge_outcomes(p_out, post_out)
            d2 = F.decide(outcomes, strict)
            fields = {"status": "passed" if d2["passed"] else "rejected", "reasons": outcomes,
                      "unsupported": d2["unsupported"], "unknown": d2["unknown"],
                      "metrics": candidate_metrics(profile, m)}
        else:
            fields = {"status": "needs_sampling", "reasons": p_out, "unsupported": d["unsupported"],
                      "unknown": d["unknown"]}
        if fields["status"] != cand.get("status") or fields.get("reasons") != cand.get("reasons"):
            changed += 1 if fields["status"] != cand.get("status") else 0
            db[CANDIDATES].update_one({"_id": cand["_id"]}, {"$set": {**fields, "updated_at": utcnow()}})
    counts = {s: db[CANDIDATES].count_documents({"search_run_id": run_id, "status": s}) for s in _STATUSES}
    db.search_history.update_one({"run_id": run_id}, {
        "$set": {"keyword_search.filters": filters, "keyword_search.strict": bool(strict),
                 "stage_counts.passed": counts["passed"], "stage_counts.rejected": counts["rejected"],
                 "stage_counts.needs_sampling": counts["needs_sampling"], "updated_at": utcnow()},
        "$push": {"filters_history": {"filters": filters, "strict": bool(strict), "at": utcnow(), "by": actor}}})
    notes = []
    new_n = int(filters.get("recent_posts_to_analyze") or 0)
    if old_n and new_n > old_n:
        notes.append(f"Re-apply uses the {old_n} posts already analyzed per profile; profiles analyzed "
                     f"before keep those.")
    plan = continue_plan(db, {**run, "keyword_search": {**(run.get("keyword_search") or {}),
                                                        "filters": filters}})
    return {"run_id": run_id, "counts": counts, "changed": changed,
            "continue_cost": plan["tokens"], "notes": notes}


def continue_plan(db, run: Dict[str, Any]) -> Dict[str, Any]:
    """What /continue would process and its worst-case token cost."""
    run_id = run["run_id"]
    org = run.get("organization_id")
    ks = run.get("keyword_search") or {}
    q = {"search_run_id": run_id}
    c = db[CANDIDATES]
    undiscovered = 0
    if (run.get("stages") or {}).get("discovery") != "done":
        done = set(run.get("discovery_pairs_done") or [])
        undiscovered = sum(1 for p in ks.get("platforms") or [] for k in ks.get("keywords") or []
                           if f"{p}|{k}" not in done)
    to_profile = c.count_documents({**q, "status": "pending", "stage_reached": "discovered"})
    to_sample = (c.count_documents({**q, "status": "needs_sampling"})
                 + c.count_documents({**q, "status": "pending", "stage_reached": "enriched"}) + to_profile)
    to_collect = to_sample + c.count_documents({**q, "status": "passed", "deep_status": {"$ne": "done"}})
    tokens = {"discover": _tokens(org, "discover", undiscovered), "enrich": _tokens(org, "enrich", to_profile),
              "sample": _tokens(org, "sample", to_sample), "collect": _tokens(org, "collect", to_collect)}
    tokens["total_max"] = sum(tokens.values())
    return {"undiscovered_pairs": undiscovered, "to_profile": to_profile, "to_sample": to_sample,
            "to_collect": to_collect, "tokens": tokens}


async def continue_run(ctx: Any, request: Request, run: Dict[str, Any], body: Any, *, db) -> Dict[str, Any]:
    from app.api.routes.search import _audit
    run_id = run["run_id"]
    from app.db.mongo import get_sync_db
    sdb = get_sync_db()
    await asyncio.to_thread(ensure_finished, sdb, run)
    plan = await asyncio.to_thread(continue_plan, sdb, run)
    if not (plan["undiscovered_pairs"] or plan["to_profile"] or plan["to_sample"] or plan["to_collect"]):
        raise _err(409, "nothing_to_continue", "Nothing left to analyze or collect in this search.")
    ks = run.get("keyword_search") or {}
    await check_access(ctx, list(ks.get("platforms") or []), db=db)
    estimate = {**plan, "notes": ["Worst case: every remaining profile passes your filters."]}
    if not body.confirm:
        raise HTTPException(status_code=409, detail={
            "success": False, "code": "CONFIRM_REQUIRED", "errorType": "confirm_required",
            "message": "Review the cost to continue, then send confirm: true.", "estimate": estimate})
    await _check_concurrency(db, ctx)
    first = next((plan["tokens"][a] for a in ("discover", "enrich", "sample", "collect") if plan["tokens"][a]), 0)
    first_unit = 0
    if first:
        for action, units in (("discover", plan["undiscovered_pairs"]), ("enrich", plan["to_profile"]),
                              ("sample", plan["to_sample"]), ("collect", plan["to_collect"])):
            if units:
                first_unit = await asyncio.to_thread(_tokens, ctx.organization_id, action, 1)
                break
    await asyncio.to_thread(check_tokens_available, ctx.organization_id, first_unit)

    stages = dict(run.get("stages") or {})
    rerun = {"sampling", "deep_scrape"}
    if plan["to_profile"]:
        rerun.add("enrichment")
    if stages.get("discovery") != "done":
        rerun.add("discovery")
    for st in rerun - {"discovery"}:
        stages[st] = "pending"
    n = int(run.get("continued_count") or 0) + 1
    sets: Dict[str, Any] = {"status": "running", "phase": "queued", "message": "Continuing keyword search…",
                            "cancel_requested": False, "error": None, "completed_at": None,
                            "stages": stages, "continued_count": n, "updated_at": utcnow()}
    if body.max_tokens:
        # a budget for THIS continuation: on top of what the run already spent
        spent = sum(int(v or 0) for v in (run.get("tokens_charged") or {}).values())
        sets["keyword_search.max_tokens"] = spent + int(body.max_tokens)
    # errors of the stages that run again are re-judged by them
    unset = {f"platform_errors.{p}": "" for p, e in (run.get("platform_errors") or {}).items()
             if (e or {}).get("stage") in rerun}
    update: Dict[str, Any] = {"$set": sets}
    if unset:
        update["$unset"] = unset
    # atomic: only one continue wins, and never while a worker still runs
    res = await db.search_history.update_one(_finished_filter(run_id), update)
    if not res.matched_count:
        raise _err(409, "run_running", "This search is still running or stopping — try again in a moment.")
    moved = [c["_id"] async for c in db[CANDIDATES].find({"search_run_id": run_id, "status": "needs_sampling"},
                                                          {"_id": 1})]
    if moved:
        await db[CANDIDATES].update_many({"_id": {"$in": moved}},
                                         {"$set": {"status": "pending", "stage_reached": "enriched",
                                                   "updated_at": utcnow()}})
    # the original (finished) job may say cancelled: it must not cancel the continuation
    await db.job_queue.update_many({"run_id": run_id, "status": "cancelled"},
                                   {"$set": {"status": "completed", "continued_at": utcnow()}})
    try:
        from app.queue.service import _get_redis
        rds = _get_redis()
        if rds:
            rds.delete(f"cancel:{run_id}")
    except Exception:
        pass
    try:
        launch(run_id, ctx, job_id=f"{run_id}_C{n}")
    except Exception as e:
        logger.exception("[KEYWORD] could not continue run %s", run_id)
        if moved:
            await db[CANDIDATES].update_many({"_id": {"$in": moved}}, {"$set": {"status": "needs_sampling"}})
        restore = {k: run.get(k) for k in ("status", "phase", "message", "completed_at", "stages",
                                           "continued_count")}
        restore["keyword_search.max_tokens"] = (run.get("keyword_search") or {}).get("max_tokens")
        await db.search_history.update_one({"run_id": run_id}, {"$set": restore})
        raise _err(503, "start_failed", "The search could not be continued. Nothing was charged — please retry.",
                   reason=str(e)[:200])
    await _audit(ctx, request, "search.keyword_continued", "search", resource_type="search_run",
                 resource_id=run_id, details={"plan": plan, "max_tokens": body.max_tokens})
    return {"run_id": run_id, "status": "running", "estimate": estimate,
            "message": "Continuing — remaining profiles are being analyzed."}
