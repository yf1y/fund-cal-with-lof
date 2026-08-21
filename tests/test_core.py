import unittest

from backend.market_data import MarketDataClient, FxQuote, NavQuote, PriceQuote, normalize_symbol
from backend.store import FundStore
from backend.valuation import ValuationService


class FakeMarket:
    async def latest_nav(self, _code):
        return NavQuote(1.0, "2026-08-20", "test-nav", True)

    async def current_market_price(self, symbol):
        return PriceQuote(symbol, 1.10, "2026-08-21", None, None, "CNY", "test-market", "direct_realtime", True)

    async def price_quote(self, symbol, base_date):
        return PriceQuote(symbol, 110.0, "2026-08-21", 100.0, base_date, "USD", "test-quote", "direct_realtime", True)

    async def fx_quote(self, currency, base_date):
        return FxQuote(currency, 7.0, 7.0, "2026-08-21", base_date, "test-fx", "intraday", True)


class CoreTests(unittest.IsolatedAsyncioTestCase):
    def test_normalizes_yahoo_shanghai_suffix(self):
        self.assertEqual(normalize_symbol("688052.SS"), "688052.SH")

    def test_preserves_market_quote_timestamp(self):
        current, previous, timestamp = MarketDataClient._parse_tencent_line(
            'v_usAAPL="51~Apple~AAPL~230.00~228.00~x~20260822091530~";'
        )
        self.assertEqual(current, 230.0)
        self.assertEqual(previous, 228.0)
        self.assertEqual(timestamp, "2026-08-22 09:15:30")

    async def test_industry_premium_formula_and_proxy_label(self):
        service = ValuationService(FundStore(), FakeMarket())
        result = await service.estimate_fund(
            {
                "fund_code": "160719",
                "fund_name": "测试 LOF",
                "is_lof": True,
                "is_qdii": True,
                "holdings": [{"name": "瑞士黄金", "price_symbol": "AUUSI.SW", "w": 50}],
            }
        )
        self.assertEqual(result["estimated_nav"], 1.05)
        self.assertAlmostEqual(result["premium_rate"], 4.7619, places=4)
        self.assertEqual(result["premium_formula"], "(场内价格 / 实时估值 - 1) × 100%")
        self.assertEqual(result["details"][0]["quality"], "proxy")
        self.assertEqual(result["details"][0]["quote_symbol"], "GLD")
        self.assertEqual(result["fx_updated_at"], "2026-08-21")
        self.assertEqual(result["details"][0]["contribution_pct"], 5.0)


if __name__ == "__main__":
    unittest.main()
