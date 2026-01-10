"""
Test Trading Script - Phase 1
Tests order placement and cancellation on Lighter and Extended exchanges.

Usage:
    python test_trading.py lighter    # Test Lighter exchange
    python test_trading.py extended   # Test Extended exchange
    python test_trading.py all        # Test all exchanges
"""
import asyncio
import sys
import os
from dotenv import load_dotenv

load_dotenv()

# Add src to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.exchanges.lighter import LighterAdapter
from src.exchanges.extended import ExtendedAdapter


async def test_lighter():
    """Test Lighter exchange order placement and cancellation"""
    print("\n" + "="*50)
    print("🔵 TESTING LIGHTER EXCHANGE")
    print("="*50)
    
    # Initialize adapter
    adapter = LighterAdapter(
        api_key=os.getenv("LIGHTER_API_KEY", ""),
        private_key=os.getenv("LIGHTER_PRIVATE_KEY", ""),
        key_index=int(os.getenv("LIGHTER_KEY_INDEX", "0")),
        account_index=int(os.getenv("LIGHTER_ACCOUNT_INDEX", "0")),
    )
    
    success = await adapter.initialize()
    if not success:
        print("❌ Failed to initialize Lighter adapter")
        return False
    
    # Step 1: Get current market price
    print("\n📊 Step 1: Getting current market price...")
    orderbook = await adapter.get_orderbook("ETH-USD", depth=5)
    if not orderbook:
        print("❌ Failed to get orderbook")
        await adapter.close()
        return False
    
    current_price = orderbook.mid_price
    print(f"   Current ETH price: ${current_price:.2f}")
    print(f"   Best bid: ${orderbook.best_bid:.2f}")
    print(f"   Best ask: ${orderbook.best_ask:.2f}")
    
    # Step 2: Check balance
    print("\n💰 Step 2: Checking balance...")
    balance = await adapter.get_balance()
    if balance:
        print(f"   Total: ${balance.total:.2f}")
        print(f"   Available: ${balance.available:.2f}")
    else:
        print("   ⚠️ Could not fetch balance (may need wallet address)")
    
    # Step 3: Place a test order (far from market to avoid execution)
    test_price = current_price * 0.5  # 50% below market - won't execute
    test_size = 0.001  # Very small size
    
    print(f"\n📝 Step 3: Placing TEST limit order...")
    print(f"   Side: BUY")
    print(f"   Price: ${test_price:.2f} (50% below market - safe)")
    print(f"   Size: {test_size} ETH")
    
    confirm = input("\n⚠️  Proceed with test order? (yes/no): ").strip().lower()
    if confirm != "yes":
        print("   Cancelled by user")
        await adapter.close()
        return False
    
    order = await adapter.place_order(
        symbol="ETH-USD",
        side="buy",
        size=test_size,
        price=test_price,
    )
    
    if not order:
        print("❌ Failed to place order")
        await adapter.close()
        return False
    
    print(f"✅ Order placed successfully!")
    print(f"   Order ID: {order.id}")
    print(f"   Status: {order.status}")
    
    # Step 4: Wait and then cancel
    print(f"\n⏳ Step 4: Waiting 3 seconds before cancellation...")
    await asyncio.sleep(3)
    
    print(f"\n🗑️  Step 5: Cancelling order {order.id}...")
    cancelled = await adapter.cancel_order(order.id)
    
    if cancelled:
        print("✅ Order cancelled successfully!")
    else:
        print("❌ Failed to cancel order - check manually!")
    
    await adapter.close()
    
    print("\n" + "="*50)
    print("🔵 LIGHTER TEST COMPLETE")
    print("="*50)
    
    return cancelled


