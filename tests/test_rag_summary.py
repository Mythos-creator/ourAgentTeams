"""Tests for RAG text trimming helpers."""

from __future__ import annotations

from src.memory.rag_summary import compact, safe_index_text, truncate


def test_truncate_short_text_unchanged():
    assert truncate("hello", 100) == "hello"


def test_truncate_long_text_keeps_head_and_marks():
    text = "x" * 5000
    out = truncate(text, max_chars=200)
    assert out.startswith("x" * 200)
    assert "已截断" in out
    assert "4800" in out


def test_compact_keeps_both_ends():
    text = "HEAD" + "m" * 1000 + "TAIL"
    out = compact(text, max_chars=200)
    assert out.startswith("HEAD")
    assert out.endswith("TAIL")
    assert "中间省略" in out


def test_safe_index_text_truncates_both():
    desc = "d" * 1000
    result = "r" * 5000
    d2, r2 = safe_index_text(desc, result, max_desc_chars=100, max_result_chars=300)
    assert len(d2) <= len(desc) and "已截断" in d2
    assert len(r2) <= len(result) and "已截断" in r2


def test_non_string_input_is_coerced():
    out = truncate(12345, max_chars=10)
    assert "12345" in out


def test_compact_short_text_unchanged():
    assert compact("abc", 100) == "abc"
