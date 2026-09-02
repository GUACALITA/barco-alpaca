"""
signal_connector_alpaca.py — Connects intelligence signals to BARCO-Alpaca.

Reads VFZ, S2, S3 and Meta-Brain (existing VPS services).
Maps raw assets (BTC, ETH...) to Alpaca symbols (BTC/USD, ETH/USD...).
Does not modify VFZ or BARCO-Binance.
"""

import os
import httpx
import logging
from datetime import datetime

log = logging.getLogger("signal_connector")

# Raw asset → Alpaca symbol mapping
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

# Intelligence service URLs — configure in .env
VFZ_URL   = os.environ.get("VFZ_URL",        "http://localhost:30818") + "/signals?limit=50"
BRAIN_URL = os.environ.get("META_BRAIN_URL", "http://localhost:30843") + "/api/status"
S2_URL    = os.environ.get("S2_URL",         "http://localhost:30841") + "/api/status"
S3_URL    = os.environ.get("S3_URL",         "http://localhost:30842") + "/api/status"

OPTIMAL_THRESHOLD = 0.35
AVOID_THRESHOLD   = -0.25


def _local_meta(vfz_signals: dict, s2_data: dict, s3_data: dict,
                brain_inputs: dict = None) -> tuple:
    """
    Computes a local score from VFZ + S2 + S3 (excludes S1 Binance arbitrage).
    VecFrachZ is not considered by Meta-Brain — added here instead.

    Uses Meta-Brain avg_pressure when S2 ok=True (more stable reading).
    Returns (label: str, score: float)
    """
    # VFZ: BUY ratio vs total → -1..+1
    total = len(vfz_signals)
    buys  = sum(1 for v in vfz_signals.values() if v == "BUY")
    vfz_score = ((buys / total) * 2 - 1) if total else 0.0

    # S2: prefer Meta-Brain avg (more stable) when available
    brain_s2  = (brain_inputs or {}).get("s2", {})
    if brain_s2.get("ok") and brain_s2.get("avg_pressure") is not None:
        avg_p = brain_s2["avg_pressure"]
    else:
        avg_p = s2_data.get("avg_pressure")
    s2_score = (avg_p - 0.5) * 2 if avg_p is not None else 0.0

    # S3: Fear & Greed 0-100 → -1..+1  (real field: "fng_value")
    brain_s3  = (brain_inputs or {}).get("s3", {})
    fng       = (brain_s3.get("fng_value") if brain_s3.get("ok")
                 else s3_data.get("fng_value") or s3_data.get("fear_greed"))
    s3_score  = ((fng - 50) / 50) if fng is not None else 0.0

    # Weights: VFZ=30%, S2=40%, S3=30%  (S1 Binance arbitrage excluded)
    score = vfz_score * 0.30 + s2_score * 0.40 + s3_score * 0.30
    score = max(-1.0, min(1.0, score))

    if score >= OPTIMAL_THRESHOLD:
        label = "OPTIMAL"
    elif score <= AVOID_THRESHOLD:
        label = "AVOID"
    else:
        label = "NEUTRAL"

    return label, round(score, 3)


