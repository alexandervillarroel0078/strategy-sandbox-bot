"""Registro del bot (--log): lista blanca de campos, sin claves en ningún archivo, y tolerancia a fallos de escritura."""
import json
import os

import pytest

from bot import bot_client
from bot.bot_client import ExchangeClient, ExchangeError
from bot.registro import CAMPOS_ORDEN, Registro, conservar_logs, host_puerto, limpiar_logs, permitir
from bot.utils import load_config

CLAVE = "SECRETO-FALSO-9f3a1c"


class RespuestaFalsa:
    def __init__(self, status, cuerpo):
        self.status, self._raw = status, json.dumps(cuerpo).encode()
        self.headers = {"Authorization": CLAVE, "X-MBX-APIKEY": CLAVE}

    def read(self):
        return self._raw


def conexion_falsa(rutas: dict):
    """HTTPConnection falsa: rutas = {ruta: (status, cuerpo)}."""
    class Conn:
        def __init__(self, *a, **k): self.ruta = None
        def request(self, method, url, headers=None, body=None): self.ruta = url.split("?")[0]
        def getresponse(self): return RespuestaFalsa(*rutas[self.ruta])
        def close(self): pass
    return Conn


def contenido(carpeta) -> str:
    return "".join(open(os.path.join(carpeta, n), encoding="utf-8").read() for n in sorted(os.listdir(carpeta)))


def test_permitir_solo_deja_los_campos_acordados_y_descarta_el_resto():
    sucio = {"lado": "buy", "tipo": "market", "fraccion": 1.0, "api_key": CLAVE, "signature": CLAVE,
             "headers": {"Authorization": CLAVE}, "X-MBX-APIKEY": CLAVE, "precio": 10.0, "id": 3,
             "motivo": "m", "estado": "NEW", "ahora": 5, "comision": 0.1, "stop_fraccion": 0.05,
             "take_profit_fraccion": 0.1, "liquidar": True, "extra": [1, 2]}
    limpio = permitir(sucio)
    assert set(limpio) <= set(CAMPOS_ORDEN) and "liquidar" not in limpio
    assert CLAVE not in json.dumps(limpio)
    assert permitir("no es un dict") == {}


def test_la_url_se_reduce_a_host_y_puerto():
    assert host_puerto(f"http://usuario:{CLAVE}@127.0.0.1:8001/api?k={CLAVE}") == "127.0.0.1:8001"


def test_la_clave_falsa_no_aparece_en_cabeceras_cuerpos_errores_ni_feed(tmp_path, monkeypatch):
    rutas = {
        "/api/session/start": (200, {"now": 3_600_000, "api_key": CLAVE}),
        # fila con un 8.º elemento que lleva la clave: el feed solo escribe las 7 columnas de la vela
        "/api/step": (200, {"now": 7_200_000, "end_of_data": False, "firma": CLAVE,
                            "candles": [[3_600_000, "1", "2", "0.5", "1.5", "10", 7_199_999, CLAVE]]}),
        "/api/klines": (200, [[0, "1", "2", "0.5", "1.5", "10", 3_599_999, CLAVE]]),
        "/api/order": (200, {"id": 1, "estado": "NEW", "ahora": 3_600_000, "api_key": CLAVE,
                             "signature": CLAVE, "headers": {"Authorization": CLAVE}}),
    }
    monkeypatch.setattr(bot_client.http.client, "HTTPConnection", conexion_falsa(rutas))
    reg = Registro(nivel="completo", carpeta=str(tmp_path))
    c = ExchangeClient(f"http://usuario:{CLAVE}@127.0.0.1:9", registro=reg)
    c.start_session(3_600_000)
    c.get_klines("1h", 5)
    c.step("1h", 1)
    c.order(lado="buy", tipo="market", fraccion=1.0, api_key=CLAVE, signature=CLAVE, headers={"Authorization": CLAVE})
    # error del servidor con la clave dentro del mensaje: se propaga al bot pero no llega al registro
    rutas["/api/order"] = (400, {"error": f"clave inválida {CLAVE}"})
    with pytest.raises(ExchangeError, match="clave inválida"):
        c.order(lado="sell", tipo="market")
    reg.evento("inicio", api=host_puerto(c.base_url))
    reg.cerrar()
    texto = contenido(tmp_path)
    assert CLAVE not in texto and "Authorization" not in texto and "usuario" not in texto
    assert '"evento": "orden_enviada"' in texto and '"evento": "orden_respuesta"' in texto and "feed_" in "".join(os.listdir(tmp_path))
    assert "api_key" not in texto and "signature" not in texto


