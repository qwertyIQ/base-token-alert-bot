#!/usr/bin/env python3
"""Base token alert bot (GMGN-first + DexScreener new-token supplement).

Alert-only scanner for Base tokens. It polls GMGN trending + smart/KOL feeds,
then supplements with DexScreener latest Base token profiles/boosts so new tokens
with strong early pumps are not ignored. No trading side effects.
"""
import html, json, os, subprocess, time, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = ROOT / 'state'; STATE.mkdir(exist_ok=True)
SEEN = STATE / 'seen_alerts.json'
ALERT_STATE = STATE / 'alert_state.json'
WALLETS = STATE / 'gmgn_base_wallets_latest.json'
SMART_X_ACCOUNTS = ROOT / 'base-smart-x-accounts.txt'
CHAIN = 'base'

# Holder analysis thresholds
MIN_TOP50_ETH_VALUE   = 0.1   # ETH — jika 50 holder teratas < 0.1 ETH, turunkan confidence
MIN_FRESH_WALLET_BALANCE = 0.01  # ETH — fresh wallet dengan < 0.01 ETH dianggap negligible
MAX_FRESH_WALLET_PCT   = 0.50  # >50% top-50 holders fresh wallet = DANGER alert
HOLDER_FETCH_LIMIT    = 50   # ambil 50 holder teratas untuk analisis


def load_env():
    env = dict(os.environ)
    p = ROOT / '.env'
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def fnum(v, default=0.0):
    try:
        return float(v or 0)
    except Exception:
        return default


def deep_num(obj, *paths, default=0.0):
    """Return the first positive numeric value found in dot-paths or keys.

    Examples:
      deep_num(row, 'market_cap', 'price.price', 'pool.liquidity')
    """
    for path in paths:
        cur = obj
        ok = True
        for part in str(path).split('.'):
            if not isinstance(cur, dict) or part not in cur:
                ok = False
                break
            cur = cur.get(part)
            if cur in (None, ''):
                ok = False
                break
        if ok:
            val = fnum(cur)
            if val > 0:
                return val
    return default


def rows_from(data):
    if isinstance(data, dict):
        for k in ('list', 'data', 'rows', 'result'):
            if isinstance(data.get(k), list):
                return data[k]
    return data if isinstance(data, list) else []


def run_gmgn(args, env, timeout=80):
    p = subprocess.run(['npx', 'gmgn-cli', *args], cwd=ROOT, env=env, text=True, capture_output=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or '').strip()[:500])
    return json.loads(p.stdout or '{}')


# ── Holder analysis ───────────────────────────────────────────────────────────

def fetch_top_holders(addr, env, limit=HOLDER_FETCH_LIMIT):
    """Ambil top-N holder dari GMGN. Returns list of holder dicts or empty list."""
    try:
        data = run_gmgn(['token', 'holders', '--chain', CHAIN, '--address', addr,
                         '--limit', str(limit), '--raw'], env, timeout=40)
        return rows_from(data)
    except Exception:
        return []


def analyze_holders(holders):
    """Analyze top holders untuk fresh wallet & low-value detection.
    
    Returns dict:
      - fresh_count    : number of fresh wallets in top-N
      - fresh_pct      : fraction (0-1) of fresh wallets
      - avg_eth        : average ETH value across all top-N
      - has_low_eth    : True if avg ETH < MIN_TOP50_ETH_VALUE
      - is_danger      : True if fresh_pct > MAX_FRESH_WALLET_PCT
      - signals        : list of human-readable warning strings
    """
    if not holders:
        return {'fresh_count': 0, 'fresh_pct': 0.0, 'avg_eth': 0.0,
                'has_low_eth': False, 'is_danger': False, 'signals': []}

    signals = []
    total_eth = 0.0
    fresh_count = 0

    for h in holders:
        # native_balance is in wei — convert to ETH (1 ETH = 1e18 wei)
        try:
            balance = float(h.get('native_balance', 0))
        except (TypeError, ValueError):
            balance = 0.0
        eth_val = balance / 1e18

        # Check if fresh wallet: tags include 'fresh_wallet' or wallet_tag_v2 starts with 'fresh'
        tags = h.get('tags', []) or []
        wallet_tag = str(h.get('wallet_tag_v2', '')).lower()
        is_fresh = ('fresh_wallet' in tags or 
                    wallet_tag.startswith('fresh') or 
                    'new' in wallet_tag)

        if is_fresh:
            fresh_count += 1

        # Fresh wallet dengan balance < threshold dianggap negligible
        if is_fresh and eth_val < MIN_FRESH_WALLET_BALANCE:
            signals.append(f"  ⚠️ Fresh wallet {h.get('address','')[:10]}... hanya {eth_val:.4f} ETH — suspect/negligible")

        total_eth += eth_val

    n = len(holders)
    avg_eth = total_eth / n if n > 0 else 0.0
    fresh_pct = fresh_count / n if n > 0 else 0.0

    result = {
        'fresh_count': fresh_count,
        'fresh_pct': fresh_pct,
        'avg_eth': avg_eth,
        'has_low_eth': avg_eth < MIN_TOP50_ETH_VALUE,
        'is_danger': fresh_pct > MAX_FRESH_WALLET_PCT,
        'signals': signals,
    }

    # Build human-readable signals
    if result['has_low_eth']:
        signals.append(f"  ⚠️ Rata-rata top-{n} holder hanya {avg_eth:.4f} ETH — likely puppet/flash-run")
        signals.append(f"  → Confidence diturunkan: top holder tidak credible")

    if result['is_danger']:
        signals.append(f"  🚨 DANGER: {fresh_count}/{n} holder ({fresh_pct*100:.0f}%) adalah fresh wallet tag!")
        signals.append(f"  → Sangat likely bot/flash-degen操纵 — HIGH RISK!")

    result['signals'] = signals
    return result


