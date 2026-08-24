#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly SCRIPT_NAME="$(basename -- "$0")"
readonly SERVICE_NAME="jarvis.service"
readonly UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}"

usage() {
  cat <<EOF
Usage: ${SCRIPT_NAME} [--repo-root ABSOLUTE_PATH] [--user USER]

Install and start Jarvis as a systemd service. By default, the repository is
resolved relative to this script and the service account is the current
non-root user (or SUDO_USER when the script itself is invoked with sudo).

  --repo-root PATH  Absolute path to the Jarvis checkout.
  --user USER       Existing non-root service account.
  -h, --help        Show this help text.
EOF
}

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

log() {
  printf '[jarvis-systemd] %s\n' "$*"
}

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root_requested="$(cd -- "${script_dir}/../.." && pwd -P)"

if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
  service_user="${SUDO_USER}"
else
  service_user="$(id -un)"
fi

while (($# > 0)); do
  case "$1" in
    --repo-root)
      (($# >= 2)) || fail "--repo-root requires an absolute path"
      repo_root_requested="$2"
      shift 2
      ;;
    --user)
      (($# >= 2)) || fail "--user requires an account name"
      service_user="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

[[ "${repo_root_requested}" == /* ]] || fail "--repo-root must be an absolute path"
repo_root="$(readlink -f -- "${repo_root_requested}" 2>/dev/null || true)"
[[ -n "${repo_root}" && "${repo_root}" == /* && -d "${repo_root}" ]] \
  || fail "repository path does not resolve to an existing absolute directory: ${repo_root_requested}"

# Restrict substitution values to characters that are unambiguous in both the
# systemd unit and the renderer below. This also rejects newlines and specifiers.
[[ "${repo_root}" =~ ^/[A-Za-z0-9._/+,:=@-]+$ ]] \
  || fail "repository path contains characters unsafe for a systemd unit; use an absolute path containing only letters, digits, /, ., _, +, :, ,, =, @, or -"
[[ "${service_user}" =~ ^[a-z_][a-z0-9_-]*[$]?$ ]] || fail "invalid service user name: ${service_user}"
id "${service_user}" >/dev/null 2>&1 || fail "service user does not exist: ${service_user}"

service_uid="$(id -u -- "${service_user}")"
((service_uid != 0)) || fail "Jarvis must not run as root; pass --user with an existing non-root account"
service_group="$(id -gn -- "${service_user}")"
[[ "${service_group}" =~ ^[a-z_][a-z0-9_-]*[$]?$ ]] || fail "primary group name is unsafe for the service template: ${service_group}"

template_path="${script_dir}/jarvis.service.template"
venv_python="${repo_root}/.venv/bin/python"

[[ -f "${template_path}" ]] || fail "service template is missing: ${template_path}"
[[ -f "${repo_root}/app/main.py" ]] || fail "app/main.py is missing from ${repo_root}"
[[ -f "${repo_root}/scripts/verify_install.py" ]] || fail "install verifier is missing from ${repo_root}"
[[ -x "${venv_python}" ]] || fail "${venv_python} is unavailable; run bootstrap.sh as ${service_user} first"
[[ -f "${repo_root}/.env" ]] || fail "${repo_root}/.env is unavailable; run bootstrap.sh as ${service_user} first"
[[ -d "${repo_root}/data" && -d "${repo_root}/secrets/live" ]] \
  || fail "runtime directories are unavailable; run bootstrap.sh as ${service_user} first"
command -v systemctl >/dev/null 2>&1 || fail "systemctl is required"
command -v cmp >/dev/null 2>&1 || fail "cmp is required"

if ((EUID == 0)); then
  admin=()
else
  command -v sudo >/dev/null 2>&1 || fail "sudo is required to install a system service"
  admin=(sudo)
fi

run_as_service_user() {
  if ((EUID == service_uid)); then
    "$@"
  elif ((EUID == 0)); then
    command -v runuser >/dev/null 2>&1 || fail "runuser is required when installing as root"
    runuser -u "${service_user}" -- "$@"
  else
    sudo -u "${service_user}" -- "$@"
  fi
}

cd -- "${repo_root}"
run_as_service_user test -x "${venv_python}" \
  || fail "${service_user} cannot execute the repository virtual environment"
run_as_service_user test -r "${repo_root}/.env" \
  || fail "${service_user} cannot read ${repo_root}/.env"
run_as_service_user test -w "${repo_root}/data" \
  || fail "${service_user} cannot write ${repo_root}/data"
run_as_service_user test -w "${repo_root}/secrets/live" \
  || fail "${service_user} cannot write ${repo_root}/secrets/live"
run_as_service_user "${venv_python}" "${repo_root}/scripts/verify_install.py" \
  || fail "Jarvis install verification failed; correct the reported failures before installing the service"
run_as_service_user env SKILL_ARTIFACT_AUTO_COMPILE_ENABLED=false "${venv_python}" -c 'import app.main' \
  || fail "Jarvis cannot be imported by ${service_user} from ${repo_root}"

rendered_unit="$(
  sed \
    -e "s|@JARVIS_USER@|${service_user}|g" \
    -e "s|@JARVIS_GROUP@|${service_group}|g" \
    -e "s|@JARVIS_REPO_ROOT@|${repo_root}|g" \
    "${template_path}"
)"$'\n'

if [[ -r "${UNIT_PATH}" ]] && printf '%s' "${rendered_unit}" | cmp -s - "${UNIT_PATH}"; then
  log "${UNIT_PATH} already matches the requested configuration"
else
  log "installing ${UNIT_PATH} for ${service_user}:${service_group}"
  printf '%s' "${rendered_unit}" | "${admin[@]}" tee "${UNIT_PATH}" >/dev/null
  "${admin[@]}" chown root:root "${UNIT_PATH}"
  "${admin[@]}" chmod 0644 "${UNIT_PATH}"
fi

"${admin[@]}" systemctl daemon-reload
"${admin[@]}" systemctl enable "${SERVICE_NAME}"
"${admin[@]}" systemctl restart "${SERVICE_NAME}"

health_ready=false
for _ in {1..30}; do
  if ! "${admin[@]}" systemctl is-active --quiet "${SERVICE_NAME}"; then
    break
  fi
  if run_as_service_user "${venv_python}" -c \
    'import json, urllib.request; response = urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2); payload = json.load(response); raise SystemExit(0 if payload.get("status") == "ok" else 1)' \
    >/dev/null 2>&1; then
    health_ready=true
    break
  fi
  sleep 1
done

if [[ "${health_ready}" != true ]] || ! "${admin[@]}" systemctl is-active --quiet "${SERVICE_NAME}"; then
  fail "${SERVICE_NAME} did not become healthy and remain active; inspect it with: journalctl -u ${SERVICE_NAME} -n 100 --no-pager"
fi

log "${SERVICE_NAME} is active on http://127.0.0.1:8000"
printf '%s\n' \
  "Health check: curl --fail http://127.0.0.1:8000/health" \
  "Logs:         journalctl -u ${SERVICE_NAME} -f"
