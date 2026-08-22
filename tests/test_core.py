import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.cache import LocalCache
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
    def test_sqlite_cache_survives_a_new_instance(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "cache.sqlite3"
            LocalCache(path).set("fund", {"code": "012870"}, 60)
            self.assertEqual(LocalCache(path).get("fund"), {"code": "012870"})

    def test_search_history_is_latest_first(self):
        with TemporaryDirectory() as directory:
            cache = LocalCache(Path(directory) / "history.sqlite3", database_url="")
            cache.record_search({"fund_code": "000001", "fund_name": "第一只"})
            cache.record_search({"fund_code": "000002", "fund_name": "第二只"})
            cache.record_search({"fund_code": "000001", "fund_name": "第一只"})
            rows = cache.list_search_history()
            self.assertEqual([item["fund_code"] for item in rows], ["000001", "000002"])

    def test_versioned_lof_catalog_and_holdings_are_separate(self):
        store = FundStore()
        health = store.health()
        self.assertGreaterEqual(health["catalog_count"], 400)
        self.assertGreaterEqual(health["holdings_count"], 21)
        self.assertEqual(len(store.list_dashboard_funds()), 21)

    def test_public_holding_symbol_markets(self):
        self.assertEqual(ValuationService._holding_symbol("600519"), "600519.SH")
        self.assertEqual(ValuationService._holding_symbol("00700"), "00700.HK")
        self.assertEqual(ValuationService._holding_symbol("AAPL"), "AAPL")

    def test_parses_latest_public_holdings_document(self):
        html = """
        <h4>2026年2季度股票投资明细</h4>
        <table><thead><tr><th>股票代码</th><th>股票名称</th><th>占净值比例</th></tr></thead>
        <tbody><tr><td>AAPL</td><td>苹果</td><td>9.99%</td></tr></tbody></table>
        """
        document = f"var apidata={{ content:{json.dumps(html, ensure_ascii=False)},arryear:[2026]}};"
        holdings, period = ValuationService._parse_holdings_document(document)
        self.assertEqual(period, "2026年2季度")
        self.assertEqual(holdings[0]["price_symbol"], "AAPL")
        self.assertEqual(holdings[0]["w"], 9.99)

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
        self.assertAlmostEqual(result["premium_rate"], -4.5455, places=4)
        self.assertEqual(result["premium_formula"], "(实时估值 - 场内价格) / 场内价格 × 100%")
        self.assertEqual(result["details"][0]["quality"], "proxy")
        self.assertEqual(result["details"][0]["quote_symbol"], "GLD")
        self.assertEqual(result["fx_updated_at"], "2026-08-21")
        self.assertEqual(result["details"][0]["contribution_pct"], 5.0)


if __name__ == "__main__":
    unittest.main()
