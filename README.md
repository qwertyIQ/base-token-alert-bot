# Base Token Alert Bot

Bot alert-only untuk token Base. Fokusnya adalah:

- memindai kandidat token Base via GMGN + DexScreener;
- mengirim alert Telegram untuk token yang lolos filter;
- memisahkan **MCap** dan **FDV**;
- menambahkan follow-up milestone pada token yang bergerak;
- menjaga repo ini tetap **bersih** dari secret dan data runtime.

## Yang disimpan di repo ini

- `base-alert-bot.py` — engine alert utama
- `run-loop.sh` — loop runner
- `watchdog.py` — watchdog sederhana untuk memastikan loop tetap hidup
- `base-smart-x-accounts.txt` — watchlist publik akun X Base/KOL
- `.env.example` — template konfigurasi tanpa secret
- `.gitignore` — menghindari file sensitif/runtime ikut ter-commit

## Yang *tidak* disimpan

- `.env` asli
- API key
- cookie / auth token X
- log runtime
- state runtime
- virtualenv / cache / pyc

## Cara pakai

1. Copy `.env.example` ke `.env`
2. Isi variabel yang diperlukan
3. Jalankan:

```bash
bash run-loop.sh
```

Atau jalankan sekali:

```bash
python3 base-alert-bot.py
```

## Environment variables

Lihat `.env.example` untuk daftar lengkap. Yang paling penting:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `GMGN_API_KEY`
- `MC_MAX_USD`
- `MIN_LIQUIDITY_USD`
- `ALERT_THRESHOLD`

## Catatan

- Repo ini sengaja dibuat tanpa secret.
- Path sudah dibuat portable, jadi bisa dijalankan dari folder clone mana pun.
- Jika ingin push ke GitHub, cukup `git remote add origin ...` lalu `git push -u origin main`.
