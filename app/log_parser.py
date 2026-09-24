"""
Structured log parser — normalizes raw log lines into structured dicts.

Handles:
- Standard Python logging format: "HH:MM:SS LEVEL  name: message"
- Lines with bracketed prefixes: [Apify][REQ], [URL SEARCH], etc.
- Multiline stack traces (grouped with preceding ERROR/WARNING)
- JSON-formatted log lines
- Unknown/malformed formats (fallback to raw)
- Secret redaction for sensitive values
"""
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# ── Secret redaction ──────────────────────────────────────────────────────────

_SECRET_PATTERNS = [
    (re.compile(r'(APIFY_API_TOKEN["\s:=]+)\S+', re.I), r'\1********'),
    (re.compile(r'(GEMINI_API_KEY["\s:=]+)\S+', re.I), r'\1********'),
    (re.compile(r'(SESSION_SECRET["\s:=]+)\S+', re.I), r'\1********'),
    (re.compile(r'(ADMIN_PASSWORD_HASH["\s:=]+)\S+', re.I), r'\1********'),
    (re.compile(r'(PANEL_ADMIN_PASSWORD_HASH["\s:=]+)\S+', re.I), r'\1********'),
    (re.compile(r'(SUPERADMIN_PASSWORD["\s:=]+)\S+', re.I), r'\1********'),
    (re.compile(r'(Authorization["\s:=]+Bearer\s+)\S+', re.I), r'\1********'),
    (re.compile(r'(password["\s:=]+)\S+', re.I), r'\1********'),
    (re.compile(r'(token["\s:=]+)\S+', re.I), r'\1********'),
    (re.compile(r'(mongodb(\+srv)?://[^:]+:)[^@]+(@)', re.I), r'\1********\2'),
]

_SECRET_KEYS = {
    'token', 'password', 'password_hash', 'session_secret',
    'admin_password_hash', 'panel_admin_password_hash', 'superadmin_password',
    'apify_api_token', 'gemini_api_key', 'new_password', 'old_password',
    'secret', 'authorization', 'cookie',
}


