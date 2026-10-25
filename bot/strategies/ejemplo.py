"""Estrategia de EJEMPLO (sin optimizar): tendencia EMA 50/200 + recuperación del RSI."""
from __future__ import annotations

import pandas as pd

from bot.broker import Position
from bot.strategies.base import BUY, HOLD, SELL, Strategy

EMA_RAPIDA, EMA_LENTA, RSI_N = 50, 200, 14
RSI_PULLBACK = 40  # el RSI cae bajo este nivel y luego lo recupera


def rsi(close: pd.Series, n: int = RSI_N) -> pd.Series:
    """RSI de Wilder, calculado solo con la ventana recibida."""
    d = close.diff()
    gan = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    per = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + gan / per)


class EmaRsi(Strategy):
    lookback = 600  # ventana fija: los indicadores no dependen de la fecha de inicio

    def on_candle(self, candles: pd.DataFrame, position: Position | None) -> str:
        self.explicacion = {"motivo": "datos insuficientes", "valores": {}}   # solo para el registro (--log)
        if len(candles) < self.lookback:
            return HOLD
        close = candles["close"]
        ema_r = close.ewm(span=EMA_RAPIDA, adjust=False).mean().iloc[-1]
        ema_l = close.ewm(span=EMA_LENTA, adjust=False).mean().iloc[-1]
        alcista = ema_r > ema_l
        r = rsi(close)
        self.explicacion = {"motivo": "sin señal", "valores": {
            "rsi": round(float(r.iloc[-1]), 2), "rsi_prev": round(float(r.iloc[-2]), 2),
            "ema50": round(float(ema_r), 2), "ema200": round(float(ema_l), 2), "alcista": bool(alcista)}}
        if position is None:
            # recuperación: el RSI estaba bajo el nivel y lo recupera, dentro de tendencia alcista
            if alcista and r.iloc[-2] < RSI_PULLBACK <= r.iloc[-1]:
                self.explicacion["motivo"] = f"tendencia alcista y RSI recupera {RSI_PULLBACK}"
                return BUY
        elif not alcista:
            self.explicacion["motivo"] = "se pierde la tendencia (EMA50 bajo EMA200)"
            return SELL  # se pierde la tendencia
        return HOLD
