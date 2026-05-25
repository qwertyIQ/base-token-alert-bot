# Base Token Alert Bot

Alert-only Base token scanner focused on fast discovery, quality filtering, and concise Telegram alerts.

This project is intentionally separate from Charon.

## Overview

The bot watches the Base chain using a GMGN-first flow, with DexScreener used as supplemental discovery and enrichment.

Features:
- Base token discovery and alerting
- Liquidity and quality filters before alerting
- Honeypot / unknown tax / high tax blocking
- 100%+ pump / volume surge detection
- Launchpad and source metadata where available
- Telegram alerts only, no auto-buy
- Optional X / smart-follower enrichment with Scweet
- Threaded follow-up updates for price milestones

## Project Layout

- Project root: `/root/base-token-alert`
- Main bot: `base-alert-bot.py`
- Social worker: `social_scweet.py`
- Social worker runner: `social_scweet_worker.py`
- Watchdog: `watchdog.py`
- GMGN wallet collector: `collect-gmgn-wallets.py`
- Local secrets: `/root/base-token-alert/secrets/`
- Runtime state: `/root/base-token-alert/state/`

## Requirements

- Python 3.10+
- A Telegram bot token
- A Telegram chat ID
- Network access to GMGN / DexScreener / Telegram
- Optional: Base RPC URL
- Optional: Basescan API key
- Optional: X / Twitter cookie auth for Scweet enrichment

## Install

```bash
cd /root/base-token-alert
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

If the project does not yet have a `requirements.txt`, install the packages used by the scripts manually.

## Configuration

Create a `.env` file in the project root.

Minimum config:

```env
TELEGRAM_BOT_TOKEN=REDACTED
TELEGRAM_CHAT_ID=REDACTED
```

Recommended safety and tuning knobs:

```env
MIN_LIQUIDITY_USD=12000
MIN_QUALITY_WALLET_LIQUIDITY_USD=12000
ALERT_THRESHOLD=62
MAX_FOLLOWUPS_PER_RUN=10
```

Optional X / Scweet settings:

```env
SCWEET_ENABLED=true
SCWEET_FOLLOWER_LIMIT=5000
SCWEET_CACHE_HOURS=12
SCWEET_COOKIES_FILE=/root/base-token-alert/secrets/scweet-cookies.json
```

## Run

Start the bot:

```bash
cd /root/base-token-alert
source .venv/bin/activate
python3 base-alert-bot.py
```

Run the social worker separately if needed:

```bash
cd /root/base-token-alert
source .venv/bin/activate
python3 social_scweet_worker.py
```

## Verification

Check the bot starts without syntax errors:

```bash
cd /root/base-token-alert
python3 -m py_compile base-alert-bot.py social_scweet.py social_scweet_worker.py watchdog.py collect-gmgn-wallets.py
```

Watch runtime logs in your deployment method, then confirm:
- the process is alive
- alerts are being delivered to Telegram
- no secret values are printed in logs

## Scweet Cookie Setup

Store the X auth cookie locally only:

- Path: `/root/base-token-alert/secrets/scweet-cookies.json`
- File mode: `600`
- Do not paste the token into chat or logs

## Deploy on VPS

Typical deployment pattern:
- run the bot as a `systemd` service
- keep secrets in `/root/base-token-alert/secrets/`
- keep runtime state in `/root/base-token-alert/state/`
- restart and verify after every code change

## Publish to GitHub

Before uploading manually:
- remove secrets from the repo
- keep `.env` out of version control
- keep `secrets/`, `state/`, and log files ignored
- verify the tree contains no tokens, cookies, or local-only paths that should stay private

## Notes

- This bot is alert-only and does not place trades.
- Smart-follower enrichment is optional and should fail gracefully if X scraping is unavailable.
- The project prefers concise alerts and safe defaults over noisy output.

