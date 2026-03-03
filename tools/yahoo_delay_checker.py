import json
import time
import argparse
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def fetch_chart(symbol: str, interval: str = "5m", range_: str = "1d") -> dict:
    base_url = "https://query1.finance.yahoo.com/v8/finance/chart/" + symbol
    query = urlencode({"range": range_, "interval": interval})
    url = f"{base_url}?{query}"

    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        },
    )

    with urlopen(req, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))

    error = payload.get("chart", {}).get("error")
    if error:
        raise RuntimeError(f"Yahoo API error: {error}")

    result = payload["chart"]["result"][0]
    return result


def ts_to_str(ts: int, offset_seconds: int) -> str:
    tz = timezone.utc
    dt_utc = datetime.fromtimestamp(ts, tz=tz)
    dt_local = dt_utc.timestamp() + offset_seconds
    local = datetime.fromtimestamp(dt_local, tz=timezone.utc)
    return local.strftime("%Y-%m-%d %H:%M:%S") + " TRT"


def validate_ohlc(open_, high, low, close) -> list[str]:
    issues = []
    if high < max(open_, close):
        issues.append("high < max(open, close)")
    if low > min(open_, close):
        issues.append("low > min(open, close)")
    if low > high:
        issues.append("low > high")
    return issues


def normalize_yahoo_payload(result: dict, interval: str, range_: str, delayed: bool = True) -> dict:
    meta = result.get("meta", {})
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    timestamps = result.get("timestamp", [])

    symbol = meta.get("symbol")
    currency = meta.get("currency")
    now_ts = int(time.time())
    market_time = int(meta.get("regularMarketTime", 0) or 0)

    last = float(meta.get("regularMarketPrice", 0) or 0)
    previous_close = float(meta.get("previousClose", 0) or 0)
    day_change = last - previous_close if previous_close else 0.0
    day_change_pct = ((day_change / previous_close) * 100) if previous_close else 0.0

    candles = []
    issues = []
    for i, ts in enumerate(timestamps):
        o = quote.get("open", [None])[i] if i < len(quote.get("open", [])) else None
        h = quote.get("high", [None])[i] if i < len(quote.get("high", [])) else None
        l = quote.get("low", [None])[i] if i < len(quote.get("low", [])) else None
        c = quote.get("close", [None])[i] if i < len(quote.get("close", [])) else None
        v = quote.get("volume", [None])[i] if i < len(quote.get("volume", [])) else None

        if None in (o, h, l, c, v):
            continue

        o_f = float(o)
        h_f = float(h)
        l_f = float(l)
        c_f = float(c)
        v_i = int(v)

        ohlc_issues = validate_ohlc(o_f, h_f, l_f, c_f)
        if ohlc_issues:
            issues.extend([f"ts={ts}: {msg}" for msg in ohlc_issues])

        is_complete = True
        if market_time and ts >= market_time:
            is_complete = False

        candles.append(
            {
                "ts": int(ts),
                "interval": interval,
                "open": o_f,
                "high": h_f,
                "low": l_f,
                "close": c_f,
                "volume": v_i,
                "isComplete": is_complete,
            }
        )

    stale_sec = (now_ts - market_time) if market_time else 0
    warnings = []
    if stale_sec > 0:
        warnings.append(f"delayed_data_sec={stale_sec}")
    if not candles:
        warnings.append("no_valid_candles")
    if issues:
        warnings.extend(issues[:10])

    normalized = {
        "provider": "yahoo",
        "market": "BIST",
        "symbol": symbol,
        "delayed": delayed,
        "currency": currency,
        "asOfTs": now_ts,
        "quote": {
            "last": last,
            "previousClose": previous_close,
            "dayChange": day_change,
            "dayChangePct": day_change_pct,
            "dayLow": meta.get("regularMarketDayLow"),
            "dayHigh": meta.get("regularMarketDayHigh"),
            "fiftyTwoWeekLow": meta.get("fiftyTwoWeekLow"),
            "fiftyTwoWeekHigh": meta.get("fiftyTwoWeekHigh"),
            "regularVolume": meta.get("regularMarketVolume"),
            "avgVolume": meta.get("averageDailyVolume3Month") or meta.get("averageDailyVolume10Day"),
            "marketTime": market_time,
        },
        "candles": candles,
        "events": {
            "dividends": [],
            "splits": [],
        },
        "meta": {
            "exchangeName": meta.get("exchangeName"),
            "fullExchangeName": meta.get("fullExchangeName"),
            "timezone": meta.get("timezone"),
            "gmtoffset": meta.get("gmtoffset"),
            "dataGranularity": meta.get("dataGranularity"),
            "range": range_,
            "marketState": meta.get("marketState"),
        },
        "quality": {
            "staleSec": max(0, int(stale_sec)),
            "ohlcConsistent": len(issues) == 0,
            "warnings": warnings,
        },
    }
    return normalized


