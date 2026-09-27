from __future__ import annotations

from app.core.ollama_observability import AdaptiveTokenBudgetPolicy


def background_email_token_budget_policy() -> AdaptiveTokenBudgetPolicy:
    """Keep optional email enrichment from monopolizing interactive inference.

    Email summaries and classifications already have deterministic, inspectable
    fallbacks.  A response that consumes its whole output allowance is therefore
    treated as a failed enrichment attempt instead of being retried at larger
    token budgets while a live user waits for the shared accelerator.
    """

    return AdaptiveTokenBudgetPolicy(
        enabled=False,
        max_attempts=1,
        max_predict_multiplier=1,
    )
