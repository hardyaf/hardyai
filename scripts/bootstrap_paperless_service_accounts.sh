#!/usr/bin/env bash
set -Eeuo pipefail

container_name="${PAPERLESS_CONTAINER_NAME:-jarvis-poc-paperless-webserver-1}"
secrets_root="${DOCUMENTS_SECRETS_ROOT:-/etc/hardyai/documents}"
helper_source="${PAPERLESS_BOOTSTRAP_HELPER:-$(dirname "$0")/bootstrap_paperless_service_account.py}"
archive_token="${secrets_root}/paperless_archive_token"
read_token="${secrets_root}/paperless_read_token"
read_user_id="${secrets_root}/paperless_read_user_id"

archive_pending="${archive_token}.pending"
read_pending="${read_token}.pending"
read_user_id_pending="${read_user_id}.pending"

cleanup() {
  rm -f -- "$archive_pending" "$read_pending" "$read_user_id_pending"
}
trap cleanup EXIT

if [[ ! -f "$helper_source" ]]; then
  printf 'ERROR: Paperless bootstrap helper is missing: %s\n' "$helper_source" >&2
  exit 1
fi
if [[ ! -d "$secrets_root" ]]; then
  printf 'ERROR: documents secrets directory is missing: %s\n' "$secrets_root" >&2
  exit 1
fi

umask 077
docker exec -i "$container_name" /command/with-contenv python - \
  hardyai-document-archive \
  documents.add_document \
  documents.change_document \
  documents.view_document \
  documents.view_paperlesstask \
  <"$helper_source" >"$archive_pending"

docker exec -i "$container_name" /command/with-contenv python - \
  hardyai-document-reader \
  documents.view_document \
  <"$helper_source" >"$read_pending"

docker exec -i "$container_name" /command/with-contenv python - \
  --lookup-user-id hardyai-document-reader \
  <"$helper_source" >"$read_user_id_pending"

if ! grep -Eq '^[0-9a-f]{40}$' "$archive_pending"; then
  printf 'ERROR: Paperless returned an invalid archive token.\n' >&2
  exit 1
fi
if ! grep -Eq '^[0-9a-f]{40}$' "$read_pending"; then
  printf 'ERROR: Paperless returned an invalid read token.\n' >&2
  exit 1
fi
if ! grep -Eq '^[1-9][0-9]*$' "$read_user_id_pending"; then
  printf 'ERROR: Paperless returned an invalid read-user ID.\n' >&2
  exit 1
fi
if cmp -s -- "$archive_pending" "$read_pending"; then
  printf 'ERROR: Paperless service-account tokens are not distinct.\n' >&2
  exit 1
fi

mv -f -- "$archive_pending" "$archive_token"
mv -f -- "$read_pending" "$read_token"
mv -f -- "$read_user_id_pending" "$read_user_id"
chmod 0600 -- "$archive_token" "$read_token" "$read_user_id"
trap - EXIT
printf 'Paperless archive and read service accounts are ready.\n'
