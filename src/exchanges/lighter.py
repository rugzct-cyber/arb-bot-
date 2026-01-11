"""
Lighter Exchange Adapter with REST API
HFT-ready with latency tracking and connection pooling
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
    
    # Market ID mapping
    MARKET_IDS = {
        "ETH-USD": 0, "BTC-USD": 1, "SOL-USD": 2,
        "DOGE-USD": 3, "XRP-USD": 7, "LINK-USD": 8,
    }
    
    # Reverse mapping for symbol lookup
    ID_TO_SYMBOL = {v: k for k, v in MARKET_IDS.items()}

    def __init__(self, api_key: str = "", private_key: str = "", key_index: int = 0, account_index: int = 0):
        super().__init__()
        self.api_key = api_key
        self.private_key = private_key
        self.key_index = key_index  # API key index (0-254)
        self.account_index = account_index  # Lighter account index
        self._session: Optional[aiohttp.ClientSession] = None
        self._signer = None
        self._initialized = False
        self._orderbooks: dict[str, Orderbook] = {}

    async def initialize(self) -> bool:
        """Initialize the session and signer"""
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
            
            # Initialize signer if we have credentials
            if self.api_key and self.private_key:
                try:
                    from lighter.signer_client import SignerClient
                    
                    # Mainnet API URL
                    MAINNET_URL = "https://mainnet.zklighter.elliot.ai"
                    
                    self._signer = SignerClient(
                        url=MAINNET_URL,
                        account_index=self.account_index,
                        api_private_keys={self.key_index: self.private_key},
                    )
                    print(f"✅ [lighter] Signer initialized (account {self.account_index}, key index {self.key_index})")
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

    def get_cached_orderbook(self, symbol: str) -> Optional[Orderbook]:
        """Get cached orderbook (for low-latency access)"""
        return self._orderbooks.get(symbol)

    async def get_balance(self) -> Optional[Balance]:
        """Fetch balance from Lighter"""
        if not self._session or not self.account_index:
            return None

        try:
            url = f"{self.BASE_URL}/account?by=index&value={self.account_index}"
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
            from lighter.signer_client import SignerClient
            
            market_id = self.MARKET_IDS.get(symbol, 0)
            is_ask = side.lower() == "sell"  # is_ask=True for sell orders
            
            # Generate unique client order index
            client_order_index = int(time.time() * 1000) % 2147483647
            
            # Convert to SDK integer format:
            # - base_amount: 9 decimals (e.g., 0.001 ETH = 1000000)
            # - price: 6 decimals (e.g., $3000 = 3000000000)
            base_amount_int = int(size * 10**9)
            
            # Market order support: if price=0, use aggressive IOC order
            if price <= 0:
                # For market sell: use price 0 (will match any bid)
                # For market buy: use very high price (will match any ask)
                if is_ask:  # Selling
                    price_int = 1  # Minimum price to sell at market
                else:  # Buying
                    price_int = int(999999 * 10**6)  # Very high price to buy at market
                order_type = SignerClient.ORDER_TYPE_LIMIT
                time_in_force = SignerClient.ORDER_TIME_IN_FORCE_IMMEDIATE_OR_CANCEL
                print(f"📊 [lighter] Market order (IOC): {'SELL' if is_ask else 'BUY'} {size}")
            else:
                price_int = int(price * 10**6)
                order_type = SignerClient.ORDER_TYPE_LIMIT
                time_in_force = SignerClient.ORDER_TIME_IN_FORCE_GOOD_TILL_TIME
            
            result = await self._signer.create_order(
                market_index=market_id,
                client_order_index=client_order_index,
                base_amount=base_amount_int,
                price=price_int,
                is_ask=is_ask,
                order_type=order_type,
                time_in_force=time_in_force,
            )
            
            print(f"✅ [lighter] Order placed: {result}")
            
            # Extract order ID from result
            order_id = f"lighter_{client_order_index}"
            if result and len(result) > 0:
                if hasattr(result[0], 'order_status'):
                    order_id = str(result[0].order_status.order_id) if result[0].order_status else order_id
            
            return Order(
                id=order_id,
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
            import traceback
            traceback.print_exc()
            return None

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel order on Lighter"""
        if not self._signer:
            return False
            
        try:
            result = await self._signer.create_cancel_order(order_id=order_id)
            print(f"✅ [lighter] Order cancelled: {result}")
            return True
        except Exception as e:
            print(f"❌ [lighter] Cancel error: {e}")
            return False

    async def close(self):
        """Close all connections"""
        if self._session:
            await self._session.close()
