"""Arbitrage and price-move alerts across bookmakers, from Oddschecker pages to Slack,
Discord, Telegram or a webhook.

Odds come from the ScrapeUnblocker Oddschecker API:
https://www.scrapeunblocker.com/scrapers/oddschecker?utm_source=github&utm_medium=integration&utm_campaign=example-repos
"""

from .collect import Event, Market, Price, Selection, collect
from .config import ArbitrageRules, Config, ConfigError, MoveRules, Page, load_config
from .process import ArbAlert, Leg, MoveAlert, evaluate, split_stake

__all__ = [
    "ArbAlert",
    "ArbitrageRules",
    "Config",
    "ConfigError",
    "Event",
    "Leg",
    "Market",
    "MoveAlert",
    "MoveRules",
    "Page",
    "Price",
    "Selection",
    "collect",
    "evaluate",
    "load_config",
    "split_stake",
]

__version__ = "0.1.0"
