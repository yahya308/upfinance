import argparse
import json
import time
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import HTTPError

KAP_BASE = "https://www.kap.org.tr"

KAP_API_COMPANY_SGBF = "api/company-detail/sgbf-data"
KAP_API_COMPANY_ITEMS = "api/company/items"
KAP_API_SEARCH_SMART = "api/search/smart"
KAP_API_FINANCIAL_DOWNLOAD = "api/financialTable/download"
TV_SCAN_URL = "https://scanner.tradingview.com/turkey/scan"

TV_COLUMNS = [
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

KAP_MAGIC_PARAMS = [
    "tr",
    "1",
    "2",
    "0",
    "8aca4ae18e616cd4018e79a802800000",
    "402881cf91d6efd60191d6f04e530000",
    "402881cf91d6efd60191d6f08e900001",
]

METRIC_LABELS = {
    "pe": "F/K",
    "ebitda": "FAVOK",
    "market_cap": "Piyasa Degeri",
    "avg_volume": "Ortalama Hacim (60g)",
    "change_1y_pct": "1 Yillik Degisim",
    "revenue": "Gelir (TTM)",
    "net_income": "Net Kar (TTM)",
    "eps": "Hisse Basina Kar (EPS)",
    "roe_pct": "Ozkaynak Getirisi (ROE)",
    "pb": "PD/DD",
}


def to_num(value):
    if isinstance(value, (int, float)):
        return float(value)
    return None


def fetch_json(url: str, method: str = "GET", body: dict | None = None):
    payload = None
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
    }
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = Request(url, data=payload, headers=headers, method=method)
    with urlopen(req, timeout=25) as response:
        raw = response.read().decode("utf-8", "ignore")
        try:
            return json.loads(raw)
        except Exception:
            return raw


def kap_get_json(path: str):
    try:
        return fetch_json(f"{KAP_BASE}/{path}", method="GET")
    except HTTPError as exc:
        return {"_http_error": exc.code}
    except Exception as exc:
        return {"_error": str(exc)}


def kap_post_json(path: str, body: dict):
    try:
        return fetch_json(f"{KAP_BASE}/{path}", method="POST", body=body)
    except HTTPError as exc:
        return {"_http_error": exc.code}
    except Exception as exc:
        return {"_error": str(exc)}


def response_meta(response) -> tuple[str, int | None, int | None, str | None]:
    if isinstance(response, list):
        return "ok", 200, len(response), None
    if isinstance(response, dict):
        if "_http_error" in response:
            return "err", int(response.get("_http_error") or 0), None, "http_error"
        if "_error" in response:
            return "err", None, None, str(response.get("_error"))
    return "err", None, None, "unexpected_response"


def normalize_symbol(symbol: str) -> str:
    clean = symbol.strip().upper()
    if clean.endswith(".IS"):
        clean = clean[:-3]
    return clean


def find_company(symbol: str) -> dict | None:
    payload = kap_post_json("tr/api/search/smart", {"keyword": symbol.lower()})
    if not isinstance(payload, list) or not payload:
        return None

    results = payload[0].get("results") or []
    exact = [r for r in results if str(r.get("cmpOrFundCode", "")).upper() == symbol]
    item = exact[0] if exact else (results[0] if results else None)
    if not isinstance(item, dict):
        return None

    oid = item.get("memberOrFundOid")
    if not oid:
        return None

    return {
        "oid": oid,
        "title": item.get("searchValue"),
        "code": str(item.get("cmpOrFundCode", "")).upper(),
    }


def extract_metrics_from_records(records) -> dict:
    if not isinstance(records, list):
        return {}
    metrics = {}

    for row in records:
        if not isinstance(row, dict):
            continue
        key = str(row.get("itemCode") or row.get("code") or row.get("name") or "").upper()
        value = to_num(row.get("value") or row.get("currentValue") or row.get("amount"))
        if value is None or not key:
            continue

        if key in {"PE", "FK", "F_K", "PRICE_EARNINGS"}:
            metrics["pe"] = value
        elif key in {"EBITDA", "FAVOK"}:
            metrics["ebitda"] = value
        elif key in {"MARKETCAP", "MARKET_CAP", "PIYASA_DEGERI"}:
            metrics["market_cap"] = value
        elif key in {"AVERAGE_VOLUME", "AVG_VOLUME", "HACIM_60G"}:
            metrics["avg_volume"] = value
        elif key in {"CHANGE_1Y", "PERFY", "YILLIK_DEGISIM"}:
            metrics["change_1y_pct"] = value
        elif key in {"REVENUE", "SATISLAR", "NET_SALES"}:
            metrics["revenue"] = value
        elif key in {"NET_INCOME", "NET_PROFIT", "NET_KAR"}:
            metrics["net_income"] = value
        elif key in {"EPS", "HISSE_BASINA_KAR"}:
            metrics["eps"] = value
        elif key in {"ROE", "ROE_PCT", "OZKAYNAK_GETIRISI"}:
            metrics["roe_pct"] = value
        elif key in {"PB", "PD_DD", "PRICE_BOOK"}:
            metrics["pb"] = value

    return metrics


