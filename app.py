"""WikiIA — interfaz web local.  Ejecutar con:  ./iniciar.sh   (o  .venv/bin/streamlit run app.py)"""
from __future__ import annotations

import base64
import html
import json
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import altair as alt
import pandas as pd
import requests
import streamlit as st

from wikiia import db
from wikiia.actualizador import ejecutar
from wikiia.alertas import enviar as enviar_alerta, texto_precio
from wikiia.calendario import enlaces_suscripcion
from wikiia.chat import SALUDO, ClaveInvalida, ErrorChat, responder, separar_opciones, validar_clave
from wikiia.config import DB_RUTA, cargar_config, es_publico, perfil_equipo
from wikiia.heuristicas import (
    ETIQUETA_ACCESO, ETIQUETA_POTENCIA, ETIQUETAS_CAP, ICONO_CAP,
    enlace_principal, estrellas, notas_importantes, num, requisitos_local, texto, veredicto_texto,
)
from wikiia.investigador import investigar_modelo, pendientes
from wikiia.motores import comando_descarga, es_difusion, motores, obtener_descargas, requisitos
from wikiia.llm import ClienteLLM, ErrorLLM

st.set_page_config(page_title="WikiIA", page_icon="🌐", layout="wide")

# Streamlit declara la página en inglés aunque todo está en español. La marcamos como español y
# "no traducir": así ni el traductor ni extensiones de traducción reescriben el HTML que maneja React.
st.html(
    """<script>
    (function () {
      var doc = document;
      try { if (window.parent && window.parent.document) doc = window.parent.document; } catch (e) {}
      var raiz = doc.documentElement;
      raiz.setAttribute("lang", "es");
      raiz.setAttribute("translate", "no");
      raiz.classList.add("notranslate");
      if (!doc.querySelector('meta[name="google"]')) {
        var meta = doc.createElement("meta");
        meta.name = "google"; meta.content = "notranslate";
        doc.head.appendChild(meta);
      }
    })();
    </script>""",
    unsafe_allow_javascript=True,
)

# Público (Streamlit Cloud): solo lectura. Ahí no hay Ollama ni .env, la RAM es la del servidor y lo que se
# escriba se borra al reiniciar; los datos se actualizan en tu PC y se suben con git push.
PUBLICO = es_publico()
CAPS = list(ETIQUETAS_CAP)
# Paleta de referencia validada (dataviz): un solo azul para magnitudes, 3 primeros tonos categóricos para acceso.
AZUL = "#2a78d6"
RAMPA_AZUL = ["#cde2fb", "#0d366b"]
COLORES_ACCESO = {"☁️ API / nube": "#2a78d6", "🖥️ Local": "#eb6834", "🖥️ Local + ☁️ API": "#1baf7a"}
ORDENES = {
    # A igual potencia, primero las fichas investigadas (más confiables) y luego las más recientes.
    "⭐ Más potentes": (["potencia", "investigado", "fecha_dt"], [False, False, False]),
    "🆕 Más recientes": (["fecha_dt", "potencia"], [False, False]),
    "🏢 Empresa": (["empresa", "potencia"], [True, False]),
    "💲 Más baratos (API)": (["precio_salida", "potencia"], [True, False]),
}
COLUMNAS_TABLA = {
    "pais_label": st.column_config.TextColumn("País"),
    "empresa": st.column_config.TextColumn("Empresa"),
    # El nombre ES el hipervínculo: la URL lleva el nombre al final (#nombre) y display_text muestra solo esa parte.
    "modelo": st.column_config.LinkColumn(
        "Modelo", width="medium", display_text=r".*#(.+)$",
        help="Toca el nombre para abrir su página: descarga en Hugging Face (modelos locales) "
             "o su web / chat oficial (modelos en la nube)"),
    "estrellas": st.column_config.TextColumn("Potencia", help="★★★★★ Frontera · ★★★★ Alto · ★★★ Medio · ★★ Ligero · ★ Básico"),
    "sirve_para": st.column_config.TextColumn(
        "Sirve para",
        help="💻 código · 🔬 investigación · 💬 chat · 👁️ visión · 🎨 imágenes · 🎬 video · 🎙️ voz/audio · 🦾 agentes"),
    "acceso_label": st.column_config.TextColumn("Cómo se usa"),
    "precio": st.column_config.TextColumn("Precio USD / 1M tokens", help="Entrada / salida, según OpenRouter"),
    "fecha_dt": st.column_config.DateColumn("Publicado", format="YYYY-MM-DD"),
}


# ───────────────────────────── datos ─────────────────────────────
def marca_db() -> tuple:
    rutas = [DB_RUTA, DB_RUTA.with_name(DB_RUTA.name + "-wal")]
    return tuple(r.stat().st_mtime if r.exists() else 0 for r in rutas)


@st.cache_data(show_spinner="Cargando WikiIA…")
def cargar_datos(marca: tuple):  # 'marca' cambia cuando la base cambia → recarga automática
    con = db.conectar()
    modelos = pd.read_sql_query(
        "SELECT m.*, e.nombre AS empresa, e.web AS empresa_web, e.chat_url, e.api_url, "
        "COALESCE(e.pais,'XX') AS pais_codigo, "
        "p.nombre AS pais, p.bandera, p.nota AS nota_pais FROM modelos m "
        "LEFT JOIN empresas e ON e.id=m.empresa_id LEFT JOIN paises p ON p.codigo=COALESCE(e.pais,'XX')", con)
    empresas = pd.read_sql_query(
        "SELECT e.*, p.nombre AS pais_nombre, p.bandera FROM empresas e LEFT JOIN paises p ON p.codigo=e.pais", con)
    paises = pd.read_sql_query("SELECT * FROM paises ORDER BY nombre", con)
    alertas = pd.read_sql_query("SELECT * FROM alertas ORDER BY id DESC LIMIT 100", con)
    ejecuciones = pd.read_sql_query("SELECT * FROM ejecuciones ORDER BY id DESC LIMIT 30", con)
    linea_base = db.meta_get(con, "linea_base")
    con.close()

    perfil = perfil_equipo()
    modelos["pais"] = modelos["pais"].fillna("Sin clasificar")
    modelos["bandera"] = modelos["bandera"].fillna("🏳️")
    modelos["empresa"] = modelos["empresa"].fillna(modelos["empresa_id"])
    modelos["pais_label"] = modelos["bandera"] + " " + modelos["pais"]
    modelos["potencia"] = modelos["potencia"].fillna(0).astype(int)
    modelos["estrellas"] = modelos["potencia"].map(estrellas)
    modelos["acceso_label"] = modelos["acceso"].map(ETIQUETA_ACCESO).fillna("—")
    for c in CAPS:
        modelos[c] = modelos[c].fillna(0).astype(int).astype(bool)
    modelos["sirve_para"] = modelos.apply(lambda f: " ".join(ICONO_CAP[c] for c in CAPS if f[c]), axis=1)
    modelos["precio"] = modelos.apply(lambda f: texto_precio(f, corto=True), axis=1)
    modelos["enlace"] = modelos.apply(enlace_principal, axis=1)
    # Streamlit decodifica el texto visible (decodeURIComponent): se codifica para que +, % o # se vean tal cual.
    modelos["modelo"] = modelos.apply(
        lambda f: f"{f['enlace'] or 'https://www.google.com/search?q=' + quote(str(f['nombre']))}"
                  f"#{quote(str(f['nombre']), safe='')}", axis=1)
    modelos["fecha_dt"] = pd.to_datetime(modelos["fecha"], errors="coerce")
    modelos["mi_pc"] = modelos.apply(lambda f: requisitos_local(f, perfil)[1], axis=1)
    return modelos, empresas, paises, alertas, ejecuciones, linea_base


