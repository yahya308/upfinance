# BIST Hisse İzleme (React - CDN)

Bu uygulama React ile yazılmıştır ve `npm` gerektirmez.

## Çalıştırma

`Bist` klasöründe terminal açıp:

```powershell
python .\web\server.py
```

Ardından tarayıcıda aç:

- http://localhost:5500

## Özellikler

- BIST hisseleri listesi (Yahoo `.IS` sembolleri keşfi + yedek liste)
- Hisse kodu ile arama
- Yıldızlama / favori listeleme (localStorage)
- Ana sayfada favori hisseler
- Fiyat ve günlük değişim yüzdesi
- Her 10 saniyede otomatik güncelleme
- Fiyat artışında yeşil, düşüşte kırmızı blink efekti
- Mat siyah / mat gri modern arayüz

## Not

- Veri kaynağı: Yahoo delayed market data
- Uygulama, CORS sorununu aşmak için local proxy (`/api/*`) üzerinden Yahoo çağırır

## Seans Override (önerilir)

Beklenmedik kapanışlar, resmi duyuru ile tam gün tatil veya yarım gün seanslar için `web/session_overrides.json` dosyası oluşturabilirsin.

Örnek format:

```json
{
	"2026-03-19": { "open": "10:00", "close": "13:00", "reason": "half_day", "label": "Arefe" },
	"2026-03-20": { "closed": true, "reason": "manual_closed", "label": "Tam gün kapalı" }
}
```

- `closed: true` => o gün tamamen kapalı
- `open` / `close` => o güne özel seans saatleri
- `reason` ve `label` => UI tarafında açıklama için
