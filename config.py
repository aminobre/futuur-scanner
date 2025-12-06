import os

# Futuur API base
FUTUUR_BASE_URL = os.getenv("FUTUUR_BASE_URL", "https://api.futuur.com/api/v1/")

# API keys (put your real keys here or as env vars)
FUTUUR_PUBLIC_KEY = os.getenv("FUTUUR_PUBLIC_KEY", "")
FUTUUR_PRIVATE_KEY = os.getenv("FUTUUR_PRIVATE_KEY", "")

# Currency mode
CURRENCY_MODE = os.getenv("CURRENCY_MODE", "real_money")
CURRENCY = os.getenv("CURRENCY", "USDC")

# Bankroll and sizing
BANKROLL_USD = float(os.getenv("BANKROLL_USD", "1000"))
DEFAULT_BANKROLL = BANKROLL_USD
DEFAULT_RISK_MODE = os.getenv("DEFAULT_RISK_MODE", "half")  # "half" or "full"

# Web server
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "8000"))

DEBUG = True  # or False if you don't want Flask debug mode
