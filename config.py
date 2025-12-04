import os

FUTUUR_BASE_URL = os.getenv("FUTUUR_BASE_URL", "https://api.futuur.com")

FUTUUR_PUBLIC_KEY = os.getenv("FUTUUR_PUBLIC_KEY", "")
FUTUUR_PRIVATE_KEY = os.getenv("FUTUUR_PRIVATE_KEY", "")
FUTUUR_API_KEY = os.getenv("FUTUUR_API_KEY", "")

BANKROLL_USD = 1000.0
EDGE_THRESHOLD = 0.02
RISK_MODE = "half"
