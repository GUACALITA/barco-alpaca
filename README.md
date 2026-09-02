# Alpaca AI Trading Agents Hackathon
## Guacalita Inc. | Deadline: 4 sep 2026 | Premio: $6,000 USD

**Plataforma:** lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon  
**Modo:** Paper trading ($100,000 USD virtual, fees 0%)  
**Puerto nuevo:** 30850 (libre, sin conflicto)  
**Plan detallado:** `PLAN_ALPACA_HACKATHON.md`  
**Código histórico:** `historico/` (alpaca_client_v2.py + trading_agent_multimercado_v3.py)

---

## IMPORTANTE — Lo que YA existe

El `trading_agent.py` en producción (**NO TOCAR — corre en :30835 con dinero real**) ya tiene:
- `AlpacaClient` importado y corriendo en modo paper
- `stocks_loop()` activo consultando SPY, QQQ, AAPL, TSLA, NVDA, MSFT, AMZN, META
- El `alpaca_client.py` ya existe en el servidor

La idea para el hackathon es crear `/root/trading_agent_alpaca/` — un agente **independiente y separado** que solo usa Alpaca, con grid completo + opciones, en puerto 30850.

---

## Servicios que BARCO usa — mapeo para duplicar en Alpaca

### Servicios de inteligencia (NO duplicar — conectar a los mismos)

Estos servicios ya corren en el VPS y BARCO-Alpaca los consumirá exactamente igual:

| Puerto | Servicio | Cómo lo usa BARCO | Endpoint clave |
|--------|----------|-------------------|---------------|
| **30818** | VecFrachZ Filter (α=2.1875, θ=72°) | Señales BUY/SELL/HOLD por símbolo crypto | `GET /signals?limit=50` |
| **30840** | S1 Triangular Arbitrage | Estado arbitraje y mercado | `GET /api/status` |
| **30841** | S2 OrderBook CNN | Presión compradora 0-1 por símbolo | `GET /api/status` |
| **30842** | S3 Sentiment NLP | Fear & Greed + noticias en tiempo real | `GET /api/status` |
| **30843** | Meta-Brain Aggregator | Score final -1→+1 (combina S1+S2+S3+VFZ) | `GET /api/status` |
| **30827** | Exchange Monitor | Precios divisas y criptos cruzados | `GET /...` |
| **30836** | Market Intel | Recomendación AVOID/NEUTRAL/OPTIMAL | `GET /...` |
| **30814** | SwarmOrchestrator RAG | Búsqueda semántica datos históricos | `POST /search` |
| **30815** | InferAI Gateway | LLM inference (múltiples modelos) | `POST /infer` |
| **30816** | OmniRoute | Router de razonamiento | `POST /route` |
| **31900** | AncloFlutter Bridge | Push notifications al móvil | `POST /notify` |
| **8901**  | ANCOL Memory Search | Memoria episódica semántica | `POST /search` |

### Cómo consumir VecFrachZ en Alpaca

VFZ analiza el ACTIVO, no el exchange. La señal BTC=BUY es válida tanto en Binance como en Alpaca.
Solo cambia el formato del símbolo:

```python
# VFZ devuelve señales por activo (sin exchange):
# {"BTC": "BUY", "SOL": "SELL", "XRP": "HOLD", "ETH": "BUY"}

# Binance usaba:  BTCUSDT, SOLUSDT, XRPUSDT
# Alpaca usa:     BTC/USD, SOL/USD, XRP/USD

# En signal_connector_alpaca.py — solo renombrar el símbolo:
VFZ_TO_ALPACA_SYMBOL = {
    "BTC": "BTC/USD",
    "ETH": "ETH/USD",
    "SOL": "SOL/USD",
    "XRP": "XRP/USD",
    "BNB": "BNB/USD",
    "AVAX": "AVAX/USD",
}
# La señal en sí (BUY/SELL/HOLD) se usa igual — no cambia nada más
```

### ⚠️ DÓNDE VIVE EL RENOMBRADO — NO OLVIDAR

