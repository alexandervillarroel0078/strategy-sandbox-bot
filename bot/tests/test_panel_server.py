"""Servidor del panel: solo localhost, Host/Origin/Content-Type, escritura atómica y POST inválido sin efecto."""
import http.client
import json
import threading

import pytest
import yaml

from bot.panel_server import host_ok, make_server

VALIDO = {"activo": True,
          "zonas": [{"nombre": "S1", "tipo": "soporte", "desde": 100, "hasta": 105, "activa": True}],
          "reglas": [{"zona": "S1", "accion": "abrir_long", "stop_pct": 3}]}
INICIAL = "activo: false\nzonas: []\nreglas: []\n"
LIM = {"riesgo_max_pct": 2, "stop_max_pct": 10, "parcial_min_pct": 10, "parcial_max_pct": 90,
       "max_zonas": 10, "max_reglas_activas": 10}
CFG = {"stop_pct": 0.05, "target_pct": 0.10, "position_fraction": 1.0}


@pytest.fixture
def srv(tmp_path):
    (tmp_path / "r.yaml").write_text(INICIAL, encoding="utf-8")
    (tmp_path / "l.yaml").write_text(yaml.safe_dump(LIM))
    (tmp_path / "c.yaml").write_text(yaml.safe_dump(CFG))
    s = make_server(str(tmp_path / "r.yaml"), str(tmp_path / "l.yaml"), str(tmp_path / "c.yaml"), port=0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s, tmp_path / "r.yaml"
    s.shutdown()
    s.server_close()


def pedir(s, metodo, ruta, cuerpo=None, **headers):
    port = s.server_address[1]
    h = {"Host": f"127.0.0.1:{port}", **headers}
    if "Content-Type" not in h and cuerpo is not None:
        h["Content-Type"] = "application/json"
    h = {k: v for k, v in h.items() if v is not None}
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.putrequest(metodo, ruta, skip_host=True, skip_accept_encoding=True)
    data = None if cuerpo is None else (cuerpo if isinstance(cuerpo, bytes) else json.dumps(cuerpo).encode())
    if data is not None:
        h["Content-Length"] = str(len(data))
    for k, v in h.items():
        c.putheader(k, v)
    c.endheaders(data)
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, (json.loads(body) if r.getheader("Content-Type", "").startswith("application/json") else body)


def test_escucha_solo_en_localhost(srv):
    assert srv[0].server_address[0] == "127.0.0.1"


def test_host_ok():
    assert host_ok("127.0.0.1:8002") and host_ok("localhost") and host_ok("LOCALHOST:8002")
    assert not host_ok("evil.com") and not host_ok("127.0.0.1.evil.com") and not host_ok(None) and not host_ok("")


def test_get_en_localhost(srv):
    s, _ = srv
    assert pedir(s, "GET", "/reglas") == (200, {"activo": False, "zonas": [], "reglas": []})
    st, j = pedir(s, "GET", "/limites")
    assert st == 200 and j["limites"]["stop_max_pct"] == 10 and j["globales"]["stop_pct"] == 5
    st, html = pedir(s, "GET", "/")
    assert st == 200 and b"Panel del bot" in html
    assert pedir(s, "GET", "/nada")[0] == 404


def test_rechaza_host_externo(srv):
    s, archivo = srv
    for host in ("evil.com", "evil.com:8002", "10.0.0.5:8002", None):
        assert pedir(s, "GET", "/reglas", Host=host)[0] == 403
        assert pedir(s, "POST", "/reglas", VALIDO, Host=host)[0] == 403
    assert archivo.read_text(encoding="utf-8") == INICIAL


def test_post_valido_escribe(srv):
    s, archivo = srv
    assert pedir(s, "POST", "/reglas", VALIDO) == (200, {"ok": True})
    assert yaml.safe_load(archivo.read_text(encoding="utf-8")) == VALIDO
    assert pedir(s, "GET", "/reglas")[1] == VALIDO          # persiste
    assert not list(archivo.parent.glob("*.tmp"))


def test_post_invalido_devuelve_errores_y_no_modifica(srv):
    s, archivo = srv
    malo = json.loads(json.dumps(VALIDO))
    malo["reglas"][0]["stop_pct"] = 50
    malo["zonas"][0]["desde"] = 200
    st, j = pedir(s, "POST", "/reglas", malo)
    assert st == 400 and {e["campo"] for e in j["errores"]} == {"reglas.0.stop_pct", "zonas.0.desde"}
    assert archivo.read_text(encoding="utf-8") == INICIAL
    assert pedir(s, "POST", "/reglas", b"{no es json")[0] == 400
    assert archivo.read_text(encoding="utf-8") == INICIAL


@pytest.mark.parametrize("origen", ["http://evil.com", "http://127.0.0.1:9999", "null", "https://localhost"])
def test_post_con_otro_origin_rechazado(srv, origen):
    s, archivo = srv
    st, _ = pedir(s, "POST", "/reglas", VALIDO, Origin=origen)
    assert 400 <= st < 500
    assert archivo.read_text(encoding="utf-8") == INICIAL


def test_post_con_origin_propio_ok(srv):
    s, _ = srv
    port = s.server_address[1]
    assert pedir(s, "POST", "/reglas", VALIDO, Origin=f"http://localhost:{port}")[0] == 200


@pytest.mark.parametrize("tipo", ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data"])
def test_post_exige_content_type_json(srv, tipo):
    s, archivo = srv
    st, _ = pedir(s, "POST", "/reglas", VALIDO, **{"Content-Type": tipo})
    assert 400 <= st < 500
    assert archivo.read_text(encoding="utf-8") == INICIAL


# ---- /bot/datos: solo lectura del final de bot/logs/ (sin lanzar el bot) ----
class GestorFalso:
    """Solo lo que usa /bot/datos: la hora de arranque del bot."""
    def __init__(self, desde=None):
        self._desde = desde

    def desde(self):
        return self._desde

    def estado(self):
        return {"estado": "sin_iniciar"}

    def cerrar(self):
        pass


def _srv_datos(tmp_path, desde=0.0):
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    (tmp_path / "r.yaml").write_text(INICIAL, encoding="utf-8")
    (tmp_path / "l.yaml").write_text(yaml.safe_dump(LIM))
    (tmp_path / "c.yaml").write_text(yaml.safe_dump(CFG))
    s = make_server(str(tmp_path / "r.yaml"), str(tmp_path / "l.yaml"), str(tmp_path / "c.yaml"), port=0,
                    gestor=GestorFalso(desde), logs_dir=str(logs))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, logs


@pytest.fixture
def datos(tmp_path):
    s, logs = _srv_datos(tmp_path)
    yield s, logs
    s.shutdown()
    s.server_close()


def _ev(tipo, **d):
    return json.dumps({"evento": tipo, **d}, ensure_ascii=False)


def _escribir_log(logs, n_pasos=30, n_dec=14, feed="feed_20250101-000000.csv", extra=""):
    lineas = [_ev("inicio", estrategia="ejemplo", feed=feed)]
    for i in range(n_pasos):
        lineas.append(_ev("step", ahora=1_756_684_800_000 + i * 900_000, intervalo="15m", velas_nuevas=1))
    for i in range(n_dec):
        lineas.append(_ev("decision", ahora=1_756_688_400_000 + i * 3_600_000, accion="HOLD", motivo="sin señal",
                          en_posicion=False, valores={"precio": 100.0 + i, "velas": 600, "rsi": 40.5, "ema50": 99.0,
                                                      "ema200": 98.0, "secreto": "x"}))
    (logs / "ultimo.jsonl").write_text("\n".join(lineas) + "\n" + extra, encoding="utf-8")
    if feed:
        filas = ["ahora,fuente,intervalo,open_time,open,high,low,close,volume,close_time"]
        for i in range(25):
            ot = 1_756_684_800_000 + i * 900_000
            filas.append(f"{ot + 900_000},{'step' if i % 2 else 'klines'},15m,{ot},{100 + i},{101 + i},{99 + i},{100.5 + i},1,{ot + 899_999}")
        (logs / feed).write_text("\n".join(filas) + "\n", encoding="utf-8")


def test_datos_sin_logs_es_sin_datos(datos):
    st, j = pedir(datos[0], "GET", "/bot/datos")
    assert st == 200 and j["estado"] == "sin_datos" and j["velas"] == [] and j["decisiones"] == []


def test_datos_sin_bot_lanzado_desde_el_panel_es_sin_datos(tmp_path):
    s, logs = _srv_datos(tmp_path, desde=None)
    try:
        _escribir_log(logs)
        assert pedir(s, "GET", "/bot/datos")[1]["estado"] == "sin_datos"
    finally:
        s.shutdown(); s.server_close()


def test_datos_log_mas_antiguo_que_el_arranque_se_oculta(tmp_path):
    s, logs = _srv_datos(tmp_path, desde=4_000_000_000.0)   # arranque "en el futuro": el log es de antes
    try:
        _escribir_log(logs)
        assert pedir(s, "GET", "/bot/datos")[1]["estado"] == "sin_datos"
    finally:
        s.shutdown(); s.server_close()


def test_datos_devuelve_las_ultimas_10_y_campos_fijos(datos):
    _escribir_log(datos[1])
    st, j = pedir(datos[0], "GET", "/bot/datos")
    assert st == 200 and j["estado"] == "ok"
    assert len(j["velas"]) == 10 and len(j["decisiones"]) == 10
    assert [d["precio"] for d in j["decisiones"]] == [104.0 + i for i in range(10)]   # las últimas, en orden
    assert j["velas"][-1]["open"] == 124.0 and j["velas"][-1]["fuente"] == "klines"
    assert set(j["velas"][0]) == {"ahora_iso", "hora", "fuente", "intervalo", "open", "high", "low", "close"}
    assert set(j["decisiones"][0]) == {"ahora_iso", "accion", "motivo", "precio", "rsi", "ema50", "ema200"}
    assert j["decisiones"][0]["rsi"] == 40.5 and j["decisiones"][0]["motivo"] == "sin señal"
    assert j["ahora_iso"] == "2025-09-01 14:00:00"    # ahora de la última decisión (la más reciente)


def test_datos_ignora_linea_final_cortada(datos):
    _escribir_log(datos[1], extra='{"evento": "step", "ahora": 17566')
    st, j = pedir(datos[0], "GET", "/bot/datos")
    assert st == 200 and j["estado"] == "ok" and len(j["decisiones"]) == 10


def test_datos_descarta_fila_final_del_feed_cortada(datos):
    _escribir_log(datos[1])
    with open(datos[1] / "feed_20250101-000000.csv", "a", encoding="utf-8") as f:
        f.write("1756688400000,klines,15m,1756688400000,1,2")     # fila a medio escribir: solo 2 de 4 precios
    st, j = pedir(datos[0], "GET", "/bot/datos")
    assert st == 200 and j["estado"] == "ok" and len(j["velas"]) == 10
    assert j["velas"][-1]["open"] == 124.0                         # la última válida; la cortada no entra


def test_datos_sin_feed_muestra_decisiones_y_ninguna_vela(datos):
    _escribir_log(datos[1], feed=None)
    j = pedir(datos[0], "GET", "/bot/datos")[1]
    assert j["estado"] == "ok" and j["velas"] == [] and len(j["decisiones"]) == 10


@pytest.mark.parametrize("nombre", ["../secreto.csv", "..\\secreto.csv", "feed_x.csv", "C:/Windows/win.ini"])
def test_datos_no_lee_feed_con_nombre_raro(datos, nombre):
    (datos[1].parent / "secreto.csv").write_text("1,step,15m,1,1,1,1,1,1,1\n", encoding="utf-8")
    _escribir_log(datos[1], feed=nombre if "/" not in nombre and "\\" not in nombre else None)
    lineas = (datos[1] / "ultimo.jsonl").read_text(encoding="utf-8").split("\n")
    lineas[0] = _ev("inicio", feed=nombre)
    (datos[1] / "ultimo.jsonl").write_text("\n".join(lineas), encoding="utf-8")
    assert pedir(datos[0], "GET", "/bot/datos")[1]["velas"] == []


def test_datos_con_archivo_grande_solo_lee_el_final(datos, monkeypatch):
    from bot import datos_vivo
    leidos = []
    real = datos_vivo._cola
    monkeypatch.setattr(datos_vivo, "_cola", lambda r, mx, b: leidos.append(r) or real(r, mx, b))
    _escribir_log(datos[1], n_pasos=40_000, n_dec=14)    # ~3 MB: mucho más que el tope de 1 MB
    assert (datos[1] / "ultimo.jsonl").stat().st_size > 3 * datos_vivo.MAX_JSONL
    j = pedir(datos[0], "GET", "/bot/datos")[1]
    assert j["estado"] == "ok" and len(j["decisiones"]) == 10 and leidos


def test_datos_rechaza_host_externo(datos):
    assert pedir(datos[0], "GET", "/bot/datos", Host="evil.com")[0] == 403


def _escribir_velas(logs, filas, intervalo="1h", paso="15m", feed="feed_20250101-000000.csv"):
    """filas: (fuente, intervalo, open_time, close). Escribe ultimo.jsonl (solo `inicio`) y el feed."""
    (logs / "ultimo.jsonl").write_text(_ev("inicio", intervalo=intervalo, paso=paso, feed=feed) + "\n", encoding="utf-8")
    out = ["ahora,fuente,intervalo,open_time,open,high,low,close,volume,close_time"]
    for fu, iv, ot, c in filas:
        out.append(f"{ot + 1},{fu},{iv},{ot},{c - 1},{c + 2},{c - 2},{c},1,{ot + 1}")
    (logs / feed).write_text("\n".join(out) + "\n", encoding="utf-8")


H = 3_600_000
T0 = 1_756_684_800_000


def test_velas_sin_logs_o_sin_panel_es_sin_datos(datos, tmp_path):
    st, j = pedir(datos[0], "GET", "/bot/velas")
    assert st == 200 and j["estado"] == "sin_datos" and j["velas"] == [] and j["intervalos"] == []
    s, logs = _srv_datos(tmp_path / "otro", desde=None) if (tmp_path / "otro").mkdir() is None else (None, None)
    try:
        _escribir_velas(logs, [("klines", "1h", T0, 100)])
        assert pedir(s, "GET", "/bot/velas")[1]["estado"] == "sin_datos"
    finally:
        s.shutdown(); s.server_close()


def test_velas_log_de_una_corrida_anterior_se_oculta(tmp_path):
    s, logs = _srv_datos(tmp_path, desde=4_000_000_000.0)
    try:
        _escribir_velas(logs, [("klines", "1h", T0, 100)])
        assert pedir(s, "GET", "/bot/velas")[1]["estado"] == "sin_datos"
    finally:
        s.shutdown(); s.server_close()


def test_velas_campos_fijos_e_intervalo_por_defecto(datos):
    _escribir_velas(datos[1], [("klines", "1h", T0, 100), ("step", "15m", T0 + 900_000, 101), ("klines", "1h", T0 + H, 102)])
    st, j = pedir(datos[0], "GET", "/bot/velas")
    assert st == 200 and j["estado"] == "ok"
    assert set(j) == {"estado", "intervalo", "intervalos", "corrida", "ahora_iso", "ultima_t", "restantes", "hay_mas",
                      "reinicio", "velas"}
    assert j["corrida"] == "feed_20250101-000000.csv" and j["ultima_t"] == T0 + H
    assert (j["restantes"], j["hay_mas"], j["reinicio"]) == (0, False, False)
    assert j["intervalo"] == "1h" and j["intervalos"] == ["1h", "15m"]
    assert [v["t"] for v in j["velas"]] == [T0, T0 + H]          # solo 1h, sin la de 15m
    assert set(j["velas"][0]) == {"t", "hora", "open", "high", "low", "close", "volume"}
    assert j["velas"][0]["volume"] == 1.0
    assert j["velas"][0]["hora"] == "2025-09-01 00:00:00" and j["ahora_iso"] == "2025-09-01 01:00:00"


def test_velas_selector_de_intervalo_y_valor_invalido(datos):
    _escribir_velas(datos[1], [("klines", "1h", T0, 100), ("step", "15m", T0 + 900_000, 101)])
    assert pedir(datos[0], "GET", "/bot/velas?intervalo=15m")[1]["intervalo"] == "15m"
    assert [v["close"] for v in pedir(datos[0], "GET", "/bot/velas?intervalo=15m")[1]["velas"]] == [101.0]
    for malo in ("5m", "../x", "1h%0d%0a", ""):
        assert pedir(datos[0], "GET", "/bot/velas?intervalo=" + malo)[1]["intervalo"] == "1h"


def test_velas_repetida_o_actualizada_gana_la_ultima(datos):
    _escribir_velas(datos[1], [("klines", "1h", T0, 100), ("klines", "1h", T0 + H, 110), ("step", "1h", T0 + H, 115),
                               ("klines", "1h", T0 + H, 115)])
    vs = pedir(datos[0], "GET", "/bot/velas")[1]["velas"]
    assert [(v["t"], v["close"]) for v in vs] == [(T0, 100.0), (T0 + H, 115.0)]


def test_velas_recorta_a_200_en_orden(datos):
    _escribir_velas(datos[1], [("klines", "1h", T0 + i * H, 100 + i) for i in range(250)])
    vs = pedir(datos[0], "GET", "/bot/velas")[1]["velas"]
    assert len(vs) == 200 and vs[0]["t"] == T0 + 50 * H and vs[-1]["close"] == 349.0
    assert [v["t"] for v in vs] == sorted(v["t"] for v in vs)


def test_velas_ignora_filas_corruptas_y_no_finitas(datos):
    _escribir_velas(datos[1], [("klines", "1h", T0, 100)])
    with open(datos[1] / "feed_20250101-000000.csv", "a", encoding="utf-8") as f:
        f.write("x,klines,1h,1,1,1,1,1\n1,klines,1h,2,nan,1,1,1,1,1\n1,otra,1h,3,1,1,1,1,1,1\n1756688400000,klines,1h,17566")
    vs = pedir(datos[0], "GET", "/bot/velas")[1]["velas"]
    assert [v["t"] for v in vs] == [T0]


def test_velas_volumen_de_la_ultima_version_y_filas_con_volumen_malo_se_ignoran(datos):
    _escribir_velas(datos[1], [("klines", "1h", T0, 100)])
    with open(datos[1] / "feed_20250101-000000.csv", "a", encoding="utf-8") as f:
        f.write(f"1,step,1h,{T0},1,3,0,2,7.5,1\n")                  # reescribe la vela T0 con otro volumen
        f.write(f"1,klines,1h,{T0 + H},1,1,1,1,-5,1\n")             # volumen negativo
        f.write(f"1,klines,1h,{T0 + 2 * H},1,1,1,1,nan,1\n")        # volumen no finito
        f.write(f"1,klines,1h,{T0 + 3 * H},1,1,1,1,\n")             # sin volumen (fila cortada)
    vs = pedir(datos[0], "GET", "/bot/velas")[1]["velas"]
    assert [(v["t"], v["volume"], v["close"]) for v in vs] == [(T0, 7.5, 2.0)]


def test_velas_sin_feed_o_feed_raro_es_sin_datos(datos):
    _escribir_velas(datos[1], [("klines", "1h", T0, 100)], feed="feed_x.csv")
    assert pedir(datos[0], "GET", "/bot/velas")[1]["estado"] == "sin_datos"


def test_velas_con_feed_grande_solo_lee_el_final(datos, monkeypatch):
    from bot import datos_vivo
    _escribir_velas(datos[1], [("step", "15m", T0 + i * 900_000, 100 + i % 50) for i in range(40_000)])
    feed = datos[1] / "feed_20250101-000000.csv"
    assert feed.stat().st_size > 2 * datos_vivo.MAX_FEED_VELAS
    leidos = []
    abrir = open
    def espia(ruta, modo="r", *a, **k):
        f = abrir(ruta, modo, *a, **k)
        if str(ruta).endswith(".csv"):
            real = f.read
            f.read = lambda n=-1: (leidos.append(n), real(n))[1]
        return f
    monkeypatch.setattr("builtins.open", espia)
    j = pedir(datos[0], "GET", "/bot/velas?intervalo=15m")[1]
    monkeypatch.undo()
    assert len(j["velas"]) == 200 and j["velas"][-1]["t"] == T0 + 39_999 * 900_000
    assert leidos and all(0 < n <= datos_vivo.MAX_FEED_VELAS for n in leidos)


def test_velas_desde_devuelve_solo_las_posteriores_e_incluye_la_marca(datos):
    _escribir_velas(datos[1], [("klines", "1h", T0 + i * H, 100 + i) for i in range(5)] + [("klines", "1h", T0 + 4 * H, 150)])
    j = pedir(datos[0], "GET", f"/bot/velas?desde={T0 + 2 * H}")[1]
    assert [v["t"] for v in j["velas"]] == [T0 + 2 * H, T0 + 3 * H, T0 + 4 * H]     # la marca vuelve (por si cambió)
    assert j["velas"][-1]["close"] == 150.0 and j["ultima_t"] == T0 + 4 * H          # y la reescrita trae su última versión
    assert (j["restantes"], j["hay_mas"], j["reinicio"]) == (0, False, False)
    assert pedir(datos[0], "GET", f"/bot/velas?desde={T0 + 4 * H}")[1]["velas"][0]["t"] == T0 + 4 * H
    assert pedir(datos[0], "GET", f"/bot/velas?desde={T0 + 99 * H}")[1]["velas"] == []   # marca por delante: nada nuevo


def test_velas_desde_en_lotes_sin_faltar_ni_repetir(datos, monkeypatch):
    from bot import datos_vivo
    monkeypatch.setattr(datos_vivo, "MAX_LOTE", 7)
    _escribir_velas(datos[1], [("step", "15m", T0 + i * 900_000, 100 + i) for i in range(30)])
    ruta = "/bot/velas?intervalo=15m"
    j = pedir(datos[0], "GET", ruta + f"&desde={T0}")[1]
    vistos, vueltas = [], 0
    while True:
        vueltas += 1
        vistos += [v["t"] for v in j["velas"]]
        if not j["hay_mas"]:
            break
        assert j["restantes"] > 0 and len(j["velas"]) == 7
        j = pedir(datos[0], "GET", ruta + f"&desde={j['velas'][-1]['t']}")[1]
    assert vueltas == 5 and sorted(set(vistos)) == [T0 + i * 900_000 for i in range(30)]
    assert len(vistos) == 30 + (vueltas - 1)       # solo se repite la marca de cada lote (la página la funde)


def test_velas_desde_fuera_de_lo_leido_pide_reinicio(datos):
    from bot import datos_vivo
    _escribir_velas(datos[1], [("step", "15m", T0 + i * 900_000, 100 + i % 50) for i in range(40_000)])
    assert (datos[1] / "feed_20250101-000000.csv").stat().st_size > datos_vivo.MAX_FEED_VELAS
    j = pedir(datos[0], "GET", f"/bot/velas?intervalo=15m&desde={T0}")[1]       # la marca está al principio del archivo
    assert j["reinicio"] is True and len(j["velas"]) == 200 and j["restantes"] == 0 and j["hay_mas"] is False
    assert j["velas"][-1]["t"] == T0 + 39_999 * 900_000
    j = pedir(datos[0], "GET", f"/bot/velas?intervalo=15m&desde={T0 + 39_990 * 900_000}")[1]    # una marca reciente sí
    assert j["reinicio"] is False and len(j["velas"]) == 10


@pytest.mark.parametrize("malo", ["abc", "-5", "1.5", "", "99999999999999999999", "1%20or%201"])
def test_velas_desde_invalido_se_ignora(datos, malo):
    _escribir_velas(datos[1], [("klines", "1h", T0 + i * H, 100 + i) for i in range(5)])
    j = pedir(datos[0], "GET", "/bot/velas?desde=" + malo)[1]
    assert len(j["velas"]) == 5 and j["reinicio"] is False


def test_velas_corrida_nueva_cambia_el_nombre(datos):
    _escribir_velas(datos[1], [("klines", "1h", T0, 100)], feed="feed_20250101-000000.csv")
    a = pedir(datos[0], "GET", "/bot/velas")[1]["corrida"]
    _escribir_velas(datos[1], [("klines", "1h", T0, 100)], feed="feed_20250101-000500.csv")
    assert (a, pedir(datos[0], "GET", "/bot/velas")[1]["corrida"]) == ("feed_20250101-000000.csv", "feed_20250101-000500.csv")


def test_grafico_se_sirve_con_ruta_fija_y_tipo_correcto(datos):
    import hashlib
    from bot.panel_server import ARCHIVO_GRAFICO
    conn = http.client.HTTPConnection("127.0.0.1", datos[0].server_address[1], timeout=5)
    conn.request("GET", "/lightweight-charts.js")
    r = conn.getresponse(); cuerpo = r.read(); conn.close()
    assert r.status == 200 and r.getheader("Content-Type").startswith("text/javascript")
    with open(ARCHIVO_GRAFICO, "rb") as f:
        assert cuerpo == f.read() and b"Lightweight Charts" in cuerpo[:300] and b"v4.1.3" in cuerpo[:300]
    assert hashlib.sha256(cuerpo).hexdigest() == "0c146b9bfbc6e5f19da1a0de876dae43023c332d5157d82f7575f9b7653ee519"
    assert pedir(datos[0], "GET", "/lightweight-charts.js", Host="evil.com")[0] == 403      # misma guardia de Host


@pytest.mark.parametrize("ruta", [
    "/panel/index.html", "/panel/lightweight-charts.standalone.production.js", "/lightweight-charts.standalone.production.js",
    "/LICENSE-lightweight-charts.txt", "/panel/LICENSE-lightweight-charts.txt", "/static/x", "/lightweight-charts.js/", "/lightweight-charts.JS",
    "/lightweight-charts.js/../index.html", "/../panel_server.py", "/..%2fpanel_server.py", "/%2e%2e/panel_server.py",
    "/panel/../panel_server.py", "/lightweight-charts.js%00.html", "/bot/lightweight-charts.js"])
def test_ninguna_otra_ruta_estatica_funciona(datos, ruta):
    conn = http.client.HTTPConnection("127.0.0.1", datos[0].server_address[1], timeout=5)
    conn.request("GET", ruta)
    r = conn.getresponse(); cuerpo = r.read(); conn.close()
    assert r.status == 404 and b"function" not in cuerpo and b"<html" not in cuerpo.lower()


def test_pagina_sin_urls_externas_ni_referencias_al_exchange_ni_innerhtml():
    import re
    from bot.panel_server import INDEX
    with open(INDEX, encoding="utf-8") as f:
        html = f.read()
    assert not re.search(r"https?://|//[a-z0-9.-]+\.[a-z]{2,}/|unpkg|cdn|exchange_sim|8001|api/klines|/api/", html, re.I)
    assert re.findall(r"<script[^>]* src=\"([^\"]+)\"", html) == ["/lightweight-charts.js"]    # único recurso externo: el propio servidor
    assert "innerHTML" not in html and "insertAdjacentHTML" not in html and "document.write" not in html


def test_velas_rechaza_host_externo(datos):
    assert pedir(datos[0], "GET", "/bot/velas", Host="evil.com")[0] == 403


_DRIVER_COLA = r"""
const V = (t, c = 100) => ({t, hora: "h" + t, open: c, high: c + 1, low: c - 1, close: c});
const R = (velas, extra = {}) => Object.assign({estado: "ok", intervalo: "15m", corrida: "A", reinicio: false, restantes: 0, hay_mas: false, velas}, extra);
const rango = (a, b) => Array.from({length: b - a}, (_, i) => V(a + i));
const ts = xs => xs.map(v => v.t);
const out = {};
let e = colaNueva();                                          // foto inicial: se dibuja entera y sin animar
out.foto = [fusionar(e, R(rango(0, 250))), e.velas.length, e.velas[0].t, e.cola.length, e.desde];
out.lote = [fusionar(e, R(rango(249, 254), {restantes: 3})), ts(e.cola), e.restantes, retraso(e), e.velas.length];   // 249 ya dibujada y igual: no es nueva
out.actualizada_dibujada = [fusionar(e, R([V(249, 150)])), e.velas[199].close, e.cola.length];                     // la última dibujada cambió
out.actualizada_en_cola = (fusionar(e, R([V(251, 777)])), [e.cola.length, e.cola.find(v => v.t === 251).close]);
out.vieja_ignorada = (fusionar(e, R([V(100, 5), V(10, 5)])), [e.cola.length, e.velas.some(v => v.close === 5)]);
let pasos = [];                                               // la cola se dibuja de a poco y en orden
for (let i = 0; i < 20; i++) pasos.push(avanzar(e, 16, "fluido"));
out.avanza = [pasos.reduce((a, b) => a + b, 0), e.cola.length, e.velas[e.velas.length - 1].t, e.velas.length];
e = colaNueva(); fusionar(e, R(rango(0, 10)));              // ritmos: 60 en cola, 100 ms
fusionar(e, R(rango(9, 70)));
const f = JSON.parse(JSON.stringify(e)), r = JSON.parse(JSON.stringify(e)), i = JSON.parse(JSON.stringify(e));
out.ritmos = [avanzar(f, 100, "fluido"), avanzar(r, 100, "rapido"), avanzar(i, 100, "instantaneo"), i.cola.length];
e = colaNueva(); fusionar(e, R(rango(0, 5)));
out.cola_grande = [fusionar(e, R(rango(4, 1500))), e.cola.length, e.velas.length, e.velas[199].t];             // atrasado: salta al final
e = colaNueva(); fusionar(e, R(rango(0, 5)));
out.corrida = [fusionar(e, R(rango(0, 3), {corrida: "B"})), e.desde, e.velas.length];                          // corrida nueva: pide la foto
out.intervalo = (e = colaNueva(), fusionar(e, R(rango(0, 5))), [fusionar(e, R(rango(0, 3), {intervalo: "1h"})), e.desde]);
e = colaNueva(); fusionar(e, R(rango(0, 5))); fusionar(e, R(rango(4, 8)));
out.reinicio = [fusionar(e, R(rango(500, 520), {reinicio: true})), e.cola.length, e.velas.length, e.desde];
out.sin_datos = (fusionar(e, {estado: "sin_datos"}), [e.velas.length, e.cola.length, e.desde]);
const W = (t, c = 100, vol = 1) => Object.assign(V(t, c), {volume: vol});
const lista = (a, b) => Array.from({length: b - a}, (_, k) => W(a + k));
let pl = planear(null, [], 0);                               // gráfico: qué hacerle (set o update)
out.plan_vacio = [pl.set, pl.upd.length, pl.prev];
pl = planear(null, lista(0, 5), 0);
out.plan_primera = [pl.set, pl.prev.n, pl.prev.ultT];
let prev = pl.prev;
pl = planear(prev, lista(0, 5), 0);
out.plan_igual = [pl.set, pl.upd.length, pl.prev.n];
pl = planear(prev, lista(0, 8), 0);                          // 3 nuevas entran con update, en orden
out.plan_nuevas = [pl.set, ts(pl.upd), pl.prev.n, pl.prev.ultT];
pl = planear(prev, lista(0, 4).concat([W(4, 999)]), 0);      // la última reescrita reemplaza (update de la misma t)
out.plan_reescrita = [pl.set, pl.upd.map(v => [v.t, v.close]), pl.prev.n];
pl = planear(prev, lista(0, 4).concat([W(4, 100, 55)]), 0);  // cambia solo el volumen
out.plan_volumen = [pl.set, pl.upd.map(v => [v.t, v.volume])];
pl = planear(prev, lista(0, 4).concat([W(4, 999), W(5), W(6)]), 0);
out.plan_reescrita_y_nuevas = [pl.set, pl.upd.map(v => v.t), pl.prev.n];
pl = planear(prev, lista(2, 7), 0);                          // la ventana se desliza: sigue siendo update, n cuenta lo añadido
out.plan_deslizada = [pl.set, ts(pl.upd), pl.prev.n];
out.plan_gen = (pl = planear(prev, lista(0, 5), 1), [pl.set, pl.prev.gen]);        // reinicio/foto/salto: recarga entera
out.plan_otra_serie = (pl = planear(prev, lista(100, 105), 0), [pl.set, pl.prev.n]);
out.plan_vaciar = (pl = planear(prev, [], 1), [pl.set, pl.prev]);
pl = planear({gen: 0, ultT: 0, ultima: W(0), n: 998}, lista(0, 4), 0);              // supera GRAF_MAX: recarga recortando
out.plan_recorte = [pl.set, pl.recorte === true, pl.prev.n];
e = colaNueva(); const g0 = e.gen;                            // gen sube con foto, vaciar y salto al final
fusionar(e, R(rango(0, 5))); const g1 = e.gen; fusionar(e, R(rango(4, 8))); const g2 = e.gen; colaVaciar(e);
out.gen = [g0, g1, g2, e.gen];
console.log(JSON.stringify(out));
"""


def test_logica_de_la_cola_de_velas_con_node(tmp_path):
    """Ejecuta con node las funciones puras de la cola (entre <logica-cola> y </logica-cola>) sobre casos fijos."""
    import re
    import shutil
    import subprocess
    from bot.panel_server import INDEX

    node = shutil.which("node")
    if node is None:
        pytest.skip("node no está instalado: no se puede ejecutar la lógica de la cola")
    with open(INDEX, encoding="utf-8") as f:
        m = re.search(r"// <logica-cola>.*?// </logica-cola>", f.read(), re.S)
    assert m, "no se encontró el bloque <logica-cola> en index.html"
    ruta = tmp_path / "cola.js"
    ruta.write_text(m.group(0) + "\n" + _DRIVER_COLA, encoding="utf-8")
    r = subprocess.run([node, str(ruta)], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout)
    assert o["foto"] == [False, 200, 50, 0, 249]                       # solo las últimas 200
    assert o["lote"] == [False, [250, 251, 252, 253], 3, 7, 200]       # 249 repetida no cuenta; retraso = cola + restantes
    assert o["actualizada_dibujada"] == [False, 150, 4]
    assert o["actualizada_en_cola"] == [4, 777]                        # reemplaza, no suma
    assert o["vieja_ignorada"] == [4, False]
    assert o["avanza"] == [4, 0, 253, 200]                             # la cola se vació en orden y la ventana sigue en 200
    assert o["ritmos"] == [4, 12, 60, 0]                               # cola 60, 100 ms: fluido max(30, 60/1,5)=40/s; rápido 120/s; instantáneo todo
    assert o["cola_grande"] == [False, 0, 200, 1499]
    assert o["corrida"] == [True, None, 0]
    assert o["intervalo"] == [True, None]
    assert o["reinicio"] == [False, 0, 20, 519]
    assert o["sin_datos"] == [0, 0, None]
    assert o["plan_vacio"] == [False, 0, None]
    assert o["plan_primera"] == [True, 5, 4]
    assert o["plan_igual"] == [False, 0, 5]
    assert o["plan_nuevas"] == [False, [5, 6, 7], 8, 7]
    assert o["plan_reescrita"] == [False, [[4, 999]], 5]               # reemplaza a la última, no suma
    assert o["plan_volumen"] == [False, [[4, 55]]]
    assert o["plan_reescrita_y_nuevas"] == [False, [4, 5, 6], 7]
    assert o["plan_deslizada"] == [False, [5, 6], 7]
    assert o["plan_gen"] == [True, 1]
    assert o["plan_otra_serie"] == [True, 5]
    assert o["plan_vaciar"] == [True, None]
    assert o["plan_recorte"] == [True, True, 4]
    assert o["gen"] == [0, 1, 1, 2]


def test_javascript_de_la_pagina_sin_errores_de_sintaxis(tmp_path):
    """Extrae cada <script> de index.html y lo revisa con `node --check` (si node no está, se omite)."""
    import re
    import shutil
    import subprocess
    from bot.panel_server import INDEX

    node = shutil.which("node")
    if node is None:
        pytest.skip("node no está instalado: no se puede comprobar la sintaxis del JavaScript")
    with open(INDEX, encoding="utf-8") as f:
        scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", f.read(), re.S | re.I)
    assert scripts, "no se encontró ningún <script> en index.html"
    for i, js in enumerate(scripts):
        ruta = tmp_path / f"script{i}.js"
        ruta.write_text(js, encoding="utf-8")
        r = subprocess.run([node, "--check", str(ruta)], capture_output=True, text=True, encoding="utf-8")
        assert r.returncode == 0, f"error de sintaxis en <script> nº {i + 1}:\n{r.stderr}"


def test_host_y_puerto_del_panel_por_entorno(monkeypatch):
    from bot import panel_server as P
    monkeypatch.delenv("PANEL_HOST", raising=False)
    monkeypatch.delenv("PANEL_PORT", raising=False)
    assert P.host_configurado() == "127.0.0.1" and P.puerto_configurado() == 8002
    monkeypatch.setenv("PANEL_HOST", "0.0.0.0")
    monkeypatch.setenv("PANEL_PORT", "9002")
    assert P.host_configurado() == "0.0.0.0" and P.puerto_configurado() == 9002
    monkeypatch.setenv("PANEL_PORT", "99999")
    with pytest.raises(SystemExit):
        P.puerto_configurado()
