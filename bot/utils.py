"""Utilidades de línea de comandos del bot (también las usan las herramientas del exchange)."""
from __future__ import annotations

import importlib
import inspect
import os

import pandas as pd
import yaml

from .strategies.base import Strategy

PKG_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(PKG_DIR)                  # raíz del proyecto (btc/)
CONFIG = os.path.join(PKG_DIR, "config.yaml")


def load_config(path: str | None = None) -> dict:
    with open(path or CONFIG, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if os.environ.get("EXCHANGE_URL") and isinstance(cfg, dict) and "api_url" in cfg:
        cfg["api_url"] = os.environ["EXCHANGE_URL"]      # la variable de entorno manda sobre api_url
    return cfg


def resolver_ruta(ruta: str) -> str:
    """Ruta relativa -> absoluta desde la raíz del proyecto (no desde el directorio actual)."""
    return ruta if os.path.isabs(ruta) else os.path.join(ROOT, ruta)


def parse_ts(s, fin: bool = False) -> pd.Timestamp:
    """Fecha -> Timestamp UTC. Una fecha sin hora usada como fin incluye ese día completo."""
    txt = str(s).strip()
    ts = pd.Timestamp(txt)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return ts + pd.Timedelta(days=1) if fin and len(txt) <= 10 else ts


def load_strategy(nombre: str, timeframe: str) -> Strategy:
    mod = importlib.import_module(f"bot.strategies.{nombre}")
    for _, cls in inspect.getmembers(mod, inspect.isclass):
        if issubclass(cls, Strategy) and cls is not Strategy and cls.__module__ == mod.__name__:
            return cls(timeframe)
    raise SystemExit(f"bot/strategies/{nombre}.py no define ninguna subclase de Strategy")
