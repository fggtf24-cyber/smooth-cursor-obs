#!/bin/sh
# Запускается в контейнере Arch из .github/workflows/niri.yml: собирает niri (один раз, дальше из кэша) и гоняет
# linux/ci_check.py niri. Программный рендер niri 26.04 отвергает одной проверкой — её убираем, видеокарты в CI нет.
set -eux
pacman -Syu --noconfirm --needed niri pipewire wireplumber seatd mesa dbus python python-numpy \
  python-pillow python-jeepney python-yaml python-xlib >/dev/null
if [ ! -x niri-build/bin/niri ]; then
  pacman -S --noconfirm --needed rust clang git pkgconf >/dev/null
  git clone -q --depth 1 -b "$NIRI" https://github.com/niri-wm/niri niri-build/src
  sed -i "s/!egl_device.is_software()/true/" niri-build/src/src/backend/tty.rs
  if grep -q is_software niri-build/src/src/backend/tty.rs; then exit 1; fi
  cargo build -q --release --locked --manifest-path niri-build/src/Cargo.toml
  mkdir -p niri-build/bin && cp niri-build/src/target/release/niri niri-build/bin/
fi
export PATH="$PWD/niri-build/bin:$PATH"
pacman -Q pipewire mesa && niri --version
seatd -g root &
sleep 1
export LIBSEAT_BACKEND=seatd
exec dbus-run-session -- python linux/ci_check.py niri
