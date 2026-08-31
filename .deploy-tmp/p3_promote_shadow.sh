#!/bin/sh
set -eu

source_root="$(realpath /home/codex/jarvis-p3-stage-20260831g)"
target_root="$(realpath /home/codex/jarvis-poc)"

test "$source_root" = /home/codex/jarvis-p3-stage-20260831g
test "$target_root" = /home/codex/jarvis-poc
test -f "$source_root/deploy/docker/compose.yaml"
test -f "$target_root/.env"

for directory in .github app benchmarks deploy docs scripts tests
do
    test -d "$source_root/$directory"
    mkdir -p "$target_root/$directory"
    rsync -a --delete "$source_root/$directory/" "$target_root/$directory/"
done

for filename in \
    .dockerignore \
    .env.example \
    .gitattributes \
    .gitignore \
    .pre-commit-config.yaml \
    README.md \
    SECURITY.md \
    pyproject.toml \
    requirements-dev.txt \
    requirements.txt
do
    if test -f "$source_root/$filename"
    then
        cp -p "$source_root/$filename" "$target_root/$filename"
    fi
done

cd "$target_root"
sed -i '/^MAIN_REPAIR_MODEL_NUM_PREDICT=/d' .env
printf '%s\n' 'MAIN_REPAIR_MODEL_NUM_PREDICT=1024' >> .env
chmod 600 .env
test "$(sed -n 's/^MAIN_TOOL_EXECUTION_MODE=//p' .env | tail -n 1)" = shadow
test -z "$(sed -n 's/^MAIN_TOOL_ENABLED_DOMAINS=//p' .env | tail -n 1)"
test -z "$(sed -n 's/^MAIN_TOOL_ENABLED_OPERATIONS=//p' .env | tail -n 1)"
test "$(sed -n 's/^LEGACY_MICRO_ROUTING_ENABLED=//p' .env | tail -n 1)" = true
test "$(sed -n 's/^MAIN_REPAIR_MODEL_NUM_PREDICT=//p' .env | tail -n 1)" = 1024

docker image inspect jarvis-poc-app:p3-bounded-loop-candidate-v6-20260831g >/dev/null
docker image tag jarvis-poc-app:p3-bounded-loop-candidate-v6-20260831g jarvis-poc-app:local
docker compose --env-file .env -f deploy/docker/compose.yaml config --quiet
docker compose --env-file .env -f deploy/docker/compose.yaml run --rm --no-deps -T \
    jarvis python -c 'from app.config import settings; print(settings.main_tool_execution_mode, len(settings.main_tool_enabled_domains), len(settings.main_tool_enabled_operations), settings.legacy_micro_routing_enabled)'
docker image inspect jarvis-poc-app:local --format '{{.Id}}'
