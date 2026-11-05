"""Configuración de WikiIA: rutas, variables del archivo .env y perfil del equipo."""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DATOS = RAIZ / "data"
DB_RUTA = RAIZ / "wikiia.db"
BLOQUEO = RAIZ / ".wikiia.lock"
USER_AGENT = "WikiIA/1.0 (catalogo local de modelos de IA)"

try:
    from dotenv import load_dotenv

    load_dotenv(RAIZ / ".env")
except ImportError:  # python-dotenv es opcional
    pass


def _env(nombre: str, defecto: str = "") -> str:
    return (os.getenv(nombre) or defecto).strip()


@dataclass(frozen=True)
class Config:
    llm_proveedor: str
    groq_api_key: str
    groq_modelo: str
    ollama_url: str
    ollama_modelo: str
    telegram_token: str
    telegram_chat_id: str
    investigar_por_ejecucion: int


def cargar_config() -> Config:
    try:
        por_ejecucion = int(_env("INVESTIGAR_POR_EJECUCION", "25"))
    except ValueError:
        por_ejecucion = 25
    proveedor = _env("LLM_PROVEEDOR", "auto").lower()
    if proveedor not in ("auto", "groq", "ollama"):
        proveedor = "auto"
    return Config(
        llm_proveedor=proveedor,
        groq_api_key=_env("GROQ_API_KEY"),
        groq_modelo=_env("GROQ_MODELO", "openai/gpt-oss-120b"),
        ollama_url=_env("OLLAMA_URL", "http://localhost:11434").rstrip("/"),
        ollama_modelo=_env("OLLAMA_MODELO", "qwen2.5:3b"),
        telegram_token=_env("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_env("TELEGRAM_CHAT_ID"),
        investigar_por_ejecucion=max(0, por_ejecucion),
    )


@lru_cache(maxsize=1)
def perfil_equipo() -> dict:
    """RAM y VRAM de este computador, para decir si un modelo local corre aquí."""
    ram_gb = 0.0
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for linea in f:
                if linea.startswith("MemTotal"):
                    ram_gb = int(linea.split()[1]) / 1024 / 1024
                    break
    except OSError:
        pass
    vram_gb = 0.0
    if shutil.which("nvidia-smi"):
        try:
            salida = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            ).stdout
            vram_gb = sum(float(x) for x in salida.split()) / 1024
        except (OSError, ValueError, subprocess.SubprocessError):
            vram_gb = 0.0
    return {"ram_gb": round(ram_gb, 1), "vram_gb": round(vram_gb, 1)}
