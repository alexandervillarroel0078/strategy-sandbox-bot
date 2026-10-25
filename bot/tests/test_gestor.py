"""Gestor del bot y rutas /bot/* del panel. Sin lanzar el bot real: un proceso falso (python -c) hace de bot."""
import http.client
import json
import sys
import threading
import time

import pytest

from bot import gestor as G
from bot.gestor import ErrorGestor, GestorBot, construir_comando, validar
from bot.panel_server import make_server

OK = {"inicio": "2025-09-01", "fin": "2025-09-07", "estrategia": "ejemplo"}


def falso(codigo: str):
    """Comando que sustituye al bot: ejecuta `codigo` con el mismo Python."""
    return lambda p: [sys.executable, "-u", "-c", codigo]


IMPRIME = ("print('Bot: ejemplo'); print('Registro: bot/logs/bot_x.jsonl · feed: bot/logs/feed_x.csv'); "
           "print('Retorno estrategia                -0.72%                            '); "
           "print('Nº de operaciones                 1                                 ')")
DUERME = "import time; print('arranco', flush=True); time.sleep(60)"


@pytest.fixture
def exchange_vivo(monkeypatch):
    monkeypatch.setattr(G, "exchange_responde", lambda url, timeout=2.0: True)


def esperar(g, estados, seg=15):
    t = time.time()
    while time.time() - t < seg:
        e = g.estado()
        if e["estado"] in estados:
            return e
        time.sleep(0.05)
    raise AssertionError(f"estado {g.estado()['estado']!r}, se esperaba {estados}")


@pytest.fixture
def gestor():
    g = GestorBot(api_url="http://127.0.0.1:9", comando=falso(IMPRIME))
    yield g
    g.cerrar()


# ---------------- validación y comando ----------------
@pytest.mark.parametrize("malo", [
    {**OK, "inicio": "2025-9-1"}, {**OK, "inicio": "2025-02-30"}, {**OK, "fin": None}, {**OK, "fin": "ayer"},
    {**OK, "inicio": "2025-09-08"}, {**OK, "estrategia": "ejemplo; calc"}, {**OK, "estrategia": "base"},
    {**OK, "estrategia": None}, {"inicio": "2025-09-01"}, [], "x", None])
def test_validar_rechaza(malo):
    with pytest.raises(ErrorGestor) as e:
        validar(malo)
    assert e.value.status == 400


def test_validar_acepta_y_ignora_campos_extra():
    assert validar({**OK, "ejecucion": "interna", "x": 1}) == OK


@pytest.mark.parametrize("estrategia", G.ESTRATEGIAS)
def test_comando_exacto_y_sin_shell(estrategia):
    cmd = construir_comando(validar({**OK, "estrategia": estrategia}))
    assert cmd == [sys.executable, "-m", "bot.run_bot_api", "--start", "2025-09-01", "--end", "2025-09-07",
                   "--strategy", estrategia, "--ejecucion", "exchange", "--log", "--log-nivel", "completo"]
    assert isinstance(cmd, list)


def test_comando_nunca_usa_ejecucion_interna():
    # ni siquiera si el formulario intenta colarla
    cmd = construir_comando(validar({**OK, "ejecucion": "interna"}))
    assert "interna" not in cmd and "--ejecucion interna" not in " ".join(cmd)
    assert cmd[cmd.index("--ejecucion") + 1] == "exchange"


# ---------------- exchange y exclusión ----------------
def test_exchange_caido_no_lanza_el_bot(monkeypatch):
    lanzado = []
    monkeypatch.setattr(G.subprocess, "Popen", lambda *a, **k: lanzado.append(a))
    g = GestorBot(api_url="http://127.0.0.1:9")          # puerto 9: nadie escucha
    with pytest.raises(ErrorGestor) as e:
        g.iniciar(OK)
    assert e.value.status == 503 and e.value.mensaje == "arranca primero el exchange (repo strategy-sandbox-exchange, ver su README)"
    assert not lanzado and g.estado()["estado"] == "sin_iniciar"


def test_exchange_responde_con_servidor_falso():
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == "/api/info" else 404)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a): pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert G.exchange_responde(f"http://127.0.0.1:{srv.server_address[1]}")
    finally:
        srv.shutdown()
        srv.server_close()
    assert not G.exchange_responde("http://127.0.0.1:9")


def test_no_permite_dos_bots(exchange_vivo):
    g = GestorBot(api_url="x", comando=falso(DUERME))
    try:
        g.iniciar(OK)
        with pytest.raises(ErrorGestor) as e:
            g.iniciar(OK)
        assert e.value.status == 409
    finally:
        g.cerrar()


# ---------------- estados ----------------
def test_estado_inicial(gestor):
    e = gestor.estado()
    assert e["estado"] == "sin_iniciar" and e["lineas"] == [] and e["operaciones"] is None


def test_terminado_muestra_operaciones_y_retorno(exchange_vivo, gestor):
    gestor.iniciar(OK)
    e = esperar(gestor, {"terminado", "error"})
    assert e["estado"] == "terminado" and e["codigo"] == 0
    assert e["operaciones"] == 1 and e["retorno"] == "-0.72%"
    assert e["registro"] == "bot/logs/bot_x.jsonl" and "aviso" not in e
    assert e["parametros"] == OK and "Bot: ejemplo" in e["lineas"]


def test_error_si_el_codigo_no_es_cero(exchange_vivo):
    g = GestorBot(api_url="x", comando=falso("print('Error del exchange simulado: algo'); raise SystemExit(1)"))
    g.iniciar(OK)
    e = esperar(g, {"terminado", "error"})
    assert e["estado"] == "error" and e["codigo"] == 1 and e["operaciones"] is None
    assert any("Error del exchange" in x for x in e["lineas"])