@st.cache_data(ttl=120, show_spinner=False)
def estado_llm() -> dict:
    return ClienteLLM(cargar_config()).estado()


def refrescar():
    st.cache_data.clear()
    st.rerun()


def ordenar(df: pd.DataFrame, orden: str) -> pd.DataFrame:
    columnas, ascendente = ORDENES[orden]
    return df.sort_values(columnas, ascending=ascendente, na_position="last")


def tabla(df: pd.DataFrame, clave: str, columnas: list[str], alto: int | None = None):
    """Tabla con selección de fila. Devuelve la fila elegida o None."""
    if df.empty:
        st.caption("No hay modelos con estos filtros.")
        return None
    alto = alto or min(36 * (len(df) + 1) + 4, 460)
    evento = st.dataframe(
        df, column_order=columnas, column_config=COLUMNAS_TABLA, hide_index=True,
        on_select="rerun", selection_mode="single-row", key=clave, height=alto,
    )
    filas = evento.selection.rows if evento and evento.selection else []
    if filas and filas[0] < len(df):
        return df.iloc[filas[0]]
    return None


# ───────────────────────────── ficha ─────────────────────────────
def mostrar_ficha(f: pd.Series, clave: str) -> None:
    perfil = perfil_equipo()
    with st.container(border=True):
        izq, der = st.columns([4, 1])
        izq.subheader(f"{f['bandera']} {f['nombre']}")
        fecha = f["fecha_dt"].date().isoformat() if pd.notna(f["fecha_dt"]) else "fecha desconocida"
        izq.caption(f"**{f['empresa']}** · {f['pais']} · publicado {fecha} · fuente: {texto(f['origen'])}")
        with der:
            if f["verificado"]:
                st.badge("Verificada" if PUBLICO else "Verificada por ti", icon="✔️", color="green")
            elif f["investigado"]:
                st.badge("Ficha hecha por IA", icon="🧠", color="blue")
            else:
                st.badge("Sin investigar", icon="⏳", color="gray")
            if texto(f["retirado"]):
                st.badge(f"Ya no está en OpenRouter ({f['retirado']})", icon="⚠️", color="orange")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric(f"Potencia · {ETIQUETA_POTENCIA.get(f['potencia'], '—')}", f["estrellas"],
                  help=f"{texto(f['potencia_razon'])}  \nFuente: {texto(f['potencia_fuente']) or '—'}")
        c2.metric("Cómo se usa", f["acceso_label"])
        # "\$": sin escapar, Streamlit toma "$4 / $20" como una fórmula matemática.
        c3.metric("Precio", texto_precio(f, corto=True).replace("$", r"\$"),
                  help=texto_precio(f).replace("$", r"\$"))
        contexto = num(f["contexto"])
        c4.metric("Memoria de conversación", f"{contexto / 1000:,.0f}K tokens" if contexto else "—",
                  help="Cuánto texto puede leer de una vez (ventana de contexto)")

        if texto(f["resumen"]):
            st.markdown(f"**¿Para qué sirve?** {f['resumen']}")
            if texto(f["mejor_para"]):
                st.markdown(f"**Mejor para:** {f['mejor_para']}")
        elif PUBLICO:
            st.info("⏳ Esta ficha todavía no se ha investigado con IA; llegará en una próxima actualización.")
        else:
            st.info("⏳ Todavía no se investigó con IA. Pulsa **Investigar con IA** abajo o espera la próxima actualización.")
            if texto(f["descripcion"]):
                st.caption(f["descripcion"][:600])

        st.markdown("**¿Para qué sirve? (capacidades)**")
        columnas = st.columns(4)
        for i, c in enumerate(CAPS):
            columnas[i % 4].markdown(f"{'✅' if f[c] else '❌'} {ETIQUETAS_CAP[c]}")

        tecnico = []
        params, activos = num(f["parametros_b"]), num(f["activos_b"])
        if params:
            tecnico.append(f"**Tamaño:** {params:,.1f} mil millones de parámetros"
                           + (f" ({activos:g}B activos por palabra, MoE)" if activos else ""))
        if texto(f["licencia"]):
            tecnico.append(f"**Licencia:** {f['licencia']}")
        req, veredicto = requisitos_local(f, perfil)
        if req:
            tecnico.append(f"**Para correrlo local:** {req}")
        if req and not PUBLICO:  # en la nube "tu PC" sería el servidor de Streamlit
            tecnico.append(f"**¿En tu PC ({perfil['ram_gb']:.0f} GB RAM"
                           f"{', GPU ' + str(perfil['vram_gb']) + ' GB' if perfil['vram_gb'] else ', sin GPU'})?** "
                           f"{veredicto_texto(veredicto, perfil)}")
        extra = db.cargar_json(f["extra"], {})
        if extra.get("corte_conocimiento"):
            tecnico.append(f"**Conocimiento hasta:** {extra['corte_conocimiento']}")
        if extra.get("expira"):
            tecnico.append(f"**⚠️ Será retirado:** {extra['expira']}")
        if tecnico:
            st.markdown("**Datos técnicos**  \n" + "  \n".join(tecnico))

        notas = notas_importantes(f, texto(f["nota_pais"]), db.cargar_json(f["notas_ia"], []))
        if notas:
            st.warning("**⚠️ Importante tener en cuenta**\n\n" + "\n".join(f"- {n}" for n in notas))

        if f["acceso"] in ("local", "ambos") and texto(f["hf_id"]):
            seccion_local(f, clave)
        seccion_web(f, clave)

        if PUBLICO:
            return
        a, b = st.columns([1, 3])
        if a.button("🧠 Investigar con IA", key=f"inv_{clave}", help="Groq → Ollama de respaldo"):
            with st.spinner("Leyendo la documentación y preguntando a la IA…"):
                con = db.conectar()
                try:
                    proveedor = investigar_modelo(con, ClienteLLM(cargar_config()), f["id"])
                    st.toast(f"Ficha actualizada con {proveedor}", icon="🧠")
                except ErrorLLM as e:
                    st.error(f"No se pudo investigar: {e}")
                    con.close()
                    st.stop()
                con.close()
            refrescar()
        with b.expander("✏️ Corregir o verificar esta ficha"):
            editar_ficha(f, clave)


def botones_enlace(botones: list[tuple[str, str]], clave: str) -> None:
    if not botones:
        return
    columnas = st.columns(min(len(botones), 3))
    for i, (etiqueta, url) in enumerate(botones):
        columnas[i % len(columnas)].link_button(etiqueta, url, width="stretch", key=f"lb_{clave}_{i}")


