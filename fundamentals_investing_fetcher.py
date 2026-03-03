import json
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import cloudscraper
from bs4 import BeautifulSoup

# This module is imported by web/server.py for scheduled/on-demand fundamentals refresh.
# Keep `run(symbols, write_file=True)` signature stable to avoid breaking runtime flow.

OUT_FILE = Path(__file__).resolve().parent / "web" / "investing_fundamentals.json"
URL_CACHE_FILE = Path(__file__).resolve().parent / "web" / "investing_symbol_urls.json"

SYMBOL_URLS = {
    "ARCLK": "https://tr.investing.com/equities/arcelik",
    "ASELS": "https://tr.investing.com/equities/aselsan",
    "THYAO": "https://tr.investing.com/equities/turk-hava-yollari",
}

METRIC_ORDER = [
    ("pe", "F/K"),
    ("ebitda", "FAVÖK"),
    ("market_cap", "Piyasa Değeri"),
    ("avg_volume_3m", "Ortalama Hacim (3Ay)"),
    ("change_1y_pct", "1 Yıllık Değişim"),
    ("revenue", "Gelir"),
    ("net_income", "Net Kâr"),
    ("eps", "Hisse Başına Kar (EPS)"),
    ("roe_pct", "Özkaynak Getirisi (ROE)"),
    ("pb", "PD/DD"),
]

LABEL_MAP = {
    "Fiyat / Kazanç Oranı": "pe",
    "FAVÖK": "ebitda",
    "Piyasa değeri": "market_cap",
    "Ortalama Hacim (3Ay)": "avg_volume_3m",
    "1 Yıllık Değişim": "change_1y_pct",
    "Gelir": "revenue",
    "Net Kâr": "net_income",
    "Hisse B. Kar": "eps",
    "Hisse Başına Kar": "eps",
    "Özkaynak Getirisi": "roe_pct",
    "Fiyat/Deft. Değeri": "pb",
}


def fetch_html(scraper: cloudscraper.CloudScraper, url: str) -> str:
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edg/128.0.0.0 Safari/537.36",
    ]

    last_error = None
    for _ in range(4):
        ua = random.choice(user_agents)
        try:
            scraper.get(
                "https://tr.investing.com/",
                timeout=25,
                headers={
                    "Accept-Language": "tr-TR,tr;q=0.9",
                    "User-Agent": ua,
                    "Referer": "https://tr.investing.com/",
                },
            )
            time.sleep(0.7)

            response = scraper.get(
                url,
                timeout=40,
                headers={
                    "Accept-Language": "tr-TR,tr;q=0.9",
                    "User-Agent": ua,
                    "Referer": "https://tr.investing.com/",
                    "Upgrade-Insecure-Requests": "1",
                    "Cache-Control": "no-cache",
                },
            )
            if response.status_code == 200:
                return response.text
            last_error = RuntimeError(f"HTTP {response.status_code}")
        except Exception as exc:
            last_error = exc
        time.sleep(1.2)

    raise RuntimeError(str(last_error) if last_error else "unknown fetch error")


def normalize_symbol(symbol: str) -> str:
    clean = (symbol or "").strip().upper()
    if clean.endswith(".IS"):
        clean = clean[:-3]
    return clean


