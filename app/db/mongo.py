"""
Async motor client for FastAPI handlers; sync pymongo for background
agents (threads). `ensure_indexes` is called once at startup and never
crashes the server when Mongo is temporarily unreachable.
"""
import logging
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


_async_client_cache = None


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
            serverSelectionTimeoutMS=10000,
            connectTimeoutMS=10000,
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


_sync_client_cache = None


def get_sync_client() -> Optional[MongoClient]:
    global _sync_client_cache
    if _sync_client_cache is not None:
        return _sync_client_cache
    try:
        _sync_client_cache = MongoClient(
            settings.mongo_uri,
            serverSelectionTimeoutMS=10000,
            connectTimeoutMS=10000,
        )
        return _sync_client_cache
    except Exception as e:
        logger.warning(f"Sync MongoClient connection warning: {e}")
        _sync_client_cache = None
        return None


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
        _create_index_safe(db.payment_events, [("subscription_id", ASCENDING), ("created_at", ASCENDING)])
        _create_index_safe(db.subscriptions, [("checkout_session_id", ASCENDING)], sparse=True)
        _create_index_safe(db.role_permissions, [("kind", ASCENDING), ("role", ASCENDING)], unique=True)
        _create_index_safe(db.search_history, [("organization_id", ASCENDING), ("user_id", ASCENDING),
                                               ("created_at", DESCENDING)])
        _create_index_safe(db.ai_comments, [("organization_id", ASCENDING), ("assigned_user_id", ASCENDING)])
        _create_index_safe(db.ai_comments, [("organization_id", ASCENDING), ("user_id", ASCENDING)])

        logger.info("MongoDB indexes verified successfully.")
    except Exception as e:
        logger.warning(f"MongoDB index verification skipped at startup (Connection timeout): {e}")