def seccion_local(f: pd.Series, clave: str) -> None:
    """Descargas, motores para ejecutarlo y requisitos mínimos."""
    st.markdown("#### 📥 Descargar y ejecutar en tu PC")
    con = db.conectar()
    with st.spinner("Buscando versiones descargables en Hugging Face…"):
        descargas = obtener_descargas(con, f)
    con.close()
    if texto(f["gated"]) not in ("", "no"):
        st.info("🔒 Este modelo pide aceptar su licencia: entra a su página de Hugging Face, acéptala e "
                "inicia sesión en tu PC con `hf auth login` antes de descargarlo.")

    lista = motores(f, descargas)
    if lista:
        st.caption("Elige un motor. Copia el comando con el botón 📋 que aparece a la derecha del recuadro.")
        pestanas = st.tabs([f"{m['icono']} {m['motor']}" for m in lista], key=f"motores_{clave}")
        for i, (pestana, m) in enumerate(zip(pestanas, lista)):
            with pestana:
                aviso = "⚠️ Necesita GPU (NVIDIA o AMD)." if m["gpu"] else "✅ Funciona también sin GPU."
                st.markdown(f"{m['para']}  \n{aviso}")
                st.code(m["comando"], language="python" if "import " in m["comando"] else "bash")
                st.link_button(f"🌐 Ir a {m['motor']}", m["web"], key=f"motor_{clave}_{i}")

    st.markdown("**🖥️ Requisitos mínimos**")
    tabla_req = requisitos(f, descargas)
    if tabla_req:
        st.dataframe(pd.DataFrame(tabla_req), hide_index=True, width="stretch")
        notas = ["Memoria = RAM si lo corres sin GPU, o VRAM de la GPU. Es para una conversación normal; "
                 "con documentos muy largos necesitas más."]
        if num(f["activos_b"]):
            notas.append(f"Es un modelo MoE: aunque pesa mucho, solo usa {num(f['activos_b']):g}B parámetros por "
                         "palabra, así que en CPU responde más rápido de lo que su tamaño sugiere.")
        st.caption("  \n".join(notas))
    else:
        st.caption("Hugging Face no publica el tamaño de este modelo; revisa su página.")

    hf_id = f["hf_id"]
    with st.expander("⬇️ Descargar los archivos"):
        imagen_video = es_difusion(f)
        st.markdown("**Archivos originales** (para "
                    + ("ComfyUI y Diffusers" if imagen_video else "vLLM, SGLang y Transformers") + "):")
        st.code(comando_descarga(f), language="bash")
        gguf = descargas.get("gguf") or []
        if gguf:
            st.markdown("**Versiones GGUF** (para "
                        + ("ComfyUI con el nodo GGUF" if imagen_video else "Ollama, LM Studio, llama.cpp y Jan") + "):")
            for g in gguf:
                st.markdown(f"- [{g['repo']}](https://huggingface.co/{g['repo']}) · {g['descargas']:,} descargas")
            cuants = descargas.get("cuantizaciones") or {}
            if cuants:
                st.caption("Tamaños de " + gguf[0]["repo"] + ": " + " · ".join(
                    f"{k} {v:,.1f} GB" for k, v in sorted(cuants.items(), key=lambda x: x[1])))
        mlx = descargas.get("mlx") or []
        if mlx:
            st.markdown("**Versiones MLX** (Mac): " + " · ".join(
                f"[{m['repo']}](https://huggingface.co/{m['repo']})" for m in mlx))
        botones_enlace([("🤗 Ver en Hugging Face", f"https://huggingface.co/{hf_id}"),
                        ("📂 Archivos", f"https://huggingface.co/{hf_id}/tree/main")], f"hf_{clave}")


def seccion_web(f: pd.Series, clave: str) -> None:
    """Dónde usarlo en la web: chat oficial, web del modelo, API y precios."""
    enlaces = db.cargar_json(f["enlaces"], {})
    botones = []
    if texto(f["chat_url"]):
        botones.append(("💬 Probarlo en su chat oficial", f["chat_url"]))
    if enlaces.get("web"):
        botones.append(("🌐 Web oficial del modelo", enlaces["web"]))
    if texto(f["api_url"]):
        botones.append(("🛠️ API y documentación", f["api_url"]))
    if enlaces.get("openrouter"):
        botones.append(("🔀 Precios y proveedores", enlaces["openrouter"]))
    if texto(f["empresa_web"]):
        botones.append((f"🏢 Web de {f['empresa']}", f["empresa_web"]))
    if botones:
        st.markdown("#### 🌐 Dónde usarlo en la web" if f["acceso"] != "local" else "#### 🌐 Enlaces")
        botones_enlace(botones, f"web_{clave}")


def editar_ficha(f: pd.Series, clave: str) -> None:
    with st.form(key=f"form_{clave}"):
        potencia = st.select_slider(
            "Potencia", options=[1, 2, 3, 4, 5], value=int(f["potencia"]) or 3,
            format_func=lambda p: f"{estrellas(p)} {ETIQUETA_POTENCIA[p]}")
        cols = st.columns(4)
        caps = {c: cols[i % 4].checkbox(ETIQUETAS_CAP[c], value=bool(f[c])) for i, c in enumerate(CAPS)}
        acceso = st.selectbox("Cómo se usa", list(ETIQUETA_ACCESO), format_func=ETIQUETA_ACCESO.get,
                              index=list(ETIQUETA_ACCESO).index(f["acceso"]) if f["acceso"] in ETIQUETA_ACCESO else 0)
        resumen = st.text_area("¿Para qué sirve?", value=texto(f["resumen"]))
        mejor = st.text_input("Mejor para", value=texto(f["mejor_para"]))
        verificado = st.checkbox("✔ Revisé esta ficha y es correcta", value=bool(f["verificado"]))
        oculto = st.checkbox("🙈 Ocultar este modelo de la wiki", value=bool(f["oculto"]))
        if st.form_submit_button("💾 Guardar", type="primary"):
            originales = {"potencia": int(f["potencia"]), "acceso": f["acceso"], "resumen": texto(f["resumen"]),
                          "mejor_para": texto(f["mejor_para"]), **{c: bool(f[c]) for c in CAPS}}
            nuevos = {"potencia": potencia, "acceso": acceso, "resumen": resumen.strip(),
                      "mejor_para": mejor.strip(), **caps}
            cambios = {k: (int(v) if isinstance(v, bool) else v) for k, v in nuevos.items() if v != originales[k]}
            if "potencia" in cambios:
                cambios.update(potencia_fuente="manual", potencia_razon="Ajustada a mano")
            con = db.conectar()
            db.actualizar_campos(con, f["id"], cambios, marcar_manual=True)
            db.actualizar_campos(con, f["id"], {"verificado": int(verificado), "oculto": int(oculto)})
            con.commit()
            con.close()
            st.toast("Ficha guardada. Tus cambios no se sobrescriben en futuras actualizaciones.", icon="💾")
            refrescar()


AYUDA_TABLA = ("☑️ **Marca la casilla** de la izquierda para ver la ficha completa del modelo (links, cómo "
               "descargarlo y en qué motores corre). Toca su **nombre** para abrir su página.")
