"""Estrategia por niveles: ejecuta las reglas de bot/reglas.yaml (se editan con el panel).

AVISO: los niveles dibujados hoy y probados sobre el pasado no son una prueba válida (sesgo de
retrospectiva). Úsala solo para comprobar que el código funciona y para pruebas hacia adelante.

Acciones soportadas: abrir_long (BUY), cerrar_long (SELL), solo_avisar (imprime y HOLD).
abrir_short, stop_pct por regla, parcial y riesgo_pct se guardan pero se IGNORAN (el Broker no los
soporta): al arrancar se imprime qué se ignora y qué valores reales se usan.

Una regla se dispara con la ÚLTIMA vela cerrada recibida si: toca la zona (low <= hasta y high >= desde)
y se cumplen todas sus confirmaciones. Solo usa velas cerradas, nunca datos futuros.
"""
from __future__ import annotations

import pandas as pd

from bot.broker import Position
from bot.reglas import cargar_limites, cargar_reglas, pendientes
from bot.strategies.base import BUY, HOLD, SELL, Strategy
from bot.utils import load_config

RSI_N = 14
PREF = "[por_niveles]"


def rsi(close: pd.Series, n: int = RSI_N) -> pd.Series:
    """RSI de Wilder, calculado solo con la ventana recibida."""
    d = close.diff()
    gan = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    per = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + gan / per)


class PorNiveles(Strategy):
    lookback = 600

    def __init__(self, timeframe: str = "1h", reglas_path: str | None = None,
                 limites_path: str | None = None, config_path: str | None = None):
        super().__init__(timeframe)
        # Si el archivo es inválido lanza ReglasInvalidas con el motivo: el bot no arranca.
        self.reglas = cargar_reglas(reglas_path, cargar_limites(limites_path))
        self.zonas = {z["nombre"].strip().casefold(): z for z in self.reglas["zonas"]}
        self._warmup = max([2 * n for n in self._ema_usadas()] + [RSI_N * 3 for _ in self._rsi_usado()] + [1])
        print(f"{PREF} reglas.yaml cargado: " + ("ACTIVO" if self.reglas["activo"] else "INACTIVO (activo: false)")
              + f" · {len(self.reglas['zonas'])} zonas · {len(self.reglas['reglas'])} reglas")
        ign = pendientes(self.reglas, load_config(config_path))
        if ign:
            print(f"{PREF} AVISO: campos de reglas.yaml que se están IGNORANDO (pendiente: no soportado aún por el Broker):")
            for linea in ign:
                print(f"{PREF}   - {linea}")

    def _ema_usadas(self):
        for r in self.reglas["reglas"]:
            e = (r.get("confirmaciones") or {}).get("ema")
            if e:
                yield int(e["n"])

    def _rsi_usado(self):
        for r in self.reglas["reglas"]:
            if (r.get("confirmaciones") or {}).get("rsi"):
                yield True

    def _dispara(self, r: dict, z: dict, candles: pd.DataFrame) -> bool:
        ult = candles.iloc[-1]
        if not (ult["low"] <= z["hasta"] and ult["high"] >= z["desde"]):
            return False
        c = r.get("confirmaciones") or {}
        fuera = c.get("cierre_fuera_zona")
        if fuera == "arriba" and not ult["close"] > z["hasta"]:
            return False
        if fuera == "abajo" and not ult["close"] < z["desde"]:
            return False
        if c.get("rsi"):
            v = rsi(candles["close"]).iloc[-1]
            if not (v > c["rsi"]["valor"] if c["rsi"]["op"] == "mayor" else v < c["rsi"]["valor"]):
                return False
        if c.get("ema"):
            e = candles["close"].ewm(span=int(c["ema"]["n"]), adjust=False).mean().iloc[-1]
            if not (ult["close"] > e if c["ema"]["op"] == "sobre" else ult["close"] < e):
                return False
        return True

    def on_candle(self, candles: pd.DataFrame, position: Position | None) -> str:
        if not self.reglas["activo"] or len(candles) < self._warmup:
            return HOLD
        cuando = candles["open_time"].iloc[-1]
        accion = HOLD
        for i, r in enumerate(self.reglas["reglas"]):
            z = self.zonas[r["zona"].strip().casefold()]
            if r.get("activa", True) is False or z.get("activa", True) is False:
                continue
            if not self._dispara(r, z, candles):
                continue
            et = f"regla {i + 1} (zona {z['nombre']!r}) en la vela {cuando}"
            if r["accion"] == "solo_avisar":
                print(f"{PREF} AVISO: se cumple la {et}")
            elif r["accion"] == "abrir_short":
                print(f"{PREF} AVISO: se cumple la {et}, pero abrir_short NO está soportado: se ignora")
            elif r["accion"] == "abrir_long" and position is None and accion == HOLD:
                accion = BUY
            elif r["accion"] == "cerrar_long" and position is not None and accion == HOLD:
                accion = SELL
        return accion
