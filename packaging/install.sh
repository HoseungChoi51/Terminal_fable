#!/usr/bin/env bash
# Install user-local launchers, desktop metadata, and the application icon.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
APP_ID="dev.agent.TerminalNative"
BIN_DIR="${HOME}/.local/bin"
DATA_DIR="${XDG_DATA_HOME:-${HOME}/.local/share}"
APP_DIR="${DATA_DIR}/applications"
ICON_THEME_DIR="${DATA_DIR}/icons/hicolor"
ICON_DIR="${ICON_THEME_DIR}/scalable/apps"
DESKTOP_PATH="${APP_DIR}/${APP_ID}.desktop"

mkdir -p "$BIN_DIR" "$APP_DIR" "$ICON_DIR"

# Redirection follows destination symlinks. Remove these exact launcher paths
# first so an older symlink-based install cannot overwrite a file in the repo.
rm -f "${BIN_DIR}/agent-terminal-native" "${BIN_DIR}/sls"

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
  "${SCRIPT_DIR}/${APP_ID}.desktop" > "$DESKTOP_PATH"
chmod 0644 "$DESKTOP_PATH"

install -m 0644 \
  "${SCRIPT_DIR}/icons/hicolor/scalable/apps/${APP_ID}.svg" \
  "${ICON_DIR}/${APP_ID}.svg"

# Older installs used a launcher filename that did not match the GTK
# application ID. GNOME needs this exact basename to associate every Wayland
# window with one dock icon; removing the stale entry also prevents a duplicate
# app-grid result after upgrading.
LEGACY_DESKTOP_PATH="${HOME}/.local/share/applications/agent-terminal-native.desktop"
if [[ "$LEGACY_DESKTOP_PATH" != "$DESKTOP_PATH" ]]; then
  rm -f "$LEGACY_DESKTOP_PATH"
fi

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APP_DIR" || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache --force --ignore-theme-index "$ICON_THEME_DIR" \
    >/dev/null 2>&1 || true
fi

echo "Installed ${BIN_DIR}/agent-terminal-native"
echo "Installed ${DESKTOP_PATH}"
echo "Installed ${ICON_DIR}/${APP_ID}.svg"
echo "Installed ${BIN_DIR}/sls"
echo "For cd-on-exit, add the shell function from docs/smart-ls.md" \
     "to your .bashrc."
