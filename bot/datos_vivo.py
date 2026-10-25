"""Lectura en vivo (solo lectura) de lo que recibe el bot, para la sección "Datos recibidos" del panel.

Lee únicamente el FINAL de bot/logs/ultimo.jsonl y del feed_*.csv que nombra su evento `inicio`; nunca los
archivos enteros. Devuelve solo campos fijos (lista blanca) y ignora un registro más antiguo que el arranque
del bot lanzado desde el panel (así no se ven datos de una corrida anterior como si fueran en vivo).
"""
from __future__ import annotations

import csv
import json
import math
import os
import re

from .registro import LOGS_DIR, iso

N = 10                          # filas por tabla
BLOQUE = 64 * 1024              # primera lectura desde el final
MAX_JSONL = 1024 * 1024         # tope de lectura del .jsonl (una estrategia diaria necesita ~250 KB)
MAX_FEED = 256 * 1024
MAX_INICIO = 64 * 1024
N_VELAS = 200                   # velas del gráfico
MAX_LOTE = 500                  # velas por respuesta cuando el gráfico pide las posteriores a una marca
MAX_FEED_VELAS = 1024 * 1024    # tope de lectura del feed para el gráfico
_RE_FEED = re.compile(r"feed_\d{8}-\d{6}\.csv")
_RE_INTERVALO = re.compile(r"\d{1,4}[mhdw]")
_VALORES = ("precio", "rsi", "ema50", "ema200")
SIN_DATOS = {"estado": "sin_datos", "ahora_iso": None, "velas": [], "decisiones": []}
SIN_VELAS = {"estado": "sin_datos", "intervalo": None, "intervalos": [], "corrida": None, "ahora_iso": None,
             "ultima_t": None, "restantes": 0, "hay_mas": False, "reinicio": False, "velas": []}


def _cola(ruta: str, max_bytes: int, bastan) -> list[bytes]:
    """Líneas completas del final del archivo. Crece hacia atrás hasta que `bastan(lineas)` o el tope."""
    with open(ruta, "rb") as f:
        tam = f.seek(0, os.SEEK_END)
        n = BLOQUE
        while True:
            leido = min(n, tam, max_bytes)
            f.seek(tam - leido)
            lineas = f.read(leido).split(b"\n")
            entero = leido >= tam
            if not entero:
                lineas = lineas[1:]      # la primera puede estar cortada
            if entero or leido >= max_bytes or bastan(lineas):
                return [x for x in lineas if x.strip()]
            n *= 2


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _json(linea: bytes):
    try:
        e = json.loads(linea)
    except ValueError:               # línea a medio escribir
        return None
    return e if isinstance(e, dict) else None


def _inicio(ruta_jsonl: str) -> dict | None:
    """El evento `inicio` (primera línea)."""
    with open(ruta_jsonl, "rb") as f:
        e = _json(f.readline(MAX_INICIO))
    return e if e and e.get("evento") == "inicio" else None


def _nombre_feed(ruta_jsonl: str) -> str | None:
    """El feed lo dice el evento `inicio`."""
    e = _inicio(ruta_jsonl)
    nombre = e.get("feed") if e else None
    return nombre if isinstance(nombre, str) and _RE_FEED.fullmatch(nombre) else None


def _decisiones(eventos: list[dict]) -> list[dict]:
    out = []
    for e in eventos:
        if e.get("evento") != "decision":
            continue
        v = e.get("valores") if isinstance(e.get("valores"), dict) else {}
        motivo = e.get("motivo")
        out.append({"ahora_iso": iso(_num(e.get("ahora"))), "accion": str(e.get("accion"))[:10],
                    "motivo": motivo[:120] if isinstance(motivo, str) else None,
                    **{k: _num(v.get(k)) for k in _VALORES}})
    return out[-N:]


def _velas(ruta_feed: str) -> list[dict]:
    out = []
    for fila in csv.reader(x.decode("utf-8", "replace") for x in
                           _cola(ruta_feed, MAX_FEED, lambda ls: len(ls) > N + 2)):
        try:
            ahora, ot = int(fila[0]), int(fila[3])
            ohlc = [float(x) for x in fila[4:8]]
        except (ValueError, IndexError):
            continue                 # cabecera, fila cortada o corrupta
        if fila[1] not in ("step", "klines") or len(ohlc) != 4:
            continue                 # fuente rara o fila final cortada
        out.append({"ahora_iso": iso(ahora), "hora": iso(ot), "fuente": fila[1],
                    "intervalo": fila[2] if _RE_INTERVALO.fullmatch(fila[2]) else None,
                    "open": ohlc[0], "high": ohlc[1], "low": ohlc[2], "close": ohlc[3]})
    return out[-N:]


