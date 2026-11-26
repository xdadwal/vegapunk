import time

import pytest

from vegapunk.ai.redact import limit_text, redact, scrub_secret

PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEAu1SU1LfVLPHCozMxH2Mo4lgOEePzNm0tRgeLezV6ffAt0gun\n"
    "VTLw7onLRnrq0/IzW7yWR7QkrmBL7jTKEn5u+qKhbwKfBstIs+bMY2Zkp18gnTxK\n"
    "-----END RSA PRIVATE KEY-----"
)


@pytest.mark.parametrize(
    "text,gone",
    [
        ("password=hunter2 next", "hunter2"),
        ("PASSWORD: hunter2", "hunter2"),
        ('{"api_key": "abc123"}', "abc123"),
        ("export DB_PASSWORD=s3cret!", "s3cret!"),
        ("token=abc&x=1", "abc"),
        ("Authorization: Bearer abcdef123456", "abcdef123456"),
        ("Authorization: Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA=="),
        ("auth Bearer abcdef123456 end", "abcdef123456"),
        ("postgres://app:pw0rd@db:5432/x", "pw0rd"),
        ("redis://:pw0rd@host:6379", "pw0rd"),
        ("jwt eyJhbGciOi.eyJzdWIiOiIx.SflKxwRJSMeKKF2QT4", "eyJhbGciOi"),
        ("key sk-abcdefghijklmnopqrstuv", "sk-abcdefghijklmnopqrstuv"),
        ("ghp_" + "a" * 36, "a" * 36),
        ("xoxb-1234567890-abcdefghij", "1234567890-abcdefghij"),
        ("AKIAABCDEFGHIJKLMNOP", "AKIAABCDEFGHIJKLMNOP"),
    ],
)
def test_secrets_are_removed(text, gone):
    out = redact(text)
    assert gone not in out
    assert "[redacted]" in out


@pytest.mark.parametrize(
    "text,gone",
    [
        ('password="two words" x', "words"),
        ("pass_token='a b c' y", "a b c"),
        ('{"client_secret": "has spaces in it"}', "spaces"),
    ],
)
def test_quoted_values_with_spaces(text, gone):
    assert gone not in redact(text)


def test_private_key_block_is_removed_across_lines():
    out = redact(f"before\n{PEM}\nafter")
    assert (
        "MIIEow" not in out and "VTLw7o" not in out and "BEGIN" not in out and "END RSA" not in out
    )
    assert out.startswith("before\n") and out.endswith("\nafter")


def test_unterminated_private_key_block_redacts_to_the_end():
    out = redact("ok\n-----BEGIN PRIVATE KEY-----\nAAAA\nBBBB")
    assert "AAAA" not in out and "BBBB" not in out and out.startswith("ok\n")


def test_ordinary_text_is_untouched():
    for text in [
        "GET /health 200 12ms",
        "listening on port 8080",
        "tokens per second: 40",
        "user logged in",
        "2026-01-01T12:00:00Z INFO started",
        "basic configuration is loaded",
        "see http://host:8080/path for docs",
        "the secretary arrived",
    ]:
        assert redact(text) == text, text


def test_the_key_name_survives_so_the_line_still_reads():
    assert redact("password=hunter2") == "password=[redacted]"
    assert redact('"api_key": "abc123"') == '"api_key": "[redacted]"'


def test_redaction_is_repeatable():
    text = f"password=hunter2\n{PEM}\npostgres://a:b@h/x Bearer abcdef123456"
    assert redact(redact(text)) == redact(text)


def test_redaction_is_fast_on_hostile_input():
    long_line = ("A" * 16_000) + "password"
    many_lines = "\n".join("x" * 2_000 for _ in range(300))
    keyword_soup = "password" * 4_000
    for text in (
        long_line,
        many_lines,
        keyword_soup,
        "password=" * 3_000,
        "Bearer " + "a" * 16_000,
    ):
        start = time.perf_counter()
        redact(text)
        assert time.perf_counter() - start < 1.0


def test_very_long_lines_are_cut_before_any_pattern_runs():
    out = redact("x" * 5_000 + "\nshort")
    first, second = out.split("\n")
    assert len(first) == 2_000 and second == "short"


def test_limit_text_leaves_short_text_alone():
    assert limit_text("hello", 100) == "hello"


def test_limit_text_keeps_the_end_and_says_so():
    out = limit_text("0123456789" * 10, 60)
    assert out.endswith("0123456789")
    assert out.startswith("[earlier output omitted: ")
    assert "characters]" in out.splitlines()[0]


@pytest.mark.parametrize(
    "size,limit", [(101, 100), (5_000, 100), (100_000, 8_000), (9_999, 8_000), (50, 20)]
)
def test_limit_text_result_never_exceeds_the_limit_including_the_note(size, limit):
    assert len(limit_text("x" * size, limit)) <= limit


def test_scrub_secret_removes_the_key_its_prefix_secrets_and_cuts():
    key = "sk-proj-abcdefghijklmnop"
    marker = "<redacted sk-pro… len=24>"
    text = f"bad key {key}; marker {marker}; password=hunter2 " + "z" * 500
    out = scrub_secret(text, key)
    assert key not in out and "sk-pro" not in out and "hunter2" not in out
    assert len(out) <= 300


def test_scrub_secret_without_a_secret_still_redacts_and_cuts():
    out = scrub_secret("password=hunter2 " + "z" * 500, None)
    assert "hunter2" not in out and len(out) <= 300


def test_scrub_secret_does_not_mangle_text_for_a_short_secret():
    assert scrub_secret("the quick fox", "abc") == "the quick fox"
