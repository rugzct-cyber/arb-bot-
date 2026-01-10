"""
Configuration loader for Arb Bot
"""
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class LighterConfig:
    """Lighter exchange configuration"""

    api_key: str = ""
    private_key: str = ""  # API private key for signing
    key_index: int = 0  # API key index (0-254)
    account_index: int = 0  # Lighter account index


@dataclass
class ExtendedConfig:
    """Extended exchange configuration"""
    api_key: str = ""
    public_key: str = ""
    stark_key: str = ""


@dataclass
class Config:
    lighter: LighterConfig
    extended: ExtendedConfig
    api_port: int

    @classmethod
    def load(cls) -> "Config":
        return cls(
            lighter=LighterConfig(
                api_key=os.getenv("LIGHTER_API_KEY", ""),
                private_key=os.getenv("LIGHTER_PRIVATE_KEY", ""),
                key_index=int(os.getenv("LIGHTER_KEY_INDEX", "0")),
                account_index=int(os.getenv("LIGHTER_ACCOUNT_INDEX", "0")),
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
