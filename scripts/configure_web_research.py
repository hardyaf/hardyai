from __future__ import annotations

import argparse
import os
import re
import secrets
import stat
import tempfile
from pathlib import Path


PLACEHOLDER_SECRETS = {"", "replace-before-nonlocal-use", "replace-with-generated-value"}


def upsert_env_text(text: str, updates: dict[str, str]) -> str:
    lines = str(text or "").splitlines()
    remaining = dict(updates)
    output: list[str] = []
    for line in lines:
        match = re.match(r"^([A-Z][A-Z0-9_]*)=", line)
        key = match.group(1) if match else None
        if key in updates:
            output.append(f"{key}={updates[key]}")
            remaining.pop(key, None)
        else:
            output.append(line)
    if remaining and output and output[-1].strip():
        output.append("")
    output.extend(f"{key}={value}" for key, value in remaining.items())
    return "\n".join(output).rstrip() + "\n"


def _current_value(text: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}=(.*)$", text, flags=re.MULTILINE)
    return match.group(1).strip() if match else None


def configure(env_path: Path) -> None:
    candidate = env_path.expanduser()
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError(f"refusing to edit non-regular environment file: {candidate}")
    resolved = candidate.resolve()
    original = resolved.read_text(encoding="utf-8")
    current_secret = _current_value(original, "SEARXNG_SECRET_KEY")
    secret_value = current_secret
    if str(current_secret or "").lower() in PLACEHOLDER_SECRETS:
        secret_value = secrets.token_hex(32)
    updated = upsert_env_text(
        original,
        {
            "WEB_RESEARCH_ENABLED": "true",
            "SEARXNG_SECRET_KEY": str(secret_value),
        },
    )
    mode = stat.S_IMODE(resolved.stat().st_mode)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".env.web-research-", dir=resolved.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, resolved)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Atomically enable Jarvis web research and create its SearXNG secret.",
    )
    parser.add_argument("--env-file", default=".env", help="Jarvis environment file (default: .env)")
    args = parser.parse_args()
    configure(Path(args.env_file))
    print("Web research enabled; SearXNG secret is configured (value hidden).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
