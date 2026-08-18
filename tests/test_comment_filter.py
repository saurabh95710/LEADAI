"""
Tests for the Comment Scraping & Keyword Intelligence layer:
text/keyword normalization, matching semantics, rule evaluation, inline
rules from run configs, rule resolution, and result persistence.

The engine is pure Python + dicts (no DB), so these tests run without
MongoDB; the DB-touching helpers use a minimal fake.
"""
from bson import ObjectId

from app.pipeline import comment_filter as cf


# ── fake database (subscript + attribute access, like motor/pymongo) ─────

def _match(doc, key, value):
    if isinstance(value, dict) and "$ne" in value:
        return doc.get(key, value["$ne"]) != value["$ne"]
    return doc.get(key) == value


class FakeCollection:
    def __init__(self, name, docs=None):
        self.name = name
        self.docs = list(docs or [])
        self.update_calls = []

    def find_one(self, query):
        for doc in self.docs:
            if all(_match(doc, k, v) for k, v in (query or {}).items()):
                return doc
        return None

    def find(self, query=None, projection=None):
        query = query or {}
        return [doc for doc in self.docs
                if all(_match(doc, k, v) for k, v in query.items())]

    def update_one(self, query, update, upsert=False):
        self.update_calls.append((query, update))
        return None

    def sort(self, *args):
        return self.docs


class FakeDb:
    def __init__(self, collections=None):
        self._cols = {
            k: (v if isinstance(v, FakeCollection) else FakeCollection(k, v))
            for k, v in (collections or {}).items()
        }

    def __getitem__(self, name):
        return self._cols.setdefault(name, FakeCollection(name))

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self._cols.setdefault(name, FakeCollection(name))


def _rule(**over):
    rule = {
        "_id": ObjectId(),
        "name": "Test rule",
        "categories": [],
        "include_keywords": ["price"],
        "exclude_keywords": [],
        "match_mode": "any",
        "groups": [],
        "detect_contacts": False,
    }
    rule.update(over)
    return rule


# ── normalization ──────────────────────────────────────────────────────────

def test_normalize_text_case_and_punctuation():
    assert cf.normalize_text("  FLAT 3-BHK, Mohali! ") == "flat 3 bhk mohali"
    assert cf.normalize_text("Pricing?? 12% off (today)") == "pricing 12 off today"
    assert cf.normalize_text("") == ""
    assert cf.normalize_text(None) == ""


def test_normalize_keyword_list_handles_string_and_list():
    assert cf.normalize_keyword_list("Flat, 3-BHK, flat") == ["flat", "3 bhk"]
    assert cf.normalize_keyword_list(["Price", "  price ", "EMI"]) == ["price", "emi"]
    assert cf.normalize_keyword_list(None) == []
    assert cf.normalize_keyword_list("") == []


def test_keyword_matches_phrase_substring():
    assert cf.keyword_matches("site visit in kharar", "site visit")
    assert not cf.keyword_matches("visit site", "site visit")


def test_keyword_matches_short_word_boundary():
    # "car" (<4 chars) must never match inside "career"
    assert cf.keyword_matches("car price", "car")
    assert not cf.keyword_matches("career guidance", "car")


def test_keyword_matches_long_prefix():
    # >=4 chars: start-boundary prefix — "price" matches "prices"/"pricey",
    # but not "pricing" (p-r-i-c-i-ng) and never mid-word ("resprice")
    assert cf.keyword_matches("flat prices", "price")
    assert cf.keyword_matches("pricey option", "price")
    assert not cf.keyword_matches("pricing details", "price")
    assert not cf.keyword_matches("myresprice is high", "price")


def test_keyword_matches_devnagari():
    assert cf.keyword_matches("खरड़ में फ्लैट की कीमत", "खरड़")


# ── rule evaluation ────────────────────────────────────────────────────────

def test_empty_rule_is_no_filter():
    res = cf.evaluate_rule("anything at all", {})
    assert res["status"] == cf.STATUS_NO_FILTER
    assert res["filter_score"] == 100

    res = cf.evaluate_rule("anything at all", None)
    assert res["status"] == cf.STATUS_NO_FILTER


def test_rule_with_no_config_is_no_filter():
    res = cf.evaluate_rule("hello", {"name": "empty", "categories": [],
                                     "include_keywords": []})
    assert res["status"] == cf.STATUS_NO_FILTER


def test_any_mode_match_and_miss():
    res = cf.evaluate_rule("what is the flat price?", _rule())
    assert res["status"] == cf.STATUS_MATCHED
    assert res["matched_keywords"] == ["price"]
    assert res["filter_score"] == 100

    res = cf.evaluate_rule("nice post!", _rule())
    assert res["status"] == cf.STATUS_NOT_MATCHED
    assert res["filter_score"] == 0


