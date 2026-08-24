from __future__ import annotations

import os
from pathlib import Path


# Tests import app.runtime during collection. Set a disposable database before
# that import so reset_runtime(hard_clear=True) can never clear the database
# selected by a developer's or server's normal .env file.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_TEST_RUNTIME_DIR = _REPO_ROOT / "data" / "pytest_runtime"
_TEST_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

os.environ["JARVIS_SKIP_DOTENV"] = "true"
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_PATH"] = str(_TEST_RUNTIME_DIR / f"jarvis_pytest_{os.getpid()}.db")
os.environ["MEMORY_MODE"] = "sqlite"
os.environ["MEMORY_MARKDOWN_PATH"] = str(_TEST_RUNTIME_DIR / "memory_markdown")
os.environ["HOUSE_SWITCH_NAMES"] = (
    "office test light,kitchen light,living room lamp,bedroom lamp"
)
os.environ["SKILL_ARTIFACT_AUTO_COMPILE_ENABLED"] = "false"

if os.getenv("JARVIS_TEST_ALLOW_EXTERNAL_SERVICES", "").strip().lower() not in {
    "1",
    "true",
    "yes",
    "on",
}:
    os.environ["MICRO_MODEL_ENABLED"] = "false"
    os.environ["MAIN_REPAIR_MODEL_ENABLED"] = "false"
    os.environ["CALENDAR_GOOGLE_ENABLED"] = "false"
    os.environ["DISCORD_ENABLED"] = "false"
