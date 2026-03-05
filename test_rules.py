"""
EPIC 7 — rules.py birim testleri
"""
import unittest
from rules import (
    COMMISSION_RATE,
    MAX_ORDER_RATIO,
    SHORT_SELL_MARGIN_RATIO,
    calculate_commission,
    check_floor_ceiling,
    check_short_sell,
    check_order_limit,
    check_commission,
    validate_order,
)


class TestCalculateCommission(unittest.TestCase):
    def test_positive_amount(self):
        self.assertAlmostEqual(calculate_commission(10_000), 10_000 * COMMISSION_RATE, places=4)

    def test_zero(self):
        self.assertEqual(calculate_commission(0), 0.0)

    def test_negative_uses_abs(self):
        self.assertAlmostEqual(calculate_commission(-5000), 5000 * COMMISSION_RATE, places=4)


class TestFloorCeiling(unittest.TestCase):
    # ── Taban fiyat kontrolleri ──
    def test_sell_at_floor_blocked(self):
        errs = check_floor_ceiling("sell", price=10.0, floor_price=10.0,
                                   ceiling_price=15.0, symbol="THYAO.IS")
        self.assertEqual(len(errs), 1)
        self.assertIn("taban", errs[0].lower())

    def test_sell_above_floor_ok(self):
        errs = check_floor_ceiling("sell", price=11.0, floor_price=10.0,
                                   ceiling_price=15.0)
        self.assertEqual(errs, [])

    # ── Tavan fiyat kontrolleri ──
    def test_buy_at_ceiling_blocked(self):
        errs = check_floor_ceiling("buy", price=15.0, floor_price=10.0,
                                   ceiling_price=15.0, symbol="ASELS.IS")
        self.assertEqual(len(errs), 1)
        self.assertIn("tavan", errs[0].lower())

    def test_buy_below_ceiling_ok(self):
        errs = check_floor_ceiling("buy", price=14.0, floor_price=10.0,
                                   ceiling_price=15.0)
        self.assertEqual(errs, [])

    # ── Farklı taraf ──
    def test_buy_at_floor_ok(self):
        """Taban fiyat sadece satışı engeller, alımı etkilemez."""
        errs = check_floor_ceiling("buy", price=10.0, floor_price=10.0,
                                   ceiling_price=15.0)
        self.assertEqual(errs, [])

    def test_sell_at_ceiling_ok(self):
        """Tavan fiyat sadece alımı engeller, satışı etkilemez."""
        errs = check_floor_ceiling("sell", price=15.0, floor_price=10.0,
                                   ceiling_price=15.0)
        self.assertEqual(errs, [])


class TestShortSell(unittest.TestCase):
    def test_normal_sell_with_holdings(self):
        """Eldeki hisse yeterliyse teminat kontrolü yapılmaz."""
        errs = check_short_sell("sell", quantity=50, holdings=100,
                                cash_balance=0, price=20.0)
        self.assertEqual(errs, [])

    def test_short_sell_sufficient_margin(self):
        """Yeterli teminat varsa açığa satışa izin verilir."""
        # 100 adet × 20 TL × %50 = 1000 TL teminat gerekli
        errs = check_short_sell("sell", quantity=100, holdings=0,
                                cash_balance=1500, price=20.0)
        self.assertEqual(errs, [])

    def test_short_sell_insufficient_margin(self):
        """Teminat yetersizse hata döner."""
        # 100 adet × 20 TL × %50 = 1000 TL gerekli, bakiye 500
        errs = check_short_sell("sell", quantity=100, holdings=0,
                                cash_balance=500, price=20.0)
        self.assertEqual(len(errs), 1)
        self.assertIn("teminat", errs[0].lower())

    def test_partial_short_sell(self):
        """Eldeki 30 hisse var ama 100 adet satmak istiyor → 70 adet açığa."""
        # 70 × 10 × 0.50 = 350 TL teminat gerekli
        errs = check_short_sell("sell", quantity=100, holdings=30,
                                cash_balance=400, price=10.0)
        self.assertEqual(errs, [])

    def test_partial_short_sell_insufficient(self):
        # 70 × 10 × 0.50 = 350 TL teminat gerekli, bakiye 200
        errs = check_short_sell("sell", quantity=100, holdings=30,
                                cash_balance=200, price=10.0)
        self.assertEqual(len(errs), 1)

    def test_buy_side_ignored(self):
        """Alım tarafında açığa satış kontrolü yapılmaz."""
        errs = check_short_sell("buy", quantity=100, holdings=0,
                                cash_balance=0, price=20.0)
        self.assertEqual(errs, [])


