# Base Token Alert Bot

A clean, shareable **alert-only** bot for Base tokens.

This repository is intentionally sanitized for public sharing: no secrets, no runtime state, and no machine-specific absolute paths.

---

## English

### What it does
- Scans Base token candidates via GMGN + DexScreener.
- Sends Telegram alerts for tokens that pass the filters.
- Separates **MCap** and **FDV** so the alert is easier to read.
- Adds milestone follow-ups for tokens that start moving.
- Keeps the repo portable and safe to publish.

### What is included
- `base-alert-bot.py` — main alert engine
- `run-loop.sh` — simple loop runner
- `watchdog.py` — lightweight watchdog for the loop
- `base-smart-x-accounts.txt` — public Base/KOL X watchlist
- `.env.example` — safe config template with no secrets
- `.gitignore` — excludes secrets, logs, state, caches, and build artifacts

### What is excluded
- Real `.env` files
- API keys
- X cookies / auth tokens
- runtime logs
- runtime state files
- virtualenvs, caches, `__pycache__`

### Quick start
1. Copy `.env.example` to `.env`
2. Fill in the required values
3. Run the bot:

```bash
bash run-loop.sh
```

Or run a single scan:

```bash
python3 base-alert-bot.py
```

### Environment variables
See `.env.example` for the full list. The most important ones are:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `GMGN_API_KEY`
- `MC_MAX_USD`
- `MIN_LIQUIDITY_USD`
- `ALERT_THRESHOLD`

### Safety notes
- This repo is designed to be shared publicly.
- Paths are portable and resolve from the script location.
- Before publishing, verify there are no secrets in the tree.
- If you want to push this to GitHub, add your remote and push `main`.

---

## Bahasa Indonesia

### Apa fungsinya
- Memindai kandidat token Base lewat GMGN + DexScreener.
- Mengirim alert Telegram untuk token yang lolos filter.
- Memisahkan **MCap** dan **FDV** supaya alert lebih mudah dibaca.
- Menambahkan follow-up milestone saat token mulai bergerak.
- Menjaga repo tetap portable dan aman untuk dibagikan.

### Isi repo
- `base-alert-bot.py` — mesin alert utama
- `run-loop.sh` — runner loop sederhana
- `watchdog.py` — watchdog ringan untuk menjaga loop tetap hidup
- `base-smart-x-accounts.txt` — watchlist publik akun X Base/KOL
- `.env.example` — template konfigurasi aman tanpa secret
- `.gitignore` — mengabaikan secret, log, state, cache, dan artifact build

### Yang tidak ikut disertakan
- File `.env` asli
- API key
- Cookie / auth token X
- Log runtime
- File state runtime
- Virtualenv, cache, `__pycache__`

### Cara cepat pakai
1. Copy `.env.example` menjadi `.env`
2. Isi nilai yang dibutuhkan
3. Jalankan bot:

```bash
bash run-loop.sh
```

Atau jalankan sekali saja:

```bash
python3 base-alert-bot.py
```

### Environment variables
Lihat `.env.example` untuk daftar lengkap. Yang paling penting:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `GMGN_API_KEY`
- `MC_MAX_USD`
- `MIN_LIQUIDITY_USD`
- `ALERT_THRESHOLD`

### Catatan keamanan
- Repo ini memang disiapkan untuk dibagikan publik.
- Path dibuat portable dan mengikuti lokasi file script.
- Sebelum publish, pastikan tidak ada secret di dalam tree repo.
- Kalau mau di-push ke GitHub, tambahkan remote lalu push branch `main`.

---

## GitHub publish / Publish ke GitHub

If you want to publish it manually from this machine, the repo is already ready.

Kalau ingin publish manual dari mesin ini, repo-nya sudah siap.

```bash
cd /root/base-token-alert-github
git status --short
git remote add origin https://github.com/<username>/<repo-name>.git
git push -u origin main
```

If you want, I can also prepare a shorter repository description and a release note / changelog section.

Kalau kamu mau, saya juga bisa siapkan deskripsi repo yang lebih singkat dan bagian release note / changelog.
