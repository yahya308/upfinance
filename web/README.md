# BIST Hisse İzleme (React CDN + Python API)

Bu uygulama `npm` gerektirmez ve mevcut üretim akışı aşağıdaki iki dosya üzerindedir:

- `web/index.html` → arayüz
- `web/server.py` → API/proxy + fundamentals refresh

## Çalıştırma

```powershell
python .\web\server.py
```

Ardından:

- http://localhost:5500

## Güncel Özellikler

- BIST sembol keşfi + yedek sembol listesi
- 5 saniye fiyat güncelleme (delayed)
- Favori/yıldızlama (localStorage)
- Detay sayfasında fundamentals kartları
- Günlük/Haftalık/Aylık/Yıllık/Tüm Zamanlar grafik
- Grafik hover tooltip + crosshair

## Veri Kaynakları

- Fiyat/Chart: Yahoo Finance (`/api/chart`, `/api/prices`)
- Fundamentals: Investing çekimi + TradingView fallback (backend tarafında)

## Seans Override (opsiyonel)

`web/session_overrides.json` dosyası oluşturup özel gün/seans tanımlayabilirsin.

```json
{
  "2026-03-19": { "open": "10:00", "close": "13:00", "reason": "half_day", "label": "Arefe" },
  "2026-03-20": { "closed": true, "reason": "manual_closed", "label": "Tam gün kapalı" }
}
```
