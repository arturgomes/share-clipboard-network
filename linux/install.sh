#!/usr/bin/env bash
# Installs the ClipCascade client systemd USER unit for the current user.
#
# - Substitutes the real repo path into the unit's ExecStart placeholder
#   (see comments in clipcascade-client.service).
# - Copies the result into ~/.config/systemd/user/.
# - Reloads the user systemd daemon and enables + starts the unit now.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

SRC_UNIT="${SCRIPT_DIR}/clipcascade-client.service"
UNIT_DIR="${HOME}/.config/systemd/user"
DEST_UNIT="${UNIT_DIR}/clipcascade-client.service"

if [[ ! -f "${SRC_UNIT}" ]]; then
  echo "ERROR: source unit not found at ${SRC_UNIT}" >&2
  exit 1
fi

mkdir -p "${UNIT_DIR}"

sed "s#/ABSOLUTE/PATH/TO/REPO#${REPO_ROOT}#g" "${SRC_UNIT}" > "${DEST_UNIT}"

echo "Installed ${DEST_UNIT}"
echo "  ExecStart -> ${REPO_ROOT}/.venv-linux/bin/python3 ${REPO_ROOT}/vendor/ClipCascade_Desktop/src/main.py --gui true --polling 1"

systemctl --user daemon-reload
systemctl --user enable --now clipcascade-client.service

echo "Enabled and started clipcascade-client.service."
echo "Check status with: systemctl --user status clipcascade-client.service"
echo
echo "Known upstream bug: after a server restart, the client can get stuck"
echo "in a reconnect loop without exiting (so Restart=on-failure won't help,"
echo "since the process never actually fails/exits). If sync stops working"
echo "after restarting the server container, manually run:"
echo "  systemctl --user restart clipcascade-client.service"
