"""Load and validate ``config.yml``, the one file a user edits."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

SITE = "https://www.oddschecker.com"
DESTINATIONS = ("slack", "discord", "telegram", "webhook")

_TOP_KEYS = {
    "pages",
    "arbitrage",
    "price_moves",
    "markets",
    "skip_live",
    "destinations",
    "state_file",
}
_PAGE_KEYS = {"url", "name"}
_ARB_KEYS = {
    "enabled",
    "min_arbitrage_percent",
    "max_arbitrage_percent",
    "total_stake",
    "currency",
    "realert_change",
}
_MOVE_KEYS = {"enabled", "min_move_percent", "max_odds"}


class ConfigError(ValueError):
    """The config file is missing, unreadable or has an invalid value."""


@dataclass(frozen=True)
class Page:
    """One Oddschecker sport or competition page: one API request per run."""

    url: str
    name: str | None = None

    @property
    def path(self) -> str:
        return urlparse(self.url).path.strip("/")

    @property
    def label(self) -> str:
        """The configured name, or the page path made readable."""
        if self.name:
            return self.name
        last = self.path.rsplit("/", 1)[-1]
        return last.replace("-", " ").title() or self.url


@dataclass(frozen=True)
class ArbitrageRules:
    enabled: bool = True
    min_arbitrage_percent: float = 0.5
    max_arbitrage_percent: float = 10.0
    total_stake: float = 100.0
    currency: str = "£"
    realert_change: float = 0.5


@dataclass(frozen=True)
class MoveRules:
    enabled: bool = True
    min_move_percent: float = 10.0
    max_odds: float = 10.0


@dataclass(frozen=True)
class Config:
    pages: tuple[Page, ...]
    arbitrage: ArbitrageRules
    price_moves: MoveRules
    markets: tuple[str, ...]
    skip_live: bool
    destinations: tuple[str, ...]
    state_file: Path

    def wants_market(self, name: str) -> bool:
        return not self.markets or name.casefold() in {m.casefold() for m in self.markets}


def normalize_page_url(value: str) -> str:
    """Accept ``football/english/premier-league`` or a full oddschecker.com link."""
    text = value.strip()
    if not text:
        raise ConfigError("a page URL is empty")
    if "://" not in text:
        text = text.lstrip("/")
        if text.startswith(("www.oddschecker.com/", "oddschecker.com/")):
            text = "https://" + text
        else:
            text = f"{SITE}/{text}"
    parsed = urlparse(text)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or host not in (
        "oddschecker.com",
        "www.oddschecker.com",
    ):
        raise ConfigError(f"{value!r} is not an oddschecker.com page")
    path = parsed.path.strip("/")
    if not path:
        raise ConfigError(f"{value!r}: give a sport or competition page, e.g. basketball/nba")
    if path.rsplit("/", 1)[-1] == "winner":
        raise ConfigError(
            f"{value!r} is a single event; use its sport or competition page "
            f"(e.g. {path.rsplit('/', 2)[0]})"
        )
    return f"{SITE}/{path}"


def load_config(path: str | Path) -> Config:
    """Read and validate a config file. A relative ``state_file`` resolves next to it."""
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from None
    config = parse_config(raw or {})
    if not config.state_file.is_absolute():
        config = replace(config, state_file=path.parent / config.state_file)
    return config


def parse_config(raw: Any) -> Config:
    if not isinstance(raw, dict):
        raise ConfigError("the config must be a mapping with a 'pages' list")
    _reject_unknown(raw, _TOP_KEYS, "config")

    pages_raw = raw.get("pages")
    if not isinstance(pages_raw, list) or not pages_raw:
        raise ConfigError("'pages' must be a non-empty list of Oddschecker pages")
    pages: list[Page] = []
    seen: set[str] = set()
    for index, entry in enumerate(pages_raw, start=1):
        page = _parse_page(entry, index)
        if page.url in seen:
            raise ConfigError(f"page {index}: {page.url} is listed twice")
        seen.add(page.url)
        pages.append(page)

    markets = raw.get("markets") or []
    if isinstance(markets, str):
        markets = [markets]
    if not isinstance(markets, list) or not all(isinstance(m, str) and m.strip() for m in markets):
        raise ConfigError("'markets' must be a list of market names, e.g. ['Match Result']")

    skip_live = raw.get("skip_live", True)
    if not isinstance(skip_live, bool):
        raise ConfigError("'skip_live' must be true or false")

    destinations = raw.get("destinations", list(DESTINATIONS))
    if isinstance(destinations, str):
        destinations = [destinations]
    if not isinstance(destinations, list) or not destinations:
        raise ConfigError(f"'destinations' must be a list, any of: {', '.join(DESTINATIONS)}")
    for name in destinations:
        if name not in DESTINATIONS:
            raise ConfigError(
                f"unknown destination {name!r}; use any of: {', '.join(DESTINATIONS)}"
            )

    state_file = raw.get("state_file", "state/odds.json")
    if not isinstance(state_file, str) or not state_file.strip():
        raise ConfigError("'state_file' must be a file path")

    return Config(
        pages=tuple(pages),
        arbitrage=_parse_arbitrage(raw.get("arbitrage") or {}),
        price_moves=_parse_moves(raw.get("price_moves") or {}),
        markets=tuple(m.strip() for m in markets),
        skip_live=skip_live,
        destinations=tuple(dict.fromkeys(destinations)),
        state_file=Path(state_file),
    )


def _parse_page(raw: Any, index: int) -> Page:
    where = f"page {index}"
    if isinstance(raw, str):
        raw = {"url": raw}
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: expected a page path or a mapping with 'url'")
    _reject_unknown(raw, _PAGE_KEYS, where)
    if not isinstance(raw.get("url"), str):
        raise ConfigError(f"{where}: 'url' is required")
    name = raw.get("name")
    if name is not None and not isinstance(name, str):
        raise ConfigError(f"{where}: 'name' must be text")
    try:
        url = normalize_page_url(raw["url"])
    except ConfigError as exc:
        raise ConfigError(f"{where}: {exc}") from None
    return Page(url=url, name=name.strip() if name and name.strip() else None)


def _parse_arbitrage(raw: Any) -> ArbitrageRules:
    if not isinstance(raw, dict):
        raise ConfigError("'arbitrage' must be a mapping")
    _reject_unknown(raw, _ARB_KEYS, "arbitrage")
    d = ArbitrageRules()
    rules = ArbitrageRules(
        enabled=_bool(raw.get("enabled", d.enabled), "arbitrage.enabled"),
        min_arbitrage_percent=_number(
            raw.get("min_arbitrage_percent", d.min_arbitrage_percent),
            "arbitrage.min_arbitrage_percent",
            low=0,
            high=50,
        ),
        max_arbitrage_percent=_number(
            raw.get("max_arbitrage_percent", d.max_arbitrage_percent),
            "arbitrage.max_arbitrage_percent",
            low=0,
            high=100,
        ),
        total_stake=_number(
            raw.get("total_stake", d.total_stake), "arbitrage.total_stake", low=1, high=10**9
        ),
        currency=_currency(raw.get("currency", d.currency)),
        realert_change=_number(
            raw.get("realert_change", d.realert_change), "arbitrage.realert_change", low=0, high=100
        ),
    )
    if rules.max_arbitrage_percent <= rules.min_arbitrage_percent:
        raise ConfigError(
            "arbitrage.max_arbitrage_percent must be larger than min_arbitrage_percent"
        )
    return rules


def _parse_moves(raw: Any) -> MoveRules:
    if not isinstance(raw, dict):
        raise ConfigError("'price_moves' must be a mapping")
    _reject_unknown(raw, _MOVE_KEYS, "price_moves")
    d = MoveRules()
    move = _number(
        raw.get("min_move_percent", d.min_move_percent), "price_moves.min_move_percent", 0, 1000
    )
    if move <= 0:
        raise ConfigError("price_moves.min_move_percent must be above 0")
    max_odds = _number(raw.get("max_odds", d.max_odds), "price_moves.max_odds", 1, 10**6)
    if max_odds <= 1:
        raise ConfigError("price_moves.max_odds must be above 1.0 (decimal odds)")
    return MoveRules(
        enabled=_bool(raw.get("enabled", d.enabled), "price_moves.enabled"),
        min_move_percent=move,
        max_odds=max_odds,
    )


def _number(value: Any, where: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not low <= value <= high:
        raise ConfigError(f"{where} must be a number from {low:g} to {high:g}")
    return float(value)


def _bool(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{where} must be true or false")
    return value


def _currency(value: Any) -> str:
    if not isinstance(value, str) or len(value.strip()) > 5:
        raise ConfigError("arbitrage.currency must be a short label such as '£', '€', '$' or 'USD'")
    return value.strip()


def _reject_unknown(raw: dict, allowed: set[str], where: str) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ConfigError(
            f"{where}: unknown key(s) {', '.join(map(repr, unknown))}; "
            f"allowed: {', '.join(sorted(allowed))}"
        )
