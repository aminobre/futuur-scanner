from typing import List
from models import Market


def get_markets() -> List[Market]:
    """
    TEMP: returns dummy markets so you can test the pipeline.
    Replace this with a real Futuur API call once the rest works.
    """
    return [
        Market(
            id="dummy1",
            title="BTC > $100k by 2026",
            category="crypto",
            subcategory="price",
            yes_price=0.30,
            no_price=0.70,
            resolves_at=None,
            raw=None,
        ),
        Market(
            id="dummy2",
            title="US CPI YoY > 4% in 2026",
            category="macro",
            subcategory="inflation",
            yes_price=0.18,
            no_price=0.82,
            resolves_at=None,
            raw=None,
        ),
        Market(
            id="dummy3",
            title="Major US crypto ban by 2026",
            category="regulatory",
            subcategory="crypto",
            yes_price=0.08,
            no_price=0.92,
            resolves_at=None,
            raw=None,
        ),
    ]
