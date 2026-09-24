"""
Tests for app.log_parser — structured log parser, secret redaction, multiline grouping.

Covers:
- Standard Python logging format parsing
- Bracketed prefix parsing
- Malformed / empty / unknown lines
- Level extraction (all levels + unknown)
- Module extraction
- Timestamp extraction
- Source classification (module-based + bracket-based)
- Event extraction and truncation
- Error type extraction
- Structured field extraction (run_id, request_id, endpoint, etc.)
- Multiline grouping (stack traces)
- Secret redaction (text + dict)
- Unicode content handling
- parse_log_lines integration
"""
import pytest
from app.log_parser import (
    parse_log_line,
    parse_log_lines,
    extract_level,
    extract_module,
    extract_timestamp,
    classify_source,
    extract_event,
    extract_error_type,
    extract_structured_fields,
    group_multiline,
    redact_secrets,
    redact_dict,
    LEVELS,
    LEVEL_SET,
)


# ── extract_level ─────────────────────────────────────────────────────────────

class TestExtractLevel:
    def test_standard_debug(self):
        assert extract_level("14:32:01 DEBUG  app.main: Server started") == "DEBUG"

    def test_standard_info(self):
        assert extract_level("14:32:01 INFO   app.main: Server started") == "INFO"

    def test_standard_warning(self):
        assert extract_level("14:32:01 WARNING  app.db.mongo: Slow query") == "WARNING"

    def test_standard_error(self):
        assert extract_level("14:32:01 ERROR  app.pipeline: Processing failed") == "ERROR"

    def test_standard_critical(self):
        assert extract_level("14:32:01 CRITICAL  app.main: Fatal crash") == "CRITICAL"

    def test_bracketed_level(self):
        assert extract_level("[ERROR] Something broke") == "ERROR"

    def test_bracketed_level_in_message(self):
        assert extract_level("2024-01-01 [WARNING] Rate limit hit") == "WARNING"

    def test_unknown_format(self):
        assert extract_level("just some random text") == "UNKNOWN"

    def test_empty_line(self):
        assert extract_level("") == "UNKNOWN"

    def test_all_levels_defined(self):
        assert LEVELS == ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
        assert LEVEL_SET == set(LEVELS)


# ── extract_module ────────────────────────────────────────────────────────────

class TestExtractModule:
    def test_standard_module(self):
        assert extract_module("14:32:01 INFO   app.main: Started") == "app.main"

    def test_nested_module(self):
        assert extract_module("14:32:01 ERROR  app.api.routes.admin: Failed") == "app.api.routes.admin"

    def test_no_module(self):
        assert extract_module("[ERROR] Something broke") == ""

    def test_no_module_random_text(self):
        assert extract_module("random text") == ""


# ── extract_timestamp ─────────────────────────────────────────────────────────

class TestExtractTimestamp:
    def test_standard_timestamp(self):
        assert extract_timestamp("14:32:01 INFO  app.main: Started") == "14:32:01"

    def test_midnight(self):
        assert extract_timestamp("00:00:00 INFO  app.main: Started") == "00:00:00"

    def test_no_timestamp(self):
        assert extract_timestamp("[ERROR] Something") == ""

    def test_no_timestamp_random(self):
        assert extract_timestamp("random text") == ""


# ── classify_source ───────────────────────────────────────────────────────────

class TestClassifySource:
    def test_database_module(self):
        assert classify_source("app.db.mongo", "") == "Database"

    def test_database_prefix(self):
        assert classify_source("app.db", "") == "Database"

    def test_apify_module(self):
        assert classify_source("app.connectors.apify_connector", "") == "Apify"

    def test_apify_bracket(self):
        assert classify_source("", "[Apify][REQ] Starting actor") == "Apify"

    def test_search_module(self):
        assert classify_source("app.social.url_search", "") == "Search"

    def test_search_bracket(self):
        assert classify_source("", "[URL SEARCH] Searching") == "Search"

    def test_ai_module(self):
        assert classify_source("app.pipeline.comment_ai", "") == "AI"

    def test_ai_bracket(self):
        assert classify_source("", "[Gemini] Analyzing") == "AI"

    def test_auth_module(self):
        assert classify_source("app.auth.service", "") == "Authentication"

    def test_system_module(self):
        assert classify_source("app.admin.settings", "") == "System"

    def test_api_module(self):
        assert classify_source("app.api.routes.admin", "") == "API"

    def test_unknown_module(self):
        assert classify_source("some.random.module", "") == "Application"

    def test_empty_module_with_message(self):
        assert classify_source("", "some message") == "Application"

    def test_facebook_bracket(self):
        assert classify_source("", "[Facebook] Scraping page") == "Search"

    def test_instagram_bracket(self):
        assert classify_source("", "[Instagram] Fetching posts") == "Search"

    def test_youtube_bracket(self):
        assert classify_source("", "[YouTube] Getting comments") == "Search"

    def test_commentai_bracket(self):
        assert classify_source("", "[CommentAI] Generating reply") == "AI"

    def test_commentfilter_bracket(self):
        assert classify_source("", "[CommentFilter] Filtering") == "AI"