def test_all_mode_requires_every_keyword():
    rule = _rule(include_keywords=["price", "flat"], match_mode="all")
    res = cf.evaluate_rule("flat price please", rule)
    assert res["status"] == cf.STATUS_MATCHED

    res = cf.evaluate_rule("just price?", rule)
    assert res["status"] == cf.STATUS_NOT_MATCHED


def test_exclude_keywords_veto_everything():
    rule = _rule(include_keywords=["price"],
                 exclude_keywords=["job", "vacancy"])
    res = cf.evaluate_rule("price details job vacancy", rule)
    assert res["status"] == cf.STATUS_NOT_MATCHED
    assert res["excluded_keywords"] == ["job", "vacancy"]

    res = cf.evaluate_rule("price details", rule)
    assert res["status"] == cf.STATUS_MATCHED


def test_empty_comment_never_matches():
    res = cf.evaluate_rule("   ", _rule())
    assert res["status"] == cf.STATUS_NOT_MATCHED


def test_builtin_category_expands_keywords():
    rule = _rule(include_keywords=[], categories=["real_estate"])
    res = cf.evaluate_rule("send me the site visit timing", rule)
    assert res["status"] == cf.STATUS_MATCHED
    assert "site visit" in res["matched_keywords"]
    assert "real_estate" in res["matched_categories"]


def test_custom_category_expands_keywords():
    custom = [{"_id": ObjectId(), "name": "Gym", "keywords": ["gym", "pt"]}]
    rule = _rule(include_keywords=[], categories=[str(custom[0]["_id"])])
    res = cf.evaluate_rule("gym membership price", rule, custom)
    assert res["status"] == cf.STATUS_MATCHED
    assert "gym" in res["matched_keywords"]
    assert str(custom[0]["_id"]) in res["matched_categories"]


def test_contact_signal_preset_matches_phone_number():
    rule = _rule(include_keywords=[], categories=["contact_signals"])
    res = cf.evaluate_rule("please call 9876543210", rule)
    assert res["status"] == cf.STATUS_MATCHED
    assert res.get("contact_signal") is True


def test_contact_signal_preset_matches_email():
    rule = _rule(include_keywords=[], categories=["contact_signals"])
    res = cf.evaluate_rule("mail me at buyer@example.com", rule)
    assert res["status"] == cf.STATUS_MATCHED


def test_advanced_groups_or_and():
    rule = _rule(include_keywords=[], match_mode="advanced",
                 group_operator="or",
                 groups=[["price", "flat"], ["site visit"]])
    res = cf.evaluate_rule("site visit booked", rule)
    assert res["status"] == cf.STATUS_MATCHED

    rule["group_operator"] = "and"
    res = cf.evaluate_rule("site visit booked", rule)
    assert res["status"] == cf.STATUS_NOT_MATCHED

    res = cf.evaluate_rule("flat price and site visit", rule)
    assert res["status"] == cf.STATUS_MATCHED


# ── rule payload / config ──────────────────────────────────────────────────

def test_normalize_rule_payload_sanitizes():
    payload = cf.normalize_rule_payload({
        "name": "  Real estate leads  ",
        "categories": "real_estate, real_estate",
        "include_keywords": "Flat, 3-BHK",
        "exclude_keywords": ["job"],
        "match_mode": "weird",
        "groups": [["a", "b"], [], "nope", ["c"]],
        "detect_contacts": "yes",
    })
    assert payload["name"] == "Real estate leads"
    assert payload["categories"] == ["real_estate"]
    assert payload["include_keywords"] == ["flat", "3 bhk"]
    assert payload["exclude_keywords"] == ["job"]
    assert payload["match_mode"] == "any"
    assert payload["groups"] == [["a", "b"], ["c"]]
    assert payload["detect_contacts"] is True


def test_rule_is_empty():
    assert cf.rule_is_empty({"name": "x", "categories": [], "include_keywords": []})
    assert not cf.rule_is_empty(_rule())


# ── inline rules from run configs ──────────────────────────────────────────

def test_build_inline_rule_from_preset():
    rule = cf.build_inline_rule({"mode": "preset", "preset": "contact_signals"})
    assert rule is not None
    assert "phone" in rule["include_keywords"]
    assert rule["detect_contacts"] is True
    assert rule["_id"].startswith("inline:")


def test_build_inline_rule_from_custom_keywords():
    rule = cf.build_inline_rule({"mode": "custom",
                                 "include_keywords": "Flat, Price",
                                 "match_mode": "all"})
    assert rule is not None
    assert rule["include_keywords"] == ["flat", "price"]
    assert rule["match_mode"] == "all"


def test_build_inline_rule_expands_category():
    rule = cf.build_inline_rule({"categories": ["real_estate"]})
    assert "site visit" in rule["include_keywords"]
    assert rule["categories"] == ["real_estate"]