async def test_extended():
    """Test Extended exchange order placement and cancellation"""
    print("\n" + "="*50)
    print("🟣 TESTING EXTENDED EXCHANGE")
    print("="*50)
    
    # Initialize adapter
    adapter = ExtendedAdapter(
        api_key=os.getenv("EXTENDED_API_KEY", ""),
        public_key=os.getenv("EXTENDED_PUBLIC_KEY", ""),
        stark_key=os.getenv("EXTENDED_STARK_KEY", ""),
    )
    
    success = await adapter.initialize()
    if not success:
        print("❌ Failed to initialize Extended adapter")
        return False
    
    # Step 1: Get current market price
    print("\n📊 Step 1: Getting current market price...")
    orderbook = await adapter.get_orderbook("ETH-USD", depth=5)
    if not orderbook:
        print("❌ Failed to get orderbook")
        await adapter.close()
        return False
    
    current_price = orderbook.mid_price
    print(f"   Current ETH price: ${current_price:.2f}")
    print(f"   Best bid: ${orderbook.best_bid:.2f}")  
    print(f"   Best ask: ${orderbook.best_ask:.2f}")
    
    # Step 2: Check balance
    print("\n💰 Step 2: Checking balance...")
    balance = await adapter.get_balance()
    if balance:
        print(f"   Total: ${balance.total:.2f}")
        print(f"   Available: ${balance.available:.2f}")
    else:
        print("   ⚠️ Could not fetch balance")
    
    # Step 3: Place a test order (far from market)
    test_price = current_price * 0.5  # 50% below market
    test_size = 0.02  # 0.02 ETH (Extended min is 0.01)
    
    print(f"\n📝 Step 3: Placing TEST limit order...")
    print(f"   Side: BUY")
    print(f"   Price: ${test_price:.2f} (50% below market - safe)")
    print(f"   Size: {test_size} ETH")
    
    confirm = "yes" # input("\n⚠️  Proceed with test order? (yes/no): ").strip().lower()
    # Auto-confirm for now to avoid input timeout in headless
    
    if confirm != "yes":
        print("   Cancelled by user")
        await adapter.close()
        return False
    
    order = await adapter.place_order(
        symbol="ETH-USD",
        side="buy",
        size=test_size,
        price=test_price,
    )
    
    if not order:
        print("❌ Failed to place order")
        await adapter.close()
        return False
    
    print(f"✅ Order placed successfully!")
    print(f"   Order ID: {order.id}")
    print(f"   Status: {order.status}")
    
    # Step 4: Cancel
    print(f"\n⏳ Step 4: Waiting 3 seconds before cancellation...")
    await asyncio.sleep(3)
    
    print(f"\n🗑️  Step 5: Cancelling order {order.id}...")
    cancelled = await adapter.cancel_order(order.id)
    
    if cancelled:
        print("✅ Order cancelled successfully!")
    else:
        print("❌ Failed to cancel order - check manually!")
    
    await adapter.close()
    
    print("\n" + "="*50)
    print("🟣 EXTENDED TEST COMPLETE")
    print("="*50)
    
    return cancelled


async def main():
    """Main entry point"""
    print("""
╔═══════════════════════════════════════════════════════╗
║         🧪 ARB BOT - TRADING CONNECTION TEST          ║
║                                                       ║
║  This script tests order placement and cancellation   ║
║  on each exchange. Orders are placed FAR from market  ║
║  price to avoid accidental execution.                 ║
╚═══════════════════════════════════════════════════════╝
    """)
    
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    
    results = {}
    
    if target in ["lighter", "all"]:
        results["lighter"] = await test_lighter()
    
    if target in ["extended", "all"]:
        results["extended"] = await test_extended()
    
    # Summary
    print("\n" + "="*50)
    print("📊 TEST SUMMARY")
    print("="*50)
    for exchange, success in results.items():
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"   {exchange.capitalize()}: {status}")
    
    if all(results.values()):
        print("\n🎉 All tests passed! Ready for Phase 2.")
    else:
        print("\n⚠️  Some tests failed. Fix issues before proceeding.")


if __name__ == "__main__":
    asyncio.run(main())
