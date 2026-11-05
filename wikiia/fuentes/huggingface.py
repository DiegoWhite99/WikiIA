"""Hugging Face: modelos con pesos abiertos publicados por las organizaciones oficiales del catálogo.

Las organizaciones publican muchas copias del mismo modelo (GGUF, FP8, LoRA...) y
material de investigación; aquí se filtra ese ruido para no llenar la wiki ni las alertas.
"""
from __future__ import annotations

import re

from ..heuristicas import TAREAS_HF, capacidades_por_texto, parametros_desde_nombre

URL = "https://huggingface.co/api/models"
CAMPOS = ("createdAt", "pipeline_tag", "tags", "likes", "downloads", "cardData", "safetensors", "gated")

# Cosas que no son modelos para usar directamente.
_RE_RUIDO = re.compile(
    r"(lora|adapter|tokenizer|encoder|eagle|draft|mtp|dspark|dflash|marking|pretrain|guard|reward|embedding|"
    r"rerank|safeguard|classifier|\bprm\b|\bsae\b|probe|checkpoint|ablation|[-_]qat[-_]|scope|"
    # paquetes de despliegue para un hardware concreto: mismo modelo, otro empaque
    r"amdnpu|tensorrt|onnx|openvino|coreml|[-_]qnn|[-_]npu)", re.I)
_RE_BASE = re.compile(r"[-_]base(?:[-_]|$)", re.I)
# Sufijos de formato/cuantización: son el mismo modelo empaquetado distinto.
_RE_VARIANTE = re.compile(
    r"[-_.](gguf|awq|gptq|(?:mx|nv)?fp\d+|int\d+|w\d+a\d+|q\d(?:_\w+)?|bf16|mlx|onnx|exl2|bnb|"
    r"\d+(?:\.\d+)?bit|prequantized|quantized|unquantized|diffusers|comfy(?:ui)?|hf|openvino|ct|litert|"
    r"amdnpu|npu|medusa|optimized)$", re.I)
# Relaciones que marcan una copia del modelo de otro (cuantizado, adaptador o mezcla).
_RELACIONES_COPIA = ("base_model:quantized:", "base_model:adapter:", "base_model:merge:")
LIKES_DERIVADO = 50      # derivado de un modelo de OTRA organización: solo si se vuelve popular
LIKES_SIN_TAREA = 30     # sin categoría en HF: solo si es grande y ya tiene algo de popularidad


def obtener_org(sesion, org: str, limite: int) -> list[dict]:
    parametros = [("author", org), ("sort", "createdAt"), ("direction", "-1"), ("limit", str(limite))]
    parametros += [("expand[]", c) for c in CAMPOS]
    respuesta = sesion.get(URL, params=parametros, timeout=30)
    respuesta.raise_for_status()
    return respuesta.json()


def obtener_modelo(sesion, hf_id: str) -> dict:
    parametros = [("expand[]", c) for c in CAMPOS]
    respuesta = sesion.get(f"{URL}/{hf_id}", params=parametros, timeout=30)
    respuesta.raise_for_status()
    return respuesta.json()


def obtener_tarjeta(sesion, hf_id: str, maximo: int = 2500) -> str:
    """Texto del README del modelo, sin metadatos, HTML ni imágenes."""
    try:
        respuesta = sesion.get(f"https://huggingface.co/{hf_id}/raw/main/README.md", timeout=30)
        if respuesta.status_code != 200:
            return ""
        texto = respuesta.text
    except Exception:  # noqa: BLE001 - la tarjeta es opcional
        return ""
    texto = re.sub(r"^---.*?---\s*", "", texto, count=1, flags=re.S)
    texto = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", texto)
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = re.sub(r"\n\s*\n+", "\n", texto)
    texto = re.sub(r"[ \t]+", " ", texto)
    return texto.strip()[:maximo]


_BYTES_POR_TIPO = {"F64": 8, "I64": 8, "F32": 4, "I32": 4, "U32": 4, "BF16": 2, "F16": 2, "I16": 2,
                   "F8_E4M3": 1, "F8_E5M2": 1, "I8": 1, "U8": 1, "F4": 0.5, "I4": 0.5, "U4": 0.5}


def peso_gb(safetensors: dict | None) -> float | None:
    """Peso real de los archivos originales según el tipo de dato de cada parámetro."""
    parametros = (safetensors or {}).get("parameters") or {}
    if not parametros:
        return None
    total = sum(n * _BYTES_POR_TIPO.get(tipo.upper(), 2) for tipo, n in parametros.items())
    return round(total / 1e9, 2)


