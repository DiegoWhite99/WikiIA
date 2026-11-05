"""Potencia medida con un benchmark independiente: el Intelligence Index de Artificial Analysis.

    python -m wikiia benchmarks      # descarga el índice y recalcula la potencia

Por qué: la potencia que estima la IA leyendo la documentación compara cada modelo con su propia familia
("el más grande de ERNIE") y no con el mundo. El índice de Artificial Analysis mide a todos con las mismas
pruebas, así que ordena de verdad.

- API gratis con clave (https://artificialanalysis.ai, 100 consultas al día): variable o Secret
  ARTIFICIAL_ANALYSIS_API_KEY. Pide mencionarlos: la app los cita junto al ranking.
- Lo descargado queda en la tabla `benchmarks`; si un día la API no responde, se usa lo último guardado.
- Cada modelo de WikiIA se empareja por nombre (sin importar el orden de las palabras ni variantes como
  "(high)" o "Thinking"). El modelo guarda su índice en `indice_ia`.
- Estrellas con índice (relativas al mejor modelo medido): 5 ≥ 85 % · 4 ≥ 70 % · 3 ≥ 50 % · 2 ≥ 30 % · 1.
- Sin índice la potencia es una estimación (IA, reglas o catálogo manual) y nunca pasa de 4: el 5 queda para
  la frontera medida.
- Lo que el usuario corrigió a mano ("potencia" en `manual`) nunca se toca.
"""
from __future__ import annotations

import re
import time
from collections import defaultdict

from . import db

URL = "https://artificialanalysis.ai/api/v2/language/models/free"
FUENTE = "benchmark (Artificial Analysis)"
MAX_PAGINAS = 15            # 4 actualizaciones al día × 15 = 60, por debajo del límite gratis de 100
TOPE_ESTIMADA = 4
UMBRALES = ((0.85, 5), (0.70, 4), (0.50, 3), (0.30, 2))

# Palabras que no cambian qué modelo es: modos de razonamiento, "instruct", "preview"…
_VARIANTES = {"reasoning", "nonreasoning", "non", "thinking", "high", "low", "medium", "minimal", "xhigh", "max",
              "adaptive", "instruct", "chat", "it", "hf", "fp8", "bf16", "preview", "latest", "model", "the"}
_GENERICAS_CREADOR = {"ai", "labs", "lab", "inc", "research", "technologies", "technology", "the", "of", "for"}


class ErrorBenchmark(Exception):
    pass


def firma(nombre: str) -> frozenset[str]:
    """Las palabras que identifican al modelo, sin orden: 'Claude 4.5 Sonnet' == 'Claude Sonnet 4.5'."""
    texto_limpio = re.sub(r"\([^)]*\)", " ", (nombre or "").lower())     # "(high)", "(Sep '25)"
    texto_limpio = texto_limpio.split(":", 1)[-1] if ":" in texto_limpio else texto_limpio  # "OpenAI: GPT-5"
    palabras = re.findall(r"[a-z]+|\d+(?:\.\d+)*", texto_limpio)
    return frozenset(p for p in palabras if p not in _VARIANTES)


def firma_slug(slug: str) -> frozenset[str]:
    """'claude-4-5-sonnet' → como 'claude 4.5 sonnet' (sin unir fechas como 'k2-0905')."""
    return firma(re.sub(r"(\d)-(\d)(?!\d)", r"\1.\2", slug or ""))


def _palabras_creador(nombre: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (nombre or "").lower())) - _GENERICAS_CREADOR


def descargar(clave: str, sesion) -> list[dict]:
    """Todas las páginas del índice gratis. Lanza ErrorBenchmark con un mensaje claro si falla."""
    modelos, pagina = [], 1
    while pagina <= MAX_PAGINAS:
        r = sesion.get(URL, params={"page": pagina}, timeout=30, headers={"x-api-key": clave})
        if r.status_code in (401, 403):
            raise ErrorBenchmark("Artificial Analysis no aceptó la clave (revisa ARTIFICIAL_ANALYSIS_API_KEY)")
        if r.status_code == 429:
            raise ErrorBenchmark("Artificial Analysis: se acabó el cupo gratis de hoy; se usa lo último guardado")
        if not r.ok:
            raise ErrorBenchmark(f"Artificial Analysis no respondió bien (código {r.status_code})")
        cuerpo = r.json()
        datos = cuerpo.get("data") if isinstance(cuerpo, dict) else cuerpo
        modelos += [m for m in (datos or []) if isinstance(m, dict)]
        paginacion = cuerpo.get("pagination") or {} if isinstance(cuerpo, dict) else {}
        if not paginacion.get("has_more"):
            break
        pagina += 1
        time.sleep(0.5)
    return modelos


def guardar(con, modelos: list[dict]) -> int:
    """Guarda en la tabla benchmarks (la crea db.conectar) los que traen índice. Devuelve cuántos."""
    n = 0
    for m in modelos:
        indice = (m.get("evaluations") or {}).get("artificial_analysis_intelligence_index")
        if indice is None or not m.get("name"):
            continue
        creador = m.get("model_creator") or {}
        con.execute(
            "INSERT INTO benchmarks(aa_id, nombre, slug, creador, indice, publicado, actualizado) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(aa_id) DO UPDATE SET nombre=excluded.nombre, slug=excluded.slug, "
            "creador=excluded.creador, indice=excluded.indice, publicado=excluded.publicado, "
            "actualizado=excluded.actualizado",
            (str(m.get("id") or m.get("slug") or m["name"]), m["name"], m.get("slug"),
             creador.get("name") if isinstance(creador, dict) else str(creador), float(indice),
             m.get("release_date"), db.ahora()))
        n += 1
    return n


