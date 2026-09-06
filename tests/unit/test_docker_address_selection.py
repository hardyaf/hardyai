from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_application_image_installs_complete_ipv4_preferred_gai_policy():
    dockerfile = (REPO_ROOT / "deploy" / "docker" / "Dockerfile").read_text(encoding="utf-8")
    policy = (REPO_ROOT / "deploy" / "docker" / "gai.conf").read_text(encoding="utf-8")

    assert "COPY deploy/docker/gai.conf /etc/gai.conf" in dockerfile
    for default_row in (
        "precedence ::1/128       50",
        "precedence ::/0          40",
        "precedence 2002::/16     30",
        "precedence ::/96         20",
    ):
        assert default_row in policy
    assert "precedence ::ffff:0:0/96 100" in policy
