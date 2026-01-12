"""
Paradex Exchange Adapter
Zero-fee perpetuals on Starknet
API: https://api.prod.paradex.trade/v1
"""
import time
from typing import Optional, List, Callable
import aiohttp

from .base import (
    ExchangeAdapter,
    Orderbook,
    Order,
    Balance,
    Position,
    PriceLevel,
    LatencyStats,
)


class ParadexAdapter(ExchangeAdapter):
    """Paradex exchange adapter for HFT arbitrage"""
    
    BASE_URL = "https://api.prod.paradex.trade/v1"
    ORDERBOOK_URL = f"{BASE_URL}/orderbook"
    MARKETS_URL = f"{BASE_URL}/markets"
    
    def __init__(self, config: dict = None):
        self.config = config or {}
        self._session: Optional[aiohttp.ClientSession] = None
        self._markets_cache: dict = {}
        self.latency = LatencyStats()

    async def initialize(self) -> bool:
        """Initialize connection and fetch markets"""
        try:
            # Create session with optimized settings
            timeout = aiohttp.ClientTimeout(total=5)
            self._session = aiohttp.ClientSession(timeout=timeout)
            
            # Fetch markets to cache
            async with self._session.get(self.MARKETS_URL) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    markets = data.get("results", [])
                    
                    # Cache only PERP markets
                    for market in markets:
                        if market.get("asset_kind") == "PERP":
                            symbol = market.get("symbol", "")
                            self._markets_cache[symbol] = market
                    
                    print(f"✅ [paradex] Connected (HFT mode, {len(self._markets_cache)} PERP markets)")
                    return True
            return False
        except Exception as e:
            print(f"❌ [paradex] Init error: {e}")
            return False

    def _get_market_symbol(self, symbol: str) -> str:
        """Convert standard symbol to Paradex format
        ETH-USD -> ETH-USD-PERP
        """
        if symbol.endswith("-PERP"):
            return symbol
        return f"{symbol}-PERP"

    async def get_orderbook(self, symbol: str, depth: int = 10) -> Optional[Orderbook]:
        """Fetch orderbook with full depth from Paradex"""
        if not self._session:
            return None

        start_time = time.time()
        
        try:
            market_symbol = self._get_market_symbol(symbol)
            url = f"{self.ORDERBOOK_URL}/{market_symbol}"

            async with self._session.get(url) as resp:
                latency_ms = (time.time() - start_time) * 1000
                self.latency.record(latency_ms)
                
                if resp.status != 200:
                    print(f"⚠️ [paradex] Orderbook fetch failed: {resp.status}")
                    return None

                data = await resp.json()
                
                raw_bids = data.get("bids", [])
                raw_asks = data.get("asks", [])

                # Parse full depth - format: [[price, size], ...]
                bids = []
                for bid in raw_bids[:depth]:
                    if isinstance(bid, list) and len(bid) >= 2:
                        price = float(bid[0])
                        size = float(bid[1])
                        if price > 0 and size > 0:
                            bids.append(PriceLevel(price=price, size=size))
                
                asks = []
                for ask in raw_asks[:depth]:
                    if isinstance(ask, list) and len(ask) >= 2:
                        price = float(ask[0])
                        size = float(ask[1])
                        if price > 0 and size > 0:
                            asks.append(PriceLevel(price=price, size=size))

                # Sort: bids high to low, asks low to high
                bids.sort(key=lambda x: x.price, reverse=True)
                asks.sort(key=lambda x: x.price)

                timestamp = data.get("last_updated_at", int(time.time() * 1000))

                return Orderbook(
                    exchange="paradex",
                    symbol=symbol,
                    bids=bids,
                    asks=asks,
                    timestamp=timestamp,
                    latency_ms=latency_ms,
                )

        except Exception as e:
            print(f"❌ [paradex] Orderbook error: {e}")
            return None

    async def get_balance(self, asset: str = "USDC") -> Optional[Balance]:
        """Get balance - requires authentication"""
        # Not implemented for now - would need JWT auth
        return Balance(asset=asset, free=0.0, locked=0.0)

    async def place_order(self, order: Order) -> Optional[str]:
        """Place order - requires authentication"""
        # Not implemented - would need private key & signing
        print(f"⚠️ [paradex] Order placement not implemented (dry run mode)")
        return None

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """Cancel order - requires authentication"""
        # Not implemented
        return False

    async def get_positions(self, symbol: str = None) -> List[Position]:
        """Get positions - not implemented"""
        return []

    async def close(self) -> None:
        """Close connections"""
        if self._session:
            await self._session.close()
            self._session = None
