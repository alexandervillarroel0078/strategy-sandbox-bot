# bot (servicio 2)

Bot de prueba que opera contra la API del mercado simulado (el exchange, repo `strategy-sandbox-exchange`). Solo simulación, sin dinero real.

> **Aviso:** Los niveles dibujados hoy y probados sobre el pasado no son una prueba válida (sesgo de retrospectiva). Úsalo solo para comprobar que el código funciona y para pruebas hacia adelante.
> `por_niveles` no se evalúa por rentabilidad ni cuenta como idea de estrategia.

## Puerta de entrada: el panel (`python -m bot`)

```
# terminal 1: el exchange (repo strategy-sandbox-exchange, ver su README) -> http://127.0.0.1:8001
python -m bot                          # terminal 2: panel de control -> http://127.0.0.1:8002/  (--port para cambiarlo)
```

Desde la página: elegir fecha de inicio y fin (por defecto 2025-09-01 a 2025-09-07) y estrategia (`ejemplo`, `donchian`,
`por_niveles`), y pulsar **Iniciar bot**. Siempre corre en modo exchange, con `--log --log-nivel completo`.
La página muestra el estado (sin iniciar / corriendo / terminado / con error / detenido), las últimas líneas de salida y,
al terminar, operaciones y retorno.

- Antes de lanzar, el panel comprueba que el exchange responde (`/api/info`). Si no, avisa
  `arranca primero el exchange (repo strategy-sandbox-exchange, ver su README)` y no lanza el bot. No permite dos bots a la vez.
- El bot corre como **proceso aparte** (`python -m bot.run_bot_api ...` con `subprocess`, ver `bot/gestor.py`); el panel no lo importa.
- **Detener** cierra solo el proceso que lanzó el panel. La corrida queda marcada como *detenida*: la posición puede haber
  quedado abierta en el exchange y ese resultado no sirve para evaluar una idea. Al cerrar el panel (Ctrl+C) el bot también se cierra;
  en Windows, además, el bot va en un Job Object, así que muere aunque el panel se mate de golpe.
- Las reglas del panel solo afectan con la estrategia `por_niveles` y solo al arrancar el bot.
- **Datos recibidos** (sección de la página, `GET /bot/datos`): en vivo, cada segundo, el "ahora" simulado, las últimas 10 velas
  que recibió el bot (hora de apertura, fuente `step`/`klines`, apertura, máximo, mínimo, cierre) y las últimas 10 decisiones
  (hora simulada, acción, motivo, precio y RSI/EMA50/EMA200 si la estrategia los informa). Es solo lectura: el endpoint lee
  únicamente el **final** de `bot/logs/ultimo.jsonl` y del `feed_*.csv` que nombra su evento `inicio` (nunca los archivos
  enteros), valida el nombre del feed, devuelve campos fijos y tiene la misma guardia de Host que los demás (`bot/datos_vivo.py`).
- **Velas recibidas** (`GET /bot/velas[?intervalo=]`): gráfico de velas y volumen (lightweight-charts, ver «Gráfico» abajo) con las últimas 200 velas distintas que el bot recibe en la corrida actual. Intervalo por defecto el de las klines del `inicio`; el selector ofrece también el paso (15m). Una vela reescrita se queda con su última versión. Solo corridas lanzadas desde el panel; misma lectura acotada del final del feed (tope 1 MB) y misma guardia de Host. Se dibuja vela por vela: la página pide `?desde=<open_time de la última recibida>` (hasta 500 por respuesta, `hay_mas` si faltan), mete las nuevas en una cola y las dibuja a 30/s (Fluido; Rápido ×4; Instantáneo), acelerando si la cola crece (retraso ≲ 1,5 s). Una vela reescrita reemplaza a la anterior (en el gráfico, `update` de la misma vela); cada vela trae `volume` (se ignoran las filas con volumen negativo, no finito o ausente); si la marca queda fuera de lo leído (`reinicio`) o cambia la corrida o el intervalo, se reinicia con la foto de las últimas 200.
  Solo muestra datos de un bot lanzado desde este panel: un log anterior a la hora de arranque se oculta ("Sin datos todavía"),
  así que al reabrir el panel no se ven las tablas de una corrida vieja (siguen en `bot/logs/`). Necesita que `ultimo.jsonl`
  sea un enlace duro al log en curso (NTFS lo permite); si el sistema no admite enlaces, no hay datos en vivo hasta terminar.
