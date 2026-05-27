"""Privacy protection audit log for tracking sensitive data handling."""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

from src.config import DATA_DIR

AUDIT_LOG_FILE = DATA_DIR / "privacy_audit.jsonl"


class PrivacyAudit:
    """Record all sensitive data handling operations for compliance and debugging."""

    @staticmethod
    def log_sanitization(
        session_id: str,
        has_sensitive: bool,
        entity_count: int,
        entity_types: list[str],
        sent_to_cloud: bool,
    ) -> None:
        """Log task sanitization event."""
        entry = {
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "session_id": session_id,
            "event": "sanitization",
            "has_sensitive": has_sensitive,
            "entity_count": entity_count,
            "entity_types": entity_types,
            "sent_to_cloud": sent_to_cloud,
        }
        _write_audit_entry(entry)

    @staticmethod
    def log_restoration(session_id: str, field: str, restored_entity_count: int) -> None:
        """Log data restoration event."""
        entry = {
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "session_id": session_id,
            "event": "restoration",
            "field": field,
            "restored_entity_count": restored_entity_count,
        }
        _write_audit_entry(entry)

    @staticmethod
    def log_rag_index(session_id: str, sanitized: bool, entity_count: int) -> None:
        """Log RAG indexing with privacy info."""
        entry = {
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "session_id": session_id,
            "event": "rag_index",
            "sanitized": sanitized,
            "entity_count": entity_count,
        }
        _write_audit_entry(entry)

    @staticmethod
    def log_database_record(session_id: str, sanitized: bool, reason: str = "") -> None:
        """Log database recording with privacy info."""
        entry = {
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "session_id": session_id,
            "event": "database_record",
            "sanitized": sanitized,
            "reason": reason,
        }
        _write_audit_entry(entry)

    @staticmethod
    def log_api_call(session_id: str, model: str, sanitized: bool) -> None:
        """Log cloud API call with sanitization status."""
        entry = {
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "session_id": session_id,
            "event": "api_call",
            "model": model,
            "sanitized": sanitized,
        }
        _write_audit_entry(entry)

    @staticmethod
    def get_audit_log(session_id: str | None = None) -> list[dict[str, Any]]:
        """Retrieve audit log entries, optionally filtered by session."""
        if not AUDIT_LOG_FILE.exists():
            return []
        
        entries = []
        with open(AUDIT_LOG_FILE, "r") as f:
            for line in f:
                entry = json.loads(line.strip())
                if session_id is None or entry.get("session_id") == session_id:
                    entries.append(entry)
        return entries


def _write_audit_entry(entry: dict[str, Any]) -> None:
    """Write entry to audit log."""
    AUDIT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_LOG_FILE, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
