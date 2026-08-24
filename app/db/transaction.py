from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import ContextManager, Iterator, Protocol


class LockLike(Protocol):
    def __enter__(self) -> object:
        ...

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> object:
        ...


@contextmanager
def sqlite_transaction(
    *,
    conn: sqlite3.Connection,
    lock: ContextManager[object],
    immediate: bool = False,
) -> Iterator[sqlite3.Cursor]:
    """Provide the repository transaction policy with rollback on every failure."""

    with lock:
        cursor = conn.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
