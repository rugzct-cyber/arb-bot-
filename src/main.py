"""
Arb Bot - Entry Point
Runs the API server which hosts the dashboard and controls the bot.
"""
import asyncio
import uvicorn
from .config import config


def print_banner():
    print("""
╔═══════════════════════════════════════════╗
║           🤖 ARB BOT v1.0                 ║
║     Simple Focused Arbitrage Bot          ║
╚═══════════════════════════════════════════╝
    """)


def main():
    print_banner()
    print("📋 Starting API server...")
    print(f"   Dashboard: http://localhost:{config.api_port}")
    print(f"   API: http://localhost:{config.api_port}/api/status")
    print("")
    print("🔧 Configure your trading pair via the dashboard.")
    print("   Press Ctrl+C to stop.\n")

    # Import here to avoid circular imports
    from .api.server import app

    uvicorn.run(app, host="0.0.0.0", port=config.api_port, log_level="warning")


if __name__ == "__main__":
    main()