CSS_AVISO_FICHA = """<style>
@keyframes wikiia-aparece { from { opacity: 0; transform: translateY(-10px); } to { opacity: 1; transform: none; } }
@keyframes wikiia-rebote { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(6px); } }
.wikiia-aviso-ficha { display: flex; align-items: center; gap: .6rem; padding: .7rem 1rem; margin: .5rem 0 .25rem;
  border-radius: 12px; border: 1px solid rgba(255, 75, 75, .5); font-weight: 600;
  background: linear-gradient(90deg, rgba(255, 75, 75, .18), rgba(255, 75, 75, .03));
  animation: wikiia-aparece .45s ease-out both; }
.wikiia-aviso-ficha .flecha { display: inline-block; font-size: 1.3rem; animation: wikiia-rebote .9s ease-in-out 4; }
.wikiia-aviso-ficha .nombre { font-weight: 700; }
</style>"""
# Destello alrededor de la ficha recién abierta (solo cuando cambia la selección).
CSS_DESTELLO = """<style>
@keyframes wikiia-destello { 0% { box-shadow: 0 0 0 0 rgba(255, 75, 75, .6); }
  100% { box-shadow: 0 0 0 16px rgba(255, 75, 75, 0); } }
.st-key-CAJA { border-radius: 12px; scroll-margin-top: 90px; animation: wikiia-destello 1.1s ease-out 2; }
</style>"""


def tabla_con_ficha(df: pd.DataFrame, clave: str, columnas: list[str], alto: int | None = None) -> None:
    fila = tabla(df, clave, columnas, alto)
    vista = f"ficha_vista_{clave}"
    if fila is None:
        st.session_state.pop(vista, None)
        return
    nueva = st.session_state.get(vista) != fila["id"]
    st.session_state[vista] = fila["id"]
    caja = "ficha_" + re.sub(r"[^A-Za-z0-9_-]", "_", clave)  # clase CSS st-key-<caja>
    with st.container(key=caja):
        st.html(CSS_AVISO_FICHA + f'<div class="wikiia-aviso-ficha"><span class="flecha">👇</span>'
                f'<span>Observa la ficha de <span class="nombre">{html.escape(str(fila["nombre"]))}</span></span></div>')
        if nueva:
            # Al elegir otro modelo: destello en la ficha y la pantalla baja hasta ella (sin "<" en el script:
            # DOMPurify borra los <script> que lo tengan).
            st.html(CSS_DESTELLO.replace("CAJA", caja)
                    + "<script>setTimeout(function () { var e = document.querySelector('.st-key-" + caja
                    + "'); if (e) e.scrollIntoView({ behavior: 'smooth', block: 'start' }); }, 350);</script>",
                    unsafe_allow_javascript=True)
        mostrar_ficha(fila, clave)


# ───────────────────────────── tour guiado ─────────────────────────────
# Se abre solo en la primera visita e ilumina cada parte de la pantalla (driver.js, MIT, en wikiia/static).
# Una cookie de 1 año recuerda en el navegador que ya se vio; tour.js también la revisa antes de empezar.
COOKIE_TOUR = "wikiia_tutorial_visto"
ESTATICOS = Path(__file__).resolve().parent / "wikiia" / "static"
CSS_TOUR = """
.driver-overlay { z-index: 1000001 !important; }
.driver-popover.wikiia-tour { border-radius: 14px; padding: 18px 18px 14px; max-width: 360px;
  font-family: "Source Sans Pro", "Source Sans 3", system-ui, sans-serif; box-shadow: 0 14px 44px rgba(0,0,0,.4); }
.driver-popover.wikiia-tour .driver-popover-title { font-size: 1.15rem; font-weight: 700; color: #1f2130; }
.driver-popover.wikiia-tour .driver-popover-description { font-size: .98rem; line-height: 1.45; color: #31333f; }
.driver-popover.wikiia-tour .driver-popover-progress-text { color: #808495; }
.driver-popover.wikiia-tour .driver-popover-footer button { border-radius: 8px; padding: 6px 12px;
  font-size: .9rem; text-shadow: none; }
.driver-popover.wikiia-tour .driver-popover-next-btn { background: #ff4b4b; border-color: #ff4b4b; color: #fff; }
"""


def pasos_tour() -> list[dict]:
    """(selector, título, texto) de cada paso. barra: True = necesita la barra lateral abierta."""
    def paso(el, titulo, texto_html, barra=None, lado="bottom"):
        return {"el": el, "titulo": titulo, "texto": texto_html, "barra": barra, "lado": lado}

    casillas = ("Deja marcada esta casilla para no ver los modelos que ya fueron <b>retirados</b>." if PUBLICO else
                "<b>Solo los que corren en mi PC</b>: los que caben en tu memoria. <b>Ocultar retirados</b>: quita "
                "los que ya no se pueden usar. <b>Mostrar los que oculté</b>: recupera los que escondiste.")
    return [
        paso(None, "👋 ¡Bienvenido a WikiIA!",
             "La enciclopedia de los modelos de inteligencia artificial del mundo. "
             "Te muestro en un minuto dónde está cada cosa."),
        paso(".st-key-metricas", "📊 El resumen",
             "Mira: aquí ves cuántos <b>modelos, países y empresas</b> cumplen tus filtros. "
             "Cambia solo cada vez que filtras.", barra=False),
        paso(".st-key-avisos", "🔔 Entérate de cada modelo nuevo",
             "Suscríbete aquí y cada lanzamiento te aparece como un evento en <b>tu calendario</b>.", barra=False),
        paso('[data-testid="stSidebar"]', "🔎 Los filtros",
             "Esta barra es tu buscador: todo lo que elijas aquí filtra las tablas.", barra=True, lado="right"),
        paso(".st-key-f_buscar", "Buscar",
             "Escribe parte del nombre, la empresa o un tema: <i>deepseek</i>, <i>gemma</i>, <i>video</i>, "
             "<i>código</i>…", barra=True, lado="right"),
        paso(".st-key-f_origen", "🌍 País y 🏢 Empresa",
             "Elige uno o varios. El número entre paréntesis es cuántos modelos tiene cada uno. "
             "Vacío = todos.", barra=True, lado="right"),
        paso(".st-key-f_caps", "🧩 Sirve para",
             "Toca lo que necesitas: código, chat, visión, imágenes, video, voz… "
             "Si eliges varias, salen los modelos que tienen <b>todas</b>.", barra=True, lado="right"),
        paso(".st-key-f_uso", "☁️ Cómo se usa y 💲 Costo",
             "<b>API / nube</b>: lo usas en su web, normalmente pagando por uso. "
             "<b>Local</b>: lo descargas y corre gratis en tu computador.", barra=True, lado="right"),
        paso(".st-key-f_nivel", "⭐ Potencia, fecha y orden",
             "Pide una <b>potencia mínima</b> (de ★ básico a ★★★★★ frontera), solo los <b>publicados</b> "
             "hace poco, y elige el <b>orden</b> de la lista.", barra=True, lado="right"),
        paso(".st-key-f_casillas", "☑️ Opciones", casillas, barra=True, lado="right"),
        paso('[data-testid="stTabs"] [role="tablist"]', "🗂️ Las pestañas",
             "Cambia la vista: <b>por país</b>, el <b>ranking</b> general, <b>por empresa</b>, las "
             "<b>novedades</b> del mes y las <b>estadísticas</b>.", barra=False),
        paso('[data-testid="stTabs"] [role="tab"]:nth-of-type(6)', "🧭 ¿No sabes cuál elegir?",
             "Aquí una guía con IA te pregunta <b>qué quieres lograr</b>, si lo quieres <b>en tu computador o en "
             "la nube</b> y con qué equipo cuentas, y te <b>recomienda el modelo ideal</b>. Funciona con tu propia "
             "clave gratis de Groq.", barra=False),
        paso('[data-testid="stExpander"]', "🌍 Cada país es una sección",
             "Ábrela para ver su tabla. Ahí, toca el <b>nombre</b> de un modelo para abrir su página, "
             "o marca la <b>casilla</b> de la fila para ver su ficha completa.", barra=False),
        paso(None, "✅ ¡Listo!", "Ya sabes moverte por WikiIA. ¡A explorar!"),
    ]


