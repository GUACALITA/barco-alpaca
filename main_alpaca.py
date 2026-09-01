"""
main_alpaca.py — BARCO-Alpaca: agente principal en puerto 30850.

Tres loops en paralelo:
  Loop 1: Claude Brain (cada 60 min)  — MCP tools → decisión → ejecución opciones
  Loop 2: Monitor Python (cada 30s)   — stop-loss / take-profit sin Claude
  Loop 3: FastAPI Dashboard           — /health, /api/status, /api/log, /api/signals
"""

import os, asyncio, logging
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

from signal_connector_alpaca import collect_signals
from trades_db_alpaca import init_db, log_trade, get_today_pnl, get_recent_decisions
from claude_brain import run_claude_cycle
from alpaca_client_v2 import AlpacaClient  # mismo directorio en servidor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("barco_alpaca")

PORT                 = int(os.environ.get("PORT",                    30850))
CLAUDE_CYCLE_MINUTES = int(os.environ.get("CLAUDE_CYCLE_MINUTES",       60))
MONITOR_INTERVAL     = int(os.environ.get("MONITOR_INTERVAL_SECONDS",   30))
STOP_LOSS_PCT        = float(os.environ.get("STOP_LOSS_PCT",          -0.03))
TAKE_PROFIT_PCT      = float(os.environ.get("TAKE_PROFIT_PCT",          0.02))

alpaca = AlpacaClient(
    api_key    = os.environ.get("ALPACA_API_KEY",    ""),
    api_secret = os.environ.get("ALPACA_API_SECRET", ""),
    paper      = True,
)

state = {
    "running":       True,
    "last_decision": None,
    "last_cycle_at": None,
    "positions":     [],
    "start_at":      datetime.utcnow().isoformat(),
}


# ─── Ejecución de órdenes de opciones ────────────────────────────────────────

async def execute_decision(decision: dict) -> dict:
    """Ejecuta la decisión de Claude via Alpaca API (stocks, crypto u opciones)."""
    action = decision.get("action", "HOLD")
    symbol = decision.get("symbol", "")

    if action == "HOLD":
        return {"executed": False}

    acc = await alpaca.get_account()
    buying_power = float(acc.get("buying_power", 0))

    # ── Stocks y Crypto (market orders, llenan instantáneamente) ──────────────
    if action in ("BUY_STOCK", "SELL_STOCK", "BUY_CRYPTO", "SELL_CRYPTO"):
        notional = float(decision.get("notional", 2000))
        notional = min(notional, 2000)  # hard cap 2% de la cuenta

        if buying_power < notional:
            log.warning(f"execute_decision: buying_power ${buying_power:.0f} < ${notional:.0f}")
            return {"executed": False, "reason": f"buying_power too low: ${buying_power:.0f}"}

        side   = "buy" if action in ("BUY_STOCK", "BUY_CRYPTO") else "sell"
        result = await alpaca.place_order(symbol, side, notional)
        order_id = result.get("id")

        if order_id:
            log_trade(symbol=symbol, side=side, qty=notional / max(alpaca.get_cached_price(symbol) or 1, 0.01),
                      price=alpaca.get_cached_price(symbol) or 0,
                      order_id=order_id, trade_type="stock")

        log.info(f"execute_decision: {action} {symbol} ${notional:.0f} → order_id={order_id}")
        return {"executed": bool(order_id), "order_id": order_id, "order": result}

    # ── Opciones ───────────────────────────────────────────────────────────────
    contract_symbol = decision.get("contract_symbol")
    if not contract_symbol:
        log.error(f"Missing contract_symbol in options decision: {decision}")
        return {"executed": False, "error": "no contract_symbol"}

    qty     = int(decision.get("contracts", 1))
    premium = float(decision.get("premium", 0))

    if action in ("BUY_CALL", "BUY_PUT"):
        side = "buy"
    elif action in ("SELL_COVERED_CALL", "SELL_CSP"):
        side = "sell"
    else:
        return {"executed": False, "error": f"Unknown action: {action}"}

    options_bp = float(acc.get("options_buying_power", buying_power))
    if options_bp < 10000:
        log.warning(f"execute_decision: options_buying_power ${options_bp:.0f} < $10,000 — skipping")
        return {"executed": False, "reason": f"options buying_power too low: ${options_bp:.0f}"}

    result   = await alpaca.place_option_order(contract_symbol, side, qty, premium)
    order_id = result.get("id")

    if order_id:
        log_trade(symbol=contract_symbol, side=side, qty=qty,
                  price=premium, order_id=order_id, trade_type="option")

    log.info(f"execute_decision: {action} {contract_symbol} qty={qty} → order_id={order_id}")
    return {"executed": bool(order_id), "order_id": order_id, "order": result}


