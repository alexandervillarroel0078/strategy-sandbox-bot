# API del mercado simulado (el exchange)

> Copia del contrato del exchange (repo `strategy-sandbox-exchange`, donde vive el original: puede estar más actualizado).

Solo simulación, sin dinero real. Servidor por defecto solo en `127.0.0.1:8001` (ver «Red» abajo). El futuro nunca sale del proceso:

todo se recorta a lo cerrado/enviado hasta el "ahora" simulado. Ningún endpoint ni mensaje de error
devuelve una fecha posterior al "ahora" ni el final de los datos (`end_of_data` y `last_date` solo aparecen cuando el reloj ya llegó ahí).

Instantes en **ms UTC**. Costos y fracciones en **fracción** (`0.05` = 5 %), nunca en porcentaje.
(El panel de reglas del bot usa porcentajes, `5` = 5 %; la conversión vive en `bot/reglas.py`:
`pct_a_fraccion` / `fraccion_a_pct`.)

## Mercado y sesión
| Ruta | Descripción |
|---|---|
| `GET /api/info` | `{base, data_start, data_start_ms}`: solo el primer dato histórico (el visor la usa; el bot nunca). **No** revela el final de los datos |
| `GET /api/session` | `{active, now, start, end_of_data[, driver]}` |
| `POST /api/session/start` `{start[, driver]}` | Inicia sesión y una **cuenta nueva**. `driver`: `bot` o `viewer` (quién conduce el reloj). Un `start` fuera del rango responde 400 `start fuera del rango disponible`, igual antes del inicio y después del final, sin fechas |
| `POST /api/session/reset` | Cierra la sesión; las órdenes NEW pasan a CANCELED |
| `GET /api/klines?interval=&limit=&endTime=` | Velas cerradas hasta el "ahora" |
| `POST /api/step` `{interval, candles}` | Avanza el reloj; devuelve las velas nuevas y `account` = `{saldo, posicion}` |

## Órdenes (solo largos, una posición a la vez, solo mercado)
### `POST /api/order`
```json
{"lado": "buy|sell", "tipo": "market", "fraccion": 1.0,
 "stop_fraccion": 0.05, "take_profit_fraccion": 0.10, "liquidar": false}
```
- `fraccion`: parte del saldo a gastar (compra), en `(0, 1]`; por defecto 1. En ventas solo vale 1 (sin parciales).
- `stop_fraccion` (0 a 1, exclusivo) y `take_profit_fraccion` (> 0) solo en compras; opcionales.
- Responde `{id, estado, ahora[, motivo]}` con HTTP 200. `estado` es `NEW`, `FILLED` (solo `liquidar`) o `REJECTED`.
- `REJECTED` (con `motivo`): saldo insuficiente (`saldo * fraccion < min_notional` o ≤ 0), ya hay posición o compra
  pendiente, venta sin posición o ya pendiente, stop/objetivo incoherentes, tipo distinto de `market`, parciales.
- Sin sesión: HTTP 409.

**Cuándo se ejecuta** (anti-futuro): una orden enviada en el instante *ahora* nunca se llena en ese instante. Se llena
en la **apertura** de la siguiente vela que entrega el reloj (`step`, en la temporalidad que use `step`), con
deslizamiento (`slippage`) y comisión (`fee`). Una orden no se llena en una vela que abrió antes de enviarla.
Después, en esa misma vela, el exchange vigila stop y objetivo con high/low y genera la orden de salida
(`tipo` `stop` o `take_profit`):
- si se tocan ambos en la misma vela, se asume que se tocó el **stop primero**;
- si la vela abre ya por debajo del stop, se sale a la **apertura**;
- el objetivo es una orden límite: sin deslizamiento (a la apertura si abre por encima).

**`liquidar: true`** (fin de simulación): solo con posición abierta (si no, `REJECTED`). Cierra ya, al **cierre de la última
vela ya procesada** (con deslizamiento), nunca de una posterior. `razon: "fin_simulacion"`.

### `GET /api/orders`
`{ahora, ordenes: [...]}`. Solo lo ya enviado; nada con ejecución posterior al "ahora". Cada orden:
`id, lado, tipo, estado (NEW|FILLED|CANCELED|REJECTED), creada, fraccion, stop_fraccion, take_profit_fraccion, liquidar,
motivo, ejecutada, precio, cantidad, comision, monto, razon (señal|stop|target|fin_simulacion)`.
`monto` = USDT gastados (compra, comisión incluida) o recibidos (venta, neto de comisión).

### `GET /api/account`
`{ahora, saldo, posicion, precio_marca, equity, saldo_inicial, fee, slippage, min_notional, trades}`.
`posicion` = `{entrada, precio_entrada, cantidad, stop, objetivo}` o `null`. `equity` = saldo + posición al último cierre
de 5m ya cerrado. `trades` = operaciones cerradas (entrada/salida, costo, pnl, comisiones, razón).

## Configuración (`config.yaml` del exchange)
`initial_balance`, `fee`, `slippage`, `min_notional`: son los de la cuenta del exchange. El bot los lee de
`/api/account` y avisa si difieren de su `config.yaml` (manda el exchange).

## Red (variables de entorno)

| Variable | Por defecto | Efecto |
|---|---|---|
| `EXCHANGE_HOST` | `127.0.0.1` | Dirección donde escucha. `0.0.0.0` = todas las interfaces (imprime un aviso: **no hay autenticación**) |
| `EXCHANGE_PORT` | `8001` (o `port` de `config.yaml`) | Puerto |
| `EXCHANGE_ALLOWED_HOSTS` | vacío | Lista separada por comas que se suma a `127.0.0.1`/`localhost` en la guardia anti DNS-rebinding, p. ej. la IP de la LAN |

`--host` y `--port` de la línea de comandos mandan sobre las variables.
