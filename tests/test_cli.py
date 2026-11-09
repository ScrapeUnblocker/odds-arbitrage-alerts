import importlib
import io
import json
from datetime import timedelta

import pytest
from conftest import (
    EPL,
    NBA,
    NOW,
    TENNIS,
    FakeClient,
    FakeResponse,
    RecordingHttp,
    football_event,
    nba_event,
    page_payload,
    tennis_event,
)
from scrapeunblocker import AuthenticationError, ServerError

from odds_arbitrage_alerts.cli import build_parser, load_env_file, main

CONFIG = """
pages:
  - url: football/english/premier-league
    name: Premier League
  - url: basketball/nba
    name: NBA
  - url: tennis
    name: Tennis
destinations: [slack, webhook]
state_file: state/odds.json
"""
SLACK_URL = "https://hooks.slack.com/services/T000/B000/XXXX"
HOOK_URL = "https://example.com/hook"
ENV = {
    "SCRAPEUNBLOCKER_KEY": "test-key",
    "SLACK_WEBHOOK_URL": SLACK_URL,
    "ALERT_WEBHOOK_URL": HOOK_URL,
}


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text(CONFIG, encoding="utf-8")
    return path


def responses(first=2.1, second=2.05, win_away=2.35):
    """Tennis has a 3.6% arbitrage at the default prices; the other pages have none."""
    return {
        EPL: page_payload(EPL, football_event()),
        NBA: page_payload(NBA, nba_event(win_away=win_away)),
        TENNIS: page_payload(TENNIS, tennis_event(first=first, second=second)),
    }


def invoke(args, *, client=None, http=None, env=None, now=NOW):
    out = io.StringIO()
    code = main(
        [*args, "--env-file", "does-not-exist.env"],
        environ=dict(ENV if env is None else env),
        client_factory=(lambda: client) if client else None,
        http_factory=lambda: http if http is not None else RecordingHttp(),
        now=now,
        out=out,
    )
    return code, out.getvalue()


def test_parser_defaults():
    args = build_parser().parse_args(["run"])
    assert args.command == "run"
    assert args.config == "config.yml"
    assert args.dry_run is False
    assert args.env_file == ".env"
    assert args.verbose is False


def test_parser_options():
    args = build_parser().parse_args(["run", "--dry-run", "-c", "other.yml", "-v"])
    assert args.dry_run and args.verbose and args.config == "other.yml"
    assert build_parser().parse_args(["status"]).command == "status"
    assert build_parser().parse_args(["test-destinations"]).command == "test-destinations"


def test_parser_requires_a_command():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_two_runs_deliver_only_new_alerts(config_path):
    http = RecordingHttp()
    code, out = invoke(["run", "-c", str(config_path)], client=FakeClient(responses()), http=http)
    assert code == 0, out
    assert "Delivered 1 alert(s) to slack, webhook." in out
    (slack_url, slack), (hook_url, hook) = http.posts
    assert slack_url == SLACK_URL and hook_url == HOOK_URL
    assert "ARBITRAGE 3.60%" in slack["text"]
    assert hook["alerts"][0]["kind"] == "arbitrage"
    state = json.loads((config_path.parent / "state" / "odds.json").read_text(encoding="utf-8"))
    assert list(state["arbs"]) == ["3588627436|Match Result|"]

    http2 = RecordingHttp()
    later = NOW + timedelta(minutes=30)
    code, out = invoke(
        ["run", "-c", str(config_path)], client=FakeClient(responses()), http=http2, now=later
    )
    assert code == 0
    assert "Nothing new to deliver." in out
    assert http2.posts == []
    assert "State unchanged" in out


def test_price_move_on_a_later_run(config_path):
    invoke(["run", "-c", str(config_path)], client=FakeClient(responses()))
    http = RecordingHttp()
    code, out = invoke(
        ["run", "-c", str(config_path)], client=FakeClient(responses(win_away=2.1)), http=http
    )
    assert code == 0
    assert "Delivered 1 alert(s)" in out
    text = http.posts[0][1]["text"]
    assert "PRICE MOVE -10.6%" in text
    assert "Indiana Pacers: 2.35 -&gt; 2.10 (27/20), shortening" in text  # Slack escapes >


def test_dry_run_sends_and_saves_nothing(config_path):
    http = RecordingHttp()
    code, out = invoke(
        ["run", "-c", str(config_path), "--dry-run"], client=FakeClient(responses()), http=http
    )
    assert code == 0
    assert http.posts == []
    assert "Dry run: 1 alert(s). Nothing was sent or saved." in out
    assert "--- slack ---" in out and "--- webhook ---" in out
    assert "ARBITRAGE 3.60% | Adolfo Daniel Vallejo v Ugo Humbert" in out
    assert not (config_path.parent / "state").exists()