# ─── Loop 1: Claude Brain (cada 60 min) ──────────────────────────────────────

async def claude_brain_loop():
    log.info(f"Claude Brain loop started — every {CLAUDE_CYCLE_MINUTES} min")
    while state["running"]:
        try:
            result = await run_claude_cycle(alpaca, execute_decision)
            state["last_decision"] = result["decision"]
            state["last_cycle_at"] = result["timestamp"]
        except Exception as e:
            log.error(f"Brain cycle error: {e}")
        await asyncio.sleep(CLAUDE_CYCLE_MINUTES * 60)


# ─── Loop 2: Monitor Python (cada 30s, sin Claude) ───────────────────────────

async def monitor_loop():
    log.info(f"Monitor loop started — every {MONITOR_INTERVAL}s")
    while state["running"]:
        try:
            positions = await alpaca.get_positions()
            # Solo actualizar cache si la respuesta es válida (no vacía por error transitorio)
            acc_lmv = float((await alpaca.get_account()).get("long_market_value", 1))
            if positions or acc_lmv == 0:
                state["positions"] = positions

            for pos in positions:
                sym         = pos.get("symbol", "")
                asset_class = pos.get("asset_class", "")
                qty         = float(pos.get("qty", 0))
                avg_in      = float(pos.get("avg_entry_price", 0))
                if avg_in == 0 or qty == 0:
                    continue

                # Crypto positions come as "BTCUSD" — convert to "BTC/USD" for correct endpoint
                price_sym = sym
                if asset_class == "crypto" and "/" not in sym:
                    price_sym = sym[:-3] + "/" + sym[-3:]  # BTCUSD → BTC/USD

                current = await alpaca.get_price(price_sym)
                if not current:
                    continue

                pnl_pct    = (current - avg_in) / avg_in
                asset_class = pos.get("asset_class", "")

                if pnl_pct < STOP_LOSS_PCT:
                    log.warning(f"STOP-LOSS {sym}: {pnl_pct:.1%}")
                    if asset_class == "us_option":
                        r = await alpaca.place_option_order(sym, "sell", int(qty), current)
                    else:
                        # price_sym = BTC/USD → time_in_force=gtc; sell_qty evita 403 insufficient balance
                        r = await alpaca.place_order(price_sym, "sell", qty * current, current, sell_qty=qty)
                    log_trade(sym, "sell", qty, current,
                              order_id=r.get("id"),
                              pnl=(current - avg_in) * qty,
                              trade_type="stop_loss")

                elif pnl_pct > TAKE_PROFIT_PCT:
                    log.info(f"TAKE-PROFIT {sym}: {pnl_pct:.1%}")
                    if asset_class == "us_option":
                        r = await alpaca.place_option_order(sym, "sell", int(qty), current)
                    else:
                        # price_sym = BTC/USD → time_in_force=gtc; sell_qty evita 403 insufficient balance
                        r = await alpaca.place_order(price_sym, "sell", qty * current, current, sell_qty=qty)
                    log_trade(sym, "sell", qty, current,
                              order_id=r.get("id"),
                              pnl=(current - avg_in) * qty,
                              trade_type="take_profit")

        except Exception as e:
            log.error(f"Monitor error: {e}")
        await asyncio.sleep(MONITOR_INTERVAL)


# ─── FastAPI (Loop 3) ─────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    asyncio.create_task(claude_brain_loop())
    asyncio.create_task(monitor_loop())
    log.info(f"BARCO-Alpaca listening on :{PORT}")
    yield
    state["running"] = False


