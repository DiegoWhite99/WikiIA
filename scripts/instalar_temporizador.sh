#!/usr/bin/env bash
# Activa la actualización automática de WikiIA cada 6 horas (temporizador de systemd de TU usuario, sin sudo).
# Si el PC estaba apagado a la hora programada, se ejecuta al encenderlo.
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
UNIDADES="$HOME/.config/systemd/user"
mkdir -p "$UNIDADES"

cat > "$UNIDADES/wikiia-actualizar.service" <<EOF
[Unit]
Description=WikiIA - buscar modelos de IA nuevos y avisar
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$DIR
ExecStart=$DIR/.venv/bin/python -m wikiia actualizar
EOF

cat > "$UNIDADES/wikiia-actualizar.timer" <<EOF
[Unit]
Description=WikiIA - revisar modelos nuevos cada 6 horas

[Timer]
OnCalendar=*-*-* 00/6:00:00
OnBootSec=5min
Persistent=true
RandomizedDelaySec=10min

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now wikiia-actualizar.timer
echo "✅ Actualización automática activada. Próximas ejecuciones:"
systemctl --user list-timers wikiia-actualizar.timer --no-pager
echo
echo "Ver el registro:   journalctl --user -u wikiia-actualizar.service -n 50"
echo "Desactivar:        ./scripts/desinstalar_temporizador.sh"
