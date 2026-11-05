"""Base de datos SQLite de WikiIA."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone

from .config import DB_RUTA

CAPACIDADES = (
    "cap_codigo", "cap_investigacion", "cap_chat", "cap_vision",
    "cap_imagen", "cap_video", "cap_audio", "cap_agentes",
)

ESQUEMA = """
CREATE TABLE IF NOT EXISTS paises (
    codigo TEXT PRIMARY KEY,
    nombre TEXT NOT NULL,
    bandera TEXT,
    nota TEXT
);
CREATE TABLE IF NOT EXISTS empresas (
    id TEXT PRIMARY KEY,
    nombre TEXT NOT NULL,
    pais TEXT DEFAULT 'XX',
    web TEXT,
    or_prefijos TEXT DEFAULT '[]',
    hf_orgs TEXT DEFAULT '[]',
    editado INTEGER DEFAULT 0          -- 1 = el usuario cambió el país a mano
);
CREATE TABLE IF NOT EXISTS modelos (
    id TEXT PRIMARY KEY,
    nombre TEXT NOT NULL,
    empresa_id TEXT,
    fecha TEXT,                         -- AAAA-MM-DD de publicación
    or_id TEXT,
    hf_id TEXT,
    acceso TEXT,                        -- api | local | ambos
    licencia TEXT,
    parametros_b REAL,                  -- miles de millones de parámetros
    activos_b REAL,                     -- parámetros activos (modelos MoE)
    contexto INTEGER,
    precio_entrada REAL,                -- USD por 1M tokens
    precio_salida REAL,
    precio_texto TEXT,
    gratis INTEGER DEFAULT 0,
    cap_codigo INTEGER DEFAULT 0,
    cap_investigacion INTEGER DEFAULT 0,
    cap_chat INTEGER DEFAULT 0,
    cap_vision INTEGER DEFAULT 0,
    cap_imagen INTEGER DEFAULT 0,
    cap_video INTEGER DEFAULT 0,
    cap_audio INTEGER DEFAULT 0,
    cap_agentes INTEGER DEFAULT 0,
    potencia INTEGER,
    potencia_fuente TEXT,
    potencia_razon TEXT,
    resumen TEXT,
    mejor_para TEXT,
    notas_ia TEXT DEFAULT '[]',
    descripcion TEXT,
    modalidad TEXT,
    tarea TEXT,
    enlaces TEXT DEFAULT '{}',
    origen TEXT,
    investigado INTEGER DEFAULT 0,
    investigado_por TEXT,
    investigado_en TEXT,
    verificado INTEGER DEFAULT 0,
    oculto INTEGER DEFAULT 0,
    manual TEXT DEFAULT '[]',           -- campos editados a mano: nunca se sobrescriben
    extra TEXT DEFAULT '{}',
    retirado TEXT,                      -- fecha en que dejó de aparecer en OpenRouter
    detectado_en TEXT,
    actualizado_en TEXT
);
CREATE INDEX IF NOT EXISTS ix_modelos_or ON modelos(or_id);
CREATE INDEX IF NOT EXISTS ix_modelos_hf ON modelos(lower(hf_id));
CREATE INDEX IF NOT EXISTS ix_modelos_empresa ON modelos(empresa_id);
CREATE TABLE IF NOT EXISTS alertas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    modelo_id TEXT,
    fecha TEXT,
    titulo TEXT,
    mensaje TEXT,
    canales TEXT,
    leida INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ejecuciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inicio TEXT,
    fin TEXT,
    nuevos INTEGER DEFAULT 0,
    actualizados INTEGER DEFAULT 0,
    investigados INTEGER DEFAULT 0,
    errores TEXT,
    resumen TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    clave TEXT PRIMARY KEY,
    valor TEXT
);
"""

# Campos JSON que se combinan (no se reemplazan) al fusionar fuentes.
_CAMPOS_DICT = ("enlaces", "extra")


def ahora() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


_RE_SUFIJO_CHAT = re.compile(r"[-_ ](instruct|it|chat)$", re.I)


def nombre_normal(nombre) -> str:
    """'Kimi-K3.1-Pro', 'Kimi K3.1 Pro' y 'gemma-4-31B-it' / 'Gemma 4 31B' quedan iguales."""
    if not isinstance(nombre, str):
        return ""
    return re.sub(r"[^a-z0-9]", "", _RE_SUFIJO_CHAT.sub("", nombre.strip()).lower())


# Columnas agregadas después de la primera versión: se crean solas en bases existentes.
_COLUMNAS_NUEVAS = {
    "modelos": {
        "peso_gb": "REAL",                  # tamaño real de los archivos originales
        "gated": "TEXT",                    # Hugging Face pide aceptar licencia antes de descargar
        "descargas": "TEXT DEFAULT '{}'",   # versiones GGUF / MLX encontradas
    },
    "empresas": {"chat_url": "TEXT", "api_url": "TEXT"},
}


def _migrar(con) -> None:
    for tabla, columnas in _COLUMNAS_NUEVAS.items():
        existentes = {fila[1] for fila in con.execute(f"PRAGMA table_info({tabla})")}
        for columna, tipo in columnas.items():
            if columna not in existentes:
                con.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {tipo}")
    con.commit()


def conectar(ruta=DB_RUTA) -> sqlite3.Connection:
    con = sqlite3.connect(ruta, timeout=30)
    con.create_function("nombre_normal", 1, nombre_normal, deterministic=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(ESQUEMA)
    _migrar(con)
    return con


def meta_get(con, clave: str, defecto=None):
    fila = con.execute("SELECT valor FROM meta WHERE clave=?", (clave,)).fetchone()
    return fila["valor"] if fila else defecto


def meta_set(con, clave: str, valor) -> None:
    con.execute(
        "INSERT INTO meta(clave, valor) VALUES(?, ?) "
        "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
        (clave, None if valor is None else str(valor)),
    )


def _a_texto(valor):
    if isinstance(valor, (dict, list)):
        return json.dumps(valor, ensure_ascii=False)
    if isinstance(valor, bool):
        return int(valor)
    return valor


def _cargar_json(texto, defecto):
    try:
        return json.loads(texto) if texto else defecto
    except (TypeError, ValueError):
        return defecto


def _combinar_acceso(viejo: str | None, nuevo: str | None) -> str | None:
    conjunto = set()
    for valor in (viejo, nuevo):
        if valor == "ambos":
            conjunto |= {"api", "local"}
        elif valor:
            conjunto.add(valor)
    if conjunto >= {"api", "local"}:
        return "ambos"
    return next(iter(conjunto), None)


def buscar_existentes(con, reg: dict) -> list[sqlite3.Row]:
    fila = con.execute("SELECT * FROM modelos WHERE id=?", (reg["id"],)).fetchone()
    if fila:
        return [fila]
    if reg.get("or_id"):
        fila = con.execute("SELECT * FROM modelos WHERE or_id=?", (reg["or_id"],)).fetchone()
        if fila:
            return [fila]
    if reg.get("hf_id"):
        if reg.get("or_id"):
            # Un modelo de OpenRouter solo se une a un registro de HF que aún no tenga dueño en OpenRouter.
            fila = con.execute(
                "SELECT * FROM modelos WHERE lower(hf_id)=lower(?) AND or_id IS NULL",
                (reg["hf_id"],),
            ).fetchone()
            if fila:
                return [fila]
        else:
            filas = con.execute(
                "SELECT * FROM modelos WHERE lower(hf_id)=lower(?)", (reg["hf_id"],)
            ).fetchall()
            if filas:
                return filas
    # Último intento: misma empresa y mismo nombre, uno viene de OpenRouter y el otro de Hugging Face.
    if reg.get("empresa_id") and nombre_normal(reg.get("nombre")):
        if reg.get("or_id"):
            condicion = "or_id IS NULL AND hf_id IS NOT NULL"
        elif reg.get("hf_id"):
            condicion = "or_id IS NOT NULL AND hf_id IS NULL"
        else:
            return []
        fila = con.execute(
            f"SELECT * FROM modelos WHERE empresa_id=? AND {condicion} AND nombre_normal(nombre)=?",
            (reg["empresa_id"], nombre_normal(reg["nombre"])),
        ).fetchone()
        if fila:
            return [fila]
    return []


def upsert_modelo(con, reg: dict, fuente: str) -> tuple[str, bool, bool]:
    """Inserta o fusiona un modelo. Devuelve (id, es_nuevo, cambio)."""
    marca = ahora()
    existentes = buscar_existentes(con, reg)
    if not existentes:
        datos = {k: _a_texto(v) for k, v in reg.items() if v is not None}
        datos.setdefault("detectado_en", marca)
        datos["actualizado_en"] = marca
        datos["origen"] = fuente
        columnas = ", ".join(datos)
        marcas = ", ".join("?" for _ in datos)
        con.execute(f"INSERT INTO modelos({columnas}) VALUES({marcas})", list(datos.values()))
        return reg["id"], True, True

    hubo_cambio = False
    for ex in existentes:
        manual = set(_cargar_json(ex["manual"], []))
        cambios: dict = {}
        for clave, valor in reg.items():
            if clave in ("id", "detectado_en") or clave in manual or valor is None:
                continue
            actual = ex[clave]
            if clave in CAPACIDADES:
                valor = max(int(actual or 0), int(valor))
            elif clave == "acceso":
                valor = _combinar_acceso(actual, valor)
            elif clave in ("potencia", "potencia_fuente", "potencia_razon"):
                # La heurística nunca pisa lo que ya decidió la IA o el usuario.
                if ex["potencia_fuente"] not in (None, "", "heurística"):
                    continue
            elif clave in _CAMPOS_DICT:
                combinado = _cargar_json(actual, {})
                combinado.update(valor)
                valor = combinado
            elif clave == "fecha":
                valor = min(v for v in (actual, valor) if v)
            elif clave in ("parametros_b", "activos_b") and fuente == "openrouter" and actual is not None:
                continue  # el tamaño de Hugging Face (exacto) gana al que se adivina por el nombre
            elif (clave in ("nombre", "descripcion") and fuente != "openrouter" and actual
                  and "openrouter" in (ex["origen"] or "")):
                continue  # los nombres y descripciones de OpenRouter son más legibles
            valor = _a_texto(valor)
            if actual != valor:
                cambios[clave] = valor
        origenes = set(filter(None, (ex["origen"] or "").split("+"))) | {fuente}
        origen = "+".join(sorted(origenes))
        if origen != ex["origen"]:
            cambios["origen"] = origen
        if cambios:
            cambios["actualizado_en"] = marca
            asignaciones = ", ".join(f"{k}=?" for k in cambios)
            con.execute(
                f"UPDATE modelos SET {asignaciones} WHERE id=?",
                [*cambios.values(), ex["id"]],
            )
            hubo_cambio = True
    return existentes[0]["id"], False, hubo_cambio


def actualizar_campos(con, modelo_id: str, campos: dict, marcar_manual: bool = False) -> None:
    """Actualiza campos de un modelo. Con marcar_manual, quedan protegidos de futuras sincronizaciones."""
    if not campos:
        return
    fila = con.execute("SELECT manual FROM modelos WHERE id=?", (modelo_id,)).fetchone()
    if not fila:
        return
    datos = {k: _a_texto(v) for k, v in campos.items()}
    if marcar_manual:
        manual = set(_cargar_json(fila["manual"], [])) | set(campos)
        datos["manual"] = json.dumps(sorted(manual))
    datos["actualizado_en"] = ahora()
    asignaciones = ", ".join(f"{k}=?" for k in datos)
    con.execute(f"UPDATE modelos SET {asignaciones} WHERE id=?", [*datos.values(), modelo_id])


def cargar_json(texto, defecto):
    return _cargar_json(texto, defecto)
