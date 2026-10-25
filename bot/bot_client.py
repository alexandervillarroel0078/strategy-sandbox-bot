"""Cliente de bot: consume la API del mercado simulado como si fuera un exchange.

Solo usa klines, step, session/start y session/reset. No conoce los datos del CSV ni el rango
disponible (nunca llama a /api/info): lo único que ve son velas cerradas hasta el "ahora".
"""
from __future__ import annotations

import http.client
import json
import os
import re
from typing import NamedTuple
from urllib.parse import urlparse

import numpy as np
import pandas as pd

from .broker import Broker, Params
from .registro import NULO, iso as _iso, permitir, registrar_decision, registrar_fills_internos
from .sim_types import Audit, Result
from .strategies.base import BUY, HOLD, SELL, Strategy

# Lista blanca: cualquier otra ruta (por ejemplo /api/info o /api/session) se rechaza en el cliente.
ALLOWED = {("GET", "/api/klines"), ("POST", "/api/step"),
           ("POST", "/api/session/start"), ("POST", "/api/session/reset"),
           ("POST", "/api/order"), ("GET", "/api/orders"), ("GET", "/api/account")}
COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time"]
_UNIT_MS = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
_WEEK_ORIGIN_MS = 345_600_000   # lunes 1970-01-05, para alinear las velas semanales


class ExchangeError(RuntimeError):
    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        self.status = status


class LeakError(ExchangeError):
    """El servidor entregó una vela con cierre posterior al 'ahora' (no debería pasar nunca)."""


class Candle(NamedTuple):
    open_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_ms: int   # instante de cierre (exclusivo) = open + intervalo


class Step(NamedTuple):
    now: int
    end_of_data: bool
    candles: list[Candle]
    account: dict | None = None   # saldo y posición tras el paso (solo si el exchange los informa)


def interval_ms(interval: str) -> int:
    m = re.fullmatch(r"(\d{1,4})([mhdw])", interval)
    if not m:
        raise ValueError(f"Intervalo inválido: {interval!r}")
    return int(m[1]) * _UNIT_MS[m[2]]


def _origin(ims: int) -> int:
    return _WEEK_ORIGIN_MS if ims % _UNIT_MS["w"] == 0 else 0


URL_DEFECTO = "http://127.0.0.1:8001"     # EXCHANGE_URL lo cambia


