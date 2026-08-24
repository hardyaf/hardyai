from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ListCreateAndAddRequest:
    list_name: str
    items: list[str]


# One create step plus up to seven distinct add steps fits within the bounded
# eight-step Main loop. Larger requests must still be chunked explicitly.
MAX_COMPOUND_LIST_ITEMS = 7


_CREATE_AND_ADD_PATTERNS = (
    re.compile(
        r"\b(?:create|make|start)\s+(?:a|an|my|the|our)?\s*"
        r"list\s+(?:called|named)\s+(?P<list>.+?)"
        r"(?:\s*[.;:]\s*|\s+(?:and|then)\s+)"
        r"(?:(?:on|in)\s+(?:it|that(?:\s+list)?|this(?:\s+list)?)\s+)?"
        r"(?:let(?:'s|s)\s+)?(?:add|put)\s*(?:[-:]\s*)?(?P<items>.+?)"
        r"(?:\s+(?:to|on|in)\s+(?:it|that(?:\s+list)?|this(?:\s+list)?|the\s+list))?$",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:create|make|start)\s+(?:a|an|my|the|our)?\s*(?P<list>.+?)\s+list"
        r"(?:\s*[.;:]\s*|\s+(?:and|then)\s+)"
        r"(?:(?:on|in)\s+(?:it|that(?:\s+list)?|this(?:\s+list)?)\s+)?"
        r"(?:let(?:'s|s)\s+)?(?:add|put)\s*(?:[-:]\s*)?(?P<items>.+?)"
        r"(?:\s+(?:to|on|in)\s+(?:it|that(?:\s+list)?|this(?:\s+list)?|the\s+list))?$",
        flags=re.IGNORECASE,
    ),
)


def parse_list_create_and_add(text: str) -> ListCreateAndAddRequest | None:
    """Parse an explicit create-list + add-items request without executing it."""

    cleaned = _normalize(text)
    for pattern in _CREATE_AND_ADD_PATTERNS:
        match = pattern.search(cleaned)
        if not match:
            continue
        list_name = _clean_list_name(match.group("list"))
        items = _split_items(match.group("items"))
        if list_name and items:
            return ListCreateAndAddRequest(list_name=list_name, items=items)
    return None


def _normalize(text: str) -> str:
    cleaned = str(text or "").replace("\u2019", "'")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(
        r"^(?:(?:hi|hello|hey|yo)\s+)?jarvis[:,]?\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"^(?:let(?:'s|s)\s+)", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _clean_list_name(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "")).strip(" .,:;-")
    cleaned = re.sub(r"^(?:called|named)\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+list$", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned


def _split_items(value: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", str(value or "")).strip(" .,:;-")
    if not cleaned:
        return []
    if re.match(r"^\d{1,2}[.)]\s*", cleaned):
        cleaned = re.sub(r"\s+(?=\d{1,2}[.)]\s*)", ", ", cleaned)
    parts = [part.strip(" .,:;-") for part in re.split(r"\s*[,;]\s*", cleaned)]
    parts = [re.sub(r"^\d{1,2}[.)]\s*", "", part).strip(" .,:;-") for part in parts]
    parts = [part for part in parts if part]
    if len(parts) > 1 and " and " in parts[-1].lower():
        tail = [
            part.strip(" .,:;-")
            for part in re.split(r"\s+and\s+", parts[-1], flags=re.IGNORECASE)
        ]
        parts = [*parts[:-1], *[part for part in tail if part]]
    return parts
