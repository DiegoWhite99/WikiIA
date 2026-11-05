"""Cuentas de WikiIA guardadas en PostgreSQL: para entrar a la app web hay que iniciar sesión.

Registro: nombre, correo y contraseña escrita dos veces. El correo es la llave de cada usuario (llave
primaria de wikiia_usuarios y llave foránea de wikiia_sesiones). Cada persona guarda además SU clave de
Groq una sola vez y la tiene cada vez que vuelve.
- La contraseña nunca se guarda: solo su huella scrypt con sal (no se puede revertir).
- La clave de Groq se guarda cifrada (Fernet) con WIKIIA_SECRETO, que vive fuera de la base.
- La sesión se recuerda 30 días con una cookie que lleva un código al azar; en la base solo queda
  su huella SHA-256, así que quien lea la base no puede entrar como nadie.
- 5 contraseñas equivocadas seguidas bloquean esa cuenta 15 minutos.
- Root (administradores): los correos cuya huella SHA-256 está en ROOTS_SHA256 (así el repositorio público
  no muestra los correos) o los que pongas en WIKIIA_ROOTS (separados por comas). Ven la pestaña
  «👑 Administración» con los usuarios registrados y pueden borrar cuentas.

Variables (en .env o en Settings → Secrets de Streamlit Cloud):
    DATABASE_URL    postgresql://usuario:contraseña@servidor/base?sslmode=require   (Neon, Supabase…)
    WIKIIA_SECRETO  texto largo al azar. Si lo cambias, las claves guardadas ya no abren y cada
                    persona tendrá que pegar la suya otra vez (las cuentas siguen funcionando).
Sin esas dos variables no se pide iniciar sesión (así funciona en tu PC) y la clave de Groq dura solo la sesión.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import secrets
from dataclasses import dataclass
from functools import lru_cache

DIAS_SESION = 30
MAX_FALLOS = 5
MINUTOS_BLOQUEO = 15
MIN_CONTRASENA = 8
MAX_CONTRASENA = 128
MAX_NOMBRE = 80
_RE_CORREO = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s.]{2,}$")
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}
# Huellas SHA-256 de los correos root (en minúsculas). Para agregar uno:
#   python3 -c "import hashlib; print(hashlib.sha256('correo@ejemplo.com'.encode()).hexdigest())"
ROOTS_SHA256 = frozenset({
    "652b8e0a5768321c559635a46613075bf42d21946400611da40ee805d58d7041",
    "5604114a8e4523fed2b4b12f446ec81d3435a8101bacf5a6e56cec30f0634edc",
})

ESQUEMA = """
CREATE TABLE IF NOT EXISTS wikiia_usuarios (
    correo TEXT PRIMARY KEY,                -- en minúsculas: identifica a cada usuario
    nombre TEXT NOT NULL,
    clave_hash TEXT NOT NULL,               -- huella scrypt de la contraseña (nunca la contraseña)
    groq_cifrada TEXT,                      -- clave de Groq cifrada con WIKIIA_SECRETO
    fallos INTEGER NOT NULL DEFAULT 0,
    bloqueado_hasta TIMESTAMPTZ,
    creado TIMESTAMPTZ NOT NULL DEFAULT now(),
    ultimo_ingreso TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS wikiia_sesiones (
    token_hash TEXT PRIMARY KEY,
    correo TEXT NOT NULL REFERENCES wikiia_usuarios(correo) ON DELETE CASCADE ON UPDATE CASCADE,
    creada TIMESTAMPTZ NOT NULL DEFAULT now(),
    expira TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_wikiia_sesiones_correo ON wikiia_sesiones(correo);
"""

log = logging.getLogger(__name__)


class ErrorCuenta(Exception):
    """El mensaje ya viene listo para mostrarlo a la persona."""


@dataclass(frozen=True)
class Usuario:
    correo: str
    nombre: str
    groq_clave: str = ""  # "" si todavía no la guardó
    root: bool = False    # administrador: ve la pestaña «👑 Administración»


def _b64(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).decode().rstrip("=")


def _desde_b64(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def huella_contrasena(contrasena: str) -> str:
    sal = secrets.token_bytes(16)
    h = hashlib.scrypt(contrasena.encode(), salt=sal, dklen=32, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${_b64(sal)}${_b64(h)}"


def contrasena_coincide(contrasena: str, huella: str) -> bool:
    try:
        _, n, r, p, sal, h = huella.split("$")
        esperado = _desde_b64(h)
        calculado = hashlib.scrypt(contrasena.encode(), salt=_desde_b64(sal), n=int(n), r=int(r), p=int(p),
                                   dklen=len(esperado))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(calculado, esperado)


@lru_cache(maxsize=1)
def _huella_falsa() -> str:
    return huella_contrasena(secrets.token_urlsafe(12))


def _huella_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalizar_correo(correo: str) -> str:
    return (correo or "").strip().lower()


def es_root(correo: str, extra: frozenset[str] = frozenset()) -> bool:
    correo = normalizar_correo(correo)
    return correo in extra or hashlib.sha256(correo.encode()).hexdigest() in ROOTS_SHA256


class Cuentas:
    """Registro, ingreso, sesiones y la clave de Groq cifrada de cada persona."""

    _esquema_listo: set[str] = set()

    def __init__(self, url: str, secreto: str, roots: frozenset[str] = frozenset()):
        from cryptography.fernet import Fernet

        self.url = url
        self.roots = frozenset(normalizar_correo(c) for c in roots if c.strip())
        llave = hashlib.sha256(b"wikiia-groq|" + secreto.encode()).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(llave))

    @classmethod
    def desde_config(cls, cfg) -> "Cuentas | None":
        if not (cfg.database_url and cfg.secreto):
            return None
        return cls(cfg.database_url, cfg.secreto, cfg.roots)

    # ---------- conexión ----------
    def _conectar(self):
        import psycopg

        try:
            con = psycopg.connect(self.url, connect_timeout=10)
        except psycopg.Error as e:
            log.warning("PostgreSQL no responde: %s", e.__class__.__name__)
            raise ErrorCuenta("No pude conectarme a la base de cuentas. Espera un momento y vuelve a "
                              "intentarlo.") from None
        if self.url not in Cuentas._esquema_listo:
            with con.transaction():
                con.execute(ESQUEMA)
            Cuentas._esquema_listo.add(self.url)
        return con

    def _consulta(self, funcion):
        """Corre funcion(con) en una transacción y traduce cualquier falla de la base a un mensaje amable."""
        import psycopg

        con = self._conectar()
        try:
            with con.transaction():
                return funcion(con)
        except psycopg.Error as e:
            log.warning("Error de PostgreSQL: %s", e.__class__.__name__)
            raise ErrorCuenta("La base de cuentas tuvo un problema. Espera un momento y vuelve a intentarlo.") from None
        finally:
            con.close()

    # ---------- cifrado de la clave de Groq ----------
    def _cifrar(self, clave: str) -> str:
        return self._fernet.encrypt(clave.encode()).decode()

    def _descifrar(self, cifrada: str | None) -> str:
        if not cifrada:
            return ""
        from cryptography.fernet import InvalidToken

        try:
            return self._fernet.decrypt(cifrada.encode()).decode()
        except InvalidToken:  # cambiaron WIKIIA_SECRETO: la persona tendrá que pegarla de nuevo
            return ""

    def _usuario(self, fila) -> Usuario:
        """fila = (correo, nombre, groq_cifrada, …)"""
        return Usuario(correo=fila[0], nombre=fila[1], groq_clave=self._descifrar(fila[2]),
                       root=es_root(fila[0], self.roots))

    @staticmethod
    def _nueva_sesion(con, correo: str) -> str:
        token = secrets.token_urlsafe(32)
        con.execute("DELETE FROM wikiia_sesiones WHERE expira < now()")
        con.execute("INSERT INTO wikiia_sesiones(token_hash, correo, expira) "
                    "VALUES (%s, %s, now() + make_interval(days => %s))", (_huella_token(token), correo, DIAS_SESION))
        return token

    # ---------- registro e ingreso ----------
    def registrar(self, nombre: str, correo: str, contrasena: str, confirmar: str) -> tuple[Usuario, str]:
        """Crea la cuenta y deja la sesión abierta. Devuelve (usuario, token para la cookie)."""
        import psycopg

        nombre = " ".join((nombre or "").split())
        correo = normalizar_correo(correo)
        if len(nombre) < 2:
            raise ErrorCuenta("Escribe tu nombre.")
        if len(nombre) > MAX_NOMBRE:
            raise ErrorCuenta(f"El nombre puede tener máximo {MAX_NOMBRE} caracteres.")
        if not _RE_CORREO.match(correo) or len(correo) > 254:
            raise ErrorCuenta("Escribe un correo válido, por ejemplo nombre@gmail.com.")
        if len(contrasena) < MIN_CONTRASENA:
            raise ErrorCuenta(f"La contraseña debe tener al menos {MIN_CONTRASENA} caracteres.")
        if len(contrasena) > MAX_CONTRASENA:
            raise ErrorCuenta(f"La contraseña puede tener máximo {MAX_CONTRASENA} caracteres.")
        if contrasena != confirmar:
            raise ErrorCuenta("Las dos contraseñas no coinciden. Escríbelas de nuevo.")
        huella = huella_contrasena(contrasena)

        def crear(con):
            try:
                with con.transaction():
                    fila = con.execute(
                        "INSERT INTO wikiia_usuarios(correo, nombre, clave_hash, ultimo_ingreso) "
                        "VALUES (%s, %s, %s, now()) RETURNING correo, nombre, groq_cifrada",
                        (correo, nombre, huella)).fetchone()
            except psycopg.errors.UniqueViolation:
                raise ErrorCuenta("Ya existe una cuenta con ese correo. Entra con tu contraseña en "
                                  "«Iniciar sesión».") from None
            return self._usuario(fila), self._nueva_sesion(con, fila[0])

        return self._consulta(crear)

    def entrar(self, correo: str, contrasena: str) -> tuple[Usuario, str]:
        """Revisa correo y contraseña. Devuelve (usuario, token para la cookie)."""
        correo = normalizar_correo(correo)
        if not correo or not contrasena:
            raise ErrorCuenta("Escribe tu correo y tu contraseña.")

        def ingresar(con):
            fila = con.execute(
                "SELECT correo, nombre, groq_cifrada, clave_hash, fallos, "
                "CEIL(EXTRACT(EPOCH FROM (bloqueado_hasta - now())) / 60) "
                "FROM wikiia_usuarios WHERE correo = %s FOR UPDATE", (correo,)).fetchone()
            if fila is None:
                contrasena_coincide(contrasena, _huella_falsa())  # tarda lo mismo: no revela si el correo existe
                return None
            if fila[5] is not None and fila[5] > 0:
                return ("bloqueado", int(fila[5]))
            if not contrasena_coincide(contrasena, fila[3]):
                fallos = fila[4] + 1
                if fallos >= MAX_FALLOS:
                    con.execute("UPDATE wikiia_usuarios SET fallos = 0, "
                                "bloqueado_hasta = now() + make_interval(mins => %s) WHERE correo = %s",
                                (MINUTOS_BLOQUEO, fila[0]))
                    return ("bloqueado", MINUTOS_BLOQUEO)
                con.execute("UPDATE wikiia_usuarios SET fallos = %s WHERE correo = %s", (fallos, fila[0]))
                return None
            con.execute("UPDATE wikiia_usuarios SET fallos = 0, bloqueado_hasta = NULL, ultimo_ingreso = now() "
                        "WHERE correo = %s", (fila[0],))
            return self._usuario(fila), self._nueva_sesion(con, fila[0])

        resultado = self._consulta(ingresar)
        if resultado is None:
            raise ErrorCuenta("Correo o contraseña incorrectos.")
        if resultado[0] == "bloqueado":
            raise ErrorCuenta(f"Por seguridad, esta cuenta quedó en pausa por demasiados intentos. "
                              f"Vuelve a intentarlo en {resultado[1]} min.")
        return resultado

    def por_sesion(self, token: str) -> Usuario | None:
        """El usuario dueño de la cookie, o None si venció o ya cerró sesión."""
        if not isinstance(token, str) or not token or len(token) > 100:
            return None
        fila = self._consulta(lambda con: con.execute(
            "SELECT u.correo, u.nombre, u.groq_cifrada FROM wikiia_sesiones s "
            "JOIN wikiia_usuarios u ON u.correo = s.correo "
            "WHERE s.token_hash = %s AND s.expira > now()", (_huella_token(token),)).fetchone())
        return self._usuario(fila) if fila else None

    def salir(self, token: str) -> None:
        if token:
            self._consulta(lambda con: con.execute(
                "DELETE FROM wikiia_sesiones WHERE token_hash = %s", (_huella_token(token),)))

    def cambiar_contrasena(self, correo: str, actual: str, nueva: str, repetida: str, token_actual: str = "") -> None:
        """Cambia la contraseña y cierra las demás sesiones abiertas (otros navegadores); la actual sigue."""
        if len(nueva) < MIN_CONTRASENA:
            raise ErrorCuenta(f"La contraseña nueva debe tener al menos {MIN_CONTRASENA} caracteres.")
        if len(nueva) > MAX_CONTRASENA:
            raise ErrorCuenta(f"La contraseña puede tener máximo {MAX_CONTRASENA} caracteres.")
        if nueva != repetida:
            raise ErrorCuenta("Las dos contraseñas nuevas no coinciden. Escríbelas de nuevo.")
        if nueva == actual:
            raise ErrorCuenta("La contraseña nueva debe ser distinta de la actual.")
        huella_nueva = huella_contrasena(nueva)

        def cambiar(con):
            fila = con.execute("SELECT clave_hash FROM wikiia_usuarios WHERE correo = %s FOR UPDATE",
                               (normalizar_correo(correo),)).fetchone()
            if fila is None or not contrasena_coincide(actual, fila[0]):
                return False
            con.execute("UPDATE wikiia_usuarios SET clave_hash = %s, fallos = 0, bloqueado_hasta = NULL "
                        "WHERE correo = %s", (huella_nueva, normalizar_correo(correo)))
            con.execute("DELETE FROM wikiia_sesiones WHERE correo = %s AND token_hash <> %s",
                        (normalizar_correo(correo), _huella_token(token_actual) if token_actual else ""))
            return True

        if not self._consulta(cambiar):
            raise ErrorCuenta("La contraseña actual no es correcta.")

    # ---------- administración (root) ----------
    def listar_usuarios(self) -> list[dict]:
        """Todos los usuarios, sin contraseñas ni claves: solo si guardaron una clave de Groq."""
        filas = self._consulta(lambda con: con.execute(
            "SELECT u.correo, u.nombre, u.creado, u.ultimo_ingreso, u.groq_cifrada IS NOT NULL, "
            "COALESCE(u.bloqueado_hasta > now(), false), "
            "(SELECT count(*) FROM wikiia_sesiones s WHERE s.correo = u.correo AND s.expira > now()) "
            "FROM wikiia_usuarios u ORDER BY u.creado DESC").fetchall())
        return [{"correo": f[0], "nombre": f[1], "creado": f[2], "ultimo_ingreso": f[3], "tiene_groq": f[4],
                 "bloqueado": f[5], "sesiones": f[6], "root": es_root(f[0], self.roots)} for f in filas]

    # ---------- clave de Groq ----------
    def guardar_clave(self, correo: str, clave: str) -> None:
        self._consulta(lambda con: con.execute(
            "UPDATE wikiia_usuarios SET groq_cifrada = %s WHERE correo = %s", (self._cifrar(clave), correo)))

    def borrar_clave(self, correo: str) -> None:
        self._consulta(lambda con: con.execute(
            "UPDATE wikiia_usuarios SET groq_cifrada = NULL WHERE correo = %s", (correo,)))

    def borrar_cuenta(self, correo: str) -> None:
        """Borra la cuenta, su clave y todas sus sesiones (la llave foránea las borra en cascada)."""
        self._consulta(lambda con: con.execute("DELETE FROM wikiia_usuarios WHERE correo = %s", (correo,)))
