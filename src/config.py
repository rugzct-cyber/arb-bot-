"""
Configuration loader for Arb Bot
"""
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class TradingConfig:
    symbol: str
    exchange_a: str
    exchange_b: str
    min_spread_percent: float
    max_position_size: float
    poll_interval_ms: int
    dry_run: bool


@dataclass
class LighterConfig:
    """Lighter exchange configuration"""

    api_key: str = ""
    private_key: str = ""  # Ethereum private key for signing
    key_index: int = 0  # API key index
    wallet_address: str = ""


@dataclass
class ExtendedConfig:
    """Extended exchange configuration"""
    api_key: str = ""
    public_key: str = ""
    stark_key: str = ""


@dataclass
class Config:
    trading: TradingConfig
    lighter: LighterConfig
    extended: ExtendedConfig
    api_port: int

    @classmethod
    def load(cls) -> "Config":
        return cls(
            trading=TradingConfig(
                symbol=os.getenv("SYMBOL", "ETH-USD"),
                exchange_a=os.getenv("EXCHANGE_A", "lighter"),
                exchange_b=os.getenv("EXCHANGE_B", "extended"),
                min_spread_percent=float(os.getenv("MIN_SPREAD_PERCENT", "0.15")),
                max_position_size=float(os.getenv("MAX_POSITION_SIZE", "20")),
                poll_interval_ms=int(os.getenv("POLL_INTERVAL_MS", "200")),
                dry_run=os.getenv("DRY_RUN", "true").lower() == "true",
            ),
            lighter=LighterConfig(
                api_key=os.getenv("LIGHTER_API_KEY", ""),
                private_key=os.getenv("LIGHTER_PRIVATE_KEY", ""),
                key_index=int(os.getenv("LIGHTER_KEY_INDEX", "0")),
                wallet_address=os.getenv("LIGHTER_WALLET_ADDRESS", ""),
            ),
            extended=ExtendedConfig(
                api_key=os.getenv("EXTENDED_API_KEY", ""),
                public_key=os.getenv("EXTENDED_PUBLIC_KEY", ""),
                stark_key=os.getenv("EXTENDED_STARK_KEY", ""),
            ),
            api_port=int(os.getenv("API_PORT", "8080")),
        )


# Global config instance
config = Config.load()