```
VFZ :30818  →  devuelve activos puros: {"BTC": "BUY", "SOL": "SELL"}
                            │
              ┌─────────────┴──────────────┐
              │                            │
   BARCO-Binance                    BARCO-Alpaca
   trading_agent.py                 signal_connector_alpaca.py  ← AQUÍ
   mapea: BTC → BTCUSDT             mapea: BTC → BTC/USD
   (ya existe, NO TOCAR)            (archivo NUEVO — solo en :30850)
```

**Reglas irrompibles:**
- VFZ **no se modifica** — devuelve activos sin exchange, siempre
- BARCO-Binance **no se modifica** — su mapeo interno no se toca
- El mapeo para Alpaca vive **únicamente** en `signal_connector_alpaca.py`
- Si mañana agregamos Alpaca a otro agente, ese agente hace su propio mapeo

### Cómo BARCO consume Meta-Brain (reproducir en Alpaca)

```python
# De trading_agent.py — api_intelligence()
urls = {
    "s1": "http://127.0.0.1:30840/api/status",
    "s2": "http://127.0.0.1:30841/api/status",
    "s3": "http://127.0.0.1:30842/api/status",
    "brain": "http://127.0.0.1:30843/api/status",
}
# state["market_intel_rec"] = "AVOID" | "NEUTRAL" | "OPTIMAL"
# Efecto → market_avoid = (market_intel_rec == "AVOID") → skip_buys=True
```

### Módulos Python que se copian (mismo código, sin cambios)

| Módulo | Qué hace | Acción para Alpaca |
|--------|----------|--------------------|
| `grid_strategy.py` | Grid adaptativo con posiciones y PnL | **Copiar sin cambios** |
| `adaptive_grid.py` | Ajuste automático del grid size | **Copiar sin cambios** |
| `risk_manager.py` | Stop-loss, daily loss limit, pausa | **Copiar sin cambios** |
| `trades_db.py` | SQLite: posiciones, trades, PnL | **Copiar, nueva DB** (`trades_alpaca.db`) |

### Módulo que ya existe para Alpaca

| Módulo | Dónde está | Estado |
|--------|-----------|--------|
| `alpaca_client.py` | En el servidor (ya en producción) | ✅ Funciona en stocks_loop() |
| `alpaca_client_v2.py` | `historico/alpaca_client_v2.py` | ✅ Versión mejorada con bulk prices |

### Módulos NUEVOS para el hackathon

| Módulo | Qué hace |
|--------|----------|
| `signal_connector_alpaca.py` | Mapea VFZ + Meta-Brain → símbolos Alpaca |
| `options_strategy.py` | Covered calls + cash-secured puts automáticos |
| `trading_agent_alpaca.py` | Agente principal en puerto 30850 |

---

## Arquitectura — qué se duplica y qué se comparte

### REGLA CLARA:
- Los servicios de INTELIGENCIA → **NO se duplican, NO son de Binance**
  Son agnósticos al exchange — analizan el ACTIVO (BTC, ETH, NVDA), no el exchange.
  La señal de BTC es la misma señal de BTC trade donde trade.
  Solo cambia el nombre del símbolo: BTCUSDT (Binance) → BTC/USD (Alpaca).
- Los módulos de EJECUCIÓN → **SÍ se copian** — cada agente necesita su propio estado, posiciones y base de datos