def test_build_inline_rule_empty_is_none():
    assert cf.build_inline_rule(None) is None
    assert cf.build_inline_rule({}) is None
    assert cf.build_inline_rule({"mode": "all"}) is None


def test_build_inline_rule_exclude_only_still_filters():
    rule = cf.build_inline_rule({"exclude_keywords": "job, vacancy"})
    assert rule is not None
    assert rule["exclude_keywords"] == ["job", "vacancy"]


# ── rule resolution ────────────────────────────────────────────────────────

def test_resolve_rule_id_wins_over_active():
    stored = {**_rule(), "_id": ObjectId()}
    active = {**_rule(name="Active"), "_id": ObjectId()}
    db = FakeDb({cf.RULES_COLLECTION: [stored, active]})
    run = {"comment_filter": {"mode": "preset", "rule_id": str(stored["_id"])}}
    assert cf.resolve_effective_rule(db, run)["_id"] == stored["_id"]


def test_resolve_inline_config_when_no_rule_ref():
    db = FakeDb({cf.RULES_COLLECTION: []})
    run = {"comment_filter": {"mode": "custom",
                              "include_keywords": "gym, pt"}}
    rule = cf.resolve_effective_rule(db, run)
    assert rule is not None
    assert rule["include_keywords"] == ["gym", "pt"]
    assert rule["_id"].startswith("inline:")


def test_resolve_falls_back_to_active_rule():
    active = {**_rule(name="Active", active=True), "_id": ObjectId()}
    db = FakeDb({cf.RULES_COLLECTION: [active]})
    assert cf.resolve_effective_rule(db, {"comment_filter": None}) == active
    assert cf.resolve_effective_rule(db, None) == active


def test_resolve_no_rule_is_none():
    db = FakeDb({cf.RULES_COLLECTION: []})
    assert cf.resolve_effective_rule(db, {"comment_filter": None}) is None


def test_resolve_missing_rule_id_falls_back():
    active = {**_rule(name="Active", active=True), "_id": ObjectId()}
    db = FakeDb({cf.RULES_COLLECTION: [active]})
    run = {"comment_filter": {"mode": "preset", "rule_id": "000000000000000000000000"}}
    assert cf.resolve_effective_rule(db, run) == active


# ── persistence + per-post filtering ───────────────────────────────────────

def test_store_filter_result_persists():
    cid = ObjectId()
    db = FakeDb({"comment_filter_results": [],
                 "facebook_comments": [{"_id": cid, "text": "hi"}]})
    res = cf.evaluate_rule("flat price", _rule())
    cf.store_filter_result(db, str(cid), res, _rule(), post_ref="p1",
                           search_run_id="r1", platform="facebook")
    upsert = db[cf.RESULTS_COLLECTION].update_calls[0]
    assert upsert[0] == {"comment_id": str(cid)}
    assert upsert[1]["$set"]["status"] == cf.STATUS_MATCHED
    assert db.facebook_comments.update_calls[0][1]["$set"][
        "keyword_filter_status"] == cf.STATUS_MATCHED


def test_filter_comments_for_post_summary():
    comments = [{"_id": ObjectId(), "post_ref": "post-1", "text": "flat price?"},
                {"_id": ObjectId(), "post_ref": "post-1", "text": "hello there"},
                {"_id": ObjectId(), "post_ref": "post-1", "text": "job vacancy please"}]
    db = FakeDb({"facebook_comments": comments,
                 "comment_filter_results": []})
    rule = _rule(exclude_keywords=["job", "vacancy"])
    summary = cf.filter_comments_for_post(db, "post-1", rule,
                                          search_run_id="r1")
    assert summary["total"] == 3
    assert summary["matched"] == 1
    assert summary["not_matched"] == 2
    assert summary["matched_refs"] == [str(comments[0]["_id"])]
    assert summary["rule_id"] == str(rule["_id"])
    assert len(db[cf.RESULTS_COLLECTION].update_calls) == 3


def test_filter_comments_no_filter_returns_all():
    comments = [{"_id": ObjectId(), "post_ref": "post-1", "text": "a"},
                {"_id": ObjectId(), "post_ref": "post-1", "text": "b"}]
    db = FakeDb({"facebook_comments": comments})
    summary = cf.filter_comments_for_post(db, "post-1", {})
    assert summary["total"] == 2
    assert summary["matched_refs"] == [str(c["_id"]) for c in comments]


def test_apply_filter_to_comment_single():
    cid = ObjectId()
    db = FakeDb({"facebook_comments": [{"_id": cid, "text": "price?"}]})
    res = cf.apply_filter_to_comment(db, {"_id": cid, "text": "price?"},
                                     _rule(), post_ref="post-1")
    assert res["status"] == cf.STATUS_MATCHED


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:
            print(f"ERROR {fn.__name__}: {e!r}")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)