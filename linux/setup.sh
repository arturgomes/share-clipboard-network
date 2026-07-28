#!/usr/bin/env bash
# One-time setup for the ClipCascade client on the Ubuntu desktop.
#
# Installs the system packages the client needs (GUI tray on GNOME/Wayland +
# clipboard access), then creates a venv and installs the client's Python
# deps from the vendored + patched source (produced by a separate lane, not
# by this script).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

VENDOR_DIR="${REPO_ROOT}/vendor/ClipCascade_Desktop"
REQUIREMENTS="${VENDOR_DIR}/requirements.txt"
VENV_DIR="${REPO_ROOT}/.venv-linux"

echo "Installing system packages (sudo required) ..."
sudo apt update
sudo apt install -y \
  python3-venv \
  gir1.2-gtk-3.0 \
  gnome-shell-extension-appindicator \
  wl-clipboard
# gnome-shell-extension-appindicator provides the GNOME Shell extension that
# renders AppIndicator/tray icons under Wayland; without it, --gui true has
# no tray to attach to on stock GNOME. wl-clipboard gives the client
# wl-copy/wl-paste for Wayland clipboard access. python3-venv is required to
# create the venv below.
echo
echo "NOTE: after installing gnome-shell-extension-appindicator you may need"
echo "to enable it (e.g. via the Extensions app or 'gnome-extensions enable"
echo "...@ubuntu.com') and log out/in once for the tray icon to appear."

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

if [[ ! -d "${VENV_DIR}" ]]; then
  echo "Creating venv at ${VENV_DIR} ..."
  python3 -m venv "${VENV_DIR}"
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

pip install --quiet --upgrade pip
pip install --quiet -r "${REQUIREMENTS}"

echo
echo "Setup complete. Next: linux/install.sh to install the systemd user unit."
