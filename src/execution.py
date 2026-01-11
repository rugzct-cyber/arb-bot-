"""
SmartExecutionManager - Unified Entry/Exit Execution System
Philosophy: "Dynamic Slicing & Dual-Check" - Take what liquidity gives
"""
import time
import asyncio
from dataclasses import dataclass, field
from typing import Optional, List, Callable, Deque
from collections import deque
from enum import Enum
from .exchanges.base import Orderbook


class ExecutionMode(Enum):
    """Current execution mode"""
    IDLE = "idle"
    ENTRY = "entry"
    EXIT = "exit"


class ExecutionState(Enum):
    """Execution state machine"""
    IDLE = "idle"
    EXECUTING = "executing"
    COMPLETED = "completed"
    PAUSED = "paused"


@dataclass
class EntryConfig:
    """
    Configuration for entry (scale-in) execution.
    All values are dynamically configurable via frontend.
    """
    # Targeting (Scale-in)
    entry_start_pct: float = 0.5       # Start firing threshold (%)
    entry_full_pct: float = 1.0        # 100% investment threshold (%)
    target_amount: float = 15.0        # Target in tokens (ETH, BTC, etc.)
    
    # Safety & Timing
    max_slippage_pct: float = 0.05     # Strict slippage limit (%)
    refill_delay_ms: int = 500         # Pause between slices (ms)
    min_validity_ms: int = 100         # Anti-fakeout duration (ms)
    
    def to_dict(self) -> dict:
        return {
            "entry_start_pct": self.entry_start_pct,
            "entry_full_pct": self.entry_full_pct,
            "target_amount": self.target_amount,
            "max_slippage_pct": self.max_slippage_pct,
            "refill_delay_ms": self.refill_delay_ms,
            "min_validity_ms": self.min_validity_ms,
        }


@dataclass
class ExitConfig:
    """Configuration for exit (scale-out) execution."""
    max_slippage_pct: float = 0.05
    refill_delay_ms: int = 500
    min_validity_ms: int = 100
    
    def to_dict(self) -> dict:
        return {
            "max_slippage_pct": self.max_slippage_pct,
            "refill_delay_ms": self.refill_delay_ms,
            "min_validity_ms": self.min_validity_ms,
        }


@dataclass
class SliceResult:
    """Result from calculate_next_slice"""
    should_execute: bool
    size: float
    reason: str
    safe_qty_a: float = 0.0
    safe_qty_b: float = 0.0
    remaining_target: float = 0.0
    capped_by_liquidity: bool = False
    
    def to_dict(self) -> dict:
        return {
            "should_execute": self.should_execute,
            "size": self.size,
            "reason": self.reason,
            "safe_qty_a": self.safe_qty_a,
            "safe_qty_b": self.safe_qty_b,
            "capped_by_liquidity": self.capped_by_liquidity,
        }


@dataclass
class SpreadSample:
    """A single spread measurement with timestamp"""
    spread: float
    timestamp_ms: int
    above_threshold: bool


class SignalValidator:
    """
    Anti-Fakeout Validator
    
    Ensures spread signal persists for a minimum duration before
    allowing execution. Prevents false triggers from momentary spikes.
    """
    
    def __init__(self, min_validity_ms: int = 100):
        self.min_validity_ms = min_validity_ms
        self._samples: Deque[SpreadSample] = deque(maxlen=100)
        self._valid_since: Optional[int] = None
    
    def update_config(self, min_validity_ms: int):
        """Hot-reload validity duration"""
        self.min_validity_ms = min_validity_ms
    
    def record(self, spread: float, threshold: float):
        """
        Record a spread measurement.
        
        Args:
            spread: Current spread value (%)
            threshold: Target threshold to check against (%)
        """
        now_ms = int(time.time() * 1000)
        above = spread >= threshold
        
        self._samples.append(SpreadSample(
            spread=spread,
            timestamp_ms=now_ms,
            above_threshold=above
        ))
        
        if above:
            if self._valid_since is None:
                self._valid_since = now_ms
        else:
            self._valid_since = None
    
    def is_valid(self) -> bool:
        """
        Check if signal has been valid for min_validity_ms.
        
        Returns:
            True if spread has been above threshold for required duration
        """
        if self._valid_since is None:
            return False
        
        now_ms = int(time.time() * 1000)
        duration_ms = now_ms - self._valid_since
        
        return duration_ms >= self.min_validity_ms
    
    def get_duration_ms(self) -> int:
        """Get how long the signal has been valid"""
        if self._valid_since is None:
            return 0
        return int(time.time() * 1000) - self._valid_since
    
    def reset(self):
        """Reset validator state"""
        self._samples.clear()
        self._valid_since = None