class ExchangeClient:
    def __init__(self, base_url: str | None = None, timeout: float = 60.0, registro=NULO):
        base_url = base_url or os.environ.get("EXCHANGE_URL") or URL_DEFECTO
        u = urlparse(base_url)
        self.base_url, self.host, self.port, self.timeout = base_url, u.hostname, u.port or 80, timeout
        self.now: int | None = None       # "ahora" simulado, según la última respuesta
        self.n_requests = 0
        self.candles_checked = 0          # velas verificadas contra el "ahora"
        self.registro = registro          # solo escritura: no influye en peticiones ni decisiones

    # ---- transporte ----
    def _request(self, method: str, path: str, body: dict | None = None, query: str = "") -> dict | list:
        if (method, path) not in ALLOWED:
            if self.registro.activo:
                self.registro.evento("error", metodo=method, ruta=path, clase="RutaNoPermitida")
            raise ExchangeError(f"Ruta no permitida para el cliente: {method} {path}")
        self.n_requests += 1
        reg = self.registro
        es_orden = reg.activo and path == "/api/order"
        if es_orden:
            reg.evento("orden_enviada", **{"ahora": self.now, "ahora_iso": _iso(self.now), "metodo": method,
                                           "ruta": path, **permitir(body)})
        elif reg.activo and path not in ("/api/step", "/api/klines"):   # esas dos ya llevan su ruta en su evento
            reg.evento("consulta", metodo=method, ruta=path, ahora=self.now)
        conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            conn.request(method, path + query, headers={"Content-Type": "application/json"},
                         body=None if body is None else json.dumps(body))
            r = conn.getresponse()
            raw = r.read()
        except OSError as e:
            if reg.activo:
                reg.evento("error", metodo=method, ruta=path, clase=type(e).__name__)
            raise ExchangeError(f"No puedo conectar con {self.base_url}. "
                                "Arranca primero el exchange (repo strategy-sandbox-exchange, ver su README)") from e
        finally:
            conn.close()
        data = json.loads(raw)
        if es_orden:
            reg.evento("orden_respuesta", **{"status": r.status, **permitir(data)})
        if r.status >= 400:
            if reg.activo:
                reg.evento("error", metodo=method, ruta=path, status=r.status)
            raise ExchangeError(data.get("error", f"HTTP {r.status}"), r.status)
        return data

    def _verificar(self, rows: list[list], now: int) -> None:
        """Defensa en el cliente: ninguna vela puede cerrar después del 'ahora'."""
        for r in rows:
            if r[6] + 1 > now:
                raise LeakError(f"Vela recibida con cierre {r[6] + 1} posterior al ahora {now}")
        self.candles_checked += len(rows)

    def _registrar_klines(self, interval: str, rows: list, now: int) -> None:
        reg = self.registro
        reg.evento("klines", metodo="GET", ruta="/api/klines", ahora=now, ahora_iso=_iso(now), intervalo=interval, n=len(rows),
                   primer_open_time=rows[0][0] if rows else None, ultimo_open_time=rows[-1][0] if rows else None,
                   primer_close_time=rows[0][6] if rows else None, ultimo_close_time=rows[-1][6] if rows else None,
                   ok=all(r[6] + 1 <= now for r in rows))
        if reg.nivel == "completo":
            reg.feed(now, "klines", interval, rows)

    def _registrar_step(self, interval: str, j: dict) -> None:
        reg, rows, now = self.registro, j["candles"], j["now"]
        reg.evento("step", metodo="POST", ruta="/api/step", ahora=now, ahora_iso=_iso(now), intervalo=interval, velas_nuevas=len(rows),
                   ultimo_close_time=rows[-1][6] if rows else None,
                   ok=all(r[6] + 1 <= now for r in rows), fin_datos=j["end_of_data"])
        if reg.nivel == "completo":
            reg.feed(now, "step", interval, rows)

    # ---- API del exchange ----
    def start_session(self, start_ms: int, driver: str | None = None) -> int:
        body = {"start": int(start_ms)} if driver is None else {"start": int(start_ms), "driver": driver}
        self.now = self._request("POST", "/api/session/start", body)["now"]
        return self.now

    def reset_session(self) -> None:
        self._request("POST", "/api/session/reset", {})
        self.now = None

    def get_klines(self, interval: str, limit: int = 500) -> pd.DataFrame:
        """Últimas `limit` velas cerradas. close_time = open_time + intervalo (como data.py)."""
        if self.now is None:
            raise ExchangeError("No hay sesión iniciada")
        rows = self._request("GET", "/api/klines", query=f"?interval={interval}&limit={int(limit)}")
        try:
            self._verificar(rows, self.now)
        finally:
            if self.registro.activo:
                self._registrar_klines(interval, rows, self.now)
        if not rows:
            return pd.DataFrame(columns=COLS)
        c = list(zip(*rows))
        num = lambda i: np.array(c[i], dtype="float64")   # los precios llegan como texto, como en Binance
        return pd.DataFrame({
            "open_time": pd.to_datetime(np.array(c[0], dtype="int64"), unit="ms", utc=True),
            "open": num(1), "high": num(2), "low": num(3), "close": num(4), "volume": num(5),
            "close_time": pd.to_datetime(np.array(c[6], dtype="int64") + 1, unit="ms", utc=True),
        })

    def step(self, interval: str, candles: int = 1) -> Step:
        """Avanza el reloj `candles` velas de `interval`; devuelve solo las velas cerradas nuevas."""
        j = self._request("POST", "/api/step", {"interval": interval, "candles": int(candles)})
        try:
            self._verificar(j["candles"], j["now"])
        finally:
            if self.registro.activo:
                self._registrar_step(interval, j)
        self.now = j["now"]
        return Step(j["now"], j["end_of_data"], [
            Candle(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), r[6] + 1)
            for r in j["candles"]], j.get("account"))

    # ---- órdenes y cuenta (solo con --ejecucion exchange) ----
    def order(self, **orden) -> dict:
        """Envía una orden de mercado. Responde {id, estado, ahora[, motivo]}."""
        return self._request("POST", "/api/order", orden)

    def orders(self) -> list[dict]:
        return self._request("GET", "/api/orders")["ordenes"]

    def account(self) -> dict:
        return self._request("GET", "/api/account")


