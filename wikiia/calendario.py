"""Calendario de lanzamientos (.ics) y su página para GitHub Pages.

    python -m wikiia.calendario --carpeta _site     # crea _site/wikiia.ics y _site/index.html

Quien se suscribe al .ics ve cada modelo nuevo como un evento en Google Calendar, Apple u Outlook,
sin dejar su correo en ningún lado. Solo usa la librería estándar: el workflow de GitHub Actions
lo corre sin instalar nada.
"""
from __future__ import annotations

import argparse
import html
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from . import db
from .enlaces import rotos as enlaces_rotos
from .config import cargar_config
from .heuristicas import ETIQUETA_ACCESO, acceso_real, enlace_principal, estrellas, texto

CONSULTA = (
    "SELECT m.*, e.nombre AS empresa, e.web AS empresa_web, e.chat_url, p.bandera, p.nombre AS pais "
    "FROM modelos m LEFT JOIN empresas e ON e.id=m.empresa_id LEFT JOIN paises p ON p.codigo=e.pais "
    "WHERE m.oculto=0 AND m.fecha >= ? ORDER BY m.fecha DESC, m.potencia DESC")


def enlaces_suscripcion(url_ics: str) -> dict[str, str]:
    """Enlaces para suscribirse: Google Calendar (un clic), webcal (Apple/Outlook) y el .ics directo."""
    webcal = "webcal://" + url_ics.split("://", 1)[-1]
    return {"google": "https://calendar.google.com/calendar/r?cid=" + quote(webcal, safe=""),
            "webcal": webcal, "ics": url_ics}


def _escapar(valor: str) -> str:
    return (valor.replace("\\", "\\\\").replace(";", r"\;").replace(",", r"\,")
            .replace("\r\n", r"\n").replace("\n", r"\n"))


def _plegar(linea: str) -> str:
    """RFC 5545: máximo 75 bytes por línea; las siguientes empiezan con un espacio."""
    partes, actual, limite = [], "", 75
    for caracter in linea:
        if len((actual + caracter).encode("utf-8")) > limite:
            partes.append(actual)
            actual, limite = "", 74  # el espacio inicial cuenta
        actual += caracter
    partes.append(actual)
    return "\r\n ".join(partes)


def _evento(f, sello: str, rotos: frozenset[str]) -> list[str]:
    dia = date.fromisoformat(f["fecha"][:10])
    enlace = enlace_principal(f, rotos) or ""
    empresa = texto(f["empresa"]) or f["empresa_id"]
    lineas_desc = [f"{texto(f['bandera'])} {texto(f['pais']) or 'Sin país'} · {empresa}".strip(),
                   f"{estrellas(f['potencia'])} · {ETIQUETA_ACCESO.get(acceso_real(f, rotos), '')}"]
    if texto(f["resumen"]):
        lineas_desc += ["", f["resumen"]]
    if enlace:
        lineas_desc += ["", f"Más información: {enlace}"]
    titulo = "🆕 " + f["nombre"] + " · " + empresa
    evento = [
        "BEGIN:VEVENT",
        f"UID:{f['id'].replace('/', '-')}@wikiia",
        f"DTSTAMP:{sello}",
        f"DTSTART;VALUE=DATE:{dia:%Y%m%d}",
        f"DTEND;VALUE=DATE:{dia + timedelta(days=1):%Y%m%d}",
        f"SUMMARY:{_escapar(titulo)}",
        f"DESCRIPTION:{_escapar(chr(10).join(lineas_desc))}",
        "TRANSP:TRANSPARENT",  # no te marca como "ocupado"
    ]
    if enlace:
        evento.append(f"URL:{enlace}")
    return evento + ["END:VEVENT"]


def generar_ics(filas, rotos: frozenset[str] = frozenset()) -> str:
    sello = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lineas = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//WikiIA//Lanzamientos de modelos de IA//ES",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        "X-WR-CALNAME:WikiIA · Nuevos modelos de IA",
        "X-WR-CALDESC:Cada modelo de inteligencia artificial nuevo\\, el día que se publicó.",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H",
    ]
    for f in filas:
        lineas += _evento(f, sello, rotos)
    lineas.append("END:VCALENDAR")
    return "\r\n".join(_plegar(l) for l in lineas) + "\r\n"