- El modo `interna` ya no se ofrece ni en el panel ni en la línea de comandos (`--ejecucion` solo acepta `exchange`).

### Gráfico (librería incluida, sin internet)

El gráfico «Velas recibidas» usa **TradingView Lightweight Charts v4.1.3** (la misma versión y estilo que el visor del exchange),
copiada en `bot/panel/` para que la página no dependa de ninguna URL externa:

- Archivo: `bot/panel/lightweight-charts.standalone.production.js` (160 943 bytes)
- sha256: `0c146b9bfbc6e5f19da1a0de876dae43023c332d5157d82f7575f9b7653ee519`
- Licencia: Apache License 2.0 (© TradingView, Inc.); texto en `bot/panel/LICENSE-lightweight-charts.txt`. El paquete no trae `NOTICE`.
- Origen: `https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js`.
- `panel_server.py` la sirve en `GET /lightweight-charts.js` (lista blanca de ese único archivo, misma guardia de Host); ninguna otra ruta estática existe.
- Un test comprueba el sha256. Para actualizar la versión hay que cambiar el archivo, la licencia, este texto y ese test.

## Correr el bot por línea de comandos

(sigue funcionando igual; el panel usa estos mismos comandos)

```
# terminal 1: el exchange (repo strategy-sandbox-exchange) -> http://127.0.0.1:8001
python -m bot.run_bot_api --start 2025-09-01 --end 2025-09-07       # terminal 2 (estrategia: ejemplo)
python -m bot.run_bot_api --strategy por_niveles --start <fecha>    # con las reglas de bot/reglas.yaml
```

## Ejecución en el exchange (`--ejecucion exchange`)

Con `--ejecucion exchange` (la única vía; el Broker interno de `bot_client.run_bot` queda solo como referencia de los tests de equivalencia) el bot
**envía las órdenes** al exchange simulado (`POST /api/order`), que las ejecuta y vigila stop/objetivo; las métricas
se arman con lo que devuelve el exchange (`/api/orders`, `/api/account`). Los costos (comisión, deslizamiento, saldo)
son los del exchange (su `config.yaml`); si difieren de `bot/config.yaml` el bot avisa y manda el exchange.

```
# terminal 1: el exchange (repo strategy-sandbox-exchange)
python -m bot.run_bot_api --ejecucion exchange --start 2025-09-01 --end 2025-09-07       # terminal 2
# abrir http://127.0.0.1:8001/ : el visor entra solo en modo observador y muestra órdenes, posición y flechas
```

Por defecto la sesión NO se resetea al terminar (para poder verla en el visor); `--reset` la limpia.
Solo largos, una posición, órdenes de mercado. Detalle de la API: [`docs/API_EXCHANGE.md`](../docs/API_EXCHANGE.md).
Unidades: la API usa fracciones (`stop_fraccion: 0.05`); el panel usa porcentajes (`5`); la conversión
vive en `bot/reglas.py` (`pct_a_fraccion`, `fraccion_a_pct`).

## Panel de reglas (`por_niveles`)

```
python -m bot                       # o: python -m bot.panel_server  ->  http://127.0.0.1:8002/
```

La parte de reglas **solo escribe `bot/reglas.yaml`**: no habla con el exchange ni con el bot, no pide claves.
`por_niveles` lee ese archivo al arrancar. Si es inválido, el bot no arranca y explica por qué.

- `bot/limites.yaml`: límites fijos (riesgo máx., stop máx., rango del parcial, máx. zonas y reglas activas). Se editan **a mano**; el panel los muestra pero no los cambia. Se validan en el panel y otra vez en el bot.
- Seguridad del panel: solo `127.0.0.1`, rechaza `Host` que no sea localhost/127.0.0.1, el POST exige `Content-Type: application/json` y rechaza cualquier `Origin` ajeno.

