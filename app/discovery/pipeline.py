"""Keyword search pipeline — staged and resumable.

Runs in a background thread (``KeywordSearchThread``) or, after a crash, in
the durable queue worker (task ``keyword_search``). All state lives in
MongoDB, so a run can resume where it stopped:

  search_history (the run doc)
      stages.{discovery, enrichment, sampling, deep_scrape}:
          pending | running | done | failed | stopped
      discovery_pairs_done   ["<platform>|<keyword>", ...] already searched
      discovery_queries      [{platform, keyword, query_text, locations,
                             extra_inputs}] what each search sent to Apify
                             (filters pushed into the search, see adapters)
      stage_counts           discovered, enriched, sampled, passed, rejected,
                             needs_sampling, errors, comments_scraped, leads
      tokens_charged         {stage: tokens}
      platform_errors        {platform: {stage, message}}

  discovery_candidates (one doc per run + platform + profile_id = dedupe)
      stage_reached  discovered -> enriched -> sampled -> scraped
      status         pending | passed | rejected | needs_sampling | error

Stages
  1. discovery    keyword search actor per keyword x platform ("discover")
  2. enrichment   details actor where discovery lacked key fields ("enrich",
                  cache hits free) + every profile-level filter
  3. sampling     recent posts of profile-passed candidates ("sample" when
                  fetched; free when discovery / the cache had them) + post
                  filters -> passed / rejected
  4. deep_scrape  passed candidates -> facebook_pages / facebook_posts /
                  facebook_comments + comment filter + AI analysis, exactly
                  like URL search ("collect")

Money: every charge is recorded on its item BEFORE the tokens are taken
(``discovery_charges`` on the run, ``charges.<stage>`` on a candidate), so a
resumed run never pays the same item twice; a failed / empty Apify call is
refunded; "collect" is charged when a profile's first comment fetch starts
and refunded when the profile is cancelled mid-way or no fetch succeeded
(deep_status error -> retried by continue).

Per-candidate failures are recorded on the candidate (status error) and never
fail the run; a stage failing entirely fails only that platform. Running out
of tokens stops the run gracefully (status completed, phase
stopped_tokens) keeping everything found so far.
"""
from __future__ import annotations

import contextlib
import functools
import logging
import math
import threading
from typing import Any, Callable, Dict, List, Optional

from bson import ObjectId

from app.db.models import utcnow
from app.db.mongo import get_sync_db
from app.discovery import cache as pcache
from app.discovery import filters as F
from app.discovery.adapters import get_adapter
from app.discovery.adapters.base import split_raw
from app.social.scrapers import get_scraper

logger = logging.getLogger(__name__)

CANDIDATES = "discovery_candidates"
STAGES = ("discovery", "enrichment", "sampling", "deep_scrape")
ENRICH_CHUNK = 20

# token action charged per stage
STAGE_ACTION = {"discovery": "discover", "enrichment": "enrich", "sampling": "sample",
                "deep_scrape": "collect"}


class StopRun(Exception):
    """End the run early: ``phase`` is cancelled | stopped_tokens | stopped_budget."""

    def __init__(self, phase: str, message: str):
        super().__init__(message)
        self.phase = phase
        self.message = message


def new_candidate_fields(run: Dict[str, Any]) -> Dict[str, Any]:
    return {k: run.get(k) for k in ("organization_id", "user_id", "created_by")}


