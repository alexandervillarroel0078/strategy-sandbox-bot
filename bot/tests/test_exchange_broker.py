"""ExchangeBroker (sin servidor, con un cliente falso), rutas permitidas y conversión de unidades."""
import pytest

from bot.bot_client import ALLOWED, ExchangeClient, ExchangeError
from bot.broker import Params
from bot.exchange_broker import ExchangeBroker
from bot.reglas import fraccion_a_pct, pct_a_fraccion


class ClienteFalso:
    def __init__(self, respuesta=None):
        self.enviadas, self.respuesta = [], respuesta or {"id": 1, "estado": "NEW", "ahora": 0}

    def order(self, **o):
        self.enviadas.append(o)
        return self.respuesta

    def account(self):
        return {"saldo": 123.0, "posicion": None, "trades": []}


def test_rutas_permitidas_son_exactamente_las_acordadas():
    assert ALLOWED == {("GET", "/api/klines"), ("POST", "/api/step"), ("POST", "/api/session/start"),
                       ("POST", "/api/session/reset"), ("POST", "/api/order"), ("GET", "/api/orders"),
                       ("GET", "/api/account")}


@pytest.mark.parametrize("metodo, ruta", [("GET", "/api/info"), ("GET", "/api/session"), ("DELETE", "/api/order"),
                                          ("POST", "/api/otra")])
def test_cualquier_otra_ruta_se_rechaza_en_el_cliente(metodo, ruta):
    with pytest.raises(ExchangeError, match="no permitida"):
        ExchangeClient("http://127.0.0.1:9")._request(metodo, ruta)


def test_la_compra_envia_las_fracciones_de_params_sin_convertir():
    c = ClienteFalso()
    b = ExchangeBroker(c, Params(stop_pct=0.05, target_pct=0.10, position_fraction=0.5))
    assert b.buy(100.0, 0) is True
    assert c.enviadas == [{"lado": "buy", "tipo": "market", "fraccion": 0.5,
                           "stop_fraccion": 0.05, "take_profit_fraccion": 0.1}]
    assert ExchangeBroker(ClienteFalso({"id": 1, "estado": "REJECTED", "ahora": 0, "motivo": "x"}), Params()).buy(1, 0) is False


def test_la_liquidacion_final_envia_liquidar_y_refresca_el_estado():
    c = ClienteFalso({"id": 2, "estado": "FILLED", "ahora": 0})
    b = ExchangeBroker(c, Params())
    b.sell_market(100.0, 0, "fin_simulacion")
    assert c.enviadas == [{"lado": "sell", "tipo": "market", "liquidar": True}] and b.cash == 123.0 and b.pos is None
    c2 = ClienteFalso()
    ExchangeBroker(c2, Params()).sell_market(100.0, 0, "señal")
    assert c2.enviadas == [{"lado": "sell", "tipo": "market", "liquidar": False}]
    with pytest.raises(ExchangeError, match="rechaz"):
        ExchangeBroker(ClienteFalso({"id": 3, "estado": "REJECTED", "ahora": 0, "motivo": "m"}), Params()).sell_market(1, 0, "señal")


def test_sync_construye_la_posicion_con_tiempos_en_segundos():
    b = ExchangeBroker(ClienteFalso(), Params())
    b.sync({"saldo": 5.0, "posicion": {"entrada": 1_700_000_000_000, "precio_entrada": 100.0, "cantidad": 2.0,
                                       "stop": 95.0, "objetivo": 110.0}})
    assert b.in_position and b.pos.entry_time == 1_700_000_000 and b.equity(120.0) == 5.0 + 240.0
    b.sync({"saldo": 9.0, "posicion": None})
    assert not b.in_position and b.equity(120.0) == 9.0


# ---- unidades: el panel usa % (5 = 5 %); la API de órdenes y config.yaml usan fracciones (0.05) ----
def test_conversion_de_unidades_en_un_solo_lugar():
    assert pct_a_fraccion(5) == 0.05 and fraccion_a_pct(0.05) == 5
    assert pct_a_fraccion(0.5) == 0.005 and fraccion_a_pct(1.0) == 100
    for x in (0.01, 0.5, 2, 10, 37.5):
        assert fraccion_a_pct(pct_a_fraccion(x)) == pytest.approx(x)


def test_una_regla_en_pct_viaja_a_la_api_como_fraccion():
    """Ejemplo del uso previsto: stop_pct=3 del panel -> stop_fraccion=0.03 en la orden."""
    c = ClienteFalso()
    ExchangeBroker(c, Params(stop_pct=pct_a_fraccion(3), target_pct=pct_a_fraccion(8))).buy(100.0, 0)
    assert c.enviadas[0]["stop_fraccion"] == 0.03 and c.enviadas[0]["take_profit_fraccion"] == 0.08
