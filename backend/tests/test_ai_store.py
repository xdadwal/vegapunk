from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet

from vegapunk.ai.store import (
    KEEP,
    add_conversation,
    clear_ai,
    conversation_for_finding,
    conversation_ids_by_finding,
    delete_conversation,
    get_conversation,
    list_conversations,
    load_ai,
    make_title,
    mark_key_rejected,
    save_ai,
    touch_conversation,
)

K = Fernet.generate_key()
NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
URL = "https://api.example.com/v1"
SECRET = "sk-SECRETSECRET1234"


def save(conn, key=K, **over):
    args = {"base_url": URL, "model": "gpt-x", "instructions": "", "api_key": SECRET, "now": NOW}
    args.update(over)
    save_ai(conn, key, **args)


def test_nothing_saved_loads_none(conn):
    assert load_ai(conn, K) is None


def test_save_and_load_round_trip_with_the_key_encrypted_in_the_database(conn):
    save(conn, instructions="NAS runs the media stack")
    raw = conn.execute("SELECT * FROM ai_settings").fetchone()
    assert SECRET not in "".join(str(v) for v in tuple(raw))
    loaded = load_ai(conn, K)
    assert loaded is not None
    assert (loaded.config.base_url, loaded.config.model) == (URL, "gpt-x")
    assert loaded.config.instructions == "NAS runs the media stack"
    assert loaded.config.api_key == SECRET
    assert (loaded.key_saved, loaded.key_hint, loaded.key_unreadable) == (True, "…1234", False)
    assert loaded.key_rejected_at is None


def test_keep_leaves_the_key_alone_and_none_removes_it(conn):
    save(conn)
    save(conn, api_key=KEEP, model="gpt-y")
    kept = load_ai(conn, K)
    assert kept.config.api_key == SECRET and kept.config.model == "gpt-y"
    save(conn, api_key=None)
    removed = load_ai(conn, K)
    assert (
        removed.config.api_key is None and removed.key_saved is False and removed.key_hint is None
    )


def test_keep_with_nothing_saved_means_no_key(conn):
    save(conn, api_key=KEEP)
    assert load_ai(conn, K).config.api_key is None


def test_a_new_key_replaces_the_old_one(conn):
    save(conn)
    save(conn, api_key="sk-another-key-9999")
    assert load_ai(conn, K).config.api_key == "sk-another-key-9999"
    assert load_ai(conn, K).key_hint == "…9999"


def test_a_saved_key_is_not_kept_for_a_different_base_url(conn):
    save(conn)
    with pytest.raises(ValueError, match="enter the API key again, or remove the saved key"):
        save(conn, api_key=KEEP, base_url="https://other.example.com/v1")
    assert load_ai(conn, K).config.base_url == URL  # nothing changed


def test_a_different_base_url_is_fine_with_a_new_key_or_no_key(conn):
    save(conn)
    save(conn, base_url="https://other.example.com/v1", api_key="sk-brand-new-key-0001")
    assert load_ai(conn, K).config.base_url == "https://other.example.com/v1"
    save(conn, base_url="http://localhost:11434/v1", api_key=None)
    assert load_ai(conn, K).config.api_key is None


def test_the_same_base_url_with_a_trailing_slash_keeps_the_key(conn):
    save(conn)
    save(conn, api_key=KEEP, base_url=URL + "/")
    assert load_ai(conn, K).config.api_key == SECRET


def test_saving_clears_a_rejected_key_mark(conn):
    save(conn)
    mark_key_rejected(conn, NOW)
    assert load_ai(conn, K).key_rejected_at == NOW
    save(conn, api_key=KEEP)
    assert load_ai(conn, K).key_rejected_at is None


def test_mark_key_rejected_is_a_no_op_with_nothing_saved(conn):
    mark_key_rejected(conn, NOW)
    assert load_ai(conn, K) is None


