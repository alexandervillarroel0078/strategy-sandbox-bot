# strategy-sandbox-bot

Bot de prueba y su panel de control. Opera contra un **exchange simulado** por HTTP (repo hermano `strategy-sandbox-exchange`).
Este repo **no contiene datos de mercado** ni código del exchange: solo simulación, sin dinero real.

## Uso rápido

```
# 1) arrancar el exchange (otro repo; ver su README):  http://127.0.0.1:8001
# 2) en este repo:
pip install -r requirements.txt
python -m bot                      # panel -> http://127.0.0.1:8002/
python -m bot.run_bot_api --start 2025-09-01 --end 2025-09-07      # o el bot por línea de comandos
run_tests.bat                      # suite del bot (no necesita el exchange)
```

Variables de entorno: `EXCHANGE_URL` (dónde está el exchange), `PANEL_HOST`, `PANEL_PORT`, `PANEL_ALLOWED_HOSTS`.

- Documentación del bot: [bot/README.md](bot/README.md)
- Contrato del exchange (copia): [docs/API_EXCHANGE.md](docs/API_EXCHANGE.md)
- Diagramas: [docs/diagramas.md](docs/diagramas.md)

> Los niveles dibujados hoy y probados sobre el pasado no son una prueba válida (sesgo de retrospectiva): `por_niveles` sirve para comprobar el código y para pruebas hacia adelante.


echo "# strategy-sandbox-bot" >> README.md
git init
git add README.md
git commit -m "first commit"
git branch -M main
git remote add origin https://github.com/alexandervillarroel0078/strategy-sandbox-bot.git
git push -u origin main