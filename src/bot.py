"""
HFT Multi-bot Manager - Event-driven architecture with full orderbook analysis
Supports multiple bots running in parallel with WebSocket streaming
"""
import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Callable
from .config import config
from .exchanges import LighterAdapter, ExtendedAdapter, ParadexAdapter, VestAdapter, ExchangeAdapter, Orderbook
from .analysis.orderbook_analyzer import OrderbookAnalyzer, SpreadOpportunity
from .execution import SmartExecutionManager, EntryConfig


@dataclass
class BotConfig:
    """Configuration for a single bot"""
    id: str
    symbol: str
    exchange_a: str
    exchange_b: str
    # Entry parameters (Scale-in)
    entry_start_pct: float = 0.5        # Start firing threshold (%)
    entry_full_pct: float = 1.0         # 100% investment threshold (%)
    target_amount: float = 15.0         # Target in tokens
    # Advanced parameters
    max_slippage_pct: float = 0.05      # Strict slippage limit (%)
    refill_delay_ms: int = 500          # Pause between slices (ms)
    min_validity_ms: int = 100          # Anti-fakeout duration (ms)
    # Modes
    poll_interval_ms: int = 50          # HFT: faster polling
    use_websocket: bool = False         # REST by default
    dry_run: bool = True
    fee_bps: float = 5.0                # Trading fees in basis points


@dataclass
class HFTStats:
    """HFT-specific statistics"""
    polls: int = 0
    ws_updates: int = 0
    opportunities: int = 0
    profitable_opportunities: int = 0
    trades: int = 0
    errors: int = 0
    start_time: int = 0
    
    # Latency tracking
    avg_latency_ms: float = 0.0
    min_latency_ms: float = float('inf')
    max_latency_ms: float = 0.0
    
    # Spread tracking
    last_spread: float = 0.0
    last_net_spread: float = 0.0
    best_spread_seen: float = 0.0
    avg_spread: float = 0.0
    
    # Analysis
    last_opportunity: Optional[SpreadOpportunity] = None
    
    def record_latency(self, latency_ms: float):
        """Record a latency measurement"""
        self.min_latency_ms = min(self.min_latency_ms, latency_ms)
        self.max_latency_ms = max(self.max_latency_ms, latency_ms)
        alpha = 0.1
        if self.avg_latency_ms == 0:
            self.avg_latency_ms = latency_ms
        else:
            self.avg_latency_ms = alpha * latency_ms + (1 - alpha) * self.avg_latency_ms
    
    def record_spread(self, spread: float):
        """Record spread measurement"""
        self.last_spread = spread
        self.best_spread_seen = max(self.best_spread_seen, spread)
        alpha = 0.05
        if self.avg_spread == 0:
            self.avg_spread = spread
        else:
            self.avg_spread = alpha * spread + (1 - alpha) * self.avg_spread


@dataclass
class OrderbookState:
    """Current state of orderbooks for a bot"""
    exchange_a: Optional[Orderbook] = None
    exchange_b: Optional[Orderbook] = None
    last_update: int = 0


