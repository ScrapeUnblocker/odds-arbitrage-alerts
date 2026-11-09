"""Destinations behind one small interface.

A sink has a ``name`` and two methods: ``send(alerts)`` delivers odds alerts and
``send_text(text)`` delivers a plain message (used by ``test-destinations``). Both raise
:class:`DeliveryError` on failure. Secrets come from environment variables, never from
``config.yml``. To add a destination, write a class like the ones below and register it
in ``REGISTRY``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from . import messages
from .process import Alert

SOURCE = "odds-arbitrage-alerts"
USER_AGENT = f"{SOURCE} (+https://github.com/ScrapeUnblocker/{SOURCE})"


class DeliveryError(RuntimeError):
    """A destination did not accept the message."""


class Sink(Protocol):
    name: str

    def send(self, alerts: Sequence[Alert]) -> None: ...

    def send_text(self, text: str) -> None: ...


class HttpPoster(Protocol):
    def post(self, url: str, *, json: Any, timeout: float) -> Any: ...


def post_json(
    http: HttpPoster,
    url: str,
    payload: Any,
    *,
    sink: str,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """POST JSON, retrying once on HTTP 429. Errors never include the URL (it holds secrets)."""
    for attempt in (1, 2):
        try:
            resp = http.post(url, json=payload, timeout=20.0)
        except httpx.HTTPError as exc:
            raise DeliveryError(f"{sink}: request failed ({type(exc).__name__})") from None
        if resp.status_code == 429 and attempt == 1:
            sleep(_retry_after(resp))
            continue
        if resp.status_code >= 300:
            body = (resp.text or "")[:200].replace("\n", " ")
            raise DeliveryError(f"{sink}: HTTP {resp.status_code} {body}".rstrip())
        return


def _retry_after(resp: Any) -> float:
    try:
        return min(float(resp.headers.get("Retry-After", "2")), 10.0)
    except (TypeError, ValueError):
        return 2.0


class TextSink:
    """A destination that shows rendered text: Slack, Discord, Telegram."""

    name = "text"
    style = messages.PLAIN
    limit = 4000

    def __init__(self, http: HttpPoster) -> None:
        self.http = http

    def send(self, alerts: Sequence[Alert]) -> None:
        for text in messages.render(alerts, self.style, limit=self.limit):
            self.send_text(text)

    def send_text(self, text: str) -> None:
        post_json(self.http, self.url(), self.payload(text), sink=self.name)

    def url(self) -> str:
        raise NotImplementedError

    def payload(self, text: str) -> dict[str, Any]:
        raise NotImplementedError


class SlackSink(TextSink):
    """Slack incoming webhook."""

    name = "slack"
    style = messages.SLACK
    limit = 3500

    def __init__(self, webhook_url: str, http: HttpPoster) -> None:
        super().__init__(http)
        self.webhook_url = webhook_url

    def url(self) -> str:
        return self.webhook_url

    def payload(self, text: str) -> dict[str, Any]:
        return {"text": text, "unfurl_links": False, "unfurl_media": False}


class DiscordSink(TextSink):
    """Discord channel webhook. Link previews are suppressed, mentions disabled."""

    name = "discord"
    style = messages.DISCORD
    limit = 1900

    def __init__(self, webhook_url: str, http: HttpPoster) -> None:
        super().__init__(http)
        self.webhook_url = webhook_url

    def url(self) -> str:
        return self.webhook_url

    def payload(self, text: str) -> dict[str, Any]:
        return {"content": text, "allowed_mentions": {"parse": []}, "flags": 4}


class TelegramSink(TextSink):
    """Telegram bot message to one chat (user, group or channel)."""

    name = "telegram"
    style = messages.TELEGRAM
    limit = 4000

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        http: HttpPoster,
        api_base: str = "https://api.telegram.org",
    ) -> None:
        super().__init__(http)
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_base = api_base.rstrip("/")

    def url(self) -> str:
        return f"{self.api_base}/bot{self.bot_token}/sendMessage"

    def payload(self, text: str) -> dict[str, Any]:
        return {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }


class WebhookSink:
    """Any URL that accepts a JSON POST: Zapier, Make, n8n, your own endpoint."""

    name = "webhook"
    style = messages.PLAIN

    def __init__(self, url: str, http: HttpPoster) -> None:
        self.webhook_url = url
        self.http = http

    def send(self, alerts: Sequence[Alert]) -> None:
        payload = {
            "source": SOURCE,
            "text": messages.render(alerts, messages.PLAIN)[0],
            "alerts": [alert.to_dict() for alert in alerts],
        }
        post_json(self.http, self.webhook_url, payload, sink=self.name)

    def send_text(self, text: str) -> None:
        payload = {"source": SOURCE, "text": text, "alerts": []}
        post_json(self.http, self.webhook_url, payload, sink=self.name)


@dataclass(frozen=True)
class SinkSpec:
    env: tuple[str, ...]
    build: Callable[[Mapping[str, str], HttpPoster], Sink]


REGISTRY: dict[str, SinkSpec] = {
    "slack": SinkSpec(
        env=("SLACK_WEBHOOK_URL",),
        build=lambda env, http: SlackSink(env["SLACK_WEBHOOK_URL"], http),
    ),
    "discord": SinkSpec(
        env=("DISCORD_WEBHOOK_URL",),
        build=lambda env, http: DiscordSink(env["DISCORD_WEBHOOK_URL"], http),
    ),
    "telegram": SinkSpec(
        env=("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"),
        build=lambda env, http: TelegramSink(
            env["TELEGRAM_BOT_TOKEN"],
            env["TELEGRAM_CHAT_ID"],
            http,
            api_base=env.get("TELEGRAM_API_BASE") or "https://api.telegram.org",
        ),
    ),
    "webhook": SinkSpec(
        env=("ALERT_WEBHOOK_URL",),
        build=lambda env, http: WebhookSink(env["ALERT_WEBHOOK_URL"], http),
    ),
}


def build_sinks(
    names: Sequence[str], env: Mapping[str, str], http: HttpPoster
) -> tuple[list[Sink], list[str]]:
    """Build every listed destination whose secrets are set.

    Returns ``(sinks, skipped)`` where ``skipped`` names destinations with missing secrets.
    """
    clean = {k: v.strip() for k, v in env.items() if isinstance(v, str)}
    sinks: list[Sink] = []
    skipped: list[str] = []
    for name in names:
        spec = REGISTRY[name]
        if all(clean.get(var) for var in spec.env):
            sinks.append(spec.build(clean, http))
        else:
            skipped.append(name)
    return sinks, skipped


def preview(sink: Sink, alerts: Sequence[Alert]) -> list[str]:
    """The message(s) ``sink.send(alerts)`` would post, for ``--dry-run``."""
    style = getattr(sink, "style", messages.PLAIN)
    return messages.render(alerts, style, limit=getattr(sink, "limit", None))


def new_http_client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=False)
