"""
Tests for the Global Settings / white-label layer:
schema registry validation & coercion, public config safety, import
sanitization (import preview is DB-free), and the revision/history service
against a minimal fake database.
"""
import asyncio

from bson import ObjectId

from app.admin import settings as sa
from app.settings import registry as R
from app.api.routes import settings as settings_api


# ── fake database (sync + async views over the same docs, Motor-like) ───────

def _match(doc, key, value):
    if isinstance(value, dict) and "$ne" in value:
        return doc.get(key, value["$ne"]) != value["$ne"]
    return doc.get(key) == value


class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, *args):
        key, direction = args
        if isinstance(key, str):
            self.docs.sort(key=lambda d: d.get(key, 0), reverse=(direction < 0))
        return self

    def limit(self, n):
        self.docs = self.docs[:n]
        return self

    def __aiter__(self):
        self._it = iter(self.docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class FakeCollection:
    def __init__(self, name, docs=None):
        self.name = name
        self.docs = list(docs or [])

    def find_one(self, query, projection=None, sort=None):
        docs = list(self.docs)
        if sort:
            key, direction = sort[0]
            docs.sort(key=lambda d: d.get(key, 0), reverse=(direction < 0))
        for doc in docs:
            if all(_match(doc, k, v) for k, v in (query or {}).items()):
                if projection:
                    return {k: doc[k] for k in projection
                            if isinstance(projection, dict) and k in doc}
                return doc
        return None

    def find(self, query=None, projection=None):
        return FakeCursor([
            doc for doc in self.docs
            if all(_match(doc, k, v) for k, v in (query or {}).items())
        ])

    def insert_one(self, doc):
        if "_id" not in doc:
            doc["_id"] = ObjectId()
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc["_id"]})()

    def update_one(self, query, update, upsert=False):
        for doc in self.docs:
            if all(_match(doc, k, v) for k, v in query.items()):
                for k, v in update.get("$set", {}).items():
                    doc[k] = v
                return type("R", (), {"modified_count": 1})()
        if upsert:
            new_doc = {**query, **update.get("$set", {})}
            self.insert_one(new_doc)
            return type("R", (), {"modified_count": 1})()
        return type("R", (), {"modified_count": 0})()

    def delete_one(self, query):
        before = len(self.docs)
        self.docs = [d for d in self.docs
                     if not all(_match(d, k, v) for k, v in query.items())]
        return type("R", (), {"deleted_count": before - len(self.docs)})()


class _AsyncCollection:
    """Async adapter over FakeCollection for the async accessors."""

    def __init__(self, sync):
        self._s = sync

    async def find_one(self, *args, **kwargs):
        return self._s.find_one(*args, **kwargs)

    def find(self, *args, **kwargs):
        return self._s.find(*args, **kwargs)

    async def insert_one(self, *args, **kwargs):
        return self._s.insert_one(*args, **kwargs)

    async def update_one(self, *args, **kwargs):
        return self._s.update_one(*args, **kwargs)

    async def delete_one(self, *args, **kwargs):
        return self._s.delete_one(*args, **kwargs)


class FakeDb:
    def __init__(self, collections=None):
        self._cols = {
            k: (v if isinstance(v, FakeCollection) else FakeCollection(k, v))
            for k, v in (collections or {}).items()
        }

    def _get(self, name):
        return self._cols.setdefault(name, FakeCollection(name))

    def __getitem__(self, name):
        return self._get(name)

    @property
    def async_view(self):
        return _AsyncDbView(self)

    @property
    def sync_view(self):
        return _SyncDbView(self)


class _AsyncDbView:
    """``db[collection]`` over the async adapter."""

    def __init__(self, owner):
        self._owner = owner

    def __getitem__(self, name):
        return _AsyncCollection(self._owner._get(name))


class _SyncDbView:
    """``db[collection]`` with plain sync methods."""

    def __init__(self, owner):
        self._owner = owner

    def __getitem__(self, name):
        return self._owner._get(name)


def _patch_db(monkeypatch, collections=None):
    db = FakeDb(collections or {})
    monkeypatch.setattr(sa, "get_async_db", lambda: db.async_view)
    monkeypatch.setattr(sa, "get_sync_db", lambda: db.sync_view)
    return db


# ── registry: validation & coercion ─────────────────────────────────────────

def test_validate_coerces_scalar_types():
    assert R.validate_value(R.SPEC_BY_KEY["branding.white_label"], "true") == (True, None)
    assert R.validate_value(R.SPEC_BY_KEY["branding.white_label"], 1) == (True, None)
    assert R.validate_value(R.SPEC_BY_KEY["ai.temperature"], "0.7") == (0.7, None)
    assert R.validate_value(R.SPEC_BY_KEY["limits.min_comments"], "5") == (5, None)
    assert R.validate_value(R.SPEC_BY_KEY["branding.font"], "inter") == ("inter", None)
    assert R.validate_value(R.SPEC_BY_KEY["branding.colors.primary"], "#7c5cff") == ("#7c5cff", None)


