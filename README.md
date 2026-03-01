# CT Amend Watch

Get instant Telegram notifications when new amendments are filed at the Connecticut General Assembly. No more manually refreshing the CGA website during session — this tool checks every 5 minutes and sends you a message with a direct link to the amendment PDF, the bill it applies to, and (optionally) an AI-generated plain-language summary.

**Free, open source, and takes about 10 minutes to set up. No coding required.**

## What You'll Get

A Telegram message like this every time a new amendment drops:

```
CT House amendment update
Date Rec.: 2/25/2026
LCO 2413
Bill: SB00298
Sched. Ltr.: A
Amendment: https://www.cga.ct.gov/2026/amd/S/pdf/2026SB-00298-R00HD-AMD.pdf

Summary: This amendment reallocates $12M in state funds to K-12
education and modifies reporting requirements for school districts.
No direct fiscal impact beyond the reallocation.
Relevance: 8/10
Bill status: https://www.cga.ct.gov/asp/CGABillStatus/cgabillstatus.asp?...
```

The summary and relevance score are optional add-on features — the basic setup just sends the amendment link and bill info.

## Setup Guide

### Step 1: Create a Telegram Bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Choose a display name (e.g., "CT Amendment Alerts")
4. Choose a username ending in `bot` (e.g., `ct_amend_alerts_bot`)
5. BotFather will reply with your **bot token** — it looks like `1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`. Save this.

### Step 2: Get Your Chat ID

1. Search for **@userinfobot** on Telegram and start a chat
2. It will reply with your **chat ID** — a number like `8366368439`. Save this.

> **Tip:** If you want notifications sent to a group or channel instead of a personal chat, add your bot to that group/channel first, then use the group's chat ID (which starts with `-`).

### Step 3: Fork This Repo

1. Click the **Fork** button at the top-right of this page
2. Keep all defaults and click **Create fork**
3. You now have your own copy of the project

### Step 4: Add Your Secrets

In **your forked repo** on GitHub:

1. Go to **Settings** (tab at the top of your repo)
2. In the left sidebar, click **Secrets and variables** > **Actions**
3. Click **New repository secret** and add each of these:

| Name | Value |
|------|-------|
| `TELEGRAM_BOT_TOKEN` | The bot token from Step 1 |
| `TELEGRAM_CHAT_ID` | The chat ID from Step 2 |

That's it for the basic setup. The AI summary features are optional — see [Enable AI Summaries](#optional-enable-ai-summaries) below.

### Step 5: Enable the Workflow

GitHub disables workflows on forked repos by default. To turn it on:

1. Go to the **Actions** tab in your forked repo
2. You'll see a banner saying "Workflows aren't being run on this forked repository." Click **I understand my workflows, go ahead and enable them**
3. In the left sidebar, click **CT Amend Watch**
4. Click **Enable workflow**

### Step 6: Verify It's Working