```
VPS 207.180.253.38
│
│  ═══════════════════════════════════════════════════
│  CAPA DE INTELIGENCIA — COMPARTIDA (no duplicar)
│  Estos servicios responden a cualquier cliente que los llame.
│  BARCO-Binance los llama. BARCO-Alpaca los llama. Mismo servicio.
│  ═══════════════════════════════════════════════════
│
│  VFZ:30818      S1:30840     S2:30841
│  S3:30842       Meta-Brain:30843
│  Market Intel:30836          Exchange Monitor:30827
│         │                           │
│         │ HTTP GET (simultáneo)     │ HTTP GET (simultáneo)
│         ▼                           ▼
│
├── BARCO BINANCE (LIVE — NO TOCAR)
│   Carpeta:  /root/trading_agent/
│   Puerto:   30835
│   Capital:  $87 USDT real en Binance
│   Exchange: binance_client.py       ← exclusivo Binance
│   Grid:     grid_strategy.py        ← copia propia
│   DB:       trades.db               ← base de datos propia
│   Nuevo:    claude_brain.py         ← NO tiene (solo Alpaca)
│
└── BARCO ALPACA (NUEVO — hackathon)
    Carpeta:  /root/trading_agent_alpaca/   ← carpeta NUEVA
    Puerto:   30850                          ← puerto NUEVO, sin conflicto
    Capital:  $100,000 USD virtual (Alpaca paper)
    Exchange: alpaca_client_v2.py    ← exclusivo Alpaca (NUEVO)
    Grid:     grid_strategy.py       ← copia propia (igual al de Binance)
    DB:       trades_alpaca.db       ← base de datos propia (NUEVA)
    Nuevo:    claude_brain.py        ← MCP cada 60 min (NUEVO)
    Nuevo:    signal_connector_alpaca.py ← mapeo símbolos (NUEVO)
    Nuevo:    options_strategy.py    ← covered calls + CSP (NUEVO)
```

### Resumen ejecutivo

| Componente | Para Alpaca |
|---|---|
| VFZ, S2, S3, Meta-Brain, Market Intel | **Consumir igual** — mismos puertos, mismas URLs |
| grid_strategy.py, risk_manager.py | **Copiar** — mismo código, estado separado |
| binance_client.py | **NO usar** — reemplazar por alpaca_client_v2.py |
| trades.db | **NO usar** — crear trades_alpaca.db nueva |
| alpaca_client_v2.py | **NUEVO** — ya en `historico/` |
| claude_brain.py | **NUEVO** — MCP + decisión cada 60 min |
| signal_connector_alpaca.py | **NUEVO** — mapea VFZ crypto → símbolos Alpaca |
| options_strategy.py | **NUEVO** — covered calls + cash-secured puts |

---

## Flujo de una decisión (para el video demo)

```
1. S3 NLP detecta: "NVIDIA beats earnings, raises guidance"
   → score NVDA = +0.85

2. S2 CNN detecta presión compradora NVDA: 0.72

3. VecFrachZ dice BTC: BUY, SOL: SELL

4. Meta-Brain agrega: score_final = +0.54 → OPTIMAL

5. signal_connector_alpaca.py:
   - NVDA: skip_buys = False (comprar)
   - SOL/USD: skip_buys = True (VFZ dice SELL)

6. Grid tick NVDA: precio cruza nivel → BUY 100 shares @ $120

7. options_strategy: vende CALL $122 viernes → cobra $120 prima

8. NVDA sube a $122 → Grid SELL → PnL $200 grid + $120 prima = $320

9. AncloFlutter push: "NVDA SELL $320 profit"
```

---

## Activos paper trading

### Crypto Alpaca (24/7, fees 0%)
| Símbolo | Capital | Grid size | Señal de |
|---------|---------|-----------|----------|
| BTC/USD | $15,000 | 0.3% | VFZ BTC |
| ETH/USD | $10,000 | 0.4% | VFZ ETH |
| SOL/USD | $5,000  | 0.5% | VFZ SOL |
| XRP/USD | $3,000  | 0.5% | VFZ XRP |

### Stocks US (9:30-16:00 ET)
| Símbolo | Capital | Grid size | Señal de |
|---------|---------|-----------|----------|
| NVDA    | $10,000 | 0.5% | S3 NLP tech + S2 CNN |
| TSLA    | $8,000  | 0.5% | S3 NLP tech |
| SPY     | $20,000 | 0.3% | Meta-Brain macro |
| AAPL    | $8,000  | 0.3% | S3 NLP + Meta-Brain |
| AMZN    | $6,000  | 0.4% | S3 NLP tech |

---

