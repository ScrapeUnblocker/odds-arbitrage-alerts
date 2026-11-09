from datetime import UTC, datetime

import pytest
from conftest import (
    EPL,
    NBA,
    TENNIS,
    FakeClient,
    football_event,
    nba_event,
    page_payload,
    tennis_event,
)
from scrapeunblocker import (
    AuthenticationError,
    InvalidRequestError,
    ScrapeTimeoutError,
    ServerError,
)

from odds_arbitrage_alerts.collect import ENDPOINT, collect, parse_events
from odds_arbitrage_alerts.config import Page

EPL_PAGE = Page(EPL, "Premier League")
NBA_PAGE = Page(NBA, "NBA")
TENNIS_PAGE = Page(TENNIS, "Tennis")
NO_EVENTS = "No events on that page - pass a sport or competition page."


def test_parse_football_event():
    [event] = parse_events(page_payload(EPL, football_event()), EPL_PAGE)
    assert event.id == "102068370"
    assert event.name == "Chelsea vs Leeds"
    assert event.page == "Premier League"
    assert event.start_utc == datetime(2026, 11, 21, 15, 0, tzinfo=UTC)
    assert event.start_local is None
    assert event.live is False
    [market] = event.markets
    assert market.name == "Match Result"
    draw = market.selections[0]
    assert (draw.name, draw.line, draw.label) == ("Draw", None, "Draw")
    assert draw.best is not None
    assert (draw.best.decimal, draw.best.fractional) == (3.75, "11/4")
    assert draw.best.bookmakers == ("PP", "WH")


def test_parse_lines_and_local_times():
    [nba] = parse_events(page_payload(NBA, nba_event()), NBA_PAGE)
    spread = nba.markets[1]
    assert [s.label for s in spread.selections] == ["Indiana Pacers +2.5", "Dallas Mavericks -2.5"]
    assert nba.competition == "NBA Matches"

    [tennis] = parse_events(page_payload(TENNIS, tennis_event(live=True)), TENNIS_PAGE)
    assert tennis.start_utc is None
    assert tennis.start_local == "2026-11-09 13:30"
    assert tennis.live is True
    assert tennis.markets[0].selections[0].best.bookmakers == ()


def test_parse_skips_malformed_entries():
    event = football_event()
    event["markets"][0]["selections"][0]["best"]["decimal"] = 1.0
    event["markets"][0]["selections"][1]["best"] = {"decimal": "n/a"}
    event["markets"].append({"selections": []})
    payload = {"events": [event, {"name": "no id"}, "junk", {"id": 1}]}
    [parsed] = parse_events(payload, EPL_PAGE)
    selections = parsed.markets[0].selections
    assert [s.best is None for s in selections] == [True, True, False]
    assert len(parsed.markets) == 1


def test_date_without_time():
    event = tennis_event()
    del event["time"]
    [parsed] = parse_events(page_payload(TENNIS, event), TENNIS_PAGE)
    assert parsed.start_local == "2026-11-09"


def test_collect_calls_one_endpoint_per_page():
    client = FakeClient(
        {
            EPL: page_payload(EPL, football_event()),
            NBA: page_payload(NBA, nba_event()),
        }
    )
    result = collect(client, [EPL_PAGE, NBA_PAGE])
    assert client.calls == [(ENDPOINT, {"url": EPL}), (ENDPOINT, {"url": NBA})]
    assert [e.page for e in result.events] == ["Premier League", "NBA"]
    assert result.failures == []
    assert result.bookmakers["IVB"] == "IvyBet"
    assert result.bookmakers["ZZ"] == "Brand New Bet"  # learned from the response
    assert result.bookmakers["B3"] == "Bet365"  # from the built-in table


def test_transient_errors_are_retried():
    sleeps = []
    payload = page_payload(EPL, football_event())
    client = FakeClient(
        {EPL: [ServerError("bad gateway", status_code=502), ScrapeTimeoutError("slow"), payload]}
    )
    result = collect(client, [EPL_PAGE], sleep=sleeps.append)
    assert len(result.events) == 1
    assert sleeps == [5.0, 20.0]


def test_a_page_that_keeps_failing_is_skipped():
    errors = [ServerError("bad gateway", status_code=502) for _ in range(3)]
    client = FakeClient({EPL: errors, NBA: page_payload(NBA, nba_event())})
    result = collect(client, [EPL_PAGE, NBA_PAGE], sleep=lambda s: None)
    assert [p.label for p in result.pages_ok] == ["NBA"]
    [(page, error)] = result.failures
    assert page == EPL_PAGE and error.startswith("ServerError")
    assert len(client.calls) == 4


def test_no_events_page_is_not_retried():
    error = ServerError(f"API returned HTTP 502: {NO_EVENTS}", status_code=502, body=NO_EVENTS)
    client = FakeClient({EPL: [error]})
    result = collect(client, [EPL_PAGE], sleep=lambda s: pytest.fail("should not retry"))
    [(_, message)] = result.failures
    assert "no events on that page" in message
    assert len(client.calls) == 1


def test_invalid_request_is_not_retried():
    client = FakeClient({EPL: [InvalidRequestError("bad url", status_code=400)]})
    result = collect(client, [EPL_PAGE], sleep=lambda s: pytest.fail("should not retry"))
    assert result.failures[0][1].startswith("rejected")


def test_unexpected_payload_is_a_page_failure():
    client = FakeClient({EPL: {"url": EPL}})
    result = collect(client, [EPL_PAGE])
    assert result.failures[0][1].endswith("no 'events' list")


def test_bad_key_stops_the_run():
    client = FakeClient({EPL: AuthenticationError("invalid key", status_code=401)})
    with pytest.raises(AuthenticationError):
        collect(client, [EPL_PAGE])
