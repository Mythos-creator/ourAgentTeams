"""Tests for transcript trimming."""

from __future__ import annotations

from src.cli.transcript import cap_transcript_keep_system, transcript_chars


def _msg(role, content): return {"role": role, "content": content}


def test_keeps_all_system_messages():
    msgs = [
        _msg("system", "S1"),
        _msg("user", "u1"), _msg("assistant", "a1"),
        _msg("user", "u2"), _msg("assistant", "a2"),
        _msg("user", "u3"), _msg("assistant", "a3"),
    ]
    out = cap_transcript_keep_system(msgs, max_user_assistant_pairs=2)
    roles = [m["role"] for m in out]
    assert roles == ["system", "user", "assistant", "user", "assistant"]
    # newest two pairs kept
    assert [m["content"] for m in out if m["role"] == "user"] == ["u2", "u3"]


def test_no_pairs_left_when_zero_budget():
    msgs = [_msg("system", "S")] + [
        _msg("user", "u1"), _msg("assistant", "a1"),
    ]
    out = cap_transcript_keep_system(msgs, max_user_assistant_pairs=0)
    # zero pairs allowed → only system survives
    assert out == [_msg("system", "S")]


def test_orphan_assistant_preserved():
    msgs = [
        _msg("system", "S"),
        _msg("assistant", "stray"),
        _msg("user", "u1"), _msg("assistant", "a1"),
    ]
    out = cap_transcript_keep_system(msgs, max_user_assistant_pairs=5)
    contents = [m["content"] for m in out]
    assert "S" in contents and "stray" in contents and "u1" in contents


def test_max_total_chars_drops_oldest_pairs_first():
    msgs = [
        _msg("system", "S"),
        _msg("user", "x" * 100), _msg("assistant", "y" * 100),
        _msg("user", "u" * 10), _msg("assistant", "a" * 10),
    ]
    out = cap_transcript_keep_system(msgs, max_user_assistant_pairs=10, max_total_chars=80)
    user_contents = [m["content"] for m in out if m["role"] == "user"]
    # only the small recent pair survives
    assert user_contents == ["u" * 10]
    # system is always there
    assert any(m["role"] == "system" for m in out)


def test_chars_helper():
    msgs = [_msg("system", "abc"), _msg("user", "12")]
    assert transcript_chars(msgs) == 5


def test_does_not_mutate_input():
    msgs = [_msg("system", "S"), _msg("user", "u")]
    snap = list(msgs)
    cap_transcript_keep_system(msgs, max_user_assistant_pairs=0)
    assert msgs == snap
