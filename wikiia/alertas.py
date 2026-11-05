"""Alertas: notificación de escritorio (Linux) y Telegram opcional."""
from __future__ import annotations

import shutil
import subprocess

import requests

from . import db
from .config import Config
from .heuristicas import ETIQUETA_ACCESO, ICONO_CAP, ETIQUETAS_CAP, estrellas, num, texto


def escritorio(titulo: str, mensaje: str) -> bool:
    if not shutil.which("notify-send"):
        return False
    try:
        subprocess.run(
            ["notify-send", "-a", "WikiIA", "-i", "dialog-information", titulo, mensaje],
            timeout=10, check=False, capture_output=True,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def telegram(cfg: Config, texto: str) -> bool:
    if not (cfg.telegram_token and cfg.telegram_chat_id):
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{cfg.telegram_token}/sendMessage",
            json={"chat_id": cfg.telegram_chat_id, "text": texto, "disable_web_page_preview": True},
            timeout=15,
        )
        return r.status_code == 200
    except requests.RequestException:
        return False


def texto_precio(fila, corto: bool = False) -> str:
    if texto(fila["precio_texto"]):
        return fila["precio_texto"]
    entrada, salida = num(fila["precio_entrada"]), num(fila["precio_salida"])
    if fila["acceso"] == "local":
        return "Gratis (local)" if corto else "Gratis (lo corres en tu equipo)"
    if salida is None and entrada is None:
        return "—"
    if not salida and not entrada:
        return "Gratis" if corto else "Gratis (con límites de uso)"
    precio = f"${entrada or 0:,.2f} / ${salida or 0:,.2f}"
    return precio if corto else f"{precio} por 1M tokens (entrada / salida)"


def mensaje_modelo(con, modelo_id: str) -> tuple[str, str]:
    f = con.execute(
        "SELECT m.*, e.nombre AS empresa, p.nombre AS pais, p.bandera FROM modelos m "
        "LEFT JOIN empresas e ON e.id=m.empresa_id LEFT JOIN paises p ON p.codigo=e.pais WHERE m.id=?",
        (modelo_id,),
    ).fetchone()
    capacidades = [ETIQUETAS_CAP[c] for c in ICONO_CAP if f[c]]
    titulo = f"🆕 WikiIA: {f['nombre']}"
    lineas = [
        f"{f['bandera'] or ''} {f['pais'] or 'Sin país'} · {f['empresa'] or f['empresa_id']}",
        f"{ETIQUETA_ACCESO.get(f['acceso'], f['acceso'])} · Potencia {estrellas(f['potencia'])}",
        f"Sirve para: {', '.join(capacidades) or 'por confirmar'}",
        f"Precio: {texto_precio(f)}",
    ]
    if f["resumen"]:
        lineas.append(f["resumen"])
    return titulo, "\n".join(lineas)


def enviar(con, cfg: Config, titulo: str, mensaje: str, modelo_id: str | None = None) -> list[str]:
    canales = []
    if escritorio(titulo, mensaje):
        canales.append("escritorio")
    if telegram(cfg, f"{titulo}\n\n{mensaje}"):
        canales.append("telegram")
    con.execute(
        "INSERT INTO alertas(modelo_id, fecha, titulo, mensaje, canales) VALUES(?,?,?,?,?)",
        (modelo_id, db.ahora(), titulo, mensaje, ",".join(canales)),
    )
    con.commit()
    return canales