def texto_gated(valor) -> str | None:
    """'manual'/'auto' = hay que aceptar la licencia en Hugging Face antes de descargar."""
    return str(valor) if valor and valor is not False else "no"


def nombre_base(nombre: str) -> str:
    anterior = None
    while anterior != nombre:
        anterior, nombre = nombre, _RE_VARIANTE.sub("", nombre)
    return nombre


def parametros_b(m: dict) -> float | None:
    total = (m.get("safetensors") or {}).get("total")
    if total:
        return round(total / 1e9, 2)
    return parametros_desde_nombre(m["id"].split("/", 1)[-1])[0]


def licencia(m: dict) -> str | None:
    valor = (m.get("cardData") or {}).get("license")
    if isinstance(valor, list):
        valor = valor[0] if valor else None
    if not valor:
        valor = next((t.split(":", 1)[1] for t in m.get("tags") or [] if t.startswith("license:")), None)
    return valor


def es_relevante(m: dict) -> bool:
    org, _, nombre = m["id"].partition("/")
    if _RE_RUIDO.search(nombre) or _RE_BASE.search(nombre):
        return False
    etiquetas = m.get("tags") or []
    if any(t.startswith(_RELACIONES_COPIA) for t in etiquetas):
        return False
    likes = m.get("likes") or 0
    derivado_ajeno = any(
        t.startswith("base_model:finetune:") and not t.split(":", 2)[2].lower().startswith(org.lower() + "/")
        for t in etiquetas
    )
    if derivado_ajeno and likes < LIKES_DERIVADO:
        return False
    tarea = m.get("pipeline_tag")
    params = parametros_b(m)
    if tarea is None:
        return bool(params and params >= 1 and likes >= LIKES_SIN_TAREA)
    if tarea not in TAREAS_HF:
        return False
    if tarea in ("text-generation", "text2text-generation") and params is not None and params < 0.3:
        return False
    return True


def normalizar(modelos: list[dict], empresa_id: str) -> list[dict]:
    """Filtra ruido, junta variantes de formato y devuelve un registro por modelo."""
    grupos: dict[str, list[dict]] = {}
    for m in modelos:
        if es_relevante(m):
            base = nombre_base(m["id"].split("/", 1)[-1]).lower()
            grupos.setdefault(base, []).append(m)

    registros = []
    for variantes in grupos.values():
        exacto = [v for v in variantes if nombre_base(v["id"].split("/", 1)[-1]) == v["id"].split("/", 1)[-1]]
        elegido = exacto[0] if exacto else max(variantes, key=lambda v: v.get("likes") or 0)
        registros.append(_registro(elegido, empresa_id))
    return registros


def _registro(m: dict, empresa_id: str) -> dict:
    hf_id = m["id"]
    nombre_repo = hf_id.split("/", 1)[-1]
    tarea = m.get("pipeline_tag")
    etiquetas = m.get("tags") or []
    capacidades = {c: 1 for c in TAREAS_HF.get(tarea, ())}
    if "conversational" in etiquetas:
        capacidades["cap_chat"] = 1
    por_texto = capacidades_por_texto(f"{nombre_repo} {' '.join(etiquetas)}")
    capacidades["cap_codigo"] = max(capacidades.get("cap_codigo", 0), por_texto["cap_codigo"])
    capacidades["cap_investigacion"] = por_texto["cap_investigacion"]
    _, activos = parametros_desde_nombre(nombre_repo)
    creado = (m.get("createdAt") or "")[:10] or None

    return {
        "id": f"hf:{hf_id}",
        "hf_id": hf_id,
        "nombre": nombre_base(nombre_repo).replace("-", " ").replace("_", " "),
        "empresa_id": empresa_id,
        "fecha": creado,
        "acceso": "local",
        "licencia": licencia(m),
        "parametros_b": parametros_b(m),
        "activos_b": activos,
        "peso_gb": peso_gb(m.get("safetensors")),
        "gated": texto_gated(m.get("gated")),
        **capacidades,
        "tarea": tarea,
        "enlaces": {"huggingface": f"https://huggingface.co/{hf_id}"},
        "extra": {"likes": m.get("likes"), "descargas": m.get("downloads")},
    }
