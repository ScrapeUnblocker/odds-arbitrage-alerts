"""Turn alerts into the text each destination shows.

Every destination gets the same message; only the markup differs (Slack mrkdwn, Discord
markdown, Telegram HTML, plain text for the webhook and ``--dry-run``).
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from .process import Alert, ArbAlert, MoveAlert

RECHECK = "Prices move fast: re-check every price at the bookmaker before you bet."
MAX_BOOKMAKERS = 3


@dataclass(frozen=True)
class Style:
    """How one destination writes bold text and links, and what it must escape."""

    name: str
    bold: Callable[[str], str]
    link: Callable[[str, str], str] | None
    escape: Callable[[str], str]


def _slack_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _discord_escape(text: str) -> str:
    text = re.sub(r"([\\*_~`\[\]])", r"\\\1", text)
    return text.replace("||", "\\|\\|")  # || starts a spoiler; a single | is plain text


def _telegram_escape(text: str) -> str:
    return html.escape(text, quote=False)


SLACK = Style(
    name="slack",
    bold=lambda text: f"*{text}*",
    link=lambda text, url: f"<{url}|{text}>",
    escape=_slack_escape,
)
DISCORD = Style(
    name="discord",
    bold=lambda text: f"**{text}**",
    link=lambda text, url: f"[{text}]({url})",
    escape=_discord_escape,
)
TELEGRAM = Style(
    name="telegram",
    bold=lambda text: f"<b>{text}</b>",
    link=lambda text, url: f'<a href="{html.escape(url, quote=True)}">{text}</a>',
    escape=_telegram_escape,
)
# No markup: the event link goes on its own line at the end of each alert.
PLAIN = Style(name="plain", bold=lambda text: text, link=None, escape=lambda text: text)


def format_odds(decimal: float) -> str:
    """``2.00``, ``1.909``, ``101.00``."""
    return f"{decimal:.2f}" if round(decimal, 2) == decimal else f"{decimal:.3f}"


def format_money(amount: float, currency: str) -> str:
    """``£42.55`` for a symbol, ``42.55 USD`` for a code."""
    if currency and not currency.isalpha():
        return f"{currency}{amount:,.2f}"
    return f"{amount:,.2f} {currency}".strip()


def format_start(start_utc: str | None, start_local: str | None) -> str | None:
    """``Sat 21 Nov 15:00 UTC``; pages that list a local time get it without a zone."""
    if start_utc:
        when = datetime.strptime(start_utc, "%Y-%m-%dT%H:%M:%SZ")
        return f"{when:%a} {when.day} {when:%b %H:%M} UTC"
    if not start_local:
        return None
    try:
        when = datetime.strptime(start_local, "%Y-%m-%d %H:%M")
        return f"{when:%a} {when.day} {when:%b %H:%M}"
    except ValueError:
        pass
    try:
        when = datetime.strptime(start_local, "%Y-%m-%d")
        return f"{when:%a} {when.day} {when:%b}"
    except ValueError:
        return start_local


def format_since(stamp: str) -> str:
    """``2026-11-09T13:05Z`` -> ``Mon 9 Nov 13:05 UTC``."""
    try:
        when = datetime.strptime(stamp, "%Y-%m-%dT%H:%MZ")
    except ValueError:
        return stamp
    return f"{when:%a} {when.day} {when:%b %H:%M} UTC"


def bookmaker_text(names: Sequence[str]) -> str:
    if not names:
        return ""
    shown = ", ".join(names[:MAX_BOOKMAKERS])
    extra = len(names) - MAX_BOOKMAKERS
    return f"{shown} +{extra} more" if extra > 0 else shown


def header(alerts: Sequence[Alert], style: Style) -> str:
    arbs = sum(isinstance(a, ArbAlert) for a in alerts)
    moves = len(alerts) - arbs
    parts = []
    if arbs:
        parts.append(f"{arbs} arbitrage{'s' if arbs != 1 else ''}")
    if moves:
        parts.append(f"{moves} price move{'s' if moves != 1 else ''}")
    text = style.bold(style.escape(f"Odds alerts: {', '.join(parts) or 'nothing new'}"))
    if arbs:
        text += "\n" + style.escape(RECHECK)
    return text


def _title(label: str, alert: Alert, style: Style) -> str:
    name = style.escape(alert.event)
    if style.link is not None:
        name = style.link(name, alert.event_url)
    return style.bold(f"{style.escape(label)} | {name}")


def _context(alert: Alert, style: Style) -> str:
    parts = [alert.competition, alert.market]
    start = format_start(alert.start_utc, alert.start_local)
    if start:
        parts.append(start)
    return style.escape(" | ".join(parts))


def render_arbitrage(alert: ArbAlert, style: Style) -> str:
    label = f"ARBITRAGE {alert.arbitrage_percent:.2f}%"
    if alert.previous_percent is not None:
        label += f" (was {alert.previous_percent:.2f}%)"
    lines = [_title(label, alert, style), _context(alert, style)]
    for leg in alert.legs:
        price = format_odds(leg.decimal)
        if leg.fractional:
            price += f" ({leg.fractional})"
        where = bookmaker_text(leg.bookmakers)
        where = f" at {where}" if where else ""
        stake = format_money(leg.stake, alert.currency)
        lines.append(style.escape(f"{leg.selection} {price}{where}: stake {stake}"))
    staked = format_money(alert.total_stake, alert.currency)
    back = format_money(alert.guaranteed_return, alert.currency)
    profit = format_money(alert.profit, alert.currency)
    lines.append(
        style.escape(f"{staked} staked returns at least {back} whichever wins (+{profit})")
    )
    if style.link is None:
        lines.append(alert.event_url)
    return "\n".join(lines)


def render_move(alert: MoveAlert, style: Style) -> str:
    direction = "drifting" if alert.change_percent > 0 else "shortening"
    label = f"PRICE MOVE {alert.change_percent:+.1f}%"
    new = format_odds(alert.new_odds)
    if alert.fractional:
        new += f" ({alert.fractional})"
    text = f"{alert.selection}: {format_odds(alert.old_odds)} -> {new}, {direction}"
    if alert.since:
        text += f", since {format_since(alert.since)}"
    where = bookmaker_text(alert.bookmakers)
    if where:
        text += f". Best at {where}"
    lines = [_title(label, alert, style), _context(alert, style), style.escape(text)]
    if style.link is None:
        lines.append(alert.event_url)
    return "\n".join(lines)


def render_alert(alert: Alert, style: Style) -> str:
    if isinstance(alert, ArbAlert):
        return render_arbitrage(alert, style)
    return render_move(alert, style)


def render(alerts: Sequence[Alert], style: Style, limit: int | None = None) -> list[str]:
    """Render alerts as one or more messages, each at most ``limit`` characters.

    Messages split between alerts, never inside one; the header goes on the first.
    """
    messages: list[str] = []
    current = header(alerts, style)
    for alert in alerts:
        block = render_alert(alert, style)
        candidate = f"{current}\n\n{block}"
        if limit is not None and len(candidate) > limit and current:
            messages.append(current)
            candidate = block
        current = candidate
    messages.append(current)
    if limit is not None:
        messages = [m if len(m) <= limit else m[: limit - 3] + "..." for m in messages]
    return messages
