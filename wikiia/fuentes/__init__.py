"""Fuentes de datos de WikiIA."""
import requests

from ..config import USER_AGENT


def nueva_sesion() -> requests.Session:
    sesion = requests.Session()
    sesion.headers["User-Agent"] = USER_AGENT
    return sesion
