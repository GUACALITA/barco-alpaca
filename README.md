# BARCO-Alpaca — Autonomous AI Trading Agent

**Alpaca AI Trading Agents Hackathon 2026** — Built by Guacalita Inc.

🔴 **Live Dashboard:** http://207.180.253.38:30850/

---

## What it does

BARCO-Alpaca is a fully autonomous AI trading agent that manages a $100,000 paper portfolio. Every 15 minutes, Claude claude-opus-4-5 receives real-time market intelligence, calls MCP tools to verify live data, and makes one trading decision — executed immediately via the Alpaca API.

**Current results:** Portfolio at $100,013 (+$13 from start), 4 open positions (BTC, ETH, SOL, NVDA), all in profit.

---

## Architecture — Three parallel loops

```
┌─────────────────────────────────────────────────────────┐
│  Signal Stack                                           │
│  VecFrachZ (IBM Quantum) + S2 CNN + S3 NLP             │
│  → Meta-Brain score: -1 (AVOID) to +1 (OPTIMAL)        │
└────────────────────┬────────────────────────────────────┘
                     │
         ┌───────────▼───────────┐
         │  Loop 1: Claude Brain  │  every 15 min
         │  MCP Tools:            │
         │  • get_account         │
         │  • get_positions       │
         │  • get_latest_quote    │
         │  • get_options_chain   │
         │  → ONE JSON decision   │
         └───────────┬───────────┘
                     │
         ┌───────────▼───────────┐
         │  Alpaca REST API       │
         │  BUY_STOCK / BUY_CRYPTO│
         │  Market orders → fill  │
         │  instantly in paper    │
         └───────────────────────┘

┌──────────────────────────────────┐
│  Loop 2: Python Monitor (30s)    │
│  • Stop-loss at -3%              │
│  • Take-profit at +2%            │
│  • No Claude needed              │
└──────────────────────────────────┘

┌──────────────────────────────────┐
│  Loop 3: FastAPI Dashboard       │
│  GET /           → Live UI       │
│  GET /health     → Status        │
│  GET /api/status → Portfolio     │
│  GET /api/log    → Decisions     │
│  GET /api/signals → Signals      │
└──────────────────────────────────┘
```

---

## Signal Intelligence Stack

| System | What it does | Edge |
|--------|-------------|------|
| **VecFrachZ** | Quantum-validated crypto signals (BTC, ETH, SOL, ADA...) | IBM Quantum validated · 0% Bit Error Rate |
| **S2 OrderBook CNN** | CNN reads live order book snapshots → buy/sell pressure 0.0–1.0 | Real-time, per-symbol |
| **S3 NLP Sentiment** | Fear & Greed index + live news headlines | Multi-source sentiment |
| **Meta-Brain** | Aggregates all three → score -1 to +1 | AVOID / NEUTRAL / OPTIMAL gate |

Claude **only trades when Meta-Brain is OPTIMAL (> +0.3)**. Neutral signal = HOLD.

---

## Decision example

```json
{
  "action": "BUY_CRYPTO",
  "symbol": "BTC/USD",
  "notional": 2000.00,
  "reason": "VecFrachZ BTC BUY signal (quantum-validated), Meta-Brain OPTIMAL +0.45, S2=0.71 strong buy pressure"
}
```

---

## Risk rules (enforced by code, not Claude)

- Max **$2,000 per trade** (2% of $100k account)
- Max **5 open positions** simultaneously
- **Stop-loss: -3%** per position (Python monitor, every 30s)
- **Take-profit: +2%** per position (Python monitor, every 30s)
- **Never trade** if today P&L < -$5,000
- **Never trade** if options_buying_power < $10,000

---

## Files

| File | Purpose |
|------|---------|
| `main_alpaca.py` | FastAPI app, 3 loops, live dashboard at `/` |
| `claude_brain.py` | Claude decision loop, MCP tool handling |
| `alpaca_client_v2.py` | Alpaca REST API client (stocks, crypto, options) |
| `signal_connector_alpaca.py` | Fetches VFZ, S2 CNN, S3 NLP, Meta-Brain signals |
| `trades_db_alpaca.py` | SQLite logging of all decisions and trades |
| `barco_alpaca.service` | systemd service for production deployment |
| `env.example` | Environment variables template |

---

## Setup

```bash
git clone https://github.com/YOUR_USER/barco-alpaca
cd barco-alpaca
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp env.example .env
# Edit .env with your keys
python main_alpaca.py
```

Open http://localhost:30850/ for the live dashboard.

---

## Environment variables

```
ALPACA_API_KEY=your_alpaca_paper_key
ALPACA_API_SECRET=your_alpaca_paper_secret
ANTHROPIC_API_KEY=your_claude_key
PORT=30850
CLAUDE_CYCLE_MINUTES=15
MONITOR_INTERVAL_SECONDS=30
STOP_LOSS_PCT=-0.03
TAKE_PROFIT_PCT=0.02
ALPACA_DB_PATH=./trades_alpaca.db
```

Get a free Alpaca paper trading account at https://alpaca.markets

---

## Tech stack

- **Claude claude-opus-4-5** (Anthropic) — trading brain via MCP tool calls
- **Alpaca Trading API** — stock, crypto, options paper trading
- **Alpaca Market Data API** — real-time quotes
- **IBM Quantum** — quantum signal validation (VecFrachZ)
- **Python 3.11** · FastAPI · httpx · SQLite · uvicorn

---

Built for the [Alpaca AI Trading Agents Hackathon](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon/) · Guacalita Inc.