def html_tour() -> str:
    driver_js = (ESTATICOS / "driverjs" / "driver.js.iife.js").read_text(encoding="utf-8")
    driver_css = (ESTATICOS / "driverjs" / "driver.css").read_text(encoding="utf-8")
    tour_js = (ESTATICOS / "tour.js").read_text(encoding="utf-8")
    cfg_tour = json.dumps({"cookie": COOKIE_TOUR, "pasos": pasos_tour()}, ensure_ascii=False)
    # Los estilos van al <head>: si el visitante usa un filtro iluminado, Streamlit redibuja y este st.html desaparece.
    # .call(window): driver.js se registra en "this"; así queda en window.driver aunque se ejecute dentro de una función.
    codigo = (f"var estilo = document.createElement('style'); estilo.textContent = {json.dumps(driver_css + CSS_TOUR)};"
              f"document.head.appendChild(estilo);\n(function () {{ {driver_js} }}).call(window);\n{tour_js}\n"
              f"window.__wikiiaIniciarTour({cfg_tour});")
    # Streamlit limpia st.html con DOMPurify, que borra todo <script> cuyo texto tenga "<b", "</"… (y el código
    # minificado tiene "a<b"). Por eso va en base64 y un cargador sin "<" lo decodifica (UTF-8) y lo ejecuta.
    empaquetado = base64.b64encode(codigo.encode("utf-8")).decode("ascii")
    cargador = ("(function () { var bytes = Uint8Array.from(atob('" + empaquetado + "'), function (c) "
                "{ return c.charCodeAt(0); }); var s = document.createElement('script'); "
                "s.text = new TextDecoder().decode(bytes); document.head.appendChild(s); })();")
    return f"<script>{cargador}</script>"


# ───────────────────────────── avisos ─────────────────────────────
@st.cache_data(ttl=600, show_spinner=False)
def calendario_publicado(url: str) -> bool:
    """El botón solo aparece cuando el workflow ya publicó el .ics en GitHub Pages (se revisa cada 10 min)."""
    try:
        return requests.head(url, timeout=5, allow_redirects=True).ok
    except requests.RequestException:
        return False


def caja_avisos() -> None:
    if not calendario_publicado(cfg.calendario_url):
        return
    urls = enlaces_suscripcion(cfg.calendario_url)
    with st.container(border=True, key="avisos"):
        texto_col, botones_col = st.columns([3, 2], vertical_alignment="center")
        texto_col.markdown("🔔 **¿Quieres enterarte de cada modelo nuevo?**  \n"
                           "Suscríbete y cada lanzamiento te aparece en tu calendario.")
        with botones_col:
            botones = st.columns(3 if cfg.telegram_canal_url else 2)
            botones[0].link_button("📅 Google Calendar", urls["google"], width="stretch")
            with botones[1].popover("📋 Otro calendario", width="stretch"):
                st.markdown("Copia este enlace y agrégalo como **calendario por URL**:")
                st.code(urls["ics"], language=None)
                st.caption("iPhone: Ajustes › Calendario › Cuentas › Añadir cuenta › Otra › Añadir calendario "
                           "suscrito. Outlook: Agregar calendario › Suscribirse desde la web.")
            if cfg.telegram_canal_url:
                botones[2].link_button("📱 Telegram", cfg.telegram_canal_url, width="stretch")


# ───────────────────────────── app ─────────────────────────────
modelos, empresas, paises, alertas, ejecuciones, linea_base = cargar_datos(marca_db())
cfg = cargar_config()

if modelos.empty:
    st.title("🌐 WikiIA")
    if PUBLICO:
        st.info("WikiIA se está actualizando. Vuelve en unos minutos.")
        st.stop()
    st.info("La base está vacía. Carga los modelos por primera vez (tarda menos de un minuto).")
    if st.button("🚀 Cargar modelos ahora", type="primary"):
        with st.status("Cargando modelos…", expanded=True) as estado:
            linea = st.empty()
            ejecutar(cfg, investigar=0, progreso=linea.text)
            estado.update(label="¡Listo!", state="complete")
        refrescar()
    st.stop()

# ---------- barra lateral: filtros ----------
# Cada grupo tiene key: Streamlit le pone la clase CSS st-key-<key> y el tour guiado la ilumina.
with st.sidebar:
    st.header("🔎 Filtros")
    buscar = st.text_input("Buscar", placeholder="deepseek, gemma, video, código…", key="f_buscar")
    with st.container(key="f_origen"):
        conteo_paises = modelos[modelos["oculto"] == 0].groupby("pais_label").size().sort_values(ascending=False)
        sel_paises = st.multiselect("País", list(conteo_paises.index), placeholder="Todos los países",
                                    format_func=lambda p: f"{p} ({conteo_paises[p]})")
        base_emp = modelos[modelos["pais_label"].isin(sel_paises)] if sel_paises else modelos
        conteo_emp = base_emp.groupby("empresa").size().sort_values(ascending=False)
        sel_empresas = st.multiselect("Empresa", list(conteo_emp.index), placeholder="Todas las empresas",
                                      format_func=lambda e: f"{e} ({conteo_emp[e]})")
    sel_caps = st.pills("Sirve para (todas las elegidas)", CAPS, selection_mode="multi",
                        format_func=ETIQUETAS_CAP.get, key="f_caps")
    with st.container(key="f_uso"):
        sel_acceso = st.pills("Cómo se usa", list(ETIQUETA_ACCESO), selection_mode="multi",
                              format_func=ETIQUETA_ACCESO.get)
        costo = st.segmented_control("Costo", ["Todos", "Gratis / local", "De pago"], default="Todos")
    with st.container(key="f_nivel"):
        pot_min = st.select_slider("Potencia mínima", options=[1, 2, 3, 4, 5], value=1,
                                   format_func=lambda p: f"{estrellas(p)} {ETIQUETA_POTENCIA[p]}")
        periodo = st.selectbox("Publicado", ["Cualquier fecha", "Últimos 7 días", "Últimos 30 días",
                                             "Últimos 90 días", "Último año"])
        orden = st.selectbox("Ordenar por", list(ORDENES))
    with st.container(key="f_casillas"):
        solo_mi_pc = False if PUBLICO else st.checkbox("🖥️ Solo los que corren en mi PC")
        ocultar_retirados = st.checkbox("Ocultar los retirados de la API", value=True)
        ver_ocultos = False if PUBLICO else st.checkbox("Mostrar los que oculté", value=False)

    st.divider()
    if not PUBLICO:
        llm = estado_llm()
        st.caption(f"**Motor de IA:** Groq {'✅' if llm['groq'] else '❌'} · Ollama {'✅' if llm['ollama'] else '❌'}")
    if not ejecuciones.empty:
        st.caption(f"**Última actualización:** {str(ejecuciones.iloc[0]['fin'])[:16].replace('T', ' ')}")

df = modelos.copy()
if not ver_ocultos:
    df = df[df["oculto"] == 0]
if ocultar_retirados:
    df = df[~(df["retirado"].notna() & (df["acceso"] == "api"))]
