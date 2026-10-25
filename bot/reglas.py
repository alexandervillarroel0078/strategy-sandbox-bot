"""Carga y validación de reglas.yaml / limites.yaml (lo comparten el panel y la estrategia por_niveles).

Los porcentajes van en % (stop_pct: 3 = 3 %). Un error es {"campo": "reglas.0.stop_pct", "mensaje": ...};
el panel usa `campo` para mostrarlo junto al input.
"""
from __future__ import annotations

import math
import os

import yaml

PKG_DIR = os.path.dirname(os.path.abspath(__file__))
REGLAS = os.path.join(PKG_DIR, "reglas.yaml")
REGLAS_EJEMPLO = os.path.join(PKG_DIR, "reglas.example.yaml")   # valor por defecto si falta reglas.yaml (en este repo sí se versiona)
LIMITES = os.path.join(PKG_DIR, "limites.yaml")

TIPOS_ZONA = ("soporte", "resistencia")
ACCIONES = ("abrir_long", "cerrar_long", "abrir_short", "solo_avisar")
ACCIONES_APERTURA = ("abrir_long", "abrir_short")
EMA_RANGO = (2, 500)
RSI_RANGO = (0, 100)          # el valor debe estar estrictamente dentro
RANGOS = {"ema": list(EMA_RANGO), "rsi": list(RSI_RANGO)}
CLAVES_LIMITES = ("riesgo_max_pct", "stop_max_pct", "parcial_min_pct", "parcial_max_pct",
                  "max_zonas", "max_reglas_activas")


def pct_a_fraccion(pct: float) -> float:
    """Porcentaje del panel (5 = 5 %) -> fracción de la API de órdenes y de config.yaml (0.05)."""
    return pct / 100


def fraccion_a_pct(fraccion: float) -> float:
    """Fracción de la API de órdenes y de config.yaml (0.05) -> porcentaje del panel (5 = 5 %)."""
    return fraccion * 100


class ReglasInvalidas(ValueError):
    def __init__(self, errores: list[dict], origen: str = ""):
        self.errores = errores
        pref = f"{origen}: " if origen else ""
        super().__init__(pref + "; ".join(f"{e['campo']}: {e['mensaje']}" for e in errores))


