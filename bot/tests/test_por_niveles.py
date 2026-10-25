"""Estrategia por_niveles: señales sin mirar el futuro y campos pendientes ignorados con aviso."""
import pandas as pd
import pytest
import yaml

from bot.broker import Position
from bot.reglas import ReglasInvalidas
from bot.strategies.base import BUY, HOLD, SELL
from bot.strategies.por_niveles import PorNiveles

LIM = {"riesgo_max_pct": 2, "stop_max_pct": 10, "parcial_min_pct": 10, "parcial_max_pct": 90,
       "max_zonas": 10, "max_reglas_activas": 10}
CFG = {"stop_pct": 0.05, "target_pct": 0.10, "position_fraction": 1.0}
ZONA = {"nombre": "S1", "tipo": "soporte", "desde": 100, "hasta": 105, "activa": True}
POS = Position(0, 100.0, 1.0, 95.0, 110.0)


def hacer(tmp_path, reglas, zonas=(ZONA,), activo=True):
    (tmp_path / "r.yaml").write_text(yaml.safe_dump({"activo": activo, "zonas": list(zonas), "reglas": reglas}))
    (tmp_path / "l.yaml").write_text(yaml.safe_dump(LIM))
    (tmp_path / "c.yaml").write_text(yaml.safe_dump(CFG))
    return PorNiveles("1h", str(tmp_path / "r.yaml"), str(tmp_path / "l.yaml"), str(tmp_path / "c.yaml"))


def velas(cierres, lows=None, highs=None):
    n = len(cierres)
    return pd.DataFrame({
        "open_time": pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC"),
        "open": cierres, "high": highs or [c + 1 for c in cierres], "low": lows or [c - 1 for c in cierres],
        "close": cierres, "volume": 1.0,
        "close_time": pd.date_range("2025-01-01 01:00", periods=n, freq="1h", tz="UTC")})


def primera_senal(s, df, position=None):
    """Alimenta la estrategia vela a vela (solo el pasado) y devuelve (índice, acción) de la 1ª no-HOLD."""
    for i in range(len(df)):
        a = s.on_candle(df.iloc[:i + 1], position)
        if a != HOLD:
            return i, a
    return None, HOLD


# Precio sobre 120, cae al soporte 100-105: la vela 40 toca y cierra DENTRO (sin confirmar),
# la 41 toca y cierra por ENCIMA (confirma). Después sigue arriba (no vuelve a tocar).
CIERRES = [120.0] * 38 + [110.0, 103.0, 103.0, 107.0, 112.0, 115.0]
LOWS = [c - 1 for c in CIERRES]
LOWS[40] = 101.0   # toca la zona
LOWS[41] = 102.0   # toca la zona
LOWS[38] = 109.0
LOWS[39] = 102.0   # cierra en 103, dentro de la zona: toca pero no confirma


def test_soporte_con_confirmacion_dispara_en_la_vela_correcta(tmp_path):
    s = hacer(tmp_path, [{"zona": "S1", "accion": "abrir_long", "stop_pct": 3,
                          "confirmaciones": {"cierre_fuera_zona": "arriba"}}])
    df = velas(CIERRES, lows=LOWS)
    assert primera_senal(s, df) == (41, BUY)
    # sin confirmación la regla dispara antes (en la primera vela que toca la zona): la confirmación retrasa
    s2 = hacer(tmp_path, [{"zona": "S1", "accion": "abrir_long", "stop_pct": 3}])
    assert primera_senal(s2, df)[0] == 39


def test_no_mira_el_futuro(tmp_path):
    s = hacer(tmp_path, [{"zona": "S1", "accion": "abrir_long", "stop_pct": 3,
                          "confirmaciones": {"cierre_fuera_zona": "arriba"}}])
    df = velas(CIERRES, lows=LOWS)
    # con la ventana cortada en la vela 40 no hay señal, aunque la vela 41 (futuro) la daría
    assert s.on_candle(df.iloc[:41], None) == HOLD
    assert s.on_candle(df.iloc[:42], None) == BUY
    # mutar velas futuras no cambia la decisión sobre la ventana pasada
    futuro = df.copy()
    futuro.loc[41:, ["open", "high", "low", "close"]] = 1.0
    assert s.on_candle(futuro.iloc[:41], None) == HOLD


