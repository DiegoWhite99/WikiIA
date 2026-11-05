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

## Personalizar

- **`.env`**: API de Groq, modelo de Ollama, Telegram (ver `.env.example`).
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