def generar_pagina(filas, cfg, rotos: frozenset[str] = frozenset()) -> str:
    """Página sencilla de GitHub Pages: botones para suscribirse y los últimos lanzamientos."""
    urls = enlaces_suscripcion(cfg.calendario_url)
    e = html.escape
    items = "\n".join(
        f'<li><span class="fecha">{e(f["fecha"][:10])}</span> {e(texto(f["bandera"]))} '
        f'<a href="{e(enlace_principal(f, rotos) or "#")}">{e(f["nombre"])}</a> '
        f'<span class="emp">· {e(texto(f["empresa"]) or f["empresa_id"])}</span></li>'
        for f in filas[:15])
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WikiIA · Calendario de lanzamientos</title>
<style>
  :root {{ --fondo:#f7f7f9; --tarjeta:#fff; --texto:#1f2130; --suave:#6b6f80; --acento:#ff4b4b; --borde:#e4e5ea; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --fondo:#0e1117; --tarjeta:#171a23; --texto:#f2f3f7; --suave:#9aa0b2; --borde:#2a2e3b; }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--fondo); color:var(--texto);
         font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }}
  main {{ max-width:720px; margin:0 auto; padding:40px 16px 56px; }}
  h1 {{ font-size:2rem; margin:0 0 4px; }}
  p.sub {{ color:var(--suave); margin:0 0 28px; }}
  .tarjeta {{ background:var(--tarjeta); border:1px solid var(--borde); border-radius:14px; padding:20px; margin-bottom:20px; }}
  .botones {{ display:flex; flex-wrap:wrap; gap:10px; margin-top:12px; }}
  .btn {{ display:inline-block; padding:10px 16px; border-radius:10px; text-decoration:none; font-weight:600;
         border:1px solid var(--borde); color:var(--texto); }}
  .btn.principal {{ background:var(--acento); border-color:var(--acento); color:#fff; }}
  code {{ display:block; margin-top:10px; padding:10px; border-radius:8px; background:var(--fondo);
          border:1px solid var(--borde); overflow-wrap:anywhere; font-size:.9rem; }}
  ul {{ list-style:none; padding:0; margin:0; }}
  li {{ padding:8px 0; border-bottom:1px solid var(--borde); }}
  li:last-child {{ border-bottom:0; }}
  .fecha {{ color:var(--suave); font-variant-numeric:tabular-nums; margin-right:6px; }}
  .emp {{ color:var(--suave); }}
  a {{ color:inherit; }}
  small {{ color:var(--suave); }}
</style>
</head>
<body>
<main>
  <h1>🌐 WikiIA</h1>
  <p class="sub">Cada modelo de inteligencia artificial nuevo, directo en tu calendario.</p>
  <section class="tarjeta">
    <strong>🔔 Suscríbete al calendario de lanzamientos</strong>
    <div class="botones">
      <a class="btn principal" href="{e(urls['google'])}">📅 Añadir a Google Calendar</a>
      <a class="btn" href="{e(urls['webcal'])}">🍎 Apple / Outlook</a>
      <a class="btn" href="{e(cfg.app_url)}">🌐 Abrir WikiIA</a>
    </div>
    <small>¿Otra aplicación? Agrégalo como calendario por URL con este enlace:</small>
    <code>{e(urls['ics'])}</code>
  </section>
  <section class="tarjeta">
    <strong>🆕 Últimos lanzamientos</strong>
    <ul>
{items}
    </ul>
  </section>
  <small>Se actualiza solo cada vez que WikiIA detecta modelos nuevos.</small>
</main>
</body>
</html>
"""


def publicar(carpeta: Path, dias: int = 365) -> int:
    cfg = cargar_config()
    con = db.conectar()
    desde = (date.today() - timedelta(days=dias)).isoformat()
    filas = con.execute(CONSULTA, (desde,)).fetchall()
    rotos = enlaces_rotos(con)
    con.close()
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / "wikiia.ics").write_text(generar_ics(filas, rotos), encoding="utf-8", newline="")
    (carpeta / "index.html").write_text(generar_pagina(filas, cfg, rotos), encoding="utf-8")
    return len(filas)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="wikiia.calendario", description=__doc__.splitlines()[0])
    parser.add_argument("--carpeta", default="_site", help="dónde dejar wikiia.ics e index.html")
    parser.add_argument("--dias", type=int, default=365, help="incluir los publicados en los últimos N días")
    args = parser.parse_args(argv)
    n = publicar(Path(args.carpeta), args.dias)
    print(f"Calendario con {n} lanzamientos → {args.carpeta}/wikiia.ics e index.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
