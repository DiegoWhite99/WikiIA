"""Revisa que los enlaces de los modelos y de las empresas abran de verdad.

    python -m wikiia enlaces            # revisa los que nunca se revisaron o llevan más de 14 días
    python -m wikiia enlaces --todos    # revisa todos otra vez

Cada resultado queda en la tabla enlaces_estado (ok = 1 abre · 0 roto · NULL no se pudo confirmar).
La app y el calendario se saltan los enlaces rotos y usan el siguiente disponible.

Cómo decide:
- Hugging Face: 200 = existe. 401/404 = no existe o es privado (HF responde 401 a lo que no existe).
- OpenRouter y el resto: 2xx/3xx = abre. 404/410, dominio inexistente, conexión rechazada o
  certificado inválido = roto. 401/403/429 y similares = la página existe pero bloquea robots (no se
  marca como rota). Si no responde a tiempo, queda sin confirmar y se revisa en la próxima vuelta.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING
from urllib.parse import quote, urlparse

from . import db

if TYPE_CHECKING:  # requests se importa adentro: el calendario (GitHub Actions) solo usa rotos() y la librería estándar
    import requests

DIAS_VIGENCIA = 14
HILOS = 6
# Algunas webs devuelven 403 a cualquier cliente que no parezca navegador: se usa uno común.
CABECERAS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "es,en;q=0.8",
}
BLOQUEA_ROBOTS = {401, 403, 405, 406, 429, 451, 999}

def recolectar(con) -> set[str]:
    """Todas las URLs que la app muestra para modelos (visibles) y empresas."""
    urls: set[str] = set()
    for f in con.execute("SELECT hf_id, enlaces FROM modelos WHERE oculto=0"):
        if f["hf_id"]:
            urls.add(f"https://huggingface.co/{f['hf_id']}")
        for valor in db.cargar_json(f["enlaces"], {}).values():
            if isinstance(valor, str) and valor.startswith("http"):
                urls.add(valor)
    for f in con.execute("SELECT web, chat_url, api_url FROM empresas"):
        urls.update(u for u in f if u and u.startswith("http"))
    return urls


def revisar_url(url: str, sesion: requests.Session) -> tuple[int | None, int | None, str]:
    """Devuelve (ok, código HTTP, detalle)."""
    import requests

    host = urlparse(url).netloc.lower()
    es_hf = host == "huggingface.co"
    respuesta = None
    for intento in range(3):
        try:
            respuesta = sesion.head(url, timeout=15, allow_redirects=True)
            # Hay servidores que no saben responder HEAD: se confirma con GET (sin descargar la página).
            if not es_hf and respuesta.status_code in BLOQUEA_ROBOTS | {404, 400, 500, 501, 502, 503}:
                respuesta = sesion.get(url, timeout=20, allow_redirects=True, stream=True)
                respuesta.close()
        except requests.exceptions.SSLError:
            return 0, None, "certificado de seguridad inválido"
        except requests.exceptions.ConnectionError as e:
            if "NameResolution" in repr(e) or "Name or service not known" in repr(e) or "getaddrinfo" in repr(e):
                return 0, None, "el dominio no existe"
            if intento == 2:
                return 0, None, "no acepta conexiones"
            time.sleep(2)
            continue
        except requests.exceptions.Timeout:
            if intento == 2:
                return None, None, "no respondió a tiempo"
            continue
        except requests.RequestException as e:
            return None, None, e.__class__.__name__
        if respuesta.status_code == 429 and intento < 2:  # Hugging Face pide calma: se espera y se reintenta
            time.sleep(min(float(respuesta.headers.get("retry-after", 10) or 10), 60))
            continue
        break
    if respuesta is None:
        return None, None, "sin respuesta"
    codigo = respuesta.status_code
    if codigo < 400:
        return 1, codigo, "ok"
    if es_hf and codigo in (401, 404):
        return 0, codigo, "no existe en Hugging Face (o es privado)"
    if codigo in (404, 410):
        return 0, codigo, "página no encontrada"
    if codigo in BLOQUEA_ROBOTS:
        return None, codigo, "existe, pero no deja revisarla automáticamente"
    return None, codigo, "el servidor falló (se revisa de nuevo después)"


def _modelos_por_autor(sesion: "requests.Session", autores: set[str]) -> dict[str, set[str]]:
    """Los modelos publicados por cada autor de Hugging Face: una consulta por autor y no una por modelo
    (Hugging Face frena a quien hace cientos de consultas seguidas)."""
    import requests

    existentes: dict[str, set[str]] = {}
    for autor in sorted(autores):
        url, ids, intentos = f"https://huggingface.co/api/models?author={quote(autor)}&limit=1000", set(), 0
        try:
            while url and intentos < 4:
                r = sesion.get(url, timeout=30)
                if r.status_code == 429:
                    intentos += 1
                    time.sleep(min(float(r.headers.get("retry-after", 15) or 15), 60))
                    continue
                if r.status_code != 200:
                    break
                ids |= {m["id"].lower() for m in r.json() if isinstance(m, dict) and m.get("id")}
                url = r.links.get("next", {}).get("url")
            else:
                if not url:
                    existentes[autor.lower()] = ids
        except (requests.RequestException, ValueError):
            continue
    return existentes


def revisar(con, todos: bool = False, limite: int | None = None, hilos: int = HILOS, progreso=print) -> dict:
    """Revisa los enlaces pendientes (o todos) y guarda el resultado. Devuelve el conteo."""
    import requests
    import requests.adapters

    urls = recolectar(con)
    # Lo que ya no se muestra (una web que cambió, un modelo que se fue) sale de la tabla.
    con.executemany("DELETE FROM enlaces_estado WHERE url = ?",
                    [(u,) for (u,) in con.execute("SELECT url FROM enlaces_estado").fetchall() if u not in urls])
    if not todos:
        frescos = {f["url"] for f in con.execute(
            "SELECT url FROM enlaces_estado WHERE revisado >= datetime('now', ?) AND ok IS NOT NULL",
            (f"-{DIAS_VIGENCIA} days",))}
        urls -= frescos
    pendientes = sorted(urls)[:limite] if limite else sorted(urls)
    conteo = {"revisados": 0, "abren": 0, "rotos": 0, "sin_confirmar": 0}
    if not pendientes:
        return conteo
    sesion = requests.Session()
    sesion.headers.update(CABECERAS)
    adaptador = requests.adapters.HTTPAdapter(pool_connections=hilos, pool_maxsize=hilos * 2)
    sesion.mount("https://", adaptador)
    sesion.mount("http://", adaptador)
    progreso(f"Revisando {len(pendientes)} enlaces…")
    # Hugging Face: primero la lista de cada autor; solo lo que no aparece ahí se revisa uno por uno
    # (puede ser un repositorio renombrado, que redirige, o uno que ya no existe).
    hf = {u: urlparse(u).path.strip("/") for u in pendientes if urlparse(u).netloc.lower() == "huggingface.co"}
    por_autor = _modelos_por_autor(sesion, {r.split("/")[0] for r in hf.values() if "/" in r})
    confirmados = {u for u, repo in hf.items() if repo.lower() in por_autor.get(repo.split("/")[0].lower(), ())}

    def revisar_uno(url: str):
        return (1, 200, "ok") if url in confirmados else revisar_url(url, sesion)

    with ThreadPoolExecutor(hilos) as grupo:
        for n, (url, (ok, codigo, detalle)) in enumerate(
                zip(pendientes, grupo.map(revisar_uno, pendientes)), 1):
            con.execute(
                "INSERT INTO enlaces_estado(url, ok, codigo, detalle, revisado) VALUES(?,?,?,?,datetime('now')) "
                "ON CONFLICT(url) DO UPDATE SET ok=excluded.ok, codigo=excluded.codigo, detalle=excluded.detalle, "
                "revisado=excluded.revisado", (url, ok, codigo, detalle))
            conteo["revisados"] += 1
            conteo["abren" if ok == 1 else "rotos" if ok == 0 else "sin_confirmar"] += 1
            if ok == 0:
                progreso(f"   ✗ {url} → {detalle}")
            if n % 100 == 0:
                con.commit()
                progreso(f"   {n}/{len(pendientes)}")
    con.commit()
    return conteo


def rotos(con) -> frozenset[str]:
    """URLs confirmadas como rotas (la app y el calendario las saltan). La tabla la crea db.conectar()."""
    return frozenset(f[0] for f in con.execute("SELECT url FROM enlaces_estado WHERE ok = 0"))
