"""Plain-language explanations for scraping errors.

Apify and the platforms answer with technical text ("Invalid actor input (HTTP
400): Field input.search must match pattern …"). Customers and admins see
instead what went wrong, in words, and what to do; the original text is kept as
``technical`` for an administrator.

``explain_error(err, platform=, stage=)`` → {code, reason, action, technical}.
``err`` may be a ScrapeError / ApifyError / any exception, or a message.
"""
import re
from typing import Any, Dict, Optional

PLATFORM_LABELS = {"instagram": "Instagram", "facebook": "Facebook", "youtube": "YouTube",
                   "linkedin": "LinkedIn"}

STAGE_LABELS = {
    "discovery": "finding profiles",
    "enrichment": "reading profile details",
    "sampling": "reading recent posts",
    "deep_scrape": "collecting comments",
    "page": "reading the page",
    "posts": "collecting posts",
    "comments": "collecting comments",
}

_PREFIXES = re.compile(r"^(?:(?:Discovery|Enrichment) failed|Recent posts could not be fetched|"
                       r"Profile details could not be fetched|Deep scrape failed)\s*:\s*", re.I)


def platform_label(platform: Optional[str]) -> str:
    return PLATFORM_LABELS.get(str(platform or "").lower(), (platform or "The platform").capitalize()
                               if platform else "The platform")


def _technical(err: Any) -> str:
    text = str(err or "").strip()
    return _PREFIXES.sub("", text)[:600]


def explain_error(err: Any, *, platform: Optional[str] = None,
                  stage: Optional[str] = None) -> Dict[str, str]:
    """What went wrong and what to do, in words anyone can follow."""
    p = platform_label(platform)
    doing = STAGE_LABELS.get(str(stage or ""), "searching")
    technical = _technical(err)
    kind = str(getattr(err, "error_type", "") or "").upper()
    low = technical.lower()

    def out(code: str, reason: str, action: str) -> Dict[str, str]:
        return {"code": code, "reason": reason, "action": action, "technical": technical}

    if kind == "CANCELLED" or "cancelled by user" in low:
        return out("cancelled", "The search was stopped.", "")
    if "must match pattern" in low:
        return out("invalid_characters",
                   f"{p} doesn't accept some characters in the search words (for example - . , / @ # & or quotes).",
                   "LeadAI now removes these characters by itself, so run the search again. "
                   "If it still fails, use plain words, e.g. \"e commerce\" instead of \"e-commerce\".")
    if (getattr(err, "__class__", None) and err.__class__.__name__ == "ApifyError"
            and ("own" in low or "your organization" in low or "your apify" in low)):
        return out("own_key", technical or "Your organization's Apify key can't be used.",
                   "An admin of your organization can fix the key in Admin → API keys & plan.")
    if re.search(r"paid plan|rent(?:al|ed)? (?:the )?actor|requires? a subscription|trial (?:has )?expired", low):
        return out("actor_needs_paid_plan",
                   f"The {p} scraping tool needs a paid Apify plan or a rental before it can run.",
                   "An administrator needs to rent or subscribe to this actor on Apify, or choose another "
                   "actor in Global Settings → Search limits.")
    if kind == "BILLING" or re.search(r"\b402\b|credit|usage limit|monthly usage|not enough usage|"
                                      r"payment required|hard limit", low):
        return out("no_credit", "The Apify account has run out of credit or reached its monthly spending limit.",
                   "An administrator needs to add Apify credit or raise the limit. Super Admin → API limits "
                   "shows how much is used.")
    if re.search(r"memory limit|concurrent|too many (?:runs|jobs)", low):
        return out("too_many_runs", "The Apify account is running too many jobs at the same time.",
                   "Wait for other searches to finish, then try again.")
    if kind == "ACCESS_DENIED" or re.search(r"\b401\b|\b403\b|unauthori[sz]ed|invalid token|token (?:is )?"
                                            r"(?:invalid|not valid)|forbidden|access denied", low):
        return out("key_rejected", "Apify rejected the key LeadAI used for this search.",
                   "Check the Apify key: Super Admin → API keys for LeadAI's key, or Admin → API keys & plan "
                   "if your organization uses its own.")
    if re.search(r"actor (?:was )?not found|\b404\b|record-not-found|actor-not-found", low):
        return out("actor_not_found", f"The {p} scraping tool set up in LeadAI wasn't found on Apify.",
                   "An administrator should check the actor name in Global Settings → Search limits "
                   "(or Super Admin → Actors).")
    if kind == "INVALID_INPUT" or "invalid actor input" in low or "input is not valid" in low:
        return out("invalid_input", f"{p}'s scraping tool didn't accept the search settings.",
                   "Try simpler keywords or fewer filters. If it keeps happening, the tool may have changed "
                   "its settings; tell your administrator.")
    if kind == "BLOCKED" or re.search(r"blocked|captcha|login required|log in to|checkpoint|"
                                      r"restricted|banned", low):
        return out("blocked", f"{p} temporarily blocked the scraper (it limits automated visits).",
                   "Try again in a little while. If it keeps happening, the actor may need a proxy or a "
                   "different actor.")
    if re.search(r"\b429\b|rate.?limit|too many requests", low):
        return out("rate_limited", "Apify is limiting how fast LeadAI can call it right now.",
                   "Wait a minute and try again.")
    if kind == "ACTOR_TIMED_OUT" or re.search(r"timed? ?out|timeout", low):
        return out("timed_out", f"{p} took too long to answer while {doing}.",
                   "Try again with fewer profiles, posts or comments, or try again later.")
    if kind == "NETWORK_ERROR" or re.search(r"getaddrinfo|connection (?:error|refused|reset|aborted)|"
                                            r"network|name resolution|ssl", low):
        return out("network", "LeadAI couldn't reach Apify (a network problem).",
                   "Try again in a moment. If it continues, the server's internet connection may be down.")
    if kind == "DATASET_ERROR" or "dataset" in low:
        return out("results_unreadable", f"The results from {p} couldn't be read.", "Try again.")
    if re.search(r"no (?:results|items|profiles)|returned (?:nothing|0|no)|empty", low):
        return out("no_results", f"{p} returned nothing while {doing}.",
                   "Try other or broader keywords, or fewer filters.")
    if kind == "ACTOR_FAILED" or "actor" in low and "fail" in low:
        return out("actor_failed", f"{p}'s scraping tool stopped with an error while {doing}.",
                   "Try again later. If it keeps failing, the actor may be broken; tell your administrator.")
    return out("unknown", f"Something went wrong on {p} while {doing}.",
               "Try again. If it keeps happening, send the technical details below to your administrator.")


def short_reason(err: Any, *, platform: Optional[str] = None, stage: Optional[str] = None) -> str:
    """One line for tables and lists: "<reason> <action>"."""
    e = explain_error(err, platform=platform, stage=stage)
    return (e["reason"] + (" " + e["action"] if e["action"] else "")).strip()
