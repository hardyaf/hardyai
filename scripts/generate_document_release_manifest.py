from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(
    *,
    application_digest: str,
    source_revision: str,
    local_image_digests: dict[str, str] | None = None,
) -> dict:
    if not _DIGEST.fullmatch(application_digest):
        raise ValueError("--application-digest must be an immutable sha256 digest")
    local_digests = dict(local_image_digests or {})
    invalid_local = sorted(
        reference for reference, digest in local_digests.items() if not _DIGEST.fullmatch(digest)
    )
    if invalid_local:
        raise ValueError(f"invalid local image digest(s): {', '.join(invalid_local)}")
    compose_path = REPO_ROOT / "deploy" / "docker" / "compose.yaml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    images = {
        name: str(service["image"])
        for name, service in dict(compose.get("services") or {}).items()
        if isinstance(service, dict) and service.get("image")
    }
    resolved_digests: dict[str, str] = {}
    unresolved: list[str] = []
    for service, image in images.items():
        if image == "jarvis-poc-app:local":
            resolved_digests[service] = application_digest
        elif "@sha256:" in image:
            resolved_digests[service] = "sha256:" + image.rsplit("@sha256:", 1)[1]
        elif image in local_digests:
            resolved_digests[service] = local_digests[image]
        else:
            unresolved.append(service)
    packages = []
    for distribution in importlib.metadata.distributions():
        name = str(distribution.metadata.get("Name") or "").strip()
        if not name:
            continue
        packages.append(
            {
                "name": name,
                "version": str(distribution.version),
                "license": str(distribution.metadata.get("License") or "unknown")[:200],
            }
        )
    inputs = {}
    for relative in (
        "requirements.txt",
        "deploy/docker/compose.yaml",
        "deploy/docker/Dockerfile",
        "deploy/docker/Dockerfile.paddleocr",
        "deploy/docker/Dockerfile.paddleocr-vl",
    ):
        path = REPO_ROOT / relative
        inputs[relative] = {"sha256": _sha256(path), "size_bytes": path.stat().st_size}
    return {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "source_revision": str(source_revision or "working-tree")[:120],
        "application_image_digest": application_digest,
        "compose_images": images,
        "compose_image_digests": resolved_digests,
        "local_image_digests": local_digests,
        "unresolved_image_digests": sorted(unresolved),
        # Retained for readers of the initial Phase 11 manifest schema.
        "unpinned_external_images": sorted(unresolved),
        "inputs": inputs,
        "python_packages": sorted(packages, key=lambda item: item["name"].casefold()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate an offline release BOM/SBOM manifest.")
    parser.add_argument("--application-digest", required=True)
    parser.add_argument("--source-revision", default="working-tree")
    parser.add_argument(
        "--local-image-digest",
        action="append",
        default=[],
        metavar="IMAGE=SHA256",
        help="Record the immutable ID of a locally built Compose image (repeatable).",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    local_image_digests: dict[str, str] = {}
    for value in args.local_image_digest:
        reference, separator, digest = value.rpartition("=")
        if not separator or not reference.strip() or not digest.strip():
            parser.error("--local-image-digest must use IMAGE=sha256:<64 hex characters>")
        if reference in local_image_digests and local_image_digests[reference] != digest:
            parser.error(f"conflicting digests supplied for {reference}")
        local_image_digests[reference] = digest
    manifest = build_manifest(
        application_digest=args.application_digest,
        source_revision=args.source_revision,
        local_image_digests=local_image_digests,
    )
    encoded = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if args.output:
        destination = args.output.expanduser().resolve()
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination.write_text(encoded, encoding="utf-8")
        destination.chmod(0o600)
    else:
        print(encoded, end="")
    return 1 if manifest["unresolved_image_digests"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
