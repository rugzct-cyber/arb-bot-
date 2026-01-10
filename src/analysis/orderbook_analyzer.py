"""
Advanced Orderbook Analysis for HFT Arbitrage
Provides sophisticated analysis of orderbook depth, imbalance, and market microstructure
"""
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import time
from ..exchanges.base import Orderbook, PriceLevel


@dataclass
class SpreadOpportunity:
    """Represents an arbitrage opportunity with full analysis"""
    symbol: str
    buy_exchange: str
    sell_exchange: str
    buy_price: float
    sell_price: float
    spread_percent: float
    spread_bps: float
    
    # Advanced metrics
    buy_slippage_pct: float = 0.0
    sell_slippage_pct: float = 0.0
    net_spread_after_slippage: float = 0.0
    buy_ob_imbalance: float = 0.0
    sell_ob_imbalance: float = 0.0
    buy_available_liquidity: float = 0.0
    sell_available_liquidity: float = 0.0
    
    # Execution metrics
    recommended_size: float = 0.0
    max_profitable_size: float = 0.0
    expected_profit_usd: float = 0.0
    confidence_score: float = 0.0  # 0-1 based on analysis
    
    # Latency
    buy_latency_ms: float = 0.0
    sell_latency_ms: float = 0.0
    total_latency_ms: float = 0.0
    
    timestamp: int = 0
    
    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "buy_exchange": self.buy_exchange,
            "sell_exchange": self.sell_exchange,
            "buy_price": self.buy_price,
            "sell_price": self.sell_price,
            "spread_percent": round(self.spread_percent, 4),
            "spread_bps": round(self.spread_bps, 2),
            "net_spread_after_slippage": round(self.net_spread_after_slippage, 4),
            "buy_slippage_pct": round(self.buy_slippage_pct, 4),
            "sell_slippage_pct": round(self.sell_slippage_pct, 4),
            "buy_ob_imbalance": round(self.buy_ob_imbalance, 4),
            "sell_ob_imbalance": round(self.sell_ob_imbalance, 4),
            "buy_liquidity": round(self.buy_available_liquidity, 2),
            "sell_liquidity": round(self.sell_available_liquidity, 2),
            "recommended_size": round(self.recommended_size, 4),
            "max_profitable_size": round(self.max_profitable_size, 4),
            "expected_profit_usd": round(self.expected_profit_usd, 2),
            "confidence": round(self.confidence_score, 2),
            "latency_ms": round(self.total_latency_ms, 2),
            "timestamp": self.timestamp,
        }


