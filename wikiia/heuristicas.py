"""Reglas deterministas: capacidades, potencia estimada, hardware y advertencias.

Todo lo que se puede deducir de los datos de las fuentes se deduce aquí, sin IA,
para que la ficha sea útil aunque Groq y Ollama no estén disponibles.
"""
from __future__ import annotations

import re
from datetime import date

ETIQUETAS_CAP = {
    "cap_codigo": "💻 Código",
    "cap_investigacion": "🔬 Investigación",
    "cap_chat": "💬 Chat",
    "cap_vision": "👁️ Visión",
    "cap_imagen": "🎨 Crear imágenes",
    "cap_video": "🎬 Crear videos",
    "cap_audio": "🎙️ Voz / audio",
    "cap_agentes": "🦾 Agentes",
}
ICONO_CAP = {k: v.split(" ", 1)[0] for k, v in ETIQUETAS_CAP.items()}

_RE_CODIGO = re.compile(
    r"\b(code|coder|coding|codex|codestral|devstral|programming|programaci[oó]n|software engineering|"
    r"swe-bench|agentic coding|developer)", re.I)
_RE_RAZONA = re.compile(
    r"\b(reason\w*|thinking|think|research|deep research|math\w*|r1|razonamiento)\b", re.I)
_RE_PARAMS = re.compile(r"(?:^|[-_/ :])(\d+(?:\.\d+)?)([bt])(?=$|[-_ :.)])", re.I)
_RE_ACTIVOS = re.compile(r"(?:^|[-_/ ])a(\d+(?:\.\d+)?)b(?=$|[-_ :.)])", re.I)

# Tareas de Hugging Face que nos interesan -> capacidades que implican.
TAREAS_HF = {
    "text-generation": ("cap_chat",),
    "text2text-generation": ("cap_chat",),
    "image-text-to-text": ("cap_chat", "cap_vision"),
    "video-text-to-text": ("cap_chat", "cap_vision"),
    "visual-question-answering": ("cap_vision",),
    "any-to-any": ("cap_chat", "cap_vision", "cap_audio"),
    "audio-text-to-text": ("cap_chat", "cap_audio"),
    "text-to-image": ("cap_imagen",),
    "image-to-image": ("cap_imagen",),
    "unconditional-image-generation": ("cap_imagen",),
    "text-to-video": ("cap_video",),
    "image-to-video": ("cap_video",),
    "image-text-to-video": ("cap_video",),
    "video-to-video": ("cap_video",),
    "text-to-speech": ("cap_audio",),
    "text-to-audio": ("cap_audio",),
    "automatic-speech-recognition": ("cap_audio",),
    "audio-to-audio": ("cap_audio",),
}

_LICENCIAS_NO_COMERCIALES = re.compile(r"(^|[-_ ])(nc|non-?commercial|noncommercial|research)([-_ ]|$)", re.I)
_LICENCIAS_ABIERTAS = {"mit", "apache-2.0", "bsd-3-clause", "bsd-2-clause", "cc-by-4.0", "openrail", "openrail++"}


def precio_por_millon(valor) -> float | None:
    """OpenRouter da el precio por token como texto; lo pasamos a USD por 1M tokens."""
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    if numero < 0:
        return None
    return round(numero * 1_000_000, 4)


def parametros_desde_nombre(texto: str) -> tuple[float | None, float | None]:
    """Extrae '70B', '1T' o 'A3B' (activos) del nombre del modelo."""
    total = None
    for numero, unidad in _RE_PARAMS.findall(texto or ""):
        valor = float(numero) * (1000 if unidad.lower() == "t" else 1)
        total = max(total or 0, valor)
    activos = None
    coincidencia = _RE_ACTIVOS.search(texto or "")
    if coincidencia:
        activos = float(coincidencia.group(1))
    return total, activos


def capacidades_por_texto(texto: str) -> dict:
    return {
        "cap_codigo": int(bool(_RE_CODIGO.search(texto or ""))),
        "cap_investigacion": int(bool(_RE_RAZONA.search(texto or ""))),
    }


def potencia_heuristica(fila) -> int:
    """Nivel 1-5 estimado con tamaño y precio. Es solo un punto de partida: la IA o tú lo corrigen."""
    candidatos = []
    params = num(fila["parametros_b"])
    if params:
        candidatos.append(5 if params >= 400 else 4 if params >= 100 else 3 if params >= 25 else 2 if params >= 5 else 1)
    precio = num(fila["precio_salida"])
    if precio and precio > 0:
        candidatos.append(5 if precio >= 10 else 4 if precio >= 3 else 3 if precio >= 0.8 else 2)
    potencia = max(candidatos) if candidatos else (3 if (num(fila["cap_imagen"]) or num(fila["cap_video"])) else 2)
    # En IA un año es mucho: lo que era frontera hace un año ya no lo es.
    fecha = texto(fila["fecha"])
    if fecha:
        try:
            dias = (date.today() - date.fromisoformat(fecha[:10])).days
        except ValueError:
            dias = 0
        if dias > 730:
            potencia = min(potencia, 3)
        elif dias > 365:
            potencia = min(potencia, 4)
    return potencia


def num(valor) -> float | None:
    """Número o None (convierte NaN de pandas y textos vacíos en None)."""
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return None if numero != numero else numero  # NaN != NaN


def texto(valor) -> str:
    return valor if isinstance(valor, str) else ""


