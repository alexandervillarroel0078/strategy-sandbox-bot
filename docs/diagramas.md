# Diagramas del bot

Diagramas Mermaid (se ven en GitHub) de este repo, hechos leyendo el código tal como queda tras separar el proyecto en dos repos:
este (**bot**, cliente HTTP sin datos) y `strategy-sandbox-exchange` (el mercado simulado y sus datos). El contrato con el
exchange está en [API_EXCHANGE.md](API_EXCHANGE.md). Donde algo no queda claro en el código lo marco con **?**.

Índice: [1 Arquitectura](#1-arquitectura) · [2 Secuencia de una corrida](#2-secuencia-de-una-corrida) ·
[3 Imports](#3-dependencias-de-imports) · [4 Clases](#4-diagrama-de-clases) · [5 Decisión por vela](#5-flujo-de-decisión-por-vela) ·
[6 Anti-futuro en el cliente](#6-cómo-se-garantiza-que-el-bot-no-ve-datos-futuros) · [Rarezas](#rarezas-e-inconsistencias-detectadas)

---

## 1. Arquitectura

Dos procesos de este repo: el panel de control (`python -m bot`, `127.0.0.1:8002`) y el bot (`bot.run_bot_api`), que el panel lanza
como proceso aparte. El bot solo habla con el exchange por HTTP (`EXCHANGE_URL`, por defecto `http://127.0.0.1:8001`).

```mermaid
flowchart LR
    EXT["Exchange simulado<br/>otro repo · HTTP /api/*"]

    subgraph PAN["Panel de control · 127.0.0.1:8002"]
        PS["panel_server.py"]
        PUI["panel/index.html<br/>+ lightweight-charts"]
        GES["gestor.py<br/>GestorBot"]
        DV["datos_vivo.py"]
    end

    subgraph BOT["bot · proceso aparte"]
        RUN["run_bot_api.py"]
        XB["exchange_broker.py<br/>run_bot_exchange"]
        CLI["bot_client.py<br/>ExchangeClient"]
        STR["strategies/*"]
        REG["registro.py"]
        MET["metrics.py"]
    end

    REGLAS[/"bot/reglas.yaml"/]
    LIM[/"bot/limites.yaml"/]
    LOGS[/"bot/logs/<br/>bot_*.jsonl · feed_*"/]
    RES[/"bot/results/"/]

    PUI <-->|"GET/POST /reglas, /limites, /bot/*"| PS
    PS --> GES
    PS --> DV
    GES -->|"subprocess + Job Object"| RUN
    GES -->|"GET /api/info (¿responde?)"| EXT
    PS -->|"escribe"| REGLAS
    PS -->|"lee"| LIM
    RUN --> XB --> CLI
    CLI <-->|"HTTP /api/*"| EXT
    XB --> STR
    STR -->|"PorNiveles lee al arrancar"| REGLAS
    STR -->|"lee"| LIM
    RUN --> REG
    XB --> REG
    CLI --> REG
    REG -->|"escribe"| LOGS
    RUN --> MET -->|"trades y equity"| RES
    DV -->|"lee solo el final"| LOGS
```

Notas verificadas en el código:
- El panel no importa el bot: lo lanza con `subprocess` ([gestor.py](../bot/gestor.py)). Detener cierra solo el proceso que lanzó el panel.
- La ejecución es siempre `exchange`: `--ejecucion` solo acepta ese valor. `run_bot` (Broker interno) se conserva únicamente como referencia de los tests.
- Red configurable por entorno: `EXCHANGE_URL`, `PANEL_HOST`, `PANEL_PORT`, `PANEL_ALLOWED_HOSTS` (ver el [README](../bot/README.md)).

---

## 2. Secuencia de una corrida

`run_bot_api` crea el `ExchangeClient` y llama a `run_bot_exchange` ([exchange_broker.py](../bot/exchange_broker.py)).
Solo se usan rutas de la lista blanca `ALLOWED` ([bot_client.py](../bot/bot_client.py)); `/api/info` nunca.

```mermaid
sequenceDiagram
    autonumber
    participant U as Usuario
    participant R as run_bot_api
    participant B as run_bot_exchange<br/>ExchangeBroker
    participant C as ExchangeClient
    participant S as Estrategia
    participant X as Exchange :8001

    U->>R: python -m bot.run_bot_api --start ...
    R->>R: carga config.yaml, load_strategy, Registro opcional
    R->>B: run_bot_exchange(client, strat, params, clock, start, end, reset)
    B->>C: start_session(start_ms, driver bot)
    C->>X: POST /api/session/start
    X-->>C: now
    B->>C: account()
    C->>X: GET /api/account
    X-->>C: saldo_inicial, fee, slippage, posicion, trades
    Note over B: avisa si el exchange usa otros costos que config.yaml
    B->>C: get_klines(tf estrategia, 1)
    C->>X: GET /api/klines?interval=...&limit=1
    X-->>C: ultima vela cerrada

    loop mientras now < end y no haya fin de datos
        B->>C: step(clock, 1)
        C->>X: POST /api/step {interval, candles}
        Note over X: llena ordenes pendientes a la apertura<br/>y vigila stop y objetivo
        X-->>C: now, end_of_data, candles, account
        C->>C: _verificar: ninguna vela cierra despues de now
        C-->>B: Step
        B->>B: sync(account) con saldo y posicion
        opt cambio de saldo o posicion y registro activo
            B->>C: orders()
            C->>X: GET /api/orders
            X-->>C: ordenes hasta now
            B->>B: registra los fills nuevos
        end
        opt cerro una vela de la estrategia
            B->>C: get_klines(tf estrategia, lookback)
            C->>X: GET /api/klines
            X-->>C: ventana de velas cerradas
            B->>S: on_candle(velas, posicion)
            S-->>B: BUY, SELL o HOLD
            alt BUY y sin posicion
                B->>C: order(buy, market, fraccion, stop_fraccion, take_profit_fraccion)
                C->>X: POST /api/order
                X-->>C: id, estado NEW, ahora
            else SELL y con posicion
                B->>C: order(sell, market)
                C->>X: POST /api/order
                X-->>C: id, estado NEW, ahora
            end
        end
    end

    opt queda posicion abierta al final
        B->>C: order(sell, market, liquidar true)
        C->>X: POST /api/order
        X-->>C: estado FILLED al cierre de la ultima vela procesada
        B->>C: account()
        C->>X: GET /api/account
    end
    B->>C: account() para armar los trades
    C->>X: GET /api/account
    opt flag --reset
        B->>C: reset_session()
        C->>X: POST /api/session/reset
    end
    B-->>R: Result
    R->>U: metricas, CSV de trades y equity en bot/results, registro opcional
```

Notas:
- Una orden enviada nunca se llena en el instante en que se envía: queda `NEW` y se llena en la apertura de la **siguiente** vela del reloj, dentro de `POST /api/step`. La excepción es `liquidar`, que cierra al cierre de la última vela ya procesada.
- `ExchangeBroker.trades` hace un `GET /api/account` cada vez que se lee ([exchange_broker.py](../bot/exchange_broker.py)); por eso aparece una consulta final de cuenta.
- Sin `--reset` la sesión queda abierta para verla en el visor del exchange.

---

## 3. Dependencias de imports

Generado leyendo los `import`/`from` reales de todos los `.py` de `bot/` (sin tests). El bot **no importa nada del exchange**:
un test lo impone ([test_imports.py](../bot/tests/test_imports.py)).

```mermaid
flowchart TB
    subgraph BOTPKG["bot"]
        main["__main__"]
        run_bot_api
        bot_client
        exchange_broker
        registro
        broker
        sim_types
        metrics
        utils
        reglas
        panel_server
        gestor
        datos_vivo
        base["strategies.base"]
        ejemplo["strategies.ejemplo"]
        donchian["strategies.donchian"]
        por_niveles["strategies.por_niveles"]
    end

    main --> panel_server
    panel_server --> datos_vivo & reglas & gestor & utils
    gestor --> utils
    datos_vivo --> registro
    run_bot_api --> metrics & bot_client & broker & exchange_broker & reglas & registro & utils
    bot_client --> broker & registro & sim_types & base
    exchange_broker --> bot_client & broker & registro & sim_types & base
    registro --> utils
    utils --> base
    sim_types --> broker
    metrics --> sim_types
    base --> broker
    ejemplo --> broker & base
    donchian --> broker & base
    por_niveles --> broker & reglas & base & utils
    utils -. "importlib: carga la estrategia por nombre" .-> ejemplo & donchian & por_niveles
```

Cómo leerlo:
- `bot/utils.py` carga las estrategias dinámicamente con `importlib` (`bot.strategies.<nombre>`), por eso la flecha punteada.
- `panel_server` no importa `run_bot_api`: `gestor` lo lanza como subproceso.
- Las herramientas de simulación y verificación del repo del exchange sí importan `bot.broker`, `bot.metrics`, `bot.utils` y `bot.strategies.base` (por eso ese repo documenta una dependencia hacia este).

---

## 4. Diagrama de clases

Núcleo del bot. `ExchangeBroker` **no hereda** de `Broker`: copia su interfaz de lectura (duck typing). `run_bot_exchange` usa
`ExchangeBroker`, que delega en el exchange; `Broker` interno solo lo usa `run_bot` (referencia de los tests). Las reglas son funciones de módulo (`bot/reglas.py`), no una clase.

```mermaid
classDiagram
    class Strategy {
        <<abstract>>
        +timeframe str
        +lookback int
        +on_candle(candles, position) str
    }
    class EmaRsi {
        +lookback 600
        +explicacion dict
    }
    class Donchian {
        +lookback 101
    }
    class PorNiveles {
        +lookback 600
        +reglas dict
        +zonas dict
    }
    class Position {
        <<frozen dataclass>>
        +entry_time int
        +entry_price float
        +qty float
        +stop float
        +target float
    }
    class Params {
        <<dataclass>>
        +initial_balance
        +fee
        +slippage
        +stop_pct
        +target_pct
        +position_fraction
    }
    class Broker {
        +cash float
        +pos Position
        +trades list
        +buy(ref_price, ts) bool
        +sell_market(ref_price, ts, reason)
        +check_exit(o, h, l, ts) bool
        +equity(price) float
    }
    class ExchangeBroker {
        +cash float
        +pos Position
        +sync(acct)
        +buy(ref_price, ts) bool
        +sell_market(ref_price, ts, reason)
        +trades list
        +equity(price) float
    }
    class ExchangeClient {
        +now int
        +n_requests int
        +start_session(start_ms, driver) int
        +reset_session()
        +get_klines(interval, limit) DataFrame
        +step(interval, candles) Step
        +order(orden) dict
        +orders() list
        +account() dict
    }
    class Step {
        <<NamedTuple>>
        +now int
        +end_of_data bool
        +candles list
        +account dict
    }
    class Result {
        <<dataclass>>
        +trades
        +equity
        +decisions
        +params
        +audit
    }
    class reglas {
        <<module>>
        +cargar_reglas(path, limites) dict
        +cargar_limites(path) dict
        +validar(d, lim) list
        +pendientes(d, cfg) list
        +pct_a_fraccion(pct) float
        +ReglasInvalidas
    }
    class Registro {
        +activo True
        +nivel str
        +evento(tipo, datos)
        +feed(ahora, fuente, intervalo, filas)
        +cerrar()
    }
    class RegistroNulo {
        +activo False
        +evento(tipo, datos)
    }
    class registro {
        <<module>>
        +permitir(d) dict
        +registrar_decision()
        +registrar_fills_internos()
        +registrar_fills_exchange()
    }
    class panel_server {
        <<module>>
        +make_server(reglas_path, limites_path) Server
        +escribir_atomico(path, data)
    }
    class GestorBot {
        +iniciar(datos) dict
        +detener() dict
        +estado() dict
        +cerrar()
    }

    Strategy <|-- EmaRsi
    Strategy <|-- Donchian
    Strategy <|-- PorNiveles
    Strategy ..> Position : recibe
    Broker o-- Position
    Broker o-- Params
    ExchangeBroker o-- Position
    ExchangeBroker o-- Params
    Broker <.. ExchangeBroker : misma interfaz de lectura
    ExchangeBroker --> ExchangeClient : envia ordenes
    ExchangeClient ..> Step : devuelve
    ExchangeClient --> Registro : escribe
    Registro <|-- RegistroNulo : mismo contrato, NULO
    registro ..> Registro
    PorNiveles ..> reglas : carga al construirse
    panel_server ..> reglas : valida y escribe
    panel_server --> GestorBot : iniciar / detener
```

Notas:
- `Registro` y `RegistroNulo` no comparten clase base: `RegistroNulo` solo replica los métodos (`evento`, `feed`, `cerrar`) y la constante `NULO` se usa como valor por defecto (**?** en código no hay `class RegistroNulo(Registro)`).
- `run_bot` y `run_bot_exchange` son funciones, no métodos; ambas devuelven `Result` (la versión de `bot/sim_types.py`).
- `ejemplo.EmaRsi` expone `explicacion` (motivo y valores) que `registrar_decision` lee con `getattr`; `Donchian` y `PorNiveles` no la definen.

---

## 5. Flujo de decisión por vela

Qué pasa cuando cierra una vela de la temporalidad de la estrategia (`run_bot_exchange`). La estrategia `por_niveles`
carga `reglas.yaml` y `limites.yaml` **una sola vez, al construirse**, no en cada vela.

```mermaid
flowchart TD
    A["POST /api/step del reloj fino<br/>5m o 15m"] --> B{"¿cerró una vela de la<br/>temporalidad de la estrategia?"}
    B -- "no" --> A
    B -- "sí" --> C["GET /api/klines<br/>últimas lookback velas cerradas"]
    C --> D{"¿la última vela es nueva?"}
    D -- "no" --> A
    D -- "sí" --> E["strategy.on_candle(velas, posición)"]

    subgraph PN["Solo en PorNiveles"]
        direction TB
        P0["__init__: cargar_limites + cargar_reglas<br/>reglas.yaml, o reglas.example.yaml si falta"]
        P1{"¿activo y hay velas<br/>de calentamiento?"}
        P2["por cada regla activa:<br/>¿la vela toca la zona?<br/>+ confirmaciones: cierre fuera, RSI, EMA"]
        P3["abrir_long → BUY<br/>cerrar_long → SELL<br/>solo_avisar o abrir_short → imprime y HOLD"]
        P0 -.-> P1 --> P2 --> P3
    end
    E -.-> P1

    E --> F{"acción"}
    F -- "HOLD" --> G["se anota la decisión"]
    F -- "BUY sin posición<br/>o SELL con posición" --> G
    G --> H["registrar_decision<br/>evento decision en el .jsonl"]
    H --> K["POST /api/order tipo market<br/>el exchange la llena en la apertura<br/>de la siguiente vela del reloj"]
    K --> M["registrar_fills_exchange<br/>GET /api/orders"]
    M --> N[("bot/logs/bot_fecha.jsonl<br/>ultimo.jsonl")]
    N --> O["panel: datos_vivo<br/>lee el final del log y del feed"]
```

Notas:
- El `.jsonl` solo se escribe con `--log` o `--verbose` (el panel siempre lanza con `--log --log-nivel completo`); sin ellos el registro es `NULO` y no se escribe nada.
- La verificación posterior del log contra los datos (`verificar_log`) vive en el repo del exchange, porque necesita sus datos.
- `limites.yaml` se valida dos veces: el panel rechaza lo que lo supere al guardar y `PorNiveles` lo vuelve a validar al cargar (`cargar_reglas` → `validar`).
- Los campos que el `Broker` no soporta (`abrir_short`, `stop_pct` por regla, parciales, `riesgo_pct`) se guardan pero se **ignoran**; `pendientes()` los lista al arrancar ([por_niveles.py](../bot/strategies/por_niveles.py)).

---

## 6. Cómo se garantiza que el bot no ve datos futuros

Del lado del bot hay dos capas; el servidor (que solo entrega lo cerrado hasta el «ahora» simulado) se documenta en el repo del exchange.

```mermaid
flowchart LR
    X["Exchange<br/>solo entrega velas cerradas<br/>hasta el ahora simulado"] --> AL
    subgraph CLI["Cliente: bot_client.py"]
        AL["ALLOWED<br/>lista blanca de rutas<br/>/api/info queda fuera"]
        VE["_verificar<br/>LeakError si una vela cierra<br/>después del ahora"]
    end
    AL --> VE --> STR["Estrategia<br/>solo recibe velas cerradas<br/>y una Position de solo lectura"]
    VE -. "log" .-> LOG[("bot/logs")]
```

| Capa | Dónde | Qué hace |
|---|---|---|
| Cliente, lista blanca | `ALLOWED` y `_request` en [bot_client.py](../bot/bot_client.py) | Rechaza cualquier ruta fuera de `ALLOWED` (`ExchangeError`); `/api/info` queda fuera. |
| Cliente, verificación | `_verificar` en [bot_client.py](../bot/bot_client.py) | `LeakError` si alguna vela recibida (en `klines` o `step`) cierra después del `now`. |
| Estrategia | [base.py](../bot/strategies/base.py) | La estrategia solo recibe las últimas `lookback` velas **cerradas** y una `Position` de solo lectura. |
| Panel | [datos_vivo.py](../bot/datos_vivo.py) | Solo lee el **final** del log y del feed, valida el nombre del feed y devuelve campos fijos. |

---

## Rarezas e inconsistencias detectadas

**Duplicación**
1. `Audit` y `Result` tienen copias propias en [sim_types.py](../bot/sim_types.py) (aislamiento: el bot no importa del exchange). `metrics.py` anota el tipo de `sim_types.Result`, pero el simulador directo del otro repo le pasa su propio `Result` (funciona por duck typing).
2. `interval_ms` / `_origin` ([bot_client.py](../bot/bot_client.py)) repiten a propósito lógica del servidor.
3. `rsi()` está copiada en [ejemplo.py](../bot/strategies/ejemplo.py) y [por_niveles.py](../bot/strategies/por_niveles.py).

**Acoplamientos y estilo**
4. [exchange_broker.py](../bot/exchange_broker.py) importa `_origin` (privado) de `bot_client`. `ExchangeBroker.buy` y `sell_market` reciben `ref_price` y `ts` que no usan (compatibilidad con la interfaz de `Broker`), y `run_bot_exchange` ignora el booleano que devuelve `buy`.
5. Estilo de imports mezclado: `strategies/*` usan absolutos (`from bot.broker import …`); el resto, relativos.
6. `ExchangeBroker.trades` es una propiedad que hace una petición HTTP cada vez que se lee.
7. `ExchangeBroker.sync` representa «sin stop» como `0.0` y «sin objetivo» como `inf` para encajar en `Position`, mientras que el exchange usa `None`.

**Comportamientos que no cuadran del todo**
8. `run_bot` (Broker interno, ya sin entrada por CLI) llama a `start_session` **sin** `driver`; solo `run_bot_exchange` declara `driver: "bot"`. Quedan `run_bot` y `registrar_fills_internos` solo por los tests de equivalencia con el exchange.
9. `run_bot_api` registra siempre el sha256 de `bot/reglas.yaml`, incluso con estrategias que no usan reglas. Si `reglas.yaml` faltara y se usara `reglas.example.yaml`, el log diría `ruta: bot/reglas.yaml` con `sha256: null` (**?** convendría registrar el archivo realmente cargado).
10. `PorNiveles` y `Donchian` no definen `explicacion`: en el log los eventos `decision` salen con `motivo: null`; solo `EmaRsi` lo rellena. `PorNiveles` además usa `print` dentro de `on_candle`.
11. `PorNiveles` acepta en `reglas.yaml` campos que ignora (`abrir_short`, `stop_pct` por regla, parcial, `riesgo_pct`); está documentado y se avisa al arrancar, pero el panel permite configurarlos.