def _num(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def cargar_yaml(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        raise ReglasInvalidas([{"campo": "", "mensaje": "no existe el archivo"}], path) from None
    except yaml.YAMLError as e:
        raise ReglasInvalidas([{"campo": "", "mensaje": f"YAML ilegible: {e}"}], path) from None


def cargar_limites(path: str | None = None) -> dict:
    path = path or LIMITES
    d = cargar_yaml(path)
    if not isinstance(d, dict):
        raise ReglasInvalidas([{"campo": "", "mensaje": "debe ser un diccionario"}], path)
    errs = []
    for k in CLAVES_LIMITES:
        v = _num(d.get(k))
        if v is None or v <= 0:
            errs.append({"campo": k, "mensaje": "falta o no es un número positivo"})
    for k in d:
        if k not in CLAVES_LIMITES:
            errs.append({"campo": str(k), "mensaje": "límite desconocido"})
    if not errs and d["parcial_min_pct"] > d["parcial_max_pct"]:
        errs.append({"campo": "parcial_min_pct", "mensaje": "no puede superar parcial_max_pct"})
    if not errs and d["parcial_max_pct"] >= 100:
        errs.append({"campo": "parcial_max_pct", "mensaje": "debe ser menor que 100"})
    if errs:
        raise ReglasInvalidas(errs, path)
    return d


def cargar_reglas(path: str | None = None, limites: dict | None = None) -> dict:
    """Lee y valida reglas.yaml; lanza ReglasInvalidas con todos los errores si algo falla.

    Sin `path` y sin bot/reglas.yaml usa reglas.example.yaml. Un `path` explícito que no existe sigue siendo error."""
    path = path or (REGLAS if os.path.exists(REGLAS) else REGLAS_EJEMPLO)
    d = cargar_yaml(path)
    errs = validar(d, limites or cargar_limites())
    if errs:
        raise ReglasInvalidas(errs, path)
    return d


def _claves(d: dict, permitidas: tuple, pref: str, errs: list) -> None:
    for k in d:
        if k not in permitidas:
            errs.append({"campo": f"{pref}.{k}" if pref else str(k), "mensaje": "campo desconocido"})


def _bool_opc(d: dict, k: str, pref: str, errs: list) -> None:
    if d.get(k) is not None and not isinstance(d[k], bool):
        errs.append({"campo": f"{pref}.{k}", "mensaje": "debe ser true o false"})


def _pct(d: dict, k: str, pref: str, maximo: float, errs: list, requerido: bool = False) -> None:
    c = f"{pref}.{k}"
    if d.get(k) is None:
        if requerido:
            errs.append({"campo": c, "mensaje": "obligatorio"})
        return
    v = _num(d[k])
    if v is None:
        errs.append({"campo": c, "mensaje": "debe ser un número"})
    elif v <= 0:
        errs.append({"campo": c, "mensaje": "debe ser mayor que 0"})
    elif v > maximo:
        errs.append({"campo": c, "mensaje": f"supera el máximo permitido ({maximo:g} %)"})


def _ema_n(d: dict, k: str, pref: str, errs: list) -> None:
    c = f"{pref}.{k}"
    v = _num(d.get(k))
    if v is None or v != int(v):
        errs.append({"campo": c, "mensaje": "debe ser un entero"})
    elif not EMA_RANGO[0] <= v <= EMA_RANGO[1]:
        errs.append({"campo": c, "mensaje": f"EMA fuera de rango ({EMA_RANGO[0]} a {EMA_RANGO[1]})"})


def _validar_confirmaciones(c, pref: str, errs: list) -> None:
    if not isinstance(c, dict):
        errs.append({"campo": pref, "mensaje": "debe ser un diccionario"})
        return
    _claves(c, ("cierre_fuera_zona", "rsi", "ema"), pref, errs)
    if c.get("cierre_fuera_zona") not in (None, "arriba", "abajo"):
        errs.append({"campo": f"{pref}.cierre_fuera_zona", "mensaje": "debe ser arriba o abajo"})
    r = c.get("rsi")
    if r is not None:
        p = f"{pref}.rsi"
        if not isinstance(r, dict):
            errs.append({"campo": p, "mensaje": "debe ser un diccionario"})
        else:
            _claves(r, ("op", "valor"), p, errs)
            if r.get("op") not in ("mayor", "menor"):
                errs.append({"campo": f"{p}.op", "mensaje": "debe ser mayor o menor"})
            v = _num(r.get("valor"))
            if v is None:
                errs.append({"campo": f"{p}.valor", "mensaje": "debe ser un número"})
            elif not RSI_RANGO[0] < v < RSI_RANGO[1]:
                errs.append({"campo": f"{p}.valor", "mensaje": "RSI fuera de rango (entre 0 y 100, sin incluirlos)"})
    e = c.get("ema")
    if e is not None:
        p = f"{pref}.ema"
        if not isinstance(e, dict):
            errs.append({"campo": p, "mensaje": "debe ser un diccionario"})
        else:
            _claves(e, ("op", "n"), p, errs)
            if e.get("op") not in ("sobre", "bajo"):
                errs.append({"campo": f"{p}.op", "mensaje": "debe ser sobre o bajo"})
            _ema_n(e, "n", p, errs)


def _validar_parcial(pa, pref: str, lim: dict, errs: list) -> None:
    if not isinstance(pa, dict):
        errs.append({"campo": pref, "mensaje": "debe ser un diccionario"})
        return
    _claves(pa, ("porcentaje", "condicion"), pref, errs)
    v = _num(pa.get("porcentaje"))
    if v is None:
        errs.append({"campo": f"{pref}.porcentaje", "mensaje": "debe ser un número"})
    elif not lim["parcial_min_pct"] <= v <= lim["parcial_max_pct"]:
        errs.append({"campo": f"{pref}.porcentaje",
                     "mensaje": f"debe estar entre {lim['parcial_min_pct']:g} % y {lim['parcial_max_pct']:g} %"})
    c = pa.get("condicion")
    p = f"{pref}.condicion"
    if not isinstance(c, dict):
        errs.append({"campo": p, "mensaje": "obligatoria (EMA N o +X %)"})
        return
    _claves(c, ("tipo", "n", "valor"), p, errs)
    if c.get("tipo") == "ema":
        _ema_n(c, "n", p, errs)
    elif c.get("tipo") == "pct":
        x = _num(c.get("valor"))
        if x is None or not 0 < x <= 100:
            errs.append({"campo": f"{p}.valor", "mensaje": "debe ser un número mayor que 0 y hasta 100"})
    else:
        errs.append({"campo": f"{p}.tipo", "mensaje": "debe ser ema o pct"})


def validar(d, lim: dict) -> list[dict]:
    """Devuelve la lista de errores (vacía si `d` es válido)."""
    errs: list[dict] = []
    if not isinstance(d, dict):
        return [{"campo": "", "mensaje": "el contenido debe ser un diccionario"}]
    _claves(d, ("activo", "zonas", "reglas"), "", errs)
    if not isinstance(d.get("activo"), bool):
        errs.append({"campo": "activo", "mensaje": "debe ser true o false"})

    zonas = d.get("zonas", [])
    nombres: set[str] = set()
    if not isinstance(zonas, list):
        errs.append({"campo": "zonas", "mensaje": "debe ser una lista"})
        zonas = []
    if len(zonas) > lim["max_zonas"]:
        errs.append({"campo": "zonas", "mensaje": f"máximo {lim['max_zonas']:g} zonas (hay {len(zonas)})"})
    for i, z in enumerate(zonas):
        p = f"zonas.{i}"
        if not isinstance(z, dict):
            errs.append({"campo": p, "mensaje": "debe ser un diccionario"})
            continue
        _claves(z, ("nombre", "tipo", "desde", "hasta", "activa"), p, errs)
        n = z.get("nombre")
        if not isinstance(n, str) or not n.strip():
            errs.append({"campo": f"{p}.nombre", "mensaje": "obligatorio"})
        elif len(n.strip()) > 40:
            errs.append({"campo": f"{p}.nombre", "mensaje": "máximo 40 caracteres"})
        elif n.strip().casefold() in nombres:
            errs.append({"campo": f"{p}.nombre", "mensaje": f"nombre repetido: {n.strip()!r}"})
        else:
            nombres.add(n.strip().casefold())
        if z.get("tipo") not in TIPOS_ZONA:
            errs.append({"campo": f"{p}.tipo", "mensaje": "debe ser soporte o resistencia"})
        desde, hasta = _num(z.get("desde")), _num(z.get("hasta"))
        for k, v in (("desde", desde), ("hasta", hasta)):
            if v is None:
                errs.append({"campo": f"{p}.{k}", "mensaje": "debe ser un número"})
            elif v <= 0:
                errs.append({"campo": f"{p}.{k}", "mensaje": "debe ser mayor que 0"})
        if desde is not None and hasta is not None and desde > 0 and desde >= hasta:
            errs.append({"campo": f"{p}.desde", "mensaje": "desde debe ser menor que hasta"})
        _bool_opc(z, "activa", p, errs)

    reglas = d.get("reglas", [])
    if not isinstance(reglas, list):
        errs.append({"campo": "reglas", "mensaje": "debe ser una lista"})
        reglas = []
    n_activas = sum(1 for r in reglas if isinstance(r, dict) and r.get("activa", True) is not False)
    if n_activas > lim["max_reglas_activas"]:
        errs.append({"campo": "reglas",
                     "mensaje": f"máximo {lim['max_reglas_activas']:g} reglas activas (hay {n_activas})"})
    for i, r in enumerate(reglas):
        p = f"reglas.{i}"
        if not isinstance(r, dict):
            errs.append({"campo": p, "mensaje": "debe ser un diccionario"})
            continue
        _claves(r, ("zona", "accion", "activa", "confirmaciones", "stop_pct", "parcial", "riesgo_pct"), p, errs)
        z = r.get("zona")
        if not isinstance(z, str) or z.strip().casefold() not in nombres:
            errs.append({"campo": f"{p}.zona", "mensaje": f"la zona {z!r} no existe"})
        if r.get("accion") not in ACCIONES:
            errs.append({"campo": f"{p}.accion", "mensaje": "acción no válida: " + ", ".join(ACCIONES)})
        _bool_opc(r, "activa", p, errs)
        if r.get("confirmaciones") is not None:
            _validar_confirmaciones(r["confirmaciones"], f"{p}.confirmaciones", errs)
        _pct(r, "stop_pct", p, lim["stop_max_pct"], errs, requerido=r.get("accion") in ACCIONES_APERTURA)
        _pct(r, "riesgo_pct", p, lim["riesgo_max_pct"], errs)
        if r.get("parcial") is not None:
            _validar_parcial(r["parcial"], f"{p}.parcial", lim, errs)
    return errs


def pendientes(d: dict, cfg: dict) -> list[str]:
    """Campos guardados y válidos que el Broker actual no soporta, con lo que se usa en su lugar.

    `cfg`: config.yaml del bot (fracciones: 0.05 = 5 %).
    """
    zonas = {z["nombre"].strip().casefold(): z for z in d.get("zonas", [])}
    out = []
    for i, r in enumerate(d.get("reglas", [])):
        if r.get("activa", True) is False:
            continue
        if zonas[r["zona"].strip().casefold()].get("activa", True) is False:
            continue
        et = f"regla {i + 1} (zona {r['zona']!r}, {r['accion']})"
        if r["accion"] == "abrir_short":
            out.append(f"{et}: IGNORADA ENTERA. El Broker es solo largos; no hay shorts.")
        if r.get("stop_pct") is not None:
            out.append(f"{et}: stop_pct={r['stop_pct']:g} % IGNORADO. Se usa el stop global de "
                       f"{fraccion_a_pct(cfg['stop_pct']):g} % y el objetivo global de {fraccion_a_pct(cfg['target_pct']):g} % (config.yaml).")
        if r.get("riesgo_pct") is not None:
            out.append(f"{et}: riesgo_pct={r['riesgo_pct']:g} % IGNORADO. Se usa position_fraction="
                       f"{fraccion_a_pct(cfg['position_fraction']):g} % del capital (config.yaml).")
        if r.get("parcial") is not None:
            out.append(f"{et}: parcial={r['parcial']['porcentaje']:g} % IGNORADO. El Broker cierra siempre "
                       "la posición entera (señal, stop o objetivo).")
    return out