def test_dry_run_without_destinations_prints_plain_text(config_path):
    code, out = invoke(
        ["run", "-c", str(config_path), "--dry-run"],
        client=FakeClient(responses()),
        env={"SCRAPEUNBLOCKER_KEY": "k"},
    )
    assert code == 0
    assert "the message would be:" in out
    assert "Odds alerts: 1 arbitrage" in out


def test_failed_delivery_is_retried_next_run(config_path):
    failing = RecordingHttp([FakeResponse(500, "down"), FakeResponse(500, "down")])
    code, out = invoke(
        ["run", "-c", str(config_path)], client=FakeClient(responses()), http=failing
    )
    assert code == 1
    assert "will be retried next run" in out
    http = RecordingHttp()
    code, out = invoke(["run", "-c", str(config_path)], client=FakeClient(responses()), http=http)
    assert code == 0 and "Delivered 1 alert(s)" in out


def test_one_failed_destination_does_not_resend_to_the_others(config_path):
    http = RecordingHttp([FakeResponse(500, "down")])  # slack fails, webhook works
    code, out = invoke(["run", "-c", str(config_path)], client=FakeClient(responses()), http=http)
    assert code == 1
    assert "Delivered 1 alert(s) to webhook." in out
    http2 = RecordingHttp()
    invoke(["run", "-c", str(config_path)], client=FakeClient(responses()), http=http2)
    assert http2.posts == []


def test_a_failed_page_warns_but_the_rest_still_runs(config_path, monkeypatch):
    data = responses()
    data[EPL] = [ServerError("bad gateway", status_code=502)] * 3
    env = {**ENV, "GITHUB_ACTIONS": "true"}
    client = FakeClient(data)
    out = io.StringIO()
    collect_module = importlib.import_module("odds_arbitrage_alerts.collect")
    monkeypatch.setattr(collect_module, "RETRY_DELAYS", (0.0, 0.0))
    code = main(
        ["run", "-c", str(config_path), "--env-file", "none.env"],
        environ=env,
        client_factory=lambda: client,
        http_factory=RecordingHttp,
        now=NOW,
        out=out,
    )
    text = out.getvalue()
    assert code == 0
    assert "Premier League           FAILED ServerError" in text
    assert (
        "::warning title=Page failed::https://www.oddschecker.com/football/english/premier-league"
        in text
    )
    assert "Delivered 1 alert(s)" in text


def test_every_page_failing_is_an_error(config_path):
    error = ServerError(
        "x", status_code=502, body="No events on that page - pass a sport or competition page."
    )
    client = FakeClient({EPL: error, NBA: error, TENNIS: error})
    code, out = invoke(["run", "-c", str(config_path)], client=client)
    assert code == 1
    assert "No page could be read" in out
    assert not (config_path.parent / "state").exists()


def test_bad_key_exits_2(config_path, capsys):
    client = FakeClient({EPL: AuthenticationError("invalid key", status_code=401)})
    code, _ = invoke(["run", "-c", str(config_path)], client=client)
    assert code == 2
    assert "AuthenticationError" in capsys.readouterr().err


def test_missing_api_key(config_path, capsys):
    code, _ = invoke(["run", "-c", str(config_path)], env={"SLACK_WEBHOOK_URL": SLACK_URL})
    assert code == 2
    assert "SCRAPEUNBLOCKER_KEY" in capsys.readouterr().err


def test_missing_destinations(config_path, capsys):
    code, _ = invoke(["run", "-c", str(config_path)], env={"SCRAPEUNBLOCKER_KEY": "k"})
    assert code == 2
    assert "no destination is set up" in capsys.readouterr().err


def test_config_error_exits_2(tmp_path, capsys):
    path = tmp_path / "config.yml"
    path.write_text("pages: []\n", encoding="utf-8")
    code, _ = invoke(["run", "-c", str(path)])
    assert code == 2
    assert "non-empty list" in capsys.readouterr().err


def test_status_without_and_with_state(config_path):
    code, out = invoke(["status", "-c", str(config_path)])
    assert code == 0
    assert "Tracked prices: 0 across 0 event(s)" in out
    invoke(["run", "-c", str(config_path)], client=FakeClient(responses()))
    code, out = invoke(["status", "-c", str(config_path)])
    assert "Tracked prices: 11 across 3 event(s)" in out
    assert (
        "3.60%  Adolfo Daniel Vallejo v Ugo Humbert (Match Result), since Mon 9 Nov 13:05 UTC"
        in out
    )


def test_test_destinations(config_path):
    http = RecordingHttp()
    code, out = invoke(["test-destinations", "-c", str(config_path)], http=http)
    assert code == 0
    assert "slack     ok" in out and "webhook   ok" in out
    text = http.posts[0][1]["text"]
    assert text.startswith("odds-arbitrage-alerts is connected")
    assert "Premier League, NBA, Tennis" in text


def test_load_env_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# c\nexport A=1\nB='two'\nC=\"3\"\nSET=new\n", encoding="utf-8")
    env = {"SET": "old"}
    load_env_file(path, env)
    assert env == {"SET": "old", "A": "1", "B": "two", "C": "3"}