if buscar:
    q = buscar.strip()
    df = df[df["nombre"].str.contains(q, case=False, regex=False, na=False)
            | df["empresa"].str.contains(q, case=False, regex=False, na=False)
            | df["id"].str.contains(q, case=False, regex=False, na=False)
            | df["resumen"].str.contains(q, case=False, regex=False, na=False)]
if sel_paises:
    df = df[df["pais_label"].isin(sel_paises)]
if sel_empresas:
    df = df[df["empresa"].isin(sel_empresas)]
for c in sel_caps or []:
    df = df[df[c]]
if sel_acceso:
    df = df[df["acceso"].isin(sel_acceso)]
if costo == "Gratis / local":
    df = df[(df["gratis"] == 1) | df["acceso"].isin(["local", "ambos"])]
elif costo == "De pago":
    df = df[df["precio_salida"].fillna(0) > 0]
df = df[df["potencia"] >= pot_min]
dias = {"Últimos 7 días": 7, "Últimos 30 días": 30, "Últimos 90 días": 90, "Último año": 365}.get(periodo)
if dias:
    df = df[df["fecha_dt"] >= pd.Timestamp(datetime.now() - timedelta(days=dias))]
if solo_mi_pc:
    df = df[df["mi_pc"] == "si"]
df = ordenar(df, orden)

# ---------- encabezado ----------
st.title("🌐 WikiIA")
st.caption("La enciclopedia de los modelos de inteligencia artificial del mundo · se actualiza sola desde "
           "OpenRouter y Hugging Face · las fichas las investiga Groq / Ollama")
hace_7 = pd.Timestamp(datetime.now() - timedelta(days=7))
m1, m2, m3, m4, m5 = st.container(key="metricas").columns(5)
m1.metric("Modelos", f"{len(df):,}", help=f"{len(modelos):,} en total en la base")
m2.metric("Países", df["pais_codigo"].nunique())
m3.metric("Empresas", df["empresa_id"].nunique())
m4.metric("Publicados esta semana", int((df["fecha_dt"] >= hace_7).sum()))
m5.metric("Fichas investigadas", f"{df['investigado'].mean() * 100:.0f}%" if len(df) else "—",
          help="Porcentaje de modelos con ficha hecha por IA o verificada")
caja_avisos()
# Las alertas "sin leer" son del dueño de la app: en la versión pública no se muestran como pendientes.
sin_leer = int((alertas["leida"] == 0).sum()) if not alertas.empty and not PUBLICO else 0
if sin_leer:
    st.info(f"🔔 Tienes **{sin_leer}** alertas sin leer: revisa la pestaña **Novedades**.")

nombres_tabs = ["🌍 Por país", "🏆 Ranking", "🏢 Por empresa", f"🔔 Novedades{f' ({sin_leer})' if sin_leer else ''}",
                "📊 Estadísticas", "🧭 ¿Qué modelo uso?"] + ([] if PUBLICO else ["⚙️ Configuración"])
tab_pais, tab_rank, tab_emp, tab_nov, tab_est, tab_chat, *tab_admin = st.tabs(nombres_tabs)

# ---------- por país ----------
with tab_pais:
    st.caption(AYUDA_TABLA)
    orden_paises = df.groupby("pais_label").size().sort_values(ascending=False)
    for pais_label, n in orden_paises.items():
        grupo = df[df["pais_label"] == pais_label]
        top = ", ".join(grupo.sort_values(["potencia", "fecha_dt"], ascending=False)["nombre"].head(3))
        # key fija: si cambian los números o destacados, React actualiza el texto en vez de rehacer la sección.
        with st.expander(f"**{pais_label}** — {n} modelos · {grupo['empresa_id'].nunique()} empresas · "
                         f"destacados: {top}", expanded=len(orden_paises) == 1,
                         key=f"exp_pais_{grupo['pais_codigo'].iloc[0]}"):
            nota = texto(grupo["nota_pais"].iloc[0])
            if nota:
                st.warning(nota, icon="⚠️")
            st.markdown(" · ".join(f"**{e}** ({c})" for e, c in grupo["empresa"].value_counts().items()))
            tabla_con_ficha(grupo, f"pais_{grupo['pais_codigo'].iloc[0]}",
                            ["modelo", "estrellas", "sirve_para", "acceso_label", "precio", "fecha_dt"])

# ---------- ranking ----------
with tab_rank:
    st.caption(f"Todos los modelos que cumplen los filtros, ordenados por: **{orden}**. " + AYUDA_TABLA)
    tabla_con_ficha(df, "ranking",
                    ["pais_label", "modelo", "estrellas", "sirve_para", "acceso_label", "precio",
                     "fecha_dt"], alto=560)

# ---------- por empresa ----------
with tab_emp:
    conteo = df.groupby("empresa_id").size().sort_values(ascending=False)
    if conteo.empty:
        st.caption("No hay modelos con estos filtros.")
    else:
        info_emp = empresas.set_index("id")
        empresa_id = st.selectbox(
            "Empresa", list(conteo.index),
            format_func=lambda e: f"{info_emp.loc[e, 'bandera'] if e in info_emp.index else '🏳️'} "
                                  f"{info_emp.loc[e, 'nombre'] if e in info_emp.index else e} ({conteo[e]})")
        grupo = df[df["empresa_id"] == empresa_id]
        fila_emp = grupo.iloc[0]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("País", f"{fila_emp['bandera']} {fila_emp['pais']}")
        c2.metric("Modelos", len(grupo))
        c3.metric("Más potente", estrellas(grupo["potencia"].max()))
        c4.metric("Con pesos abiertos", f"{grupo['acceso'].isin(['local', 'ambos']).mean() * 100:.0f}%")
        st.markdown("**Qué cubre esta empresa:** " + " · ".join(
            f"{ETIQUETAS_CAP[c]} ({int(grupo[c].sum())})" for c in CAPS if grupo[c].any()))
        if texto(fila_emp["empresa_web"]):
            st.link_button(f"🔗 Web de {fila_emp['empresa']}", fila_emp["empresa_web"])
        tabla_con_ficha(grupo, f"empresa_{empresa_id}",
                        ["modelo", "estrellas", "sirve_para", "acceso_label", "precio", "fecha_dt"])

# ---------- novedades ----------
with tab_nov:
    izq, der = st.columns([3, 2])
    with izq:
        st.subheader("🆕 Publicados en los últimos 30 días")
        recientes = df[df["fecha_dt"] >= pd.Timestamp(datetime.now() - timedelta(days=30))]
        tabla_con_ficha(recientes.sort_values(["fecha_dt", "potencia"], ascending=False), "recientes",
                        ["fecha_dt", "pais_label", "modelo", "estrellas", "sirve_para", "acceso_label"])
    with der:
        st.subheader("🔔 Alertas")
        if alertas.empty:
            st.caption("Todavía no hay alertas. Aparecerán aquí cada vez que se detecte un modelo nuevo.")
        else:
            if sin_leer and st.button("Marcar todas como leídas"):
                con = db.conectar()
                con.execute("UPDATE alertas SET leida=1")
                con.commit()
                con.close()
                refrescar()
            for _, a in alertas.head(30).iterrows():
                with st.container(border=True):
                    st.markdown(f"{'🔵 ' if not a['leida'] else ''}**{a['titulo']}**")
                    st.caption(f"{str(a['fecha'])[:16].replace('T', ' ')} · enviada por: {a['canales'] or 'solo app'}")
                    st.text(a["mensaje"])

