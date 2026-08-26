from __future__ import annotations

import re
from dataclasses import dataclass, replace

from app.skills.domains.documents.types import NormalizedBlock, NormalizedTable, NormalizedTableCell


_SSN = re.compile(r"(?<!\d)(?:\d{3}[- ]\d{2}[- ]\d{4}|\d{9})(?!\d)")
_LONG_NUMBER = re.compile(r"(?<!\d)(?:\d[ -]?){12,19}(?!\d)")
_LABELED_IDENTIFIER = re.compile(
    r"(?i)\b(account|acct|policy|passport|license|licence|record|member|claim|tax(?:payer)?[ \t]*id)"
    r"(?:[ \t]*(?:number|no\.?|#|id)[ \t]*[:=-]?|[ \t]*[:=-][ \t]*)"
    r"[ \t]*([A-Z0-9][A-Z0-9 -]{4,31})"
)
_REDACTED_MARKER = re.compile(r"<REDACTED:(?:SSN|NUMBER|IDENTIFIER):\*{4}[A-Z0-9]{0,4}>")


@dataclass(frozen=True)
class RedactionResult:
    blocks: tuple[NormalizedBlock, ...]
    tables: tuple[NormalizedTable, ...]
    markdown: str
    replacement_count: int


def contains_unmasked_restricted_value(text: str) -> bool:
    scrubbed = _REDACTED_MARKER.sub("", str(text or ""))
    return bool(_SSN.search(scrubbed) or _LONG_NUMBER.search(scrubbed) or _LABELED_IDENTIFIER.search(scrubbed))


def redact_text(text: str) -> tuple[str, int]:
    value = str(text or "")
    count = 0

    def replace_ssn(_: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return "<REDACTED:SSN:****>"

    def replace_number(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        digits = re.sub(r"\D", "", match.group(0))
        suffix = digits[-4:] if len(digits) >= 4 else ""
        return f"<REDACTED:NUMBER:****{suffix}>"

    def replace_identifier(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        prefix = match.group(0)[: match.start(2) - match.start(0)]
        raw = match.group(2)
        compact = re.sub(r"[^A-Z0-9]", "", raw.upper())
        suffix = compact[-4:] if len(compact) >= 4 else ""
        return f"{prefix}<REDACTED:IDENTIFIER:****{suffix}>"

    value = _SSN.sub(replace_ssn, value)
    value = _LABELED_IDENTIFIER.sub(replace_identifier, value)
    value = _LONG_NUMBER.sub(replace_number, value)
    return value, count


def redact_artifact_view(
    *,
    blocks: tuple[NormalizedBlock, ...],
    tables: tuple[NormalizedTable, ...],
    markdown: str,
) -> RedactionResult:
    replacements = 0
    safe_blocks: list[NormalizedBlock] = []
    for block in blocks:
        text, count = redact_text(block.text)
        replacements += count
        safe_blocks.append(replace(block, text=text, char_span=None if count else block.char_span))
    safe_tables: list[NormalizedTable] = []
    for table in tables:
        cells: list[NormalizedTableCell] = []
        for cell in table.cells:
            text, count = redact_text(cell.text)
            replacements += count
            cells.append(replace(cell, text=text))
        safe_tables.append(replace(table, cells=tuple(cells)))
    safe_markdown, count = redact_text(markdown)
    replacements += count
    return RedactionResult(tuple(safe_blocks), tuple(safe_tables), safe_markdown, replacements)
