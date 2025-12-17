"""Configuration management with validation."""

import logging
import os
import sys

# dotenv is optional in production (Render injects env vars).
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

logger = logging.getLogger(__name__)

# Render expects PORT
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("PORT", os.getenv("APP_PORT", "10000")))

BANKROLL_USD = float(os.getenv("BANKROLL_USD", "1000"))
RISK_MODE = os.getenv("RISK_MODE", "half")
DEFAULT_RISK_MODE = RISK_MODE  # Alias for backward compatibility

CURRENCY_MODE = os.getenv("CURRENCY_MODE", "real_money")
CURRENCY = os.getenv("CURRENCY", "USD")

FUTUUR_BASE_URL = os.getenv("FUTUUR_BASE_URL", "https://api.futuur.com/api/v1/")
FUTUUR_PUBLIC_KEY = os.getenv("FUTUUR_PUBLIC_KEY", "")
FUTUUR_PRIVATE_KEY = os.getenv("FUTUUR_PRIVATE_KEY", "")


def validate_config() -> None:
    """Validate configuration values and raise errors for invalid settings."""
    errors: list[str] = []
    
    # Validate RISK_MODE
    valid_risk_modes = {"half", "full", "balanced"}
    if RISK_MODE.lower() not in valid_risk_modes:
        errors.append(
            f"Invalid RISK_MODE '{RISK_MODE}'. Must be one of: {', '.join(valid_risk_modes)}"
        )
    
    # Validate BANKROLL_USD
    if BANKROLL_USD <= 0:
        errors.append(f"BANKROLL_USD must be positive, got: {BANKROLL_USD}")
    
    # Validate APP_PORT
    if not (1 <= APP_PORT <= 65535):
        errors.append(f"APP_PORT must be between 1 and 65535, got: {APP_PORT}")
    
    # Validate CURRENCY_MODE
    valid_currency_modes = {"real_money", "play_money"}
    if CURRENCY_MODE not in valid_currency_modes:
        errors.append(
            f"Invalid CURRENCY_MODE '{CURRENCY_MODE}'. Must be one of: {', '.join(valid_currency_modes)}"
        )
    
    # Warn about missing API keys (but don't fail - some operations might not need them)
    if not FUTUUR_PUBLIC_KEY:
        logger.warning("FUTUUR_PUBLIC_KEY is not set. API calls requiring authentication will fail.")
    if not FUTUUR_PRIVATE_KEY:
        logger.warning("FUTUUR_PRIVATE_KEY is not set. API calls requiring authentication will fail.")
    
    if errors:
        error_msg = "Configuration errors:\n" + "\n".join(f"  - {e}" for e in errors)
        logger.error(error_msg)
        raise ValueError(error_msg)


# Validate on import (can be disabled if needed)
try:
    validate_config()
except ValueError:
    # Allow import to succeed, but log the error
    # This way the app can still start and show the error
    pass
