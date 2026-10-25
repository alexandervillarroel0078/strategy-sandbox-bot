"""Registro de lo que el bot RECIBE y ENVÍA (--log). Solo escribe; nunca altera decisiones ni resultados.

Un evento por línea JSON en bot/logs/bot_<fecha-hora>.jsonl (y ultimo.jsonl como copia viva).
Seguridad: las órdenes se registran con LISTA BLANCA de campos (`permitir`): todo lo que no esté
en CAMPOS_ORDEN (cabeceras, firmas, claves, campos desconocidos) se descarta. Los mensajes de error
del servidor no se copian al registro; la URL se reduce a host:puerto.
Si no se puede escribir, avisa una vez y el bot sigue sin registro.
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

from .utils import ROOT

LOGS_DIR = os.path.join(ROOT, "bot", "logs")
CAMPOS_ORDEN = ("lado", "tipo", "fraccion", "stop_fraccion", "take_profit_fraccion",
                "id", "estado", "precio", "comision", "motivo", "ahora")
FEED_COLS = ["ahora", "fuente", "intervalo", "open_time", "open", "high", "low", "close", "volume", "close_time"]
_ESCALARES = (str, int, float, bool, type(None))
CONSERVAR_POR_DEFECTO = 20      # corridas que se conservan en bot/logs/ (config.yaml: logs_conservar; 0 = no borrar)
_SELLO = r"\d{8}-\d{6}"
_RE_ARCHIVO = re.compile(rf"bot_({_SELLO})\.jsonl|feed_({_SELLO})\.csv")   # solo estos nombres se pueden borrar


def permitir(d, campos=CAMPOS_ORDEN) -> dict:
    """Copia solo los campos permitidos expresamente (y solo valores escalares)."""
    if not isinstance(d, dict):
        return {}
    return {k: (v[:200] if isinstance(v, str) else v) for k, v in d.items()
            if k in campos and isinstance(v, _ESCALARES)}


def host_puerto(url: str) -> str:
    """Sin usuario, contraseña ni ruta: solo host:puerto."""
    u = urlparse(url or "")
    host = u.hostname or ""
    if ":" in host:                  # IPv6 literal
        host = f"[{host}]"
    return f"{host}:{u.port or (443 if u.scheme == 'https' else 80)}"


def iso(ms) -> str | None:
    return None if ms is None else datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class RegistroNulo:
    activo = False
    nivel = "eventos"

    def evento(self, tipo, /, **datos): pass
    def feed(self, ahora, fuente, intervalo, filas): pass
    def cerrar(self): pass


NULO = RegistroNulo()


class Registro:
    activo = True

    def __init__(self, archivo: bool = True, nivel: str = "eventos", verbose: bool = False,
                 carpeta: str | None = None):
        self.nivel, self.verbose = nivel, verbose
        self._f = self._feed = self._feed_w = None
        self._vistas: dict = {}
        self._copiar = False
        self.ruta = self.ruta_feed = None
        if archivo:
            self._abrir(carpeta or LOGS_DIR)

    def _fallo(self, e: Exception) -> None:
        print(f"AVISO: no puedo escribir el registro ({type(e).__name__}); el bot continúa sin él.", file=sys.stderr)
        self._f = self._feed = self._feed_w = None

    def _abrir(self, carpeta: str) -> None:
        try:
            os.makedirs(carpeta, exist_ok=True)
            sello = datetime.now().strftime("%Y%m%d-%H%M%S")
            self.ruta = os.path.join(carpeta, f"bot_{sello}.jsonl")
            self._f = open(self.ruta, "w", encoding="utf-8")
            self._enlazar_ultimo()
            if self.nivel == "completo":
                self.ruta_feed = os.path.join(carpeta, f"feed_{sello}.csv")
                self._feed = open(self.ruta_feed, "w", encoding="utf-8", newline="")
                self._feed_w = csv.writer(self._feed)
                self._feed_w.writerow(FEED_COLS)
        except Exception as e:   # noqa: BLE001 - el registro nunca debe tumbar al bot
            self._fallo(e)

    def _enlazar_ultimo(self) -> None:
        """ultimo.jsonl = enlace duro al log en curso (así se ve en vivo); si no hay enlaces, copia al cerrar."""
        ultimo = os.path.join(os.path.dirname(self.ruta), "ultimo.jsonl")
        try:
            if os.path.lexists(ultimo):
                os.remove(ultimo)
            os.link(self.ruta, ultimo)
            self._copiar = False
        except OSError:
            self._copiar = True

    def evento(self, tipo: str, /, **datos) -> None:
        if self._f is not None:
            try:
                self._f.write(json.dumps({"evento": tipo, **datos}, ensure_ascii=False, default=str) + "\n")
                self._f.flush()
            except Exception as e:   # noqa: BLE001
                self._fallo(e)
        if self.verbose and tipo in ("decision", "orden_enviada", "orden_respuesta", "fill"):
            print(self._linea(tipo, datos))

    @staticmethod
    def _linea(tipo: str, d: dict) -> str:
        t = d.get("ahora_iso") or iso(d.get("ahora")) or ""
        if tipo == "decision":
            extra = " ".join(f"{k}={v}" for k, v in (d.get("valores") or {}).items())
            return f"[{t}] decisión {d['accion']} · {d.get('motivo')} {extra}".rstrip()
        resto = " ".join(f"{k}={v}" for k, v in d.items() if k not in ("ahora", "ahora_iso") and v is not None)
        return f"[{t}] {tipo} {resto}"

    def feed(self, ahora: int, fuente: str, intervalo: str, filas: list) -> None:
        """Nivel completo: cada vela recibida, con el ahora de cuando llegó (una vez por vela y contenido)."""
        if self._feed_w is None:
            return
        try:
            for r in filas:
                clave = (fuente, intervalo, r[0])
                if self._vistas.get(clave) == tuple(r):
                    continue
                self._vistas[clave] = tuple(r)
                self._feed_w.writerow([ahora, fuente, intervalo, r[0], *r[1:6], r[6]])
            self._feed.flush()
        except Exception as e:   # noqa: BLE001
            self._fallo(e)

    def cerrar(self) -> None:
        for f in (self._f, self._feed):
            try:
                f and f.close()
            except Exception:   # noqa: BLE001
                pass
        if self.ruta and self._copiar:
            try:
                shutil.copyfile(self.ruta, os.path.join(os.path.dirname(self.ruta), "ultimo.jsonl"))
            except Exception as e:   # noqa: BLE001
                print(f"AVISO: no pude actualizar ultimo.jsonl ({type(e).__name__})", file=sys.stderr)
        self._f = self._feed = self._feed_w = None


def conservar_logs(cfg: dict) -> int | None:
    """`logs_conservar` de config.yaml (por defecto 20; 0 = no borrar). None si el valor no es válido."""
    v = cfg.get("logs_conservar", CONSERVAR_POR_DEFECTO)
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def limpiar_logs(carpeta: str, conservar: int, actual: str | None = None) -> int:
    """Borra las corridas más antiguas de `carpeta` y deja las `conservar` más recientes; devuelve cuántos archivos borró.

    Una corrida = los archivos con el mismo sello (bot_<sello>.jsonl y feed_<sello>.csv). Solo se tocan archivos
    normales con esos nombres exactos (nunca ultimo.jsonl ni nada más). Nunca se borra la corrida `actual` (ruta de su
    .jsonl) ni la que contiene el archivo al que apunta ultimo.jsonl. Un borrado que falla (p. ej. archivo abierto) se omite.
    """
    if not conservar:
        return 0
    corridas: dict[str, list[str]] = {}
    try:
        nombres = os.listdir(carpeta)
    except OSError:
        return 0
    for n in nombres:
        m = _RE_ARCHIVO.fullmatch(n)
        ruta = os.path.join(carpeta, n)
        if m and os.path.isfile(ruta) and not os.path.islink(ruta):
            corridas.setdefault(m[1] or m[2], []).append(ruta)
    protegidas = set()
    if actual and (m := _RE_ARCHIVO.fullmatch(os.path.basename(actual))):
        protegidas.add(m[1] or m[2])
    ultimo = os.path.join(carpeta, "ultimo.jsonl")
    for sello, rutas in corridas.items():
        for r in rutas:
            try:
                if os.path.lexists(ultimo) and os.path.samefile(r, ultimo):
                    protegidas.add(sello)
            except OSError:
                pass
    borrados = 0
    for sello in sorted(corridas)[:-conservar]:
        if sello in protegidas:
            continue
        for r in corridas[sello]:
            try:
                os.remove(r)
                borrados += 1
            except OSError:
                pass
    return borrados


def registrar_decision(reg, ahora: int, accion: str, estrategia, velas, posicion) -> None:
    """Evento `decision`: acción, motivo y valores clave. La estrategia puede exponer `explicacion`
    ({"motivo": ..., "valores": {...}}); si no, se registra solo el precio y el tamaño de la ventana."""
    if not reg.activo:
        return
    exp = getattr(estrategia, "explicacion", None) or {}
    valores = {"precio": float(velas["close"].iloc[-1]), "velas": len(velas), **(exp.get("valores") or {})}
    reg.evento("decision", ahora=ahora, ahora_iso=iso(ahora), accion=accion, motivo=exp.get("motivo"),
               en_posicion=posicion is not None, valores=valores)


def registrar_fills_internos(reg, broker, en_pos_antes: bool, n_trades_antes: int, ahora: int, fee: float) -> None:
    """Fills del Broker interno ocurridos desde la foto (en_pos_antes, n_trades_antes)."""
    if not reg.activo:
        return
    p = broker.pos
    if p is not None and not en_pos_antes:
        reg.evento("fill", ahora=ahora, ahora_iso=iso(ahora), lado="buy", precio=p.entry_price,
                   comision=p.qty * p.entry_price * fee, motivo="señal")
    for t in broker.trades[n_trades_antes:]:
        reg.evento("fill", ahora=ahora, ahora_iso=iso(ahora), lado="sell", precio=t["exit_price"],
                   comision=t["qty"] * t["exit_price"] * fee, motivo=t["reason"])


def registrar_fills_exchange(reg, client, vistos: set) -> None:
    """Fills nuevos según /api/orders (solo órdenes FILLED aún no registradas)."""
    if not reg.activo:
        return
    for o in client.orders():
        if o.get("estado") == "FILLED" and o["id"] not in vistos:
            vistos.add(o["id"])
            reg.evento("fill", **{**permitir(o), "motivo": o.get("razon"), "ahora": o.get("ejecutada"),
                                  "ahora_iso": iso(o.get("ejecutada"))})