def test_si_no_se_puede_escribir_el_bot_continua_sin_registro(tmp_path, capsys):
    ocupado = tmp_path / "archivo"
    ocupado.write_text("x")
    reg = Registro(carpeta=str(ocupado / "logs"))      # una carpeta bajo un archivo: no se puede crear
    reg.evento("inicio", a=1)                           # no lanza
    reg.feed(0, "step", "15m", [[0, "1", "1", "1", "1", "1", 1]])
    reg.cerrar()
    assert "no puedo escribir el registro" in capsys.readouterr().err


def test_un_fallo_de_escritura_a_mitad_se_avisa_una_vez_y_se_sigue(tmp_path, capsys):
    reg = Registro(carpeta=str(tmp_path))
    reg._f.close()                                      # simula disco lleno / archivo cerrado
    reg.evento("step", ahora=1)
    reg.evento("step", ahora=2)
    assert capsys.readouterr().err.count("no puedo escribir el registro") == 1


def test_ultimo_jsonl_sigue_al_log_en_curso(tmp_path):
    reg = Registro(carpeta=str(tmp_path))
    reg.evento("inicio", a=1)
    reg.cerrar()
    assert open(tmp_path / "ultimo.jsonl", encoding="utf-8").read() == open(reg.ruta, encoding="utf-8").read()


# ---- límite de logs: se conservan las últimas N corridas ----
def _corridas(carpeta, n, con_feed=True, desde=1):
    """n corridas con sello creciente (20250101-0000<desde>...); devuelve los sellos."""
    sellos = [f"20250101-{(desde + i) // 60:02d}{(desde + i) % 60:02d}00" for i in range(n)]
    for s in sellos:
        (carpeta / f"bot_{s}.jsonl").write_text("{}\n", encoding="utf-8")
        if con_feed:
            (carpeta / f"feed_{s}.csv").write_text("x\n", encoding="utf-8")
    return sellos


def _nombres(carpeta):
    return sorted(p.name for p in carpeta.iterdir())


def test_limpieza_respeta_el_tope_y_borra_bot_y_feed_juntos(tmp_path):
    sellos = _corridas(tmp_path, 25)
    assert limpiar_logs(str(tmp_path), 20) == 10   # 5 corridas × (jsonl + csv)
    quedan = _nombres(tmp_path)
    assert len(quedan) == 40
    assert all(f"bot_{s}.jsonl" not in quedan and f"feed_{s}.csv" not in quedan for s in sellos[:5])
    assert all(f"bot_{s}.jsonl" in quedan and f"feed_{s}.csv" in quedan for s in sellos[5:])


def test_limpieza_sin_exceso_no_borra_nada(tmp_path):
    _corridas(tmp_path, 20)
    assert limpiar_logs(str(tmp_path), 20) == 0 and len(_nombres(tmp_path)) == 40


def test_limpieza_cuenta_corridas_sin_feed_y_feeds_huerfanos(tmp_path):
    _corridas(tmp_path, 3, con_feed=False)
    (tmp_path / "feed_20240101-000000.csv").write_text("x\n", encoding="utf-8")   # feed huérfano, el más antiguo
    assert limpiar_logs(str(tmp_path), 3) == 1
    assert len(_nombres(tmp_path)) == 3


def test_limpieza_no_toca_archivos_de_otro_patron(tmp_path):
    ajenos = ["ultimo.jsonl", "notas.txt", "bot_x.jsonl", "bot_20200101-000000.jsonl.bak", "feed_20200101-000000.csv.old",
              "bot_20200101-000000.jsonl.txt", "mibot_20200101-000000.jsonl", "bot_2020010-000000.jsonl", "otro.csv"]
    for n in ajenos:
        (tmp_path / n).write_text("x", encoding="utf-8")
    (tmp_path / "bot_20200101-000000.jsonl").mkdir()                   # carpeta con nombre de log
    _corridas(tmp_path, 5)
    assert limpiar_logs(str(tmp_path), 2) == 6
    quedan = _nombres(tmp_path)
    assert all(n in quedan for n in ajenos) and "bot_20200101-000000.jsonl" in quedan


def test_limpieza_nunca_borra_la_corrida_actual(tmp_path):
    sellos = _corridas(tmp_path, 5)
    actual = str(tmp_path / f"bot_{sellos[0]}.jsonl")                  # la más antigua, pero es la actual
    assert limpiar_logs(str(tmp_path), 1, actual) == 6                 # caen 3 de las 4 viejas; quedan actual y la más nueva
    quedan = _nombres(tmp_path)
    assert f"bot_{sellos[0]}.jsonl" in quedan and f"feed_{sellos[0]}.csv" in quedan
    assert f"bot_{sellos[-1]}.jsonl" in quedan and len(quedan) == 4


