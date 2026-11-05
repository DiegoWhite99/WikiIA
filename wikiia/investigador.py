"""Investigación con IA: convierte los datos crudos de un modelo en una ficha en español."""
from __future__ import annotations

import json
from datetime import date

from . import db, motores
from .fuentes import huggingface, nueva_sesion
from .heuristicas import num, potencia_heuristica
from .llm import ClienteLLM, ErrorLLM

SISTEMA = """Eres el editor de WikiIA, una enciclopedia en español sobre modelos de inteligencia artificial.
Analizas los datos de UN modelo y respondes SOLO con un objeto JSON válido.
Reglas:
- Español claro, para personas no técnicas.
- Básate SOLO en los datos y la documentación entregados. Si algo no aparece, no lo inventes: usa false o una lista vacía.
- Nunca inventes precios, fechas ni puntajes de benchmarks.
- IMPORTANTE: el modelo puede ser más nuevo que tu conocimiento. No lo compares con los modelos que tú conoces
  ni menciones años; júzgalo por su tamaño, precio, contexto, empresa y lo que dice su documentación."""

PLANTILLA = """Hoy es {hoy}.

Datos del modelo:
{datos}

Extracto de su documentación oficial (puede estar vacío):
\"\"\"
{tarjeta}
\"\"\"

Responde con este JSON exacto:
{{
  "resumen": "1 o 2 frases: qué es y para qué sirve",
  "mejor_para": "frase corta con sus 2 o 3 usos más fuertes",
  "capacidades": {{"codigo": true/false, "investigacion": true/false, "chat": true/false, "vision": true/false, "agentes": true/false, "audio": true/false}},
  "potencia": 1 a 5,
  "potencia_razon": "una frase explicando el nivel",
  "notas": ["hasta 3 advertencias o datos importantes para quien lo quiera usar"]
}}
Escala de potencia (sé exigente, el 5 es escaso):
  5 = frontera: el modelo tope de una empresa líder (el más caro o grande de su familia), compite con los mejores del mundo.
  4 = alto: muy capaz pero por debajo del tope de su empresa (por ejemplo la versión intermedia, o un tope de una empresa secundaria).
  3 = medio: versiones "mini", "flash", "lite" o modelos medianos.  2 = ligero: pequeños o muy baratos.  1 = muy pequeño o experimental.
Compáralo con los otros modelos de su misma empresa (precio y tamaño): si hay uno claramente más caro o grande y reciente, este NO es 5.
Estimación automática por tamaño y precio: {potencia_ref} (solo orientativa).
"codigo" = sirve bien para programar. "investigacion" = razonamiento profundo, matemáticas o investigación. "agentes" = usa herramientas o actúa de forma autónoma.
En "notas" NO repitas la licencia, el precio ni el país (ya se muestran aparte): da consejos prácticos."""

# Capacidades que la IA puede añadir. Crear imágenes/videos solo se acepta de los datos de la fuente.
_CAP_IA = {
    "codigo": "cap_codigo", "investigacion": "cap_investigacion", "chat": "cap_chat",
    "vision": "cap_vision", "agentes": "cap_agentes", "audio": "cap_audio",
}


def _bool(valor) -> bool:
    if isinstance(valor, str):
        return valor.strip().lower() in ("true", "si", "sí", "yes", "1")
    return bool(valor)


def _datos_para_ia(con, fila) -> dict:
    empresa = con.execute(
        "SELECT e.nombre, p.nombre AS pais FROM empresas e LEFT JOIN paises p ON p.codigo=e.pais WHERE e.id=?",
        (fila["empresa_id"],),
    ).fetchone()
    datos = {
        "nombre": fila["nombre"],
        "empresa": empresa["nombre"] if empresa else fila["empresa_id"],
        "pais": empresa["pais"] if empresa else None,
        "publicado": fila["fecha"],
        "acceso": {"api": "solo API en la nube", "local": "pesos abiertos (se descarga)",
                   "ambos": "pesos abiertos y también por API"}.get(fila["acceso"], fila["acceso"]),
        "licencia": fila["licencia"],
        "parametros_miles_de_millones": num(fila["parametros_b"]),
        "parametros_activos_moe": num(fila["activos_b"]),
        "contexto_tokens": fila["contexto"],
        "precio_usd_por_millon_entrada": num(fila["precio_entrada"]),
        "precio_usd_por_millon_salida": num(fila["precio_salida"]),
        "modalidades": fila["modalidad"],
        "tarea_huggingface": fila["tarea"],
        "descripcion_de_la_fuente": (fila["descripcion"] or "")[:1500],
        "otros_modelos_recientes_de_la_misma_empresa": _hermanos(con, fila),
    }
    return {k: v for k, v in datos.items() if v not in (None, "", [])}


