#!/usr/bin/env bash
# Desactiva la actualización automática de WikiIA.
set -euo pipefail
systemctl --user disable --now wikiia-actualizar.timer 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/wikiia-actualizar.timer" "$HOME/.config/systemd/user/wikiia-actualizar.service"
systemctl --user daemon-reload
echo "Actualización automática desactivada."
