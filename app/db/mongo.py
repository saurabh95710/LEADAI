"""
Async motor client for FastAPI handlers; sync pymongo for background
agents (threads). `ensure_indexes` is called once at startup and never
crashes the server when Mongo is temporarily unreachable.
"""
import logging
from functools import lru_cache
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient, ASCENDING
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def _configure_dns():
    """Point dnspython at public resolvers so mongodb+srv:// SRV lookups
    don't hang when the system DNS is unreliable (e.g. local routers that
    time out on SRV queries)."""
    servers = [s.strip() for s in settings.dns_servers.split(",") if s.strip()]
    if not servers:
        return
    try:
        from dns import resolver, asyncresolver
        custom = resolver.Resolver(configure=False)
        custom.nameservers = servers
        custom_async = asyncresolver.Resolver(configure=False)
        custom_async.nameservers = servers
        # Point pymongo's SRV lookups (dns.resolver/asyncresolver.resolve)
        # at the reliable nameservers. Avoid override_system_resolver() —
        # it swaps socket.getaddrinfo for a positional-only version that
        # breaks pymongo's keyword call.
        resolver.default_resolver = custom
        asyncresolver.default_resolver = custom_async
        logger.info(f"DNS resolver overridden with: {servers}")
    except Exception as e:
        logger.warning(f"Failed to override DNS resolver: {e}")


_configure_dns()


@lru_cache
def get_async_client() -> Optional[AsyncIOMotorClient]:
    try:
        return AsyncIOMotorClient(
            settings.mongo_uri,
            serverSelectionTimeoutMS=3000,
            connectTimeoutMS=3000,
        )
    except Exception as e:
        logger.warning(f"Async Motor client connection warning: {e}")
        return None


def get_async_db():
    try:
        client = get_async_client()
        return client[settings.mongo_db_name] if client is not None else None
    except Exception as e:
        logger.warning(f"Async DB connection failed: {e}")
        return None


@lru_cache
def get_sync_client() -> Optional[MongoClient]:
    try:
        return MongoClient(
            settings.mongo_uri,
            serverSelectionTimeoutMS=3000,
            connectTimeoutMS=3000,
        )
    except Exception as e:
        logger.warning(f"Sync MongoClient connection warning: {e}")
        return None


def get_sync_db():
    try:
        client = get_sync_client()
        return client[settings.mongo_db_name] if client is not None else None
    except Exception as e:
        logger.warning(f"Sync DB connection failed: {e}")
        return None


def _drop_index_if_exists(collection, name: str):
    """Drop a legacy index if present (e.g. the old global-unique indexes)."""
    try:
        collection.drop_index(name)
        logger.info(f"Dropped legacy index {name}")
    except Exception:
        pass


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
        db.facebook_pages.create_index(
            [("facebook_url", ASCENDING), ("search_run_id", ASCENDING)], unique=True)
        db.facebook_pages.create_index([("search_run_id", ASCENDING)])
        db.facebook_pages.create_index([("category", ASCENDING)])
        db.facebook_pages.create_index([("city", ASCENDING)])
        db.facebook_pages.create_index([("state", ASCENDING)])
        db.facebook_pages.create_index([("posts_status", ASCENDING)])

        _drop_index_if_exists(db.facebook_posts, "post_url_1")
        db.facebook_posts.create_index(
            [("post_url", ASCENDING), ("page_ref", ASCENDING)], unique=True, sparse=True)
        db.facebook_posts.create_index([("page_id", ASCENDING)])
        db.facebook_posts.create_index([("page_ref", ASCENDING)])
        db.facebook_posts.create_index([("comments_status", ASCENDING)])

        _drop_index_if_exists(db.facebook_comments, "comment_url_1")
        db.facebook_comments.create_index(
            [("comment_url", ASCENDING), ("post_ref", ASCENDING)], unique=True, sparse=True)
        db.facebook_comments.create_index([("post_id", ASCENDING)])
        db.facebook_comments.create_index([("post_ref", ASCENDING)])

        db.ai_comments.create_index([("comment_ref", ASCENDING)], unique=True)
        db.ai_comments.create_index([("post_id", ASCENDING)])
        db.ai_comments.create_index([("is_lead", ASCENDING)])
        db.ai_comments.create_index([("lead_score", ASCENDING)])

        db.search_history.create_index([("run_id", ASCENDING)], unique=True)
        db.search_history.create_index([("created_at", ASCENDING)])

        logger.info("MongoDB indexes verified successfully.")
    except Exception as e:
        logger.warning(f"MongoDB index verification skipped at startup (Connection timeout): {e}")