def watch_prices(symbols: list[str], interval_sec: int) -> None:
    print("=" * 72)
    print("Canli Fiyat Izleme (Delayed)")
    print("=" * 72)
    print(f"Semboller: {', '.join(symbols)}")
    print(f"Guncelleme Araligi: {interval_sec} sn")
    print("Cikis: Ctrl + C\n")

    try:
        while True:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            print(f"[{now_str}]")
            for symbol in symbols:
                try:
                    result = fetch_chart(symbol=symbol, interval="5m", range_="1d")
                    meta = result.get("meta", {})

                    price = meta.get("regularMarketPrice")
                    prev_close = meta.get("previousClose")
                    market_time = meta.get("regularMarketTime")
                    gmtoffset = int(meta.get("gmtoffset", 0))

                    if price is None:
                        print(f"- {symbol}: fiyat yok")
                        continue

                    change_pct = None
                    if prev_close not in (None, 0):
                        change_pct = ((price - prev_close) / prev_close) * 100

                    time_str = "N/A"
                    if market_time:
                        time_str = ts_to_str(int(market_time), gmtoffset)

                    if change_pct is None:
                        print(f"- {symbol}: {float(price):.2f} TRY | Zaman: {time_str}")
                    else:
                        print(
                            f"- {symbol}: {float(price):.2f} TRY | Gunluk Degisim: {float(change_pct):+.2f}% | Zaman: {time_str}"
                        )
                except Exception as exc:
                    print(f"- {symbol}: veri alinamadi ({exc})")

            print("-" * 72)
            time.sleep(interval_sec)
    except KeyboardInterrupt:
        print("\nIzleme durduruldu.")


def main() -> None:
    parser = argparse.ArgumentParser(description="BIST delayed data checker")
    parser.add_argument("--symbol", default="THYAO.IS", help="Tek sembol (default: THYAO.IS)")
    parser.add_argument("--watch", action="store_true", help="Canli fiyat izleme modu")
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=["THYAO.IS", "ASELS.IS"],
        help="Watch modunda izlenecek semboller",
    )
    parser.add_argument("--interval", type=int, default=10, help="Watch guncelleme suresi (sn)")
    parser.add_argument("--normalized", action="store_true", help="Normalize edilmis JSON ciktiyi bas")
    args = parser.parse_args()

    if args.watch:
        watch_prices(symbols=args.symbols, interval_sec=args.interval)
        return

    symbol = args.symbol
    result = fetch_chart(symbol=symbol, interval="5m", range_="1d")

    if args.normalized:
        normalized = normalize_yahoo_payload(result=result, interval="5m", range_="1d", delayed=True)
        print(json.dumps(normalized, ensure_ascii=False, indent=2))
        return

    meta = result["meta"]
    quote = result["indicators"]["quote"][0]
    timestamps = result.get("timestamp", [])

    offset = int(meta.get("gmtoffset", 0))
    market_ts = int(meta.get("regularMarketTime", 0))
    market_price = float(meta.get("regularMarketPrice", 0.0))

    now_ts = int(time.time())
    delay_min = round((now_ts - market_ts) / 60, 2) if market_ts else None

    print("=" * 72)
    print("BIST Delayed Veri Kontrolu")
    print("=" * 72)
    print(f"Sembol           : {meta.get('symbol')}")
    print(f"Borsa            : {meta.get('fullExchangeName')} ({meta.get('exchangeName')})")
    print(f"Para Birimi      : {meta.get('currency')}")
    print(f"Anlik/Gosterim   : {market_price}")
    print(f"Son Piyasa Zaman : {ts_to_str(market_ts, offset)}")
    if delay_min is not None:
        print(f"Tahmini Gecikme  : {delay_min} dk")

    print("\nSon 5 mum (5m):")

    rows = []
    for i, ts in enumerate(timestamps):
        o = quote["open"][i] if i < len(quote["open"]) else None
        h = quote["high"][i] if i < len(quote["high"]) else None
        l = quote["low"][i] if i < len(quote["low"]) else None
        c = quote["close"][i] if i < len(quote["close"]) else None
        v = quote["volume"][i] if i < len(quote["volume"]) else None

        if None in (o, h, l, c, v):
            continue

        rows.append((ts, o, h, l, c, v))

    tail = rows[-5:]
    if not tail:
        print("Veri bulunamadi.")
        return

    all_issues = []
    for ts, o, h, l, c, v in tail:
        issues = validate_ohlc(o, h, l, c)
        issues_text = "OK" if not issues else "; ".join(issues)
        all_issues.extend(issues)
        print(
            f"- {ts_to_str(ts, offset)} | O:{o:.2f} H:{h:.2f} L:{l:.2f} C:{c:.2f} V:{int(v)} | {issues_text}"
        )

    # Basic sanity checks
    closes = [r[4] for r in tail]
    min_close = min(closes)
    max_close = max(closes)

    print("\nMantik Kontrolu:")
    print(f"- Son 5 mum close araligi: {min_close:.2f} - {max_close:.2f}")
    if all_issues:
        print(f"- OHLC tutarlilik: PROBLEM ({len(all_issues)} ihlal)")
    else:
        print("- OHLC tutarlilik: OK")

    if market_price > 0 and min_close > 0:
        ratio = market_price / closes[-1]
        print(f"- Last close ile market price orani: {ratio:.4f}")
        if 0.90 <= ratio <= 1.10:
            print("- Fiyat seviyesi: Mantikli (son close ile yakin)")
        else:
            print("- Fiyat seviyesi: Supheli (son close'dan cok farkli)")


if __name__ == "__main__":
    main()
