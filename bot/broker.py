"""Billetera simulada: spot, solo largos, una posición a la vez."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Position:
    """Vista de solo lectura de la posición abierta (se entrega a la estrategia)."""
    entry_time: int  # epoch en segundos
    entry_price: float
    qty: float
    stop: float
    target: float


@dataclass
class Params:
    initial_balance: float = 10_000.0
    fee: float = 0.001
    slippage: float = 0.0005
    stop_pct: float = 0.05
    target_pct: float = 0.10
    position_fraction: float = 1.0


class Broker:
    def __init__(self, p: Params):
        self.p = p
        self.cash = p.initial_balance
        self.pos: Position | None = None
        self._cost = 0.0  # USDT gastados en la entrada (incluye comisión)
        self.trades: list[dict] = []

    @property
    def in_position(self) -> bool:
        return self.pos is not None

    def equity(self, price: float) -> float:
        return self.cash + (self.pos.qty * price if self.pos else 0.0)

    def buy(self, ref_price: float, ts: int) -> bool:
        """Compra a mercado con deslizamiento; gasta fracción*caja (comisión incluida)."""
        spend = self.cash * self.p.position_fraction
        if self.pos or spend <= 0:
            return False
        px = ref_price * (1 + self.p.slippage)
        qty = spend / (px * (1 + self.p.fee))
        self.cash -= spend
        self._cost = spend
        self.pos = Position(ts, px, qty, px * (1 - self.p.stop_pct), px * (1 + self.p.target_pct))
        return True

    def _close(self, px: float, ts: int, reason: str) -> None:
        pos = self.pos
        proceeds = pos.qty * px * (1 - self.p.fee)
        pnl = proceeds - self._cost
        self.cash += proceeds
        self.trades.append({
            "entry_time": pos.entry_time, "exit_time": ts,
            "entry_price": pos.entry_price, "exit_price": px, "qty": pos.qty,
            "cost": self._cost, "pnl": pnl, "return_pct": pnl / self._cost * 100,
            "fees": pos.qty * (pos.entry_price + px) * self.p.fee, "reason": reason,
        })
        self.pos = None

    def sell_market(self, ref_price: float, ts: int, reason: str) -> None:
        """Venta a mercado: el deslizamiento juega en contra."""
        self._close(ref_price * (1 - self.p.slippage), ts, reason)

    def check_exit(self, o: float, h: float, l: float, ts: int) -> bool:
        """Revisa stop/objetivo con high/low. Si se tocan ambos, gana el stop."""
        pos = self.pos
        if l <= pos.stop:
            # stop de mercado: si la vela abre por debajo, se ejecuta a la apertura
            self.sell_market(min(pos.stop, o), ts, "stop")
            return True
        if h >= pos.target:
            # orden límite: sin deslizamiento; si abre por encima, se ejecuta a la apertura
            self._close(max(pos.target, o), ts, "target")
            return True
        return False
