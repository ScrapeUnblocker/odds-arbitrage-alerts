"""Find arbitrage and price moves in the collected odds, and decide what is new.

Arbitrage: a market where every outcome has a best price and those prices add up to
less than 100% implied probability (``sum(1 / decimal) < 1``). Backing every outcome,
each at its best bookmaker, with the stake split below returns the same amount
whichever outcome wins, and that amount is more than the total stake.

Price move: a selection's best price has moved by at least ``min_move_percent`` since
the price it was first seen at, or last alerted at.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .collect import Event, Market, Selection
from .config import Config

KEEP_DAYS = 2  # forget events not seen for this many days (finished or off the page)
_EPSILON = 1e-9


@dataclass(frozen=True)
class Leg:
    """One outcome of an arbitrage: where to back it and how much."""

    selection: str
    decimal: float
    fractional: str | None
    bookmakers: tuple[str, ...]
    stake: float
    returns: float


@dataclass(frozen=True)
class ArbAlert:
    key: str
    event: str
    event_url: str
    competition: str
    market: str
    start_utc: str | None
    start_local: str | None
    arbitrage_percent: float
    book_percent: float
    total_stake: float
    currency: str
    guaranteed_return: float
    profit: float
    legs: tuple[Leg, ...]
    previous_percent: float | None = None
    kind: str = "arbitrage"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MoveAlert:
    key: str
    event: str
    event_url: str
    competition: str
    market: str
    start_utc: str | None
    start_local: str | None
    selection: str
    old_odds: float
    new_odds: float
    change_percent: float
    fractional: str | None
    bookmakers: tuple[str, ...]
    since: str
    kind: str = "price_move"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


Alert = ArbAlert | MoveAlert


@dataclass
class Evaluation:
    alerts: list[Alert]
    state: dict[str, dict[str, Any]]
    events_checked: int = 0
    markets_checked: int = 0
    closest: list[tuple[float, str, str]] = field(default_factory=list)


def book_total(decimals: Iterable[float]) -> float:
    """Sum of implied probabilities. Below 1.0 means an arbitrage."""
    return sum(1.0 / d for d in decimals)


def split_stake(decimals: Sequence[float], total: float) -> list[float]:
    """Stakes (to the cent) that return the same amount whichever outcome wins."""
    book = book_total(decimals)
    stakes = [round(total / d / book, 2) for d in decimals]
    rest = round(total - sum(stakes), 2)
    if rest:
        biggest = max(range(len(stakes)), key=stakes.__getitem__)
        stakes[biggest] = round(stakes[biggest] + rest, 2)
    return stakes


def lines_match(selections: Sequence[Selection]) -> bool:
    """True when the selections cover every outcome of one line.

    Over/Under must share a line (Over 215.5 / Under 215.5); a two-way handicap must
    mirror it (-9 / +9). Markets without lines always match.
    """
    lines = [s.line for s in selections]
    if all(line is None for line in lines):
        return True
    if any(line is None for line in lines) or len(lines) != 2:
        return False
    try:
        first, second = (float(str(line)) for line in lines)
    except ValueError:
        return False
    if {s.name.casefold() for s in selections} == {"over", "under"}:
        return first == second
    return first == -second


def is_open(event: Event, now: datetime, skip_live: bool) -> bool:
    """Pre-match only (unless ``skip_live`` is off): in-play prices change by the second."""
    if not skip_live:
        return True
    if event.live:
        return False
    return not (event.start_utc and event.start_utc <= now)


def price_key(event: Event, market: Market, selection: Selection) -> str:
    return f"{event.id}|{market.name}|{selection.name}|{selection.line or ''}"


def arb_key(event: Event, market: Market) -> str:
    lines = "/".join(s.line for s in market.selections if s.line)
    return f"{event.id}|{market.name}|{lines}"


def competition_label(event: Event) -> str:
    comp = event.competition
    if not comp or event.page.casefold() in comp.casefold():
        return comp or event.page
    return f"{event.page} / {comp}"


def evaluate(
    events: Sequence[Event],
    state: Mapping[str, Mapping[str, Any]],
    config: Config,
    now: datetime,
    bookmakers: Mapping[str, str] | None = None,
) -> Evaluation:
    """Compare this run's prices with the state; return alerts and the new state."""
    names = dict(bookmakers or {})
    today = now.date().isoformat()
    stamp = now.strftime("%Y-%m-%dT%H:%MZ")
    prices: dict[str, Any] = dict(state.get("prices") or {})
    arbs: dict[str, Any] = dict(state.get("arbs") or {})
    arb_alerts: list[ArbAlert] = []
    move_alerts: list[MoveAlert] = []
    books: list[tuple[float, str, str]] = []
    result = Evaluation(alerts=[], state={})
    rules, moves = config.arbitrage, config.price_moves

    for event in events:
        if not is_open(event, now, config.skip_live):
            continue
        result.events_checked += 1
        for market in event.markets:
            if not config.wants_market(market.name):
                continue
            priced = [(s, s.best) for s in market.selections if s.best]

            for sel, best in priced:
                key = price_key(event, market, sel)
                new = best.decimal
                prev = prices.get(key)
                old = _odds(prev)
                if prev is None or old is None:
                    prices[key] = {"odds": new, "since": stamp, "seen": today}
                    continue
                change = (new - old) / old * 100
                if (
                    moves.enabled
                    and abs(change) + _EPSILON >= moves.min_move_percent
                    and min(old, new) <= moves.max_odds
                ):
                    move_alerts.append(
                        MoveAlert(
                            key=key,
                            event=event.name,
                            event_url=event.url,
                            competition=competition_label(event),
                            market=market.name,
                            start_utc=_iso(event.start_utc),
                            start_local=event.start_local,
                            selection=sel.label,
                            old_odds=old,
                            new_odds=new,
                            change_percent=round(change, 2),
                            fractional=best.fractional,
                            bookmakers=_names(best.bookmakers, names),
                            since=str(prev.get("since") or ""),
                        )
                    )
                    prices[key] = {"odds": new, "since": stamp, "seen": today}
                else:
                    prices[key] = {**prev, "seen": today}

            if len(priced) < 2 or len(priced) != len(market.selections):
                continue
            if not lines_match(market.selections):
                continue
            result.markets_checked += 1
            decimals = [best.decimal for _, best in priced]
            book = book_total(decimals)
            percent = round((1 - book) * 100, 3)
            books.append((round(book * 100, 2), event.name, market.name))
            key = arb_key(event, market)
            low, high = rules.min_arbitrage_percent, rules.max_arbitrage_percent
            if not (rules.enabled and percent > 0 and low - _EPSILON <= percent <= high + _EPSILON):
                arbs.pop(key, None)  # closed, or never was one
                continue
            prev = arbs.get(key)
            previous = _percent(prev)
            if previous is not None:
                moved = abs(percent - previous)
                if moved < _EPSILON or moved + _EPSILON < rules.realert_change:
                    arbs[key] = {**prev, "seen": today}
                    continue
            stakes = split_stake(decimals, rules.total_stake)
            legs = tuple(
                Leg(
                    selection=sel.label,
                    decimal=best.decimal,
                    fractional=best.fractional,
                    bookmakers=_names(best.bookmakers, names),
                    stake=stake,
                    returns=round(stake * best.decimal, 2),
                )
                for (sel, best), stake in zip(priced, stakes, strict=True)
            )
            guaranteed = min(leg.returns for leg in legs)
            arb_alerts.append(
                ArbAlert(
                    key=key,
                    event=event.name,
                    event_url=event.url,
                    competition=competition_label(event),
                    market=market.name,
                    start_utc=_iso(event.start_utc),
                    start_local=event.start_local,
                    arbitrage_percent=percent,
                    book_percent=round(book * 100, 3),
                    total_stake=rules.total_stake,
                    currency=rules.currency,
                    guaranteed_return=guaranteed,
                    profit=round(guaranteed - rules.total_stake, 2),
                    legs=legs,
                    previous_percent=previous,
                )
            )
            arbs[key] = {
                "percent": percent,
                "since": (prev or {}).get("since") or stamp,
                "seen": today,
                "event": event.name,
                "market": market.name,
            }

    cutoff = (now.date() - timedelta(days=KEEP_DAYS)).isoformat()
    result.state = {
        "prices": _prune(prices, cutoff),
        "arbs": _prune(arbs, cutoff),
    }
    arb_alerts.sort(key=lambda a: -a.arbitrage_percent)
    move_alerts.sort(key=lambda a: -abs(a.change_percent))
    result.alerts = [*arb_alerts, *move_alerts]
    result.closest = sorted(books)[:3]
    return result