def fetch_kap_live(symbol: str) -> dict:
    company = find_company(symbol)
    if not company:
        return {
            "symbol": symbol,
            "source": "kap_live",
            "asOf": int(time.time()),
            "currency": "TRY",
            "metrics": {},
            "pipelineNote": "KAP search/smart ile sirket bulunamadi",
            "provenance": [],
        }

    oid = company["oid"]
    provenance = []
    metrics = {}

    for param in KAP_MAGIC_PARAMS:
        for path in [
            f"tr/{KAP_API_COMPANY_ITEMS}/{oid}/{param}",
            f"tr/{KAP_API_COMPANY_ITEMS}/{param}/{oid}",
        ]:
            response = kap_get_json(path)
            state, status_code, size, error = response_meta(response)
            provenance.append({
                "path": path,
                "state": state,
                "http": status_code,
                "size": size,
                "error": error,
            })
            if isinstance(response, list) and response:
                metrics.update(extract_metrics_from_records(response))

    for year in [2026, 2025, 2024, 2023]:
        for period in [12, 9, 6, 3]:
            path = f"tr/{KAP_API_COMPANY_SGBF}/{oid}/{year}/{period}"
            response = kap_get_json(path)
            state, status_code, size, error = response_meta(response)
            provenance.append({
                "path": path,
                "state": state,
                "http": status_code,
                "size": size,
                "error": error,
            })
            if isinstance(response, list) and response:
                metrics.update(extract_metrics_from_records(response))

    for payload in [
        {"stockCode": symbol, "year": 2024, "period": 12},
        {"mkkMemberOid": oid, "year": 2024, "period": 12},
    ]:
        path = f"tr/{KAP_API_FINANCIAL_DOWNLOAD}"
        response = kap_post_json(path, payload)
        state, status_code, size, error = response_meta(response)
        provenance.append(
            {
                "path": path,
                "method": "POST",
                "payload": payload,
                "state": state,
                "http": status_code,
                "size": size,
                "error": error,
            }
        )

    download_429 = any(
        p.get("path") == f"tr/{KAP_API_FINANCIAL_DOWNLOAD}" and p.get("http") == 429
        for p in provenance
    )

    if metrics:
        note = "KAP endpointlerinden canli veri alindi"
    elif download_429:
        note = "KAP financialTable/download endpointi 429 ile engellendi"
    else:
        note = "KAP endpointleri sorgulandi fakat metrik veri bos dondu"

    return {
        "symbol": symbol,
        "source": "kap_live",
        "asOf": int(time.time()),
        "currency": "TRY",
        "metrics": metrics,
        "pipelineNote": note,
        "company": company,
        "provenance": provenance,
    }


def fetch_tv_fundamentals(symbols: list[str]) -> dict:
    tickers = [f"BIST:{symbol}" for symbol in symbols]
    payload = fetch_json(
        TV_SCAN_URL,
        method="POST",
        body={
            "symbols": {"tickers": tickers, "query": {"types": []}},
            "columns": TV_COLUMNS,
        },
    )

    result = {}
    if not isinstance(payload, dict):
        return result

    for row in payload.get("data", []):
        ticker = str(row.get("s") or "")
        values = row.get("d") or []
        if not ticker.startswith("BIST:") or len(values) != len(TV_COLUMNS):
            continue

        symbol = ticker.split(":", 1)[1].upper()
        mapped = dict(zip(TV_COLUMNS, values))
        derived_pe = to_num(mapped.get("price_earnings_ttm"))
        close_price = to_num(mapped.get("close"))
        eps_value = to_num(mapped.get("earnings_per_share_basic_ttm"))
        if derived_pe is None and close_price is not None and eps_value not in (None, 0):
            derived_pe = close_price / eps_value

        result[symbol] = {
            "currency": mapped.get("currency") or "TRY",
            "metrics": {
                "pe": derived_pe,
                "ebitda": to_num(mapped.get("ebitda_ttm")),
                "market_cap": to_num(mapped.get("market_cap_basic")),
                "avg_volume": to_num(mapped.get("average_volume_60d_calc")),
                "change_1y_pct": to_num(mapped.get("Perf.Y")),
                "revenue": to_num(mapped.get("total_revenue_ttm")),
                "net_income": to_num(mapped.get("net_income_ttm")),
                "eps": to_num(mapped.get("earnings_per_share_basic_ttm")),
                "roe_pct": to_num(mapped.get("return_on_equity")),
                "pb": to_num(mapped.get("price_book_fq")),
            },
        }

    return result


def merge_metrics(kap_metrics: dict, tv_metrics: dict) -> tuple[dict, list[str]]:
    merged = dict(kap_metrics)
    used_tv_fields = []
    for key, value in tv_metrics.items():
        if merged.get(key) is None and value is not None:
            merged[key] = value
            used_tv_fields.append(key)
    return merged, used_tv_fields


