"""SessionPersistence: on-disk read/write for per-user conversation
sessions -- round-tripping, never-raise-on-read resilience (mirroring
PreferenceStore's conventions), path-traversal-safe directory naming, secret
redaction before write, and POSIX file permissions.
"""

from __future__ import annotations

import json
import stat

from unified.models import ConversationHistory, ConversationTurn
from unified.session_persistence import SessionPersistence, history_from_dict, history_to_dict
from utils import register_secret
from utils import secret_redaction


def test_history_to_dict_and_back_round_trips_turns_and_fields():
    history = ConversationHistory(turns=[], max_turns=5, pending_intent="foo", pending_question="bar?", pending_turns=2)
    history.append("hi", "hello", messages=[{"role": "user", "content": "hi"}], provider="claude")

    data = history_to_dict(history)
    restored = history_from_dict(data, max_turns=5)

    assert restored.turns == [ConversationTurn(query="hi", response="hello", messages=[{"role": "user", "content": "hi"}], provider="claude")]
    assert restored.pending_intent == "foo"
    assert restored.pending_question == "bar?"
    assert restored.pending_turns == 2


def test_history_from_dict_trims_to_the_current_max_turns():
    data = {"turns": [{"query": f"q{i}", "response": f"r{i}"} for i in range(5)]}

    restored = history_from_dict(data, max_turns=2)

    assert [t.query for t in restored.turns] == ["q3", "q4"]
    assert restored.max_turns == 2


def test_history_from_dict_ignores_unknown_turn_fields():
    data = {"turns": [{"query": "q", "response": "r", "future_field": "x"}]}

    restored = history_from_dict(data, max_turns=20)

    assert restored.turns == [ConversationTurn(query="q", response="r")]


def test_history_from_dict_returns_none_on_bad_shape():
    assert history_from_dict(None, max_turns=20) is None
    assert history_from_dict({"turns": "not a list"}, max_turns=20) is None
    assert history_from_dict("not a dict", max_turns=20) is None


def test_session_round_trips_across_instances(tmp_path):
    first = SessionPersistence(tmp_path)
    first.write_session("a@example.com", "unified", 1, {"history": {"turns": [{"query": "hi", "response": "yo"}]}})

    second = SessionPersistence(tmp_path)
    data = second.read_session("a@example.com", "unified", 1)

    assert data["history"]["turns"] == [{"query": "hi", "response": "yo"}]
    assert data["user_id"] == "a@example.com"
    assert data["tool_set"] == "unified"
    assert data["session_id"] == 1


def test_index_round_trips_across_instances(tmp_path):
    first = SessionPersistence(tmp_path)
    first.write_index("a@example.com", "unified", {"current_session_id": 1, "sessions": [{"id": 1}]})

    second = SessionPersistence(tmp_path)
    data = second.read_index("a@example.com", "unified")

    assert data["current_session_id"] == 1
    assert data["sessions"] == [{"id": 1}]


def test_meta_round_trips_across_instances(tmp_path):
    first = SessionPersistence(tmp_path)
    first.write_meta("a@example.com", {"active_tool_set": "plugin_authoring"})

    second = SessionPersistence(tmp_path)
    data = second.read_meta("a@example.com")

    assert data["active_tool_set"] == "plugin_authoring"


def test_missing_file_returns_none(tmp_path):
    store = SessionPersistence(tmp_path)

    assert store.read_session("nobody@example.com", "unified", 1) is None
    assert store.read_index("nobody@example.com", "unified") is None
    assert store.read_meta("nobody@example.com") is None


def test_corrupt_json_returns_none_without_raising(tmp_path):
    store = SessionPersistence(tmp_path)
    path = store.user_dir("a@example.com") / "unified" / "session_1.json"
    path.parent.mkdir(parents=True)
    path.write_text("not json at all", encoding="utf-8")

    assert store.read_session("a@example.com", "unified", 1) is None