class SmartExecutionManager:
    """
    Unified Entry/Exit Execution Manager
    
    Philosophy: "Dynamic Slicing & Dual-Check"
    - Don't slice arbitrarily - take what liquidity gives
    - Check both exchanges before executing
    - Respect refill delay to let market makers reload
    
    Core Method: calculate_next_slice()
    - Rule of the Weakest: slice = min(safe_qty_a, safe_qty_b, remaining)
    """
    
    def __init__(
        self,
        orderbook_analyzer = None,
        log_callback: Optional[Callable[[str], None]] = None
    ):
        self.analyzer = orderbook_analyzer
        self._log_callback = log_callback
        
        # State
        self.mode = ExecutionMode.IDLE
        self.state = ExecutionState.IDLE
        
        # Entry config & state
        self.entry_config: Optional[EntryConfig] = None
        self.target_amount: float = 0.0
        self.executed_amount: float = 0.0
        
        # Exit config & state
        self.exit_config: Optional[ExitConfig] = None
        self.exit_target: float = 0.0
        
        # Timing
        self._last_execution_time_ms: int = 0
        self._current_refill_delay_ms: int = 500
        
        # Validator
        self.signal_validator = SignalValidator()
        
        # Stats
        self._slices_executed: int = 0
        self._total_volume: float = 0.0
        self._executions: List[dict] = []
    
    def _log(self, message: str):
        """Log via callback"""
        if self._log_callback:
            self._log_callback(message)
    
    def _now_ms(self) -> int:
        """Current time in milliseconds"""
        return int(time.time() * 1000)
    
    # ==================== ENTRY METHODS ====================
    
    def start_entry(self, config: EntryConfig):
        """
        Start entry execution (scale-in).
        
        Args:
            config: Entry configuration with targets and limits
        """
        self.entry_config = config
        self.mode = ExecutionMode.ENTRY
        self.state = ExecutionState.EXECUTING
        self.target_amount = config.target_amount
        self.executed_amount = 0.0
        self._current_refill_delay_ms = config.refill_delay_ms
        self._last_execution_time_ms = 0
        self._slices_executed = 0
        self._executions = []
        
        self.signal_validator = SignalValidator(config.min_validity_ms)
        
        self._log(f"🚀 ENTRY started: target={config.target_amount:.4f} tokens")
        self._log(f"   Range: {config.entry_start_pct}% → {config.entry_full_pct}%")
    
    def update_entry_config(self, config: EntryConfig):
        """Hot-reload entry configuration"""
        self.entry_config = config
        self._current_refill_delay_ms = config.refill_delay_ms
        self.signal_validator.update_config(config.min_validity_ms)
        self._log(f"🔧 Entry config updated: target={config.target_amount}, delay={config.refill_delay_ms}ms")
    
    # ==================== EXIT METHODS ====================
    
    def start_exit(self, position_size: float, config: Optional[ExitConfig] = None):
        """
        Start exit execution (scale-out).
        
        Args:
            position_size: Total position to exit
            config: Exit configuration
        """
        self.exit_config = config or ExitConfig()
        self.mode = ExecutionMode.EXIT
        self.state = ExecutionState.EXECUTING
        self.exit_target = position_size
        self.executed_amount = 0.0
        self._current_refill_delay_ms = self.exit_config.refill_delay_ms
        self._last_execution_time_ms = 0
        self._slices_executed = 0
        self._executions = []
        
        self.signal_validator = SignalValidator(self.exit_config.min_validity_ms)
        
        self._log(f"🚀 EXIT started: {position_size:.4f} tokens to close")
    
    # ==================== CORE EXECUTION ====================
    
    def can_fire(self) -> bool:
        """
        Check if enough time has passed since last execution.
        Respects refill_delay_ms to let market makers reload.
        """
        if self._last_execution_time_ms == 0:
            return True
        
        elapsed = self._now_ms() - self._last_execution_time_ms
        return elapsed >= self._current_refill_delay_ms
    
    def calculate_next_slice(
        self,
        ob_a: Orderbook,
        ob_b: Orderbook,
        direction: str,  # "buy" or "sell" for ob_a
        max_slippage_pct: float
    ) -> SliceResult:
        """
        Calculate the next execution slice using dual-check.
        
        RULE OF THE WEAKEST:
        slice_size = min(safe_qty_a, safe_qty_b, remaining_target)
        
        Args:
            ob_a: Orderbook A (where we execute leg 1)
            ob_b: Orderbook B (where we execute leg 2)
            direction: "buy" or "sell" for orderbook A
            max_slippage_pct: Maximum acceptable slippage (%)
            
        Returns:
            SliceResult with recommended size and dual-check values
        """
        remaining = self._get_remaining()
        
        if remaining <= 0:
            return SliceResult(
                should_execute=False,
                size=0.0,
                reason="No remaining target"
            )
        
        # Calculate safe qty for each exchange
        max_slippage_bps = max_slippage_pct * 100  # Convert to bps
        
        if direction == "buy":
            # Buying on A (use asks), Selling on B (use bids)
            safe_qty_a = self._calculate_safe_qty(ob_a, "buy", max_slippage_bps)
            safe_qty_b = self._calculate_safe_qty(ob_b, "sell", max_slippage_bps)
        else:
            # Selling on A (use bids), Buying on B (use asks)
            safe_qty_a = self._calculate_safe_qty(ob_a, "sell", max_slippage_bps)
            safe_qty_b = self._calculate_safe_qty(ob_b, "buy", max_slippage_bps)
        
        # RULE OF THE WEAKEST
        slice_size = min(safe_qty_a, safe_qty_b, remaining)
        
        if slice_size <= 0:
            return SliceResult(
                should_execute=False,
                size=0.0,
                reason="Insufficient liquidity on both sides",
                safe_qty_a=safe_qty_a,
                safe_qty_b=safe_qty_b,
                remaining_target=remaining
            )
        
        capped = slice_size < remaining
        
        return SliceResult(
            should_execute=True,
            size=slice_size,
            reason=f"Slice: {slice_size:.4f} (A:{safe_qty_a:.4f}, B:{safe_qty_b:.4f})",
            safe_qty_a=safe_qty_a,
            safe_qty_b=safe_qty_b,
            remaining_target=remaining,
            capped_by_liquidity=capped
        )
    
    def _calculate_safe_qty(
        self,
        orderbook: Orderbook,
        side: str,
        max_slippage_bps: float
    ) -> float:
        """Calculate max qty for acceptable slippage on one exchange"""
        if self.analyzer:
            return self.analyzer.calculate_max_safe_qty(
                orderbook, side, max_slippage_bps
            )
        
        # Fallback: use 10% of visible depth
        if side == "buy":
            return orderbook.ask_depth * 0.1 if orderbook.asks else 0.0
        else:
            return orderbook.bid_depth * 0.1 if orderbook.bids else 0.0
    
    def _get_remaining(self) -> float:
        """Get remaining amount to execute"""
        if self.mode == ExecutionMode.ENTRY:
            return self.target_amount - self.executed_amount
        elif self.mode == ExecutionMode.EXIT:
            return self.exit_target - self.executed_amount
        return 0.0
    
    def update(
        self,
        spread: float,
        ob_a: Orderbook,
        ob_b: Orderbook
    ) -> Optional[SliceResult]:
        """
        Main tick handler - called each iteration.
        
        Args:
            spread: Current spread (%)
            ob_a: Orderbook for exchange A
            ob_b: Orderbook for exchange B
            
        Returns:
            SliceResult if execution should happen, None otherwise
        """
        if self.state != ExecutionState.EXECUTING:
            return None
        
        remaining = self._get_remaining()
        if remaining <= 0:
            self.state = ExecutionState.COMPLETED
            self._log(f"✅ {self.mode.value.upper()} completed: {self.executed_amount:.4f} tokens")
            return None
        
        # Check refill delay
        if not self.can_fire():
            return None
        
        # Entry mode: check signal validity
        if self.mode == ExecutionMode.ENTRY and self.entry_config:
            threshold = self.entry_config.entry_start_pct
            self.signal_validator.record(spread, threshold)
            
            if not self.signal_validator.is_valid():
                return None
            
            # Calculate execution intensity based on spread
            intensity = self._calculate_entry_intensity(spread)
            max_slippage = self.entry_config.max_slippage_pct
            
            # Direction: buy on A, sell on B (standard arb)
            result = self.calculate_next_slice(ob_a, ob_b, "buy", max_slippage)
            
            if result.should_execute:
                # Scale by intensity (higher spread = more aggressive)
                result.size = result.size * intensity
                self._log(f"📊 ENTRY slice: {result.size:.4f} @ {spread:.3f}% (intensity: {intensity:.1%})")
            
            return result
        
        # Exit mode: simpler logic
        if self.mode == ExecutionMode.EXIT and self.exit_config:
            max_slippage = self.exit_config.max_slippage_pct
            result = self.calculate_next_slice(ob_a, ob_b, "sell", max_slippage)
            
            if result.should_execute:
                self._log(f"📊 EXIT slice: {result.size:.4f}")
            
            return result
        
        return None
    
    def _calculate_entry_intensity(self, spread: float) -> float:
        """
        Calculate execution intensity based on spread.
        
        At entry_start_pct: 10% intensity
        At entry_full_pct: 100% intensity
        Linear interpolation between
        """
        if not self.entry_config:
            return 1.0
        
        start = self.entry_config.entry_start_pct
        full = self.entry_config.entry_full_pct
        
        if spread <= start:
            return 0.0
        if spread >= full:
            return 1.0
        
        # Linear interpolation from 10% to 100%
        progress = (spread - start) / (full - start)
        return 0.1 + (0.9 * progress)
    
    def record_execution(self, qty: float, success: bool):
        """Record that an execution occurred"""
        if success and qty > 0:
            self.executed_amount += qty
            self._last_execution_time_ms = self._now_ms()
            self._slices_executed += 1
            self._total_volume += qty
            
            self._executions.append({
                "qty": qty,
                "executed_total": self.executed_amount,
                "remaining": self._get_remaining(),
                "timestamp": self._now_ms(),
            })
            
            remaining = self._get_remaining()
            if remaining <= 0:
                self.state = ExecutionState.COMPLETED
                self._log(f"✅ {self.mode.value.upper()} completed!")
    
    def get_status(self) -> dict:
        """Get current execution status for dashboard"""
        remaining = self._get_remaining()
        target = self.target_amount if self.mode == ExecutionMode.ENTRY else self.exit_target
        progress = (self.executed_amount / target * 100) if target > 0 else 0
        
        return {
            "mode": self.mode.value,
            "state": self.state.value,
            "target": target,
            "executed": self.executed_amount,
            "remaining": remaining,
            "progress_pct": progress,
            "slices_executed": self._slices_executed,
            "can_fire": self.can_fire(),
            "signal_valid": self.signal_validator.is_valid(),
            "signal_duration_ms": self.signal_validator.get_duration_ms(),
            "entry_config": self.entry_config.to_dict() if self.entry_config else None,
            "exit_config": self.exit_config.to_dict() if self.exit_config else None,
        }
    
    def reset(self):
        """Reset manager to idle state"""
        self.mode = ExecutionMode.IDLE
        self.state = ExecutionState.IDLE
        self.entry_config = None
        self.exit_config = None
        self.target_amount = 0.0
        self.exit_target = 0.0
        self.executed_amount = 0.0
        self._last_execution_time_ms = 0
        self._slices_executed = 0
        self._executions = []
        self.signal_validator.reset()
