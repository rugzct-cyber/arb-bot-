"""
Extended Exchange Adapter with Full Orderbook Depth & WebSocket Support
HFT-ready with latency tracking and real-time streaming
"""
import time
import asyncio
import aiohttp
import json
from typing import Optional
from .base import ExchangeAdapter, Orderbook, Order, Balance, PriceLevel


class ExtendedAdapter(ExchangeAdapter):
    """HFT-ready adapter for Extended (x10) exchange"""

    name = "extended"
    MARKETS_URL = "https://api.starknet.extended.exchange/api/v1/info/markets"
    ORDERBOOK_URL = "https://api.starknet.extended.exchange/api/v1/orderbook"
    WS_URL = "wss://api.starknet.extended.exchange/ws/v1"

    def __init__(self, api_key: str = "", public_key: str = "", stark_key: str = ""):
        super().__init__()
        self.api_key = api_key
        self.public_key = public_key
        self.stark_key = stark_key
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._trading_client = None
        self._initialized = False
        self._markets_cache: list = []
        self._subscribed_symbols: set = set()
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

    async def connect_websocket(self, symbol: str) -> bool:
        """Connect to Extended WebSocket for real-time orderbook updates"""
        if not self._session:
            return False
        
        try:
            if not self._ws or self._ws.closed:
                self._ws = await self._session.ws_connect(self.WS_URL)
                self._connected = True
                print(f"✅ [extended] WebSocket connected")
                
                self._ws_task = asyncio.create_task(self._handle_ws_messages())
            
            # Subscribe to orderbook channel
            market_name = self._get_market_name(symbol)
            subscribe_msg = {
                "type": "subscribe",
                "channel": "orderbook",
                "market": market_name,
            }
            await self._ws.send_json(subscribe_msg)
            self._subscribed_symbols.add(symbol)
            print(f"✅ [extended] Subscribed to {symbol} orderbook")
            
            return True
            
        except Exception as e:
            print(f"❌ [extended] WebSocket error: {e}")
            self._connected = False
            return False

    async def _handle_ws_messages(self):
        """Handle incoming WebSocket messages"""
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    start_time = time.time()
                    data = json.loads(msg.data)
                    
                    if data.get("channel") == "orderbook":
                        await self._process_orderbook_update(data, start_time)
                        
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    print(f"❌ [extended] WS error: {self._ws.exception()}")
                    break
                    
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"❌ [extended] WS handler error: {e}")
        finally:
            self._connected = False

    async def _process_orderbook_update(self, data: dict, start_time: float):
        """Process orderbook update from WebSocket"""
        latency_ms = (time.time() - start_time) * 1000
        self.latency.record(latency_ms)
        
        market = data.get("market", "").replace("_", "-")
        symbol = market if market else "ETH-USD"
        
        ob_data = data.get("data", {})
        raw_bids = ob_data.get("bids", [])
        raw_asks = ob_data.get("asks", [])
        
        bids = []
        for b in raw_bids:
            if isinstance(b, list) and len(b) >= 2:
                bids.append(PriceLevel(price=float(b[0]), size=float(b[1])))
        
        asks = []
        for a in raw_asks:
            if isinstance(a, list) and len(a) >= 2:
                asks.append(PriceLevel(price=float(a[0]), size=float(a[1])))
        
        orderbook = Orderbook(
            exchange=self.name,
            symbol=symbol,
            bids=bids,
            asks=asks,
            timestamp=int(time.time() * 1000),
            latency_ms=latency_ms,
        )
        
        self._orderbooks[symbol] = orderbook
        
        if self._orderbook_callback:
            self._orderbook_callback(orderbook)

    async def disconnect_websocket(self) -> None:
        """Disconnect WebSocket"""
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        
        if self._ws and not self._ws.closed:
            await self._ws.close()
        
        self._connected = False
        self._subscribed_symbols.clear()
        print(f"✅ [extended] WebSocket disconnected")

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
        await self.disconnect_websocket()
        if self._session:
            await self._session.close()
