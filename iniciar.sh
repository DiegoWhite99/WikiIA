#!/usr/bin/env bash
# Abre WikiIA en el navegador (http://localhost:8501)
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/streamlit ]; then
  echo "Instalando dependencias por primera vez…"
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
exec .venv/bin/streamlit run app.py