def _hermanos(con, fila) -> list[str]:
    """Otros modelos de la empresa con precio y tamaño: le dan a la IA una escala relativa."""
    filas = con.execute(
        "SELECT nombre, precio_salida, parametros_b FROM modelos WHERE empresa_id=? AND id!=? AND oculto=0 "
        "AND (precio_salida > 0 OR parametros_b > 0) ORDER BY fecha DESC LIMIT 12",
        (fila["empresa_id"], fila["id"]),
    ).fetchall()
    salida = []
    for h in filas:
        partes = []
        if h["precio_salida"]:
            partes.append(f"${h['precio_salida']:g}/1M salida")
        if h["parametros_b"]:
            partes.append(f"{h['parametros_b']:g}B parámetros")
        salida.append(f"{h['nombre']} ({', '.join(partes)})")
    return salida


def _completar_desde_hf(con, sesion, fila) -> None:
    """Para modelos con pesos en HF: licencia, tamaño real, acceso restringido y versiones descargables."""
    if not fila["hf_id"]:
        return
    if not (fila["licencia"] and fila["parametros_b"] and fila["peso_gb"] and fila["gated"]):
        try:
            m = huggingface.obtener_modelo(sesion, fila["hf_id"])
        except Exception:  # noqa: BLE001 - dato opcional
            m = None
        if m:
            manual = set(db.cargar_json(fila["manual"], []))
            nuevos = {
                "licencia": huggingface.licencia(m),
                "parametros_b": huggingface.parametros_b(m),
                "peso_gb": huggingface.peso_gb(m.get("safetensors")),
                "gated": huggingface.texto_gated(m.get("gated")),
            }
            campos = {k: v for k, v in nuevos.items() if v and not fila[k] and k not in manual}
            if campos:
                db.actualizar_campos(con, fila["id"], campos)
    motores.obtener_descargas(con, fila, sesion)


def investigar_modelo(con, cliente: ClienteLLM, modelo_id: str, sesion=None) -> str:
    """Investiga un modelo y guarda la ficha. Devuelve el proveedor usado. Lanza ErrorLLM si falla."""
    sesion = sesion or nueva_sesion()
    fila = con.execute("SELECT * FROM modelos WHERE id=?", (modelo_id,)).fetchone()
    if not fila:
        raise ErrorLLM(f"No existe el modelo {modelo_id}")
    _completar_desde_hf(con, sesion, fila)
    fila = con.execute("SELECT * FROM modelos WHERE id=?", (modelo_id,)).fetchone()

    tarjeta = huggingface.obtener_tarjeta(sesion, fila["hf_id"]) if fila["hf_id"] else ""
    usuario = PLANTILLA.format(
        hoy=date.today().isoformat(),
        datos=json.dumps(_datos_para_ia(con, fila), ensure_ascii=False, indent=1),
        tarjeta=tarjeta or "(sin documentación)",
        potencia_ref=potencia_heuristica(fila),
    )
    respuesta, proveedor = cliente.json(SISTEMA, usuario)

    manual = set(db.cargar_json(fila["manual"], []))
    campos: dict = {}
    resumen = str(respuesta.get("resumen") or "").strip()
    if resumen and "resumen" not in manual:
        campos["resumen"] = resumen[:500]
    mejor = respuesta.get("mejor_para")
    if isinstance(mejor, list):
        mejor = ", ".join(str(x) for x in mejor)
    if mejor and "mejor_para" not in manual:
        campos["mejor_para"] = str(mejor).strip()[:300]

    capacidades = respuesta.get("capacidades") or {}
    if isinstance(capacidades, dict):
        for clave, columna in _CAP_IA.items():
            if columna not in manual and _bool(capacidades.get(clave)) and not fila[columna]:
                campos[columna] = 1  # la IA solo suma capacidades; quitar una es decisión tuya

    try:
        potencia = int(round(float(respuesta.get("potencia"))))
    except (TypeError, ValueError):
        potencia = None
    if potencia and 1 <= potencia <= 5 and "potencia" not in manual:
        campos["potencia"] = potencia
        campos["potencia_fuente"] = f"IA ({proveedor})"
        campos["potencia_razon"] = str(respuesta.get("potencia_razon") or "").strip()[:300]

    notas = respuesta.get("notas") or []
    if isinstance(notas, str):
        notas = [notas]
    if "notas_ia" not in manual:
        campos["notas_ia"] = [str(n).strip()[:250] for n in notas if str(n).strip()][:3]

    campos.update({"investigado": 1, "investigado_por": proveedor, "investigado_en": db.ahora()})
    db.actualizar_campos(con, modelo_id, campos)
    con.commit()
    return proveedor


def pendientes(con, limite: int, primero: list[str] | None = None) -> list[str]:
    """Cola de modelos sin investigar: primero los indicados, luego los más potentes y recientes."""
    ids = [i for i in (primero or [])]
    if len(ids) >= limite:
        return ids[:limite]
    filas = con.execute(
        "SELECT id FROM modelos WHERE investigado=0 AND oculto=0 "
        "ORDER BY potencia DESC, fecha DESC LIMIT ?",
        (limite * 2,),
    ).fetchall()
    for f in filas:
        if f["id"] not in ids:
            ids.append(f["id"])
        if len(ids) >= limite:
            break
    return ids
