from dataclasses import replace
from datetime import timedelta

import pytest
from conftest import (
    EPL,
    NBA,
    NOW,
    TENNIS,
    football_event,
    make_config,
    nba_event,
    page_payload,
    tennis_event,
)

from odds_arbitrage_alerts.collect import BOOKMAKERS, Selection, bookmaker_names, parse_events
from odds_arbitrage_alerts.config import Page
from odds_arbitrage_alerts.process import (
    ArbAlert,
    MoveAlert,
    book_total,
    evaluate,
    lines_match,
    restore,
    split_stake,
)
from odds_arbitrage_alerts.state import empty_state

EPL_PAGE = Page(EPL, "Premier League")
NBA_PAGE = Page(NBA, "NBA")
TENNIS_PAGE = Page(TENNIS, "Tennis")


def events_of(*pairs):
    out = []
    for page, payload in pairs:
        out.extend(parse_events(payload, page))
    return out


def tennis(**kwargs):
    return events_of((TENNIS_PAGE, page_payload(TENNIS, tennis_event(**kwargs))))


def run(events, state=None, config=None, now=NOW, names=None):
    return evaluate(events, state or empty_state(), config or make_config(), now, names)


# --- the arithmetic ---------------------------------------------------------------


def test_book_total():
    assert book_total([2.0, 2.0]) == pytest.approx(1.0)
    assert book_total([2.1, 2.05]) == pytest.approx(0.96399, abs=1e-5)


def test_split_stake_returns_the_same_whichever_wins():
    stakes = split_stake([2.1, 2.05], 100)
    assert sum(stakes) == pytest.approx(100)
    returns = [stakes[0] * 2.1, stakes[1] * 2.05]
    assert max(returns) - min(returns) < 0.05
    assert min(returns) > 100


def test_split_stake_three_way_absorbs_rounding():
    stakes = split_stake([3.0, 3.0, 3.0], 100)
    assert sum(stakes) == pytest.approx(100)
    assert sorted(stakes) == [33.33, 33.33, 33.34]


@pytest.mark.parametrize(
    ("sels", "expected"),
    [
        ([Selection("A"), Selection("B")], True),
        ([Selection("Over", "215.5"), Selection("Under", "215.5")], True),
        ([Selection("Over", "215.5"), Selection("Under", "216.5")], False),
        ([Selection("Team A", "-9"), Selection("Team B", "+9")], True),
        ([Selection("Team A", "-9"), Selection("Team B", "+9.5")], False),
        ([Selection("Team A", "-9"), Selection("Team B", "-9")], False),
        ([Selection("Team A", "-9"), Selection("Team B")], False),
        ([Selection("Team A", "PK"), Selection("Team B", "PK")], False),
    ],
)
def test_lines_match(sels, expected):
    assert lines_match(sels) is expected


# --- arbitrage --------------------------------------------------------------------


def test_finds_an_arbitrage_with_the_stake_split():
    result = run(tennis(first=2.1, second=2.05))
    [alert] = result.alerts
    assert isinstance(alert, ArbAlert)
    assert alert.arbitrage_percent == pytest.approx(3.6, abs=0.001)
    assert alert.book_percent == pytest.approx(96.4, abs=0.001)
    assert [leg.selection for leg in alert.legs] == ["Adolfo Daniel Vallejo", "Ugo Humbert"]
    assert sum(leg.stake for leg in alert.legs) == pytest.approx(100)
    assert alert.guaranteed_return == min(leg.returns for leg in alert.legs)
    assert alert.profit == pytest.approx(alert.guaranteed_return - 100)
    assert alert.competition == "Tennis / ATP Stockholm"
    assert alert.start_local == "2026-11-09 13:30"
    assert result.state["arbs"]["3588627436|Match Result|"]["percent"] == alert.arbitrage_percent


def test_no_arbitrage_on_a_normal_market():
    result = run(tennis())  # 3.0 / 1.4 adds up to 104.76%
    assert result.alerts == []
    assert result.state["arbs"] == {}
    assert result.closest == [(104.76, "Adolfo Daniel Vallejo v Ugo Humbert", "Match Result")]


def test_second_run_with_the_same_prices_sends_nothing():
    first = run(tennis(first=2.1, second=2.05))
    assert len(first.alerts) == 1
    second = run(tennis(first=2.1, second=2.05), state=first.state)
    assert second.alerts == []
    assert second.state == first.state


def test_open_arbitrage_is_resent_only_when_it_moves_enough():
    first = run(tennis(first=2.1, second=2.05))  # 3.60%
    small = run(tennis(first=2.11, second=2.05), state=first.state)  # 3.83%: < 0.5 points
    assert small.alerts == []
    big = run(tennis(first=2.2, second=2.05), state=small.state)  # 5.80%
    [alert] = [a for a in big.alerts if isinstance(a, ArbAlert)]
    assert alert.previous_percent == pytest.approx(3.6, abs=0.001)
    assert big.state["arbs"][alert.key]["since"] == first.state["arbs"][alert.key]["since"]


