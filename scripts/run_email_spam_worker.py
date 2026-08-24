from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.config import settings
from app.services.google.gmail_spam_writer import GoogleGmailSpamWriter
from app.skills.domains.email_agent.config import EmailAgentPermissions
from app.skills.domains.email_agent.spam_worker import EmailSpamWorker, EmailSpamWorkerConfig
from app.skills.domains.email_agent.storage import EmailAgentSQLiteStorage


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one bounded claim cycle for explicit Jarvis Gmail mailbox operations."
    )
    parser.parse_args()
    if not settings.email_agent_enabled or not (
        settings.email_agent_spam_writes_enabled
        or settings.email_agent_label_writes_enabled
    ):
        print(json.dumps({"status": "disabled", "claimed_count": 0}))
        return 0

    permissions = EmailAgentPermissions.load(settings.email_agent_permissions_path)
    storage = EmailAgentSQLiteStorage(settings.database_path)
    try:
        writer = GoogleGmailSpamWriter.from_token_file(
            expected_profile_email=permissions.gmail_profile,
            token_path=(
                settings.email_agent_label_token_path
                if settings.email_agent_label_writes_enabled
                else settings.email_agent_spam_token_path
            ),
        )
        worker = EmailSpamWorker(
            storage=storage,
            writer=writer,
            config=EmailSpamWorkerConfig(
                enabled=settings.email_agent_spam_writes_enabled,
                label_writes_enabled=settings.email_agent_label_writes_enabled,
                batch_size=settings.email_agent_spam_worker_batch_size,
                lease_seconds=settings.email_agent_spam_worker_lease_seconds,
                max_writes_per_hour=settings.email_agent_spam_max_writes_per_hour,
                max_writes_per_day=settings.email_agent_spam_max_writes_per_day,
                label_batch_size=min(25, settings.email_agent_label_max_writes_per_hour),
                label_max_writes_per_hour=settings.email_agent_label_max_writes_per_hour,
                label_max_writes_per_day=settings.email_agent_label_max_writes_per_day,
            ),
            managed_label_names=permissions.managed_gmail_labels,
        )
        result = worker.run_once(now=datetime.now(timezone.utc))
        print(json.dumps(result, sort_keys=True))
        return 0 if result.get("status") in {"ok", "rate_limited"} else 1
    finally:
        storage.close()


if __name__ == "__main__":
    raise SystemExit(main())