def emparejar(con) -> dict[str, tuple[float, str]]:
    """{id del modelo de WikiIA: (índice, nombre en Artificial Analysis)} usando la tabla benchmarks."""
    medidos: dict[frozenset, list] = defaultdict(list)
    for b in con.execute("SELECT nombre, slug, creador, indice FROM benchmarks"):
        for f in {firma(b["nombre"]), firma_slug(b["slug"])}:
            if len(f) >= 2 or any(ch.isdigit() for p in f for ch in p):  # nada de firmas de una palabra suelta
                medidos[f].append(b)
    resultado: dict[str, tuple[float, str]] = {}
    modelos = con.execute(
        "SELECT m.id, m.nombre, m.or_id, m.hf_id, m.empresa_id, e.nombre AS empresa FROM modelos m "
        "LEFT JOIN empresas e ON e.id = m.empresa_id WHERE m.oculto = 0").fetchall()
    por_firma: dict[frozenset, list] = defaultdict(list)
    for m in modelos:
        firmas = {firma(m["nombre"])}
        if m["or_id"]:
            firmas.add(firma(m["or_id"].split("/", 1)[-1].replace("-", " ")))
        for f in firmas:
            por_firma[f].append(m)
    for f, candidatos_aa in medidos.items():
        modelos_wiki = por_firma.get(f)
        if not modelos_wiki:
            continue
        empresas = {m["empresa_id"] for m in modelos_wiki}
        for m in modelos_wiki:
            aa = candidatos_aa
            if len(empresas) > 1:  # la misma firma en dos empresas: decide el creador
                propias = _palabras_creador(m["empresa"]) | _palabras_creador(m["empresa_id"])
                aa = [b for b in candidatos_aa if _palabras_creador(b["creador"]) & propias]
            if not aa:
                continue
            mejor = max(aa, key=lambda b: b["indice"])  # variantes (high/low…): la mejor puntuación
            if m["id"] not in resultado or mejor["indice"] > resultado[m["id"]][0]:
                resultado[m["id"]] = (float(mejor["indice"]), mejor["nombre"])
    return resultado


def estrellas_por_indice(indice: float, maximo: float) -> int:
    proporcion = indice / maximo if maximo else 0
    return next((e for umbral, e in UMBRALES if proporcion >= umbral), 1)


def aplicar(con) -> dict:
    """Pone el índice y la potencia medida en los modelos emparejados y limita las estimaciones a 4★."""
    medidos = emparejar(con)
    maximo = max((i for i, _ in medidos.values()), default=0)
    con.execute("UPDATE modelos SET indice_ia = NULL, indice_ia_nombre = NULL")
    actualizados = 0
    for modelo_id, (indice, nombre_aa) in medidos.items():
        fila = con.execute("SELECT manual, potencia, potencia_fuente FROM modelos WHERE id=?", (modelo_id,)).fetchone()
        con.execute("UPDATE modelos SET indice_ia=?, indice_ia_nombre=? WHERE id=?", (indice, nombre_aa, modelo_id))
        if "potencia" in db.cargar_json(fila["manual"], []):
            continue
        estrellas = estrellas_por_indice(indice, maximo)
        razon = (f"Intelligence Index de Artificial Analysis: {indice:.0f} (el mejor modelo medido hoy tiene "
                 f"{maximo:.0f}). Medido como «{nombre_aa}».")
        con.execute("UPDATE modelos SET potencia=?, potencia_fuente=?, potencia_razon=? WHERE id=?",
                    (estrellas, FUENTE, razon, modelo_id))
        actualizados += 1
    # Los que tenían potencia medida y ya no aparecen en el índice vuelven a ser estimados.
    con.execute("UPDATE modelos SET potencia_fuente='heurística' WHERE potencia_fuente=? AND indice_ia IS NULL",
                (FUENTE,))
    limitar_estimadas(con)
    con.commit()
    return {"medidos": len(medidos), "actualizados": actualizados, "maximo": maximo}


def limitar_estimadas(con) -> None:
    """Sin benchmark nadie es 5★ (salvo lo que el usuario corrigió a mano)."""
    for f in con.execute("SELECT id, manual FROM modelos WHERE potencia > ? AND indice_ia IS NULL "
                         "AND COALESCE(potencia_fuente, '') <> ?", (TOPE_ESTIMADA, FUENTE)).fetchall():
        if "potencia" not in db.cargar_json(f["manual"], []):
            con.execute("UPDATE modelos SET potencia=? WHERE id=?", (TOPE_ESTIMADA, f["id"]))


def actualizar(con, clave: str, sesion, progreso=print) -> dict:
    """Descarga (si hay clave) y aplica. Si la descarga falla, usa lo último guardado."""
    aviso = ""
    if clave:
        try:
            n = guardar(con, descargar(clave, sesion))
            progreso(f"Artificial Analysis: {n} modelos con índice.")
        except ErrorBenchmark as e:
            aviso = str(e)
        except Exception as e:  # noqa: BLE001 - red caída o respuesta rara: se sigue con lo guardado
            aviso = f"Artificial Analysis: {e.__class__.__name__}"
    else:
        aviso = "Sin ARTIFICIAL_ANALYSIS_API_KEY: la potencia sigue siendo estimada"
    resultado = aplicar(con)
    resultado["aviso"] = aviso
    return resultado
