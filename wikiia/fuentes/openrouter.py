"""OpenRouter: catálogo público de modelos disponibles por API, con precios y modalidades.

No necesita clave: https://openrouter.ai/api/v1/models
"""
from __future__ import annotations

import re
from datetime import datetime

from ..heuristicas import capacidades_por_texto, parametros_desde_nombre, precio_por_millon

URL = "https://openrouter.ai/api/v1/models"
# Enrutadores de OpenRouter: no son modelos.
IGNORAR_PREFIJOS = {"openrouter"}


def obtener(sesion) -> list[dict]:
    respuesta = sesion.get(URL, timeout=40)
    respuesta.raise_for_status()
    return respuesta.json().get("data", [])


def _es_gratis(m: dict) -> bool:
    precios = m.get("pricing") or {}
    return m["id"].endswith(":free") or (
        precio_por_millon(precios.get("prompt")) == 0 and precio_por_millon(precios.get("completion")) == 0
    )


def normalizar(modelos: list[dict], resolutor) -> list[dict]:
    """Agrupa variantes (':free', ':thinking'...) y devuelve un registro por modelo."""
    grupos: dict[str, list[dict]] = {}
    for m in modelos:
        mid = m.get("id") or ""
        if not mid or mid.startswith("~") or "/" not in mid:
            continue  # los '~' son alias tipo "latest"
        if mid.split("/", 1)[0].lower() in IGNORAR_PREFIJOS:
            continue
        grupos.setdefault(mid.split(":", 1)[0], []).append(m)

    registros = []
    for base, variantes in grupos.items():
        principal = next((v for v in variantes if ":" not in v["id"]), variantes[0])
        gratis = any(_es_gratis(v) for v in variantes)
        registros.append(_registro(base, principal, gratis, resolutor))
    return registros


def _registro(base: str, m: dict, gratis: bool, resolutor) -> dict:
    prefijo, slug = base.split("/", 1)
    visible = m.get("name") or slug
    empresa_txt, separador, nombre = visible.partition(": ")
    if not separador:
        nombre, empresa_txt = visible, None
    nombre = re.sub(r"\s*\(free\)\s*$", "", nombre, flags=re.I).strip() or slug
    empresa_id = resolutor.empresa_or(prefijo, empresa_txt)

    arquitectura = m.get("architecture") or {}
    entrada = set(arquitectura.get("input_modalities") or [])
    salida = set(arquitectura.get("output_modalities") or [])
    soportados = set(m.get("supported_parameters") or [])
    descripcion = (m.get("description") or "").strip()
    precios = m.get("pricing") or {}
    total, activos = parametros_desde_nombre(slug)

    por_texto = capacidades_por_texto(f"{base} {nombre} {descripcion}")
    capacidades = {
        "cap_chat": int("text" in salida),
        "cap_vision": int(bool(entrada & {"image", "video"})),
        "cap_imagen": int("image" in salida),
        "cap_video": int("video" in salida),
        "cap_audio": int("audio" in entrada or "audio" in salida),
        "cap_agentes": int("tools" in soportados),
        "cap_codigo": por_texto["cap_codigo"],
        "cap_investigacion": int(
            bool(por_texto["cap_investigacion"]) or bool(soportados & {"reasoning", "include_reasoning"})
        ),
    }

    hf_id = m.get("hugging_face_id") or None
    creado = m.get("created")
    fecha = datetime.fromtimestamp(creado).date().isoformat() if creado else None
    enlaces = {"openrouter": f"https://openrouter.ai/{base}"}
    if hf_id:
        enlaces["huggingface"] = f"https://huggingface.co/{hf_id}"
    proveedor = m.get("top_provider") or {}
    extra = {
        "expira": m.get("expiration_date"),
        "max_salida": proveedor.get("max_completion_tokens"),
        "corte_conocimiento": m.get("knowledge_cutoff"),
    }

    return {
        "id": base,
        "or_id": base,
        "hf_id": hf_id,
        "nombre": nombre,
        "empresa_id": empresa_id,
        "fecha": fecha,
        "acceso": "ambos" if hf_id else "api",
        "parametros_b": total,
        "activos_b": activos,
        "contexto": m.get("context_length"),
        "precio_entrada": precio_por_millon(precios.get("prompt")),
        "precio_salida": precio_por_millon(precios.get("completion")),
        "gratis": int(gratis),
        **capacidades,
        "descripcion": descripcion[:3000],
        "modalidad": arquitectura.get("modality"),
        "enlaces": enlaces,
        "extra": {k: v for k, v in extra.items() if v},
    }
