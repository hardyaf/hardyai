#!/usr/bin/env python3
"""Fail if synthetic document canaries appear in the general Jarvis database."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _quoted(identifier: str) -> str:
    if not _IDENTIFIER.fullmatch(identifier):
        raise RuntimeError("database contains an unsupported identifier")
    return f'"{identifier}"'


def audit_database(database: Path, tokens: list[str]) -> dict[str, object]:
    normalized_tokens = [str(token) for token in tokens]
    if not normalized_tokens or any(not (4 <= len(token) <= 200) for token in normalized_tokens):
        raise ValueError("provide one or more bounded synthetic canary tokens")
    connection = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    try:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        exposures: list[str] = []
        scanned_columns = 0
        for table in tables:
            table_identifier = _quoted(table)
            columns = connection.execute(f"PRAGMA table_info({table_identifier})").fetchall()
            for column in columns:
                column_name = str(column[1])
                declared_type = str(column[2] or "").casefold()
                if not any(kind in declared_type for kind in ("char", "clob", "text", "json")):
                    continue
                scanned_columns += 1
                column_identifier = _quoted(column_name)
                for token in normalized_tokens:
                    found = connection.execute(
                        f"SELECT 1 FROM {table_identifier} "
                        f"WHERE instr(CAST({column_identifier} AS TEXT), ?) > 0 LIMIT 1",
                        (token,),
                    ).fetchone()
                    if found:
                        exposures.append(f"{table}.{column_name}")
                        break
    finally:
        connection.close()
    return {
        "status": "failed" if exposures else "passed",
        "scanned_tables": len(tables),
        "scanned_text_columns": scanned_columns,
        "canary_count": len(normalized_tokens),
        "exposures": sorted(set(exposures)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("--token", action="append", required=True)
    args = parser.parse_args()
    result = audit_database(args.database, args.token)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
