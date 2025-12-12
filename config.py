import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root (same folder as this config.py)
ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=False)

# Futuur API base
FUTUUR_BASE_URL = os.getenv("FUTUUR_BASE_URL", "https://api.futuur.com/api/v1/").strip()

# API keys
FUTUUR_PUBLIC_KEY = os.getenv("FUTUUR_PUBLIC_KEY", "").strip()
FUTUUR_PRIVATE_KEY = os.getenv("FUTUUR_PRIVATE_KEY", "").strip()

# Currency mode
CURRENCY_MODE = os.getenv("CURRENCY_MODE", "real_money").strip()
CURRENCY = os.getenv("CURRENCY", "USDC").strip()

# Bankroll and sizing
BANKROLL_USD = float(os.getenv("BANKROLL_USD", "1000"))
DEFAULT_BANKROLL = BANKROLL_USD

DEFAULT_RISK_MODE = os.getenv("DEFAULT_RISK_MODE", "half").strip()  # "half" or "full"
# Backwards-compat aliases (older code imported these names)
RISK_MODE = os.getenv("RISK_MODE", DEFAULT_RISK_MODE).strip()

# Web server
APP_HOST = os.getenv("APP_HOST", "0.0.0.0").strip()
APP_PORT = int(os.getenv("APP_PORT", "8000"))
DEBUG = os.getenv("DEBUG", "1").strip() not in {"0", "false", "False"}
