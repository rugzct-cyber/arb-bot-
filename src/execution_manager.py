"""
HybridExecutionManager - Institutional-grade position exit strategy
Combines Grid (price-reactive) + TWAP (time-reactive) with VWAP moderation
"""
import time
from dataclasses import dataclass, field
from typing import Optional, List, Callable
from enum import Enum
from .exchanges.base import Orderbook


class ExecutionState(Enum):
    """State machine for exit execution"""
    IDLE = "idle"
    EXECUTING = "executing"
    COMPLETED = "completed"


class TriggerType(Enum):
    """Type of execution trigger"""
    GRID = "grid"
    TWAP = "twap"
    BACKLOG = "backlog"


@dataclass
class ExecutionConfig:
    """
    Dynamic configuration for exit strategy.
    All parameters can be updated at runtime without restart.
    """
    # Grid settings - dynamic level generation
    grid_start_spread: float = 0.2      # Top spread level (%)
    grid_end_spread: float = -1.0       # Bottom spread level (%)
    grid_levels_count: int = 5          # Number of levels to generate
    grid_distribution: str = "equal"    # "equal" or "exponential"
    
    # TWAP settings
    twap_interval_sec: float = 60.0     # Idle timeout before TWAP triggers
    twap_qty_pct: float = 5.0           # % of remaining position per TWAP tick
    profit_threshold_pct: float = 0.2   # Only TWAP when spread < this
    
    # Safety / VWAP moderation
    max_slippage_bps: float = 5.0       # Max acceptable slippage in basis points
    
    def to_dict(self) -> dict:
        return {
            "grid_start_spread": self.grid_start_spread,
            "grid_end_spread": self.grid_end_spread,
            "grid_levels_count": self.grid_levels_count,
            "grid_distribution": self.grid_distribution,
            "twap_interval_sec": self.twap_interval_sec,
            "twap_qty_pct": self.twap_qty_pct,
            "profit_threshold_pct": self.profit_threshold_pct,
            "max_slippage_bps": self.max_slippage_bps,
        }


@dataclass
class ExecutionSignal:
    """Signal returned by update() indicating what to execute"""
    should_execute: bool
    qty: float
    trigger_type: Optional[TriggerType] = None
    level_index: Optional[int] = None
    reason: str = ""
    capped_by_vwap: bool = False
    original_qty: float = 0.0
    
    def to_dict(self) -> dict:
        return {
            "should_execute": self.should_execute,
            "qty": self.qty,
            "trigger_type": self.trigger_type.value if self.trigger_type else None,
            "level_index": self.level_index,
            "reason": self.reason,
            "capped_by_vwap": self.capped_by_vwap,
        }


@dataclass
class GridLevel:
    """Represents a single grid level"""
    index: int
    spread_threshold: float
    qty_pct: float
    triggered: bool = False
    triggered_at: Optional[float] = None


