# 🌐 WikiIA

La enciclopedia **local** de los modelos de inteligencia artificial del mundo: Estados Unidos, China,
Rusia, Alemania, Francia, Corea, Japón y más. Se actualiza sola, investiga cada modelo con IA
(Groq + Ollama) y te avisa en el escritorio (y en Telegram, si quieres) cada vez que sale uno nuevo.

## Uso rápido

```bash
./iniciar.sh                          # abre la app en http://localhost:8501
./scripts/instalar_temporizador.sh    # activa la revisión automática cada 6 horas
```

## Qué muestra cada ficha

| Sección | Contenido |
|---|---|
| Identidad | Empresa, país, fecha de publicación, si sigue activo en la API |
| Cómo se usa | ☁️ Solo API · 🖥️ Local (pesos abiertos) · ambos |
| Precio | USD por millón de tokens (entrada / salida), gratis o local |
| Sirve para | 💻 Código · 🔬 Investigación · 💬 Chat · 👁️ Visión · 🎨 Imágenes · 🎬 Video · 🎙️ Voz · 🦾 Agentes |
| Potencia | ★1 a ★5, con la razón y quién la decidió (heurística, IA o tú) |
| Técnico | Tamaño, contexto, licencia, memoria necesaria y **si corre en tu PC** |
| ⚠️ Importante | Privacidad y leyes del país, licencia comercial, versiones preview, consejos |
| 📥 Descargar y ejecutar | (modelos locales) comando listo para copiar en cada motor: Ollama, LM Studio, llama.cpp, Jan, vLLM, SGLang, Transformers, MLX (Mac); para imagen/video: ComfyUI, ComfyUI ligero (GGUF), Diffusers |
| 🖥️ Requisitos mínimos | (modelos locales) disco, memoria, GPU recomendada, si se puede solo con CPU y Mac, para 4 bits, 8 bits y original |
| 🌐 Dónde usarlo | (modelos en la nube) chat oficial, web, API/documentación, precios y proveedores |

Las versiones GGUF y MLX se buscan en Hugging Face por la relación oficial `base_model:quantized`,
así que corresponden exactamente al modelo (se descartan versiones modificadas como "abliterated").

Se puede ordenar por **país**, **empresa**, **potencia**, **fecha** y **precio**, y filtrar por capacidades.

## Cómo funciona

```
OpenRouter API ──┐  (modelos por API, precios, modalidades)
                 ├─► reglas automáticas ─► SQLite ─► Groq / Ollama investigan ─► alertas
Hugging Face API ┘  (modelos abiertos de ~60 organizaciones oficiales)          (escritorio, Telegram)
data/manuales.json  (servicios de video/imagen sin API pública: Sora, Veo, Kling, Midjourney…)
```

1. **Detección**: compara el catálogo de OpenRouter y las organizaciones oficiales de Hugging Face
   contra la base. Filtra el ruido (copias GGUF/FP8, LoRA, paquetes para un hardware, experimentos).
2. **Reglas automáticas**: deducen capacidades, acceso, precio y una potencia inicial sin IA.
3. **Investigación con IA**: Groq (`openai/gpt-oss-120b`) lee la documentación oficial y redacta la
   ficha en español. Si Groq llega a su límite gratis, sigue Ollama (`qwen2.5:3b`) en tu PC.
4. **Alertas**: cada modelo nuevo genera una notificación. La **primera carga no alerta**
   (es la línea base); tampoco una organización recién agregada al catálogo.

## Comandos

```bash
.venv/bin/python -m wikiia actualizar            # buscar nuevos + investigar + alertar
.venv/bin/python -m wikiia actualizar --sin-alertas
.venv/bin/python -m wikiia investigar --cantidad 50
.venv/bin/python -m wikiia investigar --modelo deepseek/deepseek-v4.1-flash
.venv/bin/python -m wikiia probar-llm             # comprueba Groq y Ollama
.venv/bin/python -m wikiia probar-alerta
.venv/bin/python -m wikiia estado
```

## En internet

| Qué | Dónde | Cómo se actualiza |
|---|---|---|
| La app (solo lectura) | Streamlit Community Cloud | Sola, con cada `git push` |
| Calendario de lanzamientos (`wikiia.ics`) | GitHub Pages | Workflow `.github/workflows/calendario.yml` en cada `git push` que cambie la base o el código |