## Variables de entorno necesarias

```bash
# Alpaca paper (gratis en alpaca.markets)
ALPACA_API_KEY=<paper key>
ALPACA_API_SECRET=<paper secret>
ALPACA_BASE_URL=https://paper-api.alpaca.markets

# Capital y puerto
CAPITAL_USD=100000
PORT=30850

# Servicios de inteligencia (ya corriendo en el VPS)
VFZ_URL=http://localhost:30818
META_BRAIN_URL=http://localhost:30843
S2_URL=http://localhost:30841
S3_URL=http://localhost:30842
ANCLO_URL=http://10.43.114.171:8900
```

---

## Cronograma restante (hoy 31 ago = día 4)

| Día | Fecha | Tarea | Estado |
|-----|-------|-------|--------|
| 4 | **31 ago HOY** | `options_strategy.py` + primer trade opción | ⬜ |
| 5 | 1 sep | Dashboard web :30850 completo | ⬜ |
| 6 | 2 sep | Testing + PnL paper positivo | ⬜ |
| 7 | 3 sep | Video demo + README técnico | ⬜ |
| 8 | **4 sep** | **SUBMISSION en lablab.ai** | ⬜ |

**Días 1-3 (completados según plan):**
- Día 1 (28 ago): alpaca_client_v2.py + WebSocket
- Día 2 (29 ago): trading_agent_alpaca.py + grid crypto
- Día 3 (30 ago): grid stocks + signal_connector_alpaca.py

---

## Archivos en esta carpeta

```
convocatorias/alpaca/
├── README.md                       ← este archivo
├── ARQUITECTURA_MCP_CLI.md         ← arquitectura detallada (loops, tools, system prompt)
├── PLAN_ALPACA_HACKATHON.md        ← plan técnico completo (8 fases)
├── DEPLOY_SERVIDOR.md              ← comandos exactos para el servidor
│
├── main_alpaca.py                  ← agente principal (FastAPI + 3 loops)
├── claude_brain.py                 ← Loop 1: Claude via MCP tools cada 60 min
├── signal_connector_alpaca.py      ← mapeo VFZ/S2/S3 → símbolos Alpaca
├── trades_db_alpaca.py             ← SQLite: trades_alpaca.db (NUNCA trades.db)
├── barco_alpaca.service            ← systemd unit para el servidor
├── requirements.txt                ← fastapi + uvicorn + httpx + anthropic
├── env.example                     ← variables de entorno necesarias
│
└── historico/
    ├── alpaca_client_v2.py         ← cliente Alpaca REST (stocks + crypto + opciones)
    └── trading_agent_multimercado_v3.py  ← versión multi-mercado (referencia)
```

### Estructura en el servidor (`/root/trading_agent_alpaca/`)

```
/root/trading_agent_alpaca/     ← NUEVA carpeta, separada de /root/trading_agent/
├── main_alpaca.py
├── claude_brain.py
├── signal_connector_alpaca.py
├── trades_db_alpaca.py
├── alpaca_client_v2.py         ← copiado de historico/ (import directo, sin subcarpeta)
├── trades_alpaca.db            ← generada automáticamente por init_db()
├── .env                        ← basado en env.example
└── venv/                       ← entorno virtual Python
```

**REGLA CRÍTICA DB:** `trades_alpaca.db` es la ÚNICA base de datos de BARCO-Alpaca.
Nunca escribe en `trades.db` (que pertenece a BARCO-Binance en `/root/trading_agent/`).

---

## Repositorio submission

```
barco-alpaca/                   ← repo GitHub para el hackathon
├── README.md
├── main_alpaca.py              ← agente principal
├── claude_brain.py             ← Loop 1: Claude MCP
├── alpaca_client_v2.py         ← cliente REST
├── signal_connector_alpaca.py  ← mapeo símbolos
├── trades_db_alpaca.py         ← SQLite separada
├── requirements.txt
├── env.example
└── demo/
    ├── demo_video.mp4
    └── paper_pnl_report.json
```