class OrderbookAnalyzer:
    """Advanced orderbook analysis engine for HFT"""
    
    def __init__(self, default_trade_size: float = 1.0, fee_bps: float = 5.0):
        self.default_trade_size = default_trade_size
        self.fee_bps = fee_bps  # Combined fees in basis points
        self._price_history: dict[str, List[Tuple[int, float]]] = {}
    
    def analyze_spread(
        self, 
        ob_buy: Orderbook, 
        ob_sell: Orderbook,
        trade_size: Optional[float] = None
    ) -> Optional[SpreadOpportunity]:
        """
        Analyze spread opportunity between two orderbooks.
        Returns None if no profitable opportunity exists.
        
        ob_buy: orderbook where we BUY (use asks)
        ob_sell: orderbook where we SELL (use bids)
        """
        if not ob_buy.asks or not ob_sell.bids:
            return None
        
        size = trade_size or self.default_trade_size
        
        # Get prices
        buy_price = ob_buy.best_ask
        sell_price = ob_sell.best_bid
        
        if buy_price <= 0 or sell_price <= 0:
            return None
        
        # Calculate raw spread
        spread_pct = ((sell_price - buy_price) / buy_price) * 100
        spread_bps = spread_pct * 100
        
        # Estimate slippage for the trade size
        buy_slippage = ob_buy.estimate_buy_slippage(size)
        sell_slippage = ob_sell.estimate_sell_slippage(size)
        
        # Calculate net spread after slippage and fees
        total_slippage = buy_slippage + sell_slippage
        fee_pct = self.fee_bps / 100  # Convert bps to percent
        net_spread = spread_pct - total_slippage - fee_pct
        
        # Calculate recommended and max profitable trade size
        max_size = self._find_max_profitable_size(ob_buy, ob_sell)
        recommended_size = min(size, max_size * 0.5)  # Conservative: 50% of max
        
        # Calculate expected profit
        if net_spread > 0:
            expected_profit = (net_spread / 100) * recommended_size * buy_price
        else:
            expected_profit = 0.0
        
        # Calculate confidence score
        confidence = self._calculate_confidence(
            ob_buy, ob_sell, spread_pct, net_spread, max_size
        )
        
        # Total latency
        total_latency = ob_buy.latency_ms + ob_sell.latency_ms
        
        return SpreadOpportunity(
            symbol=ob_buy.symbol,
            buy_exchange=ob_buy.exchange,
            sell_exchange=ob_sell.exchange,
            buy_price=buy_price,
            sell_price=sell_price,
            spread_percent=spread_pct,
            spread_bps=spread_bps,
            buy_slippage_pct=buy_slippage,
            sell_slippage_pct=sell_slippage,
            net_spread_after_slippage=net_spread,
            buy_ob_imbalance=ob_buy.imbalance,
            sell_ob_imbalance=ob_sell.imbalance,
            buy_available_liquidity=ob_buy.ask_depth,
            sell_available_liquidity=ob_sell.bid_depth,
            recommended_size=recommended_size,
            max_profitable_size=max_size,
            expected_profit_usd=expected_profit,
            confidence_score=confidence,
            buy_latency_ms=ob_buy.latency_ms,
            sell_latency_ms=ob_sell.latency_ms,
            total_latency_ms=total_latency,
            timestamp=int(time.time() * 1000),
        )
    
    def find_best_opportunity(
        self,
        ob_a: Orderbook,
        ob_b: Orderbook,
        trade_size: Optional[float] = None
    ) -> Optional[SpreadOpportunity]:
        """
        Find best spread opportunity between two orderbooks.
        Checks both directions: A->B and B->A
        """
        # Direction 1: Buy on A, Sell on B
        opp_1 = self.analyze_spread(ob_a, ob_b, trade_size)
        
        # Direction 2: Buy on B, Sell on A  
        opp_2 = self.analyze_spread(ob_b, ob_a, trade_size)
        
        # Return the better opportunity
        if opp_1 is None and opp_2 is None:
            return None
        elif opp_1 is None:
            return opp_2
        elif opp_2 is None:
            return opp_1
        else:
            # Return the one with higher net spread
            return opp_1 if opp_1.net_spread_after_slippage > opp_2.net_spread_after_slippage else opp_2
    
    def _find_max_profitable_size(self, ob_buy: Orderbook, ob_sell: Orderbook) -> float:
        """Find the maximum size that remains profitable after slippage"""
        if not ob_buy.asks or not ob_sell.bids:
            return 0.0
        
        # Binary search for max profitable size
        min_size = 0.0
        max_size = min(ob_buy.ask_depth, ob_sell.bid_depth)
        
        if max_size <= 0:
            return 0.0
        
        fee_pct = self.fee_bps / 100
        
        for _ in range(10):  # 10 iterations of binary search
            mid_size = (min_size + max_size) / 2
            if mid_size <= 0:
                break
                
            buy_slippage = ob_buy.estimate_buy_slippage(mid_size)
            sell_slippage = ob_sell.estimate_sell_slippage(mid_size)
            
            buy_price = ob_buy.best_ask
            sell_price = ob_sell.best_bid
            spread_pct = ((sell_price - buy_price) / buy_price) * 100
            
            net_spread = spread_pct - buy_slippage - sell_slippage - fee_pct
            
            if net_spread > 0:
                min_size = mid_size  # Still profitable, try larger
            else:
                max_size = mid_size  # Not profitable, try smaller
        
        return min_size
    
    def _calculate_confidence(
        self,
        ob_buy: Orderbook,
        ob_sell: Orderbook,
        spread_pct: float,
        net_spread: float,
        max_size: float
    ) -> float:
        """Calculate confidence score (0-1) for the opportunity"""
        score = 0.0
        
        # Net spread contributes up to 40%
        if net_spread > 0.5:  # > 0.5% net spread
            score += 0.4
        elif net_spread > 0.2:
            score += 0.3
        elif net_spread > 0.1:
            score += 0.2
        elif net_spread > 0:
            score += 0.1
        
        # Liquidity contributes up to 30%
        if max_size > 10:
            score += 0.3
        elif max_size > 5:
            score += 0.2
        elif max_size > 1:
            score += 0.1
        
        # Low latency contributes up to 15%
        total_latency = ob_buy.latency_ms + ob_sell.latency_ms
        if total_latency < 100:
            score += 0.15
        elif total_latency < 200:
            score += 0.1
        elif total_latency < 500:
            score += 0.05
        
        # Orderbook health contributes up to 15%
        if len(ob_buy.asks) >= 5 and len(ob_sell.bids) >= 5:
            score += 0.1
        if abs(ob_buy.imbalance) < 0.5 and abs(ob_sell.imbalance) < 0.5:
            score += 0.05
        
        return min(score, 1.0)
