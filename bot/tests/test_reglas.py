"""Validador de reglas.yaml / limites.yaml."""
import copy

import pytest
import yaml

from bot import reglas as R

LIM = {"riesgo_max_pct": 2, "stop_max_pct": 10, "parcial_min_pct": 10, "parcial_max_pct": 90,
       "max_zonas": 3, "max_reglas_activas": 2}
OK = {"activo": True,
      "zonas": [{"nombre": "S1", "tipo": "soporte", "desde": 100, "hasta": 105, "activa": True}],
      "reglas": [{"zona": "S1", "accion": "abrir_long", "stop_pct": 3, "riesgo_pct": 1,
                  "confirmaciones": {"cierre_fuera_zona": "arriba", "rsi": {"op": "menor", "valor": 40},
                                     "ema": {"op": "sobre", "n": 50}},
                  "parcial": {"porcentaje": 50, "condicion": {"tipo": "pct", "valor": 5}}}]}


def campos(d):
    return {e["campo"] for e in R.validar(d, LIM)}


def mod(f):
    d = copy.deepcopy(OK)
    f(d)
    return d


def test_valido():
    assert R.validar(OK, LIM) == []


def test_rechaza_stop_sobre_el_limite():
    assert "reglas.0.stop_pct" in campos(mod(lambda d: d["reglas"][0].update(stop_pct=10.5)))


def test_rechaza_sin_stop_al_abrir():
    assert "reglas.0.stop_pct" in campos(mod(lambda d: d["reglas"][0].pop("stop_pct")))
    assert "reglas.0.stop_pct" in campos(mod(lambda d: d["reglas"][0].update(accion="abrir_short", stop_pct=None)))


def test_stop_no_obligatorio_al_cerrar_o_avisar():
    assert R.validar(mod(lambda d: (d["reglas"][0].pop("stop_pct"), d["reglas"][0].update(accion="solo_avisar"))), LIM) == []


def test_rechaza_desde_mayor_o_igual_a_hasta():
    assert "zonas.0.desde" in campos(mod(lambda d: d["zonas"][0].update(desde=105)))
    assert "zonas.0.desde" in campos(mod(lambda d: d["zonas"][0].update(desde=110)))


def test_rechaza_riesgo_sobre_el_maximo():
    assert "reglas.0.riesgo_pct" in campos(mod(lambda d: d["reglas"][0].update(riesgo_pct=2.5)))


def test_rechaza_zona_inexistente():
    assert "reglas.0.zona" in campos(mod(lambda d: d["reglas"][0].update(zona="X")))


def test_rechaza_nombres_repetidos():
    d = mod(lambda d: d["zonas"].append({"nombre": "s1", "tipo": "resistencia", "desde": 1, "hasta": 2}))
    assert "zonas.1.nombre" in campos(d)


def test_parcial_fuera_de_rango():
    for v in (5, 95):
        d = mod(lambda d: d["reglas"][0]["parcial"].update(porcentaje=v))
        assert "reglas.0.parcial.porcentaje" in campos(d)


def test_ema_y_rsi_fuera_de_rango():
    d = mod(lambda d: d["reglas"][0]["confirmaciones"]["ema"].update(n=1))
    assert "reglas.0.confirmaciones.ema.n" in campos(d)
    d = mod(lambda d: d["reglas"][0]["confirmaciones"]["rsi"].update(valor=100))
    assert "reglas.0.confirmaciones.rsi.valor" in campos(d)


@pytest.mark.parametrize("malo", ["3", True, float("nan"), float("inf"), None])
def test_numeros_invalidos(malo):
    assert "zonas.0.hasta" in campos(mod(lambda d: d["zonas"][0].update(hasta=malo)))


def test_maximos_de_zonas_y_reglas_activas():
    z = lambda i: {"nombre": f"Z{i}", "tipo": "soporte", "desde": 1, "hasta": 2}
    assert "zonas" in campos(mod(lambda d: d["zonas"].extend(z(i) for i in range(3))))
    d = mod(lambda d: d["reglas"].extend(copy.deepcopy(d["reglas"][0]) for _ in range(2)))
    assert "reglas" in campos(d)
    d["reglas"][1]["activa"] = False   # las inactivas no cuentan
    assert "reglas" not in campos(d)


def test_campos_desconocidos():
    assert "reglas.0.apalancamiento" in campos(mod(lambda d: d["reglas"][0].update(apalancamiento=10)))


def test_pendientes_usan_valores_de_config():
    cfg = {"stop_pct": 0.05, "target_pct": 0.1, "position_fraction": 1.0}
    txt = "\n".join(R.pendientes(OK, cfg))
    assert "stop_pct=3 % IGNORADO" in txt and "5 %" in txt
    assert "riesgo_pct=1 % IGNORADO" in txt and "100 %" in txt
    assert "parcial=50 % IGNORADO" in txt


def test_cargar_reglas_invalidas_dice_por_que(tmp_path):
    f = tmp_path / "r.yaml"
    f.write_text(yaml.safe_dump(mod(lambda d: d["reglas"][0].update(stop_pct=50))), encoding="utf-8")
    with pytest.raises(R.ReglasInvalidas, match="stop_pct"):
        R.cargar_reglas(str(f), LIM)


def test_archivos_reales_son_validos():
    lim = R.cargar_limites()
    assert R.cargar_reglas(limites=lim)["activo"] in (True, False)
    assert lim["riesgo_max_pct"] <= 2 and lim["stop_max_pct"] <= 10


def test_sin_reglas_yaml_usa_el_ejemplo(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "REGLAS", str(tmp_path / "reglas.yaml"))      # no existe
    d = R.cargar_reglas(limites=R.cargar_limites())
    assert d["activo"] is False and d["reglas"] == []                      # el ejemplo es inofensivo


def test_path_explicito_inexistente_sigue_siendo_error(tmp_path):
    with pytest.raises(R.ReglasInvalidas):
        R.cargar_reglas(str(tmp_path / "no_existe.yaml"), LIM)