@pytest.mark.parametrize(
    "bad",
    [
        "ftp://x",
        "x",
        "",
        "http://",
        "https://",
        "http://a b",
        "https://x/" + "a" * 2_100,
        "javascript:alert(1)",
    ],
)
def test_bad_base_urls_are_rejected(conn, bad):
    with pytest.raises(ValueError, match="Base URL"):
        save(conn, base_url=bad)
    assert load_ai(conn, K) is None


def test_base_url_is_trimmed_and_loses_trailing_slashes(conn):
    save(conn, base_url="  https://api.example.com/v1//  ")
    assert load_ai(conn, K).config.base_url == "https://api.example.com/v1"


def test_model_instructions_and_key_limits(conn):
    for kwargs, text in [
        ({"model": "  "}, "model"),
        ({"model": "m" * 201}, "model"),
        ({"instructions": "i" * 4_001}, "Instructions"),
        ({"api_key": "k" * 501}, "API key"),
        ({"api_key": "has a space"}, "API key"),
        ({"api_key": "line\nbreak"}, "API key"),
        ({"api_key": "   "}, "API key"),
    ]:
        with pytest.raises(ValueError, match=text):
            save(conn, **kwargs)
    assert load_ai(conn, K) is None


def test_the_key_is_stripped_and_the_model_is_trimmed(conn):
    save(conn, api_key="  sk-padded-key-0000  ", model="  gpt-x  ")
    loaded = load_ai(conn, K)
    assert loaded.config.api_key == "sk-padded-key-0000" and loaded.config.model == "gpt-x"


def test_unreadable_key_is_reported_not_raised(conn):
    save(conn)
    loaded = load_ai(conn, Fernet.generate_key())
    assert loaded.key_unreadable is True and loaded.config.api_key is None
    assert loaded.key_saved is True and loaded.key_hint == "…1234"


def test_clear_removes_everything(conn):
    save(conn)
    clear_ai(conn)
    assert load_ai(conn, K) is None
    clear_ai(conn)  # repeatable


def test_a_failed_save_changes_nothing(conn):
    save(conn)
    with pytest.raises(ValueError):
        save(conn, model="")
    assert load_ai(conn, K).config.model == "gpt-x"


# ---- conversations
def test_conversation_list_is_newest_first(conn):
    add_conversation(conn, "a" * 32, "first", NOW)
    add_conversation(conn, "b" * 32, "second", NOW + timedelta(minutes=1))
    touch_conversation(conn, "a" * 32, NOW + timedelta(minutes=5))
    items = list_conversations(conn)
    assert [i["title"] for i in items] == ["first", "second"]
    assert items[0]["updated_at"] == "2026-01-01T12:05:00Z"
    assert set(items[0]) == {"id", "title", "updated_at", "finding_id", "finding_title"}
    assert (items[0]["finding_id"], items[0]["finding_title"]) == (None, None)


def test_get_and_delete_conversation(conn):
    add_conversation(conn, "a" * 32, "t", NOW)
    assert get_conversation(conn, "a" * 32)["title"] == "t"
    assert get_conversation(conn, "z" * 32) is None
    delete_conversation(conn, "a" * 32)
    assert get_conversation(conn, "a" * 32) is None
    delete_conversation(conn, "a" * 32)  # repeatable


def test_list_is_limited(conn):
    for i in range(5):
        add_conversation(conn, f"{i:032d}", f"t{i}", NOW + timedelta(minutes=i))
    assert len(list_conversations(conn, limit=3)) == 3


@pytest.mark.parametrize(
    "message,title",
    [
        ("", "New conversation"),
        ("   \n\t ", "New conversation"),
        ("Is anything wrong?", "Is anything wrong?"),
        ("line one\nline   two", "line one line two"),
        ("<b>x</b> & <script>", "<b>x</b> & <script>"),
    ],
)
def test_make_title(message, title):
    assert make_title(message) == title


def test_make_title_cuts_long_text_with_an_ellipsis():
    title = make_title("word " * 100)
    assert len(title) == 60 and title.endswith("…")