def test_closed_arbitrage_alerts_again_when_it_reopens():
    first = run(tennis(first=2.1, second=2.05))
    closed = run(tennis(first=1.9, second=2.0), state=first.state)
    assert closed.state["arbs"] == {}
    reopened = run(tennis(first=2.1, second=2.05), state=closed.state)
    assert [type(a) for a in reopened.alerts if isinstance(a, ArbAlert)] == [ArbAlert]


def test_thresholds_bound_the_arbitrage():
    config = make_config(arbitrage={"min_arbitrage_percent": 5, "max_arbitrage_percent": 10})
    assert run(tennis(first=2.1, second=2.05), config=config).alerts == []  # 3.6% < 5
    # 40% "arbitrage": a mistaken price or a missing outcome, not something to bet on.
    assert run(tennis(first=5.0, second=3.0), config=config).alerts == []


def test_zero_min_alerts_on_any_arbitrage_but_not_on_break_even():
    config = make_config(arbitrage={"min_arbitrage_percent": 0})
    assert run(tennis(first=2.0, second=2.0), config=config).alerts == []
    [alert] = run(tennis(first=2.02, second=2.0), config=config).alerts
    assert alert.arbitrage_percent == pytest.approx(0.495, abs=0.001)


def test_incomplete_markets_are_not_arbitrage():
    event = tennis_event(first=5.0, second=3.0)
    del event["markets"][0]["selections"][1]["best"]
    assert run(events_of((TENNIS_PAGE, page_payload(TENNIS, event)))).alerts == []
    event["markets"][0]["selections"].pop()
    assert run(events_of((TENNIS_PAGE, page_payload(TENNIS, event)))).alerts == []


def test_three_way_arbitrage_and_bookmaker_names():
    events = events_of((EPL_PAGE, page_payload(EPL, football_event(home=2.3, draw=4.0, away=4.2))))
    [alert] = run(events, names=BOOKMAKERS).alerts
    assert [leg.bookmakers for leg in alert.legs] == [
        ("Paddy Power", "William Hill"),
        ("Coral",),
        ("Betfred",),
    ]
    assert alert.competition == "Premier League"
    assert alert.start_utc == "2026-11-21T15:00:00Z"


def test_unknown_bookmaker_codes_are_kept_as_codes():
    events = events_of((EPL_PAGE, page_payload(EPL, football_event(home=2.3, draw=4.0, away=4.2))))
    [alert] = run(events, names={"CE": "Coral"}).alerts
    assert alert.legs[0].bookmakers == ("PP", "WH")


def test_names_from_the_response_odds_lists():
    payload = page_payload(NBA, nba_event())
    names = {**BOOKMAKERS, **bookmaker_names(payload)}
    assert names["ZZ"] == "Brand New Bet"
    assert names["IVB"] == "IvyBet"


def test_lines_must_cover_both_sides():
    payload = page_payload(NBA, nba_event(spread=("+2.5", "-3.5")))
    result = run(events_of((NBA_PAGE, payload)))
    assert all(market != "Point Spread" for _, _, market in result.closest)
    assert result.markets_checked == 2


def test_totals_arbitrage_key_includes_the_line():
    payload = page_payload(NBA, nba_event(over=2.1, under=2.05))
    [alert] = run(events_of((NBA_PAGE, payload))).alerts
    assert alert.key == "102100304|Total Points|223.5/223.5"
    assert [leg.selection for leg in alert.legs] == ["Over 223.5", "Under 223.5"]


def test_disabled_arbitrage_sends_nothing():
    config = make_config(arbitrage={"enabled": False})
    assert run(tennis(first=2.1, second=2.05), config=config).alerts == []


# --- price moves ------------------------------------------------------------------


def test_first_run_only_records_reference_prices():
    result = run(tennis())
    assert result.alerts == []
    entry = result.state["prices"]["3588627436|Match Result|Adolfo Daniel Vallejo|"]
    assert entry == {"odds": 3.0, "since": "2026-11-09T13:05Z", "seen": "2026-11-09"}


def test_a_big_move_alerts_and_resets_the_reference():
    first = run(tennis(first=3.0))
    later = NOW + timedelta(hours=2)
    moved = run(tennis(first=3.4), state=first.state, now=later)
    [alert] = moved.alerts
    assert isinstance(alert, MoveAlert)
    assert (alert.old_odds, alert.new_odds) == (3.0, 3.4)
    assert alert.change_percent == pytest.approx(13.33)
    assert alert.since == "2026-11-09T13:05Z"
    key = "3588627436|Match Result|Adolfo Daniel Vallejo|"
    assert moved.state["prices"][key] == {
        "odds": 3.4,
        "since": "2026-11-09T15:05Z",
        "seen": "2026-11-09",
    }
    assert run(tennis(first=3.4), state=moved.state, now=later).alerts == []


