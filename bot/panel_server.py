"""Panel local de reglas: SOLO lee/escribe bot/reglas.yaml. No ejecuta nada ni habla con el exchange o el bot.

    python -m bot.panel_server          ->  http://127.0.0.1:8002/   (PANEL_HOST / PANEL_PORT lo cambian)

Por defecto solo escucha en 127.0.0.1 (PANEL_HOST). Rechaza Host que no sea localhost/127.0.0.1 (o PANEL_ALLOWED_HOSTS); el POST exige
Content-Type: application/json y rechaza cualquier Origin que no sea el propio panel.
"""
from __future__ import annotations

import argparse
import atexit
import json
import os
import re
import signal
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import yaml

from . import datos_vivo
from . import reglas as R
from .gestor import ErrorGestor, GestorBot
from .utils import load_config

PKG_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(PKG_DIR, "panel", "index.html")
# Lista blanca de UN solo archivo estático (lightweight-charts 4.1.3, Apache-2.0): ruta fija, nunca construida con la petición.
RUTA_GRAFICO = "/lightweight-charts.js"
ARCHIVO_GRAFICO = os.path.join(PKG_DIR, "panel", "lightweight-charts.standalone.production.js")
HOST_DEFECTO = "127.0.0.1"        # PANEL_HOST lo cambia
PUERTO_DEFECTO = 8002             # PANEL_PORT lo cambia
# Host que la guardia acepta. PANEL_ALLOWED_HOSTS (separados por comas) suma, por ejemplo, la IP de la LAN.
HOSTS_OK = {"127.0.0.1", "localhost"} | {h.strip().lower() for h in os.environ.get("PANEL_ALLOWED_HOSTS", "").split(",") if h.strip()}


def host_configurado() -> str:
    return os.environ.get("PANEL_HOST") or HOST_DEFECTO


def puerto_configurado(defecto: int = PUERTO_DEFECTO) -> int:
    v = os.environ.get("PANEL_PORT")
    if not v:
        return defecto
    try:
        p = int(v)
    except ValueError:
        raise SystemExit(f"PANEL_PORT inválido: {v!r}")
    if not 0 <= p <= 65535:
        raise SystemExit(f"PANEL_PORT fuera de rango: {p}")
    return p
MAX_BODY = 256 * 1024


def host_ok(host: str | None) -> bool:
    if not host:
        return False
    h = host.rsplit(":", 1)[0] if not host.startswith("[") and ":" in host else host
    return h.lower() in HOSTS_OK


