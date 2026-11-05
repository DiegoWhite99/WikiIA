"""Orquestador: busca modelos nuevos, los investiga con IA y manda alertas."""
from __future__ import annotations

import fcntl
import json
from contextlib import contextmanager
from typing import Callable

from . import alertas, benchmarks, db, enlaces, investigador
from .catalogo import Resolutor, sincronizar_catalogo
from .config import BLOQUEO, DATOS, Config, cargar_config
from .fuentes import huggingface, nueva_sesion, openrouter
from .heuristicas import potencia_heuristica
from .llm import ClienteLLM, ErrorLLM

MAX_ALERTAS_INDIVIDUALES = 6


@contextmanager
def _bloqueo():
    """Evita que el temporizador y el botón de la app actualicen al mismo tiempo."""
    with open(BLOQUEO, "w") as archivo:
        try:
            fcntl.flock(archivo, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(archivo, fcntl.LOCK_UN)


def recalcular_potencia(con) -> None:
    """Potencia estimada para los modelos que la IA o el usuario aún no han evaluado."""
    filas = con.execute(
        "SELECT * FROM modelos WHERE potencia_fuente IS NULL OR potencia_fuente='heurística'"
    ).fetchall()
    for f in filas:
        if "potencia" in db.cargar_json(f["manual"], []):
            continue
        p = min(potencia_heuristica(f), benchmarks.TOPE_ESTIMADA)  # sin benchmark nadie es 5★
        if p != f["potencia"] or f["potencia_fuente"] is None:
            con.execute(
                "UPDATE modelos SET potencia=?, potencia_fuente='heurística', "
                "potencia_razon='Estimada por tamaño y precio; falta investigarlo con IA' WHERE id=?",
                (p, f["id"]),
            )


def _cargar_manuales(con) -> list[str]:
    with open(DATOS / "manuales.json", encoding="utf-8") as f:
        registros = json.load(f)
    nuevos = []
    for r in registros:
        r = dict(r)
        r.setdefault("potencia_fuente", "referencia")
        r.setdefault("potencia_razon", "Valor de referencia del catálogo manual")
        r.update({"investigado": 1, "investigado_por": "catálogo manual"})
        modelo_id, es_nuevo, _ = db.upsert_modelo(con, r, "manual")
        if es_nuevo:
            nuevos.append(modelo_id)
    return nuevos


def _marcar_retirados(con, ids_actuales: set[str]) -> None:
    marca = db.ahora()[:10]
    for f in con.execute("SELECT id, or_id, retirado FROM modelos WHERE or_id IS NOT NULL").fetchall():
        if f["or_id"] in ids_actuales and f["retirado"]:
            con.execute("UPDATE modelos SET retirado=NULL WHERE id=?", (f["id"],))
        elif f["or_id"] not in ids_actuales and not f["retirado"]:
            con.execute("UPDATE modelos SET retirado=? WHERE id=?", (marca, f["id"]))


def ejecutar(
    cfg: Config | None = None,
    investigar: int | None = None,
    alertar: bool = True,
    progreso: Callable[[str], None] = print,
) -> dict:
    cfg = cfg or cargar_config()
    with _bloqueo() as libre:
        if not libre:
            progreso("Ya hay otra actualización en curso; inténtalo en unos minutos.")
            return {"ocupado": True}
        return _ejecutar(cfg, investigar, alertar, progreso)


def _ejecutar(cfg: Config, investigar: int | None, alertar: bool, progreso) -> dict:
    con = db.conectar()
    inicio = db.ahora()
    errores: list[str] = []
    nuevos: list[str] = []
    silenciosos: list[str] = []  # nuevos que forman parte de una línea base: no se alertan
    actualizados = 0

    sincronizar_catalogo(con)
    con.commit()
    primera_vez = db.meta_get(con, "linea_base") is None
    resolutor = Resolutor(con)
    sesion = nueva_sesion()

    # 1) OpenRouter: modelos por API con precios.
    openrouter_ok = False
    try:
        progreso("Consultando OpenRouter…")
        crudos = openrouter.obtener(sesion)
        registros = openrouter.normalizar(crudos, resolutor)
        for r in registros:
            modelo_id, es_nuevo, cambio = db.upsert_modelo(con, r, "openrouter")
            (nuevos if es_nuevo else []).append(modelo_id)
            actualizados += int(cambio and not es_nuevo)
        if len(registros) > 100:  # evita marcar todo como retirado si la API respondió a medias
            _marcar_retirados(con, {r["or_id"] for r in registros})
            openrouter_ok = True
        con.commit()
        progreso(f"OpenRouter: {len(registros)} modelos revisados.")
    except Exception as e:  # noqa: BLE001 - una fuente caída no detiene a las demás
        errores.append(f"OpenRouter: {e}")

    # 2) Hugging Face: modelos con pesos abiertos de las organizaciones oficiales.
    orgs = resolutor.orgs_hf()
    orgs_ok = 0
    for i, (org, empresa_id) in enumerate(orgs, 1):
        progreso(f"Hugging Face {i}/{len(orgs)}: {org}")
        org_nueva = db.meta_get(con, f"hf_org:{org.lower()}") is None
        try:
            # Siempre los 50 más recientes: así un modelo filtrado por pocos likes entra cuando se vuelve popular.
            crudos = huggingface.obtener_org(sesion, org, 50)
        except Exception as e:  # noqa: BLE001
            errores.append(f"Hugging Face {org}: {e}")
            continue
        registros = huggingface.normalizar(crudos, empresa_id)
        elegidos = {r["hf_id"] for r in registros}
        for m in crudos:
            if m["id"] not in elegidos:
                # Ya no pasa el filtro (copia, variante, ruido): se limpia si nadie lo editó a mano.
                con.execute(
                    "DELETE FROM modelos WHERE lower(hf_id)=lower(?) AND origen='huggingface' "
                    "AND manual='[]' AND verificado=0",
                    (m["id"],),
                )
        for r in registros:
            modelo_id, es_nuevo, cambio = db.upsert_modelo(con, r, "huggingface")
            if es_nuevo:
                (silenciosos if org_nueva else nuevos).append(modelo_id)
            actualizados += int(cambio and not es_nuevo)
        db.meta_set(con, f"hf_org:{org.lower()}", inicio)
        orgs_ok += 1
        con.commit()

    # 3) Catálogo manual (servicios de imagen y video sin API pública en las fuentes).
    nuevos += _cargar_manuales(con)
    con.commit()

    # 3b) Potencia medida: Intelligence Index de Artificial Analysis (si hay clave; si no, lo último guardado).
    try:
        medidos = benchmarks.actualizar(con, cfg.aa_api_key, sesion, progreso)
        progreso(f"Benchmarks: {medidos['medidos']} modelos con potencia medida.")
        if medidos["aviso"] and cfg.aa_api_key:
            errores.append(medidos["aviso"])
    except Exception as e:  # noqa: BLE001 - el benchmark nunca detiene la actualización
        errores.append(f"Benchmarks: {e}")
    recalcular_potencia(con)
    con.commit()

    if primera_vez:
        silenciosos += nuevos
        nuevos = []
        if openrouter_ok and orgs_ok:
            db.meta_set(con, "linea_base", inicio)
            con.commit()
    para_alertar = [m for m in nuevos if not con.execute(
        "SELECT oculto FROM modelos WHERE id=?", (m,)).fetchone()["oculto"]]

    # 4) Investigación con IA: primero los nuevos, luego los pendientes más importantes.
    limite = cfg.investigar_por_ejecucion if investigar is None else investigar
    cola = investigador.pendientes(con, max(limite, min(len(para_alertar), 15)), primero=para_alertar[:15])
    investigados, fallos_seguidos = 0, 0
    if cola:
        cliente = ClienteLLM(cfg)
        for j, modelo_id in enumerate(cola, 1):
            progreso(f"Investigando con IA {j}/{len(cola)}: {modelo_id}")
            try:
                proveedor = investigador.investigar_modelo(con, cliente, modelo_id, sesion)
                investigados += 1
                fallos_seguidos = 0
                progreso(f"   ✓ listo con {proveedor}")
            except ErrorLLM as e:
                errores.append(f"IA {modelo_id}: {e}")
                fallos_seguidos += 1
                if fallos_seguidos >= 3:
                    errores.append("IA: 3 fallos seguidos, se detiene la investigación por ahora.")
                    break

    # 5) Enlaces: revisa los nuevos y los que llevan más de 14 días (los rotos se saltan en la app).
    try:
        conteo = enlaces.revisar(con, limite=400, progreso=progreso)
        if conteo["rotos"]:
            progreso(f"Enlaces: {conteo['rotos']} rotos de {conteo['revisados']} revisados.")
    except Exception as e:  # noqa: BLE001 - revisar enlaces nunca detiene la actualización
        errores.append(f"Enlaces: {e}")

    # 6) Alertas.
    if alertar:
        if primera_vez and db.meta_get(con, "linea_base"):
            total = con.execute("SELECT COUNT(*) FROM modelos").fetchone()[0]
            paises = con.execute(
                "SELECT COUNT(DISTINCT e.pais) FROM modelos m JOIN empresas e ON e.id=m.empresa_id"
            ).fetchone()[0]
            alertas.enviar(con, cfg, "🌐 WikiIA está lista",
                           f"Se cargaron {total} modelos de {paises} países. "
                           "Desde ahora te aviso cada vez que salga uno nuevo.")
        elif para_alertar:
            ordenados = sorted(
                para_alertar,
                key=lambda m: con.execute("SELECT potencia FROM modelos WHERE id=?", (m,)).fetchone()[0] or 0,
                reverse=True,
            )
            individuales = ordenados if len(ordenados) <= MAX_ALERTAS_INDIVIDUALES else ordenados[:5]
            for modelo_id in individuales:
                titulo, mensaje = alertas.mensaje_modelo(con, modelo_id)
                alertas.enviar(con, cfg, titulo, mensaje, modelo_id)
            resto = len(ordenados) - len(individuales)
            if resto:
                alertas.enviar(con, cfg, f"🆕 WikiIA: {resto} modelos nuevos más",
                               "Ábrelos en la pestaña 🔔 Novedades de WikiIA.")

    resumen = (f"{len(para_alertar)} nuevos, {len(silenciosos)} cargados como base, "
               f"{actualizados} actualizados, {investigados} investigados con IA")
    con.execute(
        "INSERT INTO ejecuciones(inicio, fin, nuevos, actualizados, investigados, errores, resumen) "
        "VALUES(?,?,?,?,?,?,?)",
        (inicio, db.ahora(), len(para_alertar), actualizados, investigados,
         json.dumps(errores, ensure_ascii=False), resumen),
    )
    con.commit()
    con.close()
    progreso(f"Listo: {resumen}." + (f" Avisos: {len(errores)}." if errores else ""))
    return {
        "nuevos": para_alertar, "base": len(silenciosos), "actualizados": actualizados,
        "investigados": investigados, "errores": errores, "primera_vez": primera_vez,
    }
