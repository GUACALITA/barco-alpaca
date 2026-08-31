"""
signal_connector_alpaca.py — Conecta señales de inteligencia con BARCO-Alpaca.

Lee VFZ, S2, S3 y Meta-Brain (servicios existentes en el VPS).
Mapea activos puros (BTC, ETH...) → símbolos Alpaca (BTC/USD, ETH/USD...).
No modifica VFZ, no toca BARCO-Binance. Solo renombra aquí.
"""

import httpx
import logging
from datetime import datetime

log = logging.getLogger("signal_connector")

# Mapeo VFZ (activos puros) → símbolos Alpaca
VFZ_TO_ALPACA = {
    "BTC":  "BTC/USD",
    "ETH":  "ETH/USD",
    "SOL":  "SOL/USD",
    "XRP":  "XRP/USD",
    "BNB":  "BNB/USD",
    "AVAX": "AVAX/USD",
    "DOGE": "DOGE/USD",
    "ADA":  "ADA/USD",
}

# URLs de servicios de inteligencia (compartidos con BARCO-Binance, no duplicar)
VFZ_URL    = "http://localhost:30818/signals?limit=50"
BRAIN_URL  = "http://localhost:30843/api/status"
S2_URL     = "http://localhost:30841/api/status"
S3_URL     = "http://localhost:30842/api/status"


async def collect_signals() -> dict:
    """
    Consulta todos los servicios de inteligencia del VPS y devuelve
    señales mapeadas a símbolos de Alpaca.
    """
    async with httpx.AsyncClient(timeout=5) as c:
        try:
            vfz_resp = await c.get(VFZ_URL)
            vfz_data = vfz_resp.json()
        except Exception as e:
            log.warning(f"VFZ unreachable: {e}")
            vfz_data = {}

        try:
            brain_resp = await c.get(BRAIN_URL)
            brain_data = brain_resp.json()
        except Exception as e:
            log.warning(f"Meta-Brain unreachable: {e}")
            brain_data = {}

        try:
            s2_resp = await c.get(S2_URL)
            s2_data  = s2_resp.json()
        except Exception as e:
            log.warning(f"S2 unreachable: {e}")
            s2_data = {}

        try:
            s3_resp = await c.get(S3_URL)
            s3_data  = s3_resp.json()
        except Exception as e:
            log.warning(f"S3 unreachable: {e}")
            s3_data = {}

    # Señales VFZ: {"BTC": "BUY", "SOL": "SELL", ...}
    # Las convertimos a símbolos Alpaca: {"BTC/USD": "BUY", "SOL/USD": "SELL", ...}
    raw_signals = {}
    for item in vfz_data.get("signals", []):
        asset  = item.get("symbol", "")        # "BTC", "ETH", etc.
        action = item.get("signal", "HOLD")     # "BUY" | "SELL" | "HOLD"
        if asset in VFZ_TO_ALPACA:
            alpaca_sym = VFZ_TO_ALPACA[asset]
            raw_signals[alpaca_sym] = action

    return {
        # Señales crypto en formato Alpaca
        "vfz_signals":   raw_signals,           # {"BTC/USD": "BUY", ...}

        # Meta-Brain
        "meta_brain":    brain_data.get("recommendation", "NEUTRAL"),  # AVOID|NEUTRAL|OPTIMAL
        "meta_score":    brain_data.get("score", 0.0),                 # -1.0 → +1.0

        # S2 OrderBook CNN — presión compradora 0-1 por símbolo
        "s2_pressure":   s2_data.get("pressure", {}),

        # S3 NLP Sentiment
        "s3_sentiment":  s3_data.get("fear_greed", 50),   # 0-100
        "s3_headlines":  s3_data.get("headlines", []),

        "timestamp": datetime.utcnow().isoformat(),
    }


def should_buy(symbol: str, signals: dict) -> bool:
    """
    Devuelve True si las señales permiten comprar `symbol`.

    Reglas:
    - Si Meta-Brain == AVOID → nunca comprar
    - Si VFZ dice SELL para el símbolo → no comprar
    - Resto → permitido
    """
    if signals.get("meta_brain") == "AVOID":
        return False

    # Para crypto: chequear señal VFZ directa
    vfz = signals.get("vfz_signals", {})
    if symbol in vfz and vfz[symbol] == "SELL":
        return False

    return True


def format_signals_for_claude(signals: dict) -> str:
    """Formatea las señales para el system prompt de Claude Brain."""
    vfz_lines = "\n".join(
        f"  {sym}: {action}"
        for sym, action in signals.get("vfz_signals", {}).items()
    )
    pressure_lines = "\n".join(
        f"  {sym}: {val:.2f}"
        for sym, val in signals.get("s2_pressure", {}).items()
    )
    headlines = "\n".join(
        f"  - {h}" for h in signals.get("s3_headlines", [])[:5]
    )

    return f"""VecFrachZ Crypto Signals (Alpaca symbols):
{vfz_lines or '  (no signals)'}

Meta-Brain: {signals.get('meta_brain', 'NEUTRAL')} (score: {signals.get('meta_score', 0):.2f})
Fear & Greed: {signals.get('s3_sentiment', 50)}/100

S2 OrderBook Pressure (0=bearish, 1=bullish):
{pressure_lines or '  (no data)'}

Recent Headlines:
{headlines or '  (none)'}
"""