class KeywordRun:
    """Context of one executing run (db, owner, settings, connector)."""

    def __init__(self, db, run: Dict[str, Any]):
        from app.connectors.apify_connector import ApifyConnector
        self.db = db
        self.run = run
        self.run_id: str = run["run_id"]
        self.org_id = run.get("organization_id")
        self.user_id = run.get("user_id")
        self.owner = {k: v for k, v in new_candidate_fields(run).items() if v}
        ks = run.get("keyword_search") or {}
        self.keywords: List[str] = list(ks.get("keywords") or [])
        self.platforms: List[str] = list(ks.get("platforms") or [])
        self.filters: Dict[str, Any] = dict(ks.get("filters") or {})
        self.strict: bool = bool(ks.get("strict"))
        self.limits: Dict[str, Any] = dict(ks.get("limits") or {})
        self.budget: Optional[int] = ks.get("max_tokens")
        self.n_posts = int(self.filters.get("recent_posts_to_analyze") or F.DEFAULT_RECENT_POSTS)
        self.spent = sum(int(v or 0) for v in (run.get("tokens_charged") or {}).values())
        self.connector = ApifyConnector(organization_id=str(self.org_id) if self.org_id else None)
        self._adapters: Dict[str, Any] = {}
        self.platform_errors: Dict[str, Any] = dict(run.get("platform_errors") or {})

    # ── plumbing ────────────────────────────────────────────────────────
    def adapter(self, platform: str):
        if platform not in self._adapters:
            self._adapters[platform] = get_adapter(platform, self.connector, self.should_abort)
        return self._adapters[platform]

    def should_abort(self) -> bool:
        from app.agent.search import is_run_cancelled
        from app.queue.service import is_job_cancelled
        return is_job_cancelled(self.run_id) or is_run_cancelled(self.run_id, self.db)

    def check_abort(self) -> None:
        if self.should_abort():
            raise StopRun("cancelled", "Search cancelled by user")

    def progress(self, message: Optional[str] = None, **fields: Any) -> None:
        upd: Dict[str, Any] = {"updated_at": utcnow(), **fields}
        if message is not None:
            upd["message"] = message
        self.db.search_history.update_one({"run_id": self.run_id}, {"$set": upd})

    def fail_platform(self, platform: str, stage: str, message: str, err: Any = None) -> None:
        """Record why a platform stopped, in plain words (what went wrong, what
        to do) with the original error kept as ``technical``."""
        from app.connectors.error_explain import explain_error
        info = explain_error(err if err is not None else message, platform=platform, stage=stage)
        self.platform_errors[platform] = {"stage": stage, "message": info["reason"][:500],
                                          "action": info["action"], "code": info["code"],
                                          "technical": info["technical"][:500]}
        self.progress(**{f"platform_errors.{platform}": self.platform_errors[platform]})

    def cand_query(self, **q: Any) -> Dict[str, Any]:
        return {"search_run_id": self.run_id, **q}

    def update_candidate(self, cand_id: Any, fields: Dict[str, Any]) -> None:
        self.db[CANDIDATES].update_one({"_id": cand_id}, {"$set": {**fields, "updated_at": utcnow()}})

    # ── tokens ──────────────────────────────────────────────────────────
    def cost_of(self, action: str, units: int) -> int:
        if units <= 0 or not self.org_id:
            return 0
        from app.billing.api_coverage import token_cost_for
        from app.lifecycle.config import token_cost
        return int(token_cost_for(str(self.org_id), action, token_cost(action) * units) or 0)

    def charge(self, stage: str, units: int, mark: Optional[Callable[[Optional[int]], None]] = None) -> int:
        """Spend the tokens of ``units`` of this stage's action. Raises StopRun
        when the balance (or the run's max_tokens budget) can't cover it.

        ``mark(cost)`` records the charge on the item BEFORE the tokens are
        taken (``mark(None)`` undoes it), so a crash between paying and doing
        the work never makes a resumed run charge the same item twice."""
        action = STAGE_ACTION[stage]
        cost = self.cost_of(action, units)
        if not cost:
            return 0
        if self.budget is not None and self.spent + cost > int(self.budget):
            raise StopRun("stopped_budget",
                          f"Stopped: the token budget of this search ({self.budget}) is used up. "
                          "Results found so far are kept.")
        from app.billing.tokens import TokensExhaustedException, consume
        if mark:
            mark(cost)
        try:
            bal = consume(str(self.org_id), cost, user_id=self.user_id,
                          reason=f"keyword_{action}", reference=self.run_id)
        except TokensExhaustedException:
            if mark:
                mark(None)
            raise StopRun("stopped_tokens",
                          "Stopped: not enough tokens to continue. Results found so far are kept — "
                          "add tokens, then continue this search.")
        except Exception:
            if mark:
                mark(None)
            raise
        if bal is None:          # organization not token-metered
            if mark:
                mark(0)
            return 0
        self.spent += cost
        self.db.search_history.update_one({"run_id": self.run_id},
                                          {"$inc": {f"tokens_charged.{stage}": cost}})
        return cost

    def refund(self, stage: str, cost: int, why: str) -> None:
        if cost <= 0:
            return
        from app.billing.tokens import refund
        refund(str(self.org_id), cost, reason=f"keyword_{STAGE_ACTION[stage]}: {why}", reference=self.run_id)
        self.spent -= cost
        self.db.search_history.update_one({"run_id": self.run_id},
                                          {"$inc": {f"tokens_charged.{stage}": -cost}})

    def mark_candidate(self, cand_id: Any, stage: str, cost: Optional[int]) -> None:
        """Per-candidate charge marker ``charges.<stage>`` (None = not charged)."""
        if cost is None:
            self.db[CANDIDATES].update_one({"_id": cand_id}, {"$unset": {f"charges.{stage}": ""}})
        else:
            self.db[CANDIDATES].update_one({"_id": cand_id}, {"$set": {f"charges.{stage}": int(cost)}})

    def mark_pair(self, pair: str, cost: Optional[int]) -> None:
        """Run-level charge marker of one discovery keyword x platform."""
        self.db.search_history.update_one({"run_id": self.run_id},
                                          {"$pull": {"discovery_charges": {"pair": pair}}})
        if cost is not None:
            self.db.search_history.update_one({"run_id": self.run_id},
                                              {"$push": {"discovery_charges": {"pair": pair, "cost": int(cost)}}})

    def call_failed(self, stage: str, cost: int, err: Exception) -> None:
        """An Apify call failed: refund it; a cancellation stops the run."""
        self.refund(stage, cost, f"Apify call failed: {str(err)[:120]}")
        if getattr(err, "error_type", None) == "CANCELLED" or self.should_abort():
            raise StopRun("cancelled", "Search cancelled by user")

    # ── counts ──────────────────────────────────────────────────────────
    def refresh_counts(self) -> Dict[str, int]:
        c = self.db[CANDIDATES]
        q = self.cand_query
        counts = {
            "discovered": c.count_documents(q()),
            "enriched": c.count_documents(q(stage_reached={"$in": ["enriched", "sampled", "scraped"]})),
            "sampled": c.count_documents(q(stage_reached={"$in": ["sampled", "scraped"]})),
            "passed": c.count_documents(q(status="passed")),
            "rejected": c.count_documents(q(status="rejected")),
            "needs_sampling": c.count_documents(q(status="needs_sampling")),
            "errors": c.count_documents(q(status="error")),
            "comments_scraped": self.db.facebook_comments.count_documents({"search_run_id": self.run_id}),
            "leads": self.db.ai_comments.count_documents({"search_run_id": self.run_id, "is_lead": True}),
        }
        pages = self.db.facebook_pages.count_documents({"search_run_id": self.run_id})
        self.progress(stage_counts=counts, pages_found=counts["discovered"], pages_stored=pages)
        return counts