def test_confirmacion_rsi_y_ema(tmp_path):
    conf = {"cierre_fuera_zona": "arriba", "rsi": {"op": "mayor", "valor": 99}}
    df = velas(CIERRES, lows=LOWS)
    s = hacer(tmp_path, [{"zona": "S1", "accion": "abrir_long", "stop_pct": 3, "confirmaciones": conf}])
    assert primera_senal(s, df)[1] == HOLD          # RSI nunca > 99 en el rebote
    ema = df["close"].iloc[:42].ewm(span=5, adjust=False).mean().iloc[-1]
    assert df["close"].iloc[41] < ema               # en la vela 41 el cierre está bajo la EMA(5)
    for op, esperado in (("bajo", (41, BUY)), ("sobre", (None, HOLD))):
        conf = {"cierre_fuera_zona": "arriba", "ema": {"op": op, "n": 5}}
        s = hacer(tmp_path, [{"zona": "S1", "accion": "abrir_long", "stop_pct": 3, "confirmaciones": conf}])
        assert primera_senal(s, df) == esperado


def test_cerrar_long_solo_con_posicion(tmp_path):
    s = hacer(tmp_path, [{"zona": "S1", "accion": "cerrar_long", "stop_pct": 3}])
    df = velas(CIERRES, lows=LOWS).iloc[:40]
    assert s.on_candle(df, None) == HOLD
    assert s.on_candle(df, POS) == SELL


def test_abrir_long_no_compra_con_posicion(tmp_path):
    s = hacer(tmp_path, [{"zona": "S1", "accion": "abrir_long", "stop_pct": 3}])
    df = velas(CIERRES, lows=LOWS).iloc[:40]
    assert s.on_candle(df, POS) == HOLD


def test_interruptor_y_zonas_reglas_inactivas(tmp_path):
    regla = {"zona": "S1", "accion": "abrir_long", "stop_pct": 3}
    df = velas(CIERRES, lows=LOWS).iloc[:40]
    assert hacer(tmp_path, [regla], activo=False).on_candle(df, None) == HOLD
    assert hacer(tmp_path, [regla], zonas=[{**ZONA, "activa": False}]).on_candle(df, None) == HOLD
    assert hacer(tmp_path, [{**regla, "activa": False}]).on_candle(df, None) == HOLD
    assert hacer(tmp_path, [regla]).on_candle(df, None) == BUY


def test_pendientes_se_ignoran_con_aviso(tmp_path, capsys):
    reglas = [{"zona": "S1", "accion": "abrir_long", "stop_pct": 3, "riesgo_pct": 1,
               "parcial": {"porcentaje": 50, "condicion": {"tipo": "ema", "n": 20}}},
              {"zona": "S1", "accion": "abrir_short", "stop_pct": 3}]
    s = hacer(tmp_path, reglas)
    out = capsys.readouterr().out
    assert "stop_pct=3 % IGNORADO" in out and "stop global de 5 %" in out
    assert "riesgo_pct=1 % IGNORADO" in out and "position_fraction=100 %" in out
    assert "parcial=50 % IGNORADO" in out
    assert "abrir_short" in out and "IGNORADA ENTERA" in out
    # el short nunca produce una orden: ni BUY ni SELL sustituto
    s = hacer(tmp_path, [reglas[1]])
    df = velas(CIERRES, lows=LOWS).iloc[:40]
    assert s.on_candle(df, None) == HOLD and s.on_candle(df, POS) == HOLD
    assert "abrir_short NO está soportado" in capsys.readouterr().out


def test_solo_avisar_imprime_y_no_opera(tmp_path, capsys):
    s = hacer(tmp_path, [{"zona": "S1", "accion": "solo_avisar"}])
    capsys.readouterr()
    assert s.on_candle(velas(CIERRES, lows=LOWS).iloc[:40], None) == HOLD
    assert "AVISO: se cumple la regla 1" in capsys.readouterr().out


def test_archivo_invalido_no_arranca(tmp_path):
    with pytest.raises(ReglasInvalidas, match="stop_pct"):
        hacer(tmp_path, [{"zona": "S1", "accion": "abrir_long", "stop_pct": 50}])
    with pytest.raises(ReglasInvalidas, match="no existe"):
        hacer(tmp_path, [{"zona": "X", "accion": "abrir_long", "stop_pct": 3}])
