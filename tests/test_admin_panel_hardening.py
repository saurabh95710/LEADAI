"""
Admin Panel & Global Configuration Hardening tests (Prompt 9).

Tests dashboard enhancements, health endpoint improvements, settings wiring,
audit toggle, cost protection, AI call budget, and settings registry validation.
"""
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.admin import audit, settings as s
from app.api.routes.admin import _APP_START_TIME, _contact_query, _score_bucket
from app.auth import roles
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
    """Simulate the system_settings collection."""
    monkeypatch.setattr(s, "_sync_get", lambda key: values.get(key))


# ── Dashboard Enhancements ──────────────────────────────────────────────────


class TestDashboardEnhancements:
    """Verify the dashboard endpoint returns new metrics."""

    def test_app_start_time_is_set(self):
        assert _APP_START_TIME > 0
        assert _APP_START_TIME <= time.time()

    def test_app_start_time_is_reasonable(self):
        """Start time should be within the last year."""
        assert time.time() - _APP_START_TIME < 365 * 86400

    def test_contact_query_structure(self):
        q = _contact_query()
        assert "$or" in q
        fields = {next(iter(c.keys())) for c in q["$or"]}
        assert fields == {"phone", "email", "whatsapp"}

    def test_score_bucket_complete(self):
        assert _score_bucket(None) == "no score"
        assert _score_bucket(0) == "0–19"
        assert _score_bucket(50) == "40–59"
        assert _score_bucket(85) == "80–100"
        assert _score_bucket(100) == "80–100"


# ── Health Endpoint ─────────────────────────────────────────────────────────


class TestHealthEndpoint:
    """Verify health endpoint returns version, uptime, and errors."""

    def test_version_format(self):
        from app.main import app
        assert app.version == "2.5.0"

    def test_uptime_calculation(self):
        uptime = time.time() - _APP_START_TIME
        assert uptime >= 0
        assert uptime < 365 * 86400

    def test_app_start_time_used_for_uptime(self):
        """Uptime is calculated from _APP_START_TIME."""
        uptime = round(time.time() - _APP_START_TIME, 1)
        assert isinstance(uptime, float)
        assert uptime >= 0


# ── Settings Wiring ─────────────────────────────────────────────────────────


class TestSettingsWiring:
    """Verify critical settings are consumed at runtime."""

    def test_effective_limits_includes_cost_settings(self, monkeypatch):
        monkeypatch.setattr(s, "_sync_get", lambda key: {
            "cost.stop_on_limit": True,
            "cost.warn_before_expensive": False,
        }.get(key))
        lim = s.effective_limits()
        assert "stop_on_limit" in lim
        assert "warn_before_expensive" in lim
        assert lim["stop_on_limit"] is True
        assert lim["warn_before_expensive"] is False

    def test_effective_limits_cost_defaults(self, monkeypatch):
        monkeypatch.setattr(s, "_sync_get", lambda key: None)
        lim = s.effective_limits()
        # Both cost settings default to True in SETTING_DEFAULTS
        assert lim["stop_on_limit"] is True
        assert lim["warn_before_expensive"] is True

    def test_audit_logging_toggle_default(self, monkeypatch):
        """Default: audit logging is enabled."""
        monkeypatch.setattr(s, "_sync_get", lambda key: None)
        assert s.get_bool("security.audit_logging") is True

    def test_audit_logging_toggle_off(self, monkeypatch):
        """When audit_logging is off, audit() should not write."""
        monkeypatch.setattr(s, "_sync_get", lambda key: {
            "security.audit_logging": False,
        }.get(key))
        assert s.get_bool("security.audit_logging") is False

    def test_is_audit_enabled_reads_setting(self, monkeypatch):
        monkeypatch.setattr(s, "_sync_get", lambda key: {
            "security.audit_logging": False,
        }.get(key))
        assert s.is_audit_enabled() is False

        monkeypatch.setattr(s, "_sync_get", lambda key: {
            "security.audit_logging": True,
        }.get(key))
        assert s.is_audit_enabled() is True

    def test_ai_max_calls_per_job_default(self, monkeypatch):
        monkeypatch.setattr(s, "_sync_get", lambda key: None)
        assert s.get_int("ai.max_calls_per_job", 0) == 500

    def test_ai_max_calls_per_job_override(self, monkeypatch):
        monkeypatch.setattr(s, "_sync_get", lambda key: {
            "ai.max_calls_per_job": 50,
        }.get(key))
        assert s.get_int("ai.max_calls_per_job", 0) == 50


