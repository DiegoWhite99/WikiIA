"""Catálogo de países y empresas: carga los JSON de data/ y resuelve a qué empresa pertenece cada modelo."""
from __future__ import annotations

import json

from .config import DATOS
from .db import cargar_json


def _leer(nombre: str):
    with open(DATOS / nombre, encoding="utf-8") as f:
        return json.load(f)


def sincronizar_catalogo(con) -> None:
    """Copia países y empresas del JSON a la base sin pisar los países que el usuario editó."""
    for p in _leer("paises.json"):
        con.execute(
            "INSERT INTO paises(codigo, nombre, bandera, nota) VALUES(?,?,?,?) "
            "ON CONFLICT(codigo) DO UPDATE SET nombre=excluded.nombre, "
            "bandera=excluded.bandera, nota=excluded.nota",
            (p["codigo"], p["nombre"], p["bandera"], p.get("nota", "")),
        )
    for e in _leer("empresas.json"):
        con.execute(
            "INSERT INTO empresas(id, nombre, pais, web, or_prefijos, hf_orgs, chat_url, api_url) "
            "VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET nombre=excluded.nombre, web=excluded.web, "
            "or_prefijos=excluded.or_prefijos, hf_orgs=excluded.hf_orgs, "
            "chat_url=excluded.chat_url, api_url=excluded.api_url, "
            "pais=CASE WHEN empresas.editado=1 THEN empresas.pais ELSE excluded.pais END",
            (e["id"], e["nombre"], e["pais"], e.get("web"),
             json.dumps(e.get("or", [])), json.dumps(e.get("hf", [])), e.get("chat"), e.get("api")),
        )


class Resolutor:
    """Traduce prefijos de OpenRouter y organizaciones de Hugging Face a empresas del catálogo."""

    def __init__(self, con):
        self.con = con
        self.por_or: dict[str, str] = {}
        self.por_hf: dict[str, str] = {}
        for fila in con.execute("SELECT id, or_prefijos, hf_orgs FROM empresas"):
            for pref in cargar_json(fila["or_prefijos"], []):
                self.por_or[pref.lower()] = fila["id"]
            for org in cargar_json(fila["hf_orgs"], []):
                self.por_hf[org.lower()] = fila["id"]

    def orgs_hf(self) -> list[tuple[str, str]]:
        """(organización, empresa_id) de todas las organizaciones a vigilar en Hugging Face."""
        orgs = []
        for fila in self.con.execute("SELECT id, hf_orgs FROM empresas ORDER BY id"):
            for org in cargar_json(fila["hf_orgs"], []):
                orgs.append((org, fila["id"]))
        return orgs

    def empresa_or(self, prefijo: str, nombre_visible: str | None = None) -> str:
        prefijo = prefijo.lower()
        if prefijo in self.por_or:
            return self.por_or[prefijo]
        # Empresa nueva que no está en el catálogo: queda "Sin clasificar" hasta que el usuario le asigne país.
        empresa_id = prefijo
        self.con.execute(
            "INSERT OR IGNORE INTO empresas(id, nombre, pais, or_prefijos) VALUES(?,?,?,?)",
            (empresa_id, nombre_visible or prefijo, "XX", json.dumps([prefijo])),
        )
        self.por_or[prefijo] = empresa_id
        return empresa_id

    def empresa_hf(self, org: str) -> str | None:
        return self.por_hf.get(org.lower())
