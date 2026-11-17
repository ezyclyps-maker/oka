#!/usr/bin/env bash
# Packs the Odeifried AI into an installable package:  scripts/build-deb.sh [output-folder]
# Install it on Debian/Ubuntu with:  sudo apt install ./odeifried_<version>_all.deb
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$(mkdir -p "${1:-$REPO/dist}" && cd "${1:-$REPO/dist}" && pwd)"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$REPO/pyproject.toml" | head -1)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
P="$T/pkg"

install -d "$P/DEBIAN" "$P/usr/bin" "$P/usr/lib/python3/dist-packages" "$P/usr/lib/systemd/user" \
    "$P/usr/share/applications" "$P/usr/share/dbus-1/services" "$P/etc/xdg/autostart" \
    "$P/usr/share/icons/hicolor/scalable/apps" "$P/usr/share/doc/odeifried" "$P/var/log/odeifried"
cp -r "$REPO/odeifried" "$P/usr/lib/python3/dist-packages/"
find "$P" -name __pycache__ -prune -exec rm -rf {} +
printf '#!/usr/bin/python3\nimport sys\nfrom odeifried.cli import main\nsys.exit(main())\n' > "$P/usr/bin/odeifried"
chmod 755 "$P/usr/bin/odeifried"
cp "$REPO/packaging/odeifried-core.service" "$P/usr/lib/systemd/user/"
cp "$REPO/packaging/org.odeifried.Bar.desktop" "$P/usr/share/applications/"
cp "$REPO/packaging/org.odeifried.Bar.service" "$P/usr/share/dbus-1/services/"
cp "$REPO/packaging/odeifried-autostart.desktop" "$P/etc/xdg/autostart/odeifried.desktop"
cp "$REPO/os/branding/logo.svg" "$P/usr/share/icons/hicolor/scalable/apps/odeifried.svg"
cp "$REPO/NOTICE" "$REPO/README.md" "$P/usr/share/doc/odeifried/"
chmod 1777 "$P/var/log/odeifried"        # any user's AI may add its own log files; none can remove another's

cat > "$P/DEBIAN/control" <<CTL
Package: odeifried
Version: $VERSION
Architecture: all
Maintainer: Odeifried <dev@odeifried.local>
Depends: python3 (>= 3.11), python3-gi, gir1.2-gtk-3.0, gir1.2-atspi-2.0, at-spi2-core, xdotool, libglib2.0-bin, libgtk-3-bin, xdg-utils, curl, zstd
Recommends: playerctl, flatpak, wireplumber, scrot
Section: utils
Priority: optional
Description: Odeifried AI - tell your computer what to do
 A local AI assistant that operates the desktop: an instant command router,
 an agent driven by a local model (Ollama), safety tiers, undo and an audit log.
CTL
dpkg-deb --root-owner-group --build "$P" "$OUT/odeifried_${VERSION}_all.deb" >/dev/null
echo "    $OUT/odeifried_${VERSION}_all.deb"