def test_limpieza_protege_lo_que_apunta_ultimo(tmp_path):
    sellos = _corridas(tmp_path, 4)
    try:
        os.link(tmp_path / f"bot_{sellos[1]}.jsonl", tmp_path / "ultimo.jsonl")
    except OSError:
        pytest.skip("el sistema de archivos no admite enlaces duros")
    limpiar_logs(str(tmp_path), 1)
    quedan = _nombres(tmp_path)
    assert f"bot_{sellos[1]}.jsonl" in quedan and f"feed_{sellos[1]}.csv" in quedan and "ultimo.jsonl" in quedan
    assert f"bot_{sellos[0]}.jsonl" not in quedan and f"bot_{sellos[2]}.jsonl" not in quedan


def test_limpieza_con_cero_no_borra_nada(tmp_path):
    _corridas(tmp_path, 30)
    assert limpiar_logs(str(tmp_path), 0) == 0 and len(_nombres(tmp_path)) == 60


def test_limpieza_carpeta_inexistente_no_falla(tmp_path):
    assert limpiar_logs(str(tmp_path / "no_existe"), 5) == 0


def test_limpieza_un_borrado_que_falla_se_omite(tmp_path, monkeypatch):
    sellos = _corridas(tmp_path, 3, con_feed=False)
    real = os.remove

    def remove(ruta):
        if sellos[0] in str(ruta):
            raise PermissionError("abierto por otro proceso")
        real(ruta)
    monkeypatch.setattr(os, "remove", remove)
    assert limpiar_logs(str(tmp_path), 1) == 1                         # la 0 falla (se omite); la 1 se borra
    assert f"bot_{sellos[0]}.jsonl" in _nombres(tmp_path)


@pytest.mark.parametrize("valor, esperado", [(20, 20), (0, 0), (5, 5), (-1, None), ("20", None), (2.5, None), (True, None), (None, None)])
def test_conservar_logs_valida_el_valor(valor, esperado):
    assert conservar_logs({"logs_conservar": valor}) == esperado


def test_conservar_logs_por_defecto_es_20():
    assert conservar_logs({}) == 20


def test_config_yaml_trae_logs_conservar():
    from bot.utils import load_config
    assert conservar_logs(load_config()) == 20


def test_run_bot_api_limpia_e_imprime_solo_si_borra(tmp_path, monkeypatch, capsys):
    """Con --log: llama a la limpieza sobre la carpeta del registro y avisa solo si borró algo."""
    from bot import run_bot_api

    def correr(cfg_extra):
        _corridas(tmp_path, 4, desde=1)
        cfg = {**load_config(), **cfg_extra}
        monkeypatch.setattr(run_bot_api, "load_config", lambda _=None: cfg)
        monkeypatch.setattr(run_bot_api, "Registro", lambda **kw: Registro(carpeta=str(tmp_path), **kw))
        monkeypatch.setattr(run_bot_api, "run_bot_exchange", lambda *a, **k: (_ for _ in ()).throw(ExchangeError("corte")))
        monkeypatch.setattr("sys.argv", ["run_bot_api", "--ejecucion", "exchange", "--log", "--start", "2025-09-01"])
        with pytest.raises(SystemExit):
            run_bot_api.main()
        return capsys.readouterr().out

    out = correr({"logs_conservar": 2})
    assert "limpieza de logs" in out and "se conservan las últimas 2" in out
    out = correr({"logs_conservar": 0})
    assert "limpieza" not in out and "AVISO" not in out
    out = correr({"logs_conservar": "mucho"})
    assert "AVISO" in out and "no se borra" in out
    out = correr({"logs_conservar": 1000})
    assert "limpieza" not in out and "AVISO" not in out


def test_host_puerto_acepta_url_de_lan():
    assert host_puerto("http://192.168.1.5:8001") == "192.168.1.5:8001"
    assert host_puerto("http://usuario:clave@192.168.1.5:8001/ruta") == "192.168.1.5:8001"
    assert host_puerto("http://exchange.local") == "exchange.local:80"
    assert host_puerto("https://exchange.local") == "exchange.local:443"


def test_exchange_url_manda_sobre_api_url_y_sobre_el_cliente(monkeypatch):
    monkeypatch.delenv("EXCHANGE_URL", raising=False)
    assert load_config()["api_url"] == "http://127.0.0.1:8001"
    assert ExchangeClient().base_url == "http://127.0.0.1:8001"
    monkeypatch.setenv("EXCHANGE_URL", "http://192.168.1.5:9001")
    assert load_config()["api_url"] == "http://192.168.1.5:9001"
    c = ExchangeClient()
    assert (c.host, c.port) == ("192.168.1.5", 9001)
    assert ExchangeClient("http://127.0.0.1:1").port == 1       # un argumento explícito sigue mandando
