from __future__ import annotations

from pathlib import Path


def test_compose_makes_admission_the_only_ollama_network_peer() -> None:
    compose = Path("deploy/docker/compose.yaml").read_text(encoding="utf-8")

    assert compose.count("http://ollama:11434") == 1
    assert "LOCAL_MODEL_URL: http://accelerator-admission:8040" in compose
    assert "networks: [accelerator-backend]" in compose
    assert "networks: [accelerator-control, accelerator-backend]" in compose
    assert "ACCELERATOR_ADMISSION_REQUIRED: \"true\"" in compose
    assert "ACCELERATOR_OLLAMA_EVICTABLE_MODELS" in compose
    assert "ACCELERATOR_OLLAMA_PROTECTED_MODELS" in compose
    assert "ACCELERATOR_OLLAMA_CANDIDATE_MODELS" in compose
    assert 'OLLAMA_NO_CLOUD: "1"' in compose
    assert 'device_ids: ["${JARVIS_OLLAMA_GPU_DEVICE_ID:-0}"]' in compose
    assert 'device_ids: ["${PADDLEOCR_VL_GPU_DEVICE_ID:-0}"]' in compose


def test_every_production_ollama_caller_attaches_accelerator_headers() -> None:
    expected = {
        "app/core/micro_backend.py": "micro",
        "app/core/main_backend.py": "main_conversation",
        "app/research/decision_backend.py": "research_decision",
        "app/tickets/review_backend.py": "action_ticket_review",
        "app/skills/domains/email_agent/classification.py": "email_classifier",
        "app/skills/domains/email_agent/summarization.py": "email_summary",
        "app/runtime.py": "runtime_health",
    }
    for filename, lane in expected.items():
        source = Path(filename).read_text(encoding="utf-8")
        assert "accelerator_request_headers" in source
        assert f'accelerator_request_headers("{lane}")' in source


def test_runtime_uses_generic_local_service_validation_for_admission_proxy() -> None:
    source = Path("app/runtime.py").read_text(encoding="utf-8")

    assert "validate_local_http_service_url(value, label=\"Local model URL\")" in source
    assert 'host in {"localhost", "host.docker.internal", "ollama"}' not in source
