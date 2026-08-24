#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
log() { printf '[jarvis-workers] %s\n' "$*"; }

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_requested="$(cd -- "${script_dir}/../.." && pwd -P)"
service_user="${SUDO_USER:-$(id -un)}"

while (($# > 0)); do
  case "$1" in
    --repo-root) (($# >= 2)) || fail "--repo-root requires a path"; repo_requested="$2"; shift 2 ;;
    --user) (($# >= 2)) || fail "--user requires an account"; service_user="$2"; shift 2 ;;
    -h|--help)
      printf 'Usage: %s [--repo-root ABSOLUTE_PATH] [--user USER]\n' "$(basename -- "$0")"
      exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
done

[[ "${repo_requested}" == /* ]] || fail "--repo-root must be absolute"
repo_root="$(readlink -f -- "${repo_requested}" 2>/dev/null || true)"
[[ -d "${repo_root}" ]] || fail "repository not found: ${repo_requested}"
[[ "${repo_root}" =~ ^/[A-Za-z0-9._/+,:=@-]+$ ]] || fail "repository path contains unsafe characters"
[[ "${service_user}" =~ ^[a-z_][a-z0-9_-]*[$]?$ ]] || fail "invalid service user"
id "${service_user}" >/dev/null 2>&1 || fail "service user does not exist"
(( $(id -u -- "${service_user}") != 0 )) || fail "workers must not run as root"
service_group="$(id -gn -- "${service_user}")"
[[ -x "${repo_root}/.venv/bin/python" ]] || fail "run bootstrap.sh first"
[[ -r "${repo_root}/.env" ]] || fail ".env is missing or unreadable"
[[ -w "${repo_root}/data" ]] || fail "data directory is not writable"

if ((EUID == 0)); then admin=(); else command -v sudo >/dev/null || fail "sudo is required"; admin=(sudo); fi

render_unit() {
  local template="$1" target="$2"
  [[ -f "${template}" ]] || fail "missing template: ${template}"
  sed \
    -e "s|@JARVIS_USER@|${service_user}|g" \
    -e "s|@JARVIS_GROUP@|${service_group}|g" \
    -e "s|@JARVIS_REPO_ROOT@|${repo_root}|g" \
    "${template}" | "${admin[@]}" tee "${target}" >/dev/null
  "${admin[@]}" chown root:root "${target}"
  "${admin[@]}" chmod 0644 "${target}"
}

render_unit "${script_dir}/jarvis-ticket-review.service.template" "/etc/systemd/system/jarvis-ticket-review.service"
render_unit "${script_dir}/jarvis-plane-sync.service.template" "/etc/systemd/system/jarvis-plane-sync.service"
"${admin[@]}" systemctl daemon-reload

if grep -Eiq '^[[:space:]]*ACTION_TICKET_REVIEW_ENABLED[[:space:]]*=[[:space:]]*(true|1|yes|on)[[:space:]]*$' "${repo_root}/.env"; then
  "${admin[@]}" systemctl enable --now jarvis-ticket-review.service
  log "enabled jarvis-ticket-review.service"
else
  log "review worker installed but disabled; set ACTION_TICKET_REVIEW_ENABLED=true before enabling it"
fi

if grep -Eiq '^[[:space:]]*PLANE_ENABLED[[:space:]]*=[[:space:]]*(true|1|yes|on)[[:space:]]*$' "${repo_root}/.env"; then
  "${admin[@]}" systemctl enable --now jarvis-plane-sync.service
  log "enabled jarvis-plane-sync.service"
else
  log "Plane worker installed but disabled; set PLANE_ENABLED=true before enabling it"
fi