# ── extract_event ─────────────────────────────────────────────────────────────

class TestExtractEvent:
    def test_simple_message(self):
        assert extract_event("Server started successfully") == "Server started successfully"

    def test_bracketed_prefix_removed(self):
        assert extract_event("[Apify][REQ] Starting actor run") == "Starting actor run"

    def test_truncation(self):
        long_msg = "x" * 200
        result = extract_event(long_msg)
        assert len(result) == 120
        assert result.endswith("...")

    def test_first_sentence(self):
        assert extract_event("First sentence. Second sentence") == "First sentence"

    def test_empty_message(self):
        assert extract_event("") == ""


# ── extract_error_type ────────────────────────────────────────────────────────

class TestExtractErrorType:
    def test_timeout_error(self):
        assert extract_error_type("ConnectionTimeoutError occurred") == "ConnectionTimeoutError"

    def test_value_error(self):
        assert extract_error_type("ValueError: invalid input") == "ValueError"

    def test_exception(self):
        assert extract_error_type("RuntimeError exception raised") == "RuntimeError"

    def test_no_error(self):
        assert extract_error_type("All good") == ""

    def test_http_error(self):
        assert extract_error_type("HTTPError 404") == "HTTPError"


# ── extract_structured_fields ─────────────────────────────────────────────────

class TestExtractStructuredFields:
    def test_run_id(self):
        fields = extract_structured_fields("run_id=abc123 completed")
        assert fields["run_id"] == "abc123"

    def test_request_id(self):
        fields = extract_structured_fields("request-id=req-456 processed")
        assert fields["request_id"] == "req-456"

    def test_job_id(self):
        fields = extract_structured_fields("job_id=job-789 done")
        assert fields["job_id"] == "job-789"

    def test_endpoint_and_method(self):
        fields = extract_structured_fields("GET /api/admin/jobs status=200")
        assert fields["http_method"] == "GET"
        assert fields["endpoint"] == "/api/admin/jobs"
        assert fields["http_status"] == 200

    def test_duration(self):
        fields = extract_structured_fields("duration=123.45 ms")
        assert fields["duration"] == "123.45 ms"

    def test_collection(self):
        fields = extract_structured_fields("collection=leads")
        assert fields["collection"] == "leads"

    def test_platform_facebook(self):
        fields = extract_structured_fields("Scraping facebook page")
        assert fields["platform"] == "Facebook"

    def test_platform_instagram(self):
        fields = extract_structured_fields("Fetching instagram posts")
        assert fields["platform"] == "Instagram"

    def test_platform_youtube(self):
        fields = extract_structured_fields("Getting youtube comments")
        assert fields["platform"] == "Youtube"

    def test_platform_linkedin(self):
        fields = extract_structured_fields("Searching linkedin profiles")
        assert fields["platform"] == "Linkedin"

    def test_error_code(self):
        fields = extract_structured_fields("error_code=ERR_TIMEOUT")
        assert fields["error_code"] == "ERR_TIMEOUT"

    def test_no_fields(self):
        fields = extract_structured_fields("simple message")
        assert fields == {}

    def test_multiple_fields(self):
        fields = extract_structured_fields("GET /api/search status=200 duration=45.2 ms run_id=run1")
        assert fields["http_method"] == "GET"
        assert fields["endpoint"] == "/api/search"
        assert fields["http_status"] == 200
        assert fields["duration"] == "45.2 ms"
        assert fields["run_id"] == "run1"