def redact_secrets(text: str) -> str:
    """Replace sensitive values in a log line with ********."""
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_dict(d: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively redact sensitive keys in a dictionary."""
    redacted = {}
    for k, v in d.items():
        k_lower = k.lower().strip()
        if any(s in k_lower for s in _SECRET_KEYS):
            redacted[k] = "********"
        elif isinstance(v, dict):
            redacted[k] = redact_dict(v)
        elif isinstance(v, list):
            redacted[k] = [redact_dict(i) if isinstance(i, dict) else i for i in v]
        else:
            redacted[k] = v
    return redacted


# ── Log level classification ──────────────────────────────────────────────────

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
LEVEL_SET = set(LEVELS)

_LEVEL_PATTERN = re.compile(
    r'^\d{2}:\d{2}:\d{2}\s+(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+'
)

_BRACKET_LEVEL_PATTERN = re.compile(
    r'\b(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b'
)


def extract_level(line: str) -> str:
    """Extract log level from a raw line. Returns 'UNKNOWN' if not found."""
    m = _LEVEL_PATTERN.match(line)
    if m:
        return m.group(1)
    m = _BRACKET_LEVEL_PATTERN.search(line)
    if m:
        return m.group(1)
    return "UNKNOWN"


# ── Module/logger extraction ──────────────────────────────────────────────────

_MODULE_PATTERN = re.compile(
    r'^\d{2}:\d{2}:\d{2}\s+\w+\s+([\w.]+):\s'
)


def extract_module(line: str) -> str:
    """Extract logger name/module from a raw line."""
    m = _MODULE_PATTERN.match(line)
    if m:
        return m.group(1)
    return ""


# ── Timestamp extraction ──────────────────────────────────────────────────────

_TIMESTAMP_PATTERN = re.compile(r'^(\d{2}:\d{2}:\d{2})')


def extract_timestamp(line: str) -> str:
    """Extract timestamp from a raw line (HH:MM:SS format)."""
    m = _TIMESTAMP_PATTERN.match(line)
    if m:
        return m.group(1)
    return ""


# ── Source classification ─────────────────────────────────────────────────────

_SOURCE_MAP = {
    "app.db": "Database",
    "app.db.mongo": "Database",
    "app.connectors": "Apify",
    "app.connectors.apify_connector": "Apify",
    "app.social": "Search",
    "app.social.url_detector": "Search",
    "app.social.url_search": "Search",
    "app.social.scrapers": "Search",
    "app.agent": "Search",
    "app.agent.search": "Search",
    "app.pipeline": "AI",
    "app.pipeline.comment_ai": "AI",
    "app.pipeline.comment_filter": "AI",
    "app.auth": "Authentication",
    "app.auth.crypto": "Authentication",
    "app.auth.service": "Authentication",
    "app.auth.roles": "Authentication",
    "app.admin": "System",
    "app.admin.audit": "System",
    "app.admin.settings": "System",
    "app.admin.envvars": "System",
    "app.main": "System",
    "app.api": "API",
    "app.api.routes.admin": "API",
    "app.api.routes.auth": "API",
    "app.api.routes.search": "API",
    "app.api.routes.comment_filters": "API",
    "app.api.routes.settings": "API",
}

_BRACKET_SOURCE_MAP = {
    "[Apify]": "Apify",
    "[Apify][REQ]": "Apify",
    "[Apify][RESP]": "Apify",
    "[Apify][ERR]": "Apify",
    "[URL SEARCH]": "Search",
    "[Agent]": "Search",
    "[Facebook]": "Search",
    "[Instagram]": "Search",
    "[YouTube]": "Search",
    "[LinkedIn]": "Search",
    "[Gemini]": "AI",
    "[CommentAI]": "AI",
    "[CommentFilter]": "AI",
}


def classify_source(module: str, message: str) -> str:
    """Determine the source category from module name and message."""
    if module:
        for prefix, source in _SOURCE_MAP.items():
            if module.startswith(prefix):
                return source
    for bracket, source in _BRACKET_SOURCE_MAP.items():
        if bracket in message:
            return source
    return "Application"


# ── Event/message extraction ──────────────────────────────────────────────────

def extract_event(message: str) -> str:
    """Extract a concise event summary from the message."""
    # Remove bracketed prefixes for cleaner display
    cleaned = re.sub(r'\[[\w]+\]\s*', '', message).strip()
    # Take first sentence or up to 120 chars
    first_sentence = re.split(r'[.!]\s', cleaned, maxsplit=1)
    event = first_sentence[0] if first_sentence else cleaned
    if len(event) > 120:
        event = event[:117] + "..."
    return event


def extract_error_type(message: str) -> str:
    """Extract exception/error type from message if present."""
    m = re.search(r'(\w+(?:\.\w+)*(?:Error|Exception|Warning|Timeout))', message)
    if m:
        return m.group(1)
    return ""


# ── Structured fields extraction ──────────────────────────────────────────────

def extract_structured_fields(message: str) -> Dict[str, Any]:
    """Extract structured fields from common log patterns."""
    fields: Dict[str, Any] = {}

    # run_id
    m = re.search(r'run_id[=:]\s*(\S+)', message)
    if m:
        fields["run_id"] = m.group(1).rstrip(",.")

    # request_id
    m = re.search(r'request[_-]?id[=:]\s*(\S+)', message, re.I)
    if m:
        fields["request_id"] = m.group(1).rstrip(",.")

    # job_id
    m = re.search(r'job[_-]?id[=:]\s*(\S+)', message, re.I)
    if m:
        fields["job_id"] = m.group(1).rstrip(",.")

    # endpoint / HTTP method / status
    m = re.search(r'(GET|POST|PUT|PATCH|DELETE)\s+(/\S+)', message)
    if m:
        fields["http_method"] = m.group(1)
        fields["endpoint"] = m.group(2)

    m = re.search(r'status[_= ](2\d{2}|3\d{2}|4\d{2}|5\d{2})', message)
    if m:
        fields["http_status"] = int(m.group(1))

    # duration
    m = re.search(r'duration[=:]\s*([\d.]+)\s*(ms|s|sec)', message, re.I)
    if m:
        fields["duration"] = f"{m.group(1)} {m.group(2)}"

    # collection
    m = re.search(r'collection[=:]\s*(\w+)', message, re.I)
    if m:
        fields["collection"] = m.group(1)

    # platform
    for plat in ("facebook", "instagram", "youtube", "linkedin"):
        if plat in message.lower():
            fields["platform"] = plat.capitalize()
            break

    # error_code
    m = re.search(r'error[_-]?code[=:]\s*(\S+)', message, re.I)
    if m:
        fields["error_code"] = m.group(1).rstrip(",.")

    return fields


# ── Multiline grouping ────────────────────────────────────────────────────────

def group_multiline(lines: List[str]) -> List[str]:
    """Group stack traces with their preceding ERROR/WARNING line.

    Returns a list where each element is either:
    - A single log line
    - A multi-line block (lines joined with \\n)
    """
    grouped: List[str] = []
    buffer: Optional[str] = None

    for line in lines:
        stripped = line.rstrip("\n")
        if not stripped:
            if buffer is not None:
                buffer += "\n"
            continue

        level = extract_level(stripped)
        if level in ("ERROR", "WARNING", "CRITICAL"):
            if buffer is not None:
                grouped.append(buffer)
            buffer = stripped
        elif buffer is not None:
            # Indented continuation line (stack trace)
            if stripped.startswith(" ") or stripped.startswith("Traceback"):
                buffer += "\n" + stripped
            else:
                grouped.append(buffer)
                buffer = stripped
        else:
            grouped.append(stripped)

    if buffer is not None:
        grouped.append(buffer)

    return grouped


# ── Main parser ───────────────────────────────────────────────────────────────

def parse_log_line(line: str) -> Dict[str, Any]:
    """Parse a raw log line into a structured dict.

    Never raises exceptions — malformed lines get a fallback structure.
    """
    try:
        raw = line.rstrip("\n")
        if not raw:
            return {"raw": "", "level": "EMPTY", "message": ""}

        level = extract_level(raw)
        timestamp = extract_timestamp(raw)
        module = extract_module(raw)

        # Extract message (everything after "LEVEL  module: ")
        message = raw
        m = re.match(r'^\d{2}:\d{2}:\d{2}\s+\w+\s+[\w.]+:\s', raw)
        if m:
            message = raw[m.end():]
        elif timestamp and level != "UNKNOWN":
            # Try to extract after timestamp + level
            m2 = re.match(r'^\d{2}:\d{2}:\d{2}\s+\w+\s+', raw)
            if m2:
                message = raw[m2.end():]

        source = classify_source(module, message)
        event = extract_event(message)
        error_type = extract_error_type(message)
        structured = extract_structured_fields(message)

        result: Dict[str, Any] = {
            "timestamp": timestamp,
            "level": level,
            "module": module,
            "event": event,
            "message": redact_secrets(message),
            "source": source,
            "raw": redact_secrets(raw),
        }

        if error_type:
            result["error_type"] = error_type
        if structured:
            result.update(structured)

        # Include stack trace for multiline blocks
        if "\n" in raw:
            parts = raw.split("\n", 1)
            result["message"] = redact_secrets(parts[0])
            result["stack_trace"] = redact_secrets(parts[1])

        return result

    except Exception:
        return {
            "timestamp": "",
            "level": "UNKNOWN",
            "module": "",
            "event": "Unparseable log line",
            "message": redact_secrets(line.rstrip("\n")),
            "raw": redact_secrets(line.rstrip("\n")),
            "source": "Application",
        }


def parse_log_lines(lines: List[str]) -> List[Dict[str, Any]]:
    """Parse multiple raw log lines, grouping multiline stack traces."""
    grouped = group_multiline(lines)
    return [parse_log_line(line) for line in grouped]
