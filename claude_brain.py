"""
claude_brain.py — Loop 1: Claude decide via MCP tools cada 60 minutos.

Claude recibe señales VFZ+S2+S3+Meta-Brain → llama tools MCP → decide acción.
Python ejecuta la decisión via Alpaca REST API.
Costo estimado: ~$0.04 por ciclo (claude-opus-4-5, ~2000 tokens).
"""

import os, json, logging
import anthropic

from signal_connector_alpaca import collect_signals, format_signals_for_claude
from trades_db_alpaca import log_claude_decision, get_today_pnl

log = logging.getLogger("claude_brain")

CLAUDE_MODEL = "claude-opus-4-5"

SYSTEM_PROMPT = """You are BARCO-Alpaca — an autonomous AI trading agent running on paper trading ($100,000 virtual).

You run every 15 minutes. You receive real-time intelligence from three production systems:
- VecFrachZ: quantum-validated crypto signals (IBM Quantum, 0% BER)
- S2 OrderBook CNN: buy pressure 0-1 per symbol (avg_pressure field)
- S3 NLP Sentiment: Fear & Greed index 0-100 + live headlines

These are aggregated into the Signal Gate score (-1 to +1).

YOUR JOB: Make ONE trading decision per cycle.

DECISION FRAMEWORK:
1. Check Signal Gate first — this is the GATE:
   - AVOID (score ≤ -0.25): HOLD. No exceptions.
   - NEUTRAL (-0.25 to +0.25): HOLD. Do NOT open new positions on weak signals.
   - OPTIMAL (score > +0.25): Trade. Pick the strongest signal and act.

   ⚠️ NEUTRAL = HOLD. Only enter new trades when Signal Gate is OPTIMAL.

2. When OPTIMAL — PRIMARY STRATEGY (Stocks & Crypto, fill instantly):
   - Strongest VecFrachZ crypto BUY → BUY_CRYPTO (BTC/USD, ETH/USD, SOL/USD) notional=2000
   - S2 avg_pressure > 0.65 + Fear & Greed > 55 → BUY_STOCK (NVDA, AAPL, AMZN) notional=2000
   - Call get_latest_quote first, then decide immediately — no more tools after that.

3. When OPTIMAL — SECONDARY STRATEGY (Options, only if options_buying_power > $10,000):
   - Call get_options_chain ONCE → pick best contract → return JSON immediately.
   - Do NOT call get_options_chain more than once per cycle.

RISK RULES (non-negotiable):
- NEUTRAL Signal Gate = HOLD always, no exceptions
- Max $2,000 notional per stock/crypto trade
- Max 5 open positions simultaneously
- NEVER buy a symbol you already hold — check "Currently holding" list first
- Options expiry: 3-10 days out ONLY
- Never trade if today P&L is below -$5,000
- CRITICAL: If options_buying_power < $10,000 → no options trades

RESPONSE FORMAT — always valid JSON, nothing else:

For stocks or crypto:
{
  "action": "BUY_STOCK" | "SELL_STOCK" | "BUY_CRYPTO" | "SELL_CRYPTO",
  "symbol": "NVDA",
  "notional": 2000.00,
  "reason": "S3=+0.85 NVDA earnings beat, S2=0.72 buy pressure, Meta-Brain OPTIMAL"
}

For options:
{
  "action": "BUY_CALL" | "BUY_PUT" | "SELL_COVERED_CALL" | "SELL_CSP",
  "symbol": "NVDA",
  "contract_symbol": "NVDA260905C00122000",
  "strike": 122.00,
  "expiry": "2026-09-05",
  "contracts": 1,
  "premium": 1.20,
  "reason": "Meta-Brain OPTIMAL, options_buying_power sufficient"
}

For hold:
{
  "action": "HOLD",
  "reason": "Meta-Brain AVOID or max positions reached"
}

IMPORTANT: Prefer stocks/crypto — they fill instantly. Options are secondary.
"""

ALPACA_MCP_TOOLS = [
    {
        "name": "get_account",
        "description": "Get current Alpaca account: cash, buying_power, portfolio_value",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_positions",
        "description": "List all open positions with symbol, qty, avg_entry_price, unrealized_pl",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_options_chain",
        "description": (
            "Get available option contracts for a symbol and expiration. "
            "Returns list with contract_symbol (OCC format), strike, type, open_interest. "
            "Use contract_symbol from this response in your final decision."
        ),
        "input_schema": {
            "type": "object",
            "required": ["symbol", "expiration_date"],
            "properties": {
                "symbol":          {"type": "string", "description": "underlying, e.g. NVDA, SPY"},
                "expiration_date": {"type": "string", "description": "YYYY-MM-DD, 3-10 days out"},
                "option_type":     {"type": "string", "enum": ["call", "put"]},
            },
        },
    },
    {
        "name": "get_latest_quote",
        "description": "Get current price for a stock or crypto symbol",
        "input_schema": {
            "type": "object",
            "required": ["symbol"],
            "properties": {"symbol": {"type": "string"}},
        },
    },
]