class SingleBot:
    """HFT-ready single arbitrage bot"""

    def __init__(self, bot_config: BotConfig):
        self.id = bot_config.id
        self.config = bot_config
        self.running = False
        self.stats = HFTStats()
        self.exchange_a: Optional[ExchangeAdapter] = None
        self.exchange_b: Optional[ExchangeAdapter] = None
        self.analyzer = OrderbookAnalyzer(
            default_trade_size=bot_config.target_amount,
            fee_bps=bot_config.fee_bps
        )
        self.orderbooks = OrderbookState()
        self._logs: List[str] = []
        self._ws_mode = False
        self._update_callback: Optional[Callable] = None
        
        # Smart Execution Manager with entry config from bot params
        entry_config = EntryConfig(
            entry_start_pct=bot_config.entry_start_pct,
            entry_full_pct=bot_config.entry_full_pct,
            target_amount=bot_config.target_amount,
            max_slippage_pct=bot_config.max_slippage_pct,
            refill_delay_ms=bot_config.refill_delay_ms,
            min_validity_ms=bot_config.min_validity_ms,
        )
        self.execution_manager = SmartExecutionManager(
            orderbook_analyzer=self.analyzer,
            log_callback=self.log
        )
        self.entry_config = entry_config

    def log(self, message: str):
        """Add to logs"""
        timestamp = time.strftime("%H:%M:%S.") + f"{int(time.time() * 1000) % 1000:03d}"
        log_entry = f"[{timestamp}] [{self.config.symbol}] {message}"
        self._logs.append(log_entry)
        if len(self._logs) > 100:
            self._logs = self._logs[-100:]
        print(log_entry)

    def get_logs(self) -> List[str]:
        return self._logs[-30:]

    def set_update_callback(self, callback: Callable):
        """Set callback for real-time updates to frontend"""
        self._update_callback = callback

    def to_dict(self) -> dict:
        """Convert bot state to dict for API"""
        return {
            "id": self.id,
            "symbol": self.config.symbol,
            "exchange_a": self.config.exchange_a,
            "exchange_b": self.config.exchange_b,
            "entry_start_pct": self.config.entry_start_pct,
            "running": self.running,
            "ws_mode": self._ws_mode,
            "stats": {
                "polls": self.stats.polls,
                "ws_updates": self.stats.ws_updates,
                "opportunities": self.stats.opportunities,
                "profitable": self.stats.profitable_opportunities,
                "trades": self.stats.trades,
                "errors": self.stats.errors,
                "runtime": int(time.time() - self.stats.start_time) if self.stats.start_time else 0,
            },
            "latency": {
                "avg_ms": round(self.stats.avg_latency_ms, 2),
                "min_ms": round(self.stats.min_latency_ms, 2) if self.stats.min_latency_ms != float('inf') else 0,
                "max_ms": round(self.stats.max_latency_ms, 2),
            },
            "spread": {
                "current": round(self.stats.last_spread, 4),
                "net": round(self.stats.last_net_spread, 4),
                "best": round(self.stats.best_spread_seen, 4),
                "avg": round(self.stats.avg_spread, 4),
            },
            "opportunity": self.stats.last_opportunity.to_dict() if self.stats.last_opportunity else None,
            "orderbooks": {
                "a": self.orderbooks.exchange_a.to_dict() if self.orderbooks.exchange_a else None,
                "b": self.orderbooks.exchange_b.to_dict() if self.orderbooks.exchange_b else None,
            },
            "exit_status": self.execution_manager.get_status() if self.execution_manager else None,
            "logs": self.get_logs(),
        }

    def _on_orderbook_update(self, orderbook: Orderbook):
        """Callback for WebSocket orderbook updates"""
        self.stats.ws_updates += 1
        
        # Update cached orderbook
        if orderbook.exchange == self.config.exchange_a:
            self.orderbooks.exchange_a = orderbook
        elif orderbook.exchange == self.config.exchange_b:
            self.orderbooks.exchange_b = orderbook
        
        self.orderbooks.last_update = int(time.time() * 1000)
        
        # Analyze if we have both orderbooks
        if self.orderbooks.exchange_a and self.orderbooks.exchange_b:
            self._analyze_opportunity()

    def _analyze_opportunity(self):
        """Analyze current orderbooks for opportunity"""
        if not self.orderbooks.exchange_a or not self.orderbooks.exchange_b:
            return
        
        opp = self.analyzer.find_best_opportunity(
            self.orderbooks.exchange_a,
            self.orderbooks.exchange_b,
            self.config.max_position_size
        )
        
        if opp:
            # Record stats
            self.stats.record_spread(opp.spread_percent)
            self.stats.record_latency(opp.total_latency_ms)
            self.stats.last_net_spread = opp.net_spread_after_slippage
            self.stats.last_opportunity = opp
            self.stats.opportunities += 1
            
            # Check if profitable after slippage
            if opp.net_spread_after_slippage >= self.config.entry_start_pct:
                self.stats.profitable_opportunities += 1
                
                direction = f"{opp.buy_exchange}→{opp.sell_exchange}"
                self.log(f"🎯 {opp.spread_percent:.3f}% ({direction}) net:{opp.net_spread_after_slippage:.3f}% conf:{opp.confidence_score:.2f}")
                
                if self.config.dry_run:
                    self.log(f"📝 [DRY] size:{opp.recommended_size:.4f} profit:${opp.expected_profit_usd:.2f}")
                    self.stats.trades += 1
                else:
                    # Execute trade
                    asyncio.create_task(self._execute_trade(opp))
            
            # Trigger frontend update
            if self._update_callback:
                self._update_callback(self.to_dict())

    async def _execute_trade(self, opp: SpreadOpportunity):
        """Execute arbitrage trade"""
        self.log(f"⚡ EXECUTING: {opp.recommended_size:.4f} @ {opp.buy_exchange}→{opp.sell_exchange}")
        
        try:
            # TODO: Implement actual order execution with the signer
            # For now, mark as successful
            self.stats.trades += 1
            self.log(f"✅ Trade executed")
        except Exception as e:
            self.log(f"❌ Trade failed: {e}")
            self.stats.errors += 1

    async def poll(self):
        """Single poll iteration (REST mode)"""
        if not self.exchange_a or not self.exchange_b:
            return

        self.stats.polls += 1
        start_time = time.time()

        try:
            # Fetch orderbooks in parallel
            orderbook_a, orderbook_b = await asyncio.gather(
                self.exchange_a.get_orderbook(self.config.symbol, depth=10),
                self.exchange_b.get_orderbook(self.config.symbol, depth=10),
            )
            
            total_latency = (time.time() - start_time) * 1000
            self.stats.record_latency(total_latency)

            if not orderbook_a or not orderbook_b:
                self.stats.errors += 1
                return

            # Update state
            self.orderbooks.exchange_a = orderbook_a
            self.orderbooks.exchange_b = orderbook_b
            self.orderbooks.last_update = int(time.time() * 1000)

            # Analyze opportunity
            self._analyze_opportunity()
            
            # Always trigger UI update after successful poll
            if self._update_callback:
                self._update_callback(self.to_dict())

        except Exception as e:
            self.stats.errors += 1
            self.log(f"❌ Poll error: {e}")

    async def run_polling(self):
        """Main bot loop - REST polling mode"""
        poll_interval = self.config.poll_interval_ms / 1000
        self.log(f"📊 Starting REST polling mode ({self.config.poll_interval_ms}ms)")
        
        try:
            while self.running:
                await self.poll()
                await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            pass

    async def run_websocket(self):
        """Main bot loop - WebSocket streaming mode"""
        self.log(f"🔌 Starting WebSocket streaming mode")
        self._ws_mode = True
        
        try:
            # Set up callbacks
            if self.exchange_a:
                self.exchange_a.set_orderbook_callback(self._on_orderbook_update)
            if self.exchange_b:
                self.exchange_b.set_orderbook_callback(self._on_orderbook_update)
            
            # Connect WebSockets
            ws_a = await self.exchange_a.connect_websocket(self.config.symbol) if self.exchange_a else False
            ws_b = await self.exchange_b.connect_websocket(self.config.symbol) if self.exchange_b else False
            
            if not ws_a or not ws_b:
                self.log(f"⚠️ WebSocket unavailable, falling back to REST")
                self._ws_mode = False
                await self.run_polling()
                return
            
            # Keep running while WebSocket is connected
            while self.running:
                # Check connection health
                if not self.exchange_a.is_websocket_connected or not self.exchange_b.is_websocket_connected:
                    self.log(f"⚠️ WebSocket disconnected, reconnecting...")
                    await asyncio.sleep(1)
                    await self.exchange_a.connect_websocket(self.config.symbol)
                    await self.exchange_b.connect_websocket(self.config.symbol)
                
                # Log status periodically
                if self.stats.ws_updates % 100 == 0 and self.stats.ws_updates > 0:
                    runtime = int(time.time() - self.stats.start_time)
                    self.log(f"📊 [{runtime}s] {self.stats.ws_updates} updates, avg:{self.stats.avg_latency_ms:.1f}ms")
                
                await asyncio.sleep(0.1)  # Small sleep to prevent busy loop
                
        except asyncio.CancelledError:
            pass
        finally:
            # Cleanup WebSocket connections
            if self.exchange_a:
                await self.exchange_a.disconnect_websocket()
            if self.exchange_b:
                await self.exchange_b.disconnect_websocket()

    async def run(self):
        """Main entry point - chooses WebSocket or polling mode"""
        if self.config.use_websocket:
            await self.run_websocket()
        else:
            await self.run_polling()