def escribir_atomico(path: str, data: dict) -> None:
    """Escribe en un temporal del mismo directorio y lo renombra: nunca queda un archivo a medias."""
    texto = "# Reglas de trading (las escribe el panel: python -m bot.panel_server). El bot lo lee al arrancar.\n"
    texto += yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(texto)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def make_server(reglas_path: str = R.REGLAS, limites_path: str = R.LIMITES, config_path: str | None = None,
                port: int | None = None, gestor: GestorBot | None = None,
                logs_dir: str | None = None, host: str | None = None) -> ThreadingHTTPServer:
    gestor = gestor or GestorBot(config_path=config_path)

    class Handler(BaseHTTPRequestHandler):
        server_version = "PanelReglas"

        def log_message(self, fmt, *args):
            pass

        # ---- utilidades ----
        def _json(self, status: int, obj) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _origen_propio(self) -> set[str]:
            p = self.server.server_address[1]
            return {f"http://{h}:{p}" for h in HOSTS_OK}

        def _guardia(self, post: bool) -> bool:
            def rechazar(status: int, obj) -> bool:
                # Un POST rechazado se responde tras descartar su cuerpo: cerrar el socket con bytes sin leer
                # hace que Windows envíe un RST y el cliente pierda la respuesta (WinError 10053/10054).
                if post:
                    try:
                        n = max(int(self.headers.get("Content-Length") or 0), 0)
                    except ValueError:
                        n = 0
                    resto = min(n, MAX_BODY)
                    self.connection.settimeout(2)       # si el cliente declara más de lo que manda, no se cuelga
                    try:
                        while resto > 0:
                            trozo = self.rfile.read1(min(resto, 65536))
                            if not trozo:
                                break
                            resto -= len(trozo)
                    except OSError:
                        pass
                    finally:
                        self.connection.settimeout(None)
                    if n > MAX_BODY:                    # no se lee entero: se responde y se cierra
                        self.close_connection = True
                        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                        self.send_response(status)
                        self.send_header("Content-Type", "application/json; charset=utf-8")
                        self.send_header("Content-Length", str(len(body)))
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Connection", "close")
                        self.end_headers()
                        self.wfile.write(body)
                        return False
                self._json(status, obj)
                return False

            if not host_ok(self.headers.get("Host")):
                return rechazar(403, {"error": "Host no permitido (solo localhost / 127.0.0.1 o PANEL_ALLOWED_HOSTS)"})
            origen = self.headers.get("Origin")
            if origen is not None and origen not in self._origen_propio():
                return rechazar(403, {"error": "Origin no permitido"})
            if post:
                tipo = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if tipo != "application/json":
                    return rechazar(415, {"error": "Content-Type debe ser application/json"})
            return True

        # ---- rutas ----
        def do_GET(self):
            if not self._guardia(False):
                return
            ruta = self.path.split("?")[0]
            if ruta in ("/", "/index.html"):
                with open(INDEX, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            elif ruta == RUTA_GRAFICO:
                with open(ARCHIVO_GRAFICO, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/javascript; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            elif ruta == "/reglas":
                try:
                    with open(reglas_path, encoding="utf-8") as f:
                        d = yaml.safe_load(f)
                except FileNotFoundError:
                    d = {"activo": False, "zonas": [], "reglas": []}
                except yaml.YAMLError as e:
                    return self._json(500, {"error": f"reglas.yaml ilegible: {e}"})
                self._json(200, d)
            elif ruta == "/limites":
                try:
                    lim = R.cargar_limites(limites_path)
                    cfg = load_config(config_path)
                except (R.ReglasInvalidas, OSError) as e:
                    return self._json(500, {"error": str(e)})
                self._json(200, {"limites": lim, "rangos": R.RANGOS, "globales": {
                    "stop_pct": R.fraccion_a_pct(cfg["stop_pct"]), "target_pct": R.fraccion_a_pct(cfg["target_pct"]),
                    "position_fraction_pct": R.fraccion_a_pct(cfg["position_fraction"])}})
            elif ruta == "/bot/estado":
                self._json(200, gestor.estado())
            elif ruta == "/bot/datos":   # solo lectura del final de bot/logs/ (ver datos_vivo.py)
                self._json(200, datos_vivo.leer(logs_dir, gestor.desde()))
            elif ruta == "/bot/velas":   # gráfico "Velas recibidas": ?intervalo= solo si el inicio lo ofrece; ?desde= devuelve las posteriores
                q = parse_qs(self.path.partition("?")[2])
                marca = q.get("desde", [""])[0]      # open_time de la última vela que la página ya recibió
                self._json(200, datos_vivo.velas(logs_dir, gestor.desde(), q.get("intervalo", [None])[0],
                                                 int(marca) if re.fullmatch(r"\d{1,16}", marca) else None))
            else:
                self._json(404, {"error": "no existe"})

        def _accion_bot(self, ruta: str) -> None:
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if not 0 <= n <= MAX_BODY:
                return self._json(400, {"error": "cuerpo demasiado grande"})
            try:
                datos = json.loads(self.rfile.read(n)) if n else {}
            except (ValueError, UnicodeDecodeError):
                return self._json(400, {"error": "JSON inválido"})
            try:
                self._json(200, gestor.iniciar(datos) if ruta == "/bot/iniciar" else gestor.detener())
            except ErrorGestor as e:
                self._json(e.status, {"error": e.mensaje})

        def do_POST(self):
            if not self._guardia(True):
                return
            ruta = self.path.split("?")[0]
            if ruta in ("/bot/iniciar", "/bot/detener"):
                return self._accion_bot(ruta)
            if ruta != "/reglas":
                return self._json(404, {"error": "no existe"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if not 0 < n <= MAX_BODY:
                return self._json(400, {"error": "cuerpo vacío o demasiado grande"})
            try:
                data = json.loads(self.rfile.read(n))
            except (ValueError, UnicodeDecodeError):
                return self._json(400, {"error": "JSON inválido"})
            try:
                errores = R.validar(data, R.cargar_limites(limites_path))
            except R.ReglasInvalidas as e:
                return self._json(500, {"error": str(e)})
            if errores:
                return self._json(400, {"errores": errores})
            escribir_atomico(reglas_path, data)
            self._json(200, {"ok": True})

    srv = ThreadingHTTPServer((host or host_configurado(), puerto_configurado() if port is None else port), Handler)
    srv.gestor = gestor
    return srv


def main() -> None:
    ap = argparse.ArgumentParser(description="Panel de control del bot (reglas + iniciar/detener)")
    ap.add_argument("--port", type=int, default=puerto_configurado(), help="por defecto PANEL_PORT o 8002")
    ap.add_argument("--host", default=host_configurado(), help="por defecto PANEL_HOST o 127.0.0.1")
    a = ap.parse_args()
    srv = make_server(port=a.port, host=a.host)
    atexit.register(srv.gestor.cerrar)
    if hasattr(signal, "SIGBREAK"):   # Ctrl+Break / cierre de la consola en Windows: salir por el finally
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    print(f"Panel del bot en http://{a.host}:{srv.server_address[1]}/  (Ctrl+C para salir; al salir se cierra el bot si sigue vivo)")
    if a.host in ("0.0.0.0", "::"):
        print("AVISO: escucha en todas las interfaces y el panel NO tiene autenticación: cualquiera de la red puede "
              "iniciar y detener el bot. Para entrar por la IP de la LAN usa PANEL_ALLOWED_HOSTS=<ip>.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.gestor.cerrar()
        srv.server_close()


if __name__ == "__main__":
    main()