def test_small_moves_add_up_against_the_reference():
    state = run(tennis(first=3.0)).state
    state = run(tennis(first=3.15), state=state).state  # +5%: no alert, reference stays 3.0
    assert state["prices"]["3588627436|Match Result|Adolfo Daniel Vallejo|"]["odds"] == 3.0
    [alert] = run(tennis(first=3.3), state=state).alerts  # +10% against 3.0
    assert alert.change_percent == pytest.approx(10.0)


def test_shortening_by_exactly_the_threshold_alerts():
    state = run(tennis(first=2.0)).state
    [alert] = run(tennis(first=1.8), state=state).alerts
    assert alert.change_percent == pytest.approx(-10.0)


def test_long_shots_are_ignored():
    config = make_config(price_moves={"max_odds": 10})
    state = run(tennis(first=12.0), config=config).state
    assert run(tennis(first=15.0), state=state, config=config).alerts == []
    [alert] = run(tennis(first=9.0), state=state, config=config).alerts  # comes into range
    assert alert.new_odds == 9.0


def test_disabled_price_moves_send_nothing():
    config = make_config(price_moves={"enabled": False})
    state = run(tennis(first=3.0), config=config).state
    assert run(tennis(first=2.5), state=state, config=config).alerts == []


def test_a_new_line_starts_a_new_reference():
    first = run(events_of((NBA_PAGE, page_payload(NBA, nba_event()))))
    moved_line = nba_event(spread=("+5.5", "-5.5"))
    result = run(events_of((NBA_PAGE, page_payload(NBA, moved_line))), state=first.state)
    assert result.alerts == []
    assert "102100304|Point Spread|Indiana Pacers|+5.5" in result.state["prices"]


# --- what gets checked ------------------------------------------------------------


def test_live_and_started_events_are_skipped():
    assert run(tennis(first=2.1, second=2.05, live=True)).alerts == []
    started = football_event(home=2.3, draw=4.0, away=4.2, start="2026-11-09T12:30:00Z")
    result = run(events_of((EPL_PAGE, page_payload(EPL, started))))
    assert result.alerts == [] and result.events_checked == 0


def test_skip_live_off_checks_in_play_events_too():
    config = make_config(skip_live=False)
    assert len(run(tennis(first=2.1, second=2.05, live=True), config=config).alerts) == 1


def test_market_filter():
    payload = page_payload(NBA, nba_event(over=2.1, under=2.05))
    config = make_config(markets=["win market"])
    result = run(events_of((NBA_PAGE, payload)), config=config)
    assert result.alerts == []
    assert all(key.split("|")[1] == "Win Market" for key in result.state["prices"])


# --- state upkeep -----------------------------------------------------------------


def test_events_not_seen_for_days_are_forgotten():
    state = run(tennis()).state
    later = run([], state=state, now=NOW + timedelta(days=2))
    assert later.state["prices"]  # seen 2 days ago: kept
    gone = run([], state=state, now=NOW + timedelta(days=3))
    assert gone.state == {"prices": {}, "arbs": {}}


def test_restore_puts_undelivered_alerts_back():
    base = run(tennis(first=3.0)).state
    result = run(tennis(first=2.1, second=2.05), state=base)
    assert {type(a) for a in result.alerts} == {ArbAlert, MoveAlert}
    restored = restore(result.state, base, result.alerts)
    assert restored == base
    assert len(run(tennis(first=2.1, second=2.05), state=restored).alerts) == len(result.alerts)


def test_alert_order_arbitrage_first_then_biggest_move():
    state = run(events_of((NBA_PAGE, page_payload(NBA, nba_event()))) + tennis()).state
    events = events_of((NBA_PAGE, page_payload(NBA, nba_event(win_away=2.5, win_home=1.75))))
    events += tennis(first=2.1, second=2.05)
    alerts = run(events, state=state).alerts
    kinds = [a.kind for a in alerts]
    assert kinds[:2] == ["arbitrage", "arbitrage"]
    moves = [a for a in alerts if isinstance(a, MoveAlert)]
    assert [abs(m.change_percent) for m in moves] == sorted(
        (abs(m.change_percent) for m in moves), reverse=True
    )
    assert alerts[0].arbitrage_percent >= alerts[1].arbitrage_percent


def test_to_dict_is_json_ready():
    import json

    [alert] = run(tennis(first=2.1, second=2.05)).alerts
    data = json.loads(json.dumps(alert.to_dict()))
    assert data["kind"] == "arbitrage"
    assert data["legs"][0]["selection"] == "Adolfo Daniel Vallejo"
    assert replace(alert, previous_percent=1.0).to_dict()["previous_percent"] == 1.0
