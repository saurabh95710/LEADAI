"""
Environment variable management tests: the registry (defaults, coercion,
override precedence over .env, source reporting, masking) and the dynamic
wiring (login reads the ADMIN_PASSWORD_HASH override, session TTL/cookie
flags are dynamic, the Gemini key is read at call time).
"""
import hashlib
import os

import pytest

from app.admin import envvars as ev
from app.admin.settings import _CACHE as SETTINGS_CACHE
from app.auth import service as auth_svc


@pytest.fixture(autouse=True)
def clear_caches():
    ev.clear_cache()
    SETTINGS_CACHE.clear()
    yield
    ev.clear_cache()
    SETTINGS_CACHE.clear()


def _patch_overrides(monkeypatch, values):
    """Simulate the env_overrides collection (missing key -> no override)."""
    monkeypatch.setattr(ev, "_sync_override",
                        lambda name: {"value": values[name]} if name in values else None)
    monkeypatch.setattr(ev, "_async_override",
                        lambda name: {"value": values[name]} if name in values else None)


# ── Registry ────────────────────────────────────────────────────────────────

def test_registry_covers_every_config_field():
    from app.config import Settings
    attrs = {v.get("settings_attr") for v in ev.ENVVAR_REGISTRY
             if v.get("settings_attr")}
    assert attrs == set(Settings.model_fields.keys()), \
        f"missing from env registry: {set(Settings.model_fields.keys()) - attrs}"


def test_registry_entries_typed():
    for entry in ev.ENVVAR_REGISTRY:
        assert entry["kind"] in ("str", "int", "bool")
        assert "name" in entry and "description" in entry
    kinds = {e["name"]: e["kind"] for e in ev.ENVVAR_REGISTRY}
    assert kinds["SESSION_TTL_DAYS"] == "int"
    assert kinds["SESSION_COOKIE_SECURE"] == "bool"
    assert kinds["GEMINI_API_KEY"] == "str"


def test_defaults_when_no_override_and_no_env(monkeypatch):
    _patch_overrides(monkeypatch, {})
    assert ev.get_envvar_int("SESSION_TTL_DAYS") == 7
    assert ev.get_envvar_bool("SESSION_COOKIE_SECURE") is False
    assert ev.get_envvar_str("GEMINI_MODEL") == "gemini-2.5-flash"
    assert ev.get_envvar_str("BUSINESS_DOMAIN") == ""


def test_override_wins_over_env(monkeypatch):
    _patch_overrides(monkeypatch, {"SESSION_TTL_DAYS": 1})
    assert ev.get_envvar_int("SESSION_TTL_DAYS") == 1
    _patch_overrides(monkeypatch, {"SESSION_COOKIE_SECURE": "true"})
    assert ev.get_envvar_bool("SESSION_COOKIE_SECURE") is True
    _patch_overrides(monkeypatch, {"GEMINI_MODEL": "gemini-2.0-flash"})
    assert ev.get_envvar_str("GEMINI_MODEL") == "gemini-2.0-flash"


def test_os_environ_is_consulted_for_env_only_vars(monkeypatch):
    _patch_overrides(monkeypatch, {})
    monkeypatch.setenv("API_PORT", "9000")
    assert ev.get_envvar_int("API_PORT") == 9000
    monkeypatch.delenv("API_PORT")


def test_coercion_keeps_type_safety(monkeypatch):
    _patch_overrides(monkeypatch, {"SESSION_TTL_DAYS": "not-a-number"})
    assert ev.get_envvar_int("SESSION_TTL_DAYS") == 7  # invalid -> default
    _patch_overrides(monkeypatch, {"SESSION_COOKIE_SECURE": "off"})
    assert ev.get_envvar_bool("SESSION_COOKIE_SECURE") is False


def test_unknown_name_is_rejected():
    assert ev.known_name("NOT_A_REAL_VAR") is False
    assert ev.get_envvar("NOT_A_REAL_VAR") is None
    assert ev.set_envvar_override("NOT_A_REAL_VAR", "x") is False


# ── Source reporting & masking ──────────────────────────────────────────────

def test_source_reports_override_env_default(monkeypatch):
    _patch_overrides(monkeypatch, {"SESSION_TTL_DAYS": 3})
    info = ev.envvar_info("SESSION_TTL_DAYS")
    assert info["source"] == "override"
    assert info["overridden"] is True
    assert info["value"] == 3

    _patch_overrides(monkeypatch, {})
    monkeypatch.setenv("API_PORT", "9100")
    info = ev.envvar_info("API_PORT")
    assert info["source"] == "env"
    monkeypatch.delenv("API_PORT")

    info = ev.envvar_info("BUSINESS_DOMAIN")
    assert info["source"] == "default"


def test_secret_values_never_returned(monkeypatch):
    _patch_overrides(monkeypatch, {"GEMINI_API_KEY": "AIza-secret-key-1234"})
    info = ev.envvar_info("GEMINI_API_KEY")
    assert info["secret"] is True
    assert info["value"] == ""          # never the raw value
    assert info["masked"].endswith("1234")
    assert "AIza-secret" not in info["masked"]
    assert ev.is_secret("GEMINI_API_KEY")
    assert not ev.is_secret("SESSION_TTL_DAYS")


def test_apify_token_managed_elsewhere():
    assert ev.is_managed_elsewhere("APIFY_API_TOKEN")
    info = ev.envvar_info("APIFY_API_TOKEN")
    assert info["managed_elsewhere"] is True


def test_restart_flags_are_explicit():
    assert ev.envvar_info("MONGO_URI")["restart"] is True
    assert ev.envvar_info("SESSION_TTL_DAYS")["restart"] is False
    assert ev.envvar_info("GEMINI_API_KEY")["restart"] is False


# ── Dynamic wiring ──────────────────────────────────────────────────────────

def test_login_uses_admin_password_override(monkeypatch):
    _patch_overrides(monkeypatch, {"ADMIN_PASSWORD_HASH": "0" * 64})
    monkeypatch.setattr(auth_svc, "_admin_user_record", lambda email: None)
    assert auth_svc.verify_admin_login("admin@gmail.com", "anything") is None
    _patch_overrides(monkeypatch, {})
    assert auth_svc.verify_admin_login("admin@gmail.com", "wrong") is None


def test_login_with_override_password_succeeds(monkeypatch):
    new_hash = hashlib.sha256(b"NewPass@2026").hexdigest()
    _patch_overrides(monkeypatch, {"ADMIN_PASSWORD_HASH": new_hash})
    monkeypatch.setattr(auth_svc, "_admin_user_record", lambda email: None)
    user = auth_svc.verify_admin_login("admin@gmail.com", "NewPass@2026")
    assert user is not None and user["role"] == "super_admin"
    assert auth_svc.verify_admin_login("admin@gmail.com", "Admin@2026") is None


def test_session_ttl_and_secure_cookie_are_dynamic(monkeypatch):
    _patch_overrides(monkeypatch, {"SESSION_TTL_DAYS": 2,
                                   "SESSION_COOKIE_SECURE": "true"})
    value = auth_svc.build_session_value({"email": "a@b.c", "role": "viewer"})
    payload_b64 = value.rsplit(".", 1)[0]
    import json, base64
    payload = json.loads(base64.urlsafe_b64decode(
        payload_b64 + "=" * (-len(payload_b64) % 4)))
    assert payload["exp"] - payload["iat"] == 2 * 86400
    assert auth_svc._secret() != ""