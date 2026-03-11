from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# Core backend for the currently active runtime:
# - Serves static UI from web/
# - Proxies Yahoo chart/price endpoints
# - Manages daily fundamentals refresh pipeline

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

SYMBOL_CACHE_FILE = os.path.join(BASE_DIR, "symbols_cache.json")
SESSION_OVERRIDES_FILE = os.path.join(BASE_DIR, "session_overrides.json")
FUNDAMENTALS_FILE = os.path.join(BASE_DIR, "investing_fundamentals.json")
SYMBOL_CACHE_TTL = 24 * 60 * 60
PRICE_CACHE_TTL = 3
MARKET_PROBE_TTL = 45
MARKET_PROBE_STALE_SEC = 20 * 60
FUNDAMENTALS_REFRESH_SEC = 24 * 60 * 60
try:
    MARKET_TZ = ZoneInfo("Europe/Istanbul")
except Exception:
    MARKET_TZ = timezone(timedelta(hours=3), name="Europe/Istanbul")
MARKET_OPEN_HOUR = 10
MARKET_OPEN_MINUTE = 0
MARKET_CLOSE_HOUR = 18
MARKET_CLOSE_MINUTE = 10

FALLBACK_SYMBOLS = [
    "THYAO.IS","ASELS.IS","TUPRS.IS","SISE.IS","EREGL.IS","KCHOL.IS","AKBNK.IS","GARAN.IS","ISCTR.IS","YKBNK.IS",
    "BIMAS.IS","FROTO.IS","TOASO.IS","SASA.IS","HEKTS.IS","ENKAI.IS","PETKM.IS","SAHOL.IS","TCELL.IS","PGSUS.IS",
    "VAKBN.IS","HALKB.IS","DOHOL.IS","KOZAL.IS","KOZAA.IS","ALARK.IS","ARCLK.IS","CIMSA.IS","KRDMD.IS","GUBRF.IS",
    "MAVI.IS","CCOLA.IS","AEFES.IS","ODAS.IS","SMRTG.IS","ASTOR.IS","GESAN.IS","EUPWR.IS","MIATK.IS","KONTR.IS",
    "XU100.IS"
]

SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]+\.IS$")
TV_SYMBOL_PATTERN = re.compile(r"^BIST:([A-Z0-9]+)$")
TV_FUND_COLUMNS = [
    "name",
    "close",
    "market_cap_basic",
    "price_earnings_ttm",
    "ebitda_ttm",
    "average_volume_60d_calc",
    "Perf.Y",
    "total_revenue_ttm",
    "net_income_ttm",
    "earnings_per_share_basic_ttm",
    "return_on_equity",
    "price_book_fq",
    "currency",
]
FUNDAMENTAL_KEYS = ["pe", "ebitda", "market_cap", "avg_volume_3m", "change_1y_pct", "revenue", "net_income", "eps", "roe_pct", "pb"]

YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*",
}

state_lock = threading.Lock()
symbol_state = {
    "symbols": sorted(set(FALLBACK_SYMBOLS)),
    "last_scan_ts": 0,
    "scanning": False,
}
price_cache = {}
holiday_cache = {}
session_override_cache = {"mtime": None, "data": {}}
market_probe_cache = {"ts": 0.0, "data": None}
fundamentals_state = {
    "payload": {"source": "investing.com", "fetchedAt": None, "symbols": {}},
    "last_success_ts": 0,
    "last_attempt_ts": 0,
    "last_error": None,
    "running": False,
}
MARKET_PROBE_SYMBOLS = ["THYAO.IS", "GARAN.IS", "AKBNK.IS", "ASELS.IS", "BIMAS.IS", "ISCTR.IS"]


def fetch_json(url: str):
    req = Request(url, headers=YAHOO_HEADERS)
    with urlopen(req, timeout=12) as resp:
        return json.loads(resp.read().decode("utf-8"))


def to_num(value):
    if isinstance(value, (int, float)):
        return float(value)
    return None


def normalize_symbol(symbol: str):
    clean = (symbol or "").strip().upper()
    if clean.endswith(".IS"):
        clean = clean[:-3]
    return clean


