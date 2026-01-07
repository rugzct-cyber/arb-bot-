"""
Lighter Exchange Adapter with Full Orderbook Depth & WebSocket Support
HFT-ready with latency tracking and real-time streaming
"""
import time
import asyncio
import aiohttp
import json
from typing import Optional, Callable
from .base import ExchangeAdapter, Orderbook, Order, Balance, PriceLevel


class LighterAdapter(ExchangeAdapter):
    """HFT-ready adapter for Lighter exchange"""

    name = "lighter"
    BASE_URL = "https://mainnet.zklighter.elliot.ai/api/v1"
    WS_URL = "wss://mainnet.zklighter.elliot.ai/stream"
    
    # Market ID mapping
    MARKET_IDS = {
        "ETH-USD": 0, "BTC-USD": 1, "SOL-USD": 2,
        "DOGE-USD": 3, "XRP-USD": 7, "LINK-USD": 8,
    }
    
    # Reverse mapping for WebSocket
    ID_TO_SYMBOL = {v: k for k, v in MARKET_IDS.items()}

    def __init__(self, api_key: str = "", private_key: str = "", key_index: int = 0, wallet_address: str = ""):
        super().__init__()
        self.api_key = api_key
        self.private_key = private_key
        self.key_index = key_index
        self.wallet_address = wallet_address
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._signer = None
        self._initialized = False
        self._subscribed_symbols: set = set()
        self._orderbooks: dict[str, Orderbook] = {}

    async def initialize(self) -> bool:
        """Initialize the session and signer"""
        try:
            self._session = aiohttp.ClientSession()
            
            # Initialize signer if we have credentials
            if self.api_key and self.private_key:
                try:
                    from lighter.signer_client import SignerClient
                    from lighter.constants import MAINNET_API
                    
                    self._signer = SignerClient(
                        base_url=MAINNET_API,
                        api_private_keys={self.key_index: self.private_key},
                        account_index=self.key_index,
                    )
                    print(f"✅ [lighter] Signer initialized (key index {self.key_index})")
                except ImportError as e:
                    print(f"⚠️ [lighter] SDK not available: {e}")
                except Exception as e:
                    print(f"⚠️ [lighter] Signer init failed: {e}")
            
            # Test connection
            async with self._session.get(f"{self.BASE_URL}/orderBookOrders?market_id=0&limit=5") as resp:
                if resp.status == 200:
                    self._initialized = True
                    print(f"✅ [lighter] Connected (HFT mode)")
                    return True
            return False
        except Exception as e:
            print(f"❌ [lighter] Init error: {e}")
            return False

    async def get_orderbook(self, symbol: str, depth: int = 10) -> Optional[Orderbook]:
        """Fetch orderbook with full depth from Lighter"""
        if not self._session:
            return None

        start_time = time.time()
        
        try:
            market_id = self.MARKET_IDS.get(symbol, 0)
            url = f"{self.BASE_URL}/orderBookOrders?market_id={market_id}&limit={depth}"

            async with self._session.get(url) as resp:
                latency_ms = (time.time() - start_time) * 1000
                self.latency.record(latency_ms)
                
                if resp.status != 200:
                    return None

                data = await resp.json()
                raw_bids = data.get("bids", [])
                raw_asks = data.get("asks", [])

                if not raw_bids or not raw_asks:
                    return None

                # Parse full depth
                bids = []
                for bid in raw_bids[:depth]:
                    price = float(bid.get("price", 0))
                    size = float(bid.get("remaining_base_amount", 0))
                    if price > 0 and size > 0:
                        bids.append(PriceLevel(price=price, size=size))
                
                asks = []
                for ask in raw_asks[:depth]:
                    price = float(ask.get("price", 0))
                    size = float(ask.get("remaining_base_amount", 0))
                    if price > 0 and size > 0:
                        asks.append(PriceLevel(price=price, size=size))

                if not bids or not asks:
                    return None

                orderbook = Orderbook(
                    exchange=self.name,
                    symbol=symbol,
                    bids=bids,
                    asks=asks,
                    timestamp=int(time.time() * 1000),
                    latency_ms=latency_ms,
                )
                
                # Cache for quick access
                self._orderbooks[symbol] = orderbook
                
                return orderbook
                
        except Exception as e:
            print(f"❌ [lighter] Orderbook error: {e}")
            return None

    async def connect_websocket(self, symbol: str) -> bool:
        """Connect to Lighter WebSocket for real-time orderbook updates"""
        if not self._session:
            return False
        
        try:
            if not self._ws or self._ws.closed:
                self._ws = await self._session.ws_connect(self.WS_URL)
                self._connected = True
                print(f"✅ [lighter] WebSocket connected")
                
                # Start message handler
                self._ws_task = asyncio.create_task(self._handle_ws_messages())
            
            # Subscribe to orderbook channel
            market_id = self.MARKET_IDS.get(symbol, 0)
            subscribe_msg = {
                "type": "subscribe",
                "channel": "orderbook",
                "market_id": market_id,
            }
            await self._ws.send_json(subscribe_msg)
            self._subscribed_symbols.add(symbol)
            print(f"✅ [lighter] Subscribed to {symbol} orderbook")
            
            return True
            
        except Exception as e:
            print(f"❌ [lighter] WebSocket error: {e}")
            self._connected = False
            return False

    async def _handle_ws_messages(self):
        """Handle incoming WebSocket messages"""
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    start_time = time.time()
                    data = json.loads(msg.data)
                    
                    if data.get("type") == "orderbook_update":
                        await self._process_orderbook_update(data, start_time)
                        
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    print(f"❌ [lighter] WS error: {self._ws.exception()}")
                    break
                    
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"❌ [lighter] WS handler error: {e}")
        finally:
            self._connected = False

    async def _process_orderbook_update(self, data: dict, start_time: float):
        """Process orderbook update from WebSocket"""
        latency_ms = (time.time() - start_time) * 1000
        self.latency.record(latency_ms)
        
        market_id = data.get("market_id", 0)
        symbol = self.ID_TO_SYMBOL.get(market_id, "ETH-USD") 
        
        raw_bids = data.get("bids", [])
        raw_asks = data.get("asks", [])
        
        bids = [PriceLevel(price=float(b[0]), size=float(b[1])) for b in raw_bids if float(b[0]) > 0]
        asks = [PriceLevel(price=float(a[0]), size=float(a[1])) for a in raw_asks if float(a[0]) > 0]
        
        orderbook = Orderbook(
            exchange=self.name,
            symbol=symbol,
            bids=bids,
            asks=asks,
            timestamp=int(time.time() * 1000),
            latency_ms=latency_ms,
        )
        
        self._orderbooks[symbol] = orderbook
        
        # Trigger callback if set
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
        print(f"✅ [lighter] WebSocket disconnected")

    def get_cached_orderbook(self, symbol: str) -> Optional[Orderbook]:
        """Get cached orderbook (for low-latency access)"""
        return self._orderbooks.get(symbol)

    async def get_balance(self) -> Optional[Balance]:
        """Fetch balance from Lighter"""
        if not self._session or not self.api_key:
            return None

        try:
            url = f"{self.BASE_URL}/account?by=api_key&value={self.api_key}"
            async with self._session.get(url) as resp:
                if resp.status != 200:
                    return None

                data = await resp.json()
                accounts = data.get("accounts", [])
                
                if not accounts:
                    return None

                account = accounts[0]
                return Balance(
                    exchange=self.name,
                    currency="USD",
                    total=float(account.get("collateral", 0)),
                    available=float(account.get("available_balance", 0)),
                )
        except Exception as e:
            print(f"❌ [lighter] Balance error: {e}")
            return None

    async def place_order(
        self, symbol: str, side: str, size: float, price: float
    ) -> Optional[Order]:
        """Place order on Lighter using SDK signer"""
        
        if not self._signer:
            print(f"❌ [lighter] No signer available - configure API keys")
            return None
        
        try:
            market_id = self.MARKET_IDS.get(symbol, 0)
            is_buy = side.lower() == "buy"
            
            result = self._signer.create_order(
                market_index=market_id,
                price=str(price),
                amount=str(size),
                is_bid=is_buy,
                order_type="limit",
            )
            
            print(f"✅ [lighter] Order placed: {result}")
            
            return Order(
                id=str(result.get("order_id", f"lighter_{int(time.time()*1000)}")),
                exchange=self.name,
                symbol=symbol,
                side=side,
                size=size,
                price=price,
                status="submitted",
                timestamp=int(time.time() * 1000),
            )
        except Exception as e:
            print(f"❌ [lighter] Order error: {e}")
            return None

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel order on Lighter"""
        if not self._signer:
            return False
            
        try:
            result = self._signer.create_cancel_order(order_id=order_id)
            print(f"✅ [lighter] Order cancelled: {result}")
            return True
        except Exception as e:
            print(f"❌ [lighter] Cancel error: {e}")
            return False

    async def close(self):
        """Close all connections"""
        await self.disconnect_websocket()
        if self._session:
            await self._session.close()
