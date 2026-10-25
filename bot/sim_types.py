"""Tipos de resultado del bot. Copias propias: el bot no importa nada del exchange."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .broker import Params


@dataclass
class Audit:
    """Calidad de los datos dentro de un rango."""
    n_present: int = 0
    n_missing: int = 0
    gaps: list[tuple[pd.Timestamp, pd.Timestamp, int]] = field(default_factory=list)
    n_flat: int = 0

    def warnings(self) -> list[str]:
        out = []
        if self.n_missing:
            primeros = ", ".join(f"{a:%Y-%m-%d %H:%M}→{b:%Y-%m-%d %H:%M} ({n})" for a, b, n in self.gaps[:3])
            out.append(f"HUECOS: faltan {self.n_missing} velas en {len(self.gaps)} tramos. Primeros: {primeros}")
        if self.n_flat:
            out.append(f"VELAS VACÍAS: {self.n_flat} velas con volumen 0 y OHLC plano")
        return out


@dataclass
class Result:
    trades: pd.DataFrame
    equity: pd.DataFrame           # time (cierre de cada vela fina), equity, price
    decisions: list[tuple[int, str]]  # (epoch del cierre, acción) de CADA llamada a la estrategia
    params: Params
    audit: Audit
    warnings: list[str] = field(default_factory=list)
    first_open: float = 0.0
    last_close: float = 0.0