# ── redact_secrets ────────────────────────────────────────────────────────────

class TestRedactSecrets:
    def test_apify_token(self):
        redacted = redact_secrets('APIFY_API_TOKEN="apify_token_123abc"')
        assert "apify_token_123abc" not in redacted
        assert "********" in redacted

    def test_gemini_key(self):
        redacted = redact_secrets("GEMINI_API_KEY=AIzaSyA1234567890")
        assert "AIzaSyA1234567890" not in redacted
        assert "********" in redacted

    def test_session_secret(self):
        redacted = redact_secrets('SESSION_SECRET: mysupersecretkey123')
        assert "mysupersecretkey123" not in redacted
        assert "********" in redacted

    def test_password(self):
        redacted = redact_secrets("password=admin123")
        assert "admin123" not in redacted
        assert "********" in redacted

    def test_token(self):
        redacted = redact_secrets("token=bearer_abc123")
        assert "bearer_abc123" not in redacted
        assert "********" in redacted

    def test_mongodb_uri(self):
        redacted = redact_secrets("mongodb://admin:secret123@localhost:27017")
        assert "secret123" not in redacted
        assert "********" in redacted

    def test_bearer_auth(self):
        redacted = redact_secrets("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9")
        assert "eyJhbGciOiJIUzI1NiJ9" not in redacted
        assert "********" in redacted

    def test_no_secrets_unchanged(self):
        text = "Normal log message with no secrets"
        assert redact_secrets(text) == text

    def test_multiple_secrets(self):
        text = 'APIFY_API_TOKEN=abc123 GEMINI_API_KEY=xyz789'
        redacted = redact_secrets(text)
        assert "abc123" not in redacted
        assert "xyz789" not in redacted

    def test_admin_password_hash(self):
        redacted = redact_secrets('ADMIN_PASSWORD_HASH="$2b$12$abc"')
        assert "$2b$12$abc" not in redacted


# ── redact_dict ───────────────────────────────────────────────────────────────

class TestRedactDict:
    def test_known_key(self):
        d = {"token": "secret123", "name": "test"}
        result = redact_dict(d)
        assert result["token"] == "********"
        assert result["name"] == "test"

    def test_password_key(self):
        d = {"password": "hunter2", "email": "test@test.com"}
        result = redact_dict(d)
        assert result["password"] == "********"
        assert result["email"] == "test@test.com"

    def test_nested_dict(self):
        d = {"user": {"password": "secret", "name": "John"}}
        result = redact_dict(d)
        assert result["user"]["password"] == "********"
        assert result["user"]["name"] == "John"

    def test_list_of_dicts(self):
        d = {"items": [{"token": "abc"}, {"name": "test"}]}
        result = redact_dict(d)
        assert result["items"][0]["token"] == "********"
        assert result["items"][1]["name"] == "test"

    def test_partial_key_match(self):
        d = {"new_password_hash": "$2b$12$abc", "admin_password_hash": "$2b$12$def"}
        result = redact_dict(d)
        assert result["new_password_hash"] == "********"
        assert result["admin_password_hash"] == "********"

    def test_unknown_keys_unchanged(self):
        d = {"username": "admin", "role": "super_admin"}
        result = redact_dict(d)
        assert result == d

    def test_empty_dict(self):
        assert redact_dict({}) == {}

    def test_session_secret_key(self):
        d = {"session_secret": "my-secret-key"}
        result = redact_dict(d)
        assert result["session_secret"] == "********"


# ── group_multiline ───────────────────────────────────────────────────────────

