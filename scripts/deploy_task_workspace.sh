#!/usr/bin/env bash
set -euo pipefail

# Run from the authoritative checkout after the exact candidate images pass acceptance.
: "${RELEASE_APP_IMAGE:?set RELEASE_APP_IMAGE to the tested app image tag or ID}"
: "${RELEASE_RUNNER_IMAGE:?set RELEASE_RUNNER_IMAGE to the tested runner image tag or ID}"
: "${RELEASE_COMMIT:?set RELEASE_COMMIT to the tested source commit}"
: "${BACKUP_DIR:?set BACKUP_DIR to a protected backup directory}"

test -f .env
test -f deploy/docker/compose.yaml
docker image inspect "$RELEASE_APP_IMAGE" >/dev/null
docker image inspect "$RELEASE_RUNNER_IMAGE" >/dev/null
grep -q '^TASK_WORKSPACE_ENABLED=true$' .env
grep -q "^TASK_RUNNER_IMAGE=${RELEASE_RUNNER_IMAGE}$" .env

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP_DIR/$stamp"
old_app_id="$(docker image inspect jarvis-poc-app:local --format '{{.Id}}')"
docker tag "$old_app_id" "jarvis-poc-app:rollback-$stamp"
cp --preserve=mode,timestamps .env "$BACKUP_DIR/$stamp/runtime.env"
python scripts/manage_database.py --database data/jarvis_v2.db backup \
  --destination "$BACKUP_DIR/$stamp"

docker tag "$RELEASE_APP_IMAGE" jarvis-poc-app:local
docker image inspect "$RELEASE_RUNNER_IMAGE" --format '{{.Id}}' >"$BACKUP_DIR/$stamp/runner-image-id.txt"
printf '%s\n' "$RELEASE_COMMIT" >"$BACKUP_DIR/$stamp/release-commit.txt"
printf '%s\n' "$old_app_id" >"$BACKUP_DIR/$stamp/rollback-app-image-id.txt"

compose=(docker compose --env-file .env -f deploy/docker/compose.yaml)
"${compose[@]}" up -d --no-build accelerator-admission
"${compose[@]}" --profile tasks --profile approvals up -d --no-build \
  jarvis task-runner-launcher task-worker action-approval-worker
"${compose[@]}" --profile tasks --profile approvals ps

echo "backup=$BACKUP_DIR/$stamp"
echo "rollback_image=jarvis-poc-app:rollback-$stamp"