async def _handle_tool(tool_name: str, tool_input: dict, alpaca_client) -> str:
    try:
        if tool_name == "get_account":
            return json.dumps(await alpaca_client.get_account())

        elif tool_name == "get_positions":
            return json.dumps(await alpaca_client.get_positions())

        elif tool_name == "get_options_chain":
            symbol   = tool_input["symbol"]
            exp_date = tool_input["expiration_date"]
            opt_type = tool_input.get("option_type", "call")
            chain = await alpaca_client.get_options_chain(symbol, exp_date, opt_type)
            return json.dumps(chain[:20])  # max 20 para no inflar tokens

        elif tool_name == "get_latest_quote":
            symbol = tool_input["symbol"]
            price  = await alpaca_client.get_price(symbol)
            return json.dumps({"symbol": symbol, "price": price})

        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    except Exception as e:
        log.error(f"Tool {tool_name} error: {e}")
        return json.dumps({"error": str(e)})


def _parse_decision(text: str) -> dict:
    try:
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
    except json.JSONDecodeError:
        pass
    return {"action": "HOLD", "reason": f"Parse error: {text[:200]}"}


async def claude_decide(signals: dict, alpaca_client) -> dict:
    """Claude recibe señales → llama tools MCP → retorna decisión."""
    client    = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    today_pnl  = get_today_pnl()
    acc        = await alpaca_client.get_account()
    positions  = await alpaca_client.get_positions()
    options_bp = float(acc.get("options_buying_power", acc.get("buying_power", 0)))

    held_symbols = [p.get("symbol", "") for p in positions]
    held_str     = ", ".join(held_symbols) if held_symbols else "none"

    user_message = f"""
MARKET INTELLIGENCE — {signals['timestamp']}

{format_signals_for_claude(signals)}

ACCOUNT STATUS:
- Cash: ${float(acc.get('cash', 0)):,.2f}
- Buying power: ${float(acc.get('buying_power', 0)):,.2f}
- Options buying power: ${options_bp:,.2f}
- Open positions: {len(positions)} / 5 max
- Currently holding: {held_str}  ← DO NOT buy any of these symbols again
- Today P&L: ${today_pnl:+,.2f}

{"⛔ OPTIONS BUYING POWER TOO LOW — HOLD, do NOT call get_options_chain." if options_bp < 10000 else "Call get_options_chain for your target symbol, then respond with the decision JSON."}
"""

    messages = [{"role": "user", "content": user_message}]

    # Bucle agentico — Claude puede llamar tools hasta 8 rondas
    for round_n in range(8):
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=ALPACA_MCP_TOOLS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text"):
                    decision = _parse_decision(block.text)
                    log.info(f"Claude decision round={round_n+1}: {decision.get('action')} {decision.get('symbol','')}")
                    return decision
            return {"action": "HOLD", "reason": "No text block in response"}

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    log.info(f"Claude tool: {block.name}({json.dumps(block.input)[:80]})")
                    result = await _handle_tool(block.name, block.input, alpaca_client)
                    tool_results.append({
                        "type":        "tool_result",
                        "tool_use_id": block.id,
                        "content":     result,
                    })
            # Append assistant turn + tool results
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user",      "content": tool_results})
            continue

        break  # stop_reason desconocido

    return {"action": "HOLD", "reason": "Max rounds reached without decision"}


async def run_claude_cycle(alpaca_client, execute_fn) -> dict:
    """Ciclo completo: señales → decisión → ejecución → log en DB."""
    log.info("=== Claude Brain cycle start ===")

    signals  = await collect_signals()
    decision = await claude_decide(signals, alpaca_client)

    executed = False
    order_id = None

    if decision.get("action") != "HOLD":
        result   = await execute_fn(decision)
        executed = result.get("executed", False)
        order_id = result.get("order_id")

    log_claude_decision(decision, executed=executed, order_id=order_id)
    log.info(f"Brain cycle done: {decision['action']} executed={executed}")

    return {
        "decision":  decision,
        "executed":  executed,
        "order_id":  order_id,
        "timestamp": signals["timestamp"],
    }
