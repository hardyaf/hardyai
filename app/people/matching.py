from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.people.types import ContactCandidate


_SPACE = re.compile(r"\s+")
_PHONE = re.compile(r"\D+")


@dataclass(frozen=True)
class ContactMatch:
    contact_ref: str
    display_name: str
    organization: str | None
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ContactMatchResult:
    candidates: tuple[ContactMatch, ...]
    selected_ref: str | None
    ambiguous: bool
    proposed_operation: str


def match_contacts(
    fields: dict[str, str],
    candidates: tuple[ContactCandidate, ...],
    *,
    accept_threshold: float = 0.75,
    ambiguity_delta: float = 0.12,
) -> ContactMatchResult:
    """Explainable deterministic matching; this function never mutates contacts."""

    ranked = tuple(
        sorted(
            (_score(fields, candidate) for candidate in candidates),
            key=lambda item: (-item.score, item.contact_ref),
        )
    )
    plausible = tuple(item for item in ranked if item.score >= 0.45)
    if not plausible:
        return ContactMatchResult((), None, False, "create")
    top = plausible[0]
    close_second = len(plausible) > 1 and top.score - plausible[1].score < ambiguity_delta
    exact_unique_key = bool({"exact_email", "exact_phone"} & set(top.reasons)) and not any(
        {"exact_email", "exact_phone"} & set(item.reasons) for item in plausible[1:]
    )
    accepted = top.score >= accept_threshold and (exact_unique_key or not close_second)
    return ContactMatchResult(
        plausible[:10],
        top.contact_ref if accepted else None,
        bool((close_second and not exact_unique_key) or top.score < accept_threshold),
        "update" if accepted else "select_or_create",
    )


def contact_search_query(fields: dict[str, str]) -> str:
    return next(
        (
            value
            for key in ("email", "phone", "full_name", "organization")
            if (value := str(fields.get(key) or "").strip())
        ),
        "",
    )[:200]


def _score(fields: dict[str, str], candidate: ContactCandidate) -> ContactMatch:
    reasons: list[str] = []
    score = 0.0
    email = _email(fields.get("email"))
    candidate_emails = {_email(value) for value in candidate.emails if _email(value)}
    if email and email in candidate_emails:
        score = max(score, 1.0)
        reasons.append("exact_email")
    phone = _phone(fields.get("phone"))
    candidate_phones = {_phone(value) for value in candidate.phones if _phone(value)}
    if phone and phone in candidate_phones:
        score = max(score, 0.97)
        reasons.append("exact_phone")
    name_similarity = _similarity(fields.get("full_name"), candidate.display_name)
    organization_similarity = _similarity(fields.get("organization"), candidate.organization)
    if name_similarity >= 0.98:
        reasons.append("exact_name")
    elif name_similarity >= 0.72:
        reasons.append("similar_name")
    if organization_similarity >= 0.98:
        reasons.append("exact_organization")
    elif organization_similarity >= 0.72:
        reasons.append("similar_organization")
    if name_similarity:
        composite = 0.7 * name_similarity + 0.3 * organization_similarity
        if not fields.get("organization") or not candidate.organization:
            composite = 0.72 * name_similarity
        score = max(score, composite)
    return ContactMatch(
        contact_ref=str(candidate.contact_ref)[:240],
        display_name=str(candidate.display_name)[:160],
        organization=str(candidate.organization)[:160] if candidate.organization else None,
        score=round(min(score, 1.0), 4),
        reasons=tuple(reasons),
    )


def _fold(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_value = "".join(character for character in normalized if not unicodedata.combining(character))
    return _SPACE.sub(" ", ascii_value.casefold()).strip()


def _email(value: str | None) -> str:
    return _fold(value)


def _phone(value: str | None) -> str:
    digits = _PHONE.sub("", str(value or ""))
    return digits[-10:] if len(digits) >= 7 else ""


def _similarity(left: str | None, right: str | None) -> float:
    first, second = _fold(left), _fold(right)
    if not first or not second:
        return 0.0
    return SequenceMatcher(None, first, second).ratio()
