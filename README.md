# Futuur Scanner

A betting scanner and recommendation system for Futuur prediction markets. Analyzes markets, computes Kelly Criterion-based recommendations, and provides both CLI and web interfaces.

## Features

- **Market Analysis**: Fetches markets from Futuur API and analyzes them using domain-specific heuristics
- **Kelly Criterion**: Computes optimal bet sizing using Kelly Criterion with configurable risk modes (full/half)
- **CLI Interface**: Command-line tool (`main.py`) that prints recommendations as a formatted table
- **Web Interface**: Flask web app (`web_app.py`) with filtering, sorting, and portfolio tracking
- **Portfolio Management**: View open bets, closed bets, and limit orders

## Setup

### Prerequisites

- Python 3.9+
- Virtual environment (recommended)

### Installation

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd futuur-scanner
   ```

2. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   # Windows
   .venv\Scripts\Activate.ps1
   # Linux/Mac
   source .venv/bin/activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Configure environment variables (create a `.env` file):
   ```env
   # Futuur API credentials
   FUTUUR_PUBLIC_KEY=your_public_key
   FUTUUR_PRIVATE_KEY=your_private_key
   
   # Optional configuration
   BANKROLL_USD=1000
   RISK_MODE=half  # Options: "half", "full"
   CURRENCY_MODE=real_money
   CURRENCY=USD
   
   # Web app configuration
   APP_HOST=0.0.0.0
   APP_PORT=10000
   ```

## Usage

### CLI Mode

Run the scanner from the command line:

```bash
python main.py
```

This will:
1. Load markets from available client modules
2. Compute recommendations using Kelly Criterion
3. Print a formatted table with recommendations

### Web App Mode

Start the Flask web server:

```bash
python web_app.py
```

Then open your browser to `http://localhost:10000` (or the configured port).

The web interface provides:
- **Markets View**: Browse and filter markets with search, volume filters, and sorting
- **Portfolio View**: Track your open bets, closed bets, and limit orders
- **CSV Export**: Export market data for analysis

## Architecture

### Core Components

- **`main.py`**: CLI entry point that loads markets and prints recommendations
- **`web_app.py`**: Flask web application with routes for markets and portfolio
- **`strategy.py`**: Core recommendation logic using Kelly Criterion
- **`models.py`**: Data models (Market, Recommendation, BetRow, etc.)
- **`futuur_client.py`**: Client for fetching markets from Futuur API
- **`portfolio_client.py`**: Client for fetching portfolio data
- **`futuur_api_raw.py`**: Low-level API client with authentication
- **`utils.py`**: Shared utility functions
- **`config.py`**: Configuration management

### Key Concepts

- **Market Price (s)**: The current market-implied probability (0-1)
- **Fair Probability (p0)**: Estimated true probability using domain heuristics
- **Edge**: The difference between fair probability and market price
- **Kelly Criterion**: Optimal bet sizing formula: `(p - s) / (1 - s)` for Yes bets
- **Risk Mode**: Multiplier on Kelly fraction (half = 0.5x, full = 1.0x)

## Configuration

### Risk Modes

- **`half`** (default): Uses 50% of full Kelly fraction (more conservative)
- **`full`**: Uses 100% of full Kelly fraction (more aggressive)

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BANKROLL_USD` | `1000` | Default bankroll for calculations |
| `RISK_MODE` | `half` | Risk mode: `half` or `full` |
| `CURRENCY_MODE` | `real_money` | Currency mode for API calls |
| `CURRENCY` | `USD` | Currency code |
| `FUTUUR_BASE_URL` | `https://api.futuur.com/api/v1/` | API base URL |
| `FUTUUR_PUBLIC_KEY` | (required) | API public key |
| `FUTUUR_PRIVATE_KEY` | (required) | API private key |
| `APP_HOST` | `0.0.0.0` | Web app host |
| `APP_PORT` | `10000` | Web app port |

## Development

### Project Structure

```
futuur-scanner/
├── main.py              # CLI entry point
├── web_app.py           # Flask web application
├── strategy.py          # Recommendation logic
├── models.py            # Data models
├── config.py            # Configuration
├── utils.py             # Shared utilities
├── futuur_client.py     # Market fetching client
├── portfolio_client.py  # Portfolio client
├── futuur_api_raw.py    # Low-level API client
├── gpt_client.py        # GPT integration (if used)
├── requirements.txt     # Python dependencies
└── README.md            # This file
```

### Testing

Run tests with pytest:

```bash
pytest
```

### Local automation prompts

When the OpenAI API quota is exhausted, `build_prompt.py` helps you reuse `PROMPTS.txt` to generate the text you would submit to ChatGPT manually. It understands both the `RESEARCH` and `ASSESS` templates so your automation flows always send the correct tone.

Example (using exported market data):

```bash
.venv\Scripts\python.exe build_prompt.py --mode research --json analysis_markets.json > prompt.txt
```

Copy the resulting prompt block into chat.openai.com (or drive it through a Playwright/Selenium script) to get the probabilities back, then paste the GPT output manually into the analysis table or store it in a file for later ingestion.

You can also point at a CSV export (`--csv path/to/export.csv`) or use `--sample` to see how the prompt looks before wiring it into automation.

The Analysis page now has a “Prepare Input” control: it generates the RESEARCH prompt for the selected markets, copies it to your clipboard, and lets you paste it directly into chat.openai.com when the API quota is exhausted. You can remove individual markets with the new “Remove” button, and after ChatGPT returns its response you paste the JSON into the “Import GPT analysis” textarea and click “Apply” to push the updated `p`, the reason, and any reported `max_avg_price`/`price_bought` back into the view. The Market page (home) also exposes a textarea/“Apply Market Analysis” button so you can submit the JSON there as well.

The Portfolio page similarly lets you “Prepare Input” (ASSESS) for open positions and has a dedicated textarea/button to paste ChatGPT’s JSON output; it records `price_bought`, `max_avg_price`, and the Data Integrity metadata you provide so the analysis surface can show those values even when the API is down.

### Code Style

The project uses:
- Python 3.9+ type hints
- Modern type annotations (`list[T]` instead of `List[T]`)
- Docstrings for public functions
- Consistent error handling

## License

[Add your license here]

## Contributing

[Add contribution guidelines here]
