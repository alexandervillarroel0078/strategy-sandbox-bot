"""Ejecución en el exchange simulado (--ejecucion exchange): el bot ENVÍA las órdenes por HTTP y el
exchange las ejecuta. El camino interno (broker.py + run_bot) no se toca; solo lo usan los tests como referencia.

ExchangeBroker tiene la interfaz de lectura de Broker (cash, pos, in_position, equity, trades, buy,
sell_market). Diferencias inevitables:
  - `buy` y `sell_market` ENVÍAN una orden de mercado; el exchange la llena en la apertura de la vela
    siguiente del reloj, así que el estado (cash, pos) se refresca con `sync` tras cada paso.
  - No hay `check_exit`: el exchange vigila stop y objetivo y genera la orden de salida él mismo.
Unidades: stop_pct y target_pct de Params son fracciones (0.05 = 5 %) y viajan tal cual como
stop_fraccion y take_profit_fraccion. El panel de reglas usa porcentajes: ver reglas.pct_a_fraccion.
"""
from __future__ import annotations

import pandas as pd

from .bot_client import ExchangeClient, ExchangeError, _origin, interval_ms
from .broker import Params, Position
from .registro import NULO, iso, registrar_decision, registrar_fills_exchange
from .sim_types import Audit, Result
from .strategies.base import BUY, HOLD, SELL, Strategy

TRADE_COLS = ["entry_time", "exit_time", "entry_price", "exit_price", "qty", "cost", "pnl",
              "return_pct", "fees", "reason"]


