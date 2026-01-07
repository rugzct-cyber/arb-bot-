"""Quick test script to verify fixed adapters"""
import asyncio
import aiohttp

async def test_lighter():
    print("\n=== LIGHTER ===")
    url = "https://mainnet.zklighter.elliot.ai/api/v1/orderBookOrders?market_id=0&limit=5"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            print(f"Status: {resp.status}")
            if resp.status == 200:
                data = await resp.json()
                bids = data.get("bids", [])
                asks = data.get("asks", [])
                print(f"Bids: {len(bids)}, Asks: {len(asks)}")
                if bids:
                    print(f"Best bid: ${bids[0].get('price')}")
                if asks:
                    print(f"Best ask: ${asks[0].get('price')}")
                if bids and asks:
                    bid = float(bids[0].get('price', 0))
                    ask = float(asks[0].get('price', 0))
                    spread = ((ask - bid) / bid) * 100
                    print(f"Internal spread: {spread:.4f}%")

async def test_extended():
    print("\n=== EXTENDED ===")
    url = "https://api.starknet.extended.exchange/api/v1/info/markets"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            print(f"Status: {resp.status}")
            if resp.status == 200:
                data = await resp.json()
                markets = data.get("data", [])
                # Find ETH
                for m in markets:
                    if m.get("assetName") == "ETH":
                        stats = m.get("marketStats", {})
                        bid = stats.get("bidPrice", "N/A")
                        ask = stats.get("askPrice", "N/A")
                        print(f"ETH market found!")
                        print(f"Best bid: ${bid}")
                        print(f"Best ask: ${ask}")
                        if bid and ask:
                            bid_f = float(bid)
                            ask_f = float(ask)
                            spread = ((ask_f - bid_f) / bid_f) * 100
                            print(f"Internal spread: {spread:.4f}%")
                        break

async def main():
    await test_lighter()
    await test_extended()
    
    print("\n=== CROSS-EXCHANGE SPREAD ===")
    lighter_ask = None
    extended_bid = None
    
    async with aiohttp.ClientSession() as session:
        # Get Lighter ask
        async with session.get("https://mainnet.zklighter.elliot.ai/api/v1/orderBookOrders?market_id=0&limit=5") as resp:
            if resp.status == 200:
                data = await resp.json()
                asks = data.get("asks", [])
                if asks:
                    lighter_ask = float(asks[0].get('price', 0))
        
        # Get Extended bid
        async with session.get("https://api.starknet.extended.exchange/api/v1/info/markets") as resp:
            if resp.status == 200:
                data = await resp.json()
                for m in data.get("data", []):
                    if m.get("assetName") == "ETH":
                        extended_bid = float(m.get("marketStats", {}).get("bidPrice", 0))
                        break
    
    if lighter_ask and extended_bid:
        spread = ((extended_bid - lighter_ask) / lighter_ask) * 100
        print(f"Buy Lighter @ ${lighter_ask:.2f} -> Sell Extended @ ${extended_bid:.2f}")
        print(f"Spread: {spread:.4f}%")

if __name__ == "__main__":
    asyncio.run(main())
