from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from app.reviews.service import HumanReviewService
from app.reviews.types import ReviewKind
from app.skills.domains.documents.storage import DocumentRepository
from app.skills.domains.documents.types import NormalizedBlock


_ACTION = re.compile(r"^\s*(?:[-*]\s*)?(?:\[\s*\]\s*)?(?:action(?:\s+item)?|to-?do)\s*[:=-]\s*(.+)$", re.IGNORECASE)
_MEMORY = re.compile(r"^\s*(?:remember|durable fact)\s*[:=-]\s*(.+)$", re.IGNORECASE)
_DUE = re.compile(r"\s+\bdue\b\s*[:=-]?\s*(.+)$", re.IGNORECASE)
_ASSIGNEE = re.compile(r"\s+(?:owner|assignee)\s*[:=-]\s*([A-Za-z][A-Za-z .'-]{0,60})$", re.IGNORECASE)
_US_DATE = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class NoteProposalResult:
    action_proposals: tuple[dict, ...]
    memory_proposals: tuple[dict, ...]
    review_ids: tuple[str, ...]


class NoteProposalService:
    """Deterministic note derivatives; every action remains a shared-review proposal."""

    def __init__(
        self,
        *,
        repository: DocumentRepository,
        reviews: HumanReviewService,
        target_list_name: str = "to-do",
        max_proposals: int = 20,
    ) -> None:
        self.repository = repository
        self.reviews = reviews
        self.target_list_name = " ".join(str(target_list_name or "to-do").split())[:80]
        self.max_proposals = max(1, min(int(max_proposals), 50))

    def generate(
        self,
        *,
        document_id: str,
        source_version_id: str,
        run_id: str,
        blocks: tuple[NormalizedBlock, ...],
    ) -> NoteProposalResult:
        actions: list[dict] = []
        memories: list[dict] = []
        review_ids: list[str] = []
        for block in blocks:
            for line in block.text.splitlines():
                action_match = _ACTION.match(line)
                if action_match and len(actions) < self.max_proposals:
                    parsed = self._parse_action(action_match.group(1), block)
                    if parsed is None:
                        continue
                    try:
                        proposal = self.repository.create_action_proposal(
                            document_id=document_id,
                            source_version_id=source_version_id,
                            run_id=run_id,
                            target_list_name=self.target_list_name,
                            evidence=[{"page_number": block.page_number, "block_id": block.block_id}],
                            **parsed,
                        )
                    except ValueError:
                        continue
                    review = self.reviews.create_review(
                        review_kind=ReviewKind.DOWNSTREAM_ACTION,
                        subject_type="document_action_proposal",
                        subject_id=str(proposal["proposal_id"]),
                        subject_version=source_version_id,
                        item_hash=str(proposal["item_hash"]),
                        source_ref=document_id,
                        sensitivity="private",
                        confidence=float(proposal["confidence"]),
                        validator_summary=[
                            {"code": "explicit_due_date", "passed": proposal["normalized_due_date"] is not None},
                            {"code": "assignee_unambiguous", "passed": proposal["assignee_candidate"] is None},
                        ],
                        evidence_refs=[f"page:{block.page_number}:block:{block.block_id}"],
                        target_operation="lists.add_item",
                        authorization_binding=f"owner:{document_id}:source:{source_version_id}",
                    )
                    if not self.repository.bind_action_review(
                        proposal_id=str(proposal["proposal_id"]),
                        review_id=str(review["review_id"]),
                    ):
                        raise RuntimeError("action proposal review binding failed")
                    proposal["review_id"] = str(review["review_id"])
                    actions.append(proposal)
                    review_ids.append(str(review["review_id"]))
                    continue
                memory_match = _MEMORY.match(line)
                if memory_match and len(memories) < self.max_proposals:
                    try:
                        memories.append(
                            self.repository.create_memory_proposal(
                                document_id=document_id,
                                source_version_id=source_version_id,
                                run_id=run_id,
                                fact_text=memory_match.group(1),
                                confidence=self._base_confidence(block),
                                evidence=[{"page_number": block.page_number, "block_id": block.block_id}],
                            )
                        )
                    except ValueError:
                        continue
        return NoteProposalResult(tuple(actions), tuple(memories), tuple(review_ids))

    @staticmethod
    def _parse_action(value: str, block: NormalizedBlock) -> dict | None:
        text = " ".join(str(value or "").split())[:500]
        if not text or "<REDACTED:" in text:
            return None
        confidence = NoteProposalService._base_confidence(block)
        assignee = None
        assignee_match = _ASSIGNEE.search(text)
        if assignee_match:
            assignee = assignee_match.group(1).strip()
            text = text[: assignee_match.start()].strip(" ,;-")
            confidence = min(confidence, 0.7)
        due_text = None
        normalized_due_date = None
        due_match = _DUE.search(text)
        if due_match:
            due_text = due_match.group(1).strip(" .")[:120]
            text = text[: due_match.start()].strip(" ,;-")
            normalized_due_date = _normalize_date(due_text)
            if normalized_due_date is None:
                confidence = min(confidence, 0.65)
        if not text:
            return None
        return {
            "action_text": text,
            "due_text": due_text,
            "normalized_due_date": normalized_due_date,
            "assignee_candidate": assignee,
            "confidence": confidence,
        }

    @staticmethod
    def _base_confidence(block: NormalizedBlock) -> float:
        if block.confidence is None:
            return 0.78
        return max(0.4, min(float(block.confidence), 0.98))


def _normalize_date(value: str) -> str | None:
    candidate = str(value or "").strip()
    if _ISO_DATE.fullmatch(candidate):
        try:
            return date.fromisoformat(candidate).isoformat()
        except ValueError:
            return None
    match = _US_DATE.fullmatch(candidate)
    if not match:
        return None
    year = int(match.group(3))
    if year < 100:
        year += 2000
    try:
        return date(year, int(match.group(1)), int(match.group(2))).isoformat()
    except ValueError:
        return None
