# Odds arbitrage alerts

For anyone who compares bookmaker prices: when the best prices on Oddschecker add up to an arbitrage, or a price moves sharply, a message with the stake split already worked out lands in your Slack, Discord or Telegram (or any webhook). It checks every 30 minutes.

[![CI](https://github.com/ScrapeUnblocker/odds-arbitrage-alerts/actions/workflows/ci.yml/badge.svg)](https://github.com/ScrapeUnblocker/odds-arbitrage-alerts/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![Powered by ScrapeUnblocker](https://img.shields.io/badge/powered%20by-ScrapeUnblocker-6c47ff.svg)](https://scrapeunblocker.com/?utm_source=github&utm_medium=integration&utm_campaign=example-repos)

It runs on a GitHub Actions schedule in your own copy of this repo and watches the Oddschecker pages you choose: the Premier League, the NBA and tennis by default. There is nothing to host, and you only hear about it when something is new.

> **Before you bet.** Prices move fast, often within minutes. An arbitrage in a message may already be gone, so re-check every price at the bookmaker before you place a bet. Betting may be restricted or illegal where you live, and every bookmaker has its own terms, so check both first. This tool only reads public odds and sends you messages. It never places a bet. If gambling stops being fun, [BeGambleAware](https://www.begambleaware.org/) offers free help.

## What actually arrives

This is the real message from a test run on 9 Nov 2026 (the webhook's `text`). The test lowered `min_arbitrage_percent` to 0 and `min_move_percent` to 3 so that a 20-minute test would trigger something; the defaults are 0.5 and 10. The arbitrage was real but tiny: 0.03%, or £0.02 on £100.

```text
Odds alerts: 1 arbitrage, 4 price moves
Prices move fast: re-check every price at the bookmaker before you bet.

ARBITRAGE 0.03% | Memphis Grizzlies at Toronto Raptors
NBA Matches | Total Points | Tue 10 Nov 00:40 UTC
Over 231.5 2.10 (11/10) at Paddy Power: stake £47.63
Under 231.5 1.91 (10/11) at BetAhoy, BetTom, BetGoodwin: stake £52.37
£100.00 staked returns at least £100.02 whichever wins (+£0.02)
https://www.oddschecker.com/basketball/nba/memphis-grizzlies-at-toronto-raptors/winner

PRICE MOVE +6.1% | Arsenal vs Man City
Premier League | Match Result | Sun 29 Nov 16:30 UTC
Man City: 3.30 -> 3.50 (5/2), drifting, since Mon 9 Nov 13:05 UTC. Best at Betfred
https://www.oddschecker.com/football/english/premier-league/arsenal-v-man-city/winner

PRICE MOVE +5.0% | Memphis Grizzlies at Toronto Raptors
NBA Matches | Total Points | Tue 10 Nov 00:40 UTC
Over 231.5: 2.00 -> 2.10 (11/10), drifting, since Mon 9 Nov 13:05 UTC. Best at Paddy Power
https://www.oddschecker.com/basketball/nba/memphis-grizzlies-at-toronto-raptors/winner

PRICE MOVE +4.8% | Lorenzo Sonego v Quentin Halys
Tennis / ATP Stockholm | Match Result | Tue 10 Nov 13:30
Lorenzo Sonego: 2.10 -> 2.20 (6/5), drifting, since Mon 9 Nov 13:05 UTC
https://www.oddschecker.com/tennis/atp-stockholm/lorenzo-sonego-v-quentin-halys/winner

PRICE MOVE -4.5% | Denver Nuggets at Houston Rockets
NBA Matches | Total Points | Tue 10 Nov 01:40 UTC
Over 217.5: 2.00 -> 1.91 (10/11), shortening, since Mon 9 Nov 13:05 UTC. Best at Coral, BetMGM UK, Ladbrokes +2 more
https://www.oddschecker.com/basketball/nba/denver-nuggets-at-houston-rockets/winner
```

Slack, Discord and Telegram get the same text with bold headings and the event name linked to its Oddschecker page. The generic webhook gets it as `text`, plus every alert as structured JSON in `alerts`: each outcome's price, bookmakers, stake and return.

- **ARBITRAGE x%** is how far under 100% the best prices add up: `(1 - sum of 1/odds) x 100`. Each outcome shows its best price, who offers it and what to stake out of your `total_stake`. The last line is the smallest amount you get back, whichever outcome wins. When an open arbitrage is sent again because its size changed, the heading says `(was y%)`.
- **PRICE MOVE x%** compares a selection's best price with the price it had when it was first seen or last alerted. `drifting` means longer odds and `shortening` means shorter ones.
- Bookmakers are named as Oddschecker lists them. Some pages (tennis, when this was written) do not say which bookmaker has the best price. The message then shows the price only, and the link takes you to the full price grid.

Everything from one run arrives as one message: arbitrages first (largest first), then the biggest moves. A long message is split between alerts.

## Setup in three steps

1. **Use this template.** Click **Use this template** > **Create a new repository**. A private repo is a good choice: it keeps your pages and thresholds to yourself, and GitHub pauses scheduled workflows in public repos after 60 days without repository activity (it emails you, and one click turns the schedule back on).
2. **Add secrets.** In your new repo go to **Settings > Secrets and variables > Actions** and add:
   - `SCRAPEUNBLOCKER_KEY`: your API key from [ScrapeUnblocker](https://scrapeunblocker.com/?utm_source=github&utm_medium=integration&utm_campaign=example-repos)
   - at least one destination:

   | Destination | Secret(s) | Where to get it |
   | - | - | - |
   | Slack | `SLACK_WEBHOOK_URL` | [Create an incoming webhook](https://api.slack.com/messaging/webhooks) for a channel |
   | Discord | `DISCORD_WEBHOOK_URL` | Channel settings > Integrations > Webhooks > New Webhook > Copy Webhook URL |
   | Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Create a bot with [@BotFather](https://t.me/BotFather), send it a message, then read `chat.id` from `https://api.telegram.org/bot<token>/getUpdates` |
   | Webhook | `ALERT_WEBHOOK_URL` | Any URL that accepts a JSON POST: Zapier, Make, n8n or your own endpoint |

3. **Edit `config.yml`.** List the Oddschecker sport or competition pages you care about under `pages`, set your thresholds and your total stake, and commit the change.

From then on the workflow checks every 30 minutes. To start right away, open **Actions > Odds check > Run workflow**. Tick **Dry run** to see the message without sending it. To check your destination secrets, run `odds-arbitrage-alerts test-destinations` locally, which posts a short "connected" message.

Until `SCRAPEUNBLOCKER_KEY` is set, the workflow prints a notice and exits green without doing anything.

### Run it on your own machine instead

```bash
git clone https://github.com/<you>/odds-arbitrage-alerts && cd odds-arbitrage-alerts
python -m venv .venv && . .venv/bin/activate
pip install .
cp .env.example .env              # fill in SCRAPEUNBLOCKER_KEY and a destination
odds-arbitrage-alerts run --dry-run
odds-arbitrage-alerts run
```

Then schedule it with cron (`crontab -e`). Cron starts on time, unlike GitHub's scheduler, which can run late. This line checks every 15 minutes:

```cron
*/15 * * * * cd /path/to/odds-arbitrage-alerts && .venv/bin/odds-arbitrage-alerts run >> alerts.log 2>&1
```

The CLI reads `.env` from the working directory, and environment variables win over it. State is kept in `state/odds.json` next to `config.yml`.

## How it works

1. **Collect.** One request per page to the ScrapeUnblocker [Oddschecker API](https://www.scrapeunblocker.com/scrapers/oddschecker?utm_source=github&utm_medium=integration&utm_campaign=example-repos), `POST /sports/oddschecker-events?url=<page>`. The SDK has no dedicated method for this endpoint yet, so the request goes through the official Python SDK's generic call, `client.plugin("/sports/oddschecker-events", url=...)`. The response lists each upcoming event with its start time and markets, and gives each outcome its best price (decimal and fractional) and the bookmakers offering it. A failed request is retried twice (after 5 and 20 seconds). A page that still fails is reported (as a warning annotation in Actions) and skipped while the other pages run, and its state is left untouched. A page with no events, usually a wrong path, is reported without retrying.
2. **Process.**
   - **Which markets count:** pre-match only, so live and already-started events are skipped by default. Every outcome must have a price, and lines must match: Over/Under on the same total, or a handicap and its mirror (-9 / +9).
   - **Arbitrage:** `book = sum of 1/odds` over the best prices. When `(1 - book) x 100` is at least `min_arbitrage_percent` and at most `max_arbitrage_percent`, each outcome gets `total_stake x (1/odds) / book`, rounded to the penny. In the sample, `1/2.10 + 1/1.91 = 0.99975`, which is 0.025% under 100%, and staking £47.63 on Over and £52.37 on Under returns at least £100.02 either way. Anything above `max_arbitrage_percent` is almost always a missing outcome or a mistaken price that the bookmaker will void, so it is skipped.
   - **Once per arbitrage:** an open arbitrage is sent once, and again only when its percentage moves by `realert_change` points. When it closes it is forgotten, so if it reopens later it is sent again.
   - **Price moves:** each selection's best price is compared with a reference price, which is the price when the selection was first seen or last alerted. Slow drifts add up across runs, and after an alert the new price becomes the reference. When a line moves (Over 231.5 becomes Over 232.5), the new line starts with a fresh reference. Moves where both prices are above `max_odds` are ignored, because long shots move in big steps.
   - Every run also prints the three markets closest to an arbitrage, so the log shows how tight the books are even when nothing is sent.
3. **Deliver.** Alerts are rendered for each destination (Slack mrkdwn, Discord markdown, Telegram HTML, plain text plus JSON for the webhook). If no destination accepts the message, the state for those alerts is rolled back so the next run tries again.
4. **Save state.** `state/odds.json` holds the reference prices and the arbitrages already sent. An event is forgotten 2 days after it drops off the pages. In GitHub Actions the file lives in the **Actions cache**, not in the repo: a commit every 30 minutes would flood your history. Each run restores the newest `odds-state-*` cache and saves a new one. To start over, delete those caches under **Actions > Caches**. A second run with nothing new sends nothing and leaves the state as it was.

Two commands make no API calls: `odds-arbitrage-alerts status` prints what the state holds (tracked prices, open arbitrages already sent) and `test-destinations` checks your secrets.

## Config reference

Everything lives in [`config.yml`](config.yml). Secrets never go there.

| Key | Default | Meaning |
| - | - | - |
| `pages[].url` | (required) | An Oddschecker sport or competition page: the path after `oddschecker.com/` (`basketball/nba`) or the full link. Single-event links are rejected |
| `pages[].name` | from the path | Label used in messages and logs |
| `arbitrage.enabled` | `true` | Turn arbitrage alerts on or off |
| `arbitrage.min_arbitrage_percent` | `0.5` | Alert when the best prices add up to at least this much under 100%. `0` alerts on any arbitrage |
| `arbitrage.max_arbitrage_percent` | `10` | Skip anything larger: usually a data gap or a mistaken price |
| `arbitrage.total_stake` | `100` | Total the stake split is worked out for |
| `arbitrage.currency` | `£` | Label for amounts: a symbol (`£`, `€`, `$`) or a code (`USD`) |
| `arbitrage.realert_change` | `0.5` | Send an open arbitrage again when its percentage moves by this many points |
| `price_moves.enabled` | `true` | Turn price-move alerts on or off |
| `price_moves.min_move_percent` | `10` | Alert when a best price is this many percent away from its reference price |
| `price_moves.max_odds` | `10` | Ignore moves where both the old and new decimal price are above this |
| `markets` | `[]` (all) | Only these market names, e.g. `[Match Result, Win Market]`. The default pages return Match Result (football, tennis) and Win Market, Point Spread and Total Points (NBA) |
| `skip_live` | `true` | Leave out in-play and already-started events |
| `destinations` | all four | Any of `slack`, `discord`, `telegram`, `webhook`. A listed destination without its secret is skipped |
| `state_file` | `state/odds.json` | Where reference prices and sent arbitrages are kept, relative to `config.yml` |

More pages work the same way, for example `football/champions-league` or `american-football/nfl`. The schedule is in [`.github/workflows/run.yml`](.github/workflows/run.yml) (`cron: "7,37 * * * *"`, UTC). Unknown keys and invalid values stop the run with a clear error, so a typo cannot fail silently.

## Cost

Each run makes **one API request per page** in `pages`, and the [Oddschecker API](https://www.scrapeunblocker.com/scrapers/oddschecker?utm_source=github&utm_medium=integration&utm_campaign=example-repos) charges 1 credit per request. The response already holds every event, market and best price on the page.

With the default config (3 pages, every 30 minutes), that is **3 requests per run, 144 per day, about 4,320 per 30 days**. Retries after a failed request can add a few on a bad day. `--dry-run` makes the same requests as a real run, and `status` and `test-destinations` make none. To spend less, check less often (for example `cron: "7 * * * *"` for hourly, 72 a day) or watch fewer pages.

## Adding another destination

Destinations live behind one small interface in [`src/odds_arbitrage_alerts/sinks.py`](src/odds_arbitrage_alerts/sinks.py). A sink has a `name`, `send(alerts)` and `send_text(text)`, and raises `DeliveryError` on failure. For a service that takes text over a webhook, subclass `TextSink`. Here is Google Chat:

```python
from odds_arbitrage_alerts import messages
from odds_arbitrage_alerts.sinks import REGISTRY, SinkSpec, TextSink


class GoogleChatSink(TextSink):
    name = "google_chat"
    style = messages.PLAIN
    limit = 4000

    def __init__(self, webhook_url, http):
        super().__init__(http)
        self.webhook_url = webhook_url

    def url(self):
        return self.webhook_url

    def payload(self, text):
        return {"text": text}


REGISTRY["google_chat"] = SinkSpec(
    env=("GOOGLE_CHAT_WEBHOOK_URL",),
    build=lambda env, http: GoogleChatSink(env["GOOGLE_CHAT_WEBHOOK_URL"], http),
)
```

Then add `"google_chat"` to `DESTINATIONS` in `config.py`, list it under `destinations` in `config.yml`, and pass the secret to the run step in `run.yml`. For anything else, such as a database or a spreadsheet, implement `send(alerts)` and use each alert's fields (`alert.to_dict()`).

## Development

```bash
make install    # pip install -e ".[dev]"
make lint       # ruff check + ruff format --check
make test       # offline tests: the SDK and every destination are mocked, no API credit used
make dry-run    # real odds, nothing sent
```

The tests cover the arbitrage maths (book total, stake split and rounding, line matching), the thresholds, re-sending only when an arbitrage moves, closing and reopening, price moves against the reference price, live and started events, the market filter, two runs in a row delivering only new alerts, rollback when delivery fails, each destination's message format and payload, retries in collection, config validation and the CLI. CI runs them on Python 3.11 and 3.13.

## Links

- [ScrapeUnblocker](https://scrapeunblocker.com/?utm_source=github&utm_medium=integration&utm_campaign=example-repos): the scraping API behind this repo
- [Oddschecker API](https://www.scrapeunblocker.com/scrapers/oddschecker?utm_source=github&utm_medium=integration&utm_campaign=example-repos): the events and odds endpoints, with request examples
- [Documentation](https://docs.scrapeunblocker.com/?utm_source=github&utm_medium=integration&utm_campaign=example-repos)
- [Python SDK](https://docs.scrapeunblocker.com/sdks/python?utm_source=github&utm_medium=integration&utm_campaign=example-repos)
- Related: [sofascore-scraper](https://github.com/ScrapeUnblocker/sofascore-scraper) for live scores, fixtures, lineups and match stats as JSON or CSV.

## License

[MIT](LICENSE). Copyright (c) 2026 ScrapeUnblocker.