def to_bist_symbol(symbol: str):
    clean = normalize_symbol(symbol)
    return f"{clean}.IS" if clean else ""


def parse_iso_to_ts(value):
    if not value or not isinstance(value, str):
        return 0
    try:
        fixed = value.replace("Z", "+00:00")
        return int(datetime.fromisoformat(fixed).timestamp())
    except Exception:
        return 0


def load_fundamentals_file():
    if not os.path.exists(FUNDAMENTALS_FILE):
        return
    try:
        with open(FUNDAMENTALS_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
        symbols = payload.get("symbols") if isinstance(payload, dict) else {}
        if not isinstance(symbols, dict):
            symbols = {}
        normalized = {}
        for symbol, info in symbols.items():
            clean = normalize_symbol(symbol)
            if not clean or not isinstance(info, dict):
                continue
            normalized[clean] = info

        fetched_at = payload.get("fetchedAt") if isinstance(payload, dict) else None
        ts = parse_iso_to_ts(fetched_at)
        with state_lock:
            fundamentals_state["payload"] = {
                "source": payload.get("source") if isinstance(payload, dict) else "investing.com",
                "fetchedAt": fetched_at,
                "count": len(normalized),
                "symbols": normalized,
            }
            if ts:
                fundamentals_state["last_success_ts"] = ts
    except Exception:
        pass


def _fundamentals_snapshot():
    with state_lock:
        payload = fundamentals_state["payload"]
        return {
            "source": payload.get("source"),
            "fetchedAt": payload.get("fetchedAt"),
            "count": payload.get("count") or len(payload.get("symbols") or {}),
            "symbols": dict(payload.get("symbols") or {}),
            "lastSuccessTs": fundamentals_state.get("last_success_ts") or 0,
            "lastAttemptTs": fundamentals_state.get("last_attempt_ts") or 0,
            "lastError": fundamentals_state.get("last_error"),
            "running": bool(fundamentals_state.get("running")),
        }


def _start_fundamentals_refresh(force: bool = False, symbols=None, reason: str = "scheduled"):
    with state_lock:
        if fundamentals_state["running"]:
            return False
        if not force and fundamentals_state["last_success_ts"] and (time.time() - fundamentals_state["last_success_ts"] < FUNDAMENTALS_REFRESH_SEC):
            existing_count = len((fundamentals_state.get("payload") or {}).get("symbols") or {})
            universe_count = len(symbol_state.get("symbols") or [])
            if universe_count > 0 and existing_count >= universe_count:
                return False
        fundamentals_state["running"] = True
        fundamentals_state["last_attempt_ts"] = int(time.time())
        fundamentals_state["last_error"] = None

    requested_symbols = list(symbols or [])

    def worker():
        try:
            try:
                import fundamentals_investing_fetcher as idf
            except Exception as import_exc:
                raise RuntimeError(f"investing importer error: {import_exc}")

            symbols_to_fetch = list(requested_symbols)
            if not symbols_to_fetch:
                with state_lock:
                    base_symbols = list(symbol_state["symbols"])
                symbols_to_fetch = base_symbols if base_symbols else list(FALLBACK_SYMBOLS)

            payload = idf.run(symbols_to_fetch, write_file=True)
            normalized = {}
            for symbol, info in (payload.get("symbols") or {}).items():
                clean = normalize_symbol(symbol)
                if not clean:
                    continue
                normalized[clean] = info if isinstance(info, dict) else {}

            missing_symbols = [
                s
                for s in [normalize_symbol(x) for x in symbols_to_fetch]
                if s and _needs_fundamentals_backfill(normalized.get(s))
            ]
            if missing_symbols:
                try:
                    tv_map = fetch_tv_fundamentals(missing_symbols)
                    for symbol, tv_entry in tv_map.items():
                        normalized[symbol] = _merge_fundamentals_entry(normalized.get(symbol) or {}, tv_entry)
                except Exception:
                    pass

            success_ts = parse_iso_to_ts(payload.get("fetchedAt")) or int(time.time())
            with state_lock:
                fundamentals_state["payload"] = {
                    "source": payload.get("source", "investing.com"),
                    "fetchedAt": payload.get("fetchedAt"),
                    "count": len(normalized),
                    "symbols": normalized,
                    "reason": reason,
                }
                fundamentals_state["last_success_ts"] = success_ts
                fundamentals_state["last_error"] = None
        except Exception as exc:
            with state_lock:
                fundamentals_state["last_error"] = str(exc)
        finally:
            with state_lock:
                fundamentals_state["running"] = False

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    return True


def _fundamentals_scheduler_loop():
    while True:
        try:
            _start_fundamentals_refresh(force=False, reason="daily")
        except Exception:
            pass
        time.sleep(60)


def get_public_holidays(year: int, country: str = "TR"):
    key = f"{country}-{year}"
    cached = holiday_cache.get(key)
    if cached:
        return cached

    url = f"https://date.nager.at/api/v3/PublicHolidays/{year}/{country}"
    try:
        payload = fetch_json(url)
        holidays = {}
        for item in payload if isinstance(payload, list) else []:
            day = item.get("date")
            name = item.get("localName") or item.get("name") or "Holiday"
            if isinstance(day, str):
                holidays[day] = name
        holiday_cache[key] = holidays
        return holidays
    except Exception:
        holiday_cache[key] = {}
        return {}


def _parse_hhmm(value, default_hour: int, default_minute: int):
    if not isinstance(value, str):
        return default_hour, default_minute
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", value)
    if not m:
        return default_hour, default_minute
    hour = int(m.group(1))
    minute = int(m.group(2))
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return default_hour, default_minute
    return hour, minute


def _is_half_day_holiday(name: str):
    n = (name or "").lower()
    return ("arefe" in n) or ("eve" in n)


def get_session_overrides():
    try:
        st = os.stat(SESSION_OVERRIDES_FILE)
        mtime = st.st_mtime
    except Exception:
        session_override_cache["mtime"] = None
        session_override_cache["data"] = {}
        return {}

    if session_override_cache["mtime"] == mtime and isinstance(session_override_cache["data"], dict):
        return session_override_cache["data"]

    try:
        with open(SESSION_OVERRIDES_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
        data = payload if isinstance(payload, dict) else {}
    except Exception:
        data = {}

    normalized = {}
    for key, value in data.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        if re.match(r"^\d{4}-\d{2}-\d{2}$", key):
            normalized[key] = value

    session_override_cache["mtime"] = mtime
    session_override_cache["data"] = normalized
    return normalized


def chart_to_item(symbol: str, payload: dict):
    meta = payload.get("chart", {}).get("result", [{}])[0].get("meta", {})
    if not meta:
        return None
    return {
        "symbol": meta.get("symbol") or symbol,
        "price": meta.get("regularMarketPrice"),
        "prevClose": meta.get("previousClose"),
        "marketTs": meta.get("regularMarketTime"),
        "asOfTs": int(time.time()),
        "currency": meta.get("currency"),
        "dayHigh": meta.get("regularMarketDayHigh"),
        "dayLow": meta.get("regularMarketDayLow"),
    }


def load_symbols_cache_file():
    if not os.path.exists(SYMBOL_CACHE_FILE):
        return
    try:
        with open(SYMBOL_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        symbols = data.get("symbols") or []
        ts = int(data.get("last_scan_ts") or 0)
        normalized = sorted({s.upper() for s in symbols if isinstance(s, str) and SYMBOL_PATTERN.match(s.upper())})
        if normalized:
            with state_lock:
                symbol_state["symbols"] = sorted(set(symbol_state["symbols"]) | set(normalized))
                symbol_state["last_scan_ts"] = ts
    except Exception:
        pass


def save_symbols_cache_file(symbols, last_scan_ts):
    payload = {"symbols": symbols, "last_scan_ts": int(last_scan_ts)}
    try:
        with open(SYMBOL_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception:
        pass


def build_symbol_universe():
    target = "https://scanner.tradingview.com/turkey/scan"
    body = {
        "filter": [{"left": "type", "operation": "equal", "right": "stock"}],
        "options": {"lang": "tr"},
        "markets": ["turkey"],
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": ["name"],
    }
    req = Request(
        target,
        data=json.dumps(body).encode("utf-8"),
        headers={**YAHOO_HEADERS, "Content-Type": "application/json"},
        method="POST",
    )

    discovered = set(FALLBACK_SYMBOLS)
    with urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    for row in payload.get("data", []):
        raw = row.get("s")
        if not isinstance(raw, str):
            continue
        m = TV_SYMBOL_PATTERN.match(raw.upper())
        if not m:
            continue
        discovered.add(f"{m.group(1)}.IS")

    return sorted(discovered)


def _scan_symbols_worker():
    with state_lock:
        symbol_state["scanning"] = True
    try:
        symbols = build_symbol_universe()
        ts = int(time.time())
        with state_lock:
            symbol_state["symbols"] = symbols
            symbol_state["last_scan_ts"] = ts
            symbol_state["scanning"] = False
        save_symbols_cache_file(symbols, ts)
    except Exception:
        with state_lock:
            symbol_state["scanning"] = False


def ensure_symbol_scan(force: bool = False):
    with state_lock:
        scanning = symbol_state["scanning"]
        expired = (time.time() - symbol_state["last_scan_ts"]) > SYMBOL_CACHE_TTL
    if scanning:
        return
    if force or expired:
        t = threading.Thread(target=_scan_symbols_worker, daemon=True)
        t.start()


def get_price_item(symbol: str, range_: str, interval: str):
    now = time.time()
    with state_lock:
        cached = price_cache.get((symbol, range_, interval))
    if cached and (now - cached["ts"]) < PRICE_CACHE_TTL:
        return cached["item"]

    target = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{urlencode({'range': range_, 'interval': interval})}"
    payload = fetch_json(target)
    item = chart_to_item(symbol, payload)
    if item:
        with state_lock:
            price_cache[(symbol, range_, interval)] = {"item": item, "ts": now}
    return item


def tv_ticker_from_symbol(symbol: str):
    clean = symbol.upper().strip()
    if clean.endswith(".IS"):
        clean = clean[:-3]
    return f"BIST:{clean}"


def symbol_from_tv_ticker(ticker: str):
    m = TV_SYMBOL_PATTERN.match((ticker or "").upper())
    if not m:
        return None
    return f"{m.group(1)}.IS"


def _tv_to_fund_metrics(mapped: dict):
    close_price = to_num(mapped.get("close"))
    eps_value = to_num(mapped.get("earnings_per_share_basic_ttm"))
    pe = to_num(mapped.get("price_earnings_ttm"))
    if pe is None and close_price is not None and eps_value not in (None, 0):
        pe = close_price / eps_value

    return {
        "pe": pe,
        "ebitda": to_num(mapped.get("ebitda_ttm")),
        "market_cap": to_num(mapped.get("market_cap_basic")),
        "avg_volume_3m": to_num(mapped.get("average_volume_60d_calc")),
        "change_1y_pct": to_num(mapped.get("Perf.Y")),
        "revenue": to_num(mapped.get("total_revenue_ttm")),
        "net_income": to_num(mapped.get("net_income_ttm")),
        "eps": eps_value,
        "roe_pct": to_num(mapped.get("return_on_equity")),
        "pb": to_num(mapped.get("price_book_fq")),
    }


def fetch_tv_fundamentals(symbols):
    clean_symbols = []
    seen = set()
    for s in symbols:
        clean = normalize_symbol(s)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        clean_symbols.append(clean)

    if not clean_symbols:
        return {}

    result = {}
    batch_size = 180
    for i in range(0, len(clean_symbols), batch_size):
        batch = clean_symbols[i : i + batch_size]
        tickers = [f"BIST:{s}" for s in batch]
        target = "https://scanner.tradingview.com/turkey/scan"
        body = {
            "symbols": {"tickers": tickers, "query": {"types": []}},
            "columns": TV_FUND_COLUMNS,
        }
        req = Request(
            target,
            data=json.dumps(body).encode("utf-8"),
            headers={**YAHOO_HEADERS, "Content-Type": "application/json"},
            method="POST",
        )

        with urlopen(req, timeout=25) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

        for row in payload.get("data", []):
            ticker = row.get("s")
            symbol_is = symbol_from_tv_ticker(ticker)
            symbol = normalize_symbol(symbol_is)
            values = row.get("d") or []
            if not symbol or len(values) != len(TV_FUND_COLUMNS):
                continue

            mapped = dict(zip(TV_FUND_COLUMNS, values))
            result[symbol] = {
                "url": None,
                "source": "tradingview",
                "status": "ok",
                "metrics": _tv_to_fund_metrics(mapped),
                "currency": mapped.get("currency") or "TRY",
            }

    return result


def _merge_fundamentals_entry(base: dict, incoming: dict):
    merged = dict(base or {})
    merged_metrics = dict((base or {}).get("metrics") or {})
    for key, value in ((incoming or {}).get("metrics") or {}).items():
        if merged_metrics.get(key) is None and value is not None:
            merged_metrics[key] = value

    merged["metrics"] = merged_metrics
    merged["url"] = merged.get("url") or incoming.get("url")

    base_status = (base or {}).get("status")
    if not base_status or base_status in {"no-url", "error", "empty", "missing"}:
        merged["status"] = incoming.get("status") or base_status or "ok"
    else:
        merged["status"] = base_status

    incoming_source = incoming.get("source")
    if incoming_source:
        prev_source = merged.get("source")
        if prev_source and prev_source != incoming_source:
            merged["source"] = f"{prev_source}+{incoming_source}"
        elif not prev_source:
            merged["source"] = incoming_source

    return merged


def _needs_fundamentals_backfill(entry: dict):
    if not isinstance(entry, dict):
        return True
    status = entry.get("status")
    if status in {"no-url", "error", "empty", "missing", None}:
        return True
    metrics = entry.get("metrics") or {}
    return all(metrics.get(key) is None for key in FUNDAMENTAL_KEYS)


def fetch_tv_prices(symbols):
    symbols = [s.upper().strip() for s in symbols if s and isinstance(s, str)]
    now = time.time()

    items_by_symbol = {}
    missing_symbols = []

    with state_lock:
        for symbol in symbols:
            cached = price_cache.get((symbol, "tv", "tv"))
            if cached and (now - cached["ts"]) < PRICE_CACHE_TTL:
                items_by_symbol[symbol] = cached["item"]
            else:
                missing_symbols.append(symbol)

    errors = []
    if missing_symbols:
        target = "https://scanner.tradingview.com/turkey/scan"
        tv_tickers = [tv_ticker_from_symbol(s) for s in missing_symbols]
        body = {
            "symbols": {"tickers": tv_tickers, "query": {"types": []}},
            "columns": ["name", "close", "change", "change_abs", "time", "update_mode"],
        }
        req = Request(
            target,
            data=json.dumps(body).encode("utf-8"),
            headers={**YAHOO_HEADERS, "Content-Type": "application/json"},
            method="POST",
        )

        with urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

        seen = set()
        for row in payload.get("data", []):
            ticker = row.get("s")
            symbol = symbol_from_tv_ticker(ticker)
            if not symbol:
                continue
            data = row.get("d") or []
            close = data[1] if len(data) > 1 else None
            change_pct = data[2] if len(data) > 2 else None
            change_abs = data[3] if len(data) > 3 else None
            tv_time = data[4] if len(data) > 4 else None
            update_mode = data[5] if len(data) > 5 else None
            if close is None:
                errors.append({"symbol": symbol, "error": "no-close"})
                continue

            prev_close = None
            if isinstance(change_abs, (int, float)):
                prev_close = close - change_abs
            elif isinstance(change_pct, (int, float)) and change_pct != -100:
                prev_close = close / (1 + (change_pct / 100))

            item = {
                "symbol": symbol,
                "price": close,
                "prevClose": prev_close,
                "marketTs": int(tv_time) if isinstance(tv_time, (int, float)) else None,
                "asOfTs": int(time.time()),
                "currency": "TRY",
                "dayHigh": None,
                "dayLow": None,
                "updateMode": update_mode,
            }
            items_by_symbol[symbol] = item
            seen.add(symbol)

            with state_lock:
                price_cache[(symbol, "tv", "tv")] = {"item": item, "ts": time.time()}

        for symbol in missing_symbols:
            if symbol not in seen:
                errors.append({"symbol": symbol, "error": "not-found-tv"})

    items = [items_by_symbol[s] for s in symbols if s in items_by_symbol]
    return items, errors


def _at_session_time(base_dt: datetime, hour: int, minute: int):
    return base_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _resolve_day_session(day_dt: datetime):
    day_key = day_dt.date().isoformat()
    overrides = get_session_overrides()
    day_override = overrides.get(day_key) or {}

    holidays = get_public_holidays(day_dt.year, "TR")
    holiday_name = holidays.get(day_key)
    is_weekend = day_dt.weekday() >= 5

    open_hour = MARKET_OPEN_HOUR
    open_minute = MARKET_OPEN_MINUTE
    close_hour = MARKET_CLOSE_HOUR
    close_minute = MARKET_CLOSE_MINUTE
    closed_all_day = False
    reason_hint = None
    session_label = None

    if holiday_name:
        if _is_half_day_holiday(holiday_name) and not is_weekend:
            close_hour, close_minute = _parse_hhmm("13:00", 13, 0)
            reason_hint = "half_day"
            session_label = holiday_name
        else:
            closed_all_day = True
            reason_hint = "holiday"
            session_label = holiday_name
    elif is_weekend:
        closed_all_day = True
        reason_hint = "weekend"

    if day_override:
        if "open" in day_override:
            open_hour, open_minute = _parse_hhmm(day_override.get("open"), open_hour, open_minute)
        if "close" in day_override:
            close_hour, close_minute = _parse_hhmm(day_override.get("close"), close_hour, close_minute)

        if day_override.get("closed") is True:
            closed_all_day = True
            reason_hint = day_override.get("reason") or "manual_closed"
        elif day_override.get("closed") is False or ("open" in day_override or "close" in day_override):
            closed_all_day = False
            reason_hint = day_override.get("reason") or reason_hint

        if isinstance(day_override.get("label"), str) and day_override.get("label"):
            session_label = day_override.get("label")

    return {
        "date": day_key,
        "openHour": open_hour,
        "openMinute": open_minute,
        "closeHour": close_hour,
        "closeMinute": close_minute,
        "closedAllDay": closed_all_day,
        "reasonHint": reason_hint,
        "label": session_label,
        "isWeekend": is_weekend,
        "holidayName": holiday_name,
    }


def _find_next_open(from_dt: datetime):
    cursor = from_dt
    for _ in range(400):
        day_session = _resolve_day_session(cursor)
        day_open = _at_session_time(cursor, day_session["openHour"], day_session["openMinute"])
        day_close = _at_session_time(cursor, day_session["closeHour"], day_session["closeMinute"])

        if not day_session["closedAllDay"]:
            if cursor < day_open:
                return day_open
            if day_open <= cursor < day_close:
                return day_open

        cursor = (cursor + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    return from_dt


def _get_market_probe(force: bool = False):
    now = time.time()
    with state_lock:
        cached = market_probe_cache.get("data")
        cached_ts = market_probe_cache.get("ts") or 0.0

    if not force and cached and (now - cached_ts) < MARKET_PROBE_TTL:
        return cached

    probe = {
        "active": None,
        "freshestMarketTs": None,
        "stalenessSec": None,
        "checkedSymbols": len(MARKET_PROBE_SYMBOLS),
        "source": "tradingview",
    }

    try:
        items, _ = fetch_tv_prices(MARKET_PROBE_SYMBOLS)
        market_ts_values = [int(i.get("marketTs")) for i in items if isinstance(i.get("marketTs"), (int, float))]
        if market_ts_values:
            freshest = max(market_ts_values)
            staleness = max(0, int(now) - freshest)
            probe["freshestMarketTs"] = freshest
            probe["stalenessSec"] = staleness
            probe["active"] = staleness <= MARKET_PROBE_STALE_SEC
        else:
            probe["active"] = False
    except Exception as exc:
        probe["error"] = str(exc)

    with state_lock:
        market_probe_cache["ts"] = now
        market_probe_cache["data"] = probe

    return probe


def get_market_status():
    now = datetime.now(MARKET_TZ)
    day_session = _resolve_day_session(now)
    today_open = _at_session_time(now, day_session["openHour"], day_session["openMinute"])
    today_close = _at_session_time(now, day_session["closeHour"], day_session["closeMinute"])

    expected_open = (not day_session["closedAllDay"]) and (today_open <= now < today_close)

    if expected_open:
        reason = "open"
    else:
        if day_session["closedAllDay"]:
            reason = day_session["reasonHint"] or "closed"
        elif now < today_open:
            reason = "before_open"
        elif day_session["reasonHint"] == "half_day":
            reason = "half_day_closed"
        else:
            reason = "after_close"

    probe = _get_market_probe()
    feed_active = probe.get("active")
    is_open = expected_open
    if expected_open and feed_active is False:
        # Keep session state authoritative. Feed freshness is reported separately
        # and should not mark the exchange as closed by itself.
        reason = "open_feed_stale"

    if is_open:
        next_close = today_close
        next_open = _find_next_open((today_close + timedelta(seconds=1)).replace(second=0, microsecond=0))
    else:
        next_open = _find_next_open(now)
        next_open_session = _resolve_day_session(next_open)
        next_close = _at_session_time(next_open, next_open_session["closeHour"], next_open_session["closeMinute"])

    return {
        "isOpen": is_open,
        "expectedOpen": expected_open,
        "feedActive": feed_active,
        "reason": reason,
        "timezone": "Europe/Istanbul",
        "serverTime": now.isoformat(),
        "serverTs": int(now.timestamp()),
        "session": {
            "open": f"{day_session['openHour']:02d}:{day_session['openMinute']:02d}",
            "close": f"{day_session['closeHour']:02d}:{day_session['closeMinute']:02d}",
        },
        "isHoliday": bool(day_session["holidayName"]),
        "holidayName": day_session["holidayName"],
        "sessionLabel": day_session["label"],
        "marketProbe": probe,
        "nextOpenTs": int(next_open.timestamp()),
        "nextCloseTs": int(next_close.timestamp()),
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def _send_json(self, status: int, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_prices(self, symbols, range_="1d", interval="5m"):
        symbols = [s.strip().upper() for s in symbols if isinstance(s, str) and s.strip()]
        if not symbols:
            with state_lock:
                symbols = list(symbol_state["symbols"])

        try:
            items, errors = fetch_tv_prices(symbols)
            return self._send_json(200, {"items": items, "errors": errors})
        except Exception:
            items = []
            errors = []
            for symbol in symbols:
                try:
                    item = get_price_item(symbol, range_, interval)
                    if item:
                        items.append(item)
                    else:
                        errors.append({"symbol": symbol, "error": "no-meta"})
                except Exception as exc:
                    errors.append({"symbol": symbol, "error": str(exc)})
            items.sort(key=lambda x: x.get("symbol", ""))
            return self._send_json(200, {"items": items, "errors": errors})

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/api/search":
            q = (qs.get("q") or [""])[0]
            quotes_count = (qs.get("quotesCount") or ["100"])[0]
            news_count = (qs.get("newsCount") or ["0"])[0]
            target = f"https://query2.finance.yahoo.com/v1/finance/search?{urlencode({'q': q, 'quotesCount': quotes_count, 'newsCount': news_count})}"
            return self._proxy_json(target)

        if path == "/api/symbols":
            refresh = (qs.get("refresh") or ["0"])[0] == "1"
            ensure_symbol_scan(force=refresh)
            with state_lock:
                symbols = list(symbol_state["symbols"])
                last_scan_ts = symbol_state["last_scan_ts"]
                scanning = symbol_state["scanning"]
            return self._send_json(
                200,
                {
                    "symbols": symbols,
                    "count": len(symbols),
                    "scanning": scanning,
                    "lastScanTs": last_scan_ts,
                },
            )

        if path == "/api/market-status":
            return self._send_json(200, get_market_status())

        if path == "/api/fundamentals/status":
            snap = _fundamentals_snapshot()
            return self._send_json(
                200,
                {
                    "source": snap["source"],
                    "fetchedAt": snap["fetchedAt"],
                    "count": snap["count"],
                    "running": snap["running"],
                    "lastSuccessTs": snap["lastSuccessTs"],
                    "lastAttemptTs": snap["lastAttemptTs"],
                    "lastError": snap["lastError"],
                },
            )

        if path == "/api/fundamentals":
            symbol = normalize_symbol((qs.get("symbol") or [""])[0])
            snap = _fundamentals_snapshot()

            if symbol:
                data = snap["symbols"].get(symbol)
                if _needs_fundamentals_backfill(data):
                    try:
                        tv_map = fetch_tv_fundamentals([symbol])
                        tv_entry = tv_map.get(symbol)
                        if tv_entry:
                            with state_lock:
                                current_payload = dict(fundamentals_state.get("payload") or {})
                                current_symbols = dict(current_payload.get("symbols") or {})
                                merged = _merge_fundamentals_entry(current_symbols.get(symbol) or {}, tv_entry)
                                current_symbols[symbol] = merged
                                current_payload["symbols"] = current_symbols
                                current_payload["count"] = len(current_symbols)
                                fundamentals_state["payload"] = current_payload
                            data = merged
                    except Exception:
                        pass

                if _needs_fundamentals_backfill(data) and not snap["running"]:
                    _start_fundamentals_refresh(force=True, symbols=[to_bist_symbol(symbol)], reason="on-demand-symbol")

                return self._send_json(
                    200,
                    {
                        "symbol": symbol,
                        "source": snap["source"],
                        "fetchedAt": snap["fetchedAt"],
                        "running": snap["running"],
                        "lastError": snap["lastError"],
                        "data": data,
                    },
                )

            return self._send_json(
                200,
                {
                    "source": snap["source"],
                    "fetchedAt": snap["fetchedAt"],
                    "count": snap["count"],
                    "running": snap["running"],
                    "lastError": snap["lastError"],
                },
            )

        if path == "/api/chart":
            symbol = (qs.get("symbol") or [""])[0].upper()
            range_ = (qs.get("range") or ["1d"])[0]
            interval = (qs.get("interval") or ["5m"])[0]
            if not symbol:
                return self._send_json(400, {"error": "symbol is required"})
            query = {
                "range": range_,
                "interval": interval,
                "includePrePost": "false",
                "events": "div,splits,capitalGains",
            }
            target = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{urlencode(query)}"
            return self._proxy_json(target)

        if path == "/api/prices":
            raw_symbols = (qs.get("symbols") or [""])[0]
            range_ = (qs.get("range") or ["1d"])[0]
            interval = (qs.get("interval") or ["5m"])[0]
            symbols = [s.strip().upper() for s in raw_symbols.split(",") if s.strip()]
            return self._handle_prices(symbols, range_, interval)

        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/prices":
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except Exception:
                length = 0

            body = self.rfile.read(length) if length > 0 else b"{}"
            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception:
                return self._send_json(400, {"error": "invalid json body"})

            symbols = payload.get("symbols") or []
            range_ = payload.get("range") or "1d"
            interval = payload.get("interval") or "5m"
            return self._handle_prices(symbols, range_, interval)

        if path == "/api/fundamentals/refresh":
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except Exception:
                length = 0
            body = self.rfile.read(length) if length > 0 else b"{}"
            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception:
                payload = {}

            raw_symbols = payload.get("symbols") or []
            symbols = []
            for raw in raw_symbols:
                mapped = to_bist_symbol(raw)
                if mapped:
                    symbols.append(mapped)
            started = _start_fundamentals_refresh(force=True, symbols=symbols if symbols else None, reason="manual-refresh")
            return self._send_json(200, {"started": started, "running": _fundamentals_snapshot()["running"]})

        return self._send_json(404, {"error": "not found"})

    def _proxy_json(self, target: str):
        try:
            payload = fetch_json(target)
            return self._send_json(200, payload)
        except HTTPError as exc:
            return self._send_json(exc.code, {"error": f"Yahoo HTTP {exc.code}"})
        except URLError as exc:
            return self._send_json(502, {"error": f"Yahoo bağlantı hatası: {exc.reason}"})
        except Exception as exc:
            return self._send_json(500, {"error": str(exc)})


def run(host: str = "127.0.0.1", port: int = 5500):
    load_symbols_cache_file()
    load_fundamentals_file()
    ensure_symbol_scan(force=True)
    _start_fundamentals_refresh(force=False, reason="startup")
    threading.Thread(target=_fundamentals_scheduler_loop, daemon=True).start()
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Server running at http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run()
