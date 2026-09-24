"""
AI Models Management Service.

Provides configuration and catalog management for supported AI models:
  - Default models registry (Gemini 2.5 Flash, Gemini 2.5 Pro, Gemini 1.5 Flash)
  - Purpose, token limits, temperature, context limits, and per-token pricing
  - Default model resolution with fallback
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from bson import ObjectId

from app.db.mongo import get_sync_db

logger = logging.getLogger(__name__)

DEFAULT_MODELS = [
    {
        "provider": "gemini",
        "model_name": "gemini-2.5-flash",
        "display_name": "Gemini 2.5 Flash",
        "purpose": "Comment Analysis",
        "is_enabled": True,
        "is_default": True,
        "max_tokens": 2048,
        "temperature": 0.2,
        "cost_input_per_1k": 0.0001,
        "cost_output_per_1k": 0.0004,
        "context_limit": 32000,
    },
    {
        "provider": "gemini",
        "model_name": "gemini-2.5-pro",
        "display_name": "Gemini 2.5 Pro",
        "purpose": "Deep Lead Qualification",
        "is_enabled": True,
        "is_default": False,
        "max_tokens": 4096,
        "temperature": 0.1,
        "cost_input_per_1k": 0.00125,
        "cost_output_per_1k": 0.0050,
        "context_limit": 64000,
    },
    {
        "provider": "gemini",
        "model_name": "gemini-1.5-flash",
        "display_name": "Gemini 1.5 Flash (Legacy)",
        "purpose": "Fallback / High Speed",
        "is_enabled": True,
        "is_default": False,
        "max_tokens": 2048,
        "temperature": 0.2,
        "cost_input_per_1k": 0.000075,
        "cost_output_per_1k": 0.0003,
        "context_limit": 32000,
    }
]


def seed_default_models() -> None:
    """Ensures registered AI models exist in the database."""
    try:
        db = get_sync_db()
        if db is None:
            return
        for seed in DEFAULT_MODELS:
            existing = db.ai_models.find_one({"model_name": seed["model_name"]})
            if not existing:
                doc = {
                    **seed,
                    "created_at": datetime.now(timezone.utc),
                    "updated_at": datetime.now(timezone.utc),
                }
                db.ai_models.insert_one(doc)
                logger.info(f"Seeded AI model: {seed['model_name']}")
    except Exception as e:
        logger.warning(f"Could not seed default AI models: {e}")


def list_models(only_enabled: bool = False) -> List[Dict[str, Any]]:
    """Lists registered AI models."""
    try:
        db = get_sync_db()
        if db is None:
            return DEFAULT_MODELS
        query = {"is_enabled": True} if only_enabled else {}
        cursor = db.ai_models.find(query).sort([("is_default", -1), ("display_name", 1)])
        res = []
        for doc in cursor:
            doc["_id"] = str(doc["_id"])
            res.append(doc)
        if not res and not only_enabled:
            seed_default_models()
            return list_models()
        return res
    except Exception as e:
        logger.error(f"Error listing AI models: {e}")
        return DEFAULT_MODELS


def get_default_model() -> Dict[str, Any]:
    """Returns the configured default AI model."""
    try:
        db = get_sync_db()
        if db is not None:
            doc = db.ai_models.find_one({"is_default": True, "is_enabled": True})
            if doc:
                doc["_id"] = str(doc["_id"])
                return doc
    except Exception as e:
        logger.warning(f"Failed to query default AI model: {e}")
    return DEFAULT_MODELS[0]


def get_model_by_name(model_name: str) -> Optional[Dict[str, Any]]:
    """Fetches model info by model_name."""
    try:
        db = get_sync_db()
        if db is not None:
            doc = db.ai_models.find_one({"model_name": model_name})
            if doc:
                doc["_id"] = str(doc["_id"])
                return doc
    except Exception:
        pass
    for m in DEFAULT_MODELS:
        if m["model_name"] == model_name:
            return m
    return None


def update_model(model_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """Updates model parameters."""
    db = get_sync_db()
    if db is None:
        raise RuntimeError("Database unavailable")

    clean_updates = {}
    allowed_fields = {
        "display_name", "purpose", "is_enabled", "is_default",
        "max_tokens", "temperature", "cost_input_per_1k", "cost_output_per_1k", "context_limit"
    }
    for k, v in updates.items():
        if k in allowed_fields:
            clean_updates[k] = v

    if clean_updates.get("is_default"):
        db.ai_models.update_many({}, {"$set": {"is_default": False}})

    clean_updates["updated_at"] = datetime.now(timezone.utc)
    db.ai_models.update_one({"_id": ObjectId(model_id)}, {"$set": clean_updates})
    updated = db.ai_models.find_one({"_id": ObjectId(model_id)})
    if not updated:
        raise ValueError("Model not found")
    updated["_id"] = str(updated["_id"])
    return updated


def create_model(model_data: Dict[str, Any]) -> Dict[str, Any]:
    """Registers a new AI model."""
    db = get_sync_db()
    if db is None:
        raise RuntimeError("Database unavailable")

    existing = db.ai_models.find_one({"model_name": model_data["model_name"]})
    if existing:
        raise ValueError(f"Model with name {model_data['model_name']} already exists")

    if model_data.get("is_default"):
        db.ai_models.update_many({}, {"$set": {"is_default": False}})

    now = datetime.now(timezone.utc)
    doc = {
        "provider": model_data.get("provider", "gemini"),
        "model_name": model_data["model_name"],
        "display_name": model_data.get("display_name", model_data["model_name"]),
        "purpose": model_data.get("purpose", "Comment Analysis"),
        "is_enabled": model_data.get("is_enabled", True),
        "is_default": model_data.get("is_default", False),
        "max_tokens": model_data.get("max_tokens", 2048),
        "temperature": model_data.get("temperature", 0.2),
        "cost_input_per_1k": model_data.get("cost_input_per_1k", 0.0001),
        "cost_output_per_1k": model_data.get("cost_output_per_1k", 0.0004),
        "context_limit": model_data.get("context_limit", 32000),
        "created_at": now,
        "updated_at": now,
    }
    result = db.ai_models.insert_one(doc)
    doc["_id"] = str(result.inserted_id)
    return doc
