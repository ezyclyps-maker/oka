#!/usr/bin/env bash
# Builds the Odeifried install image (a bootable .iso file).
#
#   sudo os/build.sh            full build (smallest file, slower)
#   sudo FAST=1 os/build.sh     quick test build (bigger file, faster)
#
# Works on Debian 13 or Ubuntu 22.04+. Needs ~15 GB free disk and internet.
# Result: dist/odeifried-<version>-amd64.iso
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="${WORK:-$REPO/build/iso}"
DIST="${DIST:-$REPO/dist}"
MIRROR="${MIRROR:-http://deb.debian.org/debian}"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$REPO/pyproject.toml" | head -1)"

[ "$(id -u)" -eq 0 ] || { echo "Please run with sudo:  sudo os/build.sh"; exit 1; }

echo "==> [1/5] Build tools"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
    debootstrap cpio apt-utils xz-utils zstd rsync curl ca-certificates debian-archive-keyring >/dev/null

# We need Debian's own live-build (Ubuntu ships an old, incompatible fork).
if ! lb --version 2>/dev/null | grep -Eq '^20(2[5-9]|[3-9][0-9])'; then
    . /etc/os-release
    if [ "${ID:-}" = "debian" ]; then
        apt-get install -y -qq --no-install-recommends live-build >/dev/null
    else
        echo "    fetching live-build from Debian (checksum verified)"
        tmp="$(mktemp -d)"
        curl -fsSL "https://deb.debian.org/debian/dists/trixie/main/binary-amd64/Packages.xz" | xz -d > "$tmp/Packages"
        path="$(awk '$1=="Package:" && $2=="live-build" {f=1} f && $1=="Filename:" {print $2; exit}' "$tmp/Packages")"
        sha="$(awk '$1=="Package:" && $2=="live-build" {f=1} f && $1=="SHA256:" {print $2; exit}' "$tmp/Packages")"
        curl -fsSL -o "$tmp/live-build.deb" "https://deb.debian.org/debian/$path"
        echo "$sha  $tmp/live-build.deb" | sha256sum -c - >/dev/null
        dpkg -i "$tmp/live-build.deb" >/dev/null
        rm -rf "$tmp"
    fi
fi
echo "    live-build $(lb --version)"

echo "==> [2/5] Preparing the recipe in $WORK"
mkdir -p "$WORK" "$DIST"
cd "$WORK"
lb clean >/dev/null 2>&1 || true
rm -rf config
rsync -a "$REPO/os/config/" config/
find config/hooks -name '*.hook.chroot' -exec chmod +x {} +

echo "==> [3/5] Packaging the Odeifried AI (odeifried_${VERSION}_all.deb)"
mkdir -p config/packages.chroot
if [ -x "$REPO/scripts/build-deb.sh" ]; then
    "$REPO/scripts/build-deb.sh" "$WORK/config/packages.chroot"
fi

if [ -n "${FAST:-}" ]; then COMP_LEVEL=3; else COMP_LEVEL=19; fi

echo "==> [4/5] Configuring"
lb config \
    --mode debian \
    --distribution trixie \
    --architectures amd64 \
    --archive-areas "main contrib non-free-firmware" \
    --mirror-bootstrap "$MIRROR" \
    --mirror-chroot "$MIRROR" \
    --mirror-binary "http://deb.debian.org/debian" \
    --binary-images iso-hybrid \
    --bootloaders "syslinux,grub-efi" \
    --uefi-secure-boot auto \
    --debian-installer none \
    --apt-recommends false \
    --apt-indices false \
    --memtest none \
    --firmware-chroot false \
    --firmware-binary false \
    --linux-flavours amd64 \
    --bootappend-live "boot=live components quiet splash hostname=odeifried username=user user-fullname=Odeifried locales=en_US.UTF-8" \
    --bootappend-live-failsafe "boot=live components nomodeset noapic nosplash hostname=odeifried username=user user-fullname=Odeifried" \
    --iso-application "Odeifried" \
    --iso-publisher "Odeifried" \
    --iso-preparer "live-build" \
    --iso-volume "ODEIFRIED" \
    --image-name odeifried \
    --chroot-squashfs-compression-type zstd \
    --chroot-squashfs-compression-level "$COMP_LEVEL" \
    --cache true \
    --cache-packages true \
    --checksums sha256 \
    --security true \
    --updates true \
    --source false \
    --zsync false \
    >/dev/null

echo "==> [5/5] Building (20-60 minutes; full log: $WORK/build.log)"
if ! lb build > "$WORK/build.log" 2>&1; then
    echo "BUILD FAILED. Last lines of the log:"
    tail -40 "$WORK/build.log"
    exit 1
fi

ISO="$DIST/odeifried-$VERSION-amd64.iso"
mv "$WORK/odeifried-amd64.hybrid.iso" "$ISO"
( cd "$DIST" && sha256sum "$(basename "$ISO")" > "$(basename "$ISO").sha256" )
echo
echo "Done: $ISO  ($(du -h "$ISO" | cut -f1))"