def leer(logs_dir: str | None, desde: float | None) -> dict:
    """`desde`: hora (time.time) de arranque del bot lanzado desde el panel; None = no se ha lanzado ninguno."""
    if desde is None:
        return dict(SIN_DATOS)
    ruta = os.path.join(logs_dir or LOGS_DIR, "ultimo.jsonl")
    try:
        if os.stat(ruta).st_mtime < desde:
            return dict(SIN_DATOS)   # log de una corrida anterior
        feed = _nombre_feed(ruta)
        velas = _velas(os.path.join(os.path.dirname(ruta), feed)) if feed else []   # antes que el log: el "ahora" nunca queda por detrás
        eventos = [e for e in map(_json, _cola(ruta, MAX_JSONL, lambda ls: sum(
            b'"evento": "decision"' in x for x in ls) >= N)) if e]
    except OSError:
        return dict(SIN_DATOS)
    ahoras = [_num(e.get("ahora")) for e in eventos if e.get("evento") in ("step", "klines", "decision")]
    ahoras = [a for a in ahoras if a is not None]
    if not eventos:
        return dict(SIN_DATOS)
    return {"estado": "ok", "ahora_iso": iso(ahoras[-1]) if ahoras else None,
            "velas": velas, "decisiones": _decisiones(eventos)}


def _intervalo(v) -> str | None:
    return v if isinstance(v, str) and _RE_INTERVALO.fullmatch(v) else None


def velas(logs_dir: str | None, desde: float | None, intervalo: str | None = None, desde_t: int | None = None) -> dict:
    """Velas de un intervalo del feed de la corrida actual, para el gráfico.

    Sin `desde_t`: las últimas N_VELAS velas distintas (la foto). Con `desde_t` (open_time de la última vela que la
    página ya recibió): las de open_time >= desde_t, de la más antigua a la más reciente, hasta MAX_LOTE por respuesta
    (`restantes` y `hay_mas` dicen si faltan). La de open_time == desde_t vuelve por si cambió. Si `desde_t` queda fuera
    de lo que cubre el tope de lectura, `reinicio` es True y se devuelve la foto.
    `intervalo` solo vale si es el de las klines o el paso que dice el `inicio`; si no, el de las klines.
    Una vela reescrita (misma open_time) se queda con su última versión.
    """
    if desde is None:
        return dict(SIN_VELAS)
    ruta = os.path.join(logs_dir or LOGS_DIR, "ultimo.jsonl")
    try:
        if os.stat(ruta).st_mtime < desde:
            return dict(SIN_VELAS)       # log de una corrida anterior
        ini, feed = _inicio(ruta), _nombre_feed(ruta)
        if not ini or not feed:
            return dict(SIN_VELAS)
        opciones = []
        for i in (_intervalo(ini.get("intervalo")), _intervalo(ini.get("paso"))):
            if i and i not in opciones:
                opciones.append(i)
        if not opciones:
            return dict(SIN_VELAS)
        elegido = intervalo if intervalo in opciones else opciones[0]
        clave = elegido.encode()

        def bastan(ls):
            ots = set()
            for x in ls:
                c = x.split(b",", 4)
                if len(c) > 4 and c[2] == clave and c[3].isdigit():
                    ots.add(int(c[3]))
            if desde_t is None:
                return len(ots) > N_VELAS
            return any(o <= desde_t for o in ots)    # ya se ve desde la marca hasta el final

        ruta_feed = os.path.join(os.path.dirname(ruta), feed)
        tam = os.path.getsize(ruta_feed)
        lineas = _cola(ruta_feed, MAX_FEED_VELAS, bastan)
    except OSError:
        return dict(SIN_VELAS)
    por_hora, ultimo = {}, None
    for fila in csv.reader(x.decode("utf-8", "replace") for x in lineas):
        try:
            ahora, ot = int(fila[0]), int(fila[3])
            ohlc = [float(x) for x in fila[4:8]] + [float(fila[8])]   # open, high, low, close, volume
        except (ValueError, IndexError):
            continue                     # cabecera, fila cortada o corrupta
        if (fila[1] not in ("step", "klines") or fila[2] != elegido or len(ohlc) != 5
                or not all(map(math.isfinite, ohlc)) or ohlc[4] < 0):
            continue
        por_hora[ot] = ohlc              # la última versión de la vela gana
        ultimo = ahora
    if not por_hora:
        return dict(SIN_VELAS)
    todas = sorted(por_hora.items())
    reinicio = desde_t is not None and todas[0][0] > desde_t and tam > MAX_FEED_VELAS   # la marca quedó fuera de lo leído
    restantes = 0
    if desde_t is None or reinicio:
        elegidas = todas[-N_VELAS:]
    else:
        posteriores = [x for x in todas if x[0] >= desde_t]
        elegidas = posteriores[:MAX_LOTE]
        restantes = len(posteriores) - len(elegidas)
    return {"estado": "ok", "intervalo": elegido, "intervalos": opciones, "corrida": feed, "ahora_iso": iso(ultimo),
            "ultima_t": todas[-1][0], "restantes": restantes, "hay_mas": restantes > 0, "reinicio": reinicio,
            "velas": [{"t": ot, "hora": iso(ot), "open": o[0], "high": o[1], "low": o[2], "close": o[3],
                       "volume": o[4]} for ot, o in elegidas]}
