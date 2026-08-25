from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.config import settings
from app.db.sqlite_store import SQLiteStore
from app.skills.registry_service import SkillRegistryService


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compile Jarvis skill artifacts from SQL + markdown contracts.")
    parser.add_argument(
        "--critical-output",
        default="app/prompts/skills/critical_skills.md",
        help="Output path for compiled critical skills markdown.",
    )
    parser.add_argument(
        "--micro-output",
        default="app/prompts/micro_jarvis_skills.md",
        help="Output path for compiled micro allowlist markdown.",
    )
    parser.add_argument(
        "--min-critical-level",
        type=int,
        default=1,
        help="Minimum critical level to include in critical artifact.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force rebuild even when source hash is unchanged.",
    )
    parser.add_argument(
        "--skip-sync",
        action="store_true",
        help="Skip markdown -> SQL sync before compile.",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    store = SQLiteStore(database_path=settings.database_path)
    registry = SkillRegistryService(sqlite_store=store, repo_root=str(REPO_ROOT))
    if not registry.list_skills(active_only=False):
        registry.seed_defaults()

    sync_result = None
    if not args.skip_sync:
        sync_result = registry.sync_skills_from_markdown()

    compile_if_stale = not args.force
    critical = registry.compile_critical_skills_markdown(
        output_path=args.critical_output,
        min_critical_level=max(0, int(args.min_critical_level)),
        compile_if_stale=compile_if_stale,
    )
    micro = registry.compile_micro_skills_markdown(
        output_path=args.micro_output,
        compile_if_stale=compile_if_stale,
    )

    print(
        json.dumps(
            {
                "status": "ok",
                "sync": sync_result,
                "critical": critical,
                "micro": micro,
            },
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