def acceso_real(f, rotos: frozenset[str] | set[str] = frozenset()) -> str:
    """'ambos' pasa a 'api' si su página de Hugging Face no existe (OpenRouter a veces anuncia pesos que
    la empresa aún no publicó). Si los publica, la próxima revisión de enlaces lo devuelve a 'ambos'."""
    if f["acceso"] == "ambos" and texto(f["hf_id"]) and f"https://huggingface.co/{f['hf_id']}" in rotos:
        return "api"
    return f["acceso"]


def enlace_principal(f, rotos: frozenset[str] | set[str] = frozenset()) -> str | None:
    """Local → su página de descarga en Hugging Face. Nube → su chat o web oficial.

    `f` necesita: acceso, hf_id, enlaces, chat_url, empresa_web (fila de pandas o de sqlite).
    `rotos`: URLs que no abren (wikiia/enlaces.py); se salta a la siguiente opción.
    """
    from .db import cargar_json  # aquí para no cargar la base al importar las reglas

    enlaces = cargar_json(f["enlaces"], {})
    candidatos = []
    if f["acceso"] in ("local", "ambos") and texto(f["hf_id"]):
        candidatos.append(f"https://huggingface.co/{f['hf_id']}")
    candidatos += [texto(f["chat_url"]), enlaces.get("web"), texto(f["empresa_web"]), enlaces.get("openrouter")]
    if texto(f["hf_id"]):  # un modelo de nube con pesos publicados: su página de HF también sirve
        candidatos.append(f"https://huggingface.co/{f['hf_id']}")
    return next((u for u in candidatos if u and u not in rotos), None)


def requisitos_local(fila, perfil: dict) -> tuple[str, str | None]:
    """Memoria aproximada para correrlo en tu equipo y si tu equipo alcanza.

    Devuelve (texto, veredicto) donde veredicto es 'si' | 'justo' | 'no' | None.
    """
    params = num(fila["parametros_b"])
    if not params or fila["acceso"] == "api":
        return ("", None)
    es_difusion = bool(num(fila["cap_imagen"]) or num(fila["cap_video"])) and not num(fila["cap_chat"])
    # LLM cuantizado a 4 bits ≈ 0.6 GB por mil millones; modelos de imagen/video suelen ir en 16 bits.
    necesita = params * (2.0 if es_difusion else 0.6) + 1
    ram, vram = perfil.get("ram_gb", 0), perfil.get("vram_gb", 0)
    formato = "16 bits" if es_difusion else "4 bits (Q4)"
    descripcion = f"≈ {necesita:.0f} GB de memoria en {formato}"
    activos = num(fila["activos_b"])
    if activos:
        descripcion += f" · MoE: usa solo {activos:g}B activos, va más rápido de lo que su tamaño sugiere"
    if vram and necesita <= vram:
        return (descripcion, "si")
    if ram and necesita <= ram * 0.6:
        return (descripcion, "si")
    if ram and necesita <= ram * 0.85:
        return (descripcion, "justo")
    return (descripcion, "no")


def veredicto_texto(veredicto: str | None, perfil: dict) -> str:
    gpu = perfil.get("vram_gb", 0)
    if veredicto == "si":
        return "✅ Sí corre en tu PC" + ("" if gpu else " (en CPU: funciona pero lento)")
    if veredicto == "justo":
        return "⚠️ Muy justo para tu PC: cierra otros programas o usa una versión más pequeña"
    if veredicto == "no":
        return "❌ No cabe en tu PC: úsalo por API o en un equipo más grande"
    return ""


def notas_licencia(licencia) -> list[str]:
    licencia = texto(licencia)
    if not licencia:
        return []
    lic = licencia.lower()
    if _LICENCIAS_NO_COMERCIALES.search(lic):
        return [f"Licencia {licencia}: NO permite uso comercial sin permiso."]
    if lic in _LICENCIAS_ABIERTAS:
        return [f"Licencia {licencia}: abierta, permite uso comercial."]
    if lic in ("other", "unknown"):
        return ["Licencia propia: lee sus condiciones antes de usarlo en un negocio."]
    return [f"Licencia {licencia}: revisa sus condiciones de uso comercial."]


def notas_importantes(fila, nota_pais: str, notas_ia: list[str]) -> list[str]:
    notas: list[str] = []
    nombre = texto(fila["nombre"]).lower()
    if any(p in nombre for p in ("preview", "-exp", " exp", "beta", "alpha", "experimental")):
        notas.append("Versión experimental o preview: puede cambiar o desaparecer sin aviso.")
    if fila["acceso"] == "api":
        notas.append("Solo en la nube: necesitas internet y tus datos salen de tu equipo.")
    elif fila["acceso"] in ("local", "ambos"):
        notas.append("Pesos abiertos: puedes descargarlo y usarlo sin enviar datos a nadie.")
    if num(fila["gratis"]) and fila["acceso"] != "local":
        notas.append("Tiene opción gratuita (con límites de uso).")
    notas.extend(notas_licencia(fila["licencia"]))
    if nota_pais:
        notas.append(nota_pais)
    notas.extend(n for n in notas_ia if n and n not in notas)
    return notas


def estrellas(potencia) -> str:
    try:
        p = max(0, min(5, int(potencia)))
    except (TypeError, ValueError):
        return "—"
    return "★" * p + "☆" * (5 - p)


ETIQUETA_POTENCIA = {5: "Frontera", 4: "Alto", 3: "Medio", 2: "Ligero", 1: "Básico"}
ETIQUETA_ACCESO = {"api": "☁️ API / nube", "local": "🖥️ Local", "ambos": "🖥️ Local + ☁️ API"}
