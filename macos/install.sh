#!/usr/bin/env bash
# Installs the ClipCascade client LaunchAgent for the current macOS user.
#
# - Substitutes the real repo path + home directory into the plist's
#   placeholders (see comments in com.clipcascade.client.plist).
# - Copies the result into ~/Library/LaunchAgents/.
# - Loads it with launchctl so it starts now and on every login.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

SRC_PLIST="${SCRIPT_DIR}/com.clipcascade.client.plist"
LAUNCH_AGENTS_DIR="${HOME}/Library/LaunchAgents"
DEST_PLIST="${LAUNCH_AGENTS_DIR}/com.clipcascade.client.plist"

if [[ ! -f "${SRC_PLIST}" ]]; then
  echo "ERROR: source plist not found at ${SRC_PLIST}" >&2
  exit 1
fi

mkdir -p "${LAUNCH_AGENTS_DIR}"

sed \
  -e "s#/ABSOLUTE/PATH/TO/REPO/macos/run-client.sh#${REPO_ROOT}/macos/run-client.sh#" \
  -e "s#/Users/PLACEHOLDER_USER#${HOME}#g" \
  "${SRC_PLIST}" > "${DEST_PLIST}"

echo "Installed ${DEST_PLIST}"
echo "  ProgramArguments -> ${REPO_ROOT}/macos/run-client.sh"
echo "  Logs             -> ${HOME}/Library/Logs/clipcascade.log"

# Unload first in case a previous version is already loaded, then load fresh.
launchctl unload "${DEST_PLIST}" >/dev/null 2>&1 || true
launchctl load "${DEST_PLIST}"

echo "Loaded com.clipcascade.client via launchctl."
echo "Check status with: launchctl list | grep com.clipcascade.client"
