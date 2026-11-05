"""Cómo descargar y ejecutar cada modelo local, y qué equipo mínimo necesita.

Las versiones GGUF (Ollama, LM Studio, llama.cpp, Jan) y MLX (Mac) se buscan en Hugging Face
por la relación oficial 'base_model:quantized:<modelo>', así que corresponden exactamente al modelo.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date

from . import db
from .fuentes import huggingface, nueva_sesion
from .heuristicas import num, texto

VIGENCIA_DIAS = 7
CUANT_PREFERIDAS = ("Q4_K_M", "UD-Q4_K_XL", "Q4_K_S", "IQ4_XS", "Q4_0", "Q5_K_M", "Q6_K", "Q8_0")
# Publicadores de versiones comprimidas muy usados y confiables.
CONFIABLES = ("unsloth", "bartowski", "lmstudio-community", "ggml-org", "mlx-community")
_RE_CUANT = re.compile(r"((?:UD-)?I?Q\d(?:_K)?(?:_[A-Z0-9]+)?|BF16|F16|F32)", re.I)


# ───────────────────────── datos de Hugging Face ─────────────────────────
# Versiones que ya no son el modelo original (sin filtros de seguridad, mezclas, formatos experimentales).
_RE_MODIFICADO = re.compile(r"abliterat|uncensor|heretic|nsfw|roleplay|[-_]rp[-_]|merge|nvfp4|mtp|[-_]vq[-_]|"
                            r"imatrix-nv|distill", re.I)


def _repos(sesion, hf_id: str, formato: str, limite: int = 12) -> list[dict]:
    respuesta = sesion.get(
        huggingface.URL,
        params=[("filter", f"base_model:quantized:{hf_id}"), ("filter", formato),
                ("sort", "downloads"), ("direction", "-1"), ("limit", str(limite))],
        timeout=30,
    )
    respuesta.raise_for_status()
    return [{"repo": m["id"], "descargas": m.get("downloads") or 0} for m in respuesta.json()
            if not _RE_MODIFICADO.search(m["id"].split("/", 1)[-1])]


def _ordenar(repos: list[dict], org: str) -> list[dict]:
    """Primero la versión oficial de la misma empresa, luego publicadores confiables, luego popularidad."""
    def prioridad(r):
        autor = r["repo"].split("/", 1)[0].lower()
        return (autor != org.lower(), autor not in CONFIABLES, -r["descargas"])
    return sorted(repos, key=prioridad)


def _cuantizaciones(sesion, repo: str) -> dict[str, float]:
    """Variantes del GGUF (Q4_K_M, Q8_0…) con su tamaño en GB."""
    respuesta = sesion.get(f"{huggingface.URL}/{repo}", params={"blobs": "true"}, timeout=30)
    respuesta.raise_for_status()
    tamanos: dict[str, float] = {}
    for archivo in respuesta.json().get("siblings", []):
        nombre = archivo.get("rfilename", "")
        if not nombre.lower().endswith(".gguf") or "mmproj" in nombre.lower():
            continue
        coincidencia = _RE_CUANT.search(nombre.rsplit("/", 1)[-1]) or _RE_CUANT.search(nombre)
        if coincidencia:
            clave = coincidencia.group(1).upper()
            tamanos[clave] = round(tamanos.get(clave, 0) + (archivo.get("size") or 0) / 1e9, 2)
    return {k: v for k, v in tamanos.items() if v > 0}


def buscar_descargas(sesion, hf_id: str) -> dict:
    org = hf_id.split("/", 1)[0]
    gguf = _ordenar(_repos(sesion, hf_id, "gguf"), org)[:3]
    mlx = _ordenar(_repos(sesion, hf_id, "mlx"), org)[:2]
    cuants = _cuantizaciones(sesion, gguf[0]["repo"]) if gguf else {}
    return {"gguf": gguf, "cuantizaciones": cuants, "mlx": mlx, "revisado": date.today().isoformat()}


def obtener_descargas(con, fila, sesion=None, forzar: bool = False) -> dict:
    """Devuelve las descargas guardadas o las busca (y guarda) si faltan o tienen más de 7 días."""
    hf_id = texto(fila["hf_id"])
    if not hf_id:
        return {}
    actual = db.cargar_json(fila["descargas"], {})
    if actual and not forzar:
        try:
            if (date.today() - date.fromisoformat(actual.get("revisado", "2000-01-01"))).days < VIGENCIA_DIAS:
                return actual
        except ValueError:
            pass
    try:
        nuevo = buscar_descargas(sesion or nueva_sesion(), hf_id)
    except Exception:  # noqa: BLE001 - sin internet se muestra lo que haya
        return actual
    con.execute("UPDATE modelos SET descargas=? WHERE id=?", (json.dumps(nuevo), fila["id"]))
    con.commit()
    return nuevo


def cuant_principal(descargas: dict) -> tuple[str | None, float | None]:
    cuants = descargas.get("cuantizaciones") or {}
    for preferida in CUANT_PREFERIDAS:
        if preferida in cuants:
            return preferida, cuants[preferida]
    if cuants:
        clave = min(cuants, key=cuants.get)
        return clave, cuants[clave]
    return None, None


# ───────────────────────── requisitos mínimos ─────────────────────────
def gpu_para(gb: float) -> str:
    if gb <= 6:
        return "GPU de 6–8 GB (RTX 3060 / 4060)"
    if gb <= 8:
        return "GPU de 8 GB (RTX 4060 / 3070)"
    if gb <= 12:
        return "GPU de 12 GB (RTX 3060 12 GB / 4070)"
    if gb <= 16:
        return "GPU de 16 GB (RTX 4060 Ti 16 GB / 4080 / 5080)"
    if gb <= 24:
        return "GPU de 24 GB (RTX 3090 / 4090)"
    if gb <= 32:
        return "GPU de 32 GB (RTX 5090)"
    if gb <= 48:
        return "48 GB (2× RTX 3090/4090 o RTX A6000)"
    if gb <= 80:
        return "80 GB (1× A100 / H100)"
    return f"{math.ceil(gb / 80)}× H100 / A100 de 80 GB (servidor)"


def _ram_comercial(gb: float) -> int:
    for tamano in (8, 16, 24, 32, 48, 64, 96, 128, 192, 256, 512, 768, 1024):
        if gb <= tamano:
            return tamano
    return int(math.ceil(gb / 256) * 256)


def es_difusion(fila) -> bool:
    return bool(num(fila["cap_imagen"]) or num(fila["cap_video"])) and not num(fila["cap_chat"])


def requisitos(fila, descargas: dict) -> list[dict]:
    """Tabla de equipo mínimo por formato. Incluye memoria para una conversación normal (~8K tokens)."""
    params = num(fila["parametros_b"])
    peso_original = num(fila["peso_gb"]) if "peso_gb" in fila.keys() else None
    if not params and not peso_original:
        return []
    params = params or peso_original / 2
    filas = []

    def fila_req(formato, motores, disco, memoria, cpu):
        return {"Formato": formato, "Motores": motores, "Disco": f"{disco:,.1f} GB",
                "Memoria mínima": f"{memoria:,.0f} GB", "GPU recomendada": gpu_para(memoria),
                "Solo CPU (sin GPU)": cpu, "Mac (memoria unificada)": f"{_ram_comercial(memoria + 4)} GB o más"}

    if es_difusion(fila):
        base = peso_original or params * 2
        filas.append(fila_req("16 bits (original)", "ComfyUI, Diffusers", base, base * 1.15 + 2,
                              "No práctico (minutos por imagen)"))
        filas.append(fila_req("8 bits (FP8 / GGUF Q8)", "ComfyUI", base / 2, base / 2 * 1.15 + 2,
                              "No práctico"))
        _, q4 = cuant_principal(descargas)
        if q4:
            filas.append(fila_req("4 bits (GGUF Q4)", "ComfyUI + nodo GGUF", q4, q4 * 1.15 + 2,
                                  "Muy lento, pero posible"))
        return filas

    _, q4 = cuant_principal(descargas)
    q4 = q4 or params * 0.57
    q8 = (descargas.get("cuantizaciones") or {}).get("Q8_0") or params * 1.07
    original = peso_original or params * 2
    for formato, motores, disco in (
        ("4 bits (GGUF Q4)", "Ollama, LM Studio, llama.cpp, Jan", q4),
        ("8 bits (GGUF Q8)", "Ollama, LM Studio, llama.cpp", q8),
        ("Original (16 bits)", "vLLM, SGLang, Transformers", original),
    ):
        memoria = disco * 1.1 + 1.5  # pesos + caché de la conversación + sistema
        if formato.startswith("Original"):
            cpu = "No recomendado"
        elif memoria <= 96:
            cpu = f"Sí, con {_ram_comercial(memoria + 4)} GB de RAM (lento)"
        else:
            cpu = "No práctico"
        filas.append(fila_req(formato, motores, disco, memoria, cpu))
    return filas


# ───────────────────────── motores ─────────────────────────
def motores(fila, descargas: dict) -> list[dict]:
    """Opciones para ejecutar el modelo, con el comando listo para copiar."""
    hf_id = texto(fila["hf_id"])
    if not hf_id:
        return []
    nombre = texto(fila["nombre"]) or hf_id
    carpeta = hf_id.split("/", 1)[1]
    lista: list[dict] = []

    if es_difusion(fila):
        es_video = bool(num(fila["cap_video"]))
        lista.append({
            "motor": "ComfyUI", "icono": "🧩", "gpu": True,
            "para": "Interfaz visual por nodos; la más usada para crear imágenes y videos en local.",
            "comando": f"# 1) Instala ComfyUI: https://www.comfy.org\n"
                       f"# 2) Descarga los pesos:\nhf download {hf_id} --local-dir ComfyUI/models/{carpeta}\n"
                       f"# 3) Abre una plantilla (workflow) de '{nombre}' desde el menú Templates",
            "web": "https://www.comfy.org",
        })
        gguf = descargas.get("gguf") or []
        if gguf:
            cuant, tamano = cuant_principal(descargas)
            lista.append({
                "motor": "ComfyUI ligero (GGUF)", "icono": "🪶", "gpu": True,
                "para": f"Versión comprimida para GPUs pequeñas"
                        + (f": la variante {cuant} pesa {tamano:,.1f} GB." if tamano else "."),
                "comando": "# 1) En ComfyUI instala el nodo 'ComfyUI-GGUF' (desde el Manager)\n"
                           f"# 2) Descarga la variante comprimida:\nhf download {gguf[0]['repo']}"
                           + (f" --include \"*{cuant}*\"" if cuant else "")
                           + " --local-dir ComfyUI/models/unet\n# 3) Cárgala con el nodo 'Unet Loader (GGUF)'",
                "web": "https://github.com/city96/ComfyUI-GGUF",
            })
        salida = "video = pipe(prompt).frames[0]" if es_video else "imagen = pipe(prompt).images[0]\nimagen.save('resultado.png')"
        lista.append({
            "motor": "Diffusers (Python)", "icono": "🤗", "gpu": True,
            "para": "Librería de Hugging Face para generar desde código.",
            "comando": "pip install -U diffusers transformers accelerate torch\n\n"
                       "import torch\nfrom diffusers import DiffusionPipeline\n"
                       f"pipe = DiffusionPipeline.from_pretrained(\"{hf_id}\", torch_dtype=torch.bfloat16).to(\"cuda\")\n"
                       f"prompt = \"un astronauta paseando por Bogotá, foto realista\"\n{salida}",
            "web": "https://huggingface.co/docs/diffusers",
        })
        return lista

    gguf = (descargas.get("gguf") or [])
    cuant, _ = cuant_principal(descargas)
    etiqueta = f":{cuant}" if cuant else ""
    if gguf:
        repo = gguf[0]["repo"]
        lista += [
            {"motor": "Ollama", "icono": "🦙", "gpu": False,
             "para": "Lo más fácil: un comando y listo. Funciona sin GPU (más lento).",
             "comando": f"ollama run hf.co/{repo}{etiqueta}", "web": "https://ollama.com"},
            {"motor": "LM Studio", "icono": "🖥️", "gpu": False,
             "para": "Programa con ventanas, sin terminal. Ideal para empezar.",
             "comando": f"# En LM Studio → pestaña Discover (🔍) → busca:\n{repo}\n# y elige la variante {cuant or 'Q4_K_M'}",
             "web": "https://lmstudio.ai"},
            {"motor": "llama.cpp", "icono": "⚙️", "gpu": False,
             "para": "El motor ligero sobre el que funcionan Ollama y LM Studio. Máximo control.",
             "comando": f"llama-server -hf {repo}{etiqueta}\n# luego abre http://localhost:8080",
             "web": "https://github.com/ggml-org/llama.cpp"},
            {"motor": "Jan", "icono": "💬", "gpu": False,
             "para": "App de chat local, gratuita y de código abierto.",
             "comando": f"# En Jan → Hub → pega este enlace:\nhttps://huggingface.co/{repo}",
             "web": "https://jan.ai"},
        ]
    else:
        lista.append({
            "motor": "Ollama", "icono": "🦙", "gpu": False,
            "para": "Todavía no hay versión GGUF publicada de este modelo. Búscalo en la biblioteca de Ollama "
                    "o espera unos días: la comunidad (unsloth, bartowski) suele publicarla pronto.",
            "comando": f"# Busca '{nombre}' en https://ollama.com/search", "web": f"https://ollama.com/search?q={carpeta}",
        })

    original = num(fila["peso_gb"]) if "peso_gb" in fila.keys() else None
    original = original or (num(fila["parametros_b"]) or 0) * 2
    gpus = 1
    while original * 1.2 > 80 * gpus and gpus < 16:
        gpus *= 2
    paralelo = f" --tensor-parallel-size {gpus}" if gpus > 1 else ""
    lista += [
        {"motor": "vLLM", "icono": "🚀", "gpu": True,
         "para": "Servidor de alto rendimiento para GPU (NVIDIA/AMD). Da una API igual a la de OpenAI.",
         "comando": f"pip install -U vllm\nvllm serve {hf_id}{paralelo}", "web": "https://docs.vllm.ai"},
        {"motor": "SGLang", "icono": "⚡", "gpu": True,
         "para": "Servidor muy rápido para GPU; muy usado con modelos grandes tipo MoE.",
         "comando": f"pip install -U sglang\npython -m sglang.launch_server --model-path {hf_id}"
                    + (f" --tp {gpus}" if gpus > 1 else ""),
         "web": "https://docs.sglang.ai"},
        {"motor": "Transformers (Python)", "icono": "🤗", "gpu": True,
         "para": "Librería de Hugging Face para programar y experimentar.",
         "comando": "pip install -U transformers accelerate torch\n\nfrom transformers import pipeline\n"
                    f"chat = pipeline(\"text-generation\", model=\"{hf_id}\", device_map=\"auto\")\n"
                    "print(chat([{\"role\": \"user\", \"content\": \"Hola, ¿qué sabes hacer?\"}], max_new_tokens=200))",
         "web": "https://huggingface.co/docs/transformers"},
    ]
    mlx = descargas.get("mlx") or []
    if mlx:
        lista.append({
            "motor": "MLX (Mac)", "icono": "🍎", "gpu": False,
            "para": "Para Mac con chip Apple (M1 a M4): aprovecha la memoria unificada.",
            "comando": f"pip install -U mlx-lm\nmlx_lm.generate --model {mlx[0]['repo']} --prompt \"Hola\"",
            "web": "https://github.com/ml-explore/mlx-lm"})
    return lista


def comando_descarga(fila) -> str:
    hf_id = texto(fila["hf_id"])
    return f"pip install -U huggingface_hub\nhf download {hf_id} --local-dir ./{hf_id.split('/', 1)[1]}" if hf_id else ""