def load_url_cache() -> dict:
    if not URL_CACHE_FILE.exists():
        return {}
    try:
        payload = json.loads(URL_CACHE_FILE.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return {normalize_symbol(k): v for k, v in payload.items() if isinstance(v, str) and "/equities/" in v}
    except Exception:
        pass
    return {}


def save_url_cache(cache: dict) -> None:
    URL_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    clean = {normalize_symbol(k): v for k, v in cache.items() if isinstance(v, str) and "/equities/" in v}
    URL_CACHE_FILE.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")


def extract_equity_links(text: str) -> list[str]:
    if not text:
        return []
    links = set()
    for match in re.findall(r"https?://tr\.investing\.com/equities/[a-z0-9\-]+", text, flags=re.IGNORECASE):
        links.add(match.split("?")[0])
    for match in re.findall(r"/equities/[a-z0-9\-]+", text, flags=re.IGNORECASE):
        links.add(f"https://tr.investing.com{match.split('?')[0]}")
    return sorted(links)


def choose_best_link(symbol: str, links: list[str]) -> str | None:
    if not links:
        return None
    symbol_lc = symbol.lower()

    scored = []
    for link in links:
        score = 0
        slug = link.rsplit("/", 1)[-1].lower()
        if slug == symbol_lc:
            score += 4
        if symbol_lc in slug:
            score += 3
        if "hisse" in slug:
            score -= 1
        scored.append((score, link))

    scored.sort(key=lambda x: (-x[0], len(x[1])))
    return scored[0][1] if scored else None


def resolve_symbol_url(scraper: cloudscraper.CloudScraper, symbol: str, url_cache: dict) -> str | None:
    sym = normalize_symbol(symbol)
    if sym in url_cache:
        return url_cache[sym]
    if sym in SYMBOL_URLS:
        url_cache[sym] = SYMBOL_URLS[sym]
        return SYMBOL_URLS[sym]

    queries = [
        f"https://tr.investing.com/search/?q={quote(sym)}",
        f"https://tr.investing.com/search/?q={quote(sym + ' hisse')}",
        f"https://tr.investing.com/search/?q={quote('BIST ' + sym)}",
    ]

    for query_url in queries:
        try:
            text = fetch_html(scraper, query_url)
        except Exception:
            continue

        links = extract_equity_links(text)
        best = choose_best_link(sym, links)
        if best:
            url_cache[sym] = best
            return best

    return None


def parse_metrics(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    metrics = {}

    for dt in soup.select("dt"):
        label = dt.get_text(" ", strip=True)
        if label not in LABEL_MAP:
            continue

        dd = dt.find_next_sibling("dd")
        if not dd:
            continue

        value = dd.get_text(" ", strip=True)
        if not value:
            continue

        key = LABEL_MAP[label]
        if key not in metrics:
            metrics[key] = value

    return metrics


def run(symbols: list[str], write_file: bool = True) -> dict:
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )

    normalized_symbols = []
    seen = set()
    for symbol in symbols:
        norm = normalize_symbol(symbol)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        normalized_symbols.append(norm)

    url_cache = load_url_cache()

    payload = {
        "source": "investing.com",
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "count": len(normalized_symbols),
        "symbols": {},
    }

    for symbol in normalized_symbols:
        url = resolve_symbol_url(scraper, symbol, url_cache)
        if not url:
            payload["symbols"][symbol] = {
                "url": None,
                "metrics": {},
                "status": "no-url",
            }
            continue

        try:
            html = fetch_html(scraper, url)
            metrics = parse_metrics(html)
            payload["symbols"][symbol] = {
                "url": url,
                "metrics": metrics,
                "status": "ok" if metrics else "empty",
            }
        except Exception as exc:
            payload["symbols"][symbol] = {
                "url": url,
                "metrics": {},
                "status": "error",
                "error": str(exc),
            }

    if write_file:
        OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        OUT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        save_url_cache(url_cache)

    return payload


def print_report(payload: dict) -> None:
    print("=" * 88)
    print("INVESTING GUNLUK FUNDAMENTALS")
    print("=" * 88)
    print("Kaynak:", payload.get("source"))
    print("Cekim Zamani (UTC):", payload.get("fetchedAt"))
    print("JSON:", OUT_FILE)
    print()

    for symbol, info in payload.get("symbols", {}).items():
        print("-" * 88)
        print(symbol, "|", info.get("status"), "|", info.get("url"))
        print("-" * 88)
        metrics = info.get("metrics") or {}
        for key, label in METRIC_ORDER:
            print(f"{label:<28}: {metrics.get(key, '-')}")
        print()


if __name__ == "__main__":
    result = run(["THYAO", "ASELS", "ARCLK"])
    print_report(result)