def fetch_json(url, timeout=20):
    req = urllib.request.Request(url, headers={'User-Agent': 'base-token-alert/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'replace'))


def token_addr(row):
    return (row.get('address') or row.get('token_address') or row.get('base_address') or
            row.get('ca') or row.get('contract_address') or
            (row.get('baseToken') or {}).get('address'))


def symbol(row):
    return (row.get('symbol') or row.get('ticker') or
            (row.get('base_token') or {}).get('symbol') or
            (row.get('baseToken') or {}).get('symbol') or 'UNKNOWN')


def nested_num(obj, *path):
    cur = obj
    for key in path:
        if not isinstance(cur, dict):
            return 0.0
        cur = cur.get(key)
    return fnum(cur)


def metric(row, info, *keys):
    for obj in (row, info or {}):
        if not isinstance(obj, dict):
            continue
        for k in keys:
            if k in obj and obj.get(k) not in (None, ''):
                return fnum(obj.get(k))
    return 0.0


def price_change(row, info):
    # GMGN common fields first, then DexScreener nested fields. Use the strongest
    # positive short-term window so 6h/24h 100%+ runners are not missed just
    # because h1/m5 cooled off.
    values = []
    for obj in (row, info or {}):
        if not isinstance(obj, dict):
            continue
        for k in ('price_change_percent5m', 'price_change_percent1h', 'price_change_percent6h', 'price_change_percent24h',
                  'change5m', 'change1h', 'change6h', 'change24h',
                  'price_change_m5', 'price_change_1h', 'price_change_6h', 'price_change_24h',
                  'price_change_h1', 'price_change_h6', 'price_change_h24'):
            if k in obj and obj.get(k) not in (None, ''):
                values.append(fnum(obj.get(k)))
        pc = obj.get('priceChange') or {}
        if isinstance(pc, dict):
            for tf in ('m5', 'h1', 'h6', 'h24'):
                if pc.get(tf) not in (None, ''):
                    values.append(fnum(pc.get(tf)))
    if not values:
        return 0.0
    positives = [v for v in values if v > 0]
    return max(positives) if positives else max(values)


def volume_usd(row, info):
    values = []
    for obj in (row, info or {}):
        if not isinstance(obj, dict):
            continue
        values.extend([
            deep_num(obj, 'volume', 'volume_24h', 'volume_6h', 'volume_1h', 'volume_5m', 'volume_usd'),
            deep_num(obj, 'volume.m5', 'volume.h1', 'volume.h6', 'volume.h24'),
            deep_num(obj, 'price.volume_24h', 'price.volume_6h', 'price.volume_1h', 'price.volume_5m'),
        ])
    values = [v for v in values if v > 0]
    return max(values) if values else 0.0


def swaps_count(row, info):
    direct = metric(row, info, 'swaps', 'swaps_1h', 'txns')
    if direct:
        return direct
    for obj in (row, info or {}):
        if isinstance(obj, dict):
            tx = obj.get('txns') or {}
            if isinstance(tx, dict):
                for tf in ('h1', 'h6', 'h24', 'm5'):
                    bucket = tx.get(tf) or {}
                    if isinstance(bucket, dict):
                        total = fnum(bucket.get('buys')) + fnum(bucket.get('sells'))
                        if total:
                            return total
    return 0.0


def market_cap(row, info):
    # Prefer explicit market-cap fields, then derive from price × supply.
    for obj in (row, info or {}):
        if not isinstance(obj, dict):
            continue
        val = deep_num(
            obj,
            'market_cap', 'marketCap', 'marketcap', 'mcap',
            'market_cap_usd', 'market_cap_quote',
            'stat.market_cap', 'stat.marketCap', 'dev.market_cap', 'dev.marketCap',
            'price.market_cap', 'price.marketCap',
        )
        if val > 0:
            return val

    price = price_usd(row, info)
    for obj in (row, info or {}):
        if not isinstance(obj, dict):
            continue
        circ = deep_num(
            obj,
            'circulating_supply', 'circulatingSupply', 'circulating',
            'token.circulating_supply', 'token.circulatingSupply',
            'info.circulating_supply',
            'stat.circulating_supply', 'stat.circulatingSupply',
        )
        if price and circ:
            return price * circ
    return 0.0

def fdv(row, info):
    """Fully diluted valuation — from explicit fields or price × max/total supply."""
    dev = (info or {}).get('dev') or {}
    stat = (info or {}).get('stat') or {}
    for k in ('fdv', 'fully_diluted_valuation', 'max_supply_mcap', 'fullyDilutedValuation'):
        for obj in (row, info, dev, stat):
            if not isinstance(obj, dict):
                continue
            v = deep_num(
                obj,
                k,
                f'{k}.value',
                f'{k}.usd',
            )
            if v > 0:
                return v
    # Fallback: price × max/total supply
    price = price_usd(row, info)
    for obj in (row, info or {}):
        if not isinstance(obj, dict):
            continue
        total = deep_num(
            obj,
            'max_supply', 'maxSupply', 'total_supply', 'totalSupply',
            'token.max_supply', 'token.total_supply',
            'info.max_supply', 'info.total_supply',
            'stat.total_supply', 'stat.max_supply',
        )
        if price and total:
            return price * total
    return 0.0


def liquidity_usd(row, info):
    values = []
    for obj in (row, info or {}):
        if not isinstance(obj, dict):
            continue
        values.extend([
            deep_num(obj, 'liquidity_usd', 'liquidityUsd', 'liq_usd', 'liquidity'),
            deep_num(obj, 'liquidity.usd', 'pool.liquidity', 'pool.liquidity_usd', 'price.liquidity_usd'),
        ])
    values = [v for v in values if v > 0]
    return max(values) if values else 0.0


def price_usd(row, info):
    for obj in (row, info or {}):
        if not isinstance(obj, dict):
            continue
        val = deep_num(
            obj,
            'priceUsd', 'price_usd', 'priceUSD', 'usd_price', 'price',
            'price.price', 'price.usd', 'price.priceUsd', 'price.price_usd',
        )
        if val > 0:
            return val
    return 0.0


def load_json_file(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def save_json_file(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True))


def age_hours(row, info):
    now_ms = time.time() * 1000
    for obj in (row, info or {}):
        if not isinstance(obj, dict):
            continue
        created = obj.get('pairCreatedAt') or obj.get('created_at') or obj.get('creation_time') or obj.get('open_timestamp')
        if not created:
            continue
        try:
            ts = float(created)
            if ts < 10_000_000_000:  # seconds -> ms
                ts *= 1000
            return max(0.0, (now_ms - ts) / 3_600_000)
        except Exception:
            pass
    return 0.0


def load_wallets():
    if not WALLETS.exists():
        return {'strict': [], 'whales': [], 'broad': []}
    try:
        data = json.loads(WALLETS.read_text())
    except Exception:
        return {'strict': [], 'whales': [], 'broad': []}
    return {
        'strict': data.get('strict_wallets') or [],
        'whales': data.get('whale_wallets_200k') or [],
        'broad': data.get('broad_wallets') or [],
    }


def recent_smart_kol(env):
    out = []
    for kind in ('smartmoney', 'kol'):
        for side in ('buy', 'sell'):
            try:
                data = run_gmgn(['track', kind, '--chain', CHAIN, '--limit', '100', '--side', side, '--raw'], env, timeout=60)
            except Exception:
                continue
            for r in rows_from(data):
                out.append({
                    **r,
                    '_kind': kind,
                    '_side': side,
                    '_wallet': (r.get('maker') or r.get('address') or r.get('wallet') or '').lower(),
                    '_token': (r.get('base_address') or r.get('token_address') or '').lower(),
                })
    return out


def wallet_hits_for_token(addr, feeds, wallets):
    addr = (addr or '').lower(); hits = []
    strict = {w.get('wallet', '').lower(): w for w in wallets['strict']}
    whales = {w.get('wallet', '').lower(): w for w in wallets['whales']}
    broad = {w.get('wallet', '').lower(): w for w in wallets['broad']}
    for r in feeds:
        if r.get('_token') != addr:
            continue
        w = r.get('_wallet')
        tier = 'feed'; meta = None
        if w in strict:
            tier = 'strict_smart'; meta = strict[w]
        elif w in whales:
            tier = 'whale_200k'; meta = whales[w]
        elif w in broad:
            tier = 'broad_smart'; meta = broad[w]
        hits.append({'wallet': w, 'kind': r.get('_kind'), 'side': r.get('_side'), 'amount_usd': fnum(r.get('amount_usd')), 'tier': tier, 'meta': meta})
    return hits


def find_first_url(value, needles=()):
    """Extract a useful URL from DexScreener/GMGN mixed metadata."""
    needles = tuple(n.lower() for n in needles)
    found = []

    def walk(v):
        if isinstance(v, str):
            s = v.strip()
            if s.startswith('http://') or s.startswith('https://'):
                low = s.lower()
                if not needles or any(n in low for n in needles):
                    found.append(s)
            return
        if isinstance(v, dict):
            for item in v.values():
                walk(item)
        elif isinstance(v, list):
            for item in v:
                walk(item)

    walk(value)
    return found[0] if found else ''


def links_for(row, info, addr):
    data = [row, info or {}]
    dex = ''
    twitter = ''
    website = ''
    telegram = ''
    for obj in data:
        if not isinstance(obj, dict):
            continue
        dex = dex or obj.get('url') or obj.get('dexscreenerUrl') or find_first_url(obj, ('dexscreener.com',))
        twitter = twitter or obj.get('twitter') or obj.get('twitter_url') or find_first_url(obj, ('twitter.com', 'x.com'))
        website = website or obj.get('website') or find_first_url(obj.get('websites') or obj.get('website') or {}, ())
        telegram = telegram or obj.get('telegram') or find_first_url(obj, ('t.me/', 'telegram'))
    return {
        'gmgn': f'https://gmgn.ai/base/token/{addr}',
        'dex': dex or f'https://dexscreener.com/base/{addr}',
        'twitter': twitter,
        'website': website,
        'telegram': telegram,
    }


def launchpad_for(row, info):
    """Best-effort launchpad/source detector for Base token alerts."""
    data = [row, info or {}]
    direct_keys = (
        'launchpad', 'launchpadName', 'launchpad_name', 'platform', 'source',
        'poolSource', 'pool_source', 'router', 'factory', 'exchange', 'dexId',
        'pairLabel', 'label', 'quoteLabel', 'protocol', 'origin'
    )
    values = []
    for obj in data:
        if not isinstance(obj, dict):
            continue
        for key in direct_keys:
            value = obj.get(key)
            if value not in (None, '', [], {}):
                values.append(str(value))
        for nested_key in ('pair', 'pool', 'baseToken', 'quoteToken', 'info'):
            nested = obj.get(nested_key)
            if isinstance(nested, dict):
                for key in direct_keys:
                    value = nested.get(key)
                    if value not in (None, '', [], {}):
                        values.append(str(value))
    text = ' '.join(values).lower() + ' ' + json.dumps(data, default=str).lower()[:12000]
    known = [
        ('zora', 'Zora'),
        ('clanker', 'Clanker'),
        ('virtuals', 'Virtuals'),
        ('flaunch', 'Flaunch'),
        ('wow', 'WOW'),
        ('bankr', 'Bankr'),
        ('bnkr', 'Bankr/BNKR'),
        ('ape.store', 'Ape.Store'),
        ('ape store', 'Ape.Store'),
        ('uniswap', 'Uniswap'),
        ('aerodrome', 'Aerodrome'),
        ('baseswap', 'BaseSwap'),
        ('sushiswap', 'SushiSwap'),
        ('pancakeswap', 'PancakeSwap'),
        ('balancer', 'Balancer'),
        ('maverick', 'Maverick'),
        ('velodrome', 'Velodrome'),
    ]
    for needle, label in known:
        if needle in text:
            return label
    dex_id = ''
    for obj in data:
        if isinstance(obj, dict) and obj.get('dexId'):
            dex_id = str(obj.get('dexId'))
            break
    if dex_id:
        return dex_id.replace('-', ' ').replace('_', ' ').title()
    return 'Unknown / perlu cek manual'


def compact_int(n):
    n = fnum(n)
    if n >= 1_000_000:
        return f'{n/1_000_000:.1f}M'.rstrip('0').rstrip('.')
    if n >= 1_000:
        return f'{n/1_000:.1f}K'.rstrip('0').rstrip('.')
    return f'{int(n):,}'


def twitter_handle(url):
    if not url:
        return ''
    try:
        path = urllib.parse.urlparse(url).path.strip('/')
        handle = (path.split('/') or [''])[0]
        if handle and handle.lower() not in ('i', 'intent', 'share', 'search'):
            return '@' + handle
    except Exception:
        pass
    return ''


def normalize_handle(handle):
    return ('@' + str(handle).strip().lstrip('@')).lower() if handle else ''


def load_smart_x_accounts():
    if not SMART_X_ACCOUNTS.exists():
        return set()
    out = set()
    for line in SMART_X_ACCOUNTS.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        out.add(normalize_handle(line.split()[0]))
    return out


def find_handles(value):
    found = set()
    def add_url_or_handle(s):
        if not s:
            return
        s = str(s).strip()
        if 'twitter.com/' in s or 'x.com/' in s:
            h = twitter_handle(s)
            if h:
                found.add(normalize_handle(h))
        elif s.startswith('@') and 2 <= len(s) <= 30:
            found.add(normalize_handle(s))
    def walk(v):
        if isinstance(v, str):
            add_url_or_handle(v)
        elif isinstance(v, dict):
            for item in v.values():
                walk(item)
        elif isinstance(v, list):
            for item in v:
                walk(item)
    walk(value)
    return found


def smart_x_overlap(row, info):
    smart = load_smart_x_accounts()
    if not smart:
        return []
    handles = find_handles([row, info or {}])
    return sorted(handles & smart)


def social_stats_for(row, info):
    """Best-effort social/smart-follower counters from GMGN/Dex metadata.

    DexScreener usually only exposes social URLs. GMGN-like enriched payloads may
    include twitter/smart follower counters under many possible key names; extract
    them if present without requiring an external paid X API call on every scan.
    """
    smart_keys = {
        'smartfollowers', 'smartfollower', 'smartfollowercount', 'smartfollowerscount',
        'smart_followers', 'smart_follower', 'smart_follower_count', 'smart_followers_count',
        'twitter_smart_followers', 'twitter_smart_follower_count', 'kol_followers',
        'smart_kol_followers', 'smart_followers_num'
    }
    follower_keys = {
        'followers', 'follower', 'followercount', 'followerscount',
        'follower_count', 'followers_count', 'twitter_followers',
        'twitter_follower_count', 'x_followers', 'x_follower_count'
    }
    out = {'smart': 0.0, 'followers': 0.0}

    def norm(k):
        return ''.join(ch for ch in str(k).lower() if ch.isalnum() or ch == '_')

    def walk(v):
        if isinstance(v, dict):
            for k, val in v.items():
                nk = norm(k)
                if isinstance(val, (int, float, str)):
                    num = fnum(val)
                    if num > 0 and nk in smart_keys:
                        out['smart'] = max(out['smart'], num)
                    elif num > 0 and nk in follower_keys:
                        out['followers'] = max(out['followers'], num)
                walk(val)
        elif isinstance(v, list):
            for item in v:
                walk(item)

    walk(row)
    walk(info or {})
    return out


def social_desc(row, info):
    links = links_for(row, info, token_addr(row) or token_addr(info))
    if links.get('twitter'):
        stats = social_stats_for(row, info)
        handle = twitter_handle(links.get('twitter'))
        overlap = smart_x_overlap(row, info)
        parts = []
        if handle:
            parts.append(handle)
        if overlap:
            shown = ', '.join(overlap[:3])
            more = f' +{len(overlap)-3}' if len(overlap) > 3 else ''
            parts.append(f'Base/KOL watchlist match {shown}{more}')
        if stats.get('smart'):
            parts.append(f"smart followers {compact_int(stats['smart'])}")
        if stats.get('followers'):
            parts.append(f"followers {compact_int(stats['followers'])}")
        suffix = ' · '.join(parts)
        if suffix:
            return f'🐦 X {suffix} — cek kualitas follower/narasi.'
        return '🐦 X tersedia — cek narasi/komunitas sebelum entry.'
    if links.get('website') or links.get('telegram'):
        return '🌐 Ada social/web selain X — cek komunitas sebelum entry.'
    return '🐦 X/social belum jelas — jangan naikkan confidence dari narasi.'


def bullet(lines, max_items=None):
    items = [x for x in lines if x]
    if max_items:
        items = items[:max_items]
    return '\n'.join(f'• {x}' for x in items)


# ── Red-metric detection helper ──────────────────────────────────────────────
# Normalise a percentage-like string/number from GMGN (e.g. '85' or 0.85) to
# 0-100 float. Maximum 100.
def _pct(val):
    if val is None:
        return 0.0
    try:
        n = float(val)
    except Exception:
        return 0.0
    if n <= 1.0:
        return min(100.0, n * 100)
    return min(100.0, n)

# Red-metric thresholds — result is capped once ANY of these is breached.
_RED_THRESHOLDS = {
    'top_10_holder_rate':      80.0,   # >80% holders concentrated in top-10
    'owner_holder_rate':       80.0,   # >80% team/creator holdings
    'private_vault_hold_rate': 55.0,   # >55% vested in private vaults
    'dev_team_hold_rate':      80.0,   # >80% dev/team holding
    'top_bot_degen_percentage':60.0,   # >60% bot/degen buyers — wash/flash
    'bot_degen_rate':          50.0,   # >50% bot-degen transactions
    'fresh_wallet_rate':       80.0,   # >80% fresh one-trick wallets
    'top_snipers_rate':        80.0,   # >80% top-70 wallets are snipers
    'rat_trader_percentage':   50.0,   # >50% rat-traders (quick exit)
    'entrapment_percentage':   50.0,   # >50% entrapment traders
}

# Soft penalty range for every red metric hit (additive to the cap reduction).
_RED_PENALTY_PER_HIT  = (-3.0, -8.0)   # min, max per hit
_RED_PENALTY_MAX      = -30.0           # max cumulative soft penalty

def _minor_penalty(red_count):
    """Return cumulative soft penalty between min and max_pen given hit count."""
    if red_count <= 0:
        return 0.0
    lo, hi = _RED_PENALTY_PER_HIT
    # linear interpolation scaled to clip at max
    total = lo * red_count
    return max(_RED_PENALTY_MAX, min(lo * red_count, hi * red_count))


def _confidence_tier_cap(red_metrics):
    """Return (effective_raw_max, applied_tier_label) after red-metric grading."""

    worst = max((v for v in red_metrics.values()), default=0.0)

    if worst >= 90:           # catastrophic
        tier = 'EB'; cap = 32; tier_label = 'Extremely Bearish'
    elif worst >= 80:         # critical
        tier = 'SB'; cap = 41; tier_label = 'Strong Bearish'
    elif worst >= 60:         # poor
        tier = 'MB'; cap = 55; tier_label = 'Moderately Bearish'
    elif worst >= 40:         # elevated risk
        tier = 'NT'; cap = 68; tier_label = 'Neutral-Tilted Bearish'
    else:                     # clean green
        tier = 'N';  cap = 95; tier_label = 'Neutral'

    # Soft penalty: cumulate over all hits and floor at _RED_PENALTY_MAX
    minor = _minor_penalty(len(red_metrics))
    cap = max(10, cap + minor)  # never go below 10
    return cap, tier_label


def _extract_red_metrics(info):
    """Scan GMGN token info for red-flag concentration / bot ratios.
    Returns {field_name: percent_0_100} for every breached threshold."""
    dev   = (info or {}).get('dev')   or {}
    stat  = (info or {}).get('stat')  or {}
    out   = {}

    # direct dev / stat percentage fields: parse '0' → 0, '85' → 85, 0.85 → 85
    for key, threshold in _RED_THRESHOLDS.items():
        raw = stat.get(key, dev.get(key))
        if raw in (None, '', '0', 0, 'false', 'False'):
            continue
        val = _pct(raw)
        if val >= threshold:
            out[key] = val

    # hard block: top_bot_degen_count absurdly high
    bot_cnt = stat.get('top_bot_degen_count', 0)
    if isinstance(bot_cnt, (int, float)) and bot_cnt >= 150:
        out['top_bot_degen_count'] = float(bot_cnt)

    return out


# ── Expensive-domain hard blocks ─────────────────────────────────────────────
_EXPENSIVE_DOMAINS = (
    'twitter.com','x.com','tiktok.com','youtube.com','youtu.be',
    'instagram.com','linkedin.com','facebook.com','reddit.com',
    'discord.com','github.com','medium.com','farcaster.xyz',
)
def _find_hard_blocks(obj, path=''):
    """Return [reason] for hard-block signals: no social media presence = red."""
    findings = []
    # if top-level dict has no social keys at all and it's a token—same as below but
    # FAST: any _raw responses that are just {'error': ...} with no token ID
    obj = obj or {}
    if isinstance(obj, dict):
        found_any = False
        for key, val in obj.items():
            kl = str(key).lower()
            if kl in ('error','errors','message') and isinstance(val, str):
                findings.append(f'API error: {val[:120]}')
            elif any(kl.startswith(d) for d in _EXPENSIVE_DOMAINS):
                def walk_social(v, P=path + '.' + key):
                    if isinstance(v, dict):
                        for k,vv in v.items():
                            walk_social(vv, P+'.'+k)
                    elif isinstance(v, list):
                        for item in v:
                            walk_social(item, P)
                    elif isinstance(v, str) and v.strip() and 'token' in kl.lower() and v.strip() != 'base':
                        findings.append(f'Social/SMS expense: {P}')
                walk_social(val)
        # if stat / dev is missing that's not a block on its own
    return findings


def build_description(row, info, hits):
    mcap = market_cap(row, info)
    liq = liquidity_usd(row, info)
    vol = volume_usd(row, info)
    swaps = swaps_count(row, info)
    chg = price_change(row, info)
    age = age_hours(row, info)
    source = row.get('_source', 'gmgn')

    signal = []
    risk = []
    score = 0
    _CAP_VAR = None   # set by red-metric tier cap just below

    if source.startswith('dex'):
        source_labels = {
            'dex_profile': '🆕 DexScreener latest profile',
            'dex_recent_profile': '♻️ DexScreener recent profile update',
            'dex_cto': '🤝 DexScreener community takeover / CTO',
            'dex_ad': '📣 DexScreener ad/paid visibility',
            'dex_boost': '⚡ DexScreener latest boost',
            'dex_boost_top': '🏆 DexScreener top boost',
        }
        signal.append(f"{source_labels.get(source, '🆕 DexScreener discovery')}")
        score += 8
    else:
        signal.append('📡 Sumber GMGN trending/Base feed.')

    if age:
        if age <= 24:
            signal.append(f'🆕 Token/pair baru: umur ≈ {age:.1f} jam')
            score += 10
        else:
            signal.append(f'🕒 Umur pair/token ≈ {age:.1f} jam')

    if hits:
        buy_hits = [h for h in hits if h['side'] == 'buy']
        whale = [h for h in hits if h['tier'] == 'whale_200k']
        strict = [h for h in hits if h['tier'] == 'strict_smart']
        broad = [h for h in hits if h['tier'] == 'broad_smart']
        if whale:
            signal.append(f"🐳 Whale >$200k ikut {'buy/accumulate' if any(h['side']=='buy' for h in whale) else 'sell/monitor'} ({len(whale)} wallet)"); score += 18
        if strict:
            signal.append(f'💰 Smart Money/KOL high quality overlap {len(strict)} wallet'); score += 15
        if broad:
            signal.append(f'💵 Broad smart-wallet overlap {len(broad)} wallet'); score += 7
        if buy_hits:
            signal.append(f"💸 Akumulasi smart/KOL: {len(buy_hits)} buy recent, total ≈ ${sum(h['amount_usd'] for h in buy_hits):,.0f}"); score += 12
        sell_hits = [h for h in hits if h['side'] == 'sell']
        if sell_hits:
            signal.append(f'🔻 Distribusi/sell smart/KOL {len(sell_hits)} event; jangan FOMO'); score -= 8
    else:
        pass

    if vol:
        signal.append(f'🚀 Volume surge/aktivitas volume ≈ ${vol:,.0f}')
        score += 10 if vol >= 50_000 else 6 if vol >= 10_000 else 2
    if swaps:
        signal.append(f'🔁 Swaps/tx aktif ≈ {swaps:,.0f}')
        score += 8 if swaps >= 250 else 5 if swaps >= 100 else 2
    if chg:
        signal.append(f"📈 Pump/momentum ≈ +{chg:.1f}%" if chg > 0 else f"📉 Momentum ≈ {chg:.1f}%")
        score += 18 if chg >= 100 else 10 if chg >= 50 else 5 if chg > 0 else -6
    signal.append(social_desc(row, info))

    text = json.dumps([row, info], default=str).lower()[:5000]
    if 'honeypot' in text and 'false' not in text:
        risk.append('🧨 honeypot flag perlu cek manual')
    if 'wash' in text and 'false' not in text:
        risk.append('🫧 indikasi wash volume')
    if mcap and mcap < 20_000:
        risk.append('🎲 microcap sangat volatil')
    if mcap and mcap > 2_000_000:
        risk.append('🏔️ mcap sudah besar untuk entry awal')
    if liq and liq < 5_000:
        risk.append(f'🚨 liquidity sangat rendah (${liq:,.0f}) — rawan slippage/rug')
        score -= 28
    elif liq and liq < 10_000:
        risk.append(f'⚠️ liquidity rendah (${liq:,.0f}) — entry kecil/skip jika spread jelek')
        score -= 14
    elif liq:
        signal.append(f'💧 Liquidity ≈ ${liq:,.0f}')
        score += 6 if liq >= 25_000 else 3
    if source.startswith('dex'):
        risk.append('⚠️ DexScreener hanya sinyal tambahan; tetap validasi GMGN/liquidity/holder.')
    if age and age <= 6:
        risk.append('🧪 token sangat baru: risiko rug/honeypot/liquidity pull lebih tinggi')

    # ── Red-metric hard cap before final clamp ─────────────────────────────────
    # Any red metric >= threshold collapses raw score into a tiered cap.
    # This prevents 100/100 unless ALL metrics are clean.
    red   = _extract_red_metrics(info)          # {key: pct_0_100}
    cap, tier_label = _confidence_tier_cap(red)

    # append risk blurb for every breached red metric
    _SHORT = {
        'top_10_holder_rate':         'top-10 holder konsentrasi tinggi',
        'owner_holder_rate':          'team/creator holding tinggi',
        'private_vault_hold_rate':    'private vault lock tinggi',
        'dev_team_hold_rate':         'dev/team holding tinggi',
        'top_bot_degen_percentage':   'bot/degen buyer ratio tinggi',
        'bot_degen_rate':             'transaksi bot/degen tinggi',
        'fresh_wallet_rate':          'fresh wallet ratio tinggi',
        'top_snipers_rate':           'sniper top-70 ratio tinggi',
        'rat_trader_percentage':      'rat-trader ratio tinggi',
        'entrapment_percentage':      'entrapment trader ratio tinggi',
        'top_bot_degen_count':        'bot/degen count kritis',
    }
    reasons = []
    for key, pct in sorted(red.items()):
        lbl = _SHORT.get(key, key)
        reasons.append(f'🔴 {lbl} ({pct:.0f}%)')
    risk.extend(reasons)

    # reflect tier in risk section
    risk.insert(0, f'📊 Confidence tier: {tier_label} — bisa berubah setelah cek dev/holder/social langsung.')

    # apply tier-cap to score (0 = base / neutral start from 50)
    _CAP_VAR = cap
    final_conf = max(0, min(100, 50 + score))
    if _CAP_VAR is not None:
        final_conf = min(final_conf, _CAP_VAR)
    return final_conf, bullet(signal, max_items=7), bullet(risk, max_items=4) or '• ⚠️ Volatilitas meme/Base — cek liquidity & holder sebelum entry.'



def tg_send(env, text, reply_to_message_id=None):
    token = env.get('TELEGRAM_BOT_TOKEN'); chat = env.get('TELEGRAM_CHAT_ID')
    if not token or not chat:
        return False
    payload = {
        'chat_id': chat,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': 'true',
    }
    if reply_to_message_id:
        payload['reply_to_message_id'] = str(reply_to_message_id)
        payload['allow_sending_without_reply'] = 'true'
    data = urllib.parse.urlencode(payload).encode()
    last_err = None
    for attempt in range(3):
        req = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage', data=data)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json.loads(r.read().decode('utf-8', 'replace') or '{}')
            if body.get('ok'):
                return ((body.get('result') or {}).get('message_id')) or True
            last_err = body
        except Exception as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    print(f'telegram failed after retries: {last_err}')
    return False


def milestone_label(mult):
    if abs(mult - 1.5) < 0.001:
        return '+50%'
    if float(mult).is_integer():
        return f'{int(mult)}x'
    return f'{mult:g}x'


def pending_milestones(entry_price, current_price, sent):
    if not entry_price or not current_price or current_price <= entry_price:
        return [], 0.0
    ratio = current_price / entry_price
    thresholds = [1.5] + [float(x) for x in range(2, min(101, int(ratio) + 1))]
    sent = set(str(x) for x in (sent or []))
    due = [m for m in thresholds if ratio >= m and str(m) not in sent]
    return due, ratio


def build_followup_message(state, row, info, milestone, ratio):
    sym = symbol(info or {}) if symbol(info or {}) != 'UNKNOWN' else state.get('symbol') or symbol(row)
    entry = fnum(state.get('entry_price'))
    cur = price_usd(row, info)
    mcap = market_cap(row, info)
    chg = ((cur / entry - 1) * 100) if entry and cur else ((ratio - 1) * 100)
    return (
        f"Update for {html.escape(sym)} 🦉\n"
        f"Milestone: {html.escape(milestone_label(milestone))}\n"
        f"Market Cap: {'$' + format(mcap, ',.2f') if mcap else 'n/a'}\n"
        f"Increase: {chg:.2f}%"
    )


def dexscreener_candidates(limit=80):
    """Return Base candidates from DexScreener discovery endpoints.

    Added from DexScreener API reference:
    - /token-profiles/latest/v1
    - /token-profiles/recent-updates/v1
    - /community-takeovers/latest/v1
    - /ads/latest/v1
    - /token-boosts/latest/v1
    - /token-boosts/top/v1

    Enrichment later uses:
    - /tokens/v1/{chainId}/{tokenAddresses}
    - /token-pairs/v1/{chainId}/{tokenAddress} as fallback
    """
    out = []
    seen = set()
    endpoints = [
        ('dex_profile', 'https://api.dexscreener.com/token-profiles/latest/v1'),
        ('dex_recent_profile', 'https://api.dexscreener.com/token-profiles/recent-updates/v1'),
        ('dex_cto', 'https://api.dexscreener.com/community-takeovers/latest/v1'),
        ('dex_ad', 'https://api.dexscreener.com/ads/latest/v1'),
        ('dex_boost', 'https://api.dexscreener.com/token-boosts/latest/v1'),
        ('dex_boost_top', 'https://api.dexscreener.com/token-boosts/top/v1'),
    ]
    for source, url in endpoints:
        try:
            data = fetch_json(url, timeout=20)
        except Exception as e:
            print(f'dex endpoint failed {source}: {e}')
            continue
        for r in rows_from(data):
            if str(r.get('chainId', '')).lower() != 'base':
                continue
            addr = r.get('tokenAddress') or token_addr(r)
            if not addr:
                continue
            key = addr.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append({'address': addr, 'symbol': r.get('symbol') or 'NEW', '_source': source, **r})
            if len(out) >= limit:
                return out
    return out


def dex_enrich(addr):
    """Enrich token with DexScreener pair metrics.

    Prefer the documented /tokens/v1/base/{tokenAddresses}; fallback to
    /token-pairs/v1/base/{tokenAddress}; final fallback to the older
    /latest/dex/tokens/{addr} route if available.
    """
    endpoints = [
        f'https://api.dexscreener.com/tokens/v1/base/{urllib.parse.quote(addr)}',
        f'https://api.dexscreener.com/token-pairs/v1/base/{urllib.parse.quote(addr)}',
        f'https://api.dexscreener.com/latest/dex/tokens/{urllib.parse.quote(addr)}',
    ]
    for url in endpoints:
        try:
            data = fetch_json(url, timeout=20)
        except Exception:
            continue
        pairs = data.get('pairs') if isinstance(data, dict) else data
        if not isinstance(pairs, list):
            continue
        base_pairs = [p for p in pairs if str(p.get('chainId', '')).lower() == 'base']
        if not base_pairs:
            continue
        base_pairs.sort(key=lambda p: fnum((p.get('liquidity') or {}).get('usd')), reverse=True)
        p = base_pairs[0]
        p['_source'] = 'dex_enriched'
        return p
    return {}


def candidate_key(addr):
    return (addr or '').lower()


def should_alert(row, info, hits, conf, env):
    threshold = fnum(env.get('ALERT_THRESHOLD'), 62)
    chg = price_change(row, info)
    vol = volume_usd(row, info)
    swaps = swaps_count(row, info)
    age = age_hours(row, info)
    mcap = market_cap(row, info)
    liq = liquidity_usd(row, info)
    has_quality_wallet = any(h['tier'] in ('whale_200k', 'strict_smart') for h in hits)
    buy_hits = [h for h in hits if h.get('side') == 'buy']
    source = row.get('_source', 'gmgn')

    min_mcap = fnum(env.get('MC_MIN_USD'), 3000)
    max_mcap = fnum(env.get('MC_MAX_USD'), 5_000_000)
    min_liq = fnum(env.get('MIN_LIQUIDITY_USD'), 10_000)
    min_liq_quality = fnum(env.get('MIN_QUALITY_WALLET_LIQUIDITY_USD'), 6_000)
    mcap_ok = (not mcap) or (min_mcap <= mcap <= max_mcap)
    liq_ok = liq >= min_liq
    quality_liq_ok = liq >= min_liq_quality
    new_token = (age == 0 or age <= fnum(env.get('NEW_TOKEN_MAX_AGE_HOURS'), 72))
    pump_big = chg >= fnum(env.get('NEW_TOKEN_MIN_PUMP_PERCENT'), 50)
    pump_runner = chg >= fnum(env.get('RUNNER_MIN_PUMP_PERCENT'), 100)
    activity_ok = vol >= fnum(env.get('NEW_TOKEN_MIN_VOLUME_USD'), 1000) or swaps >= fnum(env.get('NEW_TOKEN_MIN_SWAPS'), 20)
    strong_activity = vol >= fnum(env.get('RUNNER_MIN_VOLUME_USD'), 5000) or swaps >= fnum(env.get('RUNNER_MIN_SWAPS'), 50)

    if not mcap_ok:
        return False
    # Never alert tokens with extremely weak/missing liquidity; confidence from
    # pump/social alone is dangerous on Base because slippage and rug risk dominate.
    if not liq or liq < min_liq_quality:
        return False

    # Main path requested by user: fresh/fast Base runners, including 100%+ and
    # 2000%+ moves, with at least minimal real activity. Age is descriptive but
    # not a hard block for already-running tokens.
    if pump_runner and activity_ok and liq_ok:
        return True
    if new_token and pump_big and activity_ok and liq_ok:
        return True

    # Smart/KOL wallet buy overlap can pass with lower pump if activity exists,
    # but still needs a minimum liquidity floor.
    if buy_hits and chg >= 20 and activity_ok and quality_liq_ok:
        return True
    if has_quality_wallet and chg >= 10 and activity_ok and quality_liq_ok:
        return True

    # DexScreener discovery with boosts/CTO/ads/profiles can pass on strong
    # confidence, but only with positive momentum, activity, and liquidity.
    if source.startswith('dex') and conf >= threshold and chg >= 25 and strong_activity and liq_ok:
        return True

    # GMGN confidence path must still show positive momentum, activity, and liquidity.
    if not source.startswith('dex') and conf >= threshold and chg >= 25 and activity_ok and liq_ok:
        return True
    return False


def scweet_social_line(row, info):
    env = load_env()
    if str(env.get('SCWEET_ENABLED', '')).lower() not in ('1', 'true', 'yes', 'on'):
        return ''
    links = links_for(row, info, token_addr(row) or token_addr(info))
    handle = twitter_handle(links.get('twitter'))
    if not handle:
        return ''
    # Skip known company/project handles that are not individual token accounts.
    _skip = {'base','dex','dexscreener','gmgn','aerodrome','zora','l2beat','blockscout','etherscan','coinbase','coinbaseduck','opensea','uniswap','sushiswap','pancakeswap'}
    if handle.lower() in _skip:
        return ''
    worker = ROOT / 'social_scweet_worker.py'
    py = ROOT / '.venv-scweet' / 'bin' / 'python'
    if not worker.exists() or not py.exists():
        return ''
    child_env = os.environ.copy()
    for k in ('SCWEET_COOKIES_FILE', 'SCWEET_DB_PATH', 'SCWEET_CACHE_HOURS', 'SCWEET_FOLLOWER_LIMIT'):
        if env.get(k):
            child_env[k] = env.get(k)
    child_env['SMART_X_ACCOUNTS_FILE'] = str(SMART_X_ACCOUNTS)
    timeout = max(5, int(fnum(env.get('SCWEET_TIMEOUT_SECONDS'), 45)))
    try:
        cp = subprocess.run([str(py), str(worker), handle], env=child_env, text=True, capture_output=True, timeout=timeout)
        if cp.returncode != 0 or not cp.stdout.strip():
            return '🐦 Social graph: Scweet belum tersedia / gagal lookup.'
        data = json.loads(cp.stdout.strip().splitlines()[-1])
    except Exception:
        return '🐦 Social graph: lookup timeout/gagal.'
    if not data.get('ok'):
        return '🐦 Social graph: Scweet gagal validasi akun/cookie.'
    parts = []
    if 'followers_count' in data and data.get('followers_count') is not None:
        parts.append(f"followers {compact_int(data['followers_count'])}")
    if 'base_kol_count' in data and data.get('base_kol_count') is not None:
        notable = ', '.join((data.get('base_kol_followers') or [])[:3])
        more = f" +{data['base_kol_count']-3}" if data['base_kol_count'] > 3 else ''
        parts.append(f"Base/KOL followers {data['base_kol_count']}/{len(load_smart_x_accounts())}: {notable}{more}")
    elif 'sampled_followers' in data and data.get('sampled_followers') is not None:
        parts.append(f"Base/KOL followers 0/{len(load_smart_x_accounts())} sampled {compact_int(data['sampled_followers'])}")
    if not parts:
        return ''
    cache_note = ' cached' if data.get('cache_hit') else ''
    return '🐦 Social graph' + cache_note + ': ' + ' · '.join(parts)


def build_message(row, info, conf, desc, risk, holder_analysis=None):
    addr = token_addr(row) or token_addr(info)
    sym = symbol(info or {}) if symbol(info or {}) != 'UNKNOWN' else symbol(row)
    mcap = market_cap(row, info)
    liq = liquidity_usd(row, info)
    age = age_hours(row, info)
    chg = price_change(row, info)
    vol = volume_usd(row, info)
    swaps = swaps_count(row, info)
    links = links_for(row, info, addr)
    launchpad = launchpad_for(row, info)
    source = row.get('_source', 'gmgn')

    tags = []
    if age and age <= 24:
        tags.append('🆕 NEW')
    if chg >= 100:
        tags.append('🚀 100%+ PUMP')
    if source.startswith('dex'):
        tags.append('📊 DEX')
    tag_text = (' · ' + ' · '.join(tags)) if tags else ''

    link_items = [
        ('GMGN', links['gmgn']),
        ('DexScreener', links['dex']),
        ('X', links.get('twitter')),
        ('Web', links.get('website')),
        ('TG', links.get('telegram')),
    ]
    link_block = ' | '.join(
        f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>'
        for label, url in link_items if url
    )

    smart_line = 'Belum ada smart/KOL overlap.'
    if 'Akumulasi smart/KOL' in desc or 'Smart Money' in desc or 'Whale' in desc:
        smart_line = 'Ada smart/KOL activity — lihat detail di bawah.'

    safe_sym = html.escape(sym)
    safe_addr = html.escape(addr)
    safe_launchpad = html.escape(launchpad)
    _rate_limit()
    # scweet_social_line removed: requires valid X auth_token cookie (no longer available).
    # Smart-follower overlap uses GMGN metadata + local watchlist only (smart_x_overlap).
    social_line = ''
    desc_block = desc + (f"\n• {social_line}" if social_line else '')

    # Separate MCAP vs FDV
    fdv_val = fdv(row, info)
    mcap_fdv_line = (f"• MCap: {'$' + format(mcap, ',.0f') if mcap else 'n/a'}\n"
                     f"• FDV:   {'$' + format(fdv_val, ',.0f') if fdv_val else 'n/a'}")

    # Holder quality line from analysis
    holder_line = ''
    holder_section = ''
    if holder_analysis:
        ha = holder_analysis
        if ha.get('is_danger'):
            holder_line = f"DANGER: {ha['fresh_count']}/{HOLDER_FETCH_LIMIT} top holders fresh wallet ({ha['fresh_pct']*100:.0f}%) | avg {ha['avg_eth']:.4f} ETH"
        elif ha.get('has_low_eth'):
            holder_line = f"Holder quality rendah: avg {ha['avg_eth']:.4f} ETH | fresh {ha['fresh_count']}/{HOLDER_FETCH_LIMIT}"
        if holder_line:
            holder_section = f"👥 HOLDER QUALITY\n• {holder_line}\n\n"

    return (f"🚨 BASE TOKEN ALERT{tag_text}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 {safe_sym}\n"
            f"🎯 Confidence: {conf}/100\n"
            f"📌 CONTRACT\n"
            f"<code>{safe_addr}</code>\n\n"
            f"📊 MARKET\n"
            f"{mcap_fdv_line}\n"
            f"• Liquidity: {'$' + format(liq, ',.0f') if liq else 'n/a'}\n"
            f"• Age: {age:.1f} jam\n"
            f"• Pump max: {chg:+.1f}%\n"
            f"• Volume: ${vol:,.0f}\n"
            f"• Swaps: {swaps:,.0f}\n\n"
            f"🏭 LAUNCHPAD\n"
            f"• {safe_launchpad}\n\n"
            f"💰 SMART / KOL\n"
            f"• {smart_line}\n\n"
            f"📍 SIGNAL\n"
            f"{desc_block}\n\n"
            f"⚠️ RISK\n"
            f"{risk}\n\n"
            f"{holder_section}"
            f"🔗 LINKS\n"
            f"{link_block}\n\n"
            f"🧠 ACTION\n"
            f"• Watchlist/manual research dulu.\n"
            f"• Alert-only: tidak ada auto-buy.")


# Simple rate limiter: max 8 API calls per 10 seconds
_api_timestamps = []
def _rate_limit():
    now = time.time(); before = []
    for t in _api_timestamps:
        if now - t < 10: before.append(t)
    _api_timestamps.clear(); _api_timestamps.extend(before)
    if len(_api_timestamps) >= 8:
        oldest = _api_timestamps[0]; wait = 10 - (now - oldest) + 0.1
        if wait > 0: time.sleep(max(wait, 0.1))
    _api_timestamps.append(time.time())

def main():
    env = load_env(); wallets = load_wallets(); feeds = recent_smart_kol(env)
    seen = set(load_json_file(SEEN, []))
    alert_state = load_json_file(ALERT_STATE, {})

    candidates = []
    try:
        candidates.extend(rows_from(run_gmgn(['market', 'trending', '--chain', CHAIN, '--interval', '1h', '--limit', '40', '--order-by', 'volume', '--direction', 'desc', '--raw'], env)))
    except Exception as e:
        print(f'gmgn trending failed: {e}')

    # Add DexScreener discovery APIs to catch fresh 100%+ runners.
    candidates.extend(dexscreener_candidates(limit=60))

    sent = 0; followups = 0; new_seen = set(seen); processed = set()
    max_followups = int(fnum(env.get('MAX_FOLLOWUPS_PER_RUN'), 10))
    for row in candidates[:100]:
        addr = token_addr(row)
        key = candidate_key(addr)
        if not addr or key in processed:
            continue
        processed.add(key)

        info = {}
        if str(row.get('_source', '')).startswith('dex'):
            _rate_limit()
            info = dex_enrich(addr)
        if not info:
            try:
                _rate_limit()
                info = run_gmgn(['token', 'info', '--chain', CHAIN, '--address', addr, '--raw'], env, timeout=50)
            except Exception:
                info = {}

        merged_row = {**row, '_source': row.get('_source', 'gmgn')}

        # Follow-up mode: if this token already has an initial Telegram alert
        # with message_id + entry price, reply to that original alert on milestones
        # (+50%, 2x, 3x, ...). This keeps the chat threaded like Seekr Pro.
        st = alert_state.get(key)
        if st and st.get('message_id') and st.get('entry_price'):
            cur_price = price_usd(merged_row, info)
            due, ratio = pending_milestones(fnum(st.get('entry_price')), cur_price, st.get('milestones_sent'))
            for milestone in due:
                if followups >= max_followups:
                    break
                msg = build_followup_message(st, merged_row, info, milestone, ratio)
                print(msg + '\n---')
                _rate_limit()
                sent_msg_id = tg_send(env, msg, reply_to_message_id=st.get('message_id'))
                if not sent_msg_id:
                    break
                st.setdefault('milestones_sent', []).append(str(milestone))
                st['last_price'] = cur_price
                st['last_ratio'] = ratio
                st['last_followup_at'] = int(time.time())
                alert_state[key] = st
                followups += 1
            # Existing alerted tokens should not create a second root alert.
            continue

        if key in seen:
            continue

        hits = wallet_hits_for_token(addr, feeds, wallets)

        # ── Holder analysis: fetch top-50 holders & analyze for fresh-wallets / low-ETH ──
        holder_analysis = {'has_low_eth': False, 'is_danger': False, 'avg_eth': 0.0,
                           'fresh_count': 0, 'fresh_pct': 0.0, 'signals': []}
        try:
            _rate_limit()
            holders = fetch_top_holders(addr, env, limit=HOLDER_FETCH_LIMIT)
            holder_analysis = analyze_holders(holders)
            if holder_analysis['signals']:
                for sig in holder_analysis['signals']:
                    print(sig)
        except Exception as e:
            print(f'holder fetch skipped for {addr[:16]}...: {e}')

        conf, desc, risk = build_description(merged_row, info, hits)

        # ── Holder-based confidence adjustments ──────────────────────────────────
        # Override confidence if holder analysis reveals puppet/flash-run patterns
        h = holder_analysis
        if h['is_danger']:
            conf = min(conf, 32)
            risk_items = [f'🚨 DANGER: {h["fresh_count"]}/{HOLDER_FETCH_LIMIT} top holders ({h["fresh_pct"]*100:.0f}%) fresh wallet — bot/flash-degen manipulation!',
                         f'   Avg top-holder ETH: {h["avg_eth"]:.4f} — tidak credible, kemungkinan puppet token.',
                         risk]
            risk = '\n'.join(risk_items)
        elif h['has_low_eth']:
            conf = min(conf, 55)
            desc = f'{desc}\n• Holder quality rendah: avg {h["avg_eth"]:.4f} ETH — kurang credible'
            risk_items = [f'⚠️ Rata-rata top-{HOLDER_FETCH_LIMIT} holder hanya {h["avg_eth"]:.4f} ETH — likely flash-run/puppet.', risk]
            risk = '\n'.join(risk_items)

        if not should_alert(merged_row, info, hits, conf, env):
            continue

        msg = build_message(merged_row, info, conf, desc, risk, holder_analysis)
        print(msg + '\n---')
        sent_msg_id = False
        try:
            _rate_limit()
            sent_msg_id = tg_send(env, msg)
        except Exception as e:
            print(f'telegram failed: {e}')
        if not sent_msg_id:
            continue

        # IMMEDIATELY mark as seen to prevent double-alert within same scan cycle
        new_seen.add(key)

        px = price_usd(merged_row, info)
        alert_state[key] = {
            'addr': addr,
            'symbol': symbol(info or {}) if symbol(info or {}) != 'UNKNOWN' else symbol(merged_row),
            'message_id': sent_msg_id if type(sent_msg_id) is int else None,
            'entry_price': px,
            'entry_mcap': market_cap(merged_row, info),
            'entry_liquidity': liquidity_usd(merged_row, info),
            'alerted_at': int(time.time()),
            'milestones_sent': [],
        }
        sent += 1
        if sent >= 5:
            break

    save_json_file(SEEN, sorted(new_seen))
    save_json_file(ALERT_STATE, alert_state)
    print(json.dumps({'sent': sent, 'followups': followups, 'tracked_alerts': len(alert_state), 'wallets': {k: len(v) for k, v in wallets.items()}, 'feeds': len(feeds), 'candidates': len(candidates)}))


if __name__ == '__main__':
    raise SystemExit(main() or 0)
