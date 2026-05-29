"""原子文件写入工具：避免并发写文件导致半截 JSON / 数据丢失。

策略：写到 same-dir 的 .tmp 文件 + os.replace（POSIX 保证原子）。
另外提供 file lock 形式（fcntl），用于读改写场景。
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterator


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """原子写入文本。整个写入失败时不会留下半截目标文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        # 清理临时文件
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, data: Any, *, indent: int = 2, sort_keys: bool = False) -> None:
    text = json.dumps(data, indent=indent, ensure_ascii=False, sort_keys=sort_keys)
    atomic_write_text(path, text)


@contextlib.contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """对 path.lock 文件加排他锁；POSIX 用 fcntl，Windows 用 msvcrt。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    f = open(lock_path, "a+")
    try:
        try:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        except ImportError:
            try:
                import msvcrt  # type: ignore[import-not-found]
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            except Exception:
                pass  # best-effort
        yield
    finally:
        try:
            try:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except ImportError:
                pass
        finally:
            f.close()


def update_json_atomically(path: Path, mutator) -> Any:
    """读 → 改 → 写 全程持锁；mutator(data)->new_data。返回 new_data。

    适合像 models_profile.json / monthly_usage.json 这种"读改写"场景。
    """
    path = Path(path)
    with file_lock(path):
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = {}
        else:
            data = {}
        new_data = mutator(data)
        atomic_write_json(path, new_data)
        return new_data
