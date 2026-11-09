import pytest
from conftest import make_arb, make_move

from odds_arbitrage_alerts import messages
from odds_arbitrage_alerts.messages import (
    DISCORD,
    PLAIN,
    RECHECK,
    SLACK,
    TELEGRAM,
    bookmaker_text,
    format_money,
    format_odds,
    format_start,
    render,
)


def test_plain_arbitrage_message():
    [text] = render([make_arb()], PLAIN)
    assert text == "\n".join(
        [
            "Odds alerts: 1 arbitrage",
            RECHECK,
            "",
            "ARBITRAGE 3.63% | Adolfo Daniel Vallejo v Ugo Humbert",
            "Tennis / ATP Stockholm | Match Result | Mon 9 Nov 13:30",
            "Adolfo Daniel Vallejo 2.10 (11/10) at Bet365: stake £49.41",
            "Ugo Humbert 2.05 (21/20) at Unibet, Coral: stake £50.59",
            "£100.00 staked returns at least £103.77 whichever wins (+£3.77)",
            "https://www.oddschecker.com/tennis/atp-stockholm/adolfo-daniel-vallejo-v-ugo-humbert/winner",
        ]
    )


def test_plain_move_message():
    [text] = render([make_move()], PLAIN)
    assert text.split("\n") == [
        "Odds alerts: 1 price move",
        "",
        "PRICE MOVE +10.6% | Indiana Pacers at Dallas Mavericks",
        "NBA Matches | Win Market | Tue 10 Nov 01:40 UTC",
        "Indiana Pacers: 2.35 -> 2.60 (8/5), drifting, since Mon 9 Nov 09:05 UTC. Best at IvyBet",
        "https://www.oddschecker.com/basketball/nba/indiana-pacers-at-dallas-mavericks/winner",
    ]


def test_shortening_and_no_bookmaker():
    [text] = render([make_move(change_percent=-12.5, new_odds=2.05, bookmakers=())], PLAIN)
    assert "-12.5%" in text
    assert "2.35 -> 2.05 (8/5), shortening" in text
    assert "Best at" not in text


def test_reopened_arbitrage_shows_the_previous_percent():
    [text] = render([make_arb(previous_percent=1.2)], PLAIN)
    assert "ARBITRAGE 3.63% (was 1.20%)" in text


def test_header_counts_both_kinds():
    text = render([make_arb(), make_move(), make_move(key="x")], PLAIN)[0]
    assert text.startswith("Odds alerts: 1 arbitrage, 2 price moves\n" + RECHECK)


def test_slack_markup():
    [text] = render([make_arb(event="A & B <C>")], SLACK)
    assert text.startswith("*Odds alerts: 1 arbitrage*")
    assert "*ARBITRAGE 3.63% | <https://www.oddschecker.com/tennis/" in text
    assert "|A &amp; B &lt;C&gt;>*" in text


def test_discord_markup_escapes_markdown():
    [text] = render([make_move(selection="Over_215*5")], DISCORD)
    assert text.startswith("**Odds alerts: 1 price move**")
    assert "**PRICE MOVE +10.6% | [Indiana Pacers at Dallas Mavericks](https://" in text
    assert "Over\\_215\\*5" in text


def test_telegram_markup():
    [text] = render([make_move()], TELEGRAM)
    assert text.startswith("<b>Odds alerts: 1 price move</b>")
    assert '<a href="https://www.oddschecker.com/basketball/nba/' in text
    assert "2.35 -&gt; 2.60" in text


def test_long_runs_split_between_alerts():
    alerts = [make_move(key=str(i)) for i in range(20)]
    parts = render(alerts, DISCORD, limit=1900)
    assert len(parts) > 1
    assert all(len(part) <= 1900 for part in parts)
    assert parts[0].startswith("**Odds alerts: 20 price moves**")
    assert sum(part.count("PRICE MOVE") for part in parts) == 20


@pytest.mark.parametrize(
    ("value", "expected"), [(2.0, "2.00"), (1.909, "1.909"), (101.0, "101.00"), (1.5, "1.50")]
)
def test_format_odds(value, expected):
    assert format_odds(value) == expected


def test_format_money():
    assert format_money(1234.5, "£") == "£1,234.50"
    assert format_money(42.5, "USD") == "42.50 USD"
    assert format_money(42.5, "") == "42.50"


def test_format_start():
    assert format_start("2026-11-21T15:00:00Z", None) == "Sat 21 Nov 15:00 UTC"
    assert format_start(None, "2026-11-10 11:00") == "Tue 10 Nov 11:00"
    assert format_start(None, "2026-11-10") == "Tue 10 Nov"
    assert format_start(None, "tomorrow") == "tomorrow"
    assert format_start(None, None) is None


def test_bookmaker_text_caps_the_list():
    assert bookmaker_text([]) == ""
    assert bookmaker_text(["A", "B", "C"]) == "A, B, C"
    assert bookmaker_text(["A", "B", "C", "D", "E"]) == "A, B, C +2 more"


def test_format_since_passes_odd_values_through():
    assert messages.format_since("2026-11-09T13:05Z") == "Mon 9 Nov 13:05 UTC"
    assert messages.format_since("") == ""
