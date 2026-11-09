import httpx
import pytest
from conftest import FakeResponse, RecordingHttp, make_arb, make_move

from odds_arbitrage_alerts.sinks import (
    DeliveryError,
    DiscordSink,
    SlackSink,
    TelegramSink,
    WebhookSink,
    build_sinks,
    post_json,
    preview,
)

SLACK_URL = "https://hooks.slack.com/services/T000/B000/XXXX"
DISCORD_URL = "https://discord.com/api/webhooks/123/abc"


def test_slack_payload():
    http = RecordingHttp()
    SlackSink(SLACK_URL, http).send([make_arb()])
    [(url, payload)] = http.posts
    assert url == SLACK_URL
    assert payload["unfurl_links"] is False
    assert payload["text"].startswith("*Odds alerts: 1 arbitrage*")
    assert "|Adolfo Daniel Vallejo v Ugo Humbert>*" in payload["text"]


def test_discord_payload_and_chunking():
    http = RecordingHttp()
    DiscordSink(DISCORD_URL, http).send([make_move(key=str(i)) for i in range(20)])
    assert len(http.posts) > 1
    for url, payload in http.posts:
        assert url == DISCORD_URL
        assert len(payload["content"]) <= 1900
        assert payload["allowed_mentions"] == {"parse": []}
        assert payload["flags"] == 4


def test_telegram_payload():
    http = RecordingHttp()
    TelegramSink("123:abc", "-1001", http).send([make_move()])
    [(url, payload)] = http.posts
    assert url == "https://api.telegram.org/bot123:abc/sendMessage"
    assert payload["chat_id"] == "-1001"
    assert payload["parse_mode"] == "HTML"
    assert payload["text"].startswith("<b>Odds alerts")


def test_telegram_api_base_override():
    http = RecordingHttp()
    env = {
        "TELEGRAM_BOT_TOKEN": "1:x",
        "TELEGRAM_CHAT_ID": "5",
        "TELEGRAM_API_BASE": "https://tg.example/",
    }
    [sink], _ = build_sinks(["telegram"], env, http)
    sink.send_text("hi")
    assert http.posts[0][0] == "https://tg.example/bot1:x/sendMessage"


def test_webhook_payload_is_structured():
    http = RecordingHttp()
    WebhookSink("https://example.com/hook", http).send([make_arb(), make_move()])
    [(_, payload)] = http.posts
    assert payload["source"] == "odds-arbitrage-alerts"
    assert payload["text"].startswith("Odds alerts: 1 arbitrage, 1 price move")
    arb, move = payload["alerts"]
    assert arb["kind"] == "arbitrage" and arb["legs"][1]["bookmakers"] == ("Unibet", "Coral")
    assert move["kind"] == "price_move" and move["new_odds"] == 2.6


def test_preview_matches_what_send_posts():
    http = RecordingHttp()
    sink = SlackSink(SLACK_URL, http)
    alerts = [make_arb(), make_move()]
    sink.send(alerts)
    assert [p["text"] for _, p in http.posts] == preview(sink, alerts)


def test_build_sinks_skips_missing_secrets():
    env = {"SLACK_WEBHOOK_URL": SLACK_URL, "DISCORD_WEBHOOK_URL": "  ", "TELEGRAM_BOT_TOKEN": "x"}
    sinks, skipped = build_sinks(["slack", "discord", "telegram", "webhook"], env, RecordingHttp())
    assert [s.name for s in sinks] == ["slack"]
    assert skipped == ["discord", "telegram", "webhook"]


def test_post_json_retries_once_on_429():
    http = RecordingHttp([FakeResponse(429, headers={"Retry-After": "1"}), FakeResponse(200)])
    slept = []
    post_json(http, SLACK_URL, {"text": "x"}, sink="slack", sleep=slept.append)
    assert len(http.posts) == 2 and slept == [1.0]


def test_post_json_errors_hide_the_url():
    http = RecordingHttp([FakeResponse(404, text="no_service")])
    with pytest.raises(DeliveryError) as info:
        post_json(http, SLACK_URL, {"text": "x"}, sink="slack")
    assert "HTTP 404 no_service" in str(info.value)
    assert SLACK_URL not in str(info.value)

    http = RecordingHttp([httpx.ConnectError("boom")])
    with pytest.raises(DeliveryError, match="request failed"):
        post_json(http, SLACK_URL, {"text": "x"}, sink="slack")
