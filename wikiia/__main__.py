"""Línea de comandos de WikiIA.

    python -m wikiia actualizar            # busca modelos nuevos, investiga y alerta
    python -m wikiia investigar --cantidad 50
    python -m wikiia probar-llm
    python -m wikiia probar-alerta
    python -m wikiia estado
    python -m wikiia enlaces [--todos]     # revisa que los enlaces de modelos y empresas abran
    python -m wikiia benchmarks            # potencia medida con el índice de Artificial Analysis
"""
from __future__ import annotations

import argparse
import json
import sys

from . import alertas, benchmarks, db, enlaces, investigador
from .actualizador import ejecutar
from .config import cargar_config
from .fuentes import nueva_sesion
from .llm import ClienteLLM, ErrorLLM


def _probar_llm(cfg) -> int:
    cliente = ClienteLLM(cfg)
    estado = cliente.estado()
    print(f"Groq   : {'✅' if estado['groq'] else '❌'} {estado['groq_detalle']}")
    print(f"Ollama : {'✅' if estado['ollama'] else '❌'} {estado['ollama_detalle']}")
    print(f"Orden  : {' → '.join(cliente.orden())}")
    try:
        datos, proveedor = cliente.json(
            "Responde solo JSON.", 'Devuelve {"saludo": "hola desde WikiIA"} en JSON.')
        print(f"Prueba : ✅ {proveedor} respondió {datos}")
        return 0
    except ErrorLLM as e:
        print(f"Prueba : ❌ {e}")
        return 1


def _estado() -> int:
    con = db.conectar()
    total = con.execute("SELECT COUNT(*) FROM modelos").fetchone()[0]
    investigados = con.execute("SELECT COUNT(*) FROM modelos WHERE investigado=1").fetchone()[0]
    print(f"Modelos: {total} · investigados con IA: {investigados} · pendientes: {total - investigados}")
    for f in con.execute(
        "SELECT p.bandera, p.nombre, COUNT(*) n FROM modelos m JOIN empresas e ON e.id=m.empresa_id "
        "JOIN paises p ON p.codigo=e.pais GROUP BY p.codigo ORDER BY n DESC"
    ):
        print(f"  {f['bandera']} {f['nombre']}: {f['n']}")
    ultima = con.execute("SELECT * FROM ejecuciones ORDER BY id DESC LIMIT 1").fetchone()
    if ultima:
        print(f"Última actualización: {ultima['fin']} → {ultima['resumen']}")
        for e in json.loads(ultima["errores"] or "[]")[:10]:
            print(f"  ⚠ {e}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="wikiia", description="WikiIA: enciclopedia local de modelos de IA")
    sub = parser.add_subparsers(dest="comando", required=True)
    a = sub.add_parser("actualizar", help="Busca modelos nuevos, los investiga y manda alertas")
    a.add_argument("--investigar", type=int, default=None, help="cuántos modelos investigar con IA")
    a.add_argument("--sin-alertas", action="store_true")
    i = sub.add_parser("investigar", help="Investiga con IA los modelos pendientes")
    i.add_argument("--cantidad", type=int, default=10)
    i.add_argument("--modelo", help="id de un modelo concreto (por ejemplo deepseek/deepseek-v4.1-flash)")
    sub.add_parser("probar-llm", help="Comprueba Groq y Ollama")
    sub.add_parser("probar-alerta", help="Manda una alerta de prueba")
    sub.add_parser("estado", help="Resumen de la base de datos")
    e = sub.add_parser("enlaces", help="Revisa que los enlaces de modelos y empresas abran")
    e.add_argument("--todos", action="store_true", help="revisa también los que ya se revisaron hace poco")
    e.add_argument("--limite", type=int, default=None)
    sub.add_parser("benchmarks", help="Potencia medida con el Intelligence Index de Artificial Analysis")
    args = parser.parse_args(argv)
    cfg = cargar_config()

    if args.comando == "actualizar":
        resultado = ejecutar(cfg, investigar=args.investigar, alertar=not args.sin_alertas)
        return 1 if resultado.get("ocupado") else 0
    if args.comando == "investigar":
        con = db.conectar()
        cliente, sesion = ClienteLLM(cfg), nueva_sesion()
        ids = [args.modelo] if args.modelo else investigador.pendientes(con, args.cantidad)
        for n, modelo_id in enumerate(ids, 1):
            try:
                proveedor = investigador.investigar_modelo(con, cliente, modelo_id, sesion)
                print(f"[{n}/{len(ids)}] ✓ {modelo_id} ({proveedor})")
            except ErrorLLM as e:
                print(f"[{n}/{len(ids)}] ✗ {modelo_id}: {e}")
        return 0
    if args.comando == "probar-llm":
        return _probar_llm(cfg)
    if args.comando == "probar-alerta":
        con = db.conectar()
        canales = alertas.enviar(con, cfg, "🔔 WikiIA: prueba", "Si ves esto, las alertas funcionan.")
        print(f"Enviada por: {', '.join(canales) or 'ningún canal (revisa notify-send / Telegram)'}")
        return 0
    if args.comando == "estado":
        return _estado()
    if args.comando == "benchmarks":
        con = db.conectar()
        r = benchmarks.actualizar(con, cfg.aa_api_key, nueva_sesion())
        print(f"Potencia medida en {r['medidos']} modelos (mejor índice: {r['maximo']:.0f})."
              + (f" Aviso: {r['aviso']}" if r["aviso"] else ""))
        con.close()
        return 0
    if args.comando == "enlaces":
        con = db.conectar()
        conteo = enlaces.revisar(con, todos=args.todos, limite=args.limite)
        print(f"Enlaces revisados: {conteo['revisados']} · abren: {conteo['abren']} · rotos: {conteo['rotos']} · "
              f"sin confirmar: {conteo['sin_confirmar']}")
        con.close()
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
