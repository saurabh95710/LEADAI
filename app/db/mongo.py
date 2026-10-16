"""
Async motor client for FastAPI handlers; sync pymongo for background
agents (threads). `ensure_indexes` is called once at startup and never
crashes the server when Mongo is temporarily unreachable.
"""
import logging
import threading
import time
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient, ASCENDING, DESCENDING
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def _configure_dns():
    """Point dnspython at public resolvers so mongodb+srv:// SRV lookups
    don't hang when the system DNS is unreliable (e.g. local routers that
    time out on SRV queries)."""
    try:
        from dns import resolver, asyncresolver
        custom = resolver.Resolver(configure=True)
        custom_async = asyncresolver.Resolver(configure=True)
        servers = [s.strip() for s in settings.dns_servers.split(",") if s.strip()]
        # Put configured public servers FIRST so they take precedence over slow/failing local router DNS
        custom.nameservers = servers + [s for s in custom.nameservers if s not in servers]
        custom_async.nameservers = servers + [s for s in custom_async.nameservers if s not in servers]
        custom.timeout = 3.0
        custom.lifetime = 6.0
        custom_async.timeout = 3.0
        custom_async.lifetime = 6.0
        resolver.default_resolver = custom
        asyncresolver.default_resolver = custom_async
        logger.info(f"DNS resolver configured with nameservers: {custom.nameservers}")
    except Exception as e:
        logger.warning(f"Failed to override DNS resolver: {e}")


_configure_dns()


_async_client_cache: Optional[AsyncIOMotorClient] = None


def get_async_client() -> Optional[AsyncIOMotorClient]:
    global _async_client_cache
    if _async_client_cache is not None:
        try:
            import asyncio
            current_loop = asyncio.get_running_loop()
            client_loop = getattr(_async_client_cache, "get_io_loop", lambda: None)()
            if client_loop is not None and client_loop != current_loop:
                _async_client_cache = None
        except Exception:
            pass
    if _async_client_cache is not None:
        return _async_client_cache
    try:
        _async_client_cache = AsyncIOMotorClient(
            settings.mongo_uri,
            serverSelectionTimeoutMS=3000,
            connectTimeoutMS=5000,
            socketTimeoutMS=10000,
            maxPoolSize=50,
            minPoolSize=5,
            maxIdleTimeMS=45000,
        )
        return _async_client_cache
    except Exception as e:
        logger.warning(f"Async Motor client connection warning: {e}")
        _async_client_cache = None
        return None


def get_async_db():
    try:
        client = get_async_client()
        return client[settings.mongo_db_name] if client is not None else None
    except Exception as e:
        logger.warning(f"Async DB connection failed: {e}")
        return None


def reset_client_caches():
    """Clear cached clients so the next call creates fresh connections."""
    global _async_client_cache, _sync_client_cache
    _async_client_cache = None
    _sync_client_cache = None


_sync_client_cache: Optional[MongoClient] = None
_sync_client_created: float = 0.0
# Tracks consecutive sync DB failures to back off log noise
_sync_fail_count: int = 0
# A new client reports every server as Unknown until its first check, and
# under load that can take a while: only a client that has stayed unreachable
# this long is replaced.
_UNREACHABLE_GRACE_S = 30.0


def get_sync_client() -> Optional[MongoClient]:
    """Return (or create) the cached sync MongoClient.

    If the cached client's topology is in a broken state (all servers
    unknown after a network blip), the cache is cleared so the next call
    creates a fresh connection rather than returning a permanently broken
    client that would block every request for the full serverSelectionTimeoutMS.
    """
    global _sync_client_cache, _sync_fail_count, _sync_client_created
    if _sync_client_cache is not None and time.monotonic() - _sync_client_created > _UNREACHABLE_GRACE_S:
        # Detect a stale/broken client: all servers are Unknown (no primary).
        try:
            td = _sync_client_cache.topology_description
            all_unknown = all(
                sd.server_type_name == "Unknown"
                for sd in td.server_descriptions().values()
            ) if td.server_descriptions() else False
            if all_unknown:
                logger.warning("Sync MongoClient topology has no reachable servers "
                               "— resetting client cache for reconnect")
                stale = _sync_client_cache
                _sync_client_cache = None
                # other threads may still be using it: close it once their
                # operations are over (socket timeout is 10 s), never right away
                timer = threading.Timer(60.0, _close_quietly, args=(stale,))
                timer.daemon = True
                timer.start()
        except Exception:
            pass  # topology introspection not critical
    if _sync_client_cache is not None:
        return _sync_client_cache
    try:
        _sync_client_cache = MongoClient(
            settings.mongo_uri,
            # 3 s is short enough that portal loads fail fast rather than
            # hanging for 10 s; long enough not to trip on brief latency spikes.
            serverSelectionTimeoutMS=3000,
            connectTimeoutMS=5000,
            socketTimeoutMS=10000,
            maxPoolSize=50,
            minPoolSize=5,
            maxIdleTimeMS=45000,
        )
        _sync_client_created = time.monotonic()
        _sync_fail_count = 0
        return _sync_client_cache
    except Exception as e:
        _sync_fail_count += 1
        logger.warning(f"Sync MongoClient connection warning: {e}")
        _sync_client_cache = None
        return None


