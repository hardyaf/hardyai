from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RestrictedWorkflowReadiness:
    enabled: bool
    ready: bool
    reasons: tuple[str, ...]

    def public_view(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "ready": self.ready,
            "status": "ready" if self.ready else "disabled" if not self.enabled else "blocked",
            "reasons": list(self.reasons),
        }


def evaluate_restricted_workflow(
    *,
    enabled: bool,
    cipher_configured: bool,
    isolated_store_configured: bool,
    security_review_id: str,
    recovery_attestation_path: str,
) -> RestrictedWorkflowReadiness:
    if not enabled:
        return RestrictedWorkflowReadiness(False, False, ("feature_disabled",))
    reasons = []
    if not cipher_configured:
        reasons.append("authenticated_cipher_adapter_unavailable")
    if not isolated_store_configured:
        reasons.append("isolated_restricted_store_unavailable")
    if not str(security_review_id or "").strip():
        reasons.append("independent_security_review_missing")
    attestation = Path(str(recovery_attestation_path or ""))
    if not attestation.is_file() or attestation.is_symlink():
        reasons.append("clean_restore_attestation_missing")
    return RestrictedWorkflowReadiness(True, not reasons, tuple(reasons))
