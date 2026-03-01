# CT Amend Watch

Monitors Connecticut General Assembly (CGA) amendment reports for both House and Senate chambers and sends real-time notifications via Telegram when new amendments are filed.

Designed to run unattended every 5 minutes via GitHub Actions or a VPS cron job.

## How It Works

1. **Scrapes** the CGA House and Senate amendment report pages using Playwright (headless Chromium)
2. **Parses** the tabular text output to extract amendment metadata: Calendar #, LCO #, Bill #, Date Received, and Schedule Letter
3. **Compares** against a persisted `state.json` to identify new amendments since the last run
4. **Filters** amendments against an optional bill watchlist/blocklist (`config.json`)
5. **Resolves** the direct amendment PDF link by fetching the bill status page and matching the LCO number against Called/Uncalled amendment sections
6. **Sends** a Telegram message for each new amendment with the direct amendment link, bill status URL, and all relevant metadata

## Data Sources

| Chamber | Report URL |
|---------|-----------|
| House | [CGAHouseAmendRptDisp.asp](https://www.cga.ct.gov/asp/CGAAmendProc/CGAHouseAmendRptDisp.asp?optSortby=D&optSortOrder=Desc) |
| Senate | [CGASenateAmendRptDisp.asp](https://www.cga.ct.gov/asp/CGAAmendProc/CGASenateAmendRptDisp.asp?optSortby=D&optSortOrder=Desc) |

## Telegram Notification Example

```
CT House amendment update
Date Rec.: 2/25/2026
LCO 2413
Bill: SB00298
Sched. Ltr.: A
Amendment: https://www.cga.ct.gov/2026/amd/S/pdf/2026SB-00298-R00HD-AMD.pdf
Bill status: https://www.cga.ct.gov/asp/CGABillStatus/cgabillstatus.asp?...
```

## Quick Start (Local)

### Prerequisites

- Python 3.11+
- A Telegram bot token and chat ID ([how to create a bot](https://core.telegram.org/bots#how-do-i-create-a-bot))

### Setup

```bash
git clone <your-repo-url> ct-amend-watch
cd ct-amend-watch

python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate    # Windows

pip install -r requirements.txt
python -m playwright install --with-deps chromium
```

### Configure

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```dotenv
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
CT_SESSION_YEAR=2026
CT_REQUIRE_TELEGRAM=1
CT_AMEND_DEBUG=0
```

### Run

```bash
python watch_amend.py
```

On the first run with no prior `state.json`, it records the current newest LCO for each chamber without sending notifications. Subsequent runs detect and notify on anything newer.

## Bill Filtering

By default, all amendments trigger notifications. To filter by bill number, edit `config.json`:

```json
{
  "filter_mode": "watchlist",
  "watched_bills": ["SB00298", "HB05032", "HB06450"],
  "ignored_bills": []
}
```

**Filter modes:**

| Mode           | Behavior                                                     |
| -------------- | ------------------------------------------------------------ |
| `"all"`        | Notify on every amendment (default)                          |
| `"watchlist"`  | Only notify on amendments to bills listed in `watched_bills` |
| `"blocklist"`  | Notify on everything except bills listed in `ignored_bills`  |

Filtering only affects notifications. State tracking always advances past all amendments regardless of filter, so filtered amendments won't re-trigger on the next run.

The config file path can be overridden with the `CT_AMEND_CONFIG_PATH` environment variable.

## Deployment

### Option A: GitHub Actions (Recommended)

The included workflow at `.github/workflows/watch-amend.yml` runs every 5 minutes automatically.

**Setup:**

1. Push the repo to GitHub
2. Add repository secrets:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
3. The workflow handles everything else: Python setup, Playwright install, running the watcher, and committing `state.json` back to the repo

**Key workflow features:**
- Concurrency group prevents overlapping runs
- 10-minute timeout safety net
- Playwright browser caching for faster cold starts
- State auto-committed with `[skip ci]` to avoid triggering recursive runs

### Option B: VPS Cron

See [VPS_CRON.md](VPS_CRON.md) for step-by-step instructions on running this via cron on a Linux VPS.

Uses `flock` for mutual exclusion so overlapping runs are safely skipped.

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes* | — | Telegram Bot API token |
| `TELEGRAM_CHAT_ID` | Yes* | — | Telegram chat/channel ID to send messages to |
| `CT_SESSION_YEAR` | No | `2026` | CGA legislative session year |
| `CT_AMEND_DEBUG` | No | `0` | Set to `1` for verbose debug output |
| `CT_REQUIRE_TELEGRAM` | No | `0` | Set to `1` to fail if Telegram creds are missing |
| `CT_AMEND_STATE_PATH` | No | `./state.json` | Custom path for the state file |
| `CT_AMEND_CONFIG_PATH` | No | `./config.json` | Custom path for the filter config file |
| `CT_HOUSE_LAST_LCO` | No | — | Override starting LCO for House (used if no state.json) |
| `CT_SENATE_LAST_LCO` | No | — | Override starting LCO for Senate (used if no state.json) |

*\*Required unless `CT_REQUIRE_TELEGRAM=0`, in which case it runs in scrape-only mode.*

## State Tracking

State is persisted in `state.json`:

```json
{
  "house_last_lco": "2413",
  "senate_last_lco": "2299"
}
```

Each run compares the newest LCO on the report page against the stored value. Any amendments with a higher sort position (newer date, then higher LCO as tiebreaker) are treated as new and trigger notifications. After processing, the newest LCO is saved back.

Writes are atomic (write to `.tmp`, then `os.replace`) to prevent corruption if the process is interrupted.

## Dependencies

| Package | Purpose |
|---------|---------|
| `playwright` | Headless browser for scraping CGA report pages |
| `requests` | HTTP client for Telegram API and bill status page fetches |
| `beautifulsoup4` | HTML parsing for locating amendment PDF links |
| `lxml` | Fast HTML parser backend for BeautifulSoup |

## Project Structure

```
ct-amend-watch/
├── watch_amend.py                  # Main watcher script
├── config.json                     # Bill filtering configuration
├── requirements.txt                # Python dependencies
├── .env.example                    # Environment variable template
├── state.json                      # Persisted watcher state (auto-generated)
├── .github/workflows/
│   └── watch-amend.yml             # GitHub Actions workflow
├── scripts/
│   └── run_cron.sh                 # VPS cron wrapper with flock
├── VPS_CRON.md                     # VPS deployment guide
└── feature_ideas.md                # Planned feature roadmap
```

## License

Private project. Not currently licensed for redistribution.
