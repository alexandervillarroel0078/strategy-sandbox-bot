"""Métricas y reporte de una simulación."""
from __future__ import annotations

import os
from datetime import datetime

import numpy as np

from .sim_types import Result

MIN_TRADES = 30


def _max_dd(x: np.ndarray) -> float:
    """Máxima caída desde un máximo previo, en %."""
    return float((x / np.maximum.accumulate(x) - 1).min() * 100)


def compute(res: Result) -> dict:
    p, t, eq = res.params, res.trades, res.equity
    e = eq["equity"].to_numpy()
    final = float(e[-1])
    gan, per = t[t["pnl"] > 0], t[t["pnl"] <= 0]
    n = len(t)
    # comprar y mantener en el mismo período, con el mismo costo de entrada/salida
    bh = res.last_close * (1 - p.slippage) * (1 - p.fee) / (res.first_open * (1 + p.slippage) * (1 + p.fee)) - 1
    return {
        "desde": eq["time"].iloc[0], "hasta": eq["time"].iloc[-1],
        "saldo_inicial": p.initial_balance, "saldo_final": final,
        "retorno_pct": (final / p.initial_balance - 1) * 100,
        "bh_retorno_pct": bh * 100,
        "max_dd_pct": _max_dd(e),
        "bh_max_dd_pct": _max_dd(eq["price"].to_numpy()),
        "n_trades": n,
        "aciertos_pct": len(gan) / n * 100 if n else float("nan"),
        "gan_prom_pct": gan["return_pct"].mean() if len(gan) else float("nan"),
        "per_prom_pct": per["return_pct"].mean() if len(per) else float("nan"),
        "gan_prom": gan["pnl"].mean() if len(gan) else float("nan"),
        "per_prom": per["pnl"].mean() if len(per) else float("nan"),
        "expectativa_pct": t["return_pct"].mean() if n else float("nan"),
        "expectativa": t["pnl"].mean() if n else float("nan"),
        "comisiones": float(t["fees"].sum()) if n else 0.0,
        "salidas": t["reason"].value_counts().to_dict() if n else {},
    }


_FILAS = [
    ("Período", lambda m: f"{m['desde']:%Y-%m-%d} → {m['hasta']:%Y-%m-%d}"),
    ("Saldo inicial", lambda m: f"{m['saldo_inicial']:,.2f}"),
    ("Saldo final", lambda m: f"{m['saldo_final']:,.2f}"),
    ("Retorno estrategia", lambda m: f"{m['retorno_pct']:+.2f}%"),
    ("Retorno comprar y mantener", lambda m: f"{m['bh_retorno_pct']:+.2f}%"),
    ("Máxima caída estrategia", lambda m: f"{m['max_dd_pct']:.2f}%"),
    ("Máxima caída comprar y mantener", lambda m: f"{m['bh_max_dd_pct']:.2f}%"),
    ("Nº de operaciones", lambda m: f"{m['n_trades']}"),
    ("% de aciertos", lambda m: f"{m['aciertos_pct']:.1f}%"),
    ("Ganancia promedio", lambda m: f"{m['gan_prom']:+,.2f} ({m['gan_prom_pct']:+.2f}%)"),
    ("Pérdida promedio", lambda m: f"{m['per_prom']:+,.2f} ({m['per_prom_pct']:+.2f}%)"),
    ("Expectativa por operación", lambda m: f"{m['expectativa']:+,.2f} ({m['expectativa_pct']:+.2f}%)"),
    ("Comisiones pagadas", lambda m: f"{m['comisiones']:,.2f}"),
    ("Salidas", lambda m: ", ".join(f"{k}:{v}" for k, v in m["salidas"].items()) or "-"),
]


def report(items: list[tuple[str, dict, Result]]) -> str:
    """Tabla de métricas; una columna por período (lado a lado en --split)."""
    ancho = 33 + 1
    cols = [max(34, max(len(f(m)) for _, f in _FILAS) + 2) for _, m, _ in items]
    lineas = ["".ljust(ancho) + "".join(t.ljust(c) for (t, _, _), c in zip(items, cols))]
    lineas.append("-" * (ancho + sum(cols)))
    for nombre, f in _FILAS:
        lineas.append(nombre.ljust(ancho) + "".join(f(m).ljust(c) for (_, m, _), c in zip(items, cols)))
    for titulo, m, res in items:
        for w in res.warnings:
            lineas.append(f"⚠ [{titulo}] {w}")
        if m["n_trades"] < MIN_TRADES:
            lineas.append(f"⚠ [{titulo}] solo {m['n_trades']} operaciones (< {MIN_TRADES}): "
                          "el resultado NO es estadísticamente fiable")
    return "\n".join(lineas)


def save(res: Result, folder: str, sufijo: str = "") -> tuple[str, str]:
    """Guarda trades.csv y equity.csv con fecha y hora en `folder`."""
    os.makedirs(folder, exist_ok=True)
    sello = datetime.now().strftime("%Y%m%d_%H%M%S")
    pt = os.path.join(folder, f"trades_{sello}{sufijo}.csv")
    pe = os.path.join(folder, f"equity_{sello}{sufijo}.csv")
    res.trades.to_csv(pt, index=False)
    res.equity.to_csv(pe, index=False)
    return pt, pe
