"""Shared fakes: no test calls the ScrapeUnblocker API or a real webhook."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from odds_arbitrage_alerts.config import Config, parse_config
from odds_arbitrage_alerts.process import ArbAlert, Leg, MoveAlert

NOW = datetime(2026, 11, 9, 13, 5, tzinfo=UTC)
EPL = "https://www.oddschecker.com/football/english/premier-league"
NBA = "https://www.oddschecker.com/basketball/nba"
TENNIS = "https://www.oddschecker.com/tennis"


def selection(name: str, decimal: float | None, fractional: str = "1/1", codes=(), **extra):
    sel: dict[str, Any] = {"name": name, **extra}
    if decimal is not None:
        sel["best"] = {"decimal": decimal, "fractional": fractional, "bookmakers": list(codes)}
    return sel


def football_event(
    event_id: int = 102068370,
    home: float = 1.91,
    draw: float = 3.75,
    away: float = 4.2,
    start: str = "2026-11-21T15:00:00Z",
) -> dict[str, Any]:
    """A trimmed Premier League event: best prices with bookmaker codes only."""
    return {
        "id": event_id,
        "name": "Chelsea vs Leeds",
        "home": "Chelsea",
        "away": "Leeds",
        "startTime": start,
        "live": False,
        "url": f"{EPL}/chelsea-v-leeds/winner",
        "markets": [
            {
                "name": "Match Result",
                "selections": [
                    selection("Draw", draw, "11/4", ["PP", "WH"], type="DRAW"),
                    selection("Chelsea", home, "10/11", ["CE"], type="HOME"),
                    selection("Leeds", away, "16/5", ["FR"], type="AWAY"),
                ],
            }
        ],
    }


def nba_event(
    win_away: float = 2.35,
    win_home: float = 1.72,
    over: float = 2.0,
    under: float = 1.94,
    spread: tuple[str, str] = ("+2.5", "-2.5"),
) -> dict[str, Any]:
    """A trimmed NBA event: lines, plus per-bookmaker odds lists with bookmaker names."""
    return {
        "id": 102100304,
        "name": "Indiana Pacers at Dallas Mavericks",
        "competition": "NBA Matches",
        "home": "Dallas Mavericks",
        "away": "Indiana Pacers",
        "startTime": "2026-11-10T01:40:00Z",
        "live": False,
        "url": f"{NBA}/indiana-pacers-at-dallas-mavericks/winner",
        "markets": [
            {
                "name": "Win Market",
                "selections": [
                    selection(
                        "Indiana Pacers",
                        win_away,
                        "27/20",
                        ["IVB"],
                        type="AWAY",
                        odds=[
                            {"bookmaker": "IVB", "bookmakerName": "IvyBet", "decimal": win_away},
                            {
                                "bookmaker": "ZZ",
                                "bookmakerName": "Brand New Bet",
                                "decimal": 2.0,
                            },
                        ],
                    ),
                    selection("Dallas Mavericks", win_home, "18/25", ["KN"], type="HOME"),
                ],
            },
            {
                "name": "Point Spread",
                "selections": [
                    selection("Indiana Pacers", 2.0, "1/1", ["WA"], line=spread[0], type="AWAY"),
                    selection("Dallas Mavericks", 1.9, "9/10", ["VC"], line=spread[1], type="HOME"),
                ],
            },
            {
                "name": "Total Points",
                "selections": [
                    selection("Over", over, "1/1", ["PP"], line="223.5"),
                    selection("Under", under, "15/16", ["UN"], line="223.5"),
                ],
            },
        ],
    }


def tennis_event(
    event_id: int = 3588627436,
    first: float = 3.0,
    second: float = 1.4,
    live: bool | None = None,
) -> dict[str, Any]:
    """A trimmed tennis event: date + time instead of startTime, no bookmaker codes."""
    event: dict[str, Any] = {
        "id": event_id,
        "name": "Adolfo Daniel Vallejo v Ugo Humbert",
        "competition": "ATP Stockholm",
        "home": "Adolfo Daniel Vallejo",
        "away": "Ugo Humbert",
        "date": "2026-11-09",
        "time": "13:30",
        "url": f"{TENNIS}/atp-stockholm/adolfo-daniel-vallejo-v-ugo-humbert/winner",
        "markets": [
            {
                "name": "Match Result",
                "selections": [
                    {
                        "name": "Adolfo Daniel Vallejo",
                        "best": {"decimal": first, "fractional": "2/1"},
                    },
                    {"name": "Ugo Humbert", "best": {"decimal": second, "fractional": "5/11"}},
                ],
            }
        ],
    }
    if live is not None:
        event["live"] = live
    return event


def page_payload(url: str, *events: dict[str, Any]) -> dict[str, Any]:
    return {"url": url, "events": [copy.deepcopy(e) for e in events], "totalEvents": len(events)}


class FakeClient:
    """Stands in for ``scrapeunblocker.Client``; answers ``plugin()`` calls by page URL.

    An answer may be a payload, an exception, or a list of those used one per call.
    """

    def __init__(self, responses: dict[str, Any]):
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def plugin(self, path: str, **params: Any):
        self.calls.append((path, params))
        answer = self.responses[params["url"]]
        if isinstance(answer, list):
            answer = answer.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return copy.deepcopy(answer)


@dataclass
class FakeResponse:
    status_code: int = 200
    text: str = "ok"
    headers: dict[str, str] = field(default_factory=dict)


class RecordingHttp:
    """Records every POST; replies with the queued responses (default 200)."""

    def __init__(self, responses: list[Any] | None = None):
        self.posts: list[tuple[str, Any]] = []
        self.responses = list(responses or [])

    def post(self, url: str, *, json: Any, timeout: float):
        self.posts.append((url, json))
        if self.responses:
            answer = self.responses.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
        return FakeResponse()


def make_config(**overrides: Any) -> Config:
    raw: dict[str, Any] = {
        "pages": [
            {"url": "football/english/premier-league", "name": "Premier League"},
            {"url": "basketball/nba", "name": "NBA"},
            {"url": "tennis", "name": "Tennis"},
        ],
        "destinations": ["slack", "webhook"],
    }
    raw.update(overrides)
    return parse_config(raw)


def make_arb(**overrides: Any) -> ArbAlert:
    fields: dict[str, Any] = {
        "key": "3588627436|Match Result|",
        "event": "Adolfo Daniel Vallejo v Ugo Humbert",
        "event_url": f"{TENNIS}/atp-stockholm/adolfo-daniel-vallejo-v-ugo-humbert/winner",
        "competition": "Tennis / ATP Stockholm",
        "market": "Match Result",
        "start_utc": None,
        "start_local": "2026-11-09 13:30",
        "arbitrage_percent": 3.633,
        "book_percent": 96.367,
        "total_stake": 100.0,
        "currency": "£",
        "guaranteed_return": 103.77,
        "profit": 3.77,
        "legs": (
            Leg("Adolfo Daniel Vallejo", 2.1, "11/10", ("Bet365",), 49.41, 103.76),
            Leg("Ugo Humbert", 2.05, "21/20", ("Unibet", "Coral"), 50.59, 103.71),
        ),
    }
    fields.update(overrides)
    return ArbAlert(**fields)


def make_move(**overrides: Any) -> MoveAlert:
    fields: dict[str, Any] = {
        "key": "102100304|Win Market|Indiana Pacers|",
        "event": "Indiana Pacers at Dallas Mavericks",
        "event_url": f"{NBA}/indiana-pacers-at-dallas-mavericks/winner",
        "competition": "NBA Matches",
        "market": "Win Market",
        "start_utc": "2026-11-10T01:40:00Z",
        "start_local": None,
        "selection": "Indiana Pacers",
        "old_odds": 2.35,
        "new_odds": 2.6,
        "change_percent": 10.64,
        "fractional": "8/5",
        "bookmakers": ("IvyBet",),
        "since": "2026-11-09T09:05Z",
    }
    fields.update(overrides)
    return MoveAlert(**fields)
