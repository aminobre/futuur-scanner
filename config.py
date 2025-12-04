import os

# Futuur API config
FUTUUR_BASE_URL = os.getenv("FUTUUR_BASE_URL", "https://api.futuur.com")
FUTUUR_PUBLIC_KEY = os.getenv("FUTUUR_PUBLIC_KEY", "")
FUTUUR_PRIVATE_KEY = os.getenv("FUTUUR_PRIVATE_KEY", "")
FUTUUR_API_KEY = os.getenv("FUTUUR_API_KEY", "")

# Betting config
BANKROLL_USD = float(os.getenv("BANKROLL_USD", "1000"))
EDGE_THRESHOLD = float(os.getenv("EDGE_THRESHOLD", "0.02"))  # 2 percentage points
RISK_MODE = os.getenv("RISK_MODE", "half")  # 'full' or 'half'