def _close_quietly(client: MongoClient) -> None:
    try:
        client.close()
    except Exception:
        pass


def get_sync_db():
    try:
        client = get_sync_client()
        return client[settings.mongo_db_name] if client is not None else None
    except Exception as e:
        logger.warning(f"Sync DB connection failed: {e}")
        return None


def _drop_index_if_exists(collection, name: str, only_if_unique: bool = False):
    """Drop a legacy index if present (e.g. the old global-unique indexes).
    ``only_if_unique`` keeps a same-named non-unique replacement, so it is
    not dropped and rebuilt on every startup."""
    try:
        if only_if_unique and not (collection.index_information().get(name) or {}).get("unique"):
            return
        collection.drop_index(name)
        logger.info(f"Dropped legacy index {name}")
    except Exception:
        pass


def _create_index_safe(collection, keys, **kwargs):
    """Create an index, handling IndexKeySpecsConflict by dropping and recreating.

    This makes index initialization idempotent even when an existing index
    has the same name but different key specifications (e.g. sparse vs non-sparse).
    """
    try:
        collection.create_index(keys, **kwargs)
    except Exception as e:
        error_msg = str(e)
        if "IndexKeySpecsConflict" in error_msg or "index already exists" in error_msg.lower():
            # Extract index name from the error or generate from keys
            name = kwargs.get("name")
            if not name:
                # Generate default name from key spec
                parts = []
                for k, v in keys:
                    direction = {1: "1", -1: "1", "2d": "2dsphere", "text": "text"}.get(v, str(v))
                    parts.append(f"{k}_{direction}")
                name = "_".join(parts)
            try:
                collection.drop_index(name)
                logger.info(f"Dropped conflicting index {name} on {collection.name} for recreation")
                collection.create_index(keys, **kwargs)
            except Exception as retry_err:
                logger.warning(f"Could not recreate index {name} on {collection.name}: {retry_err}")
        else:
            raise