1. On the **Actions** tab, click **CT Amend Watch** in the left sidebar
2. Click the **Run workflow** dropdown (top right) and click **Run workflow**
3. Wait a minute or two for it to finish (you'll see a green checkmark)
4. The first run records the current state without sending notifications — this is normal
5. After the first run, you'll get a Telegram message the next time a new amendment is filed

The workflow runs automatically every 5 minutes. You don't need to do anything else.

## Choose Which Bills to Follow

By default, you get notifications for **every** amendment in both chambers. To narrow it down, edit `config.json` in your forked repo (you can do this right on GitHub — click the file, then click the pencil icon to edit).

**Watch specific bills only:**

```json
{
  "filter_mode": "watchlist",
  "watched_bills": ["SB00298", "HB05032"],
  "ignored_bills": [],
  "interests": [],
  "relevance_threshold": 4
}
```

**Get everything except certain bills:**

```json
{
  "filter_mode": "blocklist",
  "watched_bills": [],
  "ignored_bills": ["SB00001"],
  "interests": [],
  "relevance_threshold": 4
}
```

Bill numbers look like `SB00298` or `HB05032` — you can find them on the [CGA bill search page](https://www.cga.ct.gov/asp/CGABillStatus/cgabillstatus.asp). Filtering only affects notifications; the tool always tracks all amendments internally so you won't get duplicate alerts if you change your filters later.

## (Optional) Enable AI Summaries

You can have each amendment automatically summarized in plain language. This uses the Anthropic (Claude) API and costs fractions of a cent per summary — typical daily cost is well under $0.10 even during busy filing periods.

### Get an API Key

1. Go to [console.anthropic.com](https://console.anthropic.com) and create an account
2. Go to **API Keys** and click **Create Key**
3. Copy the key (it starts with `sk-ant-`)

### Add It to Your Repo

1. Go to **Settings** > **Secrets and variables** > **Actions** in your forked repo
2. Add a new secret:

| Name | Value |
|------|-------|
| `ANTHROPIC_API_KEY` | Your API key from above |

That's all — the workflow is already configured to use summaries when the key is present. No code changes needed.

### (Optional) AI Relevance Scoring

If summaries are enabled, you can also have the AI score each amendment's relevance to your specific interests on a 1-10 scale. Amendments below your threshold won't be sent.

Edit `config.json` to add your interests:

```json
{
  "filter_mode": "all",
  "watched_bills": [],
  "ignored_bills": [],
  "interests": [
    "K-12 education funding and teacher pay",
    "affordable housing and zoning reform",
    "state budget and appropriations"
  ],
  "relevance_threshold": 4
}
```

Amendments scoring below `relevance_threshold` (1-10 scale) are silently skipped. Set to `1` to see everything with a score.

## Troubleshooting

**"I forked the repo but nothing is happening"**
Workflows are disabled on forks by default. Go to the **Actions** tab and enable them (see Step 5 above).

**"The workflow ran but I didn't get a Telegram message"**
The first run never sends messages — it just records the current state. You'll get messages on subsequent runs when new amendments are actually filed. If amendments have been filed and you're still not getting messages, double-check your `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` secrets.

**"I'm getting too many notifications"**
Edit `config.json` to use a watchlist of specific bills, or enable AI relevance scoring to filter by your interests. See [Choose Which Bills to Follow](#choose-which-bills-to-follow).

**"The workflow is failing"**
Click on the failed run in the **Actions** tab to see the error log. The most common issues are incorrect secrets or the CGA website being temporarily down (it will retry on the next run).

**"I want to change my filters but I'm worried about missing amendments"**
Filters only affect which notifications are sent — the tool always tracks all amendments internally. Changing your filters won't cause old amendments to re-trigger.

## Advanced: VPS Deployment

If you prefer to self-host instead of using GitHub Actions, see [VPS_CRON.md](VPS_CRON.md) for instructions on running this via cron on a Linux server.

<details>
<summary><strong>Technical Reference</strong></summary>

### How It Works

1. Scrapes the CGA House and Senate amendment report pages using a headless browser
2. Parses the tabular text output to extract amendment metadata (LCO #, Bill #, Date Received, Schedule Letter)
3. Compares against `state.json` to identify new amendments since the last run
4. Applies bill watchlist/blocklist filters from `config.json`
5. Constructs the direct amendment PDF link from the LCO number (no bill status page scraping, respecting `robots.txt`)
6. (If enabled) Downloads the PDF, extracts text, and sends it to Claude Haiku for summarization
7. (If enabled) Scores the summary against configured interests for relevance filtering
8. Sends a Telegram message for each qualifying amendment

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes* | — | Telegram Bot API token |
| `TELEGRAM_CHAT_ID` | Yes* | — | Telegram chat/channel ID |
| `CT_SESSION_YEAR` | No | `2026` | CGA legislative session year |
| `CT_AMEND_DEBUG` | No | `0` | `1` for verbose debug output |
| `CT_REQUIRE_TELEGRAM` | No | `0` | `1` to fail if Telegram creds missing |
| `CT_ENABLE_SUMMARY` | No | `0` | `1` to enable AI summaries (also requires `ANTHROPIC_API_KEY`) |
| `ANTHROPIC_API_KEY` | No | — | Claude API key for summaries and relevance scoring |
| `CT_AMEND_STATE_PATH` | No | `./state.json` | Custom state file path |
| `CT_AMEND_CONFIG_PATH` | No | `./config.json` | Custom config file path |
| `CT_HOUSE_LAST_LCO` | No | — | Override starting LCO for House |
| `CT_SENATE_LAST_LCO` | No | — | Override starting LCO for Senate |

*\*Required unless `CT_REQUIRE_TELEGRAM=0`, in which case it runs in scrape-only mode.*

### Dependencies

| Package | Purpose |
|---------|---------|
| `playwright` | Headless browser for scraping CGA report pages |
| `requests` | HTTP client for Telegram API and PDF downloads |
| `pdfplumber` | PDF text extraction for AI summaries |
| `anthropic` | Claude API client for summaries and relevance scoring |

### State Tracking

State is persisted in `state.json`. Each run compares the newest LCO on the report page against the stored value. Writes are atomic (write to `.tmp`, then rename) to prevent corruption.

### Project Structure

```
ct-amend-watch/
├── watch_amend.py              # Main watcher script
├── config.json                 # Filtering configuration
├── requirements.txt            # Python dependencies
├── .env.example                # Environment variable template
├── state.json                  # Watcher state (auto-generated)
├── .github/workflows/
│   └── watch-amend.yml         # GitHub Actions workflow
├── scripts/
│   └── run_cron.sh             # VPS cron wrapper
├── VPS_CRON.md                 # VPS deployment guide
└── feature_ideas.md            # Feature roadmap
```

</details>

## License

MIT License. See [LICENSE](LICENSE) for details.
