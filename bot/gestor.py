"""Gestor del bot para el panel: lanza `python -m bot.run_bot_api` como PROCESO APARTE, lo vigila y lo detiene.

No importa run_bot_api ni nada del exchange: el exchange se comprueba por HTTP y el bot corre en su
propio proceso. El comando es fijo (lista de argumentos, sin shell); del formulario solo entran fechas validadas
y una estrategia de la lista blanca. El modo de ejecución siempre es `exchange` (la interna ya no se ofrece).

Procesos huérfanos: el gestor guarda el objeto Popen y solo actúa sobre ese PID; `cerrar()` lo llama el panel al
salir; y en Windows el hijo va en un Job Object con KILL_ON_JOB_CLOSE, así Windows lo mata aunque el panel
muera de golpe (Administrador de tareas, cierre de la consola).
"""
from __future__ import annotations

import collections
import http.client
import os
import re
import subprocess
import sys
import threading
import time
from datetime import date
from urllib.parse import urlparse

from .utils import ROOT, load_config

ESTRATEGIAS = ("ejemplo", "donchian", "por_niveles")
AVISO_SIN_EXCHANGE = "arranca primero el exchange (repo strategy-sandbox-exchange, ver su README)"
AVISO_DETENIDO = ("Corrida detenida a mitad: la posición puede haber quedado abierta en el exchange. "
                  "No usar este resultado para evaluar una idea.")
ESPERA_TERMINAR_S = 3.0
_FECHA = re.compile(r"\d{4}-\d{2}-\d{2}")
_RE_OPS = re.compile(r"Nº de operaciones\s+(\d+)")
_RE_RET = re.compile(r"Retorno estrategia\s+([+-]?\d+(?:\.\d+)?%)")
_RE_LOG = re.compile(r"^Registro: (.+?)(?: · feed: .*)?$")


class ErrorGestor(Exception):
    def __init__(self, mensaje: str, status: int):
        super().__init__(mensaje)
        self.mensaje, self.status = mensaje, status


def _fecha(valor, campo: str) -> str:
    if not isinstance(valor, str) or not _FECHA.fullmatch(valor):
        raise ErrorGestor(f"{campo}: usa el formato AAAA-MM-DD", 400)
    try:
        date.fromisoformat(valor)
    except ValueError:
        raise ErrorGestor(f"{campo}: fecha inválida", 400) from None
    return valor


def validar(datos) -> dict:
    """Valida el formulario. Devuelve {inicio, fin, estrategia} o lanza ErrorGestor(400)."""
    if not isinstance(datos, dict):
        raise ErrorGestor("se esperaba un objeto JSON", 400)
    inicio, fin = _fecha(datos.get("inicio"), "inicio"), _fecha(datos.get("fin"), "fin")
    if inicio > fin:
        raise ErrorGestor("el inicio no puede ser posterior al fin", 400)
    estrategia = datos.get("estrategia")
    if estrategia not in ESTRATEGIAS:
        raise ErrorGestor("estrategia no válida: " + ", ".join(ESTRATEGIAS), 400)
    return {"inicio": inicio, "fin": fin, "estrategia": estrategia}


def construir_comando(p: dict) -> list[str]:
    """Comando del bot a partir de parámetros ya validados. Siempre exchange + registro completo."""
    return [sys.executable, "-m", "bot.run_bot_api", "--start", p["inicio"], "--end", p["fin"],
            "--strategy", p["estrategia"], "--ejecucion", "exchange", "--log", "--log-nivel", "completo"]


def exchange_responde(url: str, timeout: float = 2.0) -> bool:
    """True si GET /api/info del exchange contesta 200. Solo comprueba que está vivo."""
    u = urlparse(url)
    try:
        c = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout)
        try:
            c.request("GET", "/api/info")
            r = c.getresponse()
            r.read()
            return r.status == 200
        finally:
            c.close()
    except (OSError, http.client.HTTPException):
        return False


class _Job:
    """Job Object de Windows con KILL_ON_JOB_CLOSE: al cerrarse el último handle (p. ej. al morir el panel)
    Windows mata los procesos asignados. Mejor esfuerzo: si algo falla, el bot corre igual sin esta red."""

    def __init__(self, proceso: subprocess.Popen):
        self.handle = None
        if os.name != "nt":
            return
        try:
            import ctypes
            from ctypes import wintypes

            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.CreateJobObjectW.restype = wintypes.HANDLE
            k.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
            k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
            k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            k.CloseHandle.argtypes = [wintypes.HANDLE]
            self._cerrar_handle = k.CloseHandle

            class Basico(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class IO(ctypes.Structure):
                _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOps", "WriteOps", "OtherOps",
                                                           "ReadBytes", "WriteBytes", "OtherBytes")]

            class Extendido(ctypes.Structure):
                _fields_ = [("Basic", Basico), ("Io", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]

            job = k.CreateJobObjectW(None, None)
            if not job:
                return
            info = Extendido()
            info.Basic.LimitFlags = 0x2000   # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            ok = (k.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))   # 9 = ExtendedLimit
                  and k.AssignProcessToJobObject(job, int(proceso._handle)))
            if ok:
                self.handle = job
            else:
                k.CloseHandle(job)
        except Exception:   # noqa: BLE001 - es una red de seguridad extra, nunca debe impedir lanzar el bot
            self.handle = None

    def cerrar(self) -> None:
        if self.handle is not None:
            try:
                self._cerrar_handle(self.handle)
            finally:
                self.handle = None


