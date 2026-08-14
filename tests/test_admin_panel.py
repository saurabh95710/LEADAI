"""
Admin panel unit tests: the settings registry (defaults, coercion, DB-row
wins, token masking), audit redaction, role ranking, and the lead-scoring
defaults that must reproduce the pre-admin behavior exactly.
"""
import pytest

from app.admin import audit, settings as s
from app.auth import roles
from app.config import get_settings
from app.pipeline.comment_ai import (
    comment_lead_score,
    derive_quality_from_score,
    signal_lead_score,
)


@pytest.fixture(autouse=True)
def clear_settings_cache():
    """The TTL cache is module-global; clear it so tests never leak values."""
    s._CACHE.clear()
    yield
    s._CACHE.clear()


def _patch_db(monkeypatch, values):
    """Simulate the system_settings collection (missing key -> env default)."""
    monkeypatch.setattr(s, "_sync_get", lambda key: values.get(key))


# ── Settings registry ────────────────────────────────────────────────────────

def test_defaults_present_and_typed():
    assert s.get_bool("platform.facebook.enabled") is True
    assert s.get_int("limits.max_posts_default", 0) == 20
    assert s.get_int("limits.max_posts_cap", 0) == 100
    assert s.get_int("limits.max_comments_per_post_cap", 0) == 500
    assert s.get_bool("ai.enabled") is True
    assert s.get_str("ai.model") != ""
    assert s.get_bool("maintenance.enabled") is False
    assert s.get_bool("features.url_search.enabled") is True
    assert s.get_bool("ci.detect_selling_intent") is False


def test_unknown_key_returns_none():
    assert s.get_setting("does.not.exist") is None


def test_db_row_wins_over_default(monkeypatch):
    _patch_db(monkeypatch, {"limits.max_posts_cap": 42,
                            "platform.facebook.enabled": False})
    assert s.get_setting("limits.max_posts_cap") == 42
    assert s.get_int("limits.max_posts_cap") == 42
    assert s.get_bool("platform.facebook.enabled") is False
    # untouched keys still fall back to the registry default
    assert s.get_int("limits.max_posts_default", 0) == 20


def test_coercion_of_admin_submitted_values():
    assert s._coerce("platform.facebook.enabled", "false") is False
    assert s._coerce("platform.facebook.enabled", "on") is True
    assert s._coerce("limits.max_posts_cap", "42") == 42
    assert s._coerce("scoring.hot_min", 90) == 90
    assert s._coerce("ai.model", 7) == "7"
    assert s._coerce("limits.max_posts_cap", "not-a-number") == 100


def test_effective_limits_defaults_match_config():
    cfg = get_settings()
    limits = s.effective_limits()
    assert limits["min_comments"] == cfg.min_comments
    assert limits["max_posts_default"] == 20
    assert limits["max_posts_cap"] == 100
    assert limits["max_comments_per_post_default"] == 30
    assert limits["max_comments_per_post_cap"] == 500
    assert limits["global_max_comments"] == cfg.max_comments_to_collect


def test_platform_actor_keys():
    assert s.platform_actor_key("facebook", "posts") == "actor.facebook.posts"
    assert s.platform_actor_key("facebook") == "actor.facebook.pages"
    assert s.platform_actor_key("instagram") == "actor.instagram.main"
    assert s.platform_actor_key("youtube") == "actor.youtube.main"
    assert s.platform_actor_key("linkedin", "company") == "actor.linkedin.company"
    assert s.platform_actor_key("linkedin", "posts") == "actor.linkedin.posts"
    assert s.platform_actor_key("unknown") == "actor.facebook.pages"


def test_apify_token_override_and_mask(monkeypatch):
    _patch_db(monkeypatch, {"apify.token": "apify_secret_token_99"})
    assert s.get_apify_token() == "apify_secret_token_99"
    hint = s.get_apify_token_hint()
    assert len(hint) == 8
    assert hint.endswith("apify_secret_token_99"[-4:])
    assert hint != "apify_secret_token_99"
    # override removed -> env token is used, hint still masked
    _patch_db(monkeypatch, {})
    assert s.get_apify_token() == s.settings.apify_api_token
    assert s.get_apify_token_hint() != s.get_apify_token() or not s.get_apify_token()


def test_sessions_epoch_default_and_override(monkeypatch):
    assert s.sessions_epoch() == 0
    _patch_db(monkeypatch, {"security.session_epoch": 5})
    assert s.sessions_epoch() == 5


# ── Audit redaction ──────────────────────────────────────────────────────────

def test_audit_redact_masks_secrets():
    clean = audit._redact({
        "token": "abc",
        "password": "x",
        "nested": {"apify.token": "y", "keep": 1},
        "email": "a@b.c",
        "action": "limits.update",
    })
    assert clean["token"] == "••••"
    assert clean["password"] == "••••"
    assert clean["nested"]["apify.token"] == "••••"
    assert clean["nested"]["keep"] == 1
    assert clean["email"] == "a@b.c"
    assert clean["action"] == "limits.update"


def test_audit_redact_non_dict_scalar():
    assert audit._redact("just-a-string") == {"value": "<redacted>"}


# ── Roles ────────────────────────────────────────────────────────────────────

def test_role_rank():
    assert roles.role_rank("viewer") == 1
    assert roles.role_rank("manager") == 2
    assert roles.role_rank("super_admin") == 3
    assert roles.role_rank("bogus") == 0


def test_effective_role(monkeypatch):
    monkeypatch.setattr(roles.settings, "admin_email", "admin@gmail.com")
    monkeypatch.setattr(roles, "get_admin_record",
                        lambda email: {"email": email, "role": "manager"})
    # the env account is always the recovery super-admin
    assert roles.effective_role("ADMIN@GMAIL.COM") == "super_admin"
    # other accounts take the role stored in admin_users
    assert roles.effective_role("jane@example.com") == "manager"


def test_effective_role_fallback(monkeypatch):
    monkeypatch.setattr(roles.settings, "admin_email", "admin@gmail.com")
    monkeypatch.setattr(roles, "get_admin_record",
                        lambda email: {"email": email, "role": "ghost"})
    assert roles.effective_role("jane@example.com") == "viewer"


# ── Lead scoring defaults reproduce the original behavior ───────────────────

def test_comment_lead_score_default_formula():
    assert comment_lead_score(None) == 0
    assert comment_lead_score({"is_useful": False, "confidence_score": 0.9}) == 0

    ai = {"is_useful": True,
          "confidence_score": 0.6,
          "priority": "high",
          "lead_quality": "warm",
          "contact": {"phone": "9876543210"},
          "spam_score": 0.1}
    # 0.6*50 + 20 (high) + 10 (warm) + 6 (phone) - 2 (spam) = 64
    assert comment_lead_score(ai) == 64


def test_signal_lead_score_default_weights():
    assert signal_lead_score(None, "anything") == 0
    # phone signal only -> scoring.phone (20)
    assert signal_lead_score({"is_useful": True}, "call me at 9876543210") == 20
    # phone + email -> 20 + 15 = 35
    assert signal_lead_score(
        {"is_useful": True},
        "call 9876543210 or mail me@x.com") == 35


def test_derive_quality_from_score_default_thresholds():
    assert derive_quality_from_score(85) == "hot"
    assert derive_quality_from_score(80) == "hot"
    assert derive_quality_from_score(60) == "warm"
    assert derive_quality_from_score(49) is None
    assert derive_quality_from_score(0) is None
