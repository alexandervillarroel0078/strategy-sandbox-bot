"""Bot que opera contra la API del mercado simulado (como si fuera un exchange).

Primero arranca el exchange (repo strategy-sandbox-exchange, ver su README).
Después, desde la raíz de este repo:       python -m bot.run_bot_api --start 2024-10-25

--interval es la temporalidad de la estrategia; --clock es el paso fino de la simulación
(los mismos nombres que usa el simulador directo del repo del exchange).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time

from . import metrics
from .bot_client import ExchangeClient, ExchangeError
from .broker import Params
from .exchange_broker import run_bot_exchange
from .reglas import REGLAS
from .registro import Registro, conservar_logs, host_puerto, limpiar_logs
from .utils import ROOT, load_config, load_strategy, parse_ts, resolver_ruta


def _hash(ruta: str) -> str | None:
    try:
        with open(ruta, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except OSError:
        return None


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Bot de prueba contra la API del mercado simulado")
    ap.add_argument("--config", help="por defecto bot/config.yaml")
    ap.add_argument("--start", help="inicio (UTC), ej. 2024-10-25")
    ap.add_argument("--end", help="fin inclusivo (UTC); por defecto hasta el final de los datos")
    ap.add_argument("--strategy", default="ejemplo", help="módulo en bot/strategies/ (sin .py)")
    ap.add_argument("--interval", help="temporalidad de la estrategia (por defecto la de config.yaml)")
    ap.add_argument("--clock", choices=["5m", "15m"], help="paso fino de la simulación")
    ap.add_argument("--url", help="dirección del mercado simulado (por defecto api_url de bot/config.yaml)")
    ap.add_argument("--ejecucion", choices=["exchange"], default="exchange",
                    help="exchange: el bot ENVÍA las órdenes al exchange simulado (la única vía; la interna ya no se ofrece)")
    ap.add_argument("--reset", action="store_true",
                    help="con --ejecucion exchange: resetear la sesión al terminar (por defecto se deja para verla en el visor)")
    ap.add_argument("--log", action="store_true", help="registrar lo que el bot recibe y envía en bot/logs/ (apagado por defecto)")
    ap.add_argument("--log-nivel", choices=["eventos", "completo"], default="eventos",
                    help="completo: guarda además cada vela recibida en bot/logs/feed_<fecha-hora>.csv")
    ap.add_argument("--verbose", action="store_true", help="imprime una línea por decisión y por orden")
    ap.add_argument("--no-save", action="store_true", help="no escribir CSV en bot/results/")
    a = ap.parse_args()

    cfg = load_config(a.config)
    url = a.url or cfg["api_url"]
    clock = a.clock or cfg["clock"]
    interval = a.interval or cfg["strategy_timeframe"]
    start = parse_ts(a.start or cfg["start"])
    fin = a.end or cfg.get("end")
    end = parse_ts(fin, fin=True) if fin else None
    params = Params(cfg["initial_balance"], cfg["fee"], cfg["slippage"],
                    cfg["stop_pct"], cfg["target_pct"], cfg["position_fraction"])
    strat = load_strategy(a.strategy, interval)
    reg = Registro(archivo=a.log, nivel=a.log_nivel, verbose=a.verbose) if (a.log or a.verbose) else None
    client = ExchangeClient(url, registro=reg) if reg else ExchangeClient(url)
    if reg:
        reg.evento("inicio", estrategia=a.strategy, intervalo=strat.timeframe, paso=clock, api=host_puerto(url),
                   ejecucion=a.ejecucion, inicio_sim=str(start), fin_sim=str(end) if end is not None else None,
                   params={"saldo": params.initial_balance, "fee": params.fee, "slippage": params.slippage,
                           "stop": params.stop_pct, "objetivo": params.target_pct,
                           "fraccion": params.position_fraction},
                   reglas={"ruta": os.path.relpath(REGLAS, ROOT).replace(os.sep, "/"),
                           "sha256": _hash(REGLAS)}, log_nivel=a.log_nivel,
                   feed=os.path.basename(reg.ruta_feed) if reg.ruta_feed else None)

    if reg and reg.ruta:
        conservar = conservar_logs(cfg)
        if conservar is None:
            print(f"AVISO: logs_conservar={cfg.get('logs_conservar')!r} no es válido (entero >= 0); no se borra ningún registro.")
        elif n := limpiar_logs(os.path.dirname(reg.ruta), conservar, reg.ruta):
            print(f"Registro: limpieza de logs: {n} archivos antiguos borrados (se conservan las últimas {conservar} corridas)")

    print(f"Bot: {a.strategy} ({strat.timeframe}) · paso {clock} · API {url} · "
          f"comisión {params.fee:.2%} · deslizamiento {params.slippage:.2%} · "
          f"stop {params.stop_pct:.0%} · objetivo {params.target_pct:.0%} · ejecución {a.ejecucion}\n")
    kw = {"registro": reg} if reg else {}
    t0 = time.perf_counter()
    try:
        res = run_bot_exchange(client, strat, params, clock, start, end, reset=a.reset, **kw)
    except ExchangeError as e:
        if reg:
            reg.evento("fin", error=type(e).__name__)
            reg.cerrar()
        raise SystemExit(f"Error del exchange simulado: {e}")
    t_sim = time.perf_counter() - t0

    if reg:
        reg.evento("fin", operaciones=len(res.trades), retorno_pct=round(float(metrics.compute(res)["retorno_pct"]), 4),
                   decisiones=len(res.decisions), peticiones=client.n_requests,
                   velas_verificadas=client.candles_checked, segundos=round(t_sim, 2))
        reg.cerrar()
        if reg.ruta:
            print(f"Registro: {reg.ruta}" + (f" · feed: {reg.ruta_feed}" if reg.ruta_feed else ""))
    print(metrics.report([("SIMULACIÓN (API)", metrics.compute(res), res)]))
    if not a.no_save:
        pt, pe = metrics.save(res, resolver_ruta(cfg.get("results_dir", "bot/results")), "_api")
        print(f"\nGuardado: {pt}  |  {pe}")
    print(f"\nPeticiones a la API: {client.n_requests:,} · velas verificadas contra el ahora: "
          f"{client.candles_checked:,} · tiempo {t_sim:.1f}s")


if __name__ == "__main__":
    main()