# ---------- estadísticas ----------
with tab_est:
    if df.empty:
        st.caption("No hay modelos con estos filtros.")
    else:
        orden_y = list(df.groupby("pais_label").size().sort_values(ascending=False).index)
        alto = max(180, 28 * len(orden_y))
        g1, g2 = st.columns(2)
        with g1:
            st.markdown("**Modelos por país**")
            por_pais = df.groupby("pais_label").size().reset_index(name="modelos")
            st.altair_chart(
                alt.Chart(por_pais).mark_bar(color=AZUL, cornerRadiusEnd=4, size=16).encode(
                    x=alt.X("modelos:Q", title="Modelos"),
                    y=alt.Y("pais_label:N", sort=orden_y, title=None),
                    tooltip=[alt.Tooltip("pais_label:N", title="País"), alt.Tooltip("modelos:Q", title="Modelos")],
                ).properties(height=alto),
                width="stretch")
        with g2:
            st.markdown("**Cómo se usan los modelos de cada país**")
            acceso = df.groupby(["pais_label", "acceso_label"]).size().reset_index(name="modelos")
            acceso = acceso[acceso["acceso_label"].isin(COLORES_ACCESO)]
            st.altair_chart(
                alt.Chart(acceso).mark_bar(size=16).encode(
                    x=alt.X("modelos:Q", stack="normalize", title="Proporción de sus modelos",
                            axis=alt.Axis(format="%")),
                    y=alt.Y("pais_label:N", sort=orden_y, title=None),
                    color=alt.Color("acceso_label:N", title=None,
                                    scale=alt.Scale(domain=list(COLORES_ACCESO), range=list(COLORES_ACCESO.values())),
                                    legend=alt.Legend(orient="top")),
                    tooltip=[alt.Tooltip("pais_label:N", title="País"), alt.Tooltip("acceso_label:N", title="Acceso"),
                             alt.Tooltip("modelos:Q", title="Modelos")],
                ).properties(height=alto + 60),  # +60: espacio para la leyenda sin aplastar las filas
                width="stretch")

        st.markdown("**¿Para qué sirven? — % de los modelos de cada país con cada capacidad**")
        filas = []
        for pais_label, grupo in df.groupby("pais_label"):
            for c in CAPS:
                filas.append({"pais": pais_label, "capacidad": ETIQUETAS_CAP[c],
                              "modelos": int(grupo[c].sum()), "pct": round(grupo[c].mean() * 100, 1)})
        mapa = pd.DataFrame(filas)
        st.altair_chart(
            alt.Chart(mapa).mark_rect(cornerRadius=3).encode(
                x=alt.X("capacidad:N", sort=[ETIQUETAS_CAP[c] for c in CAPS], title=None,
                        axis=alt.Axis(labelAngle=0, orient="top", labelLimit=140)),
                y=alt.Y("pais:N", sort=orden_y, title=None),
                color=alt.Color("pct:Q", title="% de sus modelos",
                                scale=alt.Scale(domain=[0, 100], range=RAMPA_AZUL)),
                tooltip=[alt.Tooltip("pais:N", title="País"), alt.Tooltip("capacidad:N", title="Capacidad"),
                         alt.Tooltip("modelos:Q", title="Modelos"),
                         alt.Tooltip("pct:Q", title="% del país", format=".0f")],
            ).properties(height=alto),
            width="stretch")
        with st.expander("Ver como tabla (número de modelos)"):
            st.dataframe(mapa.pivot(index="pais", columns="capacidad", values="modelos")
                         .reindex(orden_y)[[ETIQUETAS_CAP[c] for c in CAPS]].rename_axis(index="País", columns=None),
                         width="stretch")

        st.markdown("**Lanzamientos por mes (últimos 18 meses)**")
        desde = pd.Timestamp(datetime.now() - timedelta(days=548))
        meses = df[df["fecha_dt"] >= desde].copy()
        meses["mes"] = meses["fecha_dt"].dt.to_period("M").dt.to_timestamp()
        meses = meses.groupby("mes").size().reset_index(name="modelos")
        st.altair_chart(
            alt.Chart(meses).mark_bar(color=AZUL, cornerRadiusEnd=4).encode(
                x=alt.X("yearmonth(mes):T", title=None),
                y=alt.Y("modelos:Q", title="Modelos publicados"),
                tooltip=[alt.Tooltip("yearmonth(mes):T", title="Mes"), alt.Tooltip("modelos:Q", title="Modelos")],
            ).properties(height=260),
            width="stretch")

# ---------- guía con IA ----------
# Entrevista al visitante y le recomienda un modelo. Público: cada visitante usa SU clave de Groq, guardada solo
# en st.session_state (nunca en disco ni en st.cache_*, que se comparte entre visitantes). En tu PC: la del .env.
def _olvidar_clave() -> None:
    st.session_state.pop("groq_clave", None)
    st.session_state.pop("chat", None)


def _mostrar(texto_md: str) -> None:
    st.markdown(texto_md.replace("$", r"\$"))  # "$4 / $20" no es una fórmula matemática


with tab_chat:
    clave = st.session_state.get("groq_clave") or ("" if PUBLICO else cfg.groq_api_key)
    st.subheader("🧭 Tu guía para elegir modelo")
    if not clave:
        st.markdown("Te pregunto **qué quieres lograr**, si lo quieres **en tu computador o en la nube** y con qué "
                    "equipo cuentas, y te recomiendo el modelo ideal con los pasos para empezar.\n\n"
                    "Funciona con **tu propia clave gratis de Groq**:")
        st.markdown("1. Crea tu cuenta gratis en **[console.groq.com](https://console.groq.com)** "
                    "(con Google, GitHub o tu correo).\n"
                    "2. Entra a **[API Keys](https://console.groq.com/keys)** → **Create API Key** y copia la clave "
                    "(empieza por `gsk_`).\n"
                    "3. Pégala aquí:")
        with st.form("form_clave_groq", border=False):
            nueva = st.text_input("Tu clave de Groq", type="password", placeholder="gsk_…")
            if st.form_submit_button("🔌 Conectar", type="primary"):
                ok, detalle = validar_clave(nueva.strip(), cfg.groq_modelo)
                if ok:
                    st.session_state.groq_clave = nueva.strip()
                    st.rerun()
                st.error(detalle)
        st.caption("🔒 Tu clave solo vive en esta pestaña del navegador: no se guarda en ningún lado y se borra al "
                   "cerrarla. Solo viaja a Groq (cifrada por HTTPS) y las preguntas gastan tu cuota gratis de Groq.")
    else:
        conversacion = st.session_state.setdefault("chat", [])
        if not conversacion:
            conversacion.append({"role": "assistant", "content": SALUDO})
        botones = st.container(horizontal=True)
        if len(conversacion) > 1 and botones.button("🔄 Empezar de nuevo"):
            conversacion.clear()
            st.rerun()
        if st.session_state.get("groq_clave"):
            botones.button("🔒 Olvidar mi clave", on_click=_olvidar_clave)

        pregunta = None
        for i, mensaje in enumerate(conversacion):
            cuerpo, opciones = separar_opciones(mensaje["content"]) if mensaje["role"] == "assistant" else (
                mensaje["content"], [])
            with st.chat_message(mensaje["role"]):
                _mostrar(cuerpo)
                if opciones and i == len(conversacion) - 1:  # respuestas rápidas solo en la última pregunta
                    with st.container(horizontal=True):
                        for j, opcion in enumerate(opciones):
                            if st.button(opcion, key=f"opcion_{len(conversacion)}_{j}"):
                                pregunta = opcion
        pregunta = st.chat_input("Escribe tu respuesta o tu pregunta…") or pregunta
        if pregunta:
            conversacion.append({"role": "user", "content": pregunta})
            with st.chat_message("user"):
                _mostrar(pregunta)
            with st.chat_message("assistant"):
                try:
                    with st.spinner("Pensando y buscando en WikiIA…"):
                        respuesta = responder(clave, cfg.groq_modelo, conversacion, modelos)
                    conversacion.append({"role": "assistant", "content": respuesta})
                    st.rerun()  # redibuja para mostrar los botones de respuesta rápida
                except ClaveInvalida as e:
                    conversacion.pop()
                    st.session_state.pop("groq_clave", None)
                    st.error(str(e))
                except ErrorChat as e:
                    conversacion.pop()
                    st.error(str(e))

