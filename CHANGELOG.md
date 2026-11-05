# Historial de versiones

WikiIA usa versionado semántico (`MAYOR.MENOR.PARCHE`). Cada versión es un tag anotado en GitHub.

## v2.1.1

- La columna «Índice IA» muestra «—» en lugar de «None» cuando un modelo aún no tiene medición.

## v2.1.0 — Ranking con benchmark real (2026-11-05)

- El ranking «⭐ Más potentes» se ordena por el **Intelligence Index de Artificial Analysis** (benchmark
  independiente), con una columna «Índice IA» y el puntaje en cada ficha.
- **5★ solo para la frontera medida**: las estimaciones (IA, reglas o catálogo manual) llegan como máximo a ★4.
  Antes la IA comparaba cada modelo con su propia familia y había 16 modelos empatados en 5★.
- Ya no se desempata por fecha: entre estimados del mismo nivel desempata el precio.
- La IA que investiga fichas compara la potencia con los mejores modelos del mundo, no con su familia.
- La guía con IA también usa el índice para recomendar.

## v2.0.0 — Versión estable (2026-11-05)

Primera versión estable, lista para revisión.
- README completo: qué hace, arquitectura, seguridad, puesta en marcha, configuración y límites.
- Este historial de versiones.
- Dependencias fijadas en `requirements.txt` con las versiones probadas (Streamlit 1.65.0, psycopg 3.3.8,
  cryptography 50.0.2, pandas 2.3.3, altair 6.2.2, requests 2.34.2, python-dotenv 1.2.4).
- La actualización automática revisa al empezar que la clave de Groq funcione y lo dice en el resumen.

## v1.6.0 — Actualización en la nube

- Los datos se actualizan solos en **GitHub Actions** cada 6 horas: modelos nuevos, 20 fichas con Groq,
  enlaces, base y calendario. Ya no hace falta un computador encendido.
- El calendario se publica también después de cada actualización automática.

## v1.5.0 — Administración

- Cuentas **root** con la pestaña «👑 Administración»: usuarios registrados, claves de Groq guardadas,
  sesiones abiertas y borrar cuentas. Los correos root se guardan como huella SHA-256.
- Cualquier usuario puede **cambiar su contraseña** (cierra sus sesiones en otros navegadores).

## v1.4.2

- Las cuentas ya no se guardan en caché entre actualizaciones del código: «Correo o contraseña incorrectos»
  salía como error técnico. Ningún error de cuentas se muestra crudo.

## v1.4.1

- Los Secrets de la base se leen en cada visita, sin reiniciar la app.

## v1.4.0 — Iniciar sesión en toda la app

- Sin sesión solo se ve la pantalla «Iniciar sesión / Crear cuenta».
- Registro con nombre, correo y contraseña escrita dos veces; el correo es la llave del usuario.
- Botón para ver u ocultar la contraseña; menú de la cuenta en la barra lateral.

## v1.3.0 — Cuentas, guía moderada y enlaces revisados

- Cuentas en **PostgreSQL** (Neon): la clave de Groq de cada persona queda guardada cifrada.
- La guía usa un modelo moderado (`gpt-oss-20b`, con respaldo `gpt-oss-120b`), gasta unas 3 veces menos
  tokens, espera sola si Groq pide una pausa y nunca muestra errores del proveedor.
- Revisión de enlaces de modelos y empresas: la app y el calendario saltan los rotos.

## v1.2.0 – v1.2.4 — Burbuja de la guía

- v1.2.0: la guía pasa a una burbuja flotante que abre una ventana de chat.
- v1.2.1: burbuja en 3D y un poco más arriba.
- v1.2.2: verde, con brillo al pasar el mouse, mensajes animados y «escribiendo…».
- v1.2.3: nuevo diseño con una **W**.
- v1.2.4: la W queda centrada.

## v1.1.0 – v1.1.1 — Guía con IA

- v1.1.0: guía que entrevista al visitante (qué quiere lograr, local o nube, su equipo) y le recomienda un
  modelo del catálogo, con la clave de Groq del propio visitante.
- v1.1.1: el workflow del calendario usa las acciones actualizadas (Node 24).

## v1.0.0 – v1.0.3 — Primeras versiones

- v1.0.0: primera versión publicada: catálogo por país, empresa y potencia, fichas investigadas con IA,
  filtros y alertas.
- v1.0.1: el nombre del modelo es el enlace; modo público (solo lectura) en Streamlit Cloud.
- v1.0.2: tour guiado en la primera visita y calendario de lanzamientos en GitHub Pages.
- v1.0.3: sin la columna «Ficha»; aviso animado «Observa la ficha» al elegir un modelo.