def run_kap_only(symbols: list[str]) -> list[dict]:
    tv_map = fetch_tv_fundamentals(symbols)
    items = []
    for symbol in symbols:
        item = fetch_kap_live(symbol)
        tv = tv_map.get(symbol) or {}
        tv_metrics = tv.get("metrics") or {}
        merged_metrics, used_tv_fields = merge_metrics(item.get("metrics") or {}, tv_metrics)

        item["metrics"] = merged_metrics
        if used_tv_fields:
            item["source"] = "kap+tradingview"
            item["pipelineNote"] = (
                f"KAP metrikleri eksik; TradingView ile tamamlandi ({', '.join(used_tv_fields)})"
            )
            item["sourceBreakdown"] = {
                "identity": "KAP search/smart",
                "metrics_fallback": "TradingView turkey/scan",
                "tv_fields": used_tv_fields,
            }
            if tv.get("currency"):
                item["currency"] = tv.get("currency")

        items.append(item)

    items.sort(key=lambda x: x.get("symbol", ""))
    return items


def fmt_num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:,.{digits}f}".replace(",", "_").replace(".", ",").replace("_", ".")


def fmt_money(value: float | None, currency: str = "TRY") -> str:
    if value is None:
        return "-"
    if abs(value) >= 1_000_000_000:
        return f"{fmt_num(value / 1_000_000_000, 2)} mlr {currency}"
    if abs(value) >= 1_000_000:
        return f"{fmt_num(value / 1_000_000, 2)} mn {currency}"
    return f"{fmt_num(value, 2)} {currency}"


def build_rows(item: dict) -> list[tuple[str, str]]:
    m = item.get("metrics", {})
    ccy = item.get("currency") or "TRY"

    return [
        (METRIC_LABELS["pe"], fmt_num(m.get("pe"), 2)),
        (METRIC_LABELS["ebitda"], fmt_money(m.get("ebitda"), ccy)),
        (METRIC_LABELS["market_cap"], fmt_money(m.get("market_cap"), ccy)),
        (METRIC_LABELS["avg_volume"], fmt_num(m.get("avg_volume"), 0)),
        (METRIC_LABELS["change_1y_pct"], f"{fmt_num(m.get('change_1y_pct'), 2)}%" if m.get("change_1y_pct") is not None else "-"),
        (METRIC_LABELS["revenue"], fmt_money(m.get("revenue"), ccy)),
        (METRIC_LABELS["net_income"], fmt_money(m.get("net_income"), ccy)),
        (METRIC_LABELS["eps"], fmt_num(m.get("eps"), 2)),
        (METRIC_LABELS["roe_pct"], f"{fmt_num(m.get('roe_pct'), 2)}%" if m.get("roe_pct") is not None else "-"),
        (METRIC_LABELS["pb"], fmt_num(m.get("pb"), 2)),
    ]


def print_report(items: list[dict]) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("=" * 96)
    print("KAP-FIRST FUNDAMENTALS RAPORU")
    print("=" * 96)
    print(f"Rapor Zamani: {now}")
    print("Not: Kimlik/KAP bilgileri KAP'tan, eksik metrikler TradingView scan ile tamamlanir.")
    print()

    for item in items:
        symbol = item.get("symbol", "?")
        src = item.get("source", "?")
        note = item.get("pipelineNote", "")
        print("-" * 96)
        print(f"{symbol}  |  Kaynak: {src}  |  {note}")
        company = item.get("company") or {}
        if company:
            print(f"Sirket: {company.get('title', '-') }  |  OID: {company.get('oid', '-')}  |  Kod: {company.get('code', '-')}" )
        source_breakdown = item.get("sourceBreakdown") or {}
        if source_breakdown:
            print(f"Kaynak Dagilimi: {source_breakdown.get('identity')} + {source_breakdown.get('metrics_fallback')}")
        print("-" * 96)

        rows = build_rows(item)
        key_width = max(len(k) for k, _ in rows)
        for k, v in rows:
            print(f"{k:<{key_width}} : {v}")
        prov = item.get("provenance") or []
        ok_non_empty = [p for p in prov if p.get("state") == "ok" and (p.get("size") or 0) > 0]
        err_http = [p for p in prov if p.get("state") == "err" and p.get("http")]
        print(f"Provenance: {len(ok_non_empty)} non-empty / {len(prov)} toplam endpoint denemesi")
        if ok_non_empty:
            for p in ok_non_empty[:5]:
                print(f"  - {p.get('path')} (size={p.get('size')})")
        if err_http:
            top_err = err_http[:5]
            print("HTTP Hatalari:")
            for p in top_err:
                method = p.get("method") or "GET"
                print(f"  - {method} {p.get('path')} -> {p.get('http')}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="KAP-first fundamentals pipeline")
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=["THYAO", "ASELS", "ARCLK"],
        help="Semboller (orn: THYAO ASELS ARCLK)",
    )
    parser.add_argument("--json", action="store_true", help="JSON cikti ver")
    args = parser.parse_args()

    symbols = [normalize_symbol(s) for s in args.symbols]
    items = run_kap_only(symbols)

    if args.json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return

    print_report(items)


if __name__ == "__main__":
    main()
