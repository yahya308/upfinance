# upfinance

Bu repo şu an tek çalışan akış üzerine sadeleştirilmiştir.

## Çalışan Mimari

- UI: `web/index.html` (React CDN, tek dosya)
- Backend/API: `web/server.py`
- Fundamentals çekici: `fundamentals_investing_fetcher.py`
- Yardımcı test scripti: `tools/yahoo_delay_checker.py`

## Çalıştırma

```powershell
python .\web\server.py
```

Tarayıcı:

- http://localhost:5500

## Ekip Notu (AI için)

- `web/server.py` içindeki endpoint isimleri sabit kabul edilmeli.
- `web/index.html` içinde `CHART_PERIODS` chart davranışının ana konfigürasyonudur.
- Fundamentals akışında importer dosyası adı: `fundamentals_investing_fetcher.py`.