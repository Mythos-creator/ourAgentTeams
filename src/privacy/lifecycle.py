"""Data lifecycle management: automatic cleanup of sensitive data and old sessions."""

from __future__ import annotations

import datetime
import shutil
from pathlib import Path

from src.config import DATA_DIR


class DataLifecycle:
    """Manage retention and cleanup of sensitive data and sessions."""

    @staticmethod
    def cleanup_old_sessions(days: int = 90) -> int:
        """
        Delete session data older than specified days.
        
        Args:
            days: Number of days to retain sessions (default: 90)
        
        Returns:
            Number of sessions deleted
        """
        sessions_dir = DATA_DIR / "sessions"
        if not sessions_dir.exists():
            return 0
        
        cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
        deleted_count = 0
        
        for session_dir in sessions_dir.iterdir():
            if not session_dir.is_dir():
                continue
            
            mtime = datetime.datetime.fromtimestamp(session_dir.stat().st_mtime)
            if mtime < cutoff:
                shutil.rmtree(session_dir)
                deleted_count += 1
        
        return deleted_count

    @staticmethod
    def cleanup_old_rag_entries(days: int = 30) -> int:
        """
        Clean old entries from RAG vectorstore.
        
        Note: This is a placeholder - ChromaDB cleanup would require
        timestamp indexing in the collection.
        """
        # Future implementation: delete old entries from ChromaDB
        return 0

    @staticmethod
    def get_data_size() -> dict[str, int]:
        """
        Get size of sensitive data directories.
        
        Returns:
            Dictionary with sizes of each data directory in bytes
        """
        sizes = {}
        
        for subdir in ["sessions", "vectorstore", "memory", "tasks"]:
            path = DATA_DIR / subdir
            if path.exists():
                total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
                sizes[subdir] = total
        
        return sizes

    @staticmethod
    def cleanup_temp_files() -> int:
        """Clean temporary or incomplete data files."""
        data_dir = DATA_DIR
        if not data_dir.exists():
            return 0
        
        deleted_count = 0
        
        # Clean .tmp files
        for tmp_file in data_dir.rglob("*.tmp"):
            try:
                tmp_file.unlink()
                deleted_count += 1
            except Exception:
                pass
        
        # Clean incomplete session snapshots (corrupt files)
        sessions_dir = data_dir / "sessions"
        if sessions_dir.exists():
            for session_file in sessions_dir.rglob("*.json"):
                try:
                    import json
                    with open(session_file) as f:
                        json.load(f)
                except (json.JSONDecodeError, IOError):
                    try:
                        session_file.unlink()
                        deleted_count += 1
                    except Exception:
                        pass
        
        return deleted_count

    @staticmethod
    def estimate_cleanup_savings(days: int = 90) -> dict[str, int]:
        """
        Estimate how much space would be freed by cleanup.
        
        Returns:
            Dictionary with estimated savings
        """
        sessions_dir = DATA_DIR / "sessions"
        if not sessions_dir.exists():
            return {"sessions_bytes": 0, "sessions_count": 0}
        
        cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
        total_bytes = 0
        session_count = 0
        
        for session_dir in sessions_dir.iterdir():
            if not session_dir.is_dir():
                continue
            
            mtime = datetime.datetime.fromtimestamp(session_dir.stat().st_mtime)
            if mtime < cutoff:
                total_bytes += sum(f.stat().st_size for f in session_dir.rglob("*") if f.is_file())
                session_count += 1
        
        return {
            "sessions_bytes": total_bytes,
            "sessions_count": session_count,
        }