class GestorBot:
    def __init__(self, api_url: str | None = None, config_path: str | None = None,
                 comando=construir_comando, max_lineas: int = 200):
        self._api_url, self._config_path, self._comando = api_url, config_path, comando
        self._lock = threading.RLock()
        self._lineas: collections.deque = collections.deque(maxlen=max_lineas)
        self._reiniciar()

    def _reiniciar(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._hilo: threading.Thread | None = None
        self._job: _Job | None = None
        self._params: dict | None = None
        self._inicio_ts: float | None = None   # hora de arranque del bot lanzado desde aquí
        self._codigo: int | None = None
        self._terminado = False        # el lector ya leyó toda la salida y el proceso salió
        self._detenido = False
        self._operaciones = self._retorno = self._registro = None
        self._lineas.clear()

    def _url(self) -> str:
        return self._api_url or load_config(self._config_path)["api_url"]

    def _corriendo(self) -> bool:
        return self._proc is not None and not self._terminado

    # ---- acciones ----
    def iniciar(self, datos) -> dict:
        params = validar(datos)
        with self._lock:
            if self._corriendo():
                raise ErrorGestor("ya hay un bot corriendo: detenlo o espera a que termine", 409)
            if not exchange_responde(self._url()):
                raise ErrorGestor(AVISO_SIN_EXCHANGE, 503)
            self._reiniciar()
            env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            inicio_ts = time.time()
            try:
                proc = subprocess.Popen(self._comando(params), cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                        encoding="utf-8", errors="replace", bufsize=1, creationflags=flags)
            except OSError as e:
                raise ErrorGestor(f"no se pudo lanzar el bot: {type(e).__name__}", 500) from None
            self._proc, self._params, self._inicio_ts = proc, params, inicio_ts
            self._job = _Job(proc)
            self._hilo = threading.Thread(target=self._leer, args=(proc,), daemon=True, name="salida-bot")
            self._hilo.start()
            return self.estado()

    def _leer(self, proc: subprocess.Popen) -> None:
        """Hilo lector: pasa cada línea al buffer y guarda el resultado; los manejadores HTTP nunca esperan al bot."""
        try:
            for linea in proc.stdout:
                linea = linea.rstrip("\r\n")
                with self._lock:
                    if proc is not self._proc:
                        return
                    self._lineas.append(linea)
                    if m := _RE_OPS.search(linea):
                        self._operaciones = int(m[1])
                    if m := _RE_RET.search(linea):
                        self._retorno = m[1]
                    if m := _RE_LOG.match(linea):
                        self._registro = m[1]
        finally:
            codigo = proc.wait()
            with self._lock:
                if proc is self._proc:
                    self._codigo, self._terminado = codigo, True
                    if self._job:
                        self._job.cerrar()

    def detener(self) -> dict:
        """Cierra SOLO el proceso que lanzó este gestor."""
        with self._lock:
            proc = self._proc
            if proc is None or self._terminado or proc.poll() is not None:
                raise ErrorGestor("no hay ningún bot corriendo", 409)
            self._detenido = True
            self._terminar(proc)
            hilo = self._hilo
        if hilo is not None:
            hilo.join(2.0)      # que el lector cierre el estado (fuera del lock: él lo necesita)
        return self.estado()

    @staticmethod
    def _terminar(proc: subprocess.Popen) -> None:
        proc.terminate()
        try:
            proc.wait(ESPERA_TERMINAR_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    def cerrar(self) -> None:
        """Al cerrar el panel: si el bot sigue vivo, se cierra. Seguro de llamar varias veces."""
        with self._lock:
            proc = self._proc
            if proc is not None and proc.poll() is None:
                self._detenido = True
                self._terminar(proc)

    # ---- lectura ----
    def desde(self) -> float | None:
        """Hora de arranque del último bot lanzado por este gestor (None si no hay)."""
        with self._lock:
            return self._inicio_ts

    def estado(self) -> dict:
        with self._lock:
            if self._proc is None:
                estado = "sin_iniciar"
            elif not self._terminado:
                estado = "corriendo"
            elif self._detenido:
                estado = "detenido"
            else:
                estado = "terminado" if self._codigo == 0 else "error"
            out = {"estado": estado, "lineas": list(self._lineas), "codigo": self._codigo,
                   "parametros": self._params, "operaciones": None, "retorno": None, "registro": None}
            if estado == "terminado":
                out.update(operaciones=self._operaciones, retorno=self._retorno, registro=self._registro)
            elif estado in ("detenido", "error"):
                out["registro"] = self._registro
            if estado == "detenido":
                out["aviso"] = AVISO_DETENIDO
            return out
