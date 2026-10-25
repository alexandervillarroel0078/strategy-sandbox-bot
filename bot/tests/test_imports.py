"""Aislamiento: bot no puede importar de exchange_sim ni de tools (revisa el AST de todos sus .py, tests incluidos)."""
import ast
import os

PKG = "bot"
PROHIBIDOS = ("exchange_sim", "tools")
RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _importaciones(ruta: str):
    """(línea, módulo absoluto o '.' * nivel) de cada import del archivo."""
    with open(ruta, encoding="utf-8") as f:
        arbol = ast.parse(f.read(), filename=ruta)
    for n in ast.walk(arbol):
        if isinstance(n, ast.Import):
            for a in n.names:
                yield n.lineno, a.name, 0
        elif isinstance(n, ast.ImportFrom):
            yield n.lineno, n.module or "", n.level


def _archivos(paquete: str):
    base = os.path.join(RAIZ, paquete)
    for d, carpetas, nombres in os.walk(base):
        carpetas[:] = [c for c in carpetas if c not in ("__pycache__", "results", "static")]
        for nom in nombres:
            if nom.endswith(".py"):
                yield os.path.join(d, nom)


def test_bot_no_importa_de_otros_servicios():
    malos = []
    for ruta in _archivos(PKG):
        prof = len(os.path.relpath(os.path.dirname(ruta), RAIZ).split(os.sep))   # carpetas desde btc/
        for linea, modulo, nivel in _importaciones(ruta):
            if nivel > prof:                         # import relativo que sale del paquete
                malos.append(f"{os.path.relpath(ruta, RAIZ)}:{linea} import relativo fuera de {PKG}")
            elif nivel == 0 and modulo.split(".")[0] in PROHIBIDOS:
                malos.append(f"{os.path.relpath(ruta, RAIZ)}:{linea} importa {modulo}")
    assert not malos, "\n".join(malos)
