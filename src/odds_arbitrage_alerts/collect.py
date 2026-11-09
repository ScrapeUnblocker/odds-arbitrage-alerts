"""Fetch events and best prices from Oddschecker pages through the ScrapeUnblocker API.

One request per page: ``POST /sports/oddschecker-events?url=<sport or competition page>``.
The SDK has no dedicated method for this endpoint yet, so it goes through the SDK's
generic ``client.plugin(path, **params)`` call, which handles the key, errors and timeouts.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from scrapeunblocker import (
    AuthenticationError,
    BlockedError,
    BrowserTimeoutError,
    InvalidRequestError,
    PaymentRequiredError,
    RateLimitError,
    ScrapeTimeoutError,
    ScrapeUnblockerError,
    ServerError,
    UpstreamOutageError,
)
from scrapeunblocker import ConnectionError as SUConnectionError

from .config import Page

log = logging.getLogger(__name__)

ENDPOINT = "/sports/oddschecker-events"

# Errors that end the whole run: retrying or moving on to the next page cannot help.
FATAL = (AuthenticationError, PaymentRequiredError)
# Errors worth one more try after a pause.
RETRYABLE = (
    ServerError,
    UpstreamOutageError,
    RateLimitError,
    BlockedError,
    BrowserTimeoutError,
    ScrapeTimeoutError,
    SUConnectionError,
)
RETRY_DELAYS = (5.0, 20.0)
NO_EVENTS = "no events on that page"

# Bookmaker codes as the API names them in its per-bookmaker odds lists. Some pages only
# return codes for the best price; this table turns them into names. Codes seen in a
# response's own odds lists take precedence, and an unknown code is shown as is.
BOOKMAKERS = {
    "AKB": "AK Bets",
    "B3": "Bet365",
    "BAH": "BetAhoy",
    "BF": "Betfair",
    "BTT": "BetTom",
    "BY": "BOYLE Sports",
    "CE": "Coral",
    "FB": "Betfair Sportsbook",
    "FR": "Betfred",
    "G5": "BetGoodwin",
    "IVB": "IvyBet",
    "KN": "BetMGM UK",
    "LD": "Ladbrokes",
    "MA": "Matchbook",
    "OE": "10bet",
    "PP": "Paddy Power",
    "PUP": "PricedUp",
    "QN": "QuinnBet",
    "S6": "Star Sports",
    "SI": "Sporting Index",
    "SK": "Skybet",
    "SX": "Spreadex",
    "UN": "Unibet",
    "VC": "Bet Victor",
    "WA": "Betway",
    "WH": "William Hill",
}


class PluginClient(Protocol):
    """The part of ``scrapeunblocker.Client`` this package uses."""

    def plugin(self, path: str, **params: Any) -> Any: ...


@dataclass(frozen=True)
class Price:
    """The best price for one selection and the bookmakers offering it."""

    decimal: float
    fractional: str | None = None
    bookmakers: tuple[str, ...] = ()


@dataclass(frozen=True)
class Selection:
    name: str
    line: str | None = None
    best: Price | None = None

    @property
    def label(self) -> str:
        """``Over 215.5``, ``Cleveland Cavaliers -9`` or just ``Draw``."""
        return f"{self.name} {self.line}" if self.line else self.name


@dataclass(frozen=True)
class Market:
    name: str
    selections: tuple[Selection, ...]


@dataclass(frozen=True)
class Event:
    id: str
    name: str
    url: str
    page: str
    competition: str | None = None
    start_utc: datetime | None = None
    start_local: str | None = None
    live: bool = False
    markets: tuple[Market, ...] = ()


@dataclass
class CollectResult:
    events: list[Event] = field(default_factory=list)
    pages_ok: list[Page] = field(default_factory=list)
    failures: list[tuple[Page, str]] = field(default_factory=list)
    bookmakers: dict[str, str] = field(default_factory=lambda: dict(BOOKMAKERS))


def collect(
    client: PluginClient,
    pages: Sequence[Page],
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> CollectResult:
    """Fetch every page. A page that keeps failing is recorded in ``failures`` and skipped."""
    result = CollectResult()
    for page in pages:
        try:
            payload = fetch_page(client, page, sleep=sleep)
        except FATAL:
            raise
        except ScrapeUnblockerError as exc:
            result.failures.append((page, describe_error(exc)))
            continue
        result.pages_ok.append(page)
        result.events.extend(parse_events(payload, page))
        result.bookmakers.update(bookmaker_names(payload))
    return result


def fetch_page(
    client: PluginClient, page: Page, *, sleep: Callable[[float], None] = time.sleep
) -> dict[str, Any]:
    """One page, with up to two retries on transient errors."""
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            log.info("POST %s url=%s", ENDPOINT, page.url)
            payload = client.plugin(ENDPOINT, url=page.url)
        except RETRYABLE as exc:
            if _is_no_events(exc) or attempt == len(RETRY_DELAYS):
                raise
            log.warning("%s: %s, retrying", page.url, type(exc).__name__)
            sleep(RETRY_DELAYS[attempt])
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
            raise ScrapeUnblockerError("unexpected response: no 'events' list")
        return payload
    raise AssertionError("unreachable")  # pragma: no cover


def _is_no_events(exc: Exception) -> bool:
    body = getattr(exc, "body", None) or str(exc)
    return NO_EVENTS in str(body).lower()


def describe_error(exc: ScrapeUnblockerError) -> str:
    if _is_no_events(exc):
        return "no events on that page (is it a sport or competition page?)"
    if isinstance(exc, InvalidRequestError):
        return f"rejected: {exc}"
    return f"{type(exc).__name__}: {exc}"


def bookmaker_names(payload: dict[str, Any]) -> dict[str, str]:
    """``{code: name}`` from the per-bookmaker odds lists some pages include."""
    names: dict[str, str] = {}
    for event in payload.get("events") or []:
        for market in _list(event, "markets"):
            for selection in _list(market, "selections"):
                for odds in _list(selection, "odds"):
                    code, name = odds.get("bookmaker"), odds.get("bookmakerName")
                    if isinstance(code, str) and isinstance(name, str) and name.strip():
                        names[code] = name.strip()
    return names


def parse_events(payload: dict[str, Any], page: Page) -> list[Event]:
    """Shape the API response into :class:`Event` objects, skipping malformed entries."""
    events: list[Event] = []
    for raw in payload.get("events") or []:
        if not isinstance(raw, dict) or raw.get("id") is None or not raw.get("name"):
            continue
        markets = tuple(m for m in (_parse_market(m) for m in _list(raw, "markets")) if m)
        start_utc = _parse_utc(raw.get("startTime"))
        events.append(
            Event(
                id=str(raw["id"]),
                name=str(raw["name"]).strip(),
                url=str(raw.get("url") or page.url),
                page=page.label,
                competition=_text(raw.get("competition")),
                start_utc=start_utc,
                start_local=None if start_utc else _local_start(raw),
                live=bool(raw.get("live")),
                markets=markets,
            )
        )
    return events


def _parse_market(raw: Any) -> Market | None:
    if not isinstance(raw, dict) or not raw.get("name"):
        return None
    selections = []
    for sel in _list(raw, "selections"):
        if not sel.get("name"):
            continue
        selections.append(
            Selection(
                name=str(sel["name"]).strip(),
                line=_text(sel.get("line")),
                best=_parse_price(sel.get("best")),
            )
        )
    return Market(name=str(raw["name"]).strip(), selections=tuple(selections))


def _parse_price(raw: Any) -> Price | None:
    if not isinstance(raw, dict):
        return None
    decimal = raw.get("decimal")
    if isinstance(decimal, bool) or not isinstance(decimal, int | float) or decimal <= 1:
        return None
    codes = raw.get("bookmakers") or []
    return Price(
        decimal=float(decimal),
        fractional=_text(raw.get("fractional")),
        bookmakers=tuple(str(c) for c in codes if isinstance(c, str) and c),
    )


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _local_start(raw: dict[str, Any]) -> str | None:
    """Pages without ``startTime`` give ``date`` (and often ``time``) as the site lists them."""
    day = _text(raw.get("date"))
    if not day:
        return None
    clock = _text(raw.get("time"))
    return f"{day} {clock}" if clock else day


def _list(raw: Any, key: str) -> list[dict[str, Any]]:
    value = raw.get(key) if isinstance(raw, dict) else None
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
