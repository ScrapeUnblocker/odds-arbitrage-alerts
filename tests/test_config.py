from pathlib import Path

import pytest
from conftest import make_config

from odds_arbitrage_alerts.config import (
    ConfigError,
    Page,
    load_config,
    normalize_page_url,
    parse_config,
)

ROOT = Path(__file__).resolve().parents[1]


def test_shipped_config_loads():
    config = load_config(ROOT / "config.yml")
    assert [p.path for p in config.pages] == [
        "football/english/premier-league",
        "basketball/nba",
        "tennis",
    ]
    assert [p.label for p in config.pages] == ["Premier League", "NBA", "Tennis"]
    assert config.arbitrage.min_arbitrage_percent == 0.5
    assert config.arbitrage.max_arbitrage_percent == 10
    assert config.arbitrage.total_stake == 100
    assert config.arbitrage.currency == "£"
    assert config.price_moves.min_move_percent == 10
    assert config.markets == ()
    assert config.skip_live is True
    assert config.destinations == ("slack", "discord", "telegram", "webhook")
    assert config.state_file == ROOT / "state" / "odds.json"


@pytest.mark.parametrize(
    "value",
    [
        "football/english/premier-league",
        "/football/english/premier-league/",
        "oddschecker.com/football/english/premier-league",
        "https://www.oddschecker.com/football/english/premier-league",
        "http://oddschecker.com/football/english/premier-league?x=1",
    ],
)
def test_page_url_forms(value):
    assert (
        normalize_page_url(value) == "https://www.oddschecker.com/football/english/premier-league"
    )


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("https://example.com/football", "not an oddschecker.com page"),
        ("https://www.oddschecker.com/", "give a sport or competition page"),
        ("football/english/premier-league/chelsea-v-leeds/winner", "single event"),
        ("   ", "empty"),
    ],
)
def test_bad_page_urls(value, message):
    with pytest.raises(ConfigError, match=message):
        normalize_page_url(value)


def test_page_label_without_a_name():
    assert Page("https://www.oddschecker.com/football/champions-league").label == "Champions League"


def test_pages_as_plain_strings():
    config = make_config(pages=["basketball/nba", "tennis"])
    assert [p.label for p in config.pages] == ["Nba", "Tennis"]


def test_duplicate_pages_rejected():
    with pytest.raises(ConfigError, match="listed twice"):
        make_config(pages=["tennis", "https://www.oddschecker.com/tennis"])


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"pages": []}, "non-empty list"),
        ({"pages": [{"name": "x"}]}, "'url' is required"),
        ({"pages": [{"url": "tennis", "label": "x"}]}, "unknown key"),
        ({"surprise": 1}, "unknown key"),
        ({"arbitrage": {"min_arbitrage_percent": -1}}, "min_arbitrage_percent"),
        ({"arbitrage": {"min_arbitrage_percent": 5, "max_arbitrage_percent": 4}}, "larger"),
        ({"arbitrage": {"total_stake": 0}}, "total_stake"),
        ({"arbitrage": {"currency": "pounds sterling"}}, "currency"),
        ({"arbitrage": {"enabled": "yes"}}, "true or false"),
        ({"price_moves": {"min_move_percent": 0}}, "above 0"),
        ({"price_moves": {"max_odds": 1}}, "above 1.0"),
        ({"markets": [1]}, "market names"),
        ({"skip_live": "no"}, "true or false"),
        ({"destinations": ["email"]}, "unknown destination"),
        ({"destinations": []}, "must be a list"),
        ({"state_file": ""}, "file path"),
    ],
)
def test_invalid_values(overrides, message):
    with pytest.raises(ConfigError, match=message):
        make_config(**overrides)


def test_markets_filter_is_case_insensitive():
    config = make_config(markets="Match Result")
    assert config.wants_market("match result")
    assert not config.wants_market("Total Points")
    assert make_config().wants_market("anything")


def test_not_a_mapping():
    with pytest.raises(ConfigError):
        parse_config(["tennis"])


def test_missing_and_broken_files(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yml")
    bad = tmp_path / "bad.yml"
    bad.write_text("pages: [tennis\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(bad)
