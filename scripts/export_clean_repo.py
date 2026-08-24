from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from check_public_tree import check_tree


SOURCE_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_FILES = {
    ".dockerignore",
    ".env.example",
    ".gitignore",
    ".pre-commit-config.yaml",
    "README.md",
    "SECURITY.md",
    "pyproject.toml",
    "requirements-dev.txt",
    "requirements.txt",
}
PUBLIC_DIRECTORIES = {".github", "app", "deploy", "docs", "scripts", "tests"}
EXCLUDED_RELATIVE_PATHS = {
    Path("docs/runtime-authority-and-windows-decommission.md"),
    Path("docs/ubuntu-24.04-first-install.md"),
    Path("scripts/install_email_spam_worker_autostart.ps1"),
    Path("scripts/install_jarvis_autostart.ps1"),
    Path("scripts/run_email_spam_worker.ps1"),
    Path("scripts/start_jarvis.ps1"),
}
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache"}


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def export_tree(destination: Path) -> int:
    destination = destination.expanduser().resolve()
    if destination == SOURCE_ROOT or SOURCE_ROOT in destination.parents:
        raise ValueError("destination must be a sibling or other directory outside the source checkout")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("destination already exists and is not empty")
    destination.mkdir(parents=True, exist_ok=True)

    copied = 0
    for filename in sorted(PUBLIC_FILES):
        source = SOURCE_ROOT / filename
        if source.is_file():
            _copy_file(source, destination / filename)
            copied += 1

    for directory in sorted(PUBLIC_DIRECTORIES):
        source_directory = SOURCE_ROOT / directory
        if not source_directory.is_dir():
            continue
        for source in sorted(source_directory.rglob("*")):
            if not source.is_file():
                continue
            relative = source.relative_to(SOURCE_ROOT)
            if relative in EXCLUDED_RELATIVE_PATHS:
                continue
            if any(part in EXCLUDED_PARTS for part in relative.parts):
                continue
            _copy_file(source, destination / relative)
            copied += 1

    errors = check_tree(destination)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        raise RuntimeError(f"refusing export: public-tree check found {len(errors)} issue(s)")
    return copied


def initialize_git(destination: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=destination, check=True)
    subprocess.run(["git", "add", "--all"], cwd=destination, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial sanitized Jarvis v0 source"],
        cwd=destination,
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Export the current Jarvis worktree into a clean-history tree.")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--init-git", action="store_true")
    args = parser.parse_args()
    destination = args.destination.expanduser().resolve()
    try:
        copied = export_tree(destination)
        if args.init_git:
            initialize_git(destination)
    except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"clean export failed: {exc}", file=sys.stderr)
        return 1
    print(f"clean export passed: copied {copied} files to {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