def run_bot(client: ExchangeClient, strategy: Strategy, params: Params, clock: str,
            start: pd.Timestamp, end: pd.Timestamp | None = None, registro=NULO) -> Result:
    """Ciclo de referencia con Broker interno (solo lo usan los tests), pero alimentado solo por la API.

    clock: temporalidad fina del paso (/api/step); en cada vela fina: (1) ejecuta la orden pendiente
    en su apertura, (2) revisa stop/objetivo con high/low, (3) si cerró una vela de la estrategia,
    pide las últimas `lookback` velas con klines y llama a la estrategia.
    """
    s_ms = int(start.timestamp() * 1000)
    e_ms = int(end.timestamp() * 1000) if end is not None else None   # exclusivo
    sims = interval_ms(strategy.timeframe)
    sorg = _origin(sims)
    cubo = lambda t: (t - sorg) // sims   # nº de vela de la estrategia que contiene t

    broker = Broker(params)
    times, equity, prices = [], [], []
    decisions: list[tuple[int, str]] = []
    vacios: list[int] = []        # aperturas de velas finas que no llegaron (huecos)
    n_planas = 0
    first_open = last_close = 0.0
    pendiente: str | None = None

    now = client.start_session(s_ms)
    try:
        ultimo_cubo = cubo(now)
        previas = client.get_klines(strategy.timeframe, 1)
        ultima_vista = previas["open_time"].iloc[-1] if len(previas) else None

        while e_ms is None or now < e_ms:
            st = client.step(clock, 1)
            now = st.now
            if not st.candles:
                if st.end_of_data:
                    break
                vacios.append(now - interval_ms(clock))   # el paso avanzó pero no hubo vela: hueco
                continue
            for c in st.candles:
                if c.open_ms < s_ms or (e_ms is not None and c.open_ms >= e_ms):
                    continue
                ot, ct = c.open_ms // 1000, c.close_ms // 1000
                if not times:
                    first_open = c.open
                last_close = c.close
                n_planas += int(c.volume == 0 and c.open == c.high == c.low == c.close)

                en_pos, n_tr = broker.in_position, len(broker.trades)   # foto para el registro (solo lectura)
                # (1) orden pendiente: se ejecuta en la apertura de esta vela
                if pendiente == BUY:
                    broker.buy(c.open, ot)
                elif pendiente == SELL and broker.in_position:
                    broker.sell_market(c.open, ot, "señal")
                pendiente = None
                # (2) stop / objetivo
                if broker.in_position:
                    broker.check_exit(c.open, c.high, c.low, ct)
                registrar_fills_internos(registro, broker, en_pos, n_tr, now, params.fee)
                # (3) el reloj avanza
                times.append(ct)
                equity.append(broker.equity(c.close))
                prices.append(c.close)
                # (4) ¿cerró una vela de la estrategia?
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
                            pendiente = BUY
                        elif accion == SELL and broker.in_position:
                            pendiente = SELL
            if st.end_of_data:
                break
    finally:
        client.reset_session()

    if not times:
        raise ValueError("No hay datos en el rango pedido")
    if broker.in_position:
        broker.sell_market(last_close, times[-1], "fin_simulacion")
        equity[-1] = broker.cash

    trades = pd.DataFrame(broker.trades, columns=[
        "entry_time", "exit_time", "entry_price", "exit_price", "qty", "cost", "pnl",
        "return_pct", "fees", "reason"])
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
    return Result(trades, eq, decisions, params, aud, aud.warnings(), first_open, last_close)