def test_detener_deja_estado_detenido_con_aviso(exchange_vivo):
    g = GestorBot(api_url="x", comando=falso(DUERME))
    g.iniciar(OK)
    esperar(g, {"corriendo"})
    proc = g._proc
    e = g.detener()
    assert e["estado"] == "detenido" and e["estado"] != "error"
    assert e["aviso"].startswith("Corrida detenida a mitad: la posición puede haber quedado abierta en el exchange.")
    assert e["aviso"].endswith("No usar este resultado para evaluar una idea.")
    assert proc.poll() is not None
    with pytest.raises(ErrorGestor) as err:
        g.detener()
    assert err.value.status == 409


def test_detener_solo_cierra_el_proceso_propio(exchange_vivo):
    import subprocess
    ajeno = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    g = GestorBot(api_url="x", comando=falso(DUERME))
    try:
        g.iniciar(OK)
        esperar(g, {"corriendo"})
        g.detener()
        assert ajeno.poll() is None            # otro Python cualquiera sigue vivo
    finally:
        ajeno.kill()
        ajeno.wait()
        g.cerrar()


def test_cerrar_mata_el_bot_si_sigue_vivo(exchange_vivo):
    g = GestorBot(api_url="x", comando=falso(DUERME))
    g.iniciar(OK)
    proc = g._proc
    g.cerrar()
    assert proc.poll() is not None
    g.cerrar()                                  # idempotente


def test_se_puede_iniciar_otra_corrida_tras_terminar(exchange_vivo, gestor):
    gestor.iniciar(OK)
    esperar(gestor, {"terminado"})
    gestor.iniciar({**OK, "estrategia": "donchian"})
    e = esperar(gestor, {"terminado"})
    assert e["parametros"]["estrategia"] == "donchian"


def test_la_salida_larga_no_bloquea_ni_crece(exchange_vivo):
    g = GestorBot(api_url="x", comando=falso("for i in range(5000): print('linea', i)"), max_lineas=50)
    g.iniciar(OK)
    e = esperar(g, {"terminado"})
    assert len(e["lineas"]) == 50 and e["lineas"][-1] == "linea 4999"


# ---------------- rutas HTTP del panel ----------------
@pytest.fixture
def panel(tmp_path, exchange_vivo):
    (tmp_path / "r.yaml").write_text("activo: false\nzonas: []\nreglas: []\n", encoding="utf-8")
    g = GestorBot(api_url="x", comando=falso(DUERME))
    s = make_server(str(tmp_path / "r.yaml"), port=0, gestor=g)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s, g
    g.cerrar()
    s.shutdown()
    s.server_close()


def pedir(s, metodo, ruta, cuerpo=None, **headers):
    port = s.server_address[1]
    h = {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json", **headers}
    h = {k: v for k, v in h.items() if v is not None}
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    data = None if cuerpo is None else (cuerpo if isinstance(cuerpo, bytes) else json.dumps(cuerpo).encode())
    h["Content-Length"] = str(len(data or b""))
    c.putrequest(metodo, ruta, skip_host=True, skip_accept_encoding=True)
    for k, v in h.items():
        c.putheader(k, v)
    c.endheaders(data)
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, json.loads(body)


def test_rutas_iniciar_estado_detener(panel):
    s, g = panel
    assert pedir(s, "GET", "/bot/estado")[1]["estado"] == "sin_iniciar"
    st, j = pedir(s, "POST", "/bot/iniciar", OK)
    assert st == 200 and j["estado"] in ("corriendo",)
    assert pedir(s, "POST", "/bot/iniciar", OK)[0] == 409
    assert pedir(s, "GET", "/bot/estado")[1]["estado"] == "corriendo"
    st, j = pedir(s, "POST", "/bot/detener", b"")           # sin cuerpo
    assert st == 200 and j["estado"] == "detenido" and "evaluar una idea" in j["aviso"]
    assert pedir(s, "POST", "/bot/detener", b"")[0] == 409


def test_rutas_validan_y_protegen(panel):
    s, _ = panel
    assert pedir(s, "POST", "/bot/iniciar", {**OK, "estrategia": "x"})[0] == 400
    assert pedir(s, "POST", "/bot/iniciar", b"no es json")[0] == 400
    assert pedir(s, "POST", "/bot/iniciar", OK, Origin="http://evil.example")[0] == 403
    assert pedir(s, "POST", "/bot/iniciar", OK, Host="evil.example")[0] == 403
    assert pedir(s, "POST", "/bot/iniciar", OK, **{"Content-Type": "text/plain"})[0] == 415
    assert pedir(s, "GET", "/bot/estado", Host="evil.example")[0] == 403
    assert pedir(s, "GET", "/bot/estado")[1]["estado"] == "sin_iniciar"     # nada se lanzó


def test_panel_sin_exchange_devuelve_el_aviso(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "exchange_responde", lambda url, timeout=2.0: False)
    (tmp_path / "r.yaml").write_text("activo: false\nzonas: []\nreglas: []\n", encoding="utf-8")
    g = GestorBot(api_url="x", comando=falso(DUERME))
    s = make_server(str(tmp_path / "r.yaml"), port=0, gestor=g)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        st, j = pedir(s, "POST", "/bot/iniciar", OK)
        assert st == 503 and j["error"] == "arranca primero el exchange (repo strategy-sandbox-exchange, ver su README)"
        assert g.estado()["estado"] == "sin_iniciar"
    finally:
        s.shutdown()
        s.server_close()