app = FastAPI(title="BARCO-Alpaca", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BARCO-Alpaca · Live Dashboard</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d1117;color:#e6edf3;font-family:'Inter','Segoe UI',sans-serif;min-height:100vh}
body::before{content:'';position:fixed;inset:0;background-image:linear-gradient(rgba(56,189,248,.03) 1px,transparent 1px),linear-gradient(90deg,rgba(56,189,248,.03) 1px,transparent 1px);background-size:48px 48px;pointer-events:none}
header{display:flex;align-items:center;justify-content:space-between;padding:20px 32px;border-bottom:1px solid rgba(255,255,255,.07);position:sticky;top:0;background:rgba(13,17,23,.95);backdrop-filter:blur(12px);z-index:10}
.logo{font-size:22px;font-weight:800;color:#fff}.logo em{color:#38bdf8;font-style:normal}
.live-badge{display:flex;align-items:center;gap:8px;font-size:12px;color:rgba(255,255,255,.4)}
.dot{width:8px;height:8px;border-radius:50%;background:#4ade80;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.header-right{display:flex;align-items:center;gap:20px}
.refresh-info{font-size:11px;color:rgba(255,255,255,.25)}
main{padding:24px 32px;max-width:1400px;margin:0 auto}
.top-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:24px}
.stat-card{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:12px;padding:20px 24px}
.stat-card.green{border-color:rgba(74,222,128,.3);background:rgba(74,222,128,.04)}
.stat-card.blue{border-color:rgba(56,189,248,.3);background:rgba(56,189,248,.04)}
.stat-label{font-size:11px;color:rgba(255,255,255,.35);text-transform:uppercase;letter-spacing:1.5px;margin-bottom:8px}
.stat-val{font-size:32px;font-weight:800;color:#fff}
.stat-val.green{color:#4ade80}.stat-val.blue{color:#38bdf8}.stat-val.red{color:#f87171}
.stat-sub{font-size:12px;color:rgba(255,255,255,.3);margin-top:4px}
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:24px}
.grid-3{display:grid;grid-template-columns:2fr 1fr 1fr;gap:16px;margin-bottom:24px}
.panel{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:12px;padding:24px}
.panel-title{font-size:11px;color:#38bdf8;text-transform:uppercase;letter-spacing:2px;font-weight:600;margin-bottom:18px;display:flex;align-items:center;gap:8px}
.pos-row{display:flex;align-items:center;justify-content:space-between;padding:12px 0;border-bottom:1px solid rgba(255,255,255,.06)}
.pos-row:last-child{border-bottom:none}
.pos-sym{font-weight:700;font-size:15px;color:#fff}
.pos-qty{font-size:12px;color:rgba(255,255,255,.35);margin-top:2px}
.pos-pnl{text-align:right}
.pnl-val{font-size:16px;font-weight:700}
.pnl-pct{font-size:12px;margin-top:2px}
.green-text{color:#4ade80}.red-text{color:#f87171}
.dec-item{padding:12px 16px;border:1px solid rgba(163,230,53,.15);border-radius:8px;background:rgba(163,230,53,.03)}
.dec-item:last-child{border-bottom:none}
.dec-header{display:flex;align-items:center;gap:10px;margin-bottom:6px}
.dec-action{font-size:12px;font-weight:700;padding:3px 10px;border-radius:20px}
.dec-action.buy{background:rgba(74,222,128,.15);color:#4ade80;border:1px solid rgba(74,222,128,.3)}
.dec-action.hold{background:rgba(255,255,255,.06);color:rgba(255,255,255,.4);border:1px solid rgba(255,255,255,.1)}
.dec-action.sell{background:rgba(248,113,113,.15);color:#f87171;border:1px solid rgba(248,113,113,.3)}
.dec-sym{font-size:13px;font-weight:600;color:#fff}
.dec-time{font-size:11px;color:rgba(163,230,53,.5);margin-left:auto;font-family:'Courier New',monospace}
.dec-reason{font-size:12px;color:#a3e635;line-height:1.5;font-family:'Courier New',monospace;opacity:.85}
.sig-row{display:flex;align-items:center;justify-content:space-between;padding:10px 0;border-bottom:1px solid rgba(255,255,255,.06)}
.sig-row:last-child{border-bottom:none}
.sig-name{font-size:13px;color:rgba(255,255,255,.6)}
.sig-val-chip{font-size:12px;font-weight:700;padding:3px 12px;border-radius:20px}
.buy-chip{background:rgba(74,222,128,.15);color:#4ade80;border:1px solid rgba(74,222,128,.3)}
.sell-chip{background:rgba(248,113,113,.15);color:#f87171;border:1px solid rgba(248,113,113,.3)}
.neutral-chip{background:rgba(255,255,255,.06);color:rgba(255,255,255,.4);border:1px solid rgba(255,255,255,.1)}
.meta-score{font-size:28px;font-weight:800;text-align:center;padding:16px 0}
.score-bar{height:6px;background:rgba(255,255,255,.08);border-radius:3px;margin:8px 0;overflow:hidden}
.score-fill{height:100%;border-radius:3px;background:linear-gradient(90deg,#38bdf8,#4ade80);transition:width .5s}
.empty-state{text-align:center;color:rgba(255,255,255,.2);padding:32px;font-size:14px}
</style>
</head>
<body>
<header>
  <div class="logo">BARCO<em>-Alpaca</em></div>
  <div class="live-badge"><div class="dot"></div>LIVE — Paper Trading</div>
  <div class="header-right">
    <div class="refresh-info">Auto-refresh every 15s</div>
    <div class="live-badge"><div class="dot" style="background:#38bdf8"></div><span id="clock">--:--:--</span></div>
  </div>
</header>

<main>
  <div class="top-stats">
    <div class="stat-card green">
      <div class="stat-label">Portfolio Value</div>
      <div class="stat-val green" id="portfolio">$--</div>
      <div class="stat-sub" id="portfolio-sub">Loading...</div>
    </div>
    <div class="stat-card blue">
      <div class="stat-label">Cash Available</div>
      <div class="stat-val blue" id="cash">$--</div>
      <div class="stat-sub" id="buying-power">Buying power: $--</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Open Positions</div>
      <div class="stat-val" id="pos-count">--</div>
      <div class="stat-sub">Max 5 simultaneous</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Today P&amp;L</div>
      <div class="stat-val" id="today-pnl">$0.00</div>
      <div class="stat-sub" id="last-cycle">Last cycle: --</div>
    </div>
  </div>

  <div class="grid-3">
    <div class="panel">
      <div class="panel-title">📊 Open Positions</div>
      <div id="positions"><div class="empty-state">No positions open</div></div>
    </div>
    <div class="panel">
      <div class="panel-title">🧠 Meta-Brain Score</div>
      <div class="meta-score" id="meta-score">--</div>
      <div class="score-bar"><div class="score-fill" id="score-fill" style="width:50%"></div></div>
      <div style="display:flex;justify-content:space-between;font-size:11px;color:rgba(255,255,255,.25);margin-top:4px"><span>AVOID</span><span>OPTIMAL</span></div>
      <div style="margin-top:16px">
        <div class="sig-row"><span class="sig-name">VecFrachZ BTC</span><span class="sig-val-chip neutral-chip" id="vfz-btc">--</span></div>
        <div class="sig-row"><span class="sig-name">VecFrachZ ETH</span><span class="sig-val-chip neutral-chip" id="vfz-eth">--</span></div>
        <div class="sig-row"><span class="sig-name">VecFrachZ SOL</span><span class="sig-val-chip neutral-chip" id="vfz-sol">--</span></div>
      </div>
    </div>
    <div class="panel">
      <div class="panel-title">⚡ Last Decision</div>
      <div id="last-decision"><div class="empty-state">Waiting...</div></div>
    </div>
  </div>

  <div class="panel" style="border-color:rgba(163,230,53,.2);background:rgba(163,230,53,.02)">
    <div class="panel-title" style="color:#a3e635;">📋 Decision Log</div>
    <div id="decision-log"><div class="empty-state">Loading decisions...</div></div>
  </div>
</main>

<script>
function fmt(n){return new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',minimumFractionDigits:2}).format(n)}
function fmtPct(n){return(n>=0?'+':'')+( n*100).toFixed(2)+'%'}
function cls(n){return n>=0?'green-text':'red-text'}
function chipCls(s){if(!s)return 'neutral-chip';s=s.toUpperCase();if(s==='BUY'||s.includes('BUY'))return 'buy-chip';if(s==='SELL'||s.includes('SELL'))return 'sell-chip';return 'neutral-chip'}
function actionCls(a){if(!a)return 'hold';if(a.includes('BUY'))return 'buy';if(a.includes('SELL'))return 'sell';return 'hold'}
function timeSince(ts){if(!ts)return '';const d=new Date(ts.replace(' ','T')+'Z');const s=Math.floor((Date.now()-d)/1000);if(s<60)return s+'s ago';if(s<3600)return Math.floor(s/60)+'m ago';return Math.floor(s/3600)+'h ago'}

async function refresh(){
  try{
    const [st,log,sig]=await Promise.all([
      fetch('/api/status').then(r=>r.json()),
      fetch('/api/log?limit=8').then(r=>r.json()),
      fetch('/api/signals').then(r=>r.json()).catch(()=>({}))
    ]);
    const acc=st.account||{};
    const pv=parseFloat(acc.portfolio_value||100000);
    const pnl=pv-100000;
    document.getElementById('portfolio').textContent=fmt(pv);
    document.getElementById('portfolio-sub').textContent=(pnl>=0?'+':'')+fmt(pnl)+' from start';
    document.getElementById('portfolio-sub').className='stat-sub '+(pnl>=0?'green-text':'red-text');
    document.getElementById('cash').textContent=fmt(parseFloat(acc.cash||0));
    document.getElementById('buying-power').textContent='Buying power: '+fmt(parseFloat(acc.buying_power||0));
    document.getElementById('pos-count').textContent=(st.positions_count||0)+' / 5';
    const todayPnl=st.today_pnl||0;
    const pnlEl=document.getElementById('today-pnl');
    pnlEl.textContent=(todayPnl>=0?'+':'')+fmt(todayPnl);
    pnlEl.className='stat-val '+(todayPnl>=0?'green':'red-text');
    const lc=st.last_cycle_at;
    document.getElementById('last-cycle').textContent='Last cycle: '+(lc?timeSince(lc):'--');

    // positions
    const pos=st.positions||[];
    if(pos.length===0){
      document.getElementById('positions').innerHTML='<div class="empty-state">No positions open</div>';
    } else {
      document.getElementById('positions').innerHTML=pos.map(p=>{
        const pl=parseFloat(p.unrealized_pl||0);
        const plp=parseFloat(p.unrealized_plpc||0);
        return '<div class="pos-row"><div><div class="pos-sym">'+p.symbol+'</div><div class="pos-qty">'+parseFloat(p.qty).toFixed(4)+' units @ '+fmt(parseFloat(p.avg_entry_price))+'</div></div><div class="pos-pnl"><div class="pnl-val '+cls(pl)+'">'+fmt(pl)+'</div><div class="pnl-pct '+cls(pl)+'">'+fmtPct(plp)+'</div></div></div>';
      }).join('');
    }

    // last decision
    const ld=st.last_decision;
    if(ld){
      document.getElementById('last-decision').innerHTML=
        '<div class="dec-header"><span class="dec-action '+actionCls(ld.action)+'">'+ld.action+'</span>'+(ld.symbol?'<span class="dec-sym">'+ld.symbol+'</span>':'')+'</div>'
        +(ld.notional?'<div style="font-size:13px;color:#38bdf8;margin-bottom:6px;">'+fmt(ld.notional)+'</div>':'')
        +'<div class="dec-reason">'+( ld.reason||'')+'</div>';
    }

    // decision log
    const decs=(log.decisions||[]);
    if(decs.length===0){
      document.getElementById('decision-log').innerHTML='<div class="empty-state">No decisions yet</div>';
    } else {
      document.getElementById('decision-log').innerHTML='<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:12px">'+decs.map(d=>
        '<div class="dec-item"><div class="dec-header"><span class="dec-action '+actionCls(d.action)+'">'+d.action+'</span>'+(d.symbol?'<span class="dec-sym" style="color:#fff;font-weight:700">'+d.symbol+'</span>':'')+'<span class="dec-time">'+timeSince(d.created_at)+'</span></div>'
        +'<div class="dec-reason">'+(d.reason||'').slice(0,130)+((d.reason||'').length>130?'…':'')+'</div></div>'
      ).join('')+'</div>';
    }

    // signals
    const vfz=sig.vfz_signals||{};
    ['BTC','ETH','SOL'].forEach(s=>{
      const el=document.getElementById('vfz-'+s.toLowerCase());
      if(!el)return;
      const v=vfz[s+'/USD']||'--';
      el.textContent=v;
      el.className='sig-val-chip '+chipCls(v);
    });
    const mb=parseFloat(sig.meta_score||0);
    document.getElementById('meta-score').textContent=mb>=0?'+'+mb.toFixed(2):mb.toFixed(2);
    document.getElementById('meta-score').style.color=mb>0.3?'#4ade80':mb<-0.3?'#f87171':'#38bdf8';
    document.getElementById('score-fill').style.width=Math.round((mb+1)/2*100)+'%';

  }catch(e){console.error(e)}
}

setInterval(()=>{
  const now=new Date();
  document.getElementById('clock').textContent=now.toTimeString().slice(0,8);
},1000);

refresh();
setInterval(refresh,15000);
</script>
</body>
</html>"""


@app.get("/health")
async def health():
    return {"status": "ok", "service": "barco-alpaca", "port": PORT,
            "uptime_since": state["start_at"]}


@app.get("/api/status")
async def api_status():
    acc = await alpaca.get_account()
    return {
        "account":       acc,
        "positions":     state["positions"],
        "positions_count": len(state["positions"]),
        "today_pnl":     get_today_pnl(),
        "last_decision": state["last_decision"],
        "last_cycle_at": state["last_cycle_at"],
        "uptime_since":  state["start_at"],
    }


@app.get("/api/log")
async def api_log(limit: int = 20):
    return {"decisions": get_recent_decisions(limit)}


@app.get("/api/signals")
async def api_signals():
    return await collect_signals()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main_alpaca:app", host="0.0.0.0", port=PORT, reload=False)
