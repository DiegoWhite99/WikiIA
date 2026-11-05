"""Cliente de IA: Groq (nube, rápido) con respaldo en Ollama (local, gratis)."""
from __future__ import annotations

import json
import re
import time

import requests

from .config import Config

GROQ_URL = "https://api.groq.com/openai/v1"


class ErrorLLM(Exception):
    pass


class LimiteAlcanzado(ErrorLLM):
    def __init__(self, mensaje: str, espera: float):
        super().__init__(mensaje)
        self.espera = espera


def extraer_json(texto: str) -> dict:
    """Saca el primer objeto JSON de la respuesta (tolera ```json ... ``` y texto alrededor)."""
    texto = (texto or "").strip()
    texto = re.sub(r"^```(?:json)?|```$", "", texto, flags=re.M).strip()
    try:
        return json.loads(texto)
    except ValueError:
        pass
    inicio, fin = texto.find("{"), texto.rfind("}")
    if inicio == -1 or fin <= inicio:
        raise ValueError("la respuesta no contiene JSON")
    return json.loads(texto[inicio:fin + 1])


class ClienteLLM:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._groq_bloqueado_hasta = 0.0
        self._ultima_llamada_groq = 0.0
        self.sesion = requests.Session()

    # ---------- estado ----------
    def estado(self) -> dict:
        """Revisa qué motores están listos. No gasta tokens."""
        estado = {"groq": False, "groq_detalle": "", "ollama": False, "ollama_detalle": ""}
        if not self.cfg.groq_api_key:
            estado["groq_detalle"] = "Falta GROQ_API_KEY en .env"
        else:
            try:
                r = self.sesion.get(f"{GROQ_URL}/models", timeout=10,
                                    headers={"Authorization": f"Bearer {self.cfg.groq_api_key}"})
                if r.status_code == 200:
                    modelos = {m["id"] for m in r.json().get("data", [])}
                    estado["groq"] = self.cfg.groq_modelo in modelos
                    estado["groq_detalle"] = (
                        self.cfg.groq_modelo if estado["groq"]
                        else f"El modelo {self.cfg.groq_modelo} no está disponible. Opciones: {', '.join(sorted(modelos))}"
                    )
                else:
                    estado["groq_detalle"] = f"Groq respondió {r.status_code}: revisa la API key"
            except requests.RequestException as e:
                estado["groq_detalle"] = f"Sin conexión con Groq ({e.__class__.__name__})"
        try:
            r = self.sesion.get(f"{self.cfg.ollama_url}/api/tags", timeout=5)
            nombres = {m["name"] for m in r.json().get("models", [])}
            estado["ollama"] = self.cfg.ollama_modelo in nombres
            estado["ollama_detalle"] = (
                self.cfg.ollama_modelo if estado["ollama"]
                else f"Falta el modelo: ejecuta  ollama pull {self.cfg.ollama_modelo}"
            )
        except (requests.RequestException, ValueError):
            estado["ollama_detalle"] = "Ollama no está corriendo (ejecuta: ollama serve)"
        return estado

    # ---------- proveedores ----------
    def _groq(self, sistema: str, usuario: str) -> str:
        # Groq gratis limita peticiones y tokens por minuto: espaciamos las llamadas.
        espera = 2.5 - (time.time() - self._ultima_llamada_groq)
        if espera > 0:
            time.sleep(espera)
        cuerpo = {
            "model": self.cfg.groq_modelo,
            "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": usuario}],
            "temperature": 0.2,
            "max_completion_tokens": 1500,
            "response_format": {"type": "json_object"},
        }
        if "gpt-oss" in self.cfg.groq_modelo:
            cuerpo["reasoning_effort"] = "low"
        for intento in range(3):
            self._ultima_llamada_groq = time.time()
            r = self.sesion.post(
                f"{GROQ_URL}/chat/completions", json=cuerpo, timeout=90,
                headers={"Authorization": f"Bearer {self.cfg.groq_api_key}"},
            )
            if r.status_code == 429:
                try:
                    espera = float(r.headers.get("retry-after", "20"))
                except ValueError:
                    espera = 20.0
                if espera <= 30 and intento < 2:
                    time.sleep(espera + 0.5)
                    continue
                raise LimiteAlcanzado(f"Groq: límite alcanzado (reintentar en {espera:.0f}s)", espera)
            if r.status_code >= 400:
                raise ErrorLLM(f"Groq {r.status_code}: {r.text[:300]}")
            return r.json()["choices"][0]["message"]["content"] or ""
        raise LimiteAlcanzado("Groq: límite alcanzado", 60)

    def _ollama(self, sistema: str, usuario: str) -> str:
        r = self.sesion.post(
            f"{self.cfg.ollama_url}/api/chat",
            json={
                "model": self.cfg.ollama_modelo,
                "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": usuario}],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.2, "num_ctx": 4096},
            },
            timeout=600,
        )
        if r.status_code >= 400:
            raise ErrorLLM(f"Ollama {r.status_code}: {r.text[:300]}")
        return r.json()["message"]["content"]

    # ---------- API pública ----------
    def orden(self) -> list[str]:
        if self.cfg.llm_proveedor == "groq":
            return ["groq"]
        if self.cfg.llm_proveedor == "ollama":
            return ["ollama"]
        return ["groq", "ollama"]

    def json(self, sistema: str, usuario: str) -> tuple[dict, str]:
        """Pide una respuesta JSON. Devuelve (datos, 'proveedor:modelo')."""
        errores = []
        for proveedor in self.orden():
            if proveedor == "groq":
                if not self.cfg.groq_api_key:
                    errores.append("Groq sin API key")
                    continue
                if time.time() < self._groq_bloqueado_hasta:
                    errores.append("Groq en pausa por límite")
                    continue
            try:
                if proveedor == "groq":
                    texto, modelo = self._groq(sistema, usuario), self.cfg.groq_modelo
                else:
                    texto, modelo = self._ollama(sistema, usuario), self.cfg.ollama_modelo
                return extraer_json(texto), f"{proveedor}:{modelo}"
            except LimiteAlcanzado as e:
                self._groq_bloqueado_hasta = time.time() + e.espera
                errores.append(str(e))
            except (ErrorLLM, requests.RequestException, ValueError, KeyError) as e:
                errores.append(f"{proveedor}: {e}")
        raise ErrorLLM(" | ".join(errores) or "No hay motor de IA disponible")