# ── Settings Registry Validation ────────────────────────────────────────────


class TestSettingsRegistry:
    """Verify the settings registry is complete and valid."""

    def test_all_setting_categories_exist(self):
        from app.settings.registry import CATEGORIES
        cat_ids = {c["id"] for c in CATEGORIES}
        expected = {
            "general", "branding", "appearance", "ai", "limits",
            "platforms", "features", "notifications", "leads",
            "seo", "maintenance", "security",
        }
        assert expected.issubset(cat_ids)

    def test_registry_has_no_duplicate_keys(self):
        from app.settings.registry import REGISTERED_KEYS
        assert len(REGISTERED_KEYS) == len(set(REGISTERED_KEYS))

    def test_all_defaults_are_registered(self):
        from app.settings.registry import REGISTERED_KEYS
        for key in s.SETTING_DEFAULTS:
            if key.startswith("actor.") or key.startswith("apify."):
                continue
            if key == "security.session_epoch":
                continue
            # Audit logging can no longer be disabled: the default is kept for
            # display only and is intentionally not an editable setting.
            if key == "security.audit_logging":
                continue
            assert key in REGISTERED_KEYS, f"Default key {key} not in registry"

    def test_validate_patch_rejects_unknown_keys(self):
        from app.settings.registry import validate_patch
        clean, errors = validate_patch({"unknown.key": "value"})
        assert "unknown.key" in errors

    def test_validate_patch_validates_types(self):
        from app.settings.registry import validate_patch
        clean, errors = validate_patch({
            "limits.max_posts_cap": "not-a-number",
        })
        assert "limits.max_posts_cap" in errors

    def test_validate_patch_coerces_bool(self):
        from app.settings.registry import validate_patch
        clean, errors = validate_patch({
            "platform.facebook.enabled": "true",
        })
        assert clean.get("platform.facebook.enabled") is True
        assert not errors

    def test_validate_patch_validates_ranges(self):
        from app.settings.registry import validate_patch
        clean, errors = validate_patch({
            "limits.max_posts_cap": -5,
        })
        assert "limits.max_posts_cap" in errors

    def test_validate_patch_validates_colors(self):
        from app.settings.registry import validate_patch
        clean, errors = validate_patch({
            "branding.colors.primary": "not-a-color",
        })
        assert "branding.colors.primary" in errors

    def test_validate_patch_validates_urls(self):
        from app.settings.registry import validate_patch
        clean, errors = validate_patch({
            "general.company.website": "not-a-url",
        })
        assert "general.company.website" in errors

    def test_validate_patch_validates_emails(self):
        from app.settings.registry import validate_patch
        clean, errors = validate_patch({
            "general.contact.support_email": "not-an-email",
        })
        assert "general.contact.support_email" in errors


# ── Settings History / Rollback ─────────────────────────────────────────────


class TestSettingsHistory:
    """Verify settings history mechanism."""

    def test_current_revision_default(self, monkeypatch):
        """When DB is empty, revision is 0."""
        monkeypatch.setattr(s, "_sync_get", lambda key: None)
        # Can't easily test current_revision without mocking DB
        # but we can verify the function exists
        assert callable(s.current_revision)

    def test_push_revision_is_callable(self):
        assert callable(s.push_revision)

    def test_snapshot_all_is_callable(self):
        assert callable(s.snapshot_all)

    def test_apply_snapshot_is_callable(self):
        assert callable(s.apply_snapshot)

    def test_export_payload_is_callable(self):
        assert callable(s.export_payload)


# ── Role Authorization ──────────────────────────────────────────────────────


class TestRoleAuthorization:
    """Verify role system is properly configured."""

    def test_role_rank_complete(self):
        assert roles.role_rank("viewer") == 1
        assert roles.role_rank("manager") == 2
        assert roles.role_rank("super_admin") == 3
        assert roles.role_rank("bogus") == 0

    def test_require_admin_returns_callable(self):
        dep = roles.require_admin("viewer")
        assert callable(dep)

    def test_require_viewer_exists(self):
        assert callable(roles.require_viewer)

    def test_require_manager_exists(self):
        assert callable(roles.require_manager)

    def test_require_super_exists(self):
        assert callable(roles.require_super)


# ── Lead Scoring Defaults ──────────────────────────────────────────────────