class HybridExecutionManager:
    """
    Institutional-grade hybrid exit strategy manager.
    
    Combines two execution engines:
    1. Grid (Opportunistic) - Triggers when spread crosses defined levels
    2. TWAP (Patient) - Triggers after idle timeout when in profit zone
    
    All quantities are moderated by VWAP analysis to prevent excessive slippage.
    """
    
    def __init__(
        self, 
        config: Optional[ExecutionConfig] = None,
        orderbook_analyzer = None,
        log_callback: Optional[Callable[[str], None]] = None
    ):
        self.config = config or ExecutionConfig()
        self.analyzer = orderbook_analyzer
        self._log_callback = log_callback
        
        # State
        self.state = ExecutionState.IDLE
        self.initial_position: float = 0.0
        self.remaining_position: float = 0.0
        self.side: str = ""  # "long" or "short"
        
        # Grid state
        self._grid_levels: List[GridLevel] = []
        self._rebuild_grid()
        
        # TWAP state
        self._last_execution_time: float = 0.0
        self._last_spread: float = 0.0
        
        # Backlog (unexecuted due to liquidity)
        self._backlog_qty: float = 0.0
        
        # Stats
        self._total_executed: float = 0.0
        self._executions: List[dict] = []
    
    def _log(self, message: str):
        """Log message via callback if set"""
        if self._log_callback:
            self._log_callback(message)
    
    def _rebuild_grid(self):
        """Generate grid levels from config"""
        count = max(2, self.config.grid_levels_count)
        start = self.config.grid_start_spread
        end = self.config.grid_end_spread
        
        if self.config.grid_distribution == "exponential":
            # Exponential: more levels near the end (panic zone)
            levels = []
            for i in range(count):
                # Exponential curve: t^2 gives more density at end
                t = i / (count - 1)
                t_exp = t ** 2
                spread = start - (start - end) * t_exp
                levels.append(spread)
        else:
            # Equal distribution
            step = (start - end) / (count - 1)
            levels = [start - i * step for i in range(count)]
        
        # Qty per level (equal by default)
        qty_per_level = 100.0 / count
        
        self._grid_levels = [
            GridLevel(
                index=i,
                spread_threshold=level,
                qty_pct=qty_per_level,
                triggered=False
            )
            for i, level in enumerate(levels)
        ]
        
        self._log(f"🔧 Grid rebuilt: {count} levels from {start:.2f}% to {end:.2f}%")
    
    def update_config(self, new_config: ExecutionConfig):
        """Hot-reload configuration at runtime"""
        old_levels = self.config.grid_levels_count
        self.config = new_config
        
        # Rebuild grid if level params changed
        if (new_config.grid_levels_count != old_levels or 
            new_config.grid_start_spread != self.config.grid_start_spread or
            new_config.grid_end_spread != self.config.grid_end_spread):
            
            # Preserve triggered state for existing levels
            old_triggered = {l.index: l.triggered for l in self._grid_levels}
            self._rebuild_grid()
            
            # Restore triggered state for matching indices
            for level in self._grid_levels:
                if level.index in old_triggered:
                    level.triggered = old_triggered[level.index]
        
        self._log(f"🔧 Config updated: levels={new_config.grid_levels_count}, twap={new_config.twap_interval_sec}s")
    
    def start_exit(self, position_size: float, side: str):
        """
        Initialize exit execution for a position.
        
        Args:
            position_size: Total position size to exit
            side: "long" (selling) or "short" (buying back)
        """
        if position_size <= 0:
            return
        
        self.state = ExecutionState.EXECUTING
        self.initial_position = position_size
        self.remaining_position = position_size
        self.side = side
        self._last_execution_time = time.time()
        self._backlog_qty = 0.0
        self._total_executed = 0.0
        self._executions = []
        
        # Reset grid
        for level in self._grid_levels:
            level.triggered = False
            level.triggered_at = None
        
        self._log(f"🚀 Exit started: {position_size:.4f} ({side})")
    
    def update(
        self, 
        current_spread: float,
        ob_long: Optional[Orderbook],
        ob_short: Optional[Orderbook]
    ) -> ExecutionSignal:
        """
        Main tick handler - called each iteration.
        
        Returns an ExecutionSignal indicating what (if anything) to execute.
        """
        if self.state != ExecutionState.EXECUTING:
            return ExecutionSignal(should_execute=False, qty=0.0)
        
        if self.remaining_position <= 0:
            self.state = ExecutionState.COMPLETED
            self._log("✅ Exit completed - position fully closed")
            return ExecutionSignal(should_execute=False, qty=0.0, reason="completed")
        
        self._last_spread = current_spread
        signal = None
        
        # Priority 1: Check backlog first
        if self._backlog_qty > 0:
            signal = self._try_execute_backlog(ob_long, ob_short)
            if signal and signal.should_execute:
                return signal
        
        # Priority 2: Check grid triggers
        signal = self._check_grid_trigger(current_spread)
        if signal and signal.should_execute:
            # Apply VWAP moderation
            return self._moderate_with_vwap(signal, ob_long, ob_short)
        
        # Priority 3: Check TWAP trigger
        signal = self._check_twap_trigger(current_spread)
        if signal and signal.should_execute:
            return self._moderate_with_vwap(signal, ob_long, ob_short)
        
        return ExecutionSignal(should_execute=False, qty=0.0)
    
    def _check_grid_trigger(self, current_spread: float) -> Optional[ExecutionSignal]:
        """Check if spread crossed any grid level"""
        for level in self._grid_levels:
            if level.triggered:
                continue
            
            # Trigger when spread drops below threshold
            if current_spread <= level.spread_threshold:
                level.triggered = True
                level.triggered_at = time.time()
                
                # Calculate qty for this level
                qty = (level.qty_pct / 100.0) * self.initial_position
                qty = min(qty, self.remaining_position)
                
                self._log(f"🎯 GRID L{level.index}: spread {current_spread:.3f}% ≤ {level.spread_threshold:.2f}%")
                
                return ExecutionSignal(
                    should_execute=True,
                    qty=qty,
                    trigger_type=TriggerType.GRID,
                    level_index=level.index,
                    reason=f"Grid level {level.index} ({level.spread_threshold:.2f}%) triggered"
                )
        
        return None
    
    def _check_twap_trigger(self, current_spread: float) -> Optional[ExecutionSignal]:
        """Check if TWAP should trigger due to idle timeout"""
        # Only trigger TWAP when in profit zone
        if current_spread > self.config.profit_threshold_pct:
            return None
        
        time_since_last = time.time() - self._last_execution_time
        
        if time_since_last >= self.config.twap_interval_sec:
            # Calculate TWAP qty
            qty = (self.config.twap_qty_pct / 100.0) * self.remaining_position
            qty = min(qty, self.remaining_position)
            
            self._log(f"⏰ TWAP: idle {time_since_last:.0f}s, spread {current_spread:.3f}%")
            
            return ExecutionSignal(
                should_execute=True,
                qty=qty,
                trigger_type=TriggerType.TWAP,
                reason=f"TWAP after {time_since_last:.0f}s idle"
            )
        
        return None
    
    def _try_execute_backlog(
        self, 
        ob_long: Optional[Orderbook], 
        ob_short: Optional[Orderbook]
    ) -> Optional[ExecutionSignal]:
        """Try to execute backlogged quantity"""
        if self._backlog_qty <= 0:
            return None
        
        # Check if we now have liquidity
        safe_qty = self._calculate_safe_qty(self._backlog_qty, ob_long, ob_short)
        
        if safe_qty > 0:
            execute_qty = min(safe_qty, self._backlog_qty)
            self._backlog_qty -= execute_qty
            
            self._log(f"📦 BACKLOG: executing {execute_qty:.4f} (remaining: {self._backlog_qty:.4f})")
            
            return ExecutionSignal(
                should_execute=True,
                qty=execute_qty,
                trigger_type=TriggerType.BACKLOG,
                reason=f"Backlog execution ({self._backlog_qty:.4f} remaining)"
            )
        
        return None
    
    def _moderate_with_vwap(
        self, 
        signal: ExecutionSignal,
        ob_long: Optional[Orderbook],
        ob_short: Optional[Orderbook]
    ) -> ExecutionSignal:
        """Apply VWAP moderation to cap quantity based on liquidity"""
        if not signal.should_execute or signal.qty <= 0:
            return signal
        
        safe_qty = self._calculate_safe_qty(signal.qty, ob_long, ob_short)
        
        if safe_qty < signal.qty:
            # Add excess to backlog
            excess = signal.qty - safe_qty
            self._backlog_qty += excess
            
            self._log(f"⚠️ VWAP CAP: {signal.qty:.4f} → {safe_qty:.4f} (+{excess:.4f} backlog)")
            
            return ExecutionSignal(
                should_execute=safe_qty > 0,
                qty=safe_qty,
                trigger_type=signal.trigger_type,
                level_index=signal.level_index,
                reason=signal.reason,
                capped_by_vwap=True,
                original_qty=signal.qty
            )
        
        return signal
    
    def _calculate_safe_qty(
        self, 
        desired_qty: float,
        ob_long: Optional[Orderbook],
        ob_short: Optional[Orderbook]
    ) -> float:
        """Calculate max quantity that stays within slippage tolerance"""
        if not self.analyzer:
            return desired_qty  # No analyzer, no moderation
        
        # Choose orderbook based on side
        # Long exit = sell on long exchange
        # Short exit = buy on short exchange
        if self.side == "long" and ob_long:
            return self.analyzer.calculate_max_safe_qty(
                ob_long, 
                "sell", 
                self.config.max_slippage_bps
            )
        elif self.side == "short" and ob_short:
            return self.analyzer.calculate_max_safe_qty(
                ob_short, 
                "buy", 
                self.config.max_slippage_bps
            )
        
        return desired_qty
    
    def record_execution(self, qty: float, success: bool):
        """Record that an execution occurred"""
        if success and qty > 0:
            self.remaining_position -= qty
            self._total_executed += qty
            self._last_execution_time = time.time()
            
            self._executions.append({
                "qty": qty,
                "remaining": self.remaining_position,
                "timestamp": int(time.time() * 1000),
            })
            
            if self.remaining_position <= 0:
                self.state = ExecutionState.COMPLETED
                self._log("✅ Exit completed - position fully closed")
    
    def get_status(self) -> dict:
        """Get current execution status for dashboard"""
        return {
            "state": self.state.value,
            "initial_position": self.initial_position,
            "remaining_position": self.remaining_position,
            "progress_pct": ((self.initial_position - self.remaining_position) / self.initial_position * 100) if self.initial_position > 0 else 0,
            "side": self.side,
            "backlog_qty": self._backlog_qty,
            "total_executed": self._total_executed,
            "grid_levels": [
                {
                    "index": l.index,
                    "threshold": l.spread_threshold,
                    "qty_pct": l.qty_pct,
                    "triggered": l.triggered,
                }
                for l in self._grid_levels
            ],
            "last_spread": self._last_spread,
            "config": self.config.to_dict(),
            "executions_count": len(self._executions),
        }
    
    def reset(self):
        """Reset manager to idle state"""
        self.state = ExecutionState.IDLE
        self.initial_position = 0.0
        self.remaining_position = 0.0
        self.side = ""
        self._backlog_qty = 0.0
        self._total_executed = 0.0
        self._executions = []
        
        for level in self._grid_levels:
            level.triggered = False
            level.triggered_at = None
