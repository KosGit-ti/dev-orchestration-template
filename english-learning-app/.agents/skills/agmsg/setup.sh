#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -x "$SCRIPT_DIR/install.sh" ] || [ -f "$SCRIPT_DIR/install.sh" ]; then
  bash "$SCRIPT_DIR/install.sh" "$@"
  exit 0
fi

INSTALL_URL="${AGMSG_INSTALL_URL:-https://raw.githubusercontent.com/fujibee/agmsg/main/install.sh}"
if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$INSTALL_URL" | bash -s -- "$@"
elif command -v wget >/dev/null 2>&1; then
  wget -qO- "$INSTALL_URL" | bash -s -- "$@"
else
  echo "setup.sh: curl or wget is required when install.sh is not beside setup.sh" >&2
  exit 127
fi