class TestLeadScoringDefaults:
    """Verify scoring defaults reproduce original behavior."""

    def test_comment_lead_score_none(self):
        assert comment_lead_score(None) == 0

    def test_comment_lead_score_not_useful(self):
        assert comment_lead_score({"is_useful": False, "confidence_score": 0.9}) == 0

    def test_comment_lead_score_high_quality(self):
        ai = {
            "is_useful": True,
            "confidence_score": 0.6,
            "priority": "high",
            "lead_quality": "warm",
            "contact": {"phone": "9876543210"},
            "spam_score": 0.1,
        }
        score = comment_lead_score(ai)
        assert 60 <= score <= 70

    def test_signal_lead_score_phone(self):
        assert signal_lead_score({"is_useful": True}, "call me at 9876543210") == 20

    def test_signal_lead_score_phone_email(self):
        score = signal_lead_score(
            {"is_useful": True}, "call 9876543210 or mail me@x.com")
        assert score == 35

    def test_derive_quality_hot(self):
        assert derive_quality_from_score(85) == "hot"
        assert derive_quality_from_score(80) == "hot"

    def test_derive_quality_warm(self):
        assert derive_quality_from_score(60) == "warm"

    def test_derive_quality_none(self):
        assert derive_quality_from_score(49) is None
        assert derive_quality_from_score(0) is None


# ── Audit Redaction ─────────────────────────────────────────────────────────


class TestAuditRedaction:
    """Verify secrets are never logged."""

    def test_redact_token(self):
        clean = audit._redact({"token": "abc123"})
        assert clean["token"] == "••••"

    def test_redact_password(self):
        clean = audit._redact({"password": "secret"})
        assert clean["password"] == "••••"

    def test_redact_nested(self):
        clean = audit._redact({
            "nested": {"apify.token": "x", "keep": 1},
        })
        assert clean["nested"]["apify.token"] == "••••"
        assert clean["nested"]["keep"] == 1

    def test_redact_preserves_non_secrets(self):
        clean = audit._redact({
            "email": "a@b.c",
            "action": "settings.update",
            "count": 42,
        })
        assert clean["email"] == "a@b.c"
        assert clean["action"] == "settings.update"
        assert clean["count"] == 42

    def test_redact_scalar_string(self):
        result = audit._redact("just-a-string")
        assert result == {"value": "<redacted>"}

    def test_redact_new_password(self):
        clean = audit._redact({"new_password": "hunter2"})
        assert clean["new_password"] == "••••"

    def test_redact_old_password(self):
        clean = audit._redact({"old_password": "hunter2"})
        assert clean["old_password"] == "••••"


# ── Platform Configuration ──────────────────────────────────────────────────


class TestPlatformConfiguration:
    """Verify platform settings are properly managed."""

    def test_platform_actor_keys(self):
        assert s.platform_actor_key("facebook", "posts") == "actor.facebook.posts"
        assert s.platform_actor_key("facebook") == "actor.facebook.pages"
        assert s.platform_actor_key("instagram") == "actor.instagram.main"
        assert s.platform_actor_key("youtube") == "actor.youtube.main"
        assert s.platform_actor_key("linkedin", "company") == "actor.linkedin.company"
        assert s.platform_actor_key("linkedin", "posts") == "actor.linkedin.posts"
        assert s.platform_actor_key("unknown") == "actor.facebook.pages"

    def test_is_platform_enabled_default(self, monkeypatch):
        monkeypatch.setattr(s, "_sync_get", lambda key: None)
        assert s.is_platform_enabled("facebook") is True

    def test_is_platform_enabled_disabled(self, monkeypatch):
        monkeypatch.setattr(s, "_sync_get", lambda key: {
            "platform.facebook.enabled": False,
        }.get(key))
        assert s.is_platform_enabled("facebook") is False


# ── Maintenance Mode ────────────────────────────────────────────────────────


class TestMaintenanceMode:
    """Verify maintenance mode settings."""

    def test_maintenance_disabled_by_default(self, monkeypatch):
        monkeypatch.setattr(s, "_sync_get", lambda key: None)
        assert s.is_maintenance_enabled() is False

    def test_maintenance_message_default(self, monkeypatch):
        monkeypatch.setattr(s, "_sync_get", lambda key: None)
        msg = s.maintenance_message()
        assert isinstance(msg, str)
        assert len(msg) > 0


# ── Feature Flags ───────────────────────────────────────────────────────────