- **Modo público**: en Streamlit Cloud la app se pone sola en solo lectura (sin Configuración, investigar ni
  editar; ahí no hay Ollama ni `.env`). Los datos se investigan en tu PC y se suben con `git push`.
  `WIKIIA_PUBLICO=1` lo fuerza en tu PC para probarlo.
- **Tour guiado**: la primera vez que alguien entra, un recorrido ilumina cada parte de la pantalla
  (driver.js, MIT, en `wikiia/static/driverjs`). Una cookie recuerda que ya lo vio; para verlo de nuevo, abre
  la app en una ventana de incógnito.
- **Avisos**: el recuadro "¿Quieres enterarte?" aparece cuando el calendario ya está publicado. Con
  `TELEGRAM_CANAL_URL` aparece también el botón del canal de Telegram.
- **Guía con IA** (burbuja verde con la W, abajo a la derecha): entrevista al visitante (qué quiere lograr,
  local o nube, su computador, presupuesto) y le recomienda un modelo del catálogo con los pasos para empezar.
  La IA busca con la herramienta `buscar_modelos` (`wikiia/chat.py`) en vez de responder de memoria. Cada
  visitante usa **su propia clave gratis de Groq**. Usa un modelo moderado (`GROQ_MODELO_GUIA`,
  `openai/gpt-oss-20b`) y, si Groq pide una pausa o se queda sin cupo, espera o pasa a `gpt-oss-120b`. Los
  errores de Groq nunca se muestran crudos: la persona ve qué hacer (esperar, verificar su clave…).
- **Cuentas** (`wikiia/cuentas.py`): con `DATABASE_URL` (PostgreSQL) y `WIKIIA_SECRETO`, la guía pide entrar
  con correo y contraseña y guarda la clave de Groq **cifrada** en la cuenta; la sesión se recuerda 30 días.
  La contraseña solo se guarda como huella scrypt. Sin esas dos variables la clave dura solo la sesión.
- **Enlaces revisados** (`wikiia/enlaces.py`): `.venv/bin/python -m wikiia enlaces` revisa que abran los
  enlaces de modelos y empresas (también corre en cada actualización automática). Los rotos no se muestran:
  la app y el calendario usan el siguiente enlace disponible.
- **Calendario a mano**: `.venv/bin/python -m wikiia.calendario --carpeta _site`

Para que el workflow pueda publicar (una sola vez): en GitHub, **Settings → Pages → Build and deployment →
Source: GitHub Actions**.

### Base de datos de las cuentas (PostgreSQL)

1. Crea una base gratis en [neon.tech](https://neon.tech) (entra con GitHub) y copia la *connection string*
   (`postgresql://…?sslmode=require`).
2. Genera un secreto: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.
3. En Streamlit Cloud: **Manage app → ⋮ → Settings → Secrets** y pega:

   ```toml
   DATABASE_URL = "postgresql://…"
   WIKIIA_SECRETO = "el-secreto-que-generaste"
   ```

   (En tu PC van en el `.env`.) Las tablas `wikiia_usuarios` y `wikiia_sesiones` se crean solas. No cambies
   `WIKIIA_SECRETO` después: las claves de Groq guardadas dejarían de abrir.

## Versiones

Cada lote de cambios se marca con un tag (`git tag -a v1.0.2 -m "..."` y `git push origin v1.0.2`).

## Personalizar

- **`.env`**: API de Groq, modelo de Ollama, Telegram, calendario (ver `.env.example`).
- **`data/empresas.json`**: agrega empresas y sus organizaciones de Hugging Face / prefijos de OpenRouter.
- **`data/paises.json`**: países y su nota de privacidad.
- **`data/manuales.json`**: servicios que no aparecen en las fuentes automáticas.
- Desde la app puedes **corregir cualquier ficha**; tus cambios nunca se sobrescriben.

## Límites que conviene saber

- La **potencia** es una estimación (tamaño, precio, comparación con la misma empresa y documentación),
  no un benchmark. Corrígela a mano cuando sepas más.
- Los servicios de video/imagen cerrados (Sora, Veo, Kling…) no tienen fuente automática: sus
  versiones nuevas no generan alerta todavía.
- Ollama en CPU es lento (~1 minuto por ficha): se usa solo como respaldo.