def test_validate_rejects_bad_values():
    assert R.validate_value(R.SPEC_BY_KEY["branding.colors.primary"], "red")[1]
    assert R.validate_value(R.SPEC_BY_KEY["branding.colors.primary"], "#12")[1]
    assert R.validate_value(R.SPEC_BY_KEY["general.company.website"], "not-a-url")[1]
    assert R.validate_value(R.SPEC_BY_KEY["general.contact.support_email"], "nope")[1]
    assert R.validate_value(R.SPEC_BY_KEY["branding.font"], "comic-sans")[1]
    assert R.validate_value(R.SPEC_BY_KEY["limits.min_comments"], -3)[1]
    assert R.validate_value(R.SPEC_BY_KEY["limits.min_comments"], 5)[0] == 5


def test_validate_path_accepts_static_and_url():
    spec = R.SPEC_BY_KEY["branding.logo_primary"]
    assert R.validate_value(spec, "/static/uploads/branding/x.png")[1] is None
    assert R.validate_value(spec, "https://cdn.example.com/logo.png")[1] is None
    assert R.validate_value(spec, "C:\\logo.png")[1]


def test_validate_nav_overrides_json_shape():
    spec = R.SPEC_BY_KEY["appearance.nav_overrides"]
    good = {"usage": {"label": "Billing", "order": 1}, "ai": {"hidden": True}}
    cleaned, err = R.validate_value(spec, good)
    assert err is None and cleaned == good
    # unknown sub-key
    _, err = R.validate_value(spec, {"usage": {"bogus": 1}})
    assert err and "bogus" in err
    # wrong sub-value type
    _, err = R.validate_value(spec, {"usage": {"order": "first"}})
    assert err and "number" in err
    # entry must be an object
    _, err = R.validate_value(spec, {"usage": 5})
    assert err
    # invalid JSON string
    _, err = R.validate_value(spec, "{not json")
    assert err and "Invalid JSON" in err


def test_validate_patch_keeps_good_skips_bad_and_unknown():
    clean, errors = R.validate_patch({
        "branding.font": "roboto",
        "branding.colors.primary": "zzz",
        "no.such.key": 1,
        "maintenance.enabled": True,
    })
    assert clean == {"branding.font": "roboto", "maintenance.enabled": True}
    assert set(errors) == {"branding.colors.primary", "no.such.key"}


def test_registry_complete_and_secret_free():
    assert len(R.SETTINGS) == len(R.REGISTERED_KEYS) == len(R.SPEC_BY_KEY)
    for spec in R.SETTINGS:
        assert spec.key in sa.SETTING_DEFAULTS, spec.key
        assert spec.default == sa.SETTING_DEFAULTS[spec.key]
    for banned in ("apify.token", "security.session_epoch"):
        assert banned not in R.REGISTERED_KEYS
    assert not any(k.startswith("actor.") for k in R.REGISTERED_KEYS)
    assert not any("token" in k for k in R.REGISTERED_KEYS)


def test_schema_categories_shape():
    cats = R.schema_categories()
    assert [c["id"] for c in cats] == [c["id"] for c in R.CATEGORIES]
    assert len(cats) == 12
    for cat in cats:
        assert cat["groups"], cat["id"]
        for group in cat["groups"]:
            for spec in group["specs"]:
                assert spec["key"] and spec["type"] and spec["label"]


# ── public config ───────────────────────────────────────────────────────────

def test_public_config_defaults_when_unset():
    cfg = asyncio.run(R.build_public_config(lambda key: None))
    assert cfg["app"]["name"] == "LeadAI"
    assert cfg["branding"]["colors"]["primary"] == "#7c5cff"
    assert cfg["maintenance"]["enabled"] is False
    assert cfg["features"]["url_search"] is True
    assert cfg["defaults"]["comment_filter_mode"] == "all"
    assert cfg["appearance"]["nav_overrides"] == {}


def test_public_config_reflects_overrides():
    overrides = {
        "general.app.name": "ScoutAI",
        "branding.colors.primary": "#123456",
        "maintenance.enabled": True,
        "maintenance.message": "Be right back",
    }
    cfg = asyncio.run(R.build_public_config(lambda key: overrides.get(key)))
    assert cfg["app"]["name"] == "ScoutAI"
    assert cfg["branding"]["colors"]["primary"] == "#123456"
    assert cfg["maintenance"]["enabled"] is True
    assert cfg["maintenance"]["message"] == "Be right back"