def restore(
    new_state: dict[str, dict[str, Any]],
    old_state: Mapping[str, Mapping[str, Any]],
    alerts: Sequence[Alert],
) -> dict[str, dict[str, Any]]:
    """Undo the state change for undelivered alerts, so the next run sends them again."""
    restored = {name: dict(section) for name, section in new_state.items()}
    for alert in alerts:
        section = "arbs" if isinstance(alert, ArbAlert) else "prices"
        before = (old_state.get(section) or {}).get(alert.key)
        if before is None:
            restored.setdefault(section, {}).pop(alert.key, None)
        else:
            restored.setdefault(section, {})[alert.key] = before
    return restored


def _names(codes: Sequence[str], names: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(names.get(code, code) for code in codes))


def _odds(entry: Any) -> float | None:
    value = entry.get("odds") if isinstance(entry, dict) else None
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 1:
        return None
    return float(value)


def _percent(entry: Any) -> float | None:
    value = entry.get("percent") if isinstance(entry, dict) else None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _iso(value: datetime | None) -> str | None:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ") if value else None


def _prune(section: dict[str, Any], cutoff: str) -> dict[str, Any]:
    return {
        key: entry
        for key, entry in section.items()
        if isinstance(entry, dict) and str(entry.get("seen") or "") >= cutoff
    }
