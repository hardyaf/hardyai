from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path


ALLOWED_TOP_LEVEL = {
    ".dockerignore",
    ".env.example",
    ".gitattributes",
    ".github",
    ".gitignore",
    ".pre-commit-config.yaml",
    "README.md",
    "SECURITY.md",
    "app",
    "benchmarks",
    "deploy",
    "docs",
    "pyproject.toml",
    "requirements-dev.txt",
    "requirements.txt",
    "scripts",
    "tests",
}

DENIED_PARTS = {
    ".agents",
    ".codex",
    ".pytest_cache",
    ".venv",
    "Construction_markdowns",
    "__pycache__",
    "claw" + "-code-parity-main",
    "data",
    "debug_session_logs",
    "live",
    "secrets",
    "worklogs",
}

TEXT_SUFFIXES = {
    "",
    ".css",
    ".dockerignore",
    ".eml",
    ".env",
    ".example",
    ".html",
    ".ini",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}

SECRET_PATTERNS = {
    "private key material": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b"),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{20,}\b"),
    "OpenAI-style token": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
}

EMAIL_PATTERN = re.compile(r"(?<![\w.+-])([A-Z0-9._%+-]+)@([A-Z0-9.-]+\.[A-Z]{2,})(?![\w.-])", re.I)
SNOWFLAKE_PATTERN = re.compile(r"(?<!\d)(\d{17,20})(?!\d)")
PRIVATE_IPV4_PATTERN = re.compile(
    r"(?<!\d)(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|"
    r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?!\d)"
)
ABSOLUTE_USER_PATH_PATTERN = re.compile(r"(?:[A-Za-z]:\\Users\\[^\\\s]+|/home/[^/\s]+)", re.I)
CONFIG_SUFFIXES = {".env", ".example", ".json", ".toml", ".yaml", ".yml"}
SENSITIVE_ASSIGNMENT = re.compile(
    r"(?im)^\s*(?:api[_-]?key|client[_-]?secret|discord[_-]?bot[_-]?token|password|private[_-]?key|"
    r"refresh[_-]?token)\s*[:=]\s*['\"]?([^\s#'\"]+)"
)


def _is_example_email(address: str, domain: str) -> bool:
    normalized_domain = domain.casefold()
    if normalized_domain in {"example.com", "example.net", "example.org", "localhost"}:
        return True
    if normalized_domain.startswith("example.") or normalized_domain.endswith(".example"):
        return True
    return address.casefold() == "calendar-notification@google.com"


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().casefold()
    return (
        normalized.startswith("${")
        or normalized.startswith("replace")
        or normalized.startswith("example")
        or normalized.startswith("<")
        or normalized in {"none", "null", "false"}
    )


def _iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if ".git" in relative.parts:
            continue
        yield path, relative


def check_tree(root: Path) -> list[str]:
    errors: list[str] = []
    deny_patterns = [
        item.strip().casefold()
        for item in os.getenv("JARVIS_PUBLIC_DENY_PATTERNS", "").split("|")
        if item.strip()
    ]

    for child in root.iterdir():
        if child.name == ".git":
            continue
        if child.name not in ALLOWED_TOP_LEVEL:
            errors.append(f"unexpected top-level path: {child.name}")

    for path, relative in _iter_files(root):
        if any(part in DENIED_PARTS for part in relative.parts):
            errors.append(f"denied path: {relative.as_posix()}")
            continue
        suffix = path.suffix.casefold()
        if suffix not in TEXT_SUFFIXES and path.name not in {"Dockerfile", ".gitignore"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            errors.append(f"unreadable public text file: {relative.as_posix()} ({exc.__class__.__name__})")
            continue

        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                errors.append(f"{label} detected: {relative.as_posix()}")

        for match in EMAIL_PATTERN.finditer(text):
            address = match.group(0)
            if not _is_example_email(address, match.group(2)):
                errors.append(f"non-example email address: {relative.as_posix()}")
                break

        for match in SNOWFLAKE_PATTERN.finditer(text):
            value = match.group(1)
            if len(set(value)) > 1:
                errors.append(f"non-placeholder long numeric identifier: {relative.as_posix()}")
                break

        if PRIVATE_IPV4_PATTERN.search(text):
            errors.append(f"private IPv4 address: {relative.as_posix()}")
        if relative.as_posix() != "scripts/check_public_tree.py" and ABSOLUTE_USER_PATH_PATTERN.search(text):
            errors.append(f"personal absolute path: {relative.as_posix()}")

        lowered = text.casefold()
        for denied in deny_patterns:
            if denied in lowered:
                errors.append(f"operator-supplied denied identifier: {relative.as_posix()}")
                break

        if suffix in CONFIG_SUFFIXES:
            for assignment in SENSITIVE_ASSIGNMENT.finditer(text):
                if not _is_placeholder(assignment.group(1)):
                    errors.append(f"non-placeholder sensitive config value: {relative.as_posix()}")
                    break

    return sorted(set(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail closed when a clean Jarvis tree is unsafe to publish.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    errors = check_tree(root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        print(f"public-tree check failed with {len(errors)} finding(s)")
        return 1
    print(f"public-tree check passed: {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
