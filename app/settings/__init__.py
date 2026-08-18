"""Global Settings module — schema registry for the settings system.

The registry (``registry.py``) describes every admin-manageable setting; the
storage/service layer lives in :mod:`app.admin.settings`. Together they power
the Settings API (:mod:`app.api.routes.settings`) and the admin "Global
Settings" UI.
"""
from app.settings.registry import (
    CATEGORIES,
    REGISTERED_KEYS,
    SETTINGS,
    SPEC_BY_KEY,
    build_public_config,
    category_specs,
    groups_of,
    is_registered,
    schema_categories,
    validate_patch,
    validate_value,
)

__all__ = [
    "CATEGORIES",
    "REGISTERED_KEYS",
    "SETTINGS",
    "SPEC_BY_KEY",
    "build_public_config",
    "category_specs",
    "groups_of",
    "is_registered",
    "schema_categories",
    "validate_patch",
    "validate_value",
]