class ExchangeBroker:
    def __init__(self, client: ExchangeClient, p: Params):
        self.client, self.p = client, p
        self.cash = p.initial_balance
        self.pos: Position | None = None

    @property
    def in_position(self) -> bool:
        return self.pos is not None

    def sync(self, acct: dict) -> None:
        """Refresca saldo y posición con lo que informa el exchange ({saldo, posicion})."""
        self.cash = acct["saldo"]
        p = acct["posicion"]
        self.pos = None if p is None else Position(
            p["entrada"] // 1000, p["precio_entrada"], p["cantidad"],
            0.0 if p["stop"] is None else p["stop"], float("inf") if p["objetivo"] is None else p["objetivo"])

    def equity(self, price: float) -> float:
        return self.cash + (self.pos.qty * price if self.pos else 0.0)

    def buy(self, ref_price: float, ts: int) -> bool:
        """Envía una compra de mercado (fracción, stop y objetivo de Params). True si el exchange la aceptó."""
        r = self.client.order(lado="buy", tipo="market", fraccion=self.p.position_fraction,
                              stop_fraccion=self.p.stop_pct, take_profit_fraccion=self.p.target_pct)
        return r["estado"] == "NEW"

    def sell_market(self, ref_price: float, ts: int, reason: str) -> None:
        """Envía una venta de mercado. 'fin_simulacion' liquida al cierre de la última vela procesada."""
        r = self.client.order(lado="sell", tipo="market", liquidar=(reason == "fin_simulacion"))
        if r["estado"] == "REJECTED":
            raise ExchangeError(f"El exchange rechazó la venta: {r.get('motivo')}")
        if r["estado"] == "FILLED":
            a = self.client.account()
            self.sync({"saldo": a["saldo"], "posicion": a["posicion"]})

    @property
    def trades(self) -> list[dict]:
        """Operaciones cerradas, tal como las informa el exchange (tiempos en segundos, como Broker)."""
        return [{**t, "entry_time": t["entry_time"] // 1000, "exit_time": t["exit_time"] // 1000}
                for t in self.client.account()["trades"]]


def run_bot_exchange(client: ExchangeClient, strategy: Strategy, params: Params, clock: str,
                     start: pd.Timestamp, end: pd.Timestamp | None = None, reset: bool = False,
                     registro=NULO) -> Result:
    """Mismo ciclo que bot_client.run_bot, pero las órdenes las ejecuta el exchange.

    Cada decisión BUY/SELL se envía como orden en el instante "ahora"; el exchange la llena en la apertura
    de la vela siguiente del reloj y vigila stop/objetivo. Las métricas se arman con lo que devuelve el
    exchange. Por defecto NO resetea la sesión al terminar (para poder verla en el visor).
    """
    s_ms = int(start.timestamp() * 1000)
    e_ms = int(end.timestamp() * 1000) if end is not None else None   # exclusivo
    sims = interval_ms(strategy.timeframe)
    sorg = _origin(sims)
    cubo = lambda t: (t - sorg) // sims

    times, equity, prices = [], [], []
    decisions: list[tuple[int, str]] = []
    vacios: list[int] = []
    n_planas = 0
    first_open = last_close = 0.0

    now = client.start_session(s_ms, driver="bot")
    try:
        acct = client.account()
        costos = Params(acct["saldo_inicial"], acct["fee"], acct["slippage"],
                        params.stop_pct, params.target_pct, params.position_fraction)
        if (acct["saldo_inicial"], acct["fee"], acct["slippage"]) != \
                (params.initial_balance, params.fee, params.slippage):
            print(f"AVISO: el exchange usa saldo {acct['saldo_inicial']:g}, comisión {acct['fee']:.3%} y "
                  f"deslizamiento {acct['slippage']:.3%}; se ignoran los de config.yaml "
                  f"({params.initial_balance:g}, {params.fee:.3%}, {params.slippage:.3%}).")
        broker = ExchangeBroker(client, costos)
        vistos: set = set()                  # ids de fills ya registrados
        estado = (broker.cash, broker.pos)   # para detectar fills sin consultar en cada paso
        ultimo_cubo = cubo(now)
        previas = client.get_klines(strategy.timeframe, 1)
        ultima_vista = previas["open_time"].iloc[-1] if len(previas) else None

        while e_ms is None or now < e_ms:
            st = client.step(clock, 1)      # el exchange ejecuta órdenes pendientes y vigila stop/objetivo
            now = st.now
            if not st.candles:
                if st.end_of_data:
                    break
                vacios.append(now - interval_ms(clock))
                continue
            if st.account:
                broker.sync(st.account)
                if registro.activo and (broker.cash, broker.pos) != estado:
                    estado = (broker.cash, broker.pos)
                    registrar_fills_exchange(registro, client, vistos)
            for c in st.candles:
                if c.open_ms < s_ms or (e_ms is not None and c.open_ms >= e_ms):
                    continue
                ct = c.close_ms // 1000
                if not times:
                    first_open = c.open
                last_close = c.close
                n_planas += int(c.volume == 0 and c.open == c.high == c.low == c.close)
                times.append(ct)
                equity.append(broker.equity(c.close))
                prices.append(c.close)
                if cubo(c.close_ms) != ultimo_cubo:
                    ultimo_cubo = cubo(c.close_ms)
                    velas = client.get_klines(strategy.timeframe, strategy.lookback)
                    if len(velas) and velas["open_time"].iloc[-1] != ultima_vista:
                        ultima_vista = velas["open_time"].iloc[-1]
                        accion = strategy.on_candle(velas, broker.pos)
                        if accion not in (BUY, SELL, HOLD):
                            raise ValueError(f"Acción inválida de la estrategia: {accion!r}")
                        decisions.append((ct, accion))
                        registrar_decision(registro, now, accion, strategy, velas, broker.pos)
                        if accion == BUY and not broker.in_position:
                            broker.buy(c.close, ct)
                        elif accion == SELL and broker.in_position:
                            broker.sell_market(c.close, ct, "señal")
            if st.end_of_data:
                break

        if not times:
            raise ValueError("No hay datos en el rango pedido")
        if broker.in_position:
            if registro.activo:
                registro.evento("decision", ahora=now, ahora_iso=iso(now), accion=SELL, motivo="fin_simulacion",
                                en_posicion=True, valores={"precio": last_close})
            broker.sell_market(last_close, times[-1], "fin_simulacion")
            equity[-1] = broker.cash
            registrar_fills_exchange(registro, client, vistos)
        trades = pd.DataFrame(broker.trades, columns=TRADE_COLS)
    finally:
        if reset:
            client.reset_session()

    for col in ("entry_time", "exit_time"):
        trades[col] = pd.to_datetime(trades[col], unit="s", utc=True)
    eq = pd.DataFrame({"time": pd.to_datetime(times, unit="s", utc=True), "equity": equity, "price": prices})

    aud = Audit(n_present=len(times), n_missing=len(vacios), n_flat=n_planas)
    cms = interval_ms(clock)
    for t in vacios:   # tramos de huecos consecutivos
        if aud.gaps and aud.gaps[-1][1] == pd.Timestamp(t - cms, unit="ms", tz="UTC"):
            a, _, n = aud.gaps[-1]
            aud.gaps[-1] = (a, pd.Timestamp(t, unit="ms", tz="UTC"), n + 1)
        else:
            aud.gaps.append((pd.Timestamp(t, unit="ms", tz="UTC"), pd.Timestamp(t, unit="ms", tz="UTC"), 1))
    return Result(trades, eq, decisions, costos, aud, aud.warnings(), first_open, last_close)