import pytest  # noqa: E402


@pytest.mark.parametrize(
    "url",
    [
        "http://h\x00/v1",
        "http://ho\u200bst/v1",
        "http://host:99999/v1",
        "http://?",
        "http://host/v1?x=1",
        "http://host/v1#top",
        "http://user:pw@host/v1",
        "http://@host/v1",
        "http:///v1",
    ],
)
def test_base_urls_that_could_not_work_are_refused(url):
    from vegapunk.ai.store import normalise_base_url

    with pytest.raises(ValueError):
        normalise_base_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434/v1/",
        "https://api.openai.com/v1",
        "http://[::1]:8080/v1",
        "http://host.docker.internal:9101/v1",
    ],
)
def test_ordinary_base_urls_are_kept(url):
    from vegapunk.ai.store import normalise_base_url

    assert normalise_base_url(url) == url.rstrip("/")


def test_the_api_key_is_not_in_the_repr_of_the_settings():
    from vegapunk.ai.store import AiConfig, LoadedAi

    secret = "plainkeyABCDEFGHIJKLMNOP"
    config = AiConfig(base_url="https://x.example/v1", model="m", instructions="", api_key=secret)
    loaded = LoadedAi(config, True, "plai…MNOP", None, False)
    assert secret not in repr(config) and secret not in str(config)
    assert secret not in repr(loaded)
    assert config == AiConfig("https://x.example/v1", "m", "", secret)  # equality still holds
    assert config != AiConfig("https://x.example/v1", "m", "", "another-key-0000000")


def _finding(conn, title="Web keeps restarting"):
    from vegapunk.watch import findings

    draft = findings.FindingDraft("web", title, "Diagnosis.", "Fix.", ())
    return findings.upsert_finding(conn, draft, key=f"k-{title}", trigger="watch", now=NOW)


def test_a_conversation_can_be_linked_to_a_finding(conn):
    fid = _finding(conn)
    add_conversation(conn, "a" * 32, "About: Web keeps restarting", NOW, finding_id=fid)
    add_conversation(conn, "b" * 32, "Plain", NOW + timedelta(minutes=1))
    assert get_conversation(conn, "a" * 32)["finding_id"] == fid
    assert get_conversation(conn, "b" * 32)["finding_id"] is None
    assert conversation_for_finding(conn, fid)["id"] == "a" * 32
    assert conversation_for_finding(conn, fid + 1) is None
    items = {i["id"]: i for i in list_conversations(conn)}
    assert (items["a" * 32]["finding_id"], items["a" * 32]["finding_title"]) == (
        fid,
        "Web keeps restarting",
    )
    assert (items["b" * 32]["finding_id"], items["b" * 32]["finding_title"]) == (None, None)


def test_a_second_conversation_for_the_same_finding_is_refused(conn):
    import sqlite3

    fid = _finding(conn)
    add_conversation(conn, "a" * 32, "one", NOW, finding_id=fid)
    with pytest.raises(sqlite3.IntegrityError):
        add_conversation(conn, "b" * 32, "two", NOW, finding_id=fid)


def test_a_pruned_finding_leaves_the_conversation_unlinked(conn):
    fid = _finding(conn)
    add_conversation(conn, "a" * 32, "About it", NOW, finding_id=fid)
    conn.execute("DELETE FROM findings WHERE id = ?", (fid,))
    assert get_conversation(conn, "a" * 32)["finding_id"] is None
    (item,) = list_conversations(conn)
    assert (item["finding_id"], item["finding_title"]) == (None, None)


def test_conversation_ids_by_finding_maps_only_linked_ones(conn):
    a, b = _finding(conn, "one"), _finding(conn, "two")
    add_conversation(conn, "a" * 32, "x", NOW, finding_id=a)
    add_conversation(conn, "c" * 32, "plain", NOW)
    assert conversation_ids_by_finding(conn) == {a: "a" * 32}
    assert b not in conversation_ids_by_finding(conn)