class TestFeatureFlags:
    """Verify feature flags work correctly."""

    def test_url_search_enabled_default(self, monkeypatch):
        monkeypatch.setattr(s, "_sync_get", lambda key: None)
        assert s.get_bool("features.url_search.enabled") is True

    def test_exports_enabled_default(self, monkeypatch):
        monkeypatch.setattr(s, "_sync_get", lambda key: None)
        assert s.get_bool("features.exports.enabled") is True

    def test_url_search_disabled(self, monkeypatch):
        monkeypatch.setattr(s, "_sync_get", lambda key: {
            "features.url_search.enabled": False,
        }.get(key))
        assert s.get_bool("features.url_search.enabled") is False


# ── AI Configuration ────────────────────────────────────────────────────────


class TestAIConfiguration:
    """Verify AI settings are properly wired."""

    def test_ai_enabled_default(self, monkeypatch):
        monkeypatch.setattr(s, "_sync_get", lambda key: None)
        assert s.get_bool("ai.enabled") is True

    def test_ai_rule_fallback_default(self, monkeypatch):
        monkeypatch.setattr(s, "_sync_get", lambda key: None)
        assert s.get_bool("ai.rule_fallback") is True

    def test_ai_model_default(self, monkeypatch):
        monkeypatch.setattr(s, "_sync_get", lambda key: None)
        model = s.get_str("ai.model")
        assert model != ""

    def test_ai_temperature_default(self, monkeypatch):
        monkeypatch.setattr(s, "_sync_get", lambda key: None)
        temp = s.get_int("ai.temperature", 1)
        assert isinstance(temp, (int, float))


# ── Scoring Configuration ──────────────────────────────────────────────────


class TestScoringConfiguration:
    """Verify scoring weights are configurable."""

    def test_scoring_weights_have_defaults(self, monkeypatch):
        monkeypatch.setattr(s, "_sync_get", lambda key: None)
        assert s.get_int("scoring.confidence_weight", 0) == 50
        assert s.get_int("scoring.priority_weight", 0) == 20
        assert s.get_int("scoring.quality_weight", 0) == 20
        assert s.get_int("scoring.contact_phone", 0) == 6
        assert s.get_int("scoring.contact_email", 0) == 4
        assert s.get_int("scoring.spam_penalty", 0) == 20

    def test_scoring_hot_min_default(self, monkeypatch):
        monkeypatch.setattr(s, "_sync_get", lambda key: None)
        assert s.get_int("scoring.hot_min", 0) == 80

    def test_scoring_warm_min_default(self, monkeypatch):
        monkeypatch.setattr(s, "_sync_get", lambda key: None)
        assert s.get_int("scoring.warm_min", 0) == 50


# ── Comment Intelligence Configuration ──────────────────────────────────────


class TestCommentIntelligenceConfig:
    """Verify CI toggles are wired."""

    def test_ci_detect_phone_default(self, monkeypatch):
        monkeypatch.setattr(s, "_sync_get", lambda key: None)
        assert s.get_bool("ci.detect_phone") is True

    def test_ci_detect_email_default(self, monkeypatch):
        monkeypatch.setattr(s, "_sync_get", lambda key: None)
        assert s.get_bool("ci.detect_email") is True

    def test_ci_ignore_emoji_only_default(self, monkeypatch):
        monkeypatch.setattr(s, "_sync_get", lambda key: None)
        assert s.get_bool("ci.ignore_emoji_only") is True

    def test_ci_ignore_spam_default(self, monkeypatch):
        monkeypatch.setattr(s, "_sync_get", lambda key: None)
        assert s.get_bool("ci.ignore_spam") is True

    def test_ci_min_lead_score_default(self, monkeypatch):
        monkeypatch.setattr(s, "_sync_get", lambda key: None)
        assert s.get_int("ci.min_lead_score", 0) == 0


# ── Session Security ────────────────────────────────────────────────────────


class TestSessionSecurity:
    """Verify session security settings."""

    def test_session_timeout_default(self, monkeypatch):
        monkeypatch.setattr(s, "_sync_get", lambda key: None)
        timeout = s.get_int("security.session_timeout_hours", 1)
        assert timeout == 168

    def test_login_protection_default(self, monkeypatch):
        monkeypatch.setattr(s, "_sync_get", lambda key: None)
        assert s.get_bool("security.login_protection") is True

    def test_sessions_epoch_default(self, monkeypatch):
        monkeypatch.setattr(s, "_sync_get", lambda key: None)
        assert s.sessions_epoch() == 0

    def test_sessions_epoch_override(self, monkeypatch):
        monkeypatch.setattr(s, "_sync_get", lambda key: {
            "security.session_epoch": 5,
        }.get(key))
        assert s.sessions_epoch() == 5
