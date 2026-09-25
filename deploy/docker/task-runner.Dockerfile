FROM python:3.12-slim-bookworm@sha256:a116514e19457bcb7af7efe9c3dd0b9b71e85b317694e7882a1c52aa15a78134

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY deploy/docker/task_runner/jarvis_task_api.py /opt/task-runner/jarvis_task_api.py
ENV PYTHONPATH=/opt/task-runner

USER 65532:65532
WORKDIR /workspace/work
ENTRYPOINT []
CMD ["python", "/workspace/input/source.py"]
