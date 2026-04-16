"""SQLite-backed task and subtask history for RAG and reporting."""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Column, DateTime, Float, Integer, String, Text, create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from src.config import DATA_DIR

DB_PATH = DATA_DIR / "task_history.db"


class Base(DeclarativeBase):
    pass


class TaskRecord(Base):
    __tablename__ = "tasks"

    id = Column(String, primary_key=True)
    description = Column(Text, nullable=False)
    status = Column(String, default="pending")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    total_cost_usd = Column(Float, default=0.0)
    leader_model = Column(String, nullable=True)


class SubtaskRecord(Base):
    __tablename__ = "subtasks"

    id = Column(String, primary_key=True)
    task_id = Column(String, nullable=False, index=True)
    description = Column(Text, nullable=False)
    assigned_model = Column(String, nullable=True)
    status = Column(String, default="pending")
    quality_score = Column(Float, nullable=True)
    elapsed_s = Column(Float, nullable=True)
    tokens_used = Column(Integer, nullable=True)
    cost_usd = Column(Float, default=0.0)
    passed_review = Column(Integer, nullable=True)
    result_summary = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


def _get_engine():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{DB_PATH}", echo=False)


_engine = None
_SessionLocal = None


def _ensure_db():
    global _engine, _SessionLocal
    if _engine is None:
        _engine = _get_engine()
        Base.metadata.create_all(_engine)
        _SessionLocal = sessionmaker(bind=_engine)


def get_session() -> Session:
    _ensure_db()
    return _SessionLocal()


def save_task(task: TaskRecord) -> None:
    with get_session() as s:
        s.merge(task)
        s.commit()


def save_subtask(subtask: SubtaskRecord) -> None:
    with get_session() as s:
        s.merge(subtask)
        s.commit()


def get_task(task_id: str) -> TaskRecord | None:
    with get_session() as s:
        return s.get(TaskRecord, task_id)


def list_tasks(limit: int = 50) -> list[TaskRecord]:
    with get_session() as s:
        return list(s.query(TaskRecord).order_by(TaskRecord.created_at.desc()).limit(limit).all())


def list_subtasks(task_id: str) -> list[SubtaskRecord]:
    with get_session() as s:
        return list(s.query(SubtaskRecord).filter_by(task_id=task_id).all())


def get_model_history(model: str, limit: int = 20) -> list[SubtaskRecord]:
    with get_session() as s:
        return list(
            s.query(SubtaskRecord)
            .filter_by(assigned_model=model)
            .order_by(SubtaskRecord.created_at.desc())
            .limit(limit)
            .all()
        )
