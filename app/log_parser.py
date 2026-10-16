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
import re
from typing import Any, Dict, List, Optional

# ── Secret redaction ──────────────────────────────────────────────────────────

_SECRET_PATTERNS = [
    (re.compile(r'(APIFY_API_TOKEN["\s:=]+)\S+', re.I), r'\1********'),
    (re.compile(r'(GEMINI_API_KEY["\s:=]+)\S+', re.I), r'\1********'),
    (re.compile(r'(SESSION_SECRET["\s:=]+)\S+', re.I), r'\1********'),
    (re.compile(r'(ADMIN_PASSWORD_HASH["\s:=]+)\S+', re.I), r'\1********'),
    (re.compile(r'(PANEL_ADMIN_PASSWORD_HASH["\s:=]+)\S+', re.I), r'\1********'),
    (re.compile(r'(SUPERADMIN_PASSWORD["\s:=]+)\S+', re.I), r'\1********'),
    (re.compile(r'(Authorization["\s:=]+Bearer\s+)\S+', re.I), r'\1********'),
    # API keys passed as a URL query parameter (e.g. Gemini's ?key=...)
    (re.compile(r'([?&](?:key|api_key|apikey|access_token)=)[^&\s"\']+', re.I), r'\1********'),
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
    redacted: Dict[str, Any] = {}
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

# "HH:MM:SS LEVEL [app.db.mongo] [request_id=…] msg" (the app's format, see
# app/main.py) and the older "HH:MM:SS LEVEL app.db.mongo: msg"
_MODULE_PATTERN = re.compile(
    r'^\d{2}:\d{2}:\d{2}\s+\w+\s+(?:\[([\w.]+)\]|([\w.]+):)\s'
)


def extract_module(line: str) -> str:
    """Extract logger name/module from a raw line."""
    m = _MODULE_PATTERN.match(line)
    if m:
        return m.group(1) or m.group(2) or ""
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
    # Remove bracketed prefixes for cleaner display ([module] [request_id=…] [Apify][REQ] …)
    cleaned = re.sub(r'^\s*(?:\[[^\]]*\]\s*)+', '', message)
    cleaned = re.sub(r'\[[\w]+\]\s*', '', cleaned).strip()
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
        if not stripped.strip():
            continue
        # A new entry starts only where a line looks like one ("HH:MM:SS LEVEL",
        # "[LEVEL]", JSON). Everything else — traceback lines, "During handling
        # of the above exception…", the final "SomeError: …" line, wrapped text —
        # belongs to the entry above it instead of showing as its own UNKNOWN row.
        if buffer is None or is_entry_start(stripped):
            if buffer is not None:
                grouped.append(buffer)
            buffer = stripped
        else:
            buffer += "\n" + stripped

    if buffer is not None:
        grouped.append(buffer)

    return grouped


# ── Plain-language explanation ────────────────────────────────────────────────
# What a Super Admin reads first: what happened, what to do, where in the app.

_ENTRY_START = re.compile(
    r'^(?:\d{2}:\d{2}:\d{2}\s+(?:DEBUG|INFO|WARNING|ERROR|CRITICAL)\b'
    r'|\[(?:DEBUG|INFO|WARNING|ERROR|CRITICAL)\]|\{)'
)
# the final "SomeError: message" line(s) of a Python traceback
_EXC_LINE = re.compile(r'^([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*):\s?(.*)$')
_NOT_EXC = ("Traceback", "During handling", "The above exception", "future", "For more information",
            "Task exception", "Exception in", "Error in")
_FRAME = re.compile(r'File "([^"]+)", line (\d+), in (\S+)')
_PREFIX = re.compile(r'^\s*(?:\[[^\]]*\]\s*)+')


def is_entry_start(line: str) -> bool:
    """A new log entry starts with "HH:MM:SS LEVEL", "[LEVEL]" or a JSON object;
    every other line belongs to the entry above it (traceback, wrapped text)."""
    return bool(_ENTRY_START.match(line))


def final_exception(block: str) -> Optional[Dict[str, str]]:
    """The last "ErrorType: message" line of a traceback block, if any."""
    found = None
    for line in block.split("\n")[1:]:
        if not line or line[0].isspace() or line.startswith(_NOT_EXC):
            continue
        m = _EXC_LINE.match(line)
        if m and re.search(r'(Error|Exception|Timeout|Cancelled|Reconnect|gaierror|Failure|Exit|Interrupt|Warning)', m.group(1)):
            found = {"type": m.group(1), "message": m.group(2).strip()}
    return found


def app_location(block: str) -> str:
    """Where in LeadAI's own code it happened: the last frame under app/."""
    where = ""
    for path, line, func in _FRAME.findall(block):
        norm = path.replace("\\", "/")
        if "site-packages" in norm or "/app/" not in norm:
            continue
        rel = norm[norm.rindex("/app/") + 1:]
        where = f"{rel} line {line} ({func})"
    return where


_DB = re.compile(r'ServerSelectionTimeoutError|NetworkTimeout|AutoReconnect|ConnectionFailure|'
                 r'getaddrinfo failed|LifetimeTimeout|No replica set members|No primary available|'
                 r'mongodb\.net|_OperationCancelled|MongoClient|resolution lifetime expired|'
                 r'topology has no reachable servers', re.I)


def explain(level: str, message: str, error: Optional[Dict[str, str]], module: str) -> Dict[str, str]:
    """{summary, hint} in plain words for the common situations; the summary
    falls back to the cleaned message."""
    text = " ".join(filter(None, [message, (error or {}).get("type"), (error or {}).get("message")]))
    m = re.search(r'slow request (\w+) (\S+) -> (\d+) in (\d+) ?ms', text)
    if m:
        secs = int(m.group(4)) / 1000
        return {"summary": f"Slow page: {m.group(1)} {m.group(2)} took {secs:.1f} s",
                "hint": "Usually the database or an outside service answered slowly. An occasional one is "
                        "normal; many in a row mean the server or the database is overloaded or far away."}
    if "InvalidOperation" in text and "after close" in text:
        return {"summary": "A background task used the database while the app was shutting down",
                "hint": "Harmless during a restart. If it happens while the app is running, restart it."}
    if _DB.search(text):
        dns = re.search(r'getaddrinfo failed|LifetimeTimeout|resolution lifetime|Errno 11001', text, re.I)
        ssl = re.search(r'SSL handshake|handshake operation timed out', text, re.I)
        why = ("the server couldn't look up the database address (internet or DNS problem)" if dns else
               "the secure connection to the database timed out" if ssl else
               "the database didn't answer in time")
        return {"summary": f"Couldn't reach the database (MongoDB): {why}",
                "hint": "Check the server's internet connection, that the MongoDB Atlas cluster is running, and "
                        "that it allows this server's IP address (Atlas → Network Access). LeadAI retries by itself; "
                        "if it keeps happening, pages and searches will fail until the connection is back."}
    if re.search(r'ConnectionResetError|forcibly closed', text):
        return {"summary": "A network connection was closed unexpectedly",
                "hint": "Usually a short network blip; it's retried. Many of these point to an unstable connection."}
    if re.search(r'SMTPAuthenticationError|rejected SMTP_USER', text):
        return {"summary": "The mail server rejected the email login",
                "hint": "Check the email user name and password in Global Settings → Email & alerts."}
    if re.search(r'\bSMTP\w*Error|smtplib', text):
        return {"summary": "An email couldn't be sent",
                "hint": "See Global Settings → Email & alerts for the mail server status and send a test email."}
    if re.search(r'RESOURCE_EXHAUSTED|quota', text, re.I) and re.search(r'gemini|generativelanguage|\bAI\b', text, re.I):
        return {"summary": "Gemini (AI) quota reached",
                "hint": "AI analysis waits or falls back to keyword rules. See Super Admin → API limits."}
    if re.search(r'ApifyError|ScrapeError|\[Apify\]', text) or module.startswith("app.connectors"):
        detail = (error or {}).get("message") or _PREFIX.sub("", message)
        return {"summary": "Apify (scraping) problem: " + detail[:160],
                "hint": "See Super Admin → API limits and Apify jobs for the run and what Apify said."}
    if re.search(r'httpx\.(ConnectTimeout|ReadTimeout|ConnectError|RemoteProtocolError)|ReadTimeout|ConnectTimeout', text):
        return {"summary": "An outside service didn't answer (connection problem)",
                "hint": "Usually temporary. If it repeats, check the service's status and the server's internet."}
    if re.search(r'Session \w+ idle .*expired', message):
        return {"summary": "A signed-in session ended after being idle", "hint": ""}
    if error:
        return {"summary": f"Unexpected error: {error['type'].split('.')[-1]}"
                           + (f" — {error['message'][:140]}" if error.get("message") else ""),
                "hint": "Something in the app failed unexpectedly. 'Where' shows the place in LeadAI's code; "
                        "send the full details to your developer if it keeps happening."}
    return {"summary": extract_event(message), "hint": ""}


# ── Main parser ───────────────────────────────────────────────────────────────

def parse_log_line(line: str) -> Dict[str, Any]:
    """Parse a raw log line into a structured dict.

    Never raises exceptions — malformed lines get a fallback structure.
    """
    try:
        raw = line.rstrip("\n")
        if not raw:
            return {"raw": "", "level": "EMPTY", "message": ""}

        first = raw.split("\n", 1)[0]
        # a leftover piece of a traceback whose first line is older than the
        # loaded lines (the file is read from the end)
        tail_error = None if is_entry_start(first) else final_exception("\n" + raw)
        partial = bool(tail_error) or (not is_entry_start(first) and (
            first[:1].isspace() or first.startswith(("Traceback", "During handling", "The above exception"))))
        level = extract_level(first)
        timestamp = extract_timestamp(first)
        module = extract_module(first)

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
        error = None
        if "\n" in raw:
            parts = raw.split("\n", 1)
            result["message"] = redact_secrets(parts[0] if not m and not timestamp else message.split("\n", 1)[0])
            result["stack_trace"] = redact_secrets(parts[1])
            error = final_exception(raw)
            if error:
                result["error_type"] = error["type"]
                result["error_message"] = redact_secrets(error["message"])
            where = app_location(raw)
            if where:
                result["where"] = where

        # the beginning of this entry is older than the loaded lines
        error = error or tail_error
        if tail_error and not result.get("error_type"):
            result["error_type"] = tail_error["type"]
            result["error_message"] = redact_secrets(tail_error["message"])
        if partial:
            if level == "UNKNOWN":
                result["level"] = "ERROR" if error else "INFO"
            result["partial"] = True

        info = explain(result["level"], result["message"], error, module)
        summary = info["summary"] or result["event"]
        if partial:
            summary += " (start of this entry is older than the lines loaded)"
        result["summary"] = redact_secrets(summary)
        if info["hint"]:
            result["hint"] = info["hint"]
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
