"""
EPIC 7 — Sistem Kuralları ve Kısıtlar
BIST işlem kurallarını doğrulayan kural motoru.
"""

# ── Sabitler ──────────────────────────────────────────────────────────────────
COMMISSION_RATE = 0.0002          # %0.02 komisyon oranı
MAX_ORDER_RATIO = 0.25            # Tek emirde bakiyenin en fazla %25'i
SHORT_SELL_MARGIN_RATIO = 0.50    # Açığa satışta %50 teminat zorunluluğu


# ── Yardımcı ──────────────────────────────────────────────────────────────────
def calculate_commission(total_cost: float) -> float:
    """İşlem tutarı üzerinden komisyon hesaplar."""
    return round(abs(total_cost) * COMMISSION_RATE, 4)


# ── Kural Fonksiyonları ──────────────────────────────────────────────────────

def check_floor_ceiling(side: str, price: float,
                        floor_price: float, ceiling_price: float,
                        symbol: str = "") -> list[str]:
    """
    Taban/Tavan fiyat kontrolü.
    - Hisse taban fiyattaysa satış yapılamaz.
    - Hisse tavan fiyattaysa alım yapılamaz.
    """
    errors: list[str] = []
    label = f"'{symbol}' " if symbol else ""

    if side == "sell" and price is not None and floor_price is not None:
        if price <= floor_price:
            errors.append(
                f"{label}taban fiyatta olduğu için satılamaz."
            )

    if side == "buy" and price is not None and ceiling_price is not None:
        if price >= ceiling_price:
            errors.append(
                f"{label}tavan fiyatta olduğu için alınamaz."
            )

    return errors


def check_short_sell(side: str, quantity: int, holdings: int,
                     cash_balance: float, price: float,
                     symbol: str = "") -> list[str]:
    """
    Açığa satış (short selling) kontrolü.
    Elinde yeterli hisse yoksa satışa izin verilir ancak
    (price × quantity × %50) kadar teminat bakiyede olmalıdır.
    """
    errors: list[str] = []

    if side != "sell":
        return errors

    # Elindeki hisse yeterliyse normal satış — teminat kontrolü yok
    if holdings >= quantity:
        return errors

    # Açığa satış durumu: teminat kontrolü
    short_quantity = quantity - holdings
    required_margin = round(price * short_quantity * SHORT_SELL_MARGIN_RATIO, 2)

    if cash_balance < required_margin:
        errors.append(
            f"Açığa satış teminatı yetersiz. "
            f"Gereken: {required_margin:.2f} TL, Mevcut: {cash_balance:.2f} TL."
        )

    return errors


def check_order_limit(total_cost: float, cash_balance: float) -> list[str]:
    """
    Tek bir emirde toplam bakiyenin %25'inden fazlası kullanılamaz.
    """
    errors: list[str] = []
    max_allowed = round(cash_balance * MAX_ORDER_RATIO, 2)

    if total_cost > max_allowed:
        errors.append(
            f"Tek bir emir, toplam bakiyenin %25'ini aşamaz. "
            f"Maksimum: {max_allowed:.2f} TL."
        )

    return errors


def check_commission(total_cost: float, commission: float,
                     cash_balance: float) -> list[str]:
    """
    Komisyon dahil toplam tutar için bakiye yeterliliği kontrolü.
    """
    errors: list[str] = []
    needed = round(total_cost + commission, 2)

    if needed > cash_balance:
        errors.append(
            f"Bakiye yetersiz (komisyon dahil). "
            f"Gereken: {needed:.2f} TL, Mevcut: {cash_balance:.2f} TL."
        )

    return errors


# ── Ana Doğrulama ─────────────────────────────────────────────────────────────

def validate_order(
    side: str,
    symbol: str,
    quantity: int,
    price: float,
    floor_price: float,
    ceiling_price: float,
    cash_balance: float,
    holdings: int = 0,
) -> dict:
    """
    Tüm BIST kurallarını sırasıyla uygular.

    Parametreler
    ----------
    side          : "buy" veya "sell"
    symbol        : Hisse sembolü (ör. "THYAO.IS")
    quantity      : Adet
    price         : Güncel / emir fiyatı (TL)
    floor_price   : Taban fiyat (TL)
    ceiling_price : Tavan fiyat (TL)
    cash_balance  : Nakit bakiye (TL)
    holdings      : Eldeki hisse adedi (varsayılan 0)

    Dönüş
    ------
    {
        "valid":      bool,
        "errors":     [str, ...],
        "commission": float,
        "total_cost": float,
    }
    """
    side = side.lower().strip()
    errors: list[str] = []
    total_cost = round(price * quantity, 2)
    commission = calculate_commission(total_cost)

    # 1) Taban / Tavan kontrolü
    errors.extend(
        check_floor_ceiling(side, price, floor_price, ceiling_price, symbol)
    )

    # 2) Açığa satış kontrolü
    errors.extend(
        check_short_sell(side, quantity, holdings, cash_balance, price, symbol)
    )

    # 3) Alım emirlerinde bakiye bazlı kontroller
    if side == "buy":
        # 3a) Tek emir limiti (%25)
        errors.extend(check_order_limit(total_cost, cash_balance))

        # 3b) Komisyon dahil bakiye yeterliliği
        errors.extend(check_commission(total_cost, commission, cash_balance))

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "commission": commission,
        "total_cost": total_cost,
    }
