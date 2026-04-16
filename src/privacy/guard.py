"""Privacy guard: detect, anonymize, and restore sensitive information.

Uses Microsoft Presidio for built-in PII entities and custom regex
patterns loaded from privacy_rules.yaml.  The Leader keeps the original
text locally and only sends anonymized versions to external API workers.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

try:
    from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern
    from presidio_anonymizer import AnonymizerEngine
    from presidio_anonymizer.entities import OperatorConfig

    _HAS_PRESIDIO = True
except ImportError:
    _HAS_PRESIDIO = False

from src.config import CONFIG_DIR


@dataclass
class SensitiveSpan:
    entity_type: str
    start: int
    end: int
    text: str
    placeholder: str
    score: float = 0.0


@dataclass
class SanitizeResult:
    original: str
    sanitized: str
    spans: list[SensitiveSpan] = field(default_factory=list)
    has_sensitive: bool = False
    placeholder_map: dict[str, str] = field(default_factory=dict)


class PrivacyGuard:
    """Detects and replaces sensitive information, keeps a reversible map."""

    def __init__(self, entities: list[str] | None = None, language: str = "en"):
        self._language = language
        self._entities = entities or [
            "PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD",
        ]
        self._custom_patterns: list[dict[str, Any]] = []
        self._load_custom_rules()

        if _HAS_PRESIDIO:
            self._analyzer = AnalyzerEngine()
            self._anonymizer = AnonymizerEngine()
            self._register_custom_recognizers()
        else:
            self._analyzer = None
            self._anonymizer = None

    def _load_custom_rules(self) -> None:
        rules_path = CONFIG_DIR / "privacy_rules.yaml"
        if rules_path.exists():
            with open(rules_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            self._custom_patterns = data.get("custom_patterns", [])

    def _register_custom_recognizers(self) -> None:
        if not self._analyzer:
            return
        for pat in self._custom_patterns:
            recognizer = PatternRecognizer(
                supported_entity=pat["name"],
                patterns=[Pattern(name=pat["name"], regex=pat["regex"], score=pat.get("score", 0.85))],
            )
            self._analyzer.registry.add_recognizer(recognizer)
            if pat["name"] not in self._entities:
                self._entities.append(pat["name"])

    def scan(self, text: str) -> list[SensitiveSpan]:
        """Return a list of detected sensitive spans (no modification)."""
        spans: list[SensitiveSpan] = []

        if self._analyzer:
            results = self._analyzer.analyze(
                text=text, language=self._language, entities=self._entities,
            )
            for r in results:
                spans.append(SensitiveSpan(
                    entity_type=r.entity_type,
                    start=r.start,
                    end=r.end,
                    text=text[r.start:r.end],
                    placeholder="",
                    score=r.score,
                ))
        else:
            spans = self._regex_fallback(text)

        spans.sort(key=lambda s: s.start)
        return spans

    def sanitize(self, text: str) -> SanitizeResult:
        """Replace sensitive spans with unique placeholders; return a reversible map."""
        spans = self.scan(text)
        if not spans:
            return SanitizeResult(original=text, sanitized=text)

        placeholder_map: dict[str, str] = {}
        sanitized = text

        for sp in reversed(spans):
            ph = f"[{sp.entity_type}_{uuid.uuid4().hex[:8]}]"
            sp.placeholder = ph
            placeholder_map[ph] = sp.text
            sanitized = sanitized[:sp.start] + ph + sanitized[sp.end:]

        return SanitizeResult(
            original=text,
            sanitized=sanitized,
            spans=spans,
            has_sensitive=True,
            placeholder_map=placeholder_map,
        )

    def restore(self, sanitized_text: str, placeholder_map: dict[str, str]) -> str:
        """Replace placeholders back with original sensitive values."""
        result = sanitized_text
        for ph, original in placeholder_map.items():
            result = result.replace(ph, original)
        return result

    def _regex_fallback(self, text: str) -> list[SensitiveSpan]:
        """Fallback when Presidio is not installed — use custom regex only."""
        spans: list[SensitiveSpan] = []
        for pat in self._custom_patterns:
            for m in re.finditer(pat["regex"], text):
                spans.append(SensitiveSpan(
                    entity_type=pat["name"],
                    start=m.start(),
                    end=m.end(),
                    text=m.group(),
                    placeholder="",
                    score=pat.get("score", 0.85),
                ))
        return spans
