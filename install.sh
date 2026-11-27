#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> A verificar PySide6..."
if ! python3 -c "import PySide6" >/dev/null 2>&1; then
    if command -v apt >/dev/null 2>&1; then
        sudo apt update && sudo apt install -y python3-pyside6.qtwidgets python3-pyside6.qtgui python3-pyside6.qtcore || pip3 install --user PySide6
    else
        pip3 install --user PySide6
    fi
fi

echo "==> A instalar regras udev para Keychron (3434) e Glorious (093a)..."
sudo tee /etc/udev/rules.d/99-perifericos-rgb.rules >/dev/null <<'EOF'
KERNEL=="hidraw*", SUBSYSTEM=="hidraw", ATTRS{idVendor}=="3434", MODE="0660", TAG+="uaccess"
KERNEL=="hidraw*", SUBSYSTEM=="hidraw", ATTRS{idVendor}=="093a", MODE="0660", TAG+="uaccess"
KERNEL=="event*", SUBSYSTEM=="input", ATTRS{idVendor}=="3434", TAG+="uaccess"
KERNEL=="event*", SUBSYSTEM=="input", ATTRS{idVendor}=="093a", TAG+="uaccess"
EOF
sudo udevadm control --reload-rules
sudo udevadm trigger

echo "==> A criar atalho no menu de aplicações..."
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/keychron-rgb.desktop" <<EOF
[Desktop Entry]
Name=Periféricos RGB
Comment=Controlo RGB, bateria e áudio para Keychron V6 Max, Glorious Model I 2 e REDMI Neo
Exec=python3 "$APP_DIR/app.py"
Icon=$APP_DIR/icon.svg
Terminal=false
Type=Application
Categories=Utility;Settings;HardwareSettings;
Keywords=keychron;glorious;redmi;rgb;keyboard;mouse;headphones;
EOF
update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true

echo "==> A ativar serviço silencioso de automação (systemd --user)..."
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/keychron-rgb-auto.service" <<EOF
[Unit]
Description=Perifericos RGB Automacao Silenciosa
After=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 "$APP_DIR/automation.py" --daemon
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now keychron-rgb-auto.service

echo "Instalação concluída! Procura por 'Periféricos RGB' no menu de aplicações."
