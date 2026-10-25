"""IDEA 2: Donchian diario, solo largos. Parámetros fijos 100/50 (no se tocan)."""
from __future__ import annotations

import pandas as pd

from bot.broker import Position
from bot.strategies.base import BUY, HOLD, SELL, Strategy

ENTRADA_N, SALIDA_N = 100, 50


class Donchian(Strategy):
    lookback = ENTRADA_N + 1   # 100 velas previas + la que acaba de cerrar

    def on_candle(self, candles: pd.DataFrame, position: Position | None) -> str:
        if len(candles) < self.lookback:
            return HOLD
        cierre = candles["close"].iloc[-1]
        if position is None:
            # entrada: cierre sobre el máximo de las 100 velas anteriores
            if cierre > candles["high"].iloc[-(ENTRADA_N + 1):-1].max():
                return BUY
        elif cierre < candles["low"].iloc[-(SALIDA_N + 1):-1].min():
            return SELL   # salida: cierre bajo el mínimo de las 50 velas anteriores
        return HOLD
