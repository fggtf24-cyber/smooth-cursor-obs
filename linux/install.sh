#!/bin/sh
# Ставит Smooth Cursor для текущего пользователя: ./install.sh (права root не нужны). Удалить: ./install.sh --remove
set -eu
dest="${XDG_DATA_HOME:-$HOME/.local/share}/smooth-cursor"
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
bin="$HOME/.local/bin"

rm -rf "$dest"
rm -f "$apps/smooth-cursor.desktop" "$bin/smooth-cursor"
if [ "${1:-}" = "--remove" ]; then
    echo "Smooth Cursor удалён. Настройки остались в ~/.config/SmoothCursor"
    exit 0
fi

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
mkdir -p "$dest" "$apps" "$bin"
cp -r "$here/app/." "$dest/"
ln -s "$dest/Smooth Cursor" "$bin/smooth-cursor"
icon=$(find "$dest" -name icon.png | head -n 1)
cat > "$apps/smooth-cursor.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Smooth Cursor
Comment=Smooth cursor for OBS screen recordings
Exec="$dest/Smooth Cursor"
Icon=$icon
Categories=AudioVideo;Video;
Terminal=false
EOF

echo "Готово: Smooth Cursor есть в меню программ."
if [ "${XDG_SESSION_TYPE:-}" = "wayland" ] && ! id -nG | grep -qw input; then
    echo "На Wayland клики и F9 читаются из /dev/input. Чтобы они работали, выполните и войдите заново:"
    echo "  sudo usermod -aG input \$USER"
fi