### Qué soporta hoy el Broker (solo largos, una posición, tamaño y stop globales)

| Campo de reglas.yaml | Estado |
|---|---|
| `activo`, zonas, confirmaciones (cierre fuera de zona, RSI, EMA) | soportado |
| acciones `abrir_long` (BUY), `cerrar_long` (SELL), `solo_avisar` | soportado |
| `abrir_short` | **pendiente**: se guarda y valida; se ignora con aviso |
| `stop_pct` por regla | **pendiente**: se usa el stop global de `config.yaml` |
| `parcial` | **pendiente**: el Broker cierra siempre la posición entera |
| `riesgo_pct` | **pendiente**: se usa `position_fraction` de `config.yaml` |

Al arrancar, `por_niveles` imprime qué campos ignora y qué valores reales se usan.

### Qué haría falta para soportar lo pendiente (requiere tocar Broker/Strategy)

- **Shorts**: lado (long/short) en `Position` y `Broker`, PnL y stop/objetivo espejados, y acciones nuevas en la interfaz `Strategy`.
- **Stop por regla**: que la señal lleve stop (y objetivo) por operación y el Broker los use en lugar de `Params`.
- **Parcial**: `Broker.sell_partial(fracción)` con trades parciales y coste proporcional, más una acción nueva en la interfaz.
- **Riesgo por operación**: `buy` calcula `qty = riesgo × caja / distancia al stop` en vez de `position_fraction`.

Una regla se dispara con la última vela cerrada recibida si toca la zona (`low <= hasta` y `high >= desde`) y cumple todas sus confirmaciones. Solo usa velas cerradas.

## Registro de lo que el bot recibe y envía (`--log`)

```
python -m bot.run_bot_api --start 2025-09-01 --end 2025-09-07 --ejecucion exchange --log [--log-nivel completo] [--verbose]
# verificar el log: `verificar_log` vive en el repo strategy-sandbox-exchange (necesita sus datos)
```
- `--log` (apagado por defecto) escribe `bot/logs/bot_<fecha-hora>.jsonl` (un evento JSON por línea) y mantiene
  `bot/logs/ultimo.jsonl` como enlace al log en curso. `--log-nivel completo` añade `feed_<fecha-hora>.csv` con cada vela
  recibida y el "ahora" de cuando llegó. `--verbose` imprime una línea por decisión y por orden.
- Eventos: `inicio`, `step`, `klines`, `consulta`, `decision`, `orden_enviada`, `orden_respuesta`, `fill`, `error`, `fin`.
- Las órdenes se registran con lista blanca de campos (`bot/registro.py: CAMPOS_ORDEN`): nunca claves, firmas ni cabeceras.
- Si el log no se puede escribir, el bot avisa y sigue sin él; no cambia ninguna decisión ni resultado.
- En vivo (PowerShell): `Get-Content bot\logs\ultimo.jsonl -Wait -Tail 20`
- Límite de logs: al iniciar con `--log` se conservan solo las últimas `logs_conservar` corridas (`bot/config.yaml`, por defecto 20) y se borran
  las más antiguas con su `bot_*.jsonl` y `feed_*.csv`; imprime cuántos archivos limpió. `0` = no borrar nada; un valor inválido avisa y
  no borra. Solo toca archivos con esos nombres exactos: nunca `ultimo.jsonl`, la corrida actual ni otros archivos.

## Tests

`run_tests.bat` (desde `btc/`) corre las 3 suites por separado.

## Red (variables de entorno)

- `EXCHANGE_URL`: dirección del exchange (sustituye a `api_url` de `bot/config.yaml`; por defecto `http://127.0.0.1:8001`). `--url` manda sobre ella.
- `PANEL_HOST` / `PANEL_PORT`: dónde escucha el panel (por defecto `127.0.0.1` / `8002`). Con `0.0.0.0` avisa de que no hay autenticación.
- `PANEL_ALLOWED_HOSTS`: lista separada por comas que se suma a `127.0.0.1`/`localhost` en la guardia de Host/Origin (p. ej. la IP de la LAN).
