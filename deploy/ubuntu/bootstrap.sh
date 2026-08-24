#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly SCRIPT_NAME="$(basename -- "$0")"

usage() {
  cat <<EOF
Usage: ${SCRIPT_NAME} [--install-apt] [--python PYTHON]

Prepare this Jarvis checkout on Ubuntu 24.04.

  --install-apt     Explicitly install Python, venv/pip, curl, and CA certificates
                    with apt. No apt command runs unless this flag is supplied.
  --python PYTHON   Python interpreter used to create .venv (default: python3).
  -h, --help        Show this help text.

Run this script as the non-root user that will run Jarvis. The script does not
install Ollama, download models, or configure remote-pipe integrations.
EOF
}

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

log() {
  printf '[jarvis-bootstrap] %s\n' "$*"
}

install_apt=false
python_requested="python3"

while (($# > 0)); do
  case "$1" in
    --install-apt)
      install_apt=true
      shift
      ;;
    --python)
      (($# >= 2)) || fail "--python requires an interpreter path or command"
      python_requested="$2"
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

((EUID != 0)) || fail "run this script as the non-root Jarvis service user; use --install-apt for explicit sudo-based package installation"

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd -- "${script_dir}/../.." && pwd -P)"

[[ "${repo_root}" == /* ]] || fail "repository root did not resolve to an absolute path: ${repo_root}"
[[ -f "${repo_root}/requirements.txt" ]] || fail "requirements.txt is missing from ${repo_root}"
[[ -f "${repo_root}/.env.example" ]] || fail ".env.example is missing from ${repo_root}"
[[ -s "${repo_root}/app/prompts/skills/critical_skills.md" ]] \
  || fail "checked-in critical skill artifact is missing from ${repo_root}"
[[ -s "${repo_root}/app/prompts/micro_jarvis_skills.md" ]] \
  || fail "checked-in micro skill artifact is missing from ${repo_root}"

[[ -r /etc/os-release ]] || fail "cannot identify the operating system because /etc/os-release is unavailable"
# /etc/os-release is trusted operating-system metadata on the target host.
# shellcheck disable=SC1091
source /etc/os-release
[[ "${ID:-}" == "ubuntu" && "${VERSION_ID:-}" == "24.04" ]] || fail "this installer supports Ubuntu 24.04; detected ${PRETTY_NAME:-unknown operating system}"

if [[ "${install_apt}" == true ]]; then
  command -v sudo >/dev/null 2>&1 || fail "sudo is required by --install-apt"
  log "installing the explicitly requested Ubuntu Python packages"
  sudo apt-get update
  sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    python3 \
    python3-pip \
    python3-venv
else
  log "skipping apt packages (pass --install-apt to install the Python prerequisites)"
fi

python_command="$(command -v -- "${python_requested}" 2>/dev/null || true)"
[[ -n "${python_command}" ]] || fail "${python_requested} was not found; rerun with --install-apt or provide --python"
"${python_command}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
  || fail "Jarvis requires Python 3.11 or newer"

cd -- "${repo_root}"

mkdir -p -- "${repo_root}/data" "${repo_root}/secrets/live"
chmod 700 -- "${repo_root}/data" "${repo_root}/secrets" "${repo_root}/secrets/live"

if [[ -e "${repo_root}/.env" || -L "${repo_root}/.env" ]]; then
  [[ -f "${repo_root}/.env" && ! -L "${repo_root}/.env" ]] \
    || fail "existing .env is not a regular file; refusing to replace or follow it"
  chmod 0600 -- "${repo_root}/.env"
  log "preserved the existing .env content and enforced mode 0600"
else
  install -m 0600 -- "${repo_root}/.env.example" "${repo_root}/.env"
  log "created .env from .env.example with mode 0600"
fi

venv_dir="${repo_root}/.venv"
venv_python="${venv_dir}/bin/python"

if [[ ! -x "${venv_python}" ]]; then
  log "creating the repository-local Linux virtual environment at ${venv_dir}"
  "${python_command}" -m venv "${venv_dir}" \
    || fail "virtual environment creation failed; rerun with --install-apt to install python3-venv"
else
  log "reusing the repository-local virtual environment at ${venv_dir}"
fi

"${venv_python}" -c \
  'import pathlib, sys; expected = pathlib.Path(sys.argv[1]).resolve(); actual = pathlib.Path(sys.prefix).resolve(); valid = sys.platform.startswith("linux") and actual == expected and sys.version_info >= (3, 11); raise SystemExit(0 if valid else 1)' \
  "${venv_dir}" \
  || fail "${venv_dir} must be a native Linux Python 3.11+ virtual environment rooted in this repository; remove or relocate an incompatible .venv, then rerun bootstrap"

log "installing pinned Python dependencies"
PIP_DISABLE_PIP_VERSION_CHECK=1 "${venv_python}" -m pip install --requirement "${repo_root}/requirements.txt"
"${venv_python}" -m pip check

log "bootstrap complete"
printf '%s\n' \
  "Next:" \
  "  1. Edit ${repo_root}/.env for Discord and optional local-model settings." \
  "  2. Add private policy/credential files under ${repo_root}/secrets/live/." \
  "  3. Run: bash ${repo_root}/deploy/ubuntu/install-systemd.sh"