class TestGroupMultiline:
    def test_single_line(self):
        lines = ["14:32:01 INFO  app.main: Started"]
        result = group_multiline(lines)
        assert result == ["14:32:01 INFO  app.main: Started"]

    def test_error_with_traceback(self):
        lines = [
            "14:32:01 ERROR  app.pipeline: Processing failed",
            "Traceback (most recent call last):",
            '  File "app/pipeline.py", line 42, in process',
            "    raise ValueError('bad input')",
        ]
        result = group_multiline(lines)
        assert len(result) == 1
        assert "ERROR" in result[0]
        assert "Traceback" in result[0]
        assert "raise ValueError" in result[0]

    def test_warning_with_continuation(self):
        lines = [
            "14:32:01 WARNING  app.db: Slow query detected",
            "    Query: SELECT * FROM leads",
            "    Duration: 2500ms",
        ]
        result = group_multiline(lines)
        assert len(result) == 1
        assert "WARNING" in result[0]
        assert "Slow query" in result[0]

    def test_multiple_errors(self):
        lines = [
            "14:32:01 ERROR  app.first: Error one",
            "    at line 10",
            "14:32:02 ERROR  app.second: Error two",
            "    at line 20",
        ]
        result = group_multiline(lines)
        assert len(result) == 2
        assert "Error one" in result[0]
        assert "Error two" in result[1]

    def test_error_then_normal_line(self):
        lines = [
            "14:32:01 ERROR  app.pipeline: Failed",
            "14:32:02 INFO  app.main: Continuing",
        ]
        result = group_multiline(lines)
        assert len(result) == 2

    def test_empty_lines_between(self):
        lines = [
            "14:32:01 INFO  app.main: Started",
            "",
            "14:32:02 INFO  app.main: Running",
        ]
        result = group_multiline(lines)
        assert len(result) == 2

    def test_only_empty_lines(self):
        lines = ["", "", ""]
        result = group_multiline(lines)
        assert result == []

    def test_traceback_standalone(self):
        lines = [
            "14:32:01 ERROR  app.pipeline: Failed",
            "Traceback (most recent call last):",
            "  File \"test.py\", line 1",
            "    pass",
        ]
        result = group_multiline(lines)
        assert len(result) == 1
        assert "Traceback" in result[0]

    def test_info_not_grouped(self):
        lines = [
            "14:32:01 INFO  app.main: Step 1",
            "14:32:02 INFO  app.main: Step 2",
        ]
        result = group_multiline(lines)
        assert len(result) == 2

    def test_critical_groups(self):
        lines = [
            "14:32:01 CRITICAL  app.main: Fatal",
            "    Stack trace here",
            "    More details",
        ]
        result = group_multiline(lines)
        assert len(result) == 1
        assert "CRITICAL" in result[0]


# ── parse_log_line ────────────────────────────────────────────────────────────

class TestParseLogLine:
    def test_standard_info_line(self):
        result = parse_log_line("14:32:01 INFO   app.main: Server started on port 8000")
        assert result["timestamp"] == "14:32:01"
        assert result["level"] == "INFO"
        assert result["module"] == "app.main"
        assert result["source"] == "System"
        assert "Server started" in result["message"]

    def test_standard_error_line(self):
        result = parse_log_line("14:32:01 ERROR  app.pipeline: Processing failed")
        assert result["level"] == "ERROR"
        assert result["module"] == "app.pipeline"
        assert result["source"] == "AI"

    def test_standard_warning_line(self):
        result = parse_log_line("14:32:01 WARNING  app.db.mongo: Slow query")
        assert result["level"] == "WARNING"
        assert result["source"] == "Database"

    def test_standard_debug_line(self):
        result = parse_log_line("14:32:01 DEBUG  app.auth: Token validated")
        assert result["level"] == "DEBUG"
        assert result["source"] == "Authentication"

    def test_bracketed_apify_line(self):
        result = parse_log_line("[Apify][REQ] Starting actor run")
        assert result["level"] == "UNKNOWN"
        assert result["source"] == "Apify"

    def test_bracketed_url_search(self):
        result = parse_log_line("[URL SEARCH] Searching facebook")
        assert result["source"] == "Search"
        assert "facebook" in result["message"].lower()

    def test_empty_line(self):
        result = parse_log_line("")
        assert result["level"] == "EMPTY"
        assert result["message"] == ""

    def test_malformed_line(self):
        result = parse_log_line("totally random garbage with no structure")
        assert result["level"] == "UNKNOWN"
        assert result["source"] == "Application"

    def test_multiline_block(self):
        block = "14:32:01 ERROR  app.pipeline: Failed\nTraceback:\n  at line 10"
        result = parse_log_line(block)
        assert result["level"] == "ERROR"
        assert "stack_trace" in result
        assert "Traceback" in result["stack_trace"]

    def test_secret_redacted_in_message(self):
        result = parse_log_line('14:32:01 INFO  app.main: APIFY_API_TOKEN=secret123')
        assert "secret123" not in result["message"]
        assert "********" in result["message"]

    def test_secret_redacted_in_raw(self):
        result = parse_log_line('14:32:01 INFO  app.main: password=hunter2')
        assert "hunter2" not in result["raw"]

    def test_structured_fields_extracted(self):
        result = parse_log_line("14:32:01 INFO  app.api: GET /api/admin/jobs status=200 run_id=run123")
        assert result["run_id"] == "run123"
        assert result["http_method"] == "GET"
        assert result["endpoint"] == "/api/admin/jobs"
        assert result["http_status"] == 200

    def test_error_type_extracted(self):
        result = parse_log_line("14:32:01 ERROR  app.pipeline: ConnectionTimeoutError occurred")
        assert result["error_type"] == "ConnectionTimeoutError"

    def test_event_extracted(self):
        result = parse_log_line("14:32:01 INFO  app.main: Server started successfully on port 8000")
        assert "Server started" in result["event"]

    def test_never_raises(self):
        # Extremely malformed input should not raise
        result = parse_log_line("\x00\x01\x02\x03")
        assert "level" in result
        assert "message" in result

    def test_result_has_required_keys(self):
        result = parse_log_line("14:32:01 INFO  app.main: test")
        for key in ("timestamp", "level", "module", "event", "message", "source", "raw"):
            assert key in result, f"Missing key: {key}"


