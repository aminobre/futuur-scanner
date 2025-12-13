import os

# dotenv is optional in production (Render injects env vars).
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# Render expects PORT
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("PORT", os.getenv("APP_PORT", "10000")))

BANKROLL_USD = float(os.getenv("BANKROLL_USD", "1000"))

FUTUUR_BASE_URL = os.getenv("FUTUUR_BASE_URL", "https://api.futuur.com/api/v1/")
FUTUUR_PUBLIC_KEY = os.getenv("FUTUUR_PUBLIC_KEY", "")
FUTUUR_PRIVATE_KEY = os.getenv("FUTUUR_PRIVATE_KEY", "")
