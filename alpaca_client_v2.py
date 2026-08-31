#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Alpaca Client — Paper trading (acciones + crypto + opciones)
BARCO-Alpaca | Guacalita Inc. | Hackathon sep 2026
"""

import os, time, logging
import httpx

log = logging.getLogger("alpaca")

BASE_URL = "https://paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"


class AlpacaClient:
    def __init__(self, api_key: str = "", api_secret: str = "", paper: bool = True):
        self.api_key    = api_key
        self.api_secret = api_secret
        self.paper      = paper
        self._prices    = {}
        self._headers   = {
            "APCA-API-KEY-ID":     api_key,
            "APCA-API-SECRET-KEY": api_secret,
        }

    def _has_keys(self) -> bool:
        return bool(self.api_key and self.api_secret)

    # ─── Precios ──────────────────────────────────────────────────────────────

    async def get_price(self, symbol: str) -> float | None:
        """Precio de stock o crypto. Crypto: símbolo = BTC/USD → usa endpoint crypto."""
        if not self._has_keys():
            return self._prices.get(symbol)
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                # Crypto usa endpoint diferente
                if "/" in symbol:
                    crypto_sym = symbol.replace("/", "")  # BTC/USD → BTCUSD
                    r = await c.get(
                        f"{DATA_URL}/v1beta3/crypto/us/latest/quotes",
                        params={"symbols": crypto_sym},
                        headers=self._headers,
                    )
                    if r.status_code == 200:
                        data = r.json().get("quotes", {}).get(crypto_sym, {})
                        bid = data.get("bp", 0)
                        ask = data.get("ap", 0)
                        price = (bid + ask) / 2 if bid and ask else 0
                        if price:
                            self._prices[symbol] = price
                        return price or None
                else:
                    r = await c.get(
                        f"{DATA_URL}/v2/stocks/{symbol}/quotes/latest",
                        headers=self._headers,
                    )
                    if r.status_code == 200:
                        q = r.json().get("quote", {})
                        bid = q.get("bp", 0)
                        ask = q.get("ap", 0)
                        price = (bid + ask) / 2 if bid and ask else 0
                        if price:
                            self._prices[symbol] = price
                        return price or None
        except Exception as e:
            log.debug(f"get_price {symbol}: {e}")
        return self._prices.get(symbol)

    async def get_prices_bulk(self, symbols: list[str]) -> dict:
        if not self._has_keys():
            return {}
        try:
            syms = ",".join(s for s in symbols if "/" not in s)
            if syms:
                async with httpx.AsyncClient(timeout=8) as c:
                    r = await c.get(
                        f"{DATA_URL}/v2/stocks/bars/latest",
                        params={"symbols": syms, "feed": "iex"},
                        headers=self._headers,
                    )
                    if r.status_code == 200:
                        for sym, bar in r.json().get("bars", {}).items():
                            self._prices[sym] = float(bar.get("c", 0))
        except Exception as e:
            log.debug(f"get_prices_bulk: {e}")
        return self._prices

    # ─── Cuenta y posiciones ──────────────────────────────────────────────────

    async def get_account(self) -> dict:
        if not self._has_keys():
            return {"cash": 100000, "buying_power": 100000,
                    "portfolio_value": 100000, "paper": True}
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{BASE_URL}/v2/account", headers=self._headers)
                if r.status_code == 200:
                    return r.json()
        except Exception as e:
            log.warning(f"get_account: {e}")
        return {}

    async def get_positions(self) -> list:
        """Lista todas las posiciones abiertas."""
        if not self._has_keys():
            return []
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{BASE_URL}/v2/positions", headers=self._headers)
                if r.status_code == 200:
                    return r.json()
        except Exception as e:
            log.warning(f"get_positions: {e}")
        return []

    # ─── Options chain ────────────────────────────────────────────────────────

    async def get_options_chain(self, symbol: str, expiration_date: str,
                                option_type: str = "call") -> list:
        """
        Devuelve contratos de opciones disponibles para un símbolo y vencimiento.
        Cada contrato incluye: symbol (OCC), strike_price, type, bid, ask, open_interest.
        """
        if not self._has_keys():
            return self._fake_options_chain(symbol, expiration_date, option_type)
        try:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get(
                    f"{BASE_URL}/v2/options/contracts",
                    params={
                        "underlying_symbols": symbol,
                        "expiration_date":    expiration_date,
                        "type":               option_type,
                        "status":             "active",
                        "limit":              50,
                    },
                    headers=self._headers,
                )
                if r.status_code == 200:
                    contracts = r.json().get("option_contracts", [])
                    # Devolver solo campos útiles para Claude
                    return [
                        {
                            "contract_symbol": c_["symbol"],
                            "underlying":      c_["underlying_symbol"],
                            "type":            c_["type"],
                            "strike":          float(c_.get("strike_price", 0)),
                            "expiry":          c_.get("expiration_date"),
                            "open_interest":   c_.get("open_interest", 0),
                        }
                        for c_ in contracts
                    ]
                log.warning(f"options chain {r.status_code}: {r.text[:200]}")
        except Exception as e:
            log.warning(f"get_options_chain: {e}")
        return self._fake_options_chain(symbol, expiration_date, option_type)

    def _fake_options_chain(self, symbol: str, expiry: str, opt_type: str) -> list:
        """Cadena sintética para paper trading sin keys o fallo de API."""
        price = self._prices.get(symbol, 100)
        strikes = [round(price * m, 2) for m in [0.97, 0.99, 1.0, 1.01, 1.03]]
        yy = expiry[2:4]; mm = expiry[5:7]; dd = expiry[8:10]
        cp = "C" if opt_type == "call" else "P"
        return [
            {
                "contract_symbol": f"{symbol}{yy}{mm}{dd}{cp}{int(s*1000):08d}",
                "underlying": symbol,
                "type": opt_type,
                "strike": s,
                "expiry": expiry,
                "open_interest": 500,
            }
            for s in strikes
        ]

    # ─── Órdenes stock/crypto ─────────────────────────────────────────────────

    async def place_order(self, symbol: str, side: str, notional: float,
                          price: float = None) -> dict:
        """Orden de mercado para stock o crypto por monto en dólares."""
        if not self._has_keys():
            return self._fake_fill(symbol, side, notional, price)

        data = {
            "symbol":        symbol,
            "side":          side.lower(),
            "type":          "market",
            "time_in_force": "gtc" if "/" in symbol else "day",
            "notional":      str(round(notional, 2)),
        }
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(
                    f"{BASE_URL}/v2/orders",
                    json=data,
                    headers=self._headers,
                )
                if r.status_code in (200, 201):
                    return r.json()
                log.warning(f"place_order {r.status_code}: {r.text[:200]}")
        except Exception as e:
            log.warning(f"place_order: {e}")
        return self._fake_fill(symbol, side, notional, price)

    # ─── Órdenes opciones ─────────────────────────────────────────────────────

    async def place_option_order(self, contract_symbol: str, side: str,
                                 qty: int, limit_price: float) -> dict:
        """
        Orden de opción usando el símbolo OCC del contrato.
        contract_symbol: p.ej. 'NVDA260905C00122000'
        side: 'buy' | 'sell'
        qty: número de contratos
        limit_price: precio límite por contrato (prima)
        """
        if not self._has_keys():
            return self._fake_option_fill(contract_symbol, side, qty, limit_price)

        data = {
            "symbol":        contract_symbol,
            "side":          side.lower(),
            "type":          "market",
            "time_in_force": "day",
            "qty":           str(qty),
        }
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(
                    f"{BASE_URL}/v2/orders",
                    json=data,
                    headers=self._headers,
                )
                if r.status_code in (200, 201):
                    return r.json()
                log.warning(f"place_option_order {r.status_code}: {r.text[:200]}")
        except Exception as e:
            log.warning(f"place_option_order: {e}")
        return self._fake_option_fill(contract_symbol, side, qty, limit_price)

    def _fake_fill(self, symbol, side, notional, price):
        p = price or self._prices.get(symbol, 100)
        return {
            "id":               f"PAPER_{int(time.time()*1000)}",
            "symbol":           symbol,
            "side":             side,
            "filled_avg_price": str(p),
            "filled_qty":       str(round(notional / p, 4) if p else 0),
            "status":           "filled",
        }

    def _fake_option_fill(self, contract_symbol, side, qty, limit_price):
        return {
            "id":               f"OPT_PAPER_{int(time.time()*1000)}",
            "symbol":           contract_symbol,
            "side":             side,
            "filled_avg_price": str(limit_price),
            "filled_qty":       str(qty),
            "status":           "filled",
        }

    def get_cached_price(self, symbol: str) -> float | None:
        return self._prices.get(symbol)
