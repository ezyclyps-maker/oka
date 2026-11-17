#!/usr/bin/env bash
# Installs Odeifried for the current user inside the Ubuntu VM.
# Run from the project folder:  bash scripts/install.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> System packages (asks for your password once)"
sudo apt-get update -qq
sudo apt-get install -y python3-venv python3-gi gir1.2-atspi-2.0 at-spi2-core \
    xdotool scrot imagemagick zenity

echo "==> Turn on accessibility so apps expose their buttons (AT-SPI)"
gsettings set org.gnome.desktop.interface toolkit-accessibility true || true

echo "==> Python environment in ~/.local/share/odeifried/venv"
VENV="$HOME/.local/share/odeifried/venv"
python3 -m venv --system-site-packages "$VENV"      # system site-packages = access to python3-gi (AT-SPI)
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -e ".[dev]"

mkdir -p "$HOME/.local/bin"
ln -sf "$VENV/bin/odeifried" "$HOME/.local/bin/odeifried"

echo "==> Log folder /var/log/odeifried (owned by you; Phase 3 moves this to a dedicated user)"
sudo mkdir -p /var/log/odeifried
sudo chown "$USER":"$USER" /var/log/odeifried

mkdir -p "$HOME/.config/odeifried"
[ -f "$HOME/.config/odeifried/config.toml" ] || cp odeifried/default_config.toml "$HOME/.config/odeifried/config.toml"

echo
echo "Installed. Open a NEW terminal, then run:  odeifried doctor"