# ---------- tour guiado (primera visita) ----------
# Se envía una vez por sesión si el navegador no trae la cookie; tour.js la vuelve a revisar antes de empezar.
if not st.session_state.get("tour_enviado") and st.context.cookies.get(COOKIE_TOUR) != "1":
    st.session_state.tour_enviado = True
    st.html(html_tour(), unsafe_allow_javascript=True)

# ---------- configuración (solo en tu PC) ----------
if PUBLICO:
    st.stop()
with tab_admin[0]:
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("🔄 Actualizar")
        investigar_n = st.number_input("Modelos a investigar con IA en esta actualización", 0, 100,
                                       value=min(cfg.investigar_por_ejecucion, 10))
        if st.button("🔄 Buscar modelos nuevos ahora", type="primary"):
            with st.status("Actualizando WikiIA…", expanded=True) as estado:
                linea = st.empty()
                resultado = ejecutar(cfg, investigar=investigar_n, progreso=linea.text)
                if resultado.get("ocupado"):
                    estado.update(label="Ya hay una actualización en curso", state="error")
                else:
                    nuevos = len(resultado["nuevos"])
                    estado.update(label=f"Listo: {nuevos} modelos nuevos, {resultado['investigados']} investigados",
                                  state="complete")
                    for e in resultado["errores"][:8]:
                        st.caption(f"⚠️ {e}")
            st.cache_data.clear()

        st.subheader("🧠 Investigar pendientes con IA")
        con = db.conectar()
        faltan = con.execute("SELECT COUNT(*) FROM modelos WHERE investigado=0 AND oculto=0").fetchone()[0]
        con.close()
        st.caption(f"Quedan **{faltan}** modelos sin ficha de IA. Se investigan primero los más potentes y recientes.")
        cantidad = st.number_input("¿Cuántos?", 1, 200, value=10)
        if st.button("🧠 Investigar ahora"):
            con = db.conectar()
            cliente = ClienteLLM(cfg)
            ids = pendientes(con, int(cantidad))
            barra = st.progress(0.0, text="Investigando…")
            fallos = 0
            for i, modelo_id in enumerate(ids, 1):
                try:
                    investigar_modelo(con, cliente, modelo_id)
                except ErrorLLM as e:
                    fallos += 1
                    st.caption(f"⚠️ {modelo_id}: {e}")
                barra.progress(i / len(ids), text=f"{i}/{len(ids)} · {modelo_id}")
            con.close()
            st.success(f"Investigados {len(ids) - fallos} de {len(ids)}.")
            st.cache_data.clear()

        st.subheader("🔔 Alertas")
        st.caption("Escritorio: siempre activas. Telegram: agrega TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en el archivo .env")
        if st.button("Enviar alerta de prueba"):
            con = db.conectar()
            canales = enviar_alerta(con, cfg, "🔔 WikiIA: prueba", "Si ves esto, las alertas funcionan.")
            con.close()
            st.success(f"Enviada por: {', '.join(canales) or 'ningún canal'}")

    with c2:
        st.subheader("🧠 Motor de IA")
        llm = estado_llm()
        st.markdown(f"- **Groq** {'✅' if llm['groq'] else '❌'} {llm['groq_detalle']}\n"
                    f"- **Ollama** {'✅' if llm['ollama'] else '❌'} {llm['ollama_detalle']}\n"
                    f"- **Modo:** `{cfg.llm_proveedor}` (auto = Groq primero, Ollama si Groq falla)")
        perfil = perfil_equipo()
        st.caption(f"Tu equipo: {perfil['ram_gb']} GB de RAM · "
                   f"{'GPU ' + str(perfil['vram_gb']) + ' GB' if perfil['vram_gb'] else 'sin GPU NVIDIA'}")

        st.subheader("⏰ Actualización automática")
        try:
            activo = subprocess.run(["systemctl", "--user", "is-active", "wikiia-actualizar.timer"],
                                    capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            activo = "desconocido"
        if activo == "active":
            st.success("Activa: revisa cada 6 horas (y al encender el PC si se perdió una revisión).")
        else:
            st.warning("No está activa. Para activarla ejecuta en una terminal:\n\n"
                       "`./scripts/instalar_temporizador.sh`")

        st.subheader("🏳️ Países de las empresas")
        sin_pais = empresas[empresas["pais"] == "XX"]
        etiquetas = {r["codigo"]: f"{r['bandera']} {r['nombre']}" for _, r in paises.iterrows()}
        if sin_pais.empty:
            st.caption("Todas las empresas tienen país asignado.")
        else:
            st.caption(f"{len(sin_pais)} empresas nuevas sin país. Asígnalo y pulsa Guardar.")
            editor = st.data_editor(
                sin_pais[["id", "nombre", "pais"]], hide_index=True, key="editor_paises",
                column_config={
                    "id": st.column_config.TextColumn("Código", disabled=True),
                    "nombre": st.column_config.TextColumn("Empresa", disabled=True),
                    "pais": st.column_config.SelectboxColumn("País", options=list(etiquetas), required=True,
                                                             format_func=etiquetas.get),
                })
            if st.button("💾 Guardar países"):
                con = db.conectar()
                for _, r in editor.iterrows():
                    if r["pais"] != "XX":
                        con.execute("UPDATE empresas SET pais=?, editado=1 WHERE id=?", (r["pais"], r["id"]))
                con.commit()
                con.close()
                refrescar()
        with st.expander("Corregir el país de cualquier empresa"):
            empresa_sel = st.selectbox("Empresa", list(empresas.sort_values("nombre")["id"]),
                                       format_func=lambda e: empresas.set_index("id").loc[e, "nombre"])
            actual = empresas.set_index("id").loc[empresa_sel, "pais"]
            nuevo = st.selectbox("País", list(etiquetas), format_func=etiquetas.get,
                                 index=list(etiquetas).index(actual) if actual in etiquetas else 0)
            if st.button("Guardar país"):
                con = db.conectar()
                con.execute("UPDATE empresas SET pais=?, editado=1 WHERE id=?", (nuevo, empresa_sel))
                con.commit()
                con.close()
                refrescar()

    st.subheader("📜 Historial de actualizaciones")
    if not ejecuciones.empty:
        st.dataframe(ejecuciones[["inicio", "fin", "nuevos", "actualizados", "investigados", "resumen"]],
                     hide_index=True, width="stretch")
