#!/usr/bin/env bash
# Install a user-local launcher and desktop entry for the native terminal.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
BIN_DIR="${HOME}/.local/bin"
APP_DIR="${HOME}/.local/share/applications"

mkdir -p "$BIN_DIR" "$APP_DIR"

cat > "${BIN_DIR}/agent-terminal-native" <<WRAPPER
#!/usr/bin/env bash
exec "${REPO_ROOT}/bin/agent-terminal-native" "\$@"
WRAPPER
chmod +x "${BIN_DIR}/agent-terminal-native"

cat > "${BIN_DIR}/sls" <<WRAPPER
#!/usr/bin/env bash
exec "${REPO_ROOT}/bin/sls" "\$@"
WRAPPER
chmod +x "${BIN_DIR}/sls"

sed "s|^Exec=.*|Exec=${BIN_DIR}/agent-terminal-native|" \
  "${SCRIPT_DIR}/agent-terminal-native.desktop" \
  > "${APP_DIR}/agent-terminal-native.desktop"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APP_DIR" || true
fi

echo "Installed ${BIN_DIR}/agent-terminal-native"
echo "Installed ${APP_DIR}/agent-terminal-native.desktop"
echo "Installed ${BIN_DIR}/sls"
echo "For cd-on-exit, add the shell function from docs/smart-ls.md" \
     "to your .bashrc."
