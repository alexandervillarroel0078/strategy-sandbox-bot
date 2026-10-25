"""Interfaz de las estrategias."""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from bot.broker import Position

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class Strategy(ABC):
    lookback: int = 500  # nº de velas cerradas que se piden en cada llamada

    def __init__(self, timeframe: str = "1h"):
        self.timeframe = timeframe

    @abstractmethod
    def on_candle(self, candles: pd.DataFrame, position: Position | None) -> str:
        """Se llama al cerrar cada vela de `timeframe`.

        `candles`: últimas `lookback` velas CERRADAS (open_time, open, high, low, close,
        volume, close_time). `position`: None si no hay posición abierta.
        Devuelve BUY, SELL o HOLD; la orden se ejecuta en la apertura de la vela siguiente.
        """
