"""
Vest Exchange Adapter
Perpetual futures on Vest.exchange
API: https://server-prod.hz.vestmarkets.com/v2
Docs: https://docs.vestmarkets.com/vest-api
"""
import asyncio
import time
from typing import Optional, List, Callable
import aiohttp

from .base import (
    ExchangeAdapter,
    Orderbook,
    Order,
    Balance,
    PriceLevel,
    LatencyStats,
)


class VestAdapter(ExchangeAdapter):
    """Vest exchange adapter for HFT arbitrage"""
    
    BASE_URL = "https://server-prod.hz.vestmarkets.com/v2"
    DEPTH_URL = f"{BASE_URL}/depth"
    EXCHANGE_INFO_URL = f"{BASE_URL}/exchangeInfo"
    TICKER_URL = f"{BASE_URL}/ticker/latest"
    
    def __init__(self, config: dict = None, account_group: int = 0):
        self.config = config or {}
        self.account_group = account_group
        self._session: Optional[aiohttp.ClientSession] = None
        self._markets_cache: dict = {}
        self.latency = LatencyStats()
        self._orderbook_callback: Optional[Callable[[Orderbook], None]] = None
        self._connected = False

    def _get_headers(self) -> dict:
        """Get required headers for Vest API"""
        return {
            "xrestservermm": f"restserver{self.account_group}",
            "Content-Type": "application/json",
        }

    async def initialize(self) -> bool:
        """Initialize connection and fetch markets"""
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                headers=self._get_headers(),
            )
            
            # Fetch exchange info to cache markets
            async with self._session.get(self.EXCHANGE_INFO_URL) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    symbols = data.get("symbols", [])
                    
                    for market in symbols:
                        symbol = market.get("symbol", "")
                        self._markets_cache[symbol] = market
                    
                    print(f"✅ [vest] Connected (HFT mode, {len(self._markets_cache)} markets)")
                    self._connected = True
                    return True
                else:
                    print(f"❌ [vest] Exchange info failed: {resp.status}")
            return False
        except Exception as e:
            print(f"❌ [vest] Init error: {e}")
            return False

    def _get_market_symbol(self, symbol: str) -> str:
        """Convert standard symbol to Vest format
        ETH-USD -> ETH-PERP
        """
        if symbol.endswith("-PERP"):
            return symbol
        # ETH-USD -> ETH-PERP
        base = symbol.split("-")[0]
        return f"{base}-PERP"

    async def get_orderbook(self, symbol: str, depth: int = 10) -> Optional[Orderbook]:
        """Fetch orderbook with full depth from Vest"""
        if not self._session:
            return None

        start_time = time.time()
        
        try:
            market_symbol = self._get_market_symbol(symbol)
            url = f"{self.DEPTH_URL}?symbol={market_symbol}&limit={depth}"

            async with self._session.get(url) as resp:
                latency_ms = (time.time() - start_time) * 1000
                self.latency.record(latency_ms)
                
                if resp.status != 200:
                    print(f"⚠️ [vest] Orderbook fetch failed: {resp.status}")
                    return None

                data = await resp.json()
                
                raw_bids = data.get("bids", [])
                raw_asks = data.get("asks", [])

                # Parse depth - format: [[price, size], ...] or strings
                bids = []
                for bid in raw_bids[:depth]:
                    try:
                        if isinstance(bid, list) and len(bid) >= 2:
                            price = float(bid[0])
                            size = float(bid[1])
                        elif isinstance(bid, dict):
                            price = float(bid.get("price", 0))
                            size = float(bid.get("size", 0))
                        else:
                            continue
                        if price > 0 and size > 0:
                            bids.append(PriceLevel(price=price, size=size))
                    except (ValueError, TypeError):
                        continue
                
                asks = []
                for ask in raw_asks[:depth]:
                    try:
                        if isinstance(ask, list) and len(ask) >= 2:
                            price = float(ask[0])
                            size = float(ask[1])
                        elif isinstance(ask, dict):
                            price = float(ask.get("price", 0))
                            size = float(ask.get("size", 0))
                        else:
                            continue
                        if price > 0 and size > 0:
                            asks.append(PriceLevel(price=price, size=size))
                    except (ValueError, TypeError):
                        continue

                # Sort: bids high to low, asks low to high
                bids.sort(key=lambda x: x.price, reverse=True)
                asks.sort(key=lambda x: x.price)

                timestamp = int(time.time() * 1000)

                return Orderbook(
                    exchange="vest",
                    symbol=symbol,
                    bids=bids,
                    asks=asks,
                    timestamp=timestamp,
                    latency_ms=latency_ms,
                )

        except Exception as e:
            print(f"❌ [vest] Orderbook error: {e}")
            return None

    async def get_balance(self, asset: str = "USDC") -> Optional[Balance]:
        """Get balance - requires authentication"""
        return Balance(asset=asset, free=0.0, locked=0.0)

    async def place_order(self, order: Order) -> Optional[str]:
        """Place order - requires authentication"""
        print(f"⚠️ [vest] Order placement not implemented (dry run mode)")
        return None

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """Cancel order - requires authentication"""
        return False

    async def connect_websocket(self, symbol: str) -> bool:
        """WebSocket not implemented - using REST for sync"""
        return False

    async def disconnect_websocket(self) -> None:
        """Disconnect WebSocket if connected"""
        pass

    def set_orderbook_callback(self, callback: Callable[[Orderbook], None]) -> None:
        """Set callback for orderbook updates"""
        self._orderbook_callback = callback

    async def close(self) -> None:
        """Close connections"""
        if self._session:
            await self._session.close()
            self._session = None