def test_public_config_accepts_async_getter():
    async def get(key):
        return {"general.app.name": "AsyncAI"}.get(key)
    cfg = asyncio.run(R.build_public_config(get))
    assert cfg["app"]["name"] == "AsyncAI"


def test_public_config_never_leaks_secrets():
    cfg = asyncio.run(R.build_public_config(lambda key: None))
    text = repr(cfg).lower()
    for banned in ("token", "password", "secret", "apikey", "api_key"):
        assert banned not in text


# ── import preview sanitization (DB-free endpoint) ─────────────────────────

def test_import_preview_counts_valid_and_invalid():
    res = asyncio.run(settings_api.import_preview({
        "payload": {"settings": {
            "branding.font": "manrope",
            "branding.colors.primary": "not-a-color",
            "unknown.key": 1,
            "limits.min_comments": 42,
        }}
    }))
    assert res["valid"] is False
    assert res["count"] == 2
    assert set(res["invalid"]) == {"branding.colors.primary", "unknown.key"}
    assert set(res["valid_keys"]) == {"branding.font", "limits.min_comments"}


# ── settings service + revision history ─────────────────────────────────────

def test_revision_flow_with_fake_db(monkeypatch):
    _patch_db(monkeypatch, {"settings_history": [
        {"version": 1, "snapshot": {}, "changed": {}, "changed_by": "a",
         "reason": "r", "created_at": 1.0},
    ]})

    async def run():
        assert await sa.current_revision() == 1
        v = await sa.push_revision({"branding.font": "x"}, {"branding.font": {}},
                                   by="admin", reason="test")
        assert v == 2
        assert await sa.current_revision() == 2
        entries = await sa.list_history(10)
        assert len(entries) == 2
        assert all(e["snapshot"] is None for e in entries)
        doc = await sa.get_history_version(2)
        assert doc["version"] == 2 and doc["reason"] == "test"
        assert await sa.get_history_version(99) is None
    asyncio.run(run())


def test_aset_adelete_setting_roundtrip(monkeypatch):
    db = _patch_db(monkeypatch)

    async def run():
        assert await sa.aset_setting("branding.font", "roboto", by="tester") is True
        doc = db.sync_view[sa.COLLECTION].find_one({"_id": "branding.font"})
        assert doc["value"] == "roboto" and doc["updated_by"] == "tester"
        assert await sa.aget_setting("branding.font") == "roboto"
        await sa.adelete_setting("branding.font")
        assert db.sync_view[sa.COLLECTION].find_one({"_id": "branding.font"}) is None
        assert await sa.aget_setting("branding.font") == sa.SETTING_DEFAULTS["branding.font"]
    asyncio.run(run())


def test_apply_snapshot_restores_and_revisions(monkeypatch):
    _patch_db(monkeypatch, {"system_settings": [
        {"_id": "branding.font", "value": "comic-sans", "updated_at": 0.0},
    ]})

    async def run():
        version, applied = await sa.apply_snapshot(
            {"branding.font": "inter", "no.such.key": 1},
            by="admin", reason="restore")
        assert applied == ["branding.font"]
        assert version == 1
        assert await sa.aget_setting("branding.font") == "inter"
        assert await sa.current_revision() == 1
        # second restore of identical snapshot applies nothing
        version, applied = await sa.apply_snapshot({"branding.font": "inter"})
        assert applied == [] and version == 0
    asyncio.run(run())


def test_history_router_strips_objectid_and_snapshot(monkeypatch):
    async def fake_list(limit):
        return [{
            "_id": ObjectId(),
            "version": 7,
            "snapshot": {"branding.font": "x"},
            "changed": {"branding.font": {"old": "a", "new": "b"}},
            "changed_by": "admin@x.io", "ip": "1.2.3.4",
            "reason": "test", "created_at": 100.0,
        }]
    monkeypatch.setattr(settings_api.s, "list_history", fake_list)

    res = asyncio.run(settings_api.history(10))
    assert len(res["versions"]) == 1
    entry = res["versions"][0]
    assert "_id" not in entry and "snapshot" not in entry
    assert entry["version"] == 7 and entry["reason"] == "test"


def test_export_payload_shape(monkeypatch):
    db = _patch_db(monkeypatch, {"system_settings": [
        {"_id": "branding.font", "value": "manrope"},
    ]})
    payload = sa.export_payload()
    assert payload["schema"] == "leadai.settings.v1"
    assert payload["settings"]["branding.font"] == "manrope"
    assert "apify.token" not in payload["settings"]
    assert set(payload["settings"]) == set(R.REGISTERED_KEYS)


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        try:
            fn(type("M", (), {"setattr": lambda self, obj, name, val: setattr(obj, name, val)})())
            print(f"PASS {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:
            print(f"ERROR {fn.__name__}: {e!r}")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
