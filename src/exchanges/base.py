"""
Enhanced base classes for HFT arbitrage bot
Supports full orderbook depth with multiple price levels
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List, Callable
import time


@dataclass
class PriceLevel:
    """Single price level in the orderbook"""
    price: float
    size: float
    orders_count: int = 1
    
    @property
    def value(self) -> float:
        """Total value at this level"""
        return self.price * self.size


@dataclass
class Orderbook:
    """Full orderbook with depth levels"""
    exchange: str
    symbol: str
    bids: List[PriceLevel] = field(default_factory=list)  # Sorted high to low
    asks: List[PriceLevel] = field(default_factory=list)  # Sorted low to high
    timestamp: int = 0
    latency_ms: float = 0.0  # Time to receive this update
    
    @property
    def best_bid(self) -> float:
        """Best bid price (highest)"""
        return self.bids[0].price if self.bids else 0.0
    
    @property
    def best_ask(self) -> float:
        """Best ask price (lowest)"""
        return self.asks[0].price if self.asks else 0.0
    
    @property
    def best_bid_size(self) -> float:
        """Size at best bid"""
        return self.bids[0].size if self.bids else 0.0
    
    @property
    def best_ask_size(self) -> float:
        """Size at best ask"""
        return self.asks[0].size if self.asks else 0.0
    
    @property
    def mid_price(self) -> float:
        """Mid-market price"""
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2
        return 0.0
    
    @property
    def spread(self) -> float:
        """Absolute spread"""
        return self.best_ask - self.best_bid if self.best_bid and self.best_ask else 0.0
    
    @property
    def spread_bps(self) -> float:
        """Spread in basis points"""
        if self.mid_price:
            return (self.spread / self.mid_price) * 10000
        return 0.0
    
    @property
    def bid_depth(self) -> float:
        """Total bid liquidity"""
        return sum(level.size for level in self.bids)
    
    @property
    def ask_depth(self) -> float:
        """Total ask liquidity"""
        return sum(level.size for level in self.asks)
    
    @property
    def imbalance(self) -> float:
        """Order book imbalance: (bid_depth - ask_depth) / (bid_depth + ask_depth)
        Positive = more bids (bullish), Negative = more asks (bearish)
        Range: -1 to +1
        """
        total = self.bid_depth + self.ask_depth
        if total == 0:
            return 0.0
        return (self.bid_depth - self.ask_depth) / total
    
    def estimate_buy_slippage(self, size: float) -> float:
        """Estimate slippage for a buy order of given size
        Returns the average execution price vs best ask
        """
        if not self.asks or size <= 0:
            return 0.0
        
        remaining = size
        total_cost = 0.0
        
        for level in self.asks:
            if remaining <= 0:
                break
            fill_size = min(remaining, level.size)
            total_cost += fill_size * level.price
            remaining -= fill_size
        
        if remaining > 0:
            # Not enough liquidity - use last price
            total_cost += remaining * self.asks[-1].price
        
        avg_price = total_cost / size
        slippage_pct = ((avg_price - self.best_ask) / self.best_ask) * 100
        return slippage_pct
    
    def estimate_sell_slippage(self, size: float) -> float:
        """Estimate slippage for a sell order of given size
        Returns the average execution price vs best bid
        """
        if not self.bids or size <= 0:
            return 0.0
        
        remaining = size
        total_proceeds = 0.0
        
        for level in self.bids:
            if remaining <= 0:
                break
            fill_size = min(remaining, level.size)
            total_proceeds += fill_size * level.price
            remaining -= fill_size
        
        if remaining > 0:
            # Not enough liquidity - use last price
            total_proceeds += remaining * self.bids[-1].price
        
        avg_price = total_proceeds / size
        slippage_pct = ((self.best_bid - avg_price) / self.best_bid) * 100
        return slippage_pct
    
    def liquidity_weighted_mid(self, levels: int = 5) -> float:
        """Calculate liquidity-weighted mid price using top N levels"""
        bid_levels = self.bids[:levels]
        ask_levels = self.asks[:levels]
        
        if not bid_levels or not ask_levels:
            return self.mid_price
        
        bid_weighted = sum(l.price * l.size for l in bid_levels)
        bid_size = sum(l.size for l in bid_levels)
        
        ask_weighted = sum(l.price * l.size for l in ask_levels)
        ask_size = sum(l.size for l in ask_levels)
        
        if bid_size == 0 or ask_size == 0:
            return self.mid_price
        
        vwap_bid = bid_weighted / bid_size
        vwap_ask = ask_weighted / ask_size
        
        return (vwap_bid + vwap_ask) / 2
    
    def to_dict(self) -> dict:
        """Convert to dictionary for API/WebSocket"""
        return {
            "exchange": self.exchange,
            "symbol": self.symbol,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "best_bid_size": self.best_bid_size,
            "best_ask_size": self.best_ask_size,
            "mid_price": self.mid_price,
            "spread_bps": self.spread_bps,
            "imbalance": round(self.imbalance, 4),
            "bid_depth": self.bid_depth,
            "ask_depth": self.ask_depth,
            "latency_ms": self.latency_ms,
            "bids": [{"price": l.price, "size": l.size} for l in self.bids[:10]],
            "asks": [{"price": l.price, "size": l.size} for l in self.asks[:10]],
            "timestamp": self.timestamp,
        }


@dataclass
class Order:
    """Represents an order"""
    id: str
    exchange: str
    symbol: str
    side: str  # 'buy' or 'sell'
    size: float
    price: float
    status: str
    timestamp: int


@dataclass
class Balance:
    """Account balance"""
    exchange: str
    currency: str
    total: float
    available: float


@dataclass 
class LatencyStats:
    """Latency tracking for HFT"""
    last_update_ms: float = 0.0
    avg_latency_ms: float = 0.0
    min_latency_ms: float = float('inf')
    max_latency_ms: float = 0.0
    update_count: int = 0
    
    def record(self, latency_ms: float):
        """Record a latency measurement"""
        self.last_update_ms = latency_ms
        self.min_latency_ms = min(self.min_latency_ms, latency_ms)
        self.max_latency_ms = max(self.max_latency_ms, latency_ms)
        self.update_count += 1
        # Exponential moving average
        alpha = 0.1
        if self.avg_latency_ms == 0:
            self.avg_latency_ms = latency_ms
        else:
            self.avg_latency_ms = alpha * latency_ms + (1 - alpha) * self.avg_latency_ms
    
    def to_dict(self) -> dict:
        return {
            "last_ms": round(self.last_update_ms, 2),
            "avg_ms": round(self.avg_latency_ms, 2),
            "min_ms": round(self.min_latency_ms, 2) if self.min_latency_ms != float('inf') else 0,
            "max_ms": round(self.max_latency_ms, 2),
            "updates": self.update_count,
        }


class ExchangeAdapter(ABC):
    """Abstract base class for all exchange adapters with HFT support"""

    name: str = "unknown"
    
    def __init__(self):
        self.latency = LatencyStats()
        self._orderbook_callback: Optional[Callable[[Orderbook], None]] = None
        self._connected = False

    @abstractmethod
    async def initialize(self) -> bool:
        """Initialize the exchange connection"""
        pass

    @abstractmethod
    async def get_orderbook(self, symbol: str, depth: int = 10) -> Optional[Orderbook]:
        """Fetch current orderbook for a symbol with given depth"""
        pass

    @abstractmethod
    async def get_balance(self) -> Optional[Balance]:
        """Fetch account balance"""
        pass

    @abstractmethod
    async def place_order(
        self, symbol: str, side: str, size: float, price: float
    ) -> Optional[Order]:
        """Place an order"""
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        pass
    
    # WebSocket methods for HFT - optional implementation
    async def connect_websocket(self, symbol: str) -> bool:
        """Connect to WebSocket for real-time updates"""
        return False
    
    async def disconnect_websocket(self) -> None:
        """Disconnect WebSocket"""
        pass
    
    def set_orderbook_callback(self, callback: Callable[[Orderbook], None]) -> None:
        """Set callback for orderbook updates"""
        self._orderbook_callback = callback
    
    @property
    def is_websocket_connected(self) -> bool:
        """Check if WebSocket is connected"""
        return self._connected