class TestOrderLimit(unittest.TestCase):
    def test_within_limit(self):
        # %25 of 100_000 = 25_000 → 20_000 emir OK
        errs = check_order_limit(total_cost=20_000, cash_balance=100_000)
        self.assertEqual(errs, [])

    def test_at_limit(self):
        errs = check_order_limit(total_cost=25_000, cash_balance=100_000)
        self.assertEqual(errs, [])

    def test_exceeds_limit(self):
        errs = check_order_limit(total_cost=30_000, cash_balance=100_000)
        self.assertEqual(len(errs), 1)
        self.assertIn("%25", errs[0])


class TestCommissionCheck(unittest.TestCase):
    def test_sufficient_balance(self):
        errs = check_commission(total_cost=1000, commission=0.20,
                                cash_balance=5000)
        self.assertEqual(errs, [])

    def test_insufficient_balance(self):
        errs = check_commission(total_cost=5000, commission=1.0,
                                cash_balance=5000)
        self.assertEqual(len(errs), 1)
        self.assertIn("komisyon", errs[0].lower())


class TestValidateOrder(unittest.TestCase):
    """validate_order entegrasyon testleri."""

    def _base(self, **overrides):
        defaults = dict(
            side="buy",
            symbol="THYAO.IS",
            quantity=10,
            price=50.0,
            floor_price=45.0,
            ceiling_price=55.0,
            cash_balance=100_000,
            holdings=0,
        )
        defaults.update(overrides)
        return validate_order(**defaults)

    def test_valid_buy(self):
        result = self._base()
        self.assertTrue(result["valid"])
        self.assertEqual(result["errors"], [])
        self.assertGreater(result["commission"], 0)
        self.assertEqual(result["total_cost"], 500.0)

    def test_buy_at_ceiling_rejected(self):
        result = self._base(price=55.0, ceiling_price=55.0)
        self.assertFalse(result["valid"])
        self.assertTrue(any("tavan" in e.lower() for e in result["errors"]))

    def test_sell_at_floor_rejected(self):
        result = self._base(side="sell", price=45.0, floor_price=45.0, holdings=10)
        self.assertFalse(result["valid"])
        self.assertTrue(any("taban" in e.lower() for e in result["errors"]))

    def test_buy_exceeds_25pct(self):
        # 10 × 50 = 500, bakiye 1000 → %25 = 250, 500 > 250
        result = self._base(cash_balance=1000)
        self.assertFalse(result["valid"])
        self.assertTrue(any("%25" in e for e in result["errors"]))

    def test_short_sell_margin_ok(self):
        # 10 × 50 × 0.50 = 250 TL teminat, bakiye 100_000
        result = self._base(side="sell", holdings=0)
        self.assertTrue(result["valid"])

    def test_short_sell_margin_fail(self):
        # 10 × 50 × 0.50 = 250 TL teminat, bakiye 100
        result = self._base(side="sell", holdings=0, cash_balance=100)
        self.assertFalse(result["valid"])
        self.assertTrue(any("teminat" in e.lower() for e in result["errors"]))

    def test_commission_calculated(self):
        result = self._base()
        expected_commission = round(500.0 * COMMISSION_RATE, 4)
        self.assertAlmostEqual(result["commission"], expected_commission, places=4)

    def test_multiple_errors(self):
        """Tavan fiyat + bakiye yetersiz → birden fazla hata dönmeli."""
        result = self._base(price=55.0, ceiling_price=55.0, cash_balance=10)
        self.assertFalse(result["valid"])
        self.assertGreaterEqual(len(result["errors"]), 2)


if __name__ == "__main__":
    unittest.main()
