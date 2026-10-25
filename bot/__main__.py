"""Puerta de entrada al bot:  python -m bot  [--port 8002]

Abre el panel de control (http://127.0.0.1:8002/): editar reglas e iniciar/detener el bot.
Es lo mismo que `python -m bot.panel_server`; `python -m bot.run_bot_api` sigue funcionando por consola.
"""
from .panel_server import main

if __name__ == "__main__":
    main()