# ═════════════════════════════════════════════════════════════════════════
# Profile evaluation (shared by the pipeline and re-apply)
# ═════════════════════════════════════════════════════════════════════════

def profile_decision(profile: Dict[str, Any], filters: Dict[str, Any], strict: bool) -> Dict[str, Any]:
    outcomes = F.evaluate_profile(profile, filters)
    return {"outcomes": outcomes, **F.decide(outcomes, strict)}


def candidate_metrics(profile: Dict[str, Any], post_m: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    m = {"followers": profile.get("followers"), "posts_count": profile.get("posts_count"),
         "last_post_at": profile.get("last_post_at"), "verified": profile.get("verified"),
         "is_business": profile.get("is_business"),
         "has_contact_info": any((profile.get("contacts") or {}).get(k) for k in ("emails", "phones", "websites"))}
    if post_m:
        m.update({k: v for k, v in post_m.items() if not (k == "last_post_at" and v is None)})
    return m


def _profile_fields(profile: Dict[str, Any], filters: Dict[str, Any], strict: bool) -> Dict[str, Any]:
    d = profile_decision(profile, filters, strict)
    prof = {k: v for k, v in profile.items() if k != "recent_posts"}
    return {
        "profile": prof, "name": profile.get("name"), "url": profile.get("url"),
        "reasons": d["outcomes"], "unsupported": d["unsupported"], "unknown": d["unknown"],
        "metrics": candidate_metrics(profile),
        "status": "pending" if d["passed"] else "rejected",
        "stage_reached": "enriched", "error": None,
    }


# ═════════════════════════════════════════════════════════════════════════
# Stages
# ═════════════════════════════════════════════════════════════════════════

def _insert_candidate(r: KeywordRun, profile: Dict[str, Any], keyword: str) -> bool:
    """Insert one discovered profile; a duplicate (another keyword found it)
    only records the keyword. Returns True when a new candidate was added."""
    coll = r.db[CANDIDATES]
    key = {"search_run_id": r.run_id, "platform": profile["platform"], "profile_id": profile["profile_id"]}
    # same profile id OR same canonical URL (one result carried a native id,
    # another only its URL) -> one candidate
    same = {"search_run_id": r.run_id, "platform": profile["platform"],
            "$or": [{"profile_id": profile["profile_id"]}, {"url": profile.get("url")}]}
    found = coll.find_one(same, {"_id": 1})
    if found:
        coll.update_one({"_id": found["_id"]}, {"$addToSet": {"keywords": keyword}})
        return False
    posts, raws = split_raw(profile.get("recent_posts") or [])
    prof = {k: v for k, v in profile.items() if k != "recent_posts"}
    doc = {
        **new_candidate_fields(r.run), **key, "_id": ObjectId(),
        "url": profile.get("url"), "name": profile.get("name"), "keywords": [keyword],
        "stage_reached": "discovered", "status": "pending", "reasons": [], "unsupported": [],
        "unknown": [], "metrics": candidate_metrics(profile), "profile": prof,
        "recent_posts": posts, "posts_raw": raws, "posts_source": "discovery" if posts else None,
        "error": None, "page_ref": None, "deep_status": None,
        "created_at": utcnow(), "updated_at": utcnow(),
    }
    try:
        coll.insert_one(doc)
    except Exception as e:  # unique index race: someone inserted it first
        if "duplicate" in str(e).lower() or e.__class__.__name__ == "DuplicateKeyError":
            coll.update_one(key, {"$addToSet": {"keywords": keyword}})
            return False
        raise
    return True


def record_query(r: KeywordRun, platform: str, keyword: str, adapter: Any) -> None:
    """Store what the keyword search sent to Apify (one entry per platform x keyword)."""
    rec = getattr(adapter, "last_query", None)
    if not isinstance(rec, dict):
        rec = {"platform": platform, "keyword": keyword, "query_text": keyword, "locations": [],
               "extra_inputs": {}}
    rec = {**rec, "platform": platform, "keyword": keyword}
    r.db.search_history.update_one({"run_id": r.run_id},
                                   {"$pull": {"discovery_queries": {"platform": platform, "keyword": keyword}}})
    r.db.search_history.update_one({"run_id": r.run_id}, {"$push": {"discovery_queries": rec}})


def stage_discovery(r: KeywordRun) -> None:
    max_c = int(r.limits.get("max_candidates") or 1)
    pairs = [(p, kw) for p in r.platforms for kw in r.keywords]
    if not pairs:
        return
    per_call = max(1, math.ceil(max_c / len(pairs)))
    done = set(r.run.get("discovery_pairs_done") or [])
    # pairs paid for by an earlier invocation that died before finishing them
    charged = {d.get("pair"): int(d.get("cost") or 0) for d in r.run.get("discovery_charges") or []
               if isinstance(d, dict)}
    errors: Dict[str, List[Exception]] = {}
    attempted: Dict[str, int] = {}
    for platform, kw in pairs:
        key = f"{platform}|{kw}"
        if key in done:
            continue
        r.check_abort()
        if r.db[CANDIDATES].count_documents(r.cand_query()) >= max_c:
            r.progress(message=f"Candidate limit reached ({max_c}).")
            break
        r.progress(phase="discovery", message=f"Searching {platform} for “{kw}”…")
        attempted[platform] = attempted.get(platform, 0) + 1
        if key in charged:
            cost = charged[key]
        else:
            cost = r.charge("discovery", 1, mark=functools.partial(r.mark_pair, key))
        adapter = r.adapter(platform)
        adapter.last_query = None
        try:
            # the filters go into the Apify search where the actor supports them
            profiles = adapter.discover(kw, per_call, filters=r.filters)
            record_query(r, platform, kw, adapter)
            added = 0
            for prof in profiles:
                if not prof.get("profile_id") or not prof.get("url"):
                    continue
                if r.db[CANDIDATES].count_documents(r.cand_query()) >= max_c:
                    break
                added += 1 if _insert_candidate(r, prof, kw) else 0
        except StopRun:
            raise
        except Exception as e:
            logger.warning("[KEYWORD] discovery %s/%s failed: %s", platform, kw, e)
            if adapter.last_query:
                record_query(r, platform, kw, adapter)
            r.mark_pair(key, None)
            errors.setdefault(platform, []).append(e)
            from app.connectors.error_explain import explain_error
            info = explain_error(e, platform=platform, stage="discovery")
            r.db.search_history.update_one({"run_id": r.run_id},
                                           {"$addToSet": {"discovery_pairs_done": key},
                                            "$push": {"discovery_errors": {"platform": platform, "keyword": kw,
                                                                           "message": info["reason"][:300],
                                                                           "action": info["action"],
                                                                           "technical": info["technical"][:300]}}})
            r.call_failed("discovery", cost, e)
            continue
        r.db.search_history.update_one({"run_id": r.run_id}, {"$addToSet": {"discovery_pairs_done": key}})
        logger.info("[KEYWORD] %s discovery '%s': %d results, %d new", platform, kw, len(profiles), added)
        r.refresh_counts()
    for platform, errs in errors.items():
        # a platform with candidates from earlier keywords / invocations keeps
        # going: failing it would strand candidates that were already paid for
        if (len(errs) >= attempted.get(platform, 0)
                and not r.db[CANDIDATES].count_documents(r.cand_query(platform=platform))):
            r.fail_platform(platform, "discovery", str(errs[-1]), err=errs[-1])


def _split(total: int, n: int) -> List[int]:
    """Split ``total`` tokens over ``n`` items (the first ones take the remainder)."""
    if n <= 0:
        return []
    base, rest = divmod(int(total), n)
    return [base + (1 if i < rest else 0) for i in range(n)]


def _evaluate_and_store(r: KeywordRun, cand: Dict[str, Any], profile: Dict[str, Any]) -> None:
    profile = {**profile, "recent_posts": cand.get("recent_posts") or []}
    r.update_candidate(cand["_id"], _profile_fields(profile, r.filters, r.strict))


def stage_enrichment(r: KeywordRun) -> None:
    for platform in r.platforms:
        if platform in r.platform_errors:
            continue
        adapter = r.adapter(platform)
        cands = list(r.db[CANDIDATES].find(r.cand_query(platform=platform, status="pending",
                                                         stage_reached="discovered")))
        need: List[Dict[str, Any]] = []
        for cand in cands:
            prof = cand.get("profile") or {}
            if adapter.needs_enrichment(prof):
                cached = pcache.get_profile(r.db, platform, cand["profile_id"])
                if cached:
                    _evaluate_and_store(r, cand, {**cached, "enriched_from": "cache"})
                else:
                    need.append(cand)
            else:
                _evaluate_and_store(r, cand, prof)
        if not need:
            r.refresh_counts()
            continue
        r.progress(phase="enrichment", message=f"Fetching details of {len(need)} {platform} profiles…")
        chunk_failures, chunks = 0, 0
        last_error: Any = ""
        for i in range(0, len(need), ENRICH_CHUNK):
            chunk = need[i:i + ENRICH_CHUNK]
            chunks += 1
            r.check_abort()
            # what each candidate already paid (an earlier invocation died after paying)
            paid = {c["_id"]: (c.get("charges") or {}).get("enrichment") for c in chunk}
            new = [c for c in chunk if paid[c["_id"]] is None]

            def mark(cost: Optional[int], new: List[Dict[str, Any]] = new,
                     paid: Dict[Any, Optional[int]] = paid) -> None:
                shares = _split(cost, len(new)) if cost is not None else [None] * len(new)
                for c, share in zip(new, shares):
                    paid[c["_id"]] = share
                    r.mark_candidate(c["_id"], "enrichment", share)
            if new:
                r.charge("enrichment", len(new), mark=mark)
            try:
                result = adapter.enrich([c["profile"] for c in chunk])
            except StopRun:
                raise
            except Exception as e:
                logger.warning("[KEYWORD] %s enrichment failed: %s", platform, e)
                chunk_failures += 1
                last_error = e
                from app.connectors.error_explain import short_reason
                for cand in chunk:
                    r.mark_candidate(cand["_id"], "enrichment", None)
                    r.update_candidate(cand["_id"], {"status": "error",
                                                     "error": short_reason(e, platform=platform, stage="enrichment")[:400],
                                                     "error_technical": str(e)[:300]})
                r.call_failed("enrichment", sum(int(v or 0) for v in paid.values()), e)
                continue
            for cand in chunk:
                prof = result.get(cand["profile_id"])
                if not prof:
                    # nothing came back for this profile: give its share back
                    r.refund("enrichment", int(paid.get(cand["_id"]) or 0), "profile details not returned")
                    r.mark_candidate(cand["_id"], "enrichment", None)
                    r.update_candidate(cand["_id"], {"status": "error",
                                                     "error": "The platform didn't return this profile's details "
                                                              "(it may be private, removed or renamed). Nothing "
                                                              "was charged for it."})
                    continue
                pcache.put_profile(r.db, prof)
                _evaluate_and_store(r, cand, prof)
            r.refresh_counts()
        if chunks and chunk_failures == chunks:
            r.fail_platform(platform, "enrichment", str(last_error), err=last_error)
        r.refresh_counts()


def _sample_candidate(r: KeywordRun, cand: Dict[str, Any]) -> None:
    platform = cand["platform"]
    adapter = r.adapter(platform)
    n = r.n_posts
    profile = {**(cand.get("profile") or {}), "recent_posts": cand.get("recent_posts") or []}
    posts: List[Dict[str, Any]]
    raws: List[Any]
    source = None
    if profile["recent_posts"] and adapter.posts_available(profile, n):
        posts = profile["recent_posts"][:n]
        raws = (cand.get("posts_raw") or [None] * len(posts))[:n]
        source = cand.get("posts_source") or "discovery"
    else:
        cached = pcache.get_posts(r.db, platform, cand["profile_id"], n)
        if cached:
            posts, raws, source = cached["posts"], cached["raw"], "cache"
        else:
            prior = (cand.get("charges") or {}).get("sampling")
            cost = int(prior) if prior is not None else r.charge(
                "sampling", 1, mark=lambda c: r.mark_candidate(cand["_id"], "sampling", c))
            try:
                got = adapter.recent_posts(profile, n)
            except StopRun:
                raise
            except Exception as e:
                logger.warning("[KEYWORD] recent posts of %s failed: %s", cand.get("url"), e)
                r.mark_candidate(cand["_id"], "sampling", None)
                r.call_failed("sampling", cost, e)
                from app.connectors.error_explain import short_reason
                r.update_candidate(cand["_id"], {"status": "error",
                                                 "error": short_reason(e, platform=cand.get("platform"), stage="sampling")[:400],
                                                 "error_technical": str(e)[:300]})
                raise _SampleFailed(str(e))
            posts, raws = split_raw(got)
            source = "apify"
            pcache.put_posts(r.db, platform, cand["profile_id"], posts, raws, n)
    post_outcomes, post_m = F.evaluate_posts(profile, posts, r.filters)
    profile_outcomes = F.evaluate_profile(profile, r.filters)
    outcomes = F.merge_outcomes(profile_outcomes, post_outcomes)
    d = F.decide(outcomes, r.strict)
    r.update_candidate(cand["_id"], {
        "recent_posts": posts, "posts_raw": raws, "posts_source": source,
        "reasons": outcomes, "unsupported": d["unsupported"], "unknown": d["unknown"],
        "metrics": candidate_metrics(profile, post_m),
        "status": "passed" if d["passed"] else "rejected", "stage_reached": "sampled",
        "sampled_at": utcnow(),
    })


class _SampleFailed(Exception):
    pass


def stage_sampling(r: KeywordRun) -> None:
    for platform in r.platforms:
        if platform in r.platform_errors:
            continue
        cands = list(r.db[CANDIDATES].find(r.cand_query(platform=platform, status="pending",
                                                         stage_reached="enriched")))
        if not cands:
            continue
        r.progress(phase="sampling", message=f"Analyzing recent posts of {len(cands)} {platform} profiles…")
        failures, last_error = 0, ""
        for i, cand in enumerate(cands):
            r.check_abort()
            try:
                _sample_candidate(r, cand)
            except _SampleFailed as e:
                failures += 1
                last_error = str(e)
            if i % 5 == 4:
                r.refresh_counts()
        if failures and failures == len(cands):
            r.fail_platform(platform, "sampling", last_error)
        r.refresh_counts()


def _page_doc(r: KeywordRun, cand: Dict[str, Any]) -> Dict[str, Any]:
    """facebook_pages doc with the fields URL search writes."""
    prof = cand.get("profile") or {}
    contacts = prof.get("contacts") or {}
    return {
        "page_id": None if str(prof.get("profile_id") or "").startswith("http") else prof.get("profile_id"),
        "page_name": prof.get("name") or cand.get("url"),
        "facebook_url": cand["url"],
        "platform": cand["platform"],
        "category": prof.get("category"),
        "about": prof.get("bio"),
        "followers": prof.get("followers"),
        "likes": prof.get("likes"),
        "verified": prof.get("verified"),
        "phone": (contacts.get("phones") or [None])[0],
        "email": (contacts.get("emails") or [None])[0],
        "whatsapp": None,
        "website": (contacts.get("websites") or [None])[0],
        "address": prof.get("address"),
        "city": None, "state": None,
        "country": prof.get("country"),
        "profile_picture": prof.get("profile_picture"),
        "cover_image": None,
        "source_type": "keyword",
        "source_page_url": cand["url"],
        "search_run_id": r.run_id,
        "search_keyword": ", ".join(cand.get("keywords") or []) or None,
        "source": "keyword_search",
        "provider": "apify",
        "posts_status": "not_started",
        "comments_status": "not_started",
        "discovery_candidate_id": str(cand["_id"]),
        "keyword_metrics": cand.get("metrics") or {},
        "created_at": utcnow(), "updated_at": utcnow(),
    }


def _fallback_raw(post: Dict[str, Any]) -> Dict[str, Any]:
    """A raw-like item from a normalized post (when the raw wasn't kept)."""
    return {"url": post.get("url"), "postUrl": post.get("url"), "id": post.get("id"),
            "commentsCount": post.get("comments_count"), "likesCount": post.get("likes_count"),
            "likes": post.get("likes_count"), "shares": post.get("shares_count"),
            "timestamp": post.get("posted_at"), "date": post.get("posted_at")}


def deep_scrape_candidate(r: KeywordRun, cand: Dict[str, Any], per_post: int, cap: int,
                          before_fetch: Optional[Callable[[], None]] = None) -> Dict[str, int]:
    """Store the page, its sampled posts and their comments, then run the
    comment filter + AI analysis — the same documents URL search writes.
    ``before_fetch`` runs before each comment fetch (the collect charge is
    taken there, so a profile with nothing to fetch is never charged).
    Returns posts / comments stored and fetches attempted / succeeded."""
    from app.social.url_search import filter_and_analyze_post, upsert_comment, upsert_post
    db = r.db
    scraper = get_scraper(cand["platform"], connector=r.connector)
    page_doc = _page_doc(r, cand)
    page_doc.update(r.owner)
    existing = db.facebook_pages.find_one({"facebook_url": page_doc["facebook_url"], "search_run_id": r.run_id})
    if existing:
        page_doc = existing
    else:
        page_doc["_id"] = ObjectId()
        db.facebook_pages.insert_one(page_doc)
    page_id = str(page_doc["_id"])
    r.update_candidate(cand["_id"], {"page_ref": page_id})

    posts = cand.get("recent_posts") or []
    raws = cand.get("posts_raw") or []
    post_docs: List[Dict[str, Any]] = []
    seen: set = set()
    for idx, post in enumerate(posts):
        raw = raws[idx] if idx < len(raws) and isinstance(raws[idx], dict) else _fallback_raw(post)
        try:
            post_json = scraper.normalize_post(raw, page_doc)
        except Exception as e:
            logger.warning("[KEYWORD] post normalization failed: %s", e)
            continue
        if not post_json or not post_json.get("post_url"):
            continue
        stored = upsert_post(db, post_json, page_doc, r.run_id, r.owner)
        if stored["_id"] in seen:       # the same post twice: fetch its comments once
            continue
        seen.add(stored["_id"])
        stored["_known_comments"] = post.get("comments_count")
        post_docs.append(stored)
    db.facebook_pages.update_one({"_id": page_doc["_id"]}, {"$set": {
        "posts_status": "completed" if post_docs else "empty", "posts_count": len(post_docs),
        "posts_collected_at": utcnow(), "updated_at": utcnow()}})
    try:
        from app.agent.search import _compute_page_stats
        stats = _compute_page_stats(post_docs)
        page_stats = {k: stats[k] for k in ("total_posts_found", "latest_post_date", "activity_status",
                                            "total_comments_on_qualifying_posts", "lead_score",
                                            "has_qualifying_posts")}
        for k in ("relevant_posts_count", "qualifying_posts_count"):
            if stats.get(k):
                page_stats[k] = stats[k]
        db.facebook_pages.update_one({"_id": page_doc["_id"]}, {"$set": {**page_stats, "updated_at": utcnow()}})
    except Exception:
        logger.warning("[KEYWORD] page stats computation failed", exc_info=True)

    comments = fetches = fetched_ok = 0
    if getattr(scraper, "comments_supported", True):
        for post in post_docs:
            r.check_abort()
            if comments >= cap:
                break
            known = post.pop("_known_comments", None)
            if known == 0:
                db.facebook_posts.update_one({"_id": post["_id"]}, {"$set": {
                    "comments_status": "empty", "scraped_comment_count": 0, "updated_at": utcnow()}})
                continue
            if before_fetch:
                before_fetch()
            fetches += 1
            db.facebook_posts.update_one({"_id": post["_id"]}, {"$set": {
                "comments_status": "running", "comments_error": None, "scraped_comment_count": 0,
                "comments_started_at": utcnow(), "updated_at": utcnow()}})
            try:
                raw_comments = scraper.fetch_comments(post["post_url"], per_post, should_abort=r.should_abort)
                fetched_ok += 1
            except Exception as e:
                if getattr(e, "error_type", None) == "CANCELLED" or r.should_abort():
                    raise StopRun("cancelled", "Search cancelled by user")
                logger.warning("[KEYWORD] comments failed for %s: %s", post.get("post_url"), e)
                db.facebook_posts.update_one({"_id": post["_id"]}, {"$set": {
                    "comments_status": "error", "comments_error": str(e)[:300], "updated_at": utcnow()}})
                continue
            stored_for_post = 0
            for item in raw_comments or []:
                try:
                    cj = scraper.normalize_comment(item, post)
                except Exception as e:
                    logger.warning("[KEYWORD] comment normalization failed: %s", e)
                    continue
                if not cj or not (cj.get("text") or cj.get("comment_id")):
                    continue
                if upsert_comment(db, cj, post, r.run_id, r.owner):
                    comments += 1
                    stored_for_post += 1
                if comments >= cap:
                    break
            db.facebook_posts.update_one({"_id": post["_id"]}, {"$set": {
                "comments_status": "completed" if stored_for_post else "empty",
                "scraped_comment_count": stored_for_post, "comments_collected_at": utcnow(),
                "updated_at": utcnow()}})
            if stored_for_post:
                filter_and_analyze_post(db, r.run_id, post, stored_for_post)
    db.facebook_pages.update_one({"_id": page_doc["_id"]}, {"$set": {
        "comments_status": "completed" if not fetches or fetched_ok else "error", "updated_at": utcnow()}})
    return {"posts": len(post_docs), "comments": comments, "fetches": fetches, "fetched_ok": fetched_ok}


def stage_deep_scrape(r: KeywordRun) -> None:
    from app.admin.settings import effective_limits
    lim = effective_limits()
    per_post = max(1, min(int(r.limits.get("comments_per_post") or lim["max_comments_per_post_default"]),
                          lim["max_comments_per_post_cap"], lim["global_max_comments"]))
    cap = max(500, lim.get("global_max_comments", 500))
    cands = list(r.db[CANDIDATES].find(r.cand_query(status="passed", deep_status={"$ne": "done"})))
    if not cands:
        return
    for i, cand in enumerate(cands):
        if cand["platform"] in r.platform_errors and r.platform_errors[cand["platform"]].get("stage") == "deep_scrape":
            continue
        r.check_abort()
        r.progress(phase="deep_scrape",
                   message=f"Collecting posts and comments of {cand.get('name') or cand['url']} "
                           f"({i + 1}/{len(cands)})…")
        # "collect" is charged once per profile, when its first comment fetch
        # starts; a profile already paid for (earlier invocation) isn't charged again
        prior = (cand.get("charges") or {}).get("deep_scrape")
        state: Dict[str, Any] = {"paid": prior is not None, "cost": int(prior or 0)}

        def before_fetch(cand_id: Any = cand["_id"], state: Dict[str, Any] = state) -> None:
            if not state["paid"]:
                state["cost"] = r.charge("deep_scrape", 1,
                                         mark=lambda c: r.mark_candidate(cand_id, "deep_scrape", c))
                state["paid"] = True

        def give_back(why: str, cand_id: Any = cand["_id"], state: Dict[str, Any] = state) -> None:
            r.mark_candidate(cand_id, "deep_scrape", None)
            r.refund("deep_scrape", state["cost"], why)
            state["cost"], state["paid"] = 0, False

        try:
            res = deep_scrape_candidate(r, cand, per_post, cap, before_fetch=before_fetch)
        except StopRun as stop:
            if stop.phase == "cancelled":       # not finished: a continue redoes (and charges) it once
                give_back("cancelled")
            raise
        except Exception as e:
            logger.warning("[KEYWORD] deep scrape of %s failed: %s", cand.get("url"), e)
            give_back(f"deep scrape failed: {str(e)[:120]}")
            from app.connectors.error_explain import short_reason
            r.update_candidate(cand["_id"], {"deep_status": "error",
                                             "error": short_reason(e, platform=cand.get("platform"), stage="deep_scrape")[:400],
                                             "error_technical": str(e)[:300]})
            if r.should_abort():
                raise StopRun("cancelled", "Search cancelled by user")
            continue
        if res["fetches"] and not res["fetched_ok"]:
            # every comment fetch failed: nothing was delivered -> refund, retryable by continue
            give_back("no comments could be fetched")
            r.update_candidate(cand["_id"], {"deep_status": "error",
                                             "error": "Comments could not be fetched; continue the search to retry.",
                                             "posts_stored": res["posts"]})
            r.refresh_counts()
            continue
        r.update_candidate(cand["_id"], {"deep_status": "done", "stage_reached": "scraped", "error": None,
                                         "comments_scraped": res["comments"], "posts_stored": res["posts"]})
        r.refresh_counts()


STAGE_FUNCS: Dict[str, Callable[[KeywordRun], None]] = {
    "discovery": stage_discovery, "enrichment": stage_enrichment,
    "sampling": stage_sampling, "deep_scrape": stage_deep_scrape,
}


# ═════════════════════════════════════════════════════════════════════════
# Run
# ═════════════════════════════════════════════════════════════════════════

def _finish(r: KeywordRun, *, status: str, phase: str, message: str, error: Optional[str] = None) -> Dict[str, Any]:
    from app.agent.search import persist_scrape_info, pipeline_audit
    from app.events.notifications import notify_search_finished
    counts = r.refresh_counts()
    r.progress(message=message, status=status, phase=phase, error=error, completed_at=utcnow())
    pipeline_audit(f"keyword_search.{phase}", "search", r.owner, success=status != "error",
                   resource_type="search_run", resource_id=r.run_id,
                   details={"counts": counts, "platform_errors": r.platform_errors, "error": error})
    if status != "cancelled":
        notify_search_finished(r.owner, r.run_id, success=status != "error", platform="keyword",
                               posts=counts.get("passed", 0), comments=counts.get("comments_scraped", 0),
                               error=error)
    try:
        persist_scrape_info(r.db, r.run_id, r.connector)
    except Exception:
        pass
    return {"status": status, "phase": phase, "run_id": r.run_id, "counts": counts,
            "success": status != "error", "error": error}


def run_keyword_search(run_id: str) -> Dict[str, Any]:
    """Execute (or resume) one keyword search. Stages already ``done`` are
    skipped; within a stage only unprocessed candidates are handled."""
    db = get_sync_db()
    if db is None:
        return {"status": "error", "error": "Database unavailable"}
    run = db.search_history.find_one({"run_id": run_id})
    if not run or run.get("search_type") != "keyword":
        return {"status": "error", "error": f"Keyword search run not found: {run_id}"}
    from app.agent.search import mark_run_cancelled
    from app.connectors.apify_connector import set_run_context
    set_run_context(organization_id=run.get("organization_id"), user_id=run.get("user_id"),
                    search_run_id=run_id)
    r = KeywordRun(db, run)
    if r.should_abort():
        mark_run_cancelled(run_id, db)
        return {"status": "cancelled", "run_id": run_id}

    # an organization on its own Apify key never falls back to LeadAI's key
    from app.services.tenant_api_keys import get_tenant_apify_token_sync
    token, source = get_tenant_apify_token_sync(r.org_id)
    if not token:
        if source == "organization_missing":
            from app.services.tenant_api_keys import own_key_problem, record_key_failure
            problem = own_key_problem(r.org_id, "apify") or {}
            msg = problem.get("message") or "Your organization's own Apify key is missing."
            record_key_failure(r.org_id, "apify", problem.get("code", "OWN_APIFY_KEY_MISSING"), msg)
        else:
            msg = "Apify is not configured on this server (no API token)."
        return _finish(r, status="error", phase="failed", message=msg, error=msg)

    stages = dict(run.get("stages") or {})
    try:
        for stage in STAGES:
            if stages.get(stage) == "done":
                continue
            stages[stage] = "running"
            r.progress(phase=stage, **{f"stages.{stage}": "running"})
            STAGE_FUNCS[stage](r)
            # a stage fails when every platform failed in it
            failed_here = [p for p, e in r.platform_errors.items() if e.get("stage") == stage]
            stages[stage] = "failed" if failed_here and len(r.platform_errors) >= len(r.platforms) else "done"
            r.progress(**{f"stages.{stage}": stages[stage]})
            if stages[stage] == "failed":
                break
    except StopRun as stop:
        current = next((s for s in STAGES if stages.get(s) == "running"), None)
        if current:
            r.progress(**{f"stages.{current}": "stopped"})
        if stop.phase == "cancelled":
            mark_run_cancelled(run_id, db)
            return _finish(r, status="cancelled", phase="cancelled", message=stop.message)
        return _finish(r, status="completed", phase=stop.phase, message=stop.message)

    if r.platforms and len(r.platform_errors) >= len(r.platforms):
        from app.connectors.error_explain import platform_label
        err = "; ".join(f"{platform_label(p)}: {e['message']}" for p, e in r.platform_errors.items())
        return _finish(r, status="error", phase="failed", message=f"The search couldn't finish. {err}", error=err)
    counts = r.refresh_counts()
    note = ""
    if r.platform_errors:
        from app.connectors.error_explain import STAGE_LABELS, platform_label
        note = " (" + ", ".join(f"{platform_label(p)} stopped while {STAGE_LABELS.get(e['stage'], e['stage'])}"
                                for p, e in r.platform_errors.items()) + ")"
    return _finish(r, status="completed", phase="completed",
                   message=(f"Completed — {counts['passed']} of {counts['discovered']} profiles passed, "
                            f"{counts['comments_scraped']} comments, {counts['leads']} leads{note}"))


class KeywordSearchThread(threading.Thread):
    """Background worker for a keyword search run (start / continue)."""

    def __init__(self, run_id: str, job_id: Optional[str] = None, organization_id: Optional[str] = None,
                 created_by: Optional[str] = None, user_id: Optional[str] = None):
        super().__init__(daemon=True)
        self.run_id = run_id
        self.job_id = job_id or run_id
        self.organization_id = organization_id
        self.created_by = created_by
        self.user_id = user_id

    def run(self) -> None:
        from app.queue.service import claim_inline_job, complete_job, fail_job, keep_lease_alive

        # Recorded already claimed by this thread so the queue worker never
        # runs it twice; if this process dies the lease lapses and the
        # worker resumes it (stages already done are skipped).
        owner: Optional[str] = None
        try:
            owner = claim_inline_job(
                "keyword_search",
                payload={"run_id": self.run_id, "organization_id": self.organization_id,
                         "created_by": self.created_by, "user_id": self.user_id},
                run_id=self.job_id, organization_id=self.organization_id,
                created_by=self.created_by, user_id=self.user_id)
            if owner is None:
                return
        except Exception as q_err:  # noqa: BLE001 — no durable record; still run it
            logger.warning("[KEYWORD] Could not record durable job: %s", q_err)
        try:
            with (keep_lease_alive(self.job_id, owner) if owner else contextlib.nullcontext()):
                res = run_keyword_search(self.run_id)
            try:
                complete_job(self.job_id, res if isinstance(res, dict) else None)
            except Exception as c_err:  # noqa: BLE001
                logger.debug("Could not complete queue job: %s", c_err)
        except Exception as e:
            try:
                fail_job(self.job_id, str(e), can_retry=False)
            except Exception as f_err:  # noqa: BLE001
                logger.debug("Could not fail queue job: %s", f_err)
            logger.exception("[KEYWORD] background run crashed")
            db = get_sync_db()
            if db is not None:
                db.search_history.update_one({"run_id": self.run_id}, {"$set": {
                    "status": "error", "phase": "failed", "error": f"Internal error: {e}",
                    "completed_at": utcnow(), "updated_at": utcnow()}})
            from app.events.notifications import notify_search_finished
            notify_search_finished({"organization_id": self.organization_id, "user_id": self.user_id,
                                    "created_by": self.created_by}, self.run_id, success=False,
                                   error=f"Internal error: {e}")
