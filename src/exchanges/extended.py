"""
Extended Exchange Adapter with REST API
HFT-ready with latency tracking and connection pooling
"""
import time
import asyncio
import aiohttp
import json
from typing import Optional, List
from .base import ExchangeAdapter, Orderbook, Order, Balance, Position, PriceLevel


class ExtendedAdapter(ExchangeAdapter):
    """HFT-ready adapter for Extended (x10) exchange"""

    name = "extended"
    MARKETS_URL = "https://api.starknet.extended.exchange/api/v1/info/markets"
    ORDERBOOK_URL = "https://api.starknet.extended.exchange/api/v1/orderbook"

    def __init__(self, api_key: str = "", public_key: str = "", stark_key: str = ""):
        super().__init__()
        self.api_key = api_key
        self.public_key = public_key
        self.stark_key = stark_key
        self._session: Optional[aiohttp.ClientSession] = None
        self._trading_client = None
        self._initialized = False
        self._markets_cache: list = []
        self._orderbooks: dict[str, Orderbook] = {}

    async def initialize(self) -> bool:
        """Initialize the session and trading client"""
        try:
            # Connection pooling for HFT: keep-alive, limit connections per host
            connector = aiohttp.TCPConnector(
                limit=10,              # Max connections total
                limit_per_host=5,      # Max per single host
                keepalive_timeout=30,  # Keep connections alive for reuse
                enable_cleanup_closed=True,
                force_close=False,     # Reuse connections
            )
            self._session = aiohttp.ClientSession(connector=connector)
            
            # Initialize trading client if we have credentials
            if self.api_key and self.stark_key:
                try:
                    from x10.perpetual.accounts import StarkPerpetualAccount
                    from x10.perpetual.trading_client import PerpetualTradingClient
                    from x10.perpetual.configuration import MAINNET_CONFIG
                    
                    account = StarkPerpetualAccount(
                        api_key=self.api_key,
                        public_key=self.public_key,
                        private_key=self.stark_key,
                        vault=0,
                    )
                    
                    self._trading_client = PerpetualTradingClient(
                        endpoint_config=MAINNET_CONFIG,
                        stark_account=account,
                    )
                    print(f"✅ [extended] Trading client initialized")
                except ImportError as e:
                    print(f"⚠️ [extended] SDK not available: {e}")
                except Exception as e:
                    print(f"⚠️ [extended] Trading client init failed: {e}")
            
            # Test connection and cache markets
            async with self._session.get(self.MARKETS_URL) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self._markets_cache = data.get("data", [])
                    self._initialized = True
                    print(f"✅ [extended] Connected (HFT mode, {len(self._markets_cache)} markets)")
                    return True
            return False
        except Exception as e:
            print(f"❌ [extended] Init error: {e}")
            return False

    def _get_market_name(self, symbol: str) -> str:
        """Convert symbol to x10 market name format"""
        # ETH-USD -> ETH_USD
        return symbol.replace("-", "_")

    async def get_orderbook(self, symbol: str, depth: int = 10) -> Optional[Orderbook]:
        """Fetch orderbook with full depth from Extended"""
        if not self._session:
            return None

        start_time = time.time()
        
        try:
            market_name = self._get_market_name(symbol)
            url = f"{self.ORDERBOOK_URL}?market={market_name}&depth={depth}"

            async with self._session.get(url) as resp:
                latency_ms = (time.time() - start_time) * 1000
                self.latency.record(latency_ms)
                
                if resp.status != 200:
                    # Fallback to markets endpoint for best bid/ask
                    return await self._get_orderbook_from_markets(symbol, latency_ms)

                data = await resp.json()
                ob_data = data.get("data", data)
                
                raw_bids = ob_data.get("bids", [])
                raw_asks = ob_data.get("asks", [])

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

                if not bids or not asks:
                    return await self._get_orderbook_from_markets(symbol, latency_ms)

                orderbook = Orderbook(
                    exchange=self.name,
                    symbol=symbol,
                    bids=bids,
                    asks=asks,
                    timestamp=int(time.time() * 1000),
                    latency_ms=latency_ms,
                )
                
                self._orderbooks[symbol] = orderbook
                return orderbook
                
        except Exception as e:
            print(f"❌ [extended] Orderbook error: {e}")
            return None

    async def _get_orderbook_from_markets(self, symbol: str, latency_ms: float) -> Optional[Orderbook]:
        """Fallback: get best bid/ask from markets endpoint"""
        try:
            async with self._session.get(self.MARKETS_URL) as resp:
                if resp.status != 200:
                    return None

                data = await resp.json()
                markets = data.get("data", [])
                
                base_symbol = symbol.replace("-USD", "")
                market = None
                for m in markets:
                    asset = m.get("assetName", "")
                    name = m.get("name", "")
                    if asset == base_symbol or name == symbol or name == f"{base_symbol}-USD":
                        market = m
                        break
                
                if not market or market.get("status") != "ACTIVE":
                    return None
                
                stats = market.get("marketStats", {})
                best_bid = float(stats.get("bidPrice", 0) or 0)
                best_ask = float(stats.get("askPrice", 0) or 0)
                
                if best_bid <= 0 or best_ask <= 0:
                    return None

                # Create orderbook with only top level
                orderbook = Orderbook(
                    exchange=self.name,
                    symbol=symbol,
                    bids=[PriceLevel(price=best_bid, size=0)],
                    asks=[PriceLevel(price=best_ask, size=0)],
                    timestamp=int(time.time() * 1000),
                    latency_ms=latency_ms,
                )
                
                self._orderbooks[symbol] = orderbook
                return orderbook
                
        except Exception as e:
            return None

    def get_cached_orderbook(self, symbol: str) -> Optional[Orderbook]:
        """Get cached orderbook (for low-latency access)"""
        return self._orderbooks.get(symbol)

    async def get_balance(self) -> Optional[Balance]:
        """Fetch balance from Extended"""
        if not self._session or not self.api_key:
            return None

        try:
            headers = {"X-API-Key": self.api_key}
            url = "https://api.starknet.extended.exchange/api/v1/user/account"

            async with self._session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    return None

                data = await resp.json()
                account = data.get("data", data)

                return Balance(
                    exchange=self.name,
                    currency="USD",
                    total=float(account.get("equity", 0)),
                    available=float(account.get("availableForTrade", 0)),
                )
        except Exception as e:
            print(f"❌ [extended] Balance error: {e}")
            return None

    async def get_positions(self, symbol: str = None) -> List[Position]:
        """Fetch open positions from Extended"""
        if not self._session or not self.api_key:
            return []

        try:
            headers = {"X-API-Key": self.api_key}
            url = "https://api.starknet.extended.exchange/api/v1/user/positions"
            
            # Add market filter if symbol specified
            if symbol:
                market_name = self._get_market_name(symbol)
                url += f"?market={market_name}"

            async with self._session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    return []

                data = await resp.json()
                positions_data = data.get("data", [])
                
                positions = []
                for pos_data in positions_data:
                    size = float(pos_data.get("size", 0))
                    if size == 0:
                        continue
                    
                    # Convert market name back to symbol format
                    market = pos_data.get("market", "")
                    pos_symbol = market.replace("-", "-") if market else "ETH-USD"
                    
                    side = pos_data.get("side", "LONG").lower()
                    
                    positions.append(Position(
                        exchange=self.name,
                        symbol=pos_symbol,
                        side=side,
                        size=abs(size),
                        entry_price=float(pos_data.get("openPrice", 0)),
                        mark_price=float(pos_data.get("markPrice", 0)),
                        unrealized_pnl=float(pos_data.get("unrealisedPnl", 0)),
                        liquidation_price=float(pos_data.get("liquidationPrice", 0)),
                    ))
                
                return positions
                
        except Exception as e:
            print(f"❌ [extended] Positions error: {e}")
            return []

    async def place_order(
        self, symbol: str, side: str, size: float, price: float
    ) -> Optional[Order]:
        """Place order on Extended using SDK"""
        
        if not self._trading_client:
            print(f"❌ [extended] No trading client - configure API keys")
            return None
        
        try:
            from x10.perpetual.orders import OrderSide, OrderType
            
            order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
            
            from decimal import Decimal
            
            # Market order support: if price=0, use market order type
            if price <= 0:
                print(f"📊 [extended] Market order: {side.upper()} {size}")
                result = await self._trading_client.place_order(
                    market_name=symbol,
                    side=order_side,
                    amount_of_synthetic=Decimal(str(size)),
                    order_type=OrderType.MARKET,
                )
            else:
                result = await self._trading_client.place_order(
                    market_name=symbol,
                    side=order_side,
                    price=Decimal(str(price)),
                    amount_of_synthetic=Decimal(str(size)),
                )
            
            print(f"✅ [extended] Order placed: {result}")
            
            return Order(
                id=str(result.get("id", f"extended_{int(time.time()*1000)}")),
                exchange=self.name,
                symbol=symbol,
                side=side,
                size=size,
                price=price,
                status="submitted",
                timestamp=int(time.time() * 1000),
            )
        except Exception as e:
            print(f"❌ [extended] Order error: {e}")
            return None

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel order on Extended"""
        if not self._trading_client:
            return False
            
        try:
            result = await self._trading_client.cancel_order(order_id=order_id)
            print(f"✅ [extended] Order cancelled: {result}")
            return True
        except Exception as e:
            print(f"❌ [extended] Cancel error: {e}")
            return False

    async def close(self):
        """Close all connections"""
        if self._session:
            await self._session.close()