async def collect_signals() -> dict:
    """
    Queries all VPS intelligence services and returns signals
    mapped to Alpaca symbols.
    Computes local score when Meta-Brain cannot read S2/S3.
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

    # VFZ signals → Alpaca symbols
    raw_signals = {}
    for item in vfz_data.get("signals", []):
        asset  = item.get("symbol", "")
        action = item.get("signal", "HOLD")
        if asset in VFZ_TO_ALPACA:
            raw_signals[VFZ_TO_ALPACA[asset]] = action

    # S2 — real field names from the service
    s2_avg_pressure = s2_data.get("avg_pressure")                                   # 0.0–1.0
    s2_signal       = s2_data.get("last_signal", "NEUTRAL")                         # OPTIMAL|NEUTRAL
    s2_pressure_map = s2_data.get("pressure_by_symbol", s2_data.get("pressure", {}))

    # S3 — real field names from the service
    fng_value  = s3_data.get("fng_value") or s3_data.get("fear_greed", 50)
    headlines  = s3_data.get("last_headlines", s3_data.get("headlines", []))

    # Local score: VFZ + S2 + S3 (excludes S1/Binance arbitrage, includes VecFrachZ)
    brain_inputs = brain_data.get("inputs", {})
    local_label, local_score = _local_meta(raw_signals, s2_data, s3_data, brain_inputs)

    brain_label = brain_data.get("last_rec", brain_data.get("recommendation", "NEUTRAL"))
    brain_score = brain_data.get("last_score", brain_data.get("score", 0.0))

    # Use local score when OPTIMAL (VFZ adds signal that Meta-Brain does not consider)
    if local_label == "OPTIMAL" and len(raw_signals) > 0:
        meta_label = local_label
        meta_score = local_score
        log.info(
            f"VFZ+S2+S3 local score OPTIMAL ({local_score:.3f}) "
            f"→ overriding Meta-Brain NEUTRAL ({brain_score:.3f})"
        )
    else:
        meta_label = brain_label
        meta_score = brain_score

    return {
        "vfz_signals":      raw_signals,
        "meta_brain":       meta_label,
        "meta_score":       meta_score,
        "s2_pressure":      s2_pressure_map,
        "s2_avg_pressure":  s2_avg_pressure,
        "s2_signal":        s2_signal,
        "s3_sentiment":     fng_value,
        "s3_headlines":     headlines,
        "local_meta_label": local_label,
        "local_meta_score": local_score,
        "timestamp":        datetime.utcnow().isoformat(),
    }


def should_buy(symbol: str, signals: dict) -> bool:
    """
    Returns True if signals allow buying `symbol`.
    Blocks on AVOID or if VFZ signals SELL for the symbol.
    """
    if signals.get("meta_brain") == "AVOID":
        return False
    vfz = signals.get("vfz_signals", {})
    if symbol in vfz and vfz[symbol] == "SELL":
        return False
    return True


def format_signals_for_claude(signals: dict) -> str:
    """Formats signals for the Claude Brain user message."""
    vfz_lines = "\n".join(
        f"  {sym}: {action}"
        for sym, action in signals.get("vfz_signals", {}).items()
    )

    # S2: show avg_pressure and direct signal
    s2_avg = signals.get("s2_avg_pressure")
    s2_sig = signals.get("s2_signal", "NEUTRAL")
    if s2_avg is not None:
        s2_line = f"  avg_pressure: {s2_avg:.3f} → {s2_sig}"
        per_sym = "\n".join(
            f"    {sym}: {val:.3f}"
            for sym, val in signals.get("s2_pressure", {}).items()
        )
        if per_sym:
            s2_line += f"\n{per_sym}"
    else:
        s2_line = "  (no data)"

    # S3: Fear & Greed with label
    fng = signals.get("s3_sentiment", 50)
    if fng >= 75:
        fng_label = "EXTREME GREED"
    elif fng >= 55:
        fng_label = "GREED"
    elif fng >= 45:
        fng_label = "NEUTRAL"
    elif fng >= 25:
        fng_label = "FEAR"
    else:
        fng_label = "EXTREME FEAR"

    # Headlines: supports list of str or dicts
    headline_lines = "\n".join(
        f"  - {h.get('title', str(h)) if isinstance(h, dict) else h}"
        for h in signals.get("s3_headlines", [])[:5]
    )

    return f"""VecFrachZ Crypto Signals (IBM Quantum validated):
{vfz_lines or '  (no signals)'}

Signal Gate: {signals.get('meta_brain', 'NEUTRAL')} (score: {signals.get('meta_score', 0):.3f})

Fear & Greed Index: {fng}/100 ({fng_label})

S2 OrderBook CNN (0=bearish, 1=bullish):
{s2_line}

Recent Headlines:
{headline_lines or '  (none)'}
"""