# ── parse_log_lines ───────────────────────────────────────────────────────────

class TestParseLogLines:
    def test_simple_lines(self):
        lines = [
            "14:32:01 INFO  app.main: Started",
            "14:32:02 DEBUG  app.auth: Token validated",
        ]
        results = parse_log_lines(lines)
        assert len(results) == 2
        assert results[0]["level"] == "INFO"
        assert results[1]["level"] == "DEBUG"

    def test_multiline_grouping(self):
        lines = [
            "14:32:01 ERROR  app.pipeline: Failed",
            "Traceback (most recent call last):",
            '  File "test.py", line 1',
            "    raise ValueError",
            "14:32:02 INFO  app.main: Continuing",
        ]
        results = parse_log_lines(lines)
        assert len(results) == 2
        assert results[0]["level"] == "ERROR"
        assert "stack_trace" in results[0]
        assert results[1]["level"] == "INFO"

    def test_empty_list(self):
        results = parse_log_lines([])
        assert results == []

    def test_only_empty_lines(self):
        results = parse_log_lines(["", "", ""])
        assert results == []


# ── Unicode handling ──────────────────────────────────────────────────────────

class TestUnicode:
    def test_unicode_message(self):
        result = parse_log_line("14:32:01 INFO  app.main: 用户登录成功 ログイン完了")
        assert result["level"] == "INFO"
        assert "用户登录成功" in result["message"]

    def test_emoji_in_message(self):
        result = parse_log_line("14:32:01 INFO  app.main: ✅ All checks passed")
        assert result["level"] == "INFO"
        assert "✅" in result["message"]

    def test_unicode_module_name(self):
        result = parse_log_line("14:32:01 INFO  app.日本語モジュール: test")
        assert result["module"] == "app.日本語モジュール"

    def test_unicode_in_redaction(self):
        text = "password=秘密のパスワード"
        redacted = redact_secrets(text)
        assert "秘密のパスワード" not in redacted


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_very_long_line(self):
        long_msg = "14:32:01 INFO  app.main: " + "x" * 10000
        result = parse_log_line(long_msg)
        assert result["level"] == "INFO"
        assert len(result["message"]) > 0

    def test_special_characters(self):
        result = parse_log_line('14:32:01 INFO  app.main: <script>alert("xss")</script>')
        assert "<script>" in result["message"]

    def test_newline_in_line(self):
        result = parse_log_line("14:32:01 ERROR  app.main: bad\nTraceback:")
        assert result["level"] == "ERROR"

    def test_tab_characters(self):
        result = parse_log_line("14:32:01 INFO  app.main:\tindented message")
        assert result["level"] == "INFO"

    def test_consecutive_spaces(self):
        result = parse_log_line("14:32:01 INFO   app.main: extra   spaces")
        assert result["level"] == "INFO"

    def test_level_case_variations(self):
        # Only exact uppercase matches should work
        assert extract_level("14:32:01 info  app.main: test") == "UNKNOWN"
        assert extract_level("14:32:01 INFO  app.main: test") == "INFO"
