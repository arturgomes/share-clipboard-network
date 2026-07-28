#!/usr/bin/env bash
# Runs the ClipCascade desktop client on macOS.
#
# Expects the vendored + patched client source to already exist at
# ../vendor/ClipCascade_Desktop/ (relative to this repo's root) — that is
# produced by a separate lane and is not created by this script. If it's
# missing, this script fails fast with a clear message instead of a raw
# Python traceback.
#
# Creates/reuses a venv at <repo-root>/.venv-mac for the client's deps.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

VENDOR_DIR="${REPO_ROOT}/vendor/ClipCascade_Desktop"
REQUIREMENTS="${VENDOR_DIR}/src/requirements_mac.txt"
MAIN_PY="${VENDOR_DIR}/src/main.py"
VENV_DIR="${REPO_ROOT}/.venv-mac"

if [[ ! -d "${VENDOR_DIR}" ]]; then
  echo "ERROR: vendored ClipCascade client not found at:" >&2
  echo "  ${VENDOR_DIR}" >&2
  echo "This is produced by the vendoring lane of this repo (vendor/ territory)." >&2
  echo "Make sure that work has landed before running this script." >&2
  exit 1
fi

if [[ ! -f "${REQUIREMENTS}" ]]; then
  echo "ERROR: expected requirements file not found at:" >&2
  echo "  ${REQUIREMENTS}" >&2
  exit 1
fi

if [[ ! -f "${MAIN_PY}" ]]; then
  echo "ERROR: expected client entrypoint not found at:" >&2
  echo "  ${MAIN_PY}" >&2
  exit 1
fi

if [[ ! -d "${VENV_DIR}" ]]; then
  echo "Creating venv at ${VENV_DIR} ..."
  python3 -m venv "${VENV_DIR}"
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

pip install --quiet --upgrade pip
pip install --quiet -r "${REQUIREMENTS}"

exec python3 "${MAIN_PY}"