def test_wrong_version_returns_none(tmp_path):
    store = SessionPersistence(tmp_path)
    store.write_session("a@example.com", "unified", 1, {"history": {}})
    path = store.user_dir("a@example.com") / "unified" / "session_1.json"
    data = json.loads(path.read_text())
    data["version"] = 999
    path.write_text(json.dumps(data))

    assert store.read_session("a@example.com", "unified", 1) is None


def test_mismatched_user_id_returns_none(tmp_path):
    # Simulates a hash collision or a hand-copied file: the file's own
    # recorded user_id doesn't match the lookup key.
    store = SessionPersistence(tmp_path)
    store.write_session("a@example.com", "unified", 1, {"history": {}})
    path = store.user_dir("a@example.com") / "unified" / "session_1.json"
    data = json.loads(path.read_text())
    data["user_id"] = "someone-else@example.com"
    path.write_text(json.dumps(data))

    assert store.read_session("a@example.com", "unified", 1) is None


def test_mismatched_tool_set_or_session_id_returns_none(tmp_path):
    store = SessionPersistence(tmp_path)
    store.write_session("a@example.com", "unified", 1, {"history": {}})

    # Reading under a different tool_set/session_id than what's recorded
    # inside the file must not succeed even if the path happened to match.
    assert store.read_session("a@example.com", "plugin_authoring", 1) is None


def test_user_dir_is_a_hash_and_stays_inside_the_root(tmp_path):
    store = SessionPersistence(tmp_path)

    traversal_dir = store.user_dir("../../etc/passwd")
    normal_dir = store.user_dir("a@example.com")

    assert tmp_path in traversal_dir.parents
    assert tmp_path in normal_dir.parents
    assert traversal_dir != normal_dir
    # Plaintext user_id never appears in the directory name itself.
    assert "etc" not in traversal_dir.name
    assert "example.com" not in normal_dir.name


def test_invalid_tool_set_name_raises():
    store = SessionPersistence("/tmp/unused")

    try:
        store._tool_set_dir("a@example.com", "../escape")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_delete_session_file_removes_it_and_no_ops_on_missing(tmp_path):
    store = SessionPersistence(tmp_path)
    store.write_session("a@example.com", "unified", 1, {"history": {}})
    path = store.user_dir("a@example.com") / "unified" / "session_1.json"
    assert path.exists()

    store.delete_session_file("a@example.com", "unified", 1)
    assert not path.exists()

    store.delete_session_file("a@example.com", "unified", 1)  # no-op, doesn't raise


def test_secret_is_redacted_before_being_written_to_disk(tmp_path):
    saved = set(secret_redaction._secrets)
    try:
        register_secret("sk-super-secret-value")
        store = SessionPersistence(tmp_path)
        store.write_session(
            "a@example.com",
            "unified",
            1,
            {"history": {"turns": [{"query": "my key is sk-super-secret-value", "response": "ok"}]}},
        )

        path = store.user_dir("a@example.com") / "unified" / "session_1.json"
        raw_bytes = path.read_bytes()

        assert b"sk-super-secret-value" not in raw_bytes
        assert b"[REDACTED]" in raw_bytes
    finally:
        secret_redaction._secrets.clear()
        secret_redaction._secrets.update(saved)


def test_no_leftover_tmp_files_after_a_write(tmp_path):
    store = SessionPersistence(tmp_path)
    store.write_session("a@example.com", "unified", 1, {"history": {}})

    tmp_files = list(store.user_dir("a@example.com").rglob("*.tmp"))
    assert tmp_files == []


def test_file_and_directory_permissions_are_restrictive(tmp_path):
    store = SessionPersistence(tmp_path)
    store.write_session("a@example.com", "unified", 1, {"history": {}})

    user_dir = store.user_dir("a@example.com")
    session_file = user_dir / "unified" / "session_1.json"

    assert stat.S_IMODE(user_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((user_dir / "unified").stat().st_mode) == 0o700
    assert stat.S_IMODE(session_file.stat().st_mode) == 0o600
