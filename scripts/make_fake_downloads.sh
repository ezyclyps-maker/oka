#!/usr/bin/env bash
# Fills ~/Downloads with ~40 mixed test files for the Phase 1 test.
set -euo pipefail
D="$HOME/Downloads"
mkdir -p "$D"
for n in report invoice resume notes thesis manual; do echo "test" > "$D/$n.pdf"; done
for n in letter essay; do echo "test" > "$D/$n.docx"; done
echo "a,b" > "$D/budget.csv"; echo "x" > "$D/sales.xlsx"; echo "x" > "$D/pitch.pptx"
for n in holiday cat screenshot1 screenshot2 logo; do echo "x" > "$D/$n.png"; done
for n in IMG_0001 IMG_0002 IMG_0003; do echo "x" > "$D/$n.JPG"; done
for n in clip movie; do echo "x" > "$D/$n.mp4"; done
for n in song podcast; do echo "x" > "$D/$n.mp3"; done
for n in photos project; do echo "x" > "$D/$n.zip"; done
echo "x" > "$D/source.tar.gz"
echo "x" > "$D/vscode.deb"; echo "x" > "$D/tool.AppImage"; echo "x" > "$D/setup.exe"
echo "x" > "$D/script.py"; echo "x" > "$D/page.html"; echo "x" > "$D/config.json"
echo "x" > "$D/README"; echo "x" > "$D/mystery.xyz"
echo "x" > "$D/still-downloading.iso.part"
echo "Created $(ls -1 "$D" | wc -l) items in $D"
