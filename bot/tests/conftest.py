"""Estrategias de prueba."""
import pytest

from bot.strategies.base import BUY, HOLD, SELL, Strategy


class Toy(Strategy):
    """Estrategia determinista y activa: sigue el cierre contra la media de 10 velas."""
    lookback = 30

    def on_candle(self, candles, position):
        if len(candles) < 10:
            return HOLD
        sobre = candles["close"].iloc[-1] > candles["close"].iloc[-10:].mean()
        if position is None and sobre:
            return BUY
        if position is not None and not sobre:
            return SELL
        return HOLD


class ComprarUnaVez(Strategy):
    lookback = 5

    def on_candle(self, candles, position):
        return BUY if position is None else HOLD


@pytest.fixture
def toy():
    return Toy("1h")
