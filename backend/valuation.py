from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import akshare as ak

from .market_data import (
    MarketDataClient,
    exchange_symbol_for_fund,
    normalize_symbol,
    symbol_currency,
)
from .store import FundStore


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if result == result else None
    except (TypeError, ValueError):
        return None


class ValuationService:
    def __init__(self, store: FundStore, market: MarketDataClient) -> None:
        self.store = store
        self.market = market
        self._fund_names: dict[str, str] = {}
        self._name_lock = asyncio.Lock()
        self._portfolio_cache: dict[str, dict[str, Any]] = {}
        self._dashboard_cache: tuple[float, list[dict[str, Any]]] | None = None

    async def dashboard(self) -> list[dict[str, Any]]:
        now = datetime.now().timestamp()
        if self._dashboard_cache and self._dashboard_cache[0] > now:
            return self._dashboard_cache[1]
        funds = self.store.list_dashboard_funds()
        navs = await asyncio.gather(
            *(self.market.latest_nav(str(fund.get("fund_code", ""))) for fund in funds)
        )
        spot_symbols: list[str] = []
        fx_keys: set[tuple[str, str | None]] = set()
        for fund, nav in zip(funds, navs):
            spot_symbols.append(exchange_symbol_for_fund(str(fund.get("fund_code", ""))))
            for holding in fund.get("holdings") or []:
                raw_symbol = normalize_symbol(
                    holding.get("price_symbol") or holding.get("reported_code") or ""
                )
                mapping = self.store.proxy_mapping(raw_symbol)
                quote_symbol = mapping["proxy_symbol"] if mapping else raw_symbol
                spot_symbols.append(quote_symbol)
                fx_keys.add((symbol_currency(quote_symbol), nav.nav_date))
        await self.market.current_prices([symbol for symbol in spot_symbols if symbol])
        await asyncio.gather(
            *(self.market.fx_quote(currency, base_date) for currency, base_date in fx_keys if currency != "UNKNOWN")
        )
        rows = await asyncio.gather(
            *(self.estimate_fund(fund) for fund in funds)
        )
        rows = sorted(
            rows,
            key=lambda row: row.get("premium_rate")
            if row.get("premium_rate") is not None
            else -10_000,
            reverse=True,
        )
        self._dashboard_cache = (datetime.now().timestamp() + 60, rows)
        return rows

    async def estimate_fund(self, fund: dict[str, Any]) -> dict[str, Any]:
        code = str(fund.get("fund_code", "")).zfill(6)
        name = str(fund.get("fund_name") or "未知基金")
        holdings = fund.get("holdings") or []
        is_lof = bool(fund.get("is_lof"))
        nav = await self.market.latest_nav(code)
        market_quote = (
            await self.market.current_market_price(exchange_symbol_for_fund(code))
            if is_lof
            else None
        )
        base_date = nav.nav_date
        prepared: list[dict[str, Any]] = []
        quote_tasks = []
        for holding in holdings:
            weight = _number(holding.get("w") if isinstance(holding, dict) else None) or 0.0
            if weight <= 0:
                continue
            raw_symbol = normalize_symbol(
                holding.get("price_symbol")
                or holding.get("reported_code")
                or holding.get("symbol")
                or ""
            )
            mapping = self.store.proxy_mapping(raw_symbol)
            quote_symbol = mapping["proxy_symbol"] if mapping else raw_symbol
            prepared.append(
                {
                    "name": holding.get("name") or raw_symbol,
                    "symbol": raw_symbol,
                    "quote_symbol": quote_symbol,
                    "weight": weight,
                    "mapping": mapping,
                }
            )
            quote_tasks.append(self.market.price_quote(quote_symbol, base_date))

        quotes = await asyncio.gather(*quote_tasks) if quote_tasks else []
        fx_tasks = [self.market.fx_quote(quote.currency, quote.base_date or base_date) for quote in quotes]
        fx_quotes = await asyncio.gather(*fx_tasks) if fx_tasks else []

        contribution = 0.0
        recorded_weight = sum(item["weight"] for item in prepared)
        priced_weight = 0.0
        details: list[dict[str, Any]] = []
        for item, quote, fx in zip(prepared, quotes, fx_quotes):
            usable = (
                quote.ok
                and fx.ok
                and quote.latest is not None
                and quote.base_close not in (None, 0)
                and fx.latest is not None
                and fx.base not in (None, 0)
            )
            holding_return = None
            if usable:
                holding_return = (quote.latest / quote.base_close) * (fx.latest / fx.base) - 1
                contribution += item["weight"] / 100 * holding_return
                priced_weight += item["weight"]
            quality = "proxy" if item["mapping"] and usable else quote.quality
            details.append(
                {
                    "name": item["name"],
                    "symbol": item["symbol"],
                    "quote_symbol": item["quote_symbol"],
                    "weight": round(item["weight"], 4),
                    "quality": quality,
                    "source": quote.source,
                    "proxy_reason": item["mapping"]["reason"] if item["mapping"] else None,
                    "latest": quote.latest,
                    "latest_date": quote.latest_date,
                    "base_close": quote.base_close,
                    "base_date": quote.base_date,
                    "currency": quote.currency,
                    "fx_source": fx.source,
                    "holding_return_pct": round(holding_return * 100, 4) if holding_return is not None else None,
                    "message": "ok" if usable else quote.message or fx.message,
                }
            )

        estimated_nav = nav.value * (1 + contribution) if nav.ok and nav.value is not None else None
        premium_rate = None
        if market_quote and market_quote.latest not in (None, 0) and estimated_nav not in (None, 0):
            premium_rate = (market_quote.latest / estimated_nav - 1) * 100
        recorded_coverage = min(recorded_weight, 100.0)
        priced_coverage = min(priced_weight, 100.0)
        status = "complete" if recorded_weight > 0 and abs(priced_weight - recorded_weight) < 0.01 else "partial"
        if not nav.ok or (is_lof and (not market_quote or not market_quote.ok)):
            status = "error"
        qualities = sorted({item["quality"] for item in details if item["quality"]})
        return {
            "fund_code": code,
            "fund_name": name,
            "is_qdii": bool(fund.get("is_qdii")),
            "is_lof": is_lof,
            "report_period": fund.get("report_period"),
            "estimated_nav": round(estimated_nav, 4) if estimated_nav is not None else None,
            "estimated_change_pct": round(contribution * 100, 4) if nav.ok else None,
            "base_nav": nav.value,
            "base_nav_date": nav.nav_date,
            "market_price": market_quote.latest if market_quote else None,
            "market_price_date": market_quote.latest_date if market_quote else None,
            "premium_rate": round(premium_rate, 4) if premium_rate is not None else None,
            "premium_formula": "(场内价格 / 实时估值 - 1) × 100%",
            "recorded_weight": round(recorded_coverage, 2),
            "priced_weight": round(priced_coverage, 2),
            "unknown_weight": round(max(0.0, 100.0 - recorded_coverage), 2),
            "coverage_rate": round(priced_weight / recorded_weight * 100, 2) if recorded_weight else 0.0,
            "status": status,
            "quote_qualities": qualities,
            "market_source": market_quote.source if market_quote else None,
            "nav_source": nav.source,
            "update_time": datetime.now().isoformat(timespec="seconds"),
            "details": details,
            "warnings": self._warnings(status, recorded_weight, priced_weight, qualities),
        }

    @staticmethod
    def _warnings(status: str, recorded: float, priced: float, qualities: list[str]) -> list[str]:
        warnings = ["估值基于最新披露持仓；未披露仓位与调仓会造成偏差。"]
        if status == "partial":
            warnings.append(f"已记录持仓中仅 {priced:.2f}/{recorded:.2f}% 权重成功取价。")
        if "proxy" in qualities:
            warnings.append("部分持仓使用相关 ETF/指数代理，详情中已逐项标注。")
        if "eod" in qualities:
            warnings.append("部分持仓仅有收盘行情。")
        return warnings

    async def _load_fund_names(self) -> None:
        if self._fund_names:
            return
        async with self._name_lock:
            if self._fund_names:
                return
            for item in self.store.list_dashboard_funds():
                self._fund_names[str(item["fund_code"]).zfill(6)] = item["fund_name"]
            try:
                frame = await asyncio.to_thread(ak.fund_name_em)
                code_column = next((col for col in frame.columns if "代码" in str(col)), None)
                name_column = next((col for col in frame.columns if "简称" in str(col) or "名称" in str(col)), None)
                if code_column is not None and name_column is not None:
                    for _, row in frame.iterrows():
                        self._fund_names[str(row[code_column]).zfill(6)] = str(row[name_column])
            except Exception:
                pass

    async def resolve_query(self, query: str) -> dict[str, Any] | None:
        query = query.strip()
        curated = self.store.find_curated(query)
        if curated:
            return curated
        await self._load_fund_names()
        if query.isdigit() and len(query) == 6 and query in self._fund_names:
            code = query
        else:
            exact = [code for code, name in self._fund_names.items() if name == query]
            if len(exact) != 1:
                return None
            code = exact[0]
        return await self._load_public_fund(code, self._fund_names.get(code, "未知基金"))

    async def _load_public_fund(self, code: str, name: str) -> dict[str, Any]:
        cached = self._portfolio_cache.get(code)
        if cached:
            return cached

        def load() -> tuple[list[dict[str, Any]], str | None]:
            current_year = datetime.now().year
            frame = None
            for year in range(current_year, current_year - 3, -1):
                try:
                    candidate = ak.fund_portfolio_hold_em(symbol=code, date=str(year))
                    if candidate is not None and not candidate.empty:
                        frame = candidate
                        break
                except Exception:
                    continue
            if frame is None or frame.empty:
                return [], None
            quarter_column = next((col for col in frame.columns if "季度" in str(col)), None)
            if quarter_column is not None:
                latest_quarter = frame[quarter_column].astype(str).max()
                frame = frame[frame[quarter_column].astype(str) == latest_quarter]
            code_column = next((col for col in frame.columns if "股票代码" in str(col)), None)
            name_column = next((col for col in frame.columns if "股票名称" in str(col)), None)
            weight_column = next((col for col in frame.columns if "占净值比例" in str(col)), None)
            if code_column is None or weight_column is None:
                return [], None
            result = []
            for _, row in frame.iterrows():
                stock_code = str(row[code_column]).split(".")[0].zfill(6)
                if not stock_code.isdigit() or len(stock_code) != 6:
                    continue
                suffix = ".SH" if stock_code.startswith(("6", "9")) else ".SZ"
                weight = _number(row[weight_column])
                if weight is None or weight <= 0:
                    continue
                result.append(
                    {
                        "name": str(row[name_column]) if name_column is not None else stock_code,
                        "price_symbol": stock_code + suffix,
                        "w": weight,
                        "type": "STOCK",
                    }
                )
            return result, latest_quarter if quarter_column is not None else None

        holdings, report_period = await asyncio.to_thread(load)
        fund = {
            "fund_code": code,
            "fund_name": name,
            "is_qdii": False,
            "is_lof": False,
            "report_period": report_period,
            "holdings": holdings,
        }
        self._portfolio_cache[code] = fund
        return fund