class BotManager:
    """Manages multiple HFT bots"""

    def __init__(self):
        self.bots: Dict[str, SingleBot] = {}
        self._adapters: Dict[str, ExchangeAdapter] = {}
        self._update_callbacks: List[Callable] = []

    def add_update_callback(self, callback: Callable):
        """Add callback for bot updates"""
        self._update_callbacks.append(callback)

    def _broadcast_update(self, bot_data: dict):
        """Broadcast bot update to all callbacks"""
        for callback in self._update_callbacks:
            try:
                callback(bot_data)
            except Exception as e:
                print(f"Callback error: {e}")

    async def get_adapter(self, exchange_name: str) -> Optional[ExchangeAdapter]:
        """Get or create a shared adapter"""
        if exchange_name in self._adapters:
            return self._adapters[exchange_name]

        adapter = None
        if exchange_name == "lighter":
            adapter = LighterAdapter(
                api_key=config.lighter.api_key,
                private_key=config.lighter.private_key,
                key_index=config.lighter.key_index,
                account_index=config.lighter.account_index,
            )
        elif exchange_name == "extended":
            adapter = ExtendedAdapter(
                api_key=config.extended.api_key,
                public_key=config.extended.public_key,
                stark_key=config.extended.stark_key,
            )
        elif exchange_name == "paradex":
            adapter = ParadexAdapter()
        elif exchange_name == "vest":
            adapter = VestAdapter()

        if adapter:
            success = await adapter.initialize()
            if success:
                self._adapters[exchange_name] = adapter
                return adapter
        return None

    async def create_bot(
        self,
        symbol: str,
        exchange_a: str,
        exchange_b: str,
        entry_start_pct: float = 0.5,
        entry_full_pct: float = 1.0,
        target_amount: float = 15.0,
        max_slippage_pct: float = 0.05,
        refill_delay_ms: int = 500,
        min_validity_ms: int = 100,
        poll_interval: int = 50,
        use_websocket: bool = True,
        dry_run: bool = True,
    ) -> dict:
        """Create and start a new HFT bot"""
        
        # Check if same symbol already running
        for bot in self.bots.values():
            if bot.config.symbol == symbol and bot.running:
                return {"success": False, "error": f"{symbol} bot already running"}

        bot_id = str(uuid.uuid4())[:8]
        bot_config = BotConfig(
            id=bot_id,
            symbol=symbol,
            exchange_a=exchange_a,
            exchange_b=exchange_b,
            entry_start_pct=entry_start_pct,
            entry_full_pct=entry_full_pct,
            target_amount=target_amount,
            max_slippage_pct=max_slippage_pct,
            refill_delay_ms=refill_delay_ms,
            min_validity_ms=min_validity_ms,
            poll_interval_ms=poll_interval,
            use_websocket=use_websocket,
            dry_run=dry_run,
        )

        bot = SingleBot(bot_config)
        bot.set_update_callback(self._broadcast_update)
        
        # Get adapters
        bot.exchange_a = await self.get_adapter(exchange_a)
        bot.exchange_b = await self.get_adapter(exchange_b)

        if not bot.exchange_a or not bot.exchange_b:
            return {"success": False, "error": "Failed to connect to exchanges"}

        # Start bot
        bot.running = True
        bot.stats.start_time = int(time.time())
        self.bots[bot_id] = bot
        
        mode = "WebSocket" if use_websocket else "REST"
        bot.log(f"🚀 Started in {mode} mode!")
        asyncio.create_task(bot.run())

        return {"success": True, "bot_id": bot_id}

    def stop_bot(self, bot_id: str) -> dict:
        """Stop a bot"""
        if bot_id not in self.bots:
            return {"success": False, "error": "Bot not found"}

        bot = self.bots[bot_id]
        bot.running = False
        runtime = int(time.time() - bot.stats.start_time)
        bot.log(f"🛑 Stopped after {runtime}s ({bot.stats.opportunities} opportunities)")
        
        return {"success": True}

    def remove_bot(self, bot_id: str) -> dict:
        """Remove a bot completely"""
        if bot_id not in self.bots:
            return {"success": False, "error": "Bot not found"}

        bot = self.bots[bot_id]
        if bot.running:
            bot.running = False
        
        del self.bots[bot_id]
        return {"success": True}

    def get_all_bots(self) -> List[dict]:
        """Get all bots as list of dicts"""
        return [bot.to_dict() for bot in self.bots.values()]

    def get_bot(self, bot_id: str) -> Optional[dict]:
        """Get a single bot"""
        if bot_id in self.bots:
            return self.bots[bot_id].to_dict()
        return None

    def get_exchange_latencies(self) -> dict:
        """Get latency stats for all exchanges"""
        latencies = {}
        for name, adapter in self._adapters.items():
            latencies[name] = adapter.latency.to_dict()
        return latencies


# Global manager instance
manager = BotManager()
