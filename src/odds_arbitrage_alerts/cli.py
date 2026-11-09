"""Command line: ``odds-arbitrage-alerts run | test-destinations | status``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import Counter
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from scrapeunblocker import Client

from . import __version__, messages
from .collect import FATAL, PluginClient, collect
from .config import Config, ConfigError, load_config
from .process import evaluate, restore
from .sinks import DeliveryError, Sink, build_sinks, new_http_client, preview
from .state import StateError, load_state, save_state

API_KEY_ENV = "SCRAPEUNBLOCKER_KEY"
DESTINATION_ENV = {
    "slack": "SLACK_WEBHOOK_URL",
    "discord": "DISCORD_WEBHOOK_URL",
    "telegram": "TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID",
    "webhook": "ALERT_WEBHOOK_URL",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="odds-arbitrage-alerts",
        description=(
            "Check Oddschecker pages for arbitrage and big price moves and send the new "
            "ones to Slack, Discord, Telegram or a webhook. Odds come from the "
            "ScrapeUnblocker Oddschecker API."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-c", "--config", default="config.yml", help="config file (default: config.yml)"
    )
    common.add_argument(
        "--env-file",
        default=".env",
        help="read environment variables from this file when it exists (default: .env)",
    )
    common.add_argument("-v", "--verbose", action="store_true", help="log every API call")

    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    run = sub.add_parser("run", parents=[common], help="check the odds now and deliver new alerts")
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="print exactly what would be delivered; deliver nothing and save no state",
    )
    sub.add_parser(
        "test-destinations",
        parents=[common],
        help="send a test message to each configured destination (no API calls)",
    )
    sub.add_parser("status", parents=[common], help="show what the state file holds (no API calls)")
    return parser


def load_env_file(path: str | Path, environ: MutableMapping[str, str]) -> None:
    """Minimal ``.env`` reader: ``KEY=value`` lines; real environment variables win."""
    path = Path(path)
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in environ:
            environ[key] = value


def run_check(
    config: Config,
    client: PluginClient,
    sinks: Sequence[Sink],
    *,
    dry_run: bool,
    now: datetime,
    out: TextIO = sys.stdout,
    annotate: bool = False,
    sleep: Callable[[float], None] | None = None,
) -> int:
    """One scheduled check: collect, evaluate, deliver, save. Returns the exit code."""
    state = load_state(config.state_file)
    kwargs: dict[str, Any] = {"sleep": sleep} if sleep else {}
    result = collect(client, config.pages, **kwargs)

    print(f"Checked {len(config.pages)} page(s) at {now:%Y-%m-%d %H:%M} UTC:", file=out)
    per_page = Counter(event.page for event in result.events)
    for page in result.pages_ok:
        print(f"  {page.label:<24} {per_page[page.label]} event(s)", file=out)
    for page, error in result.failures:
        print(f"  {page.label:<24} FAILED {error}", file=out)
        if annotate:
            print(f"::warning title=Page failed::{page.url}: {error}", file=out)
    if not result.pages_ok:
        print("No page could be read; nothing was evaluated or saved.", file=out)
        return 1

    evaluation = evaluate(result.events, state, config, now, result.bookmakers)
    alerts = evaluation.alerts
    print(
        f"{evaluation.events_checked} pre-match event(s), {evaluation.markets_checked} "
        "complete market(s).",
        file=out,
    )
    if evaluation.closest:
        best = "; ".join(
            f"{book:.2f}% {event} ({market})" for book, event, market in evaluation.closest
        )
        print(f"Closest to arbitrage (100% = break-even): {best}", file=out)

    if dry_run:
        print(file=out)
        if not alerts:
            print("Dry run: nothing new to deliver. Nothing was sent or saved.", file=out)
            return 0
        print(f"Dry run: {len(alerts)} alert(s). Nothing was sent or saved.", file=out)
        if not sinks:
            print("No destination secrets are set; the message would be:\n", file=out)
            print(messages.render(alerts, messages.PLAIN)[0], file=out)
        for sink in sinks:
            for text in preview(sink, alerts):
                print(f"\n--- {sink.name} ---\n{text}", file=out)
        return 0

    new_state = evaluation.state
    failed: list[str] = []
    if alerts:
        delivered: list[str] = []
        for sink in sinks:
            try:
                sink.send(alerts)
            except DeliveryError as exc:
                print(f"Delivery failed: {exc}", file=sys.stderr)
                failed.append(sink.name)
            else:
                delivered.append(sink.name)
        if delivered:
            print(f"Delivered {len(alerts)} alert(s) to {', '.join(delivered)}.", file=out)
        else:
            new_state = restore(new_state, state, alerts)
            print("No destination accepted the alerts; they will be retried next run.", file=out)
    else:
        print("Nothing new to deliver.", file=out)

    changed = save_state(config.state_file, new_state)
    print(f"State {'saved to' if changed else 'unchanged:'} {config.state_file}", file=out)
    return 1 if failed else 0


def show_status(config: Config, out: TextIO = sys.stdout) -> int:
    """Print what the state file knows: tracked prices and open arbitrages."""
    state = load_state(config.state_file)
    prices, arbs = state["prices"], state["arbs"]
    events = {key.split("|", 1)[0] for key in prices}
    print(f"State file: {config.state_file}", file=out)
    print(f"Tracked prices: {len(prices)} across {len(events)} event(s)", file=out)
    if not arbs:
        print("Open arbitrages already delivered: none", file=out)
        return 0
    print("Open arbitrages already delivered:", file=out)
    for entry in sorted(arbs.values(), key=lambda e: -float(e.get("percent") or 0)):
        since = messages.format_since(str(entry.get("since") or ""))
        print(
            f"  {float(entry.get('percent') or 0):5.2f}%  {entry.get('event')} "
            f"({entry.get('market')}), since {since}",
            file=out,
        )
    return 0


def test_destinations(config: Config, sinks: Sequence[Sink], out: TextIO = sys.stdout) -> int:
    pages = ", ".join(page.label for page in config.pages)
    text = (
        "odds-arbitrage-alerts is connected. Arbitrage and price-move alerts for "
        f"{pages} will arrive here."
    )
    failed = 0
    for sink in sinks:
        try:
            sink.send_text(text)
        except DeliveryError as exc:
            failed += 1
            print(f"  {sink.name:<9} FAILED {exc}", file=out)
        else:
            print(f"  {sink.name:<9} ok", file=out)
    return 1 if failed else 0


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: MutableMapping[str, str] | None = None,
    client_factory: Callable[[], PluginClient] | None = None,
    http_factory: Callable[[], Any] = new_http_client,
    now: datetime | None = None,
    out: TextIO | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    environ = os.environ if environ is None else environ
    if out is None:
        out = sys.stdout
        _replace_unprintable(sys.stdout, sys.stderr)
    now = now or datetime.now(UTC)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    # httpx logs full request URLs at INFO, and webhook URLs are secrets.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    load_env_file(args.env_file, environ)
    try:
        config = load_config(args.config)
        if args.command == "status":
            return show_status(config, out)
        http = http_factory()
        try:
            sinks, skipped = build_sinks(config.destinations, environ, http)
            if args.command == "test-destinations":
                return _test_command(config, sinks, skipped, out)
            return _run_command(config, sinks, skipped, args, environ, client_factory, now, out)
        finally:
            close = getattr(http, "close", None)
            if callable(close):
                close()
    except (ConfigError, StateError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except FATAL as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


def _replace_unprintable(*streams: Any) -> None:
    """Keep a console without UTF-8 (older Windows code pages) from crashing on '£'."""
    for stream in streams:
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(errors="replace")


def _test_command(
    config: Config, sinks: Sequence[Sink], skipped: Sequence[str], out: TextIO
) -> int:
    if not sinks:
        _no_destination_error(skipped)
        return 2
    for name in skipped:
        print(f"  {name:<9} skipped ({DESTINATION_ENV[name]} not set)", file=out)
    return test_destinations(config, sinks, out)


def _run_command(
    config: Config,
    sinks: Sequence[Sink],
    skipped: Sequence[str],
    args: argparse.Namespace,
    environ: Mapping[str, str],
    client_factory: Callable[[], PluginClient] | None,
    now: datetime,
    out: TextIO,
) -> int:
    if not (environ.get(API_KEY_ENV) or "").strip():
        print(
            f"error: set {API_KEY_ENV} to your ScrapeUnblocker API key "
            "(get one at https://scrapeunblocker.com/?utm_source=github&utm_medium=integration"
            "&utm_campaign=example-repos)",
            file=sys.stderr,
        )
        return 2
    if not sinks and not args.dry_run:
        _no_destination_error(skipped)
        return 2
    if client_factory is not None:
        client = client_factory()
    else:
        # Retries are handled per page in collect.py, so a page with no events is not retried.
        client = Client(api_key=environ[API_KEY_ENV].strip(), max_retries=0)
    try:
        return run_check(
            config,
            client,
            sinks,
            dry_run=args.dry_run,
            now=now,
            out=out,
            annotate=environ.get("GITHUB_ACTIONS") == "true",
        )
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


def _no_destination_error(skipped: Sequence[str]) -> None:
    wanted = ", ".join(f"{DESTINATION_ENV[name]} ({name})" for name in skipped)
    print(f"error: no destination is set up. Set at least one of: {wanted}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
