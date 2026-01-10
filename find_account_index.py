"""
Find Lighter Account Index
Run this script with your Ethereum address to find your Lighter account index.

Usage:
    python find_account_index.py 0xYourEthereumAddress
"""
import asyncio
import sys
import aiohttp

BASE_URL = "https://mainnet.zklighter.elliot.ai/api/v1"


async def find_account_index(l1_address: str):
    """Find your Lighter account index using your Ethereum address"""
    print("\n" + "="*50)
    print("🔍 FINDING LIGHTER ACCOUNT INDEX")
    print("="*50)
    
    print(f"\n📋 Using L1 Address: {l1_address}")
    
    async with aiohttp.ClientSession() as session:
        # Query account by L1 address
        url = f"{BASE_URL}/accountsByL1Address?l1_address={l1_address}"
        print(f"\n🔗 Querying: {url}")
        
        async with session.get(url) as resp:
            print(f"   Status: {resp.status}")
            
            if resp.status == 200:
                data = await resp.json()
                
                sub_accounts = data.get("sub_accounts", [])
                if sub_accounts:
                    # First element is main account
                    main_account = sub_accounts[0]
                    index = main_account.get("index", "unknown")
                    collateral = main_account.get("collateral", 0)
                    
                    print(f"\n✅ FOUND YOUR ACCOUNT:")
                    print(f"   Account Index: {index}")
                    print(f"   Collateral: ${collateral}")
                    
                    if len(sub_accounts) > 1:
                        print(f"\n   Sub-accounts: {len(sub_accounts) - 1}")
                        for i, acc in enumerate(sub_accounts[1:], 1):
                            print(f"     [{i}] Index: {acc.get('index')}")
                    
                    print(f"\n" + "="*50)
                    print(f"📝 UPDATE YOUR .env FILE:")
                    print(f"   LIGHTER_ACCOUNT_INDEX={index}")
                    print("="*50)
                    return index
                else:
                    print("❌ No accounts found for this address")
                    print("   Make sure you've created an account on https://app.lighter.xyz")
            else:
                text = await resp.text()
                print(f"❌ Failed: {text}")
    
    return None


async def main():
    if len(sys.argv) < 2:
        print("""
╔═══════════════════════════════════════════════════════╗
║         🔍 LIGHTER ACCOUNT INDEX FINDER              ║
╚═══════════════════════════════════════════════════════╝

Usage: python find_account_index.py <your_ethereum_address>

Example:
    python find_account_index.py 0x1234567890abcdef1234567890abcdef12345678
        """)
        return
    
    l1_address = sys.argv[1]
    
    if not l1_address.startswith("0x"):
        print("❌ Invalid Ethereum address. Must start with 0x")
        return
    
    await find_account_index(l1_address)


if __name__ == "__main__":
    asyncio.run(main())
