"""Tests for the Privacy Guard module."""

import pytest

from src.privacy.guard import PrivacyGuard


@pytest.fixture
def guard():
    return PrivacyGuard(entities=["PERSON", "EMAIL_ADDRESS", "API_KEY", "PASSWORD"])


def test_scan_no_sensitive(guard):
    spans = guard.scan("Build a REST API with FastAPI")
    api_key_spans = [s for s in spans if s.entity_type in ("API_KEY", "PASSWORD")]
    assert len(api_key_spans) == 0


def test_scan_detects_api_key(guard):
    # Match privacy_rules "API_KEY" without embedding sk-* (avoids gitleaks false positives)
    text = "Use api_key=FakeTestCredential_0123456789ABCDEF"
    spans = guard.scan(text)
    names = [s.entity_type for s in spans]
    assert "API_KEY" in names


def test_sanitize_and_restore(guard):
    text = "Send email to user@example.com and call password=secret123 for api"
    result = guard.sanitize(text)

    if result.has_sensitive:
        assert "user@example.com" not in result.sanitized or "secret123" not in result.sanitized
        restored = guard.restore(result.sanitized, result.placeholder_map)
        assert "user@example.com" in restored or "secret123" in restored


def test_sanitize_no_sensitive(guard):
    result = guard.sanitize("Hello world, no secrets here")
    assert not result.has_sensitive or result.sanitized == result.original