def ensure_indexes():
    try:
        db = get_sync_db()
        if db is None:
            logger.warning("Skipping MongoDB index creation: Database client unavailable.")
            return

        # Pages/posts/comments are deduped PER search run now: the same real
        # page may belong to several runs, so the old global-unique indexes
        # must go. Uniqueness is per {entity, run-owner} instead.
        _drop_index_if_exists(db.facebook_pages, "facebook_url_1")
        _create_index_safe(db.facebook_pages,
            [("facebook_url", ASCENDING), ("search_run_id", ASCENDING)], unique=True)
        _create_index_safe(db.facebook_pages, [("search_run_id", ASCENDING)])
        _create_index_safe(db.facebook_pages, [("category", ASCENDING)])
        _create_index_safe(db.facebook_pages, [("city", ASCENDING)])
        _create_index_safe(db.facebook_pages, [("state", ASCENDING)])
        _create_index_safe(db.facebook_pages, [("posts_status", ASCENDING)])
        _create_index_safe(db.facebook_pages, [("platform", ASCENDING)])
        _create_index_safe(db.facebook_pages, [("created_at", ASCENDING)])
        _create_index_safe(db.facebook_pages, [("keyword_filter_status", ASCENDING)])

        _drop_index_if_exists(db.facebook_posts, "post_url_1")
        _create_index_safe(db.facebook_posts,
            [("post_url", ASCENDING), ("page_ref", ASCENDING)], unique=True, sparse=True)
        _create_index_safe(db.facebook_posts, [("page_id", ASCENDING)])
        _create_index_safe(db.facebook_posts, [("page_ref", ASCENDING)])
        _create_index_safe(db.facebook_posts, [("platform", ASCENDING)])
        _create_index_safe(db.facebook_posts, [("search_run_id", ASCENDING)])
        _create_index_safe(db.facebook_posts, [("created_at", ASCENDING)])

        _drop_index_if_exists(db.facebook_comments, "comment_url_1", only_if_unique=True)
        _drop_index_if_exists(db.facebook_comments, "comment_url_1_post_ref_1")
        _create_index_safe(db.facebook_comments, [("comment_url", ASCENDING)])
        _create_index_safe(db.facebook_comments, [("comment_id", ASCENDING)])
        _create_index_safe(db.facebook_comments, [("post_ref", ASCENDING)])
        _create_index_safe(db.facebook_comments, [("search_run_id", ASCENDING)])
        _create_index_safe(db.facebook_comments, [("platform", ASCENDING)])
        _create_index_safe(db.facebook_comments, [("keyword_filter_status", ASCENDING)])
        _create_index_safe(db.facebook_comments, [("created_at", ASCENDING)])

        # ── Platform-Neutral Social Collections (Phase 3) ───────────────────
        _create_index_safe(db.social_pages,
            [("page_url", ASCENDING), ("search_run_id", ASCENDING)], unique=True, sparse=True)
        _create_index_safe(db.social_pages,
            [("facebook_url", ASCENDING), ("search_run_id", ASCENDING)], sparse=True)
        _create_index_safe(db.social_pages, [("search_run_id", ASCENDING)])
        _create_index_safe(db.social_pages, [("category", ASCENDING)])
        _create_index_safe(db.social_pages, [("platform", ASCENDING)])
        _create_index_safe(db.social_pages, [("created_at", ASCENDING)])
        _create_index_safe(db.social_pages, [("organization_id", ASCENDING), ("created_at", ASCENDING)])
        _create_index_safe(db.social_pages, [("organization_id", ASCENDING), ("page_url", ASCENDING)])

        _create_index_safe(db.social_posts,
            [("post_url", ASCENDING), ("page_ref", ASCENDING)], unique=True, sparse=True)
        _create_index_safe(db.social_posts, [("page_ref", ASCENDING)])
        _create_index_safe(db.social_posts, [("platform", ASCENDING)])
        _create_index_safe(db.social_posts, [("search_run_id", ASCENDING)])
        _create_index_safe(db.social_posts, [("created_at", ASCENDING)])
        _create_index_safe(db.social_posts, [("organization_id", ASCENDING), ("page_ref", ASCENDING)])

        _create_index_safe(db.social_comments, [("comment_url", ASCENDING)])
        _create_index_safe(db.social_comments, [("post_ref", ASCENDING)])
        _create_index_safe(db.social_comments, [("search_run_id", ASCENDING)])
        _create_index_safe(db.social_comments, [("platform", ASCENDING)])
        _create_index_safe(db.social_comments, [("created_at", ASCENDING)])
        _create_index_safe(db.social_comments, [("organization_id", ASCENDING), ("post_ref", ASCENDING)])

        _create_index_safe(db.ai_comments, [("comment_ref", ASCENDING)], unique=True)
        _create_index_safe(db.ai_comments, [("is_lead", ASCENDING)])
        _create_index_safe(db.ai_comments, [("lead_score", ASCENDING)])
        _create_index_safe(db.ai_comments, [("platform", ASCENDING)])
        _create_index_safe(db.ai_comments, [("analyzed_at", ASCENDING)])
        _create_index_safe(db.ai_comments, [("is_lead", ASCENDING), ("lead_score", ASCENDING)])
        _create_index_safe(db.ai_comments, [("lead_status", ASCENDING)])
        _create_index_safe(db.ai_comments, [("lead_priority", ASCENDING)])
        _create_index_safe(db.ai_comments, [("is_lead", ASCENDING), ("lead_status", ASCENDING)])
        _create_index_safe(db.ai_comments, [("is_lead", ASCENDING), ("lead_priority", ASCENDING)])
        _create_index_safe(db.ai_comments, [("assigned_to", ASCENDING)])
        _create_index_safe(db.ai_comments, [("lead_updated_at", ASCENDING)])

        _create_index_safe(db.search_history, [("run_id", ASCENDING)], unique=True)
        _create_index_safe(db.search_history, [("created_at", ASCENDING)])
        _create_index_safe(db.search_history, [("status", ASCENDING)])
        _create_index_safe(db.search_history, [("platform", ASCENDING)])
        _create_index_safe(db.search_history, [("created_by", ASCENDING)])

        # Admin panel collections (_id index is implicit; unique keys enforced below)
        _create_index_safe(db.admin_users, [("email", ASCENDING)], unique=True)
        _create_index_safe(db.audit_logs, [("category", ASCENDING)])
        _create_index_safe(db.audit_logs, [("action", ASCENDING), ("at", DESCENDING)])
        # Retention (TTL) on `at` — AUDIT_RETENTION_DAYS (default 365). The TTL
        # index also serves time-range queries, so no separate plain `at` index
        # (two indexes on the same key conflict and one silently failed before).
        _drop_index_if_exists(db.audit_logs, "at_1")
        _drop_index_if_exists(db.audit_logs, "at_ttl_90d")
        try:
            import os as _os
            retention_days = int(_os.environ.get("AUDIT_RETENTION_DAYS", "365"))
        except ValueError:
            retention_days = 365
        _create_index_safe(db.audit_logs, [("at", ASCENDING)],
                           expireAfterSeconds=max(30, retention_days) * 24 * 3600,
                           name="at_ttl")

        # Global Settings — versioned revisions for rollback
        _create_index_safe(db.settings_history, [("version", ASCENDING)], unique=True)
        _create_index_safe(db.settings_history, [("created_at", ASCENDING)])

        # ── SaaS Multi-Tenant Platform Collections ─────────────────────────────
        _create_index_safe(db.organizations, [("slug", ASCENDING)], unique=True)
        _create_index_safe(db.organizations, [("status", ASCENDING)])
        _create_index_safe(db.organizations, [("created_at", ASCENDING)])
        _create_index_safe(db.organizations, [("owner_id", ASCENDING)])

        _create_index_safe(db.organization_members,
            [("organization_id", ASCENDING), ("user_id", ASCENDING)], unique=True)
        _create_index_safe(db.organization_members, [("user_id", ASCENDING)])
        _create_index_safe(db.organization_members, [("organization_id", ASCENDING)])
        _create_index_safe(db.organization_members, [("status", ASCENDING)])

        _create_index_safe(db.users, [("email", ASCENDING)], unique=True)
        _create_index_safe(db.users, [("status", ASCENDING)])
        _create_index_safe(db.users, [("created_at", ASCENDING)])
        _create_index_safe(db.users, [("is_platform_admin", ASCENDING)])

        # User Sessions with TTL expiration
        _create_index_safe(db.user_sessions, [("session_id", ASCENDING)], unique=True)
        _create_index_safe(db.user_sessions, [("user_id", ASCENDING)])
        _create_index_safe(db.user_sessions, [("organization_id", ASCENDING)])
        _create_index_safe(db.user_sessions,
            [("expires_at", ASCENDING)],
            expireAfterSeconds=0,
            name="expires_at_ttl",
        )

        # Multi-Tenant compound indexes on customer data
        _create_index_safe(db.facebook_pages, [("organization_id", ASCENDING), ("created_at", ASCENDING)])
        _create_index_safe(db.facebook_pages, [("organization_id", ASCENDING), ("facebook_url", ASCENDING)])
        _create_index_safe(db.facebook_posts, [("organization_id", ASCENDING), ("page_ref", ASCENDING)])
        _create_index_safe(db.facebook_comments, [("organization_id", ASCENDING), ("post_ref", ASCENDING)])
        _create_index_safe(db.ai_comments, [("organization_id", ASCENDING), ("is_lead", ASCENDING), ("lead_score", ASCENDING)])
        _create_index_safe(db.search_history, [("organization_id", ASCENDING), ("created_at", ASCENDING)])
        _create_index_safe(db.audit_logs, [("organization_id", ASCENDING), ("at", ASCENDING)])
        _create_index_safe(db.audit_logs, [("actor_user_id", ASCENDING)])

        # ── SaaS Billing & Entitlements Collections ────────────────────────────
        _create_index_safe(db.plans, [("slug", ASCENDING)], unique=True)
        _create_index_safe(db.plans, [("status", ASCENDING)])
        _create_index_safe(db.plans, [("display_order", ASCENDING)])

        _create_index_safe(db.subscriptions, [("organization_id", ASCENDING)])
        _create_index_safe(db.subscriptions, [("status", ASCENDING)])
        _create_index_safe(db.subscriptions, [("provider_subscription_id", ASCENDING)], sparse=True)

        _create_index_safe(db.organization_invitations, [("token_hash", ASCENDING)], unique=True)
        _create_index_safe(db.organization_invitations, [("organization_id", ASCENDING)])
        _create_index_safe(db.organization_invitations, [("email", ASCENDING)])
        _create_index_safe(db.organization_invitations, [("status", ASCENDING)])

        _create_index_safe(db.usage_records, [("organization_id", ASCENDING), ("period", ASCENDING)])
        _create_index_safe(db.usage_records, [("metric", ASCENDING)])
        _create_index_safe(db.usage_records, [("created_at", ASCENDING)])

        _create_index_safe(db.organization_usage,
            [("organization_id", ASCENDING), ("period_start", ASCENDING), ("period_end", ASCENDING)], unique=True)
        _create_index_safe(db.organization_usage, [("organization_id", ASCENDING)])

        _create_index_safe(db.invoices, [("number", ASCENDING)], unique=True)
        _create_index_safe(db.invoices, [("organization_id", ASCENDING)])
        _create_index_safe(db.invoices, [("status", ASCENDING)])

        _create_index_safe(db.payments, [("organization_id", ASCENDING)])
        _create_index_safe(db.payments, [("provider_payment_id", ASCENDING)], sparse=True)

        _create_index_safe(db.temporary_entitlements, [("organization_id", ASCENDING)])
        _create_index_safe(db.temporary_entitlements, [("expires_at", ASCENDING)])

        _create_index_safe(db.processed_webhooks, [("provider_event_id", ASCENDING)], unique=True)

        # Comment Scraping & Keyword Intelligence (keyword filter layer)
        _create_index_safe(db.comment_filter_rules, [("active", ASCENDING)])
        _create_index_safe(db.comment_filter_rules, [("platform", ASCENDING)])
        _create_index_safe(db.comment_filter_rules, [("organization_id", ASCENDING)])
        _create_index_safe(db.comment_filter_rules, [("created_at", ASCENDING)])
        _create_index_safe(db.comment_filter_results, [("comment_id", ASCENDING)], unique=True)
        _create_index_safe(db.comment_filter_results, [("rule_id", ASCENDING)])
        _create_index_safe(db.comment_filter_results, [("organization_id", ASCENDING)])
        _create_index_safe(db.comment_filter_results, [("status", ASCENDING)])
        # TTL index: auto-delete filter results older than 30 days
        _create_index_safe(db.comment_filter_results,
            [("created_at", ASCENDING)],
            expireAfterSeconds=30 * 24 * 3600,
            name="created_at_ttl_30d",
        )
        _create_index_safe(db.comment_categories, [("active", ASCENDING)])
        _create_index_safe(db.comment_categories, [("name", ASCENDING)], unique=True, sparse=True)
        _create_index_safe(db.comment_categories, [("created_at", ASCENDING)])
        # industry catalog (custom industries + overrides of built-in ones)
        _create_index_safe(db.industries, [("key", ASCENDING)], unique=True)

        # ── Master Prompt 3: AI & Apify Control Centers, Leads & Follow-ups ──
        _create_index_safe(db.ai_prompts, [("prompt_key", ASCENDING), ("version", ASCENDING)], unique=True)
        _create_index_safe(db.ai_prompts, [("prompt_key", ASCENDING), ("is_active", ASCENDING)])
        _create_index_safe(db.ai_prompts, [("created_at", DESCENDING)])

        _create_index_safe(db.ai_models, [("model_name", ASCENDING)], unique=True)
        _create_index_safe(db.ai_models, [("is_enabled", ASCENDING)])

        _create_index_safe(db.ai_requests, [("organization_id", ASCENDING), ("created_at", DESCENDING)])
        _create_index_safe(db.ai_requests, [("model", ASCENDING)])
        _create_index_safe(db.ai_requests, [("status", ASCENDING)])
        _create_index_safe(db.ai_requests, [("created_at", DESCENDING)])

        _create_index_safe(db.apify_actors, [("actor_id", ASCENDING)], unique=True)
        _create_index_safe(db.apify_actors, [("platform", ASCENDING)])
        _create_index_safe(db.apify_actors, [("enabled", ASCENDING)])

        _create_index_safe(db.apify_jobs, [("job_id", ASCENDING)], unique=True)
        _create_index_safe(db.apify_jobs, [("organization_id", ASCENDING), ("created_at", DESCENDING)])
        _create_index_safe(db.apify_jobs, [("actor_id", ASCENDING)])
        _create_index_safe(db.apify_jobs, [("status", ASCENDING)])
        _create_index_safe(db.apify_jobs, [("created_at", DESCENDING)])

        _create_index_safe(db.lead_follow_ups, [("lead_id", ASCENDING)])
        _create_index_safe(db.lead_follow_ups, [("organization_id", ASCENDING), ("follow_up_date", ASCENDING)])
        _create_index_safe(db.lead_follow_ups, [("status", ASCENDING)])

        _create_index_safe(db.lead_notes, [("lead_id", ASCENDING), ("created_at", DESCENDING)])
        _create_index_safe(db.lead_notes, [("organization_id", ASCENDING)])

        # ── Customer lifecycle, tokens, notifications, security (Step 1) ──
        _create_index_safe(db.demo_requests, [("status", ASCENDING), ("created_at", DESCENDING)])
        _create_index_safe(db.demo_requests, [("email", ASCENDING)])
        _create_index_safe(db.demo_requests, [("organization_id", ASCENDING)])
        _create_index_safe(db.token_balances, [("organization_id", ASCENDING)], unique=True)
        _create_index_safe(db.token_ledger, [("organization_id", ASCENDING), ("created_at", DESCENDING)])
        _create_index_safe(db.token_ledger, [("user_id", ASCENDING), ("created_at", DESCENDING)])
        _create_index_safe(db.notifications, [("audience", ASCENDING), ("organization_id", ASCENDING),
                                              ("created_at", DESCENDING)])
        _create_index_safe(db.notifications, [("user_id", ASCENDING), ("created_at", DESCENDING)])
        _create_index_safe(db.security_events, [("at", DESCENDING)])
        _create_index_safe(db.security_events, [("type", ASCENDING), ("at", DESCENDING)])
        _create_index_safe(db.security_events, [("organization_id", ASCENDING), ("at", DESCENDING)])
        _create_index_safe(db.login_lockouts, [("email", ASCENDING)], unique=True)
        # durable rate-limit counters (app/auth/rate_limit.py): one doc per
        # {bucket, key, window_start}; old slots expire on their own
        _create_index_safe(db.rate_limits, [("bucket", ASCENDING), ("key", ASCENDING),
                                            ("window_start", ASCENDING)],
                           unique=True, name="rate_limits_bucket_key_window")
        _create_index_safe(db.rate_limits, [("expires_at", ASCENDING)],
                           expireAfterSeconds=0, name="rate_limits_expires_at_ttl")
        _create_index_safe(db.password_resets, [("token_hash", ASCENDING)], unique=True, sparse=True)
        # reset tokens are deleted as soon as they expire (matches the
        # existing production index, so startup never fights over it)
        _create_index_safe(db.password_resets, [("expires_at", ASCENDING)],
                           expireAfterSeconds=0, name="pw_resets_expires_at_ttl")
        _create_index_safe(db.email_outbox, [("status", ASCENDING), ("created_at", DESCENDING)])
        # API usage & limits monitor (app/limits): latest status per scope/provider
        # (_id = "<scope>:<provider>"), 30-day history, alert dedupe
        _create_index_safe(db.api_limit_status, [("kind", ASCENDING), ("scope", ASCENDING)])
        _create_index_safe(db.api_limit_status, [("organization_id", ASCENDING)], sparse=True)
        _create_index_safe(db.api_limit_history, [("scope", ASCENDING), ("provider", ASCENDING),
                                                  ("at", ASCENDING)])
        _create_index_safe(db.api_limit_history, [("at", ASCENDING)],
                           expireAfterSeconds=30 * 24 * 3600, name="api_limit_history_at_ttl_30d")
        _create_index_safe(db.api_limit_alerts, [("scope", ASCENDING), ("provider", ASCENDING),
                                                 ("metric", ASCENDING), ("recovered_at", ASCENDING)])
        _create_index_safe(db.api_limit_alerts, [("notified_at", ASCENDING)],
                           expireAfterSeconds=180 * 24 * 3600, name="api_limit_alerts_ttl_180d")
        _create_index_safe(db.ai_requests, [("key_source", ASCENDING), ("status", ASCENDING),
                                            ("created_at", DESCENDING)])
        _create_index_safe(db.sms_outbox, [("limit_hit", ASCENDING), ("created_at", DESCENDING)], sparse=True)
        _create_index_safe(db.email_outbox, [("limit_hit", ASCENDING), ("created_at", DESCENDING)], sparse=True)
        _create_index_safe(db.payment_events, [("subscription_id", ASCENDING), ("created_at", ASCENDING)])
        _create_index_safe(db.subscriptions, [("checkout_session_id", ASCENDING)], sparse=True)
        _create_index_safe(db.role_permissions, [("kind", ASCENDING), ("role", ASCENDING)], unique=True)
        _create_index_safe(db.search_history, [("organization_id", ASCENDING), ("user_id", ASCENDING),
                                               ("created_at", DESCENDING)])
        _create_index_safe(db.search_history, [("organization_id", ASCENDING), ("created_at", DESCENDING)])
        _create_index_safe(db.search_history, [("organization_id", ASCENDING), ("status", ASCENDING), ("created_at", DESCENDING)])
        _create_index_safe(db.search_history, [("user_id", ASCENDING), ("created_at", DESCENDING)])
        _create_index_safe(db.search_history, [("user_id", ASCENDING), ("status", ASCENDING)])

        _create_index_safe(db.ai_comments, [("organization_id", ASCENDING), ("assigned_user_id", ASCENDING)])
        _create_index_safe(db.ai_comments, [("organization_id", ASCENDING), ("user_id", ASCENDING)])
        _create_index_safe(db.ai_comments, [("user_id", ASCENDING), ("is_lead", ASCENDING)])
        _create_index_safe(db.ai_comments, [("user_id", ASCENDING), ("created_at", DESCENDING)])
        _create_index_safe(db.ai_comments, [("organization_id", ASCENDING), ("is_lead", ASCENDING), ("lead_status", ASCENDING)])
        _create_index_safe(db.ai_comments, [("organization_id", ASCENDING), ("lead_priority", ASCENDING)])
        _create_index_safe(db.ai_comments, [("organization_id", ASCENDING), ("created_at", DESCENDING)])
        _create_index_safe(db.ai_comments, [("search_run_id", ASCENDING), ("is_lead", ASCENDING)])

        _create_index_safe(db.search_presets, [("organization_id", ASCENDING), ("created_at", DESCENDING)])
        _create_index_safe(db.search_presets, [("organization_id", ASCENDING), ("platform", ASCENDING), ("created_at", DESCENDING)])
        _create_index_safe(db.search_presets, [("organization_id", ASCENDING), ("kind", ASCENDING), ("created_at", DESCENDING)])

        # ── Keyword / discovery search (app/discovery) ──────────────────
        # one candidate per run + platform + profile = discovery dedupe
        _create_index_safe(db.discovery_candidates, [("search_run_id", ASCENDING), ("platform", ASCENDING),
                                                     ("profile_id", ASCENDING)], unique=True)
        _create_index_safe(db.discovery_candidates, [("search_run_id", ASCENDING), ("status", ASCENDING),
                                                     ("platform", ASCENDING)])
        _create_index_safe(db.discovery_candidates, [("search_run_id", ASCENDING), ("stage_reached", ASCENDING)])
        _create_index_safe(db.discovery_candidates, [("organization_id", ASCENDING), ("created_at", DESCENDING)])
        _create_index_safe(db.discovery_candidates, [("user_id", ASCENDING), ("created_at", DESCENDING)])
        # shared profile cache; the TTL index deletes rows once expires_at passes
        _create_index_safe(db.discovery_profile_cache, [("platform", ASCENDING), ("profile_id", ASCENDING)],
                           unique=True)
        _create_index_safe(db.discovery_profile_cache, [("expires_at", ASCENDING)], expireAfterSeconds=0,
                           name="discovery_cache_expires_at_ttl")
        _create_index_safe(db.search_history, [("organization_id", ASCENDING), ("search_type", ASCENDING),
                                               ("created_at", DESCENDING)])

        _create_index_safe(db.token_balances, [("updated_at", DESCENDING)])

        _create_index_safe(db.notifications, [("audience", ASCENDING), ("user_id", ASCENDING), ("created_at", DESCENDING)])
        _create_index_safe(db.notifications, [("read_by", ASCENDING), ("created_at", DESCENDING)])

        # ── Partner program ─────────────────────────────────────────────
        # uniqueness: one partner per user, unique public codes, one referral
        # (= partner customer) per organization, one commission per payment
        # period, one coupon redemption per organization.
        _create_index_safe(db.partners, [("user_id", ASCENDING)], unique=True)
        _create_index_safe(db.partners, [("referral_code", ASCENDING)], unique=True)
        _create_index_safe(db.partners, [("partner_code", ASCENDING)], unique=True)
        _create_index_safe(db.partners, [("status", ASCENDING), ("partner_type", ASCENDING)])
        _create_index_safe(db.partner_applications, [("user_id", ASCENDING), ("created_at", DESCENDING)])
        _create_index_safe(db.partner_applications, [("status", ASCENDING), ("created_at", DESCENDING)])
        _create_index_safe(db.partner_referrals, [("organization_id", ASCENDING)], unique=True)
        _create_index_safe(db.partner_referrals, [("partner_id", ASCENDING), ("signed_up_at", DESCENDING)])
        _create_index_safe(db.partner_referrals, [("partner_id", ASCENDING), ("stage", ASCENDING)])
        _create_index_safe(db.partner_referral_clicks, [("partner_id", ASCENDING), ("created_at", DESCENDING)])
        _create_index_safe(db.partner_referral_clicks, [("partner_id", ASCENDING), ("visitor_id", ASCENDING),
                                                        ("created_at", DESCENDING)])
        _create_index_safe(db.partner_campaigns, [("partner_id", ASCENDING), ("slug", ASCENDING)], unique=True)
        _create_index_safe(db.partner_commissions, [("subscription_id", ASCENDING), ("period_key", ASCENDING)],
                           unique=True, partialFilterExpression={"kind": "commission"})
        _create_index_safe(db.partner_commissions, [("partner_id", ASCENDING), ("status", ASCENDING),
                                                    ("created_at", DESCENDING)])
        _create_index_safe(db.partner_commissions, [("status", ASCENDING), ("hold_until", ASCENDING)])
        _create_index_safe(db.partner_commissions, [("payout_id", ASCENDING)], sparse=True)
        _create_index_safe(db.partner_wallets, [("partner_id", ASCENDING)], unique=True)
        _create_index_safe(db.partner_wallet_transactions, [("partner_id", ASCENDING), ("created_at", DESCENDING)])
        _create_index_safe(db.partner_payouts, [("partner_id", ASCENDING), ("status", ASCENDING)])
        _create_index_safe(db.partner_payouts, [("status", ASCENDING), ("created_at", DESCENDING)])
        _create_index_safe(db.partner_coupons, [("code", ASCENDING)], unique=True)
        _create_index_safe(db.partner_coupons, [("partner_id", ASCENDING)])
        _create_index_safe(db.partner_coupon_usages, [("coupon_id", ASCENDING), ("organization_id", ASCENDING)],
                           unique=True)
        _create_index_safe(db.api_keys, [("partner_id", ASCENDING)], sparse=True)
        _create_index_safe(db.partner_activity, [("at", DESCENDING)])
        _create_index_safe(db.partner_activity, [("partner_id", ASCENDING), ("at", DESCENDING)])
        _create_index_safe(db.partner_activity, [("kind", ASCENDING), ("at", DESCENDING)])
        _create_index_safe(db.partner_activity, [("expires_at", ASCENDING)], expireAfterSeconds=0)
        _create_index_safe(db.partner_deals, [("partner_id", ASCENDING), ("status", ASCENDING)])
        _create_index_safe(db.partner_deals, [("contact_email", ASCENDING)])
        _create_index_safe(db.partner_deals, [("status", ASCENDING), ("created_at", DESCENDING)])
        _create_index_safe(db.partner_tasks, [("partner_id", ASCENDING), ("status", ASCENDING)])
        _create_index_safe(db.partner_tasks, [("status", ASCENDING), ("created_at", DESCENDING)])
        _create_index_safe(db.partner_fraud_flags, [("status", ASCENDING), ("created_at", DESCENDING)])
        _create_index_safe(db.user_sessions, [("scope", ASCENDING), ("user_id", ASCENDING)])
        _create_index_safe(db.partner_pricing, [("status", ASCENDING), ("scope", ASCENDING)])
        _create_index_safe(db.partner_commissions, [("invoice_id", ASCENDING)], sparse=True)
        _create_index_safe(db.partner_commissions, [("referral_id", ASCENDING), ("status", ASCENDING)])
        # refunds: invoice of a subscription, payment by provider reference
        _create_index_safe(db.invoices, [("subscription_id", ASCENDING), ("created_at", DESCENDING)])
        _create_index_safe(db.payments, [("provider_payment_id", ASCENDING)], sparse=True)
        _create_index_safe(db.audit_logs, [("category", ASCENDING), ("details.partner_id", ASCENDING),
                                           ("at", DESCENDING)])

        logger.info("MongoDB indexes verified successfully.")
    except Exception as e:
        logger.warning(f"MongoDB index verification skipped at startup (Connection timeout): {e}")
