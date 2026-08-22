from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from io import StringIO
from typing import Any

import akshare as ak
import pandas as pd

from .cache import LocalCache
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
    def __init__(
        self,
        store: FundStore,
        market: MarketDataClient,
        cache: LocalCache | None = None,
    ) -> None:
        self.store = store
        self.market = market
        self.cache = cache or LocalCache()
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
        fx_update_times: list[str] = []
        fx_currencies: set[str] = set()
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
            if quote.currency != "CNY" and fx.latest_date:
                fx_update_times.append(str(fx.latest_date))
                fx_currencies.add(quote.currency)
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
                    "fx_rate": fx.latest,
                    "fx_updated_at": fx.latest_date,
                    "holding_return_pct": round(holding_return * 100, 4) if holding_return is not None else None,
                    "contribution_pct": round(item["weight"] * holding_return, 4) if holding_return is not None else None,
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
        warnings = self._warnings(status, recorded_weight, priced_weight, qualities)
        period_match = re.search(r"(20\d{2})", str(fund.get("report_period") or ""))
        if period_match and int(period_match.group(1)) < datetime.now().year - 1:
            warnings.append(
                f"最新可用持仓披露期为 {fund.get('report_period')}，可能已明显偏离当前组合。"
            )
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
            "fx_updated_at": max(fx_update_times) if fx_update_times else None,
            "fx_currencies": sorted(fx_currencies),
            "update_time": datetime.now().isoformat(timespec="seconds"),
            "details": details,
            "warnings": warnings,
            "holdings_cache": fund.get("_cache_status", "curated"),
        }

    @staticmethod
    def _warnings(status: str, recorded: float, priced: float, qualities: list[str]) -> list[str]:
        warnings = ["估值基于最新披露持仓；未披露仓位与调仓会造成偏差。"]
        if status == "partial":
            warnings.append(f"已记录持仓中仅 {priced:.2f}/{recorded:.2f}% 权重成功取价。")
        if "proxy" in qualities:
            warnings.append("部分持仓无法稳定取得原标的价格，已使用相关 ETF 或指数近似；明细中列出了替代标的和原因。")
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
            cached_names = self.cache.get("fund-names:v1")
            if isinstance(cached_names, dict):
                self._fund_names.update(
                    {str(code).zfill(6): str(name) for code, name in cached_names.items()}
                )
                return
            try:
                frame = await asyncio.to_thread(ak.fund_name_em)
                code_column = next((col for col in frame.columns if "代码" in str(col)), None)
                name_column = next((col for col in frame.columns if "简称" in str(col) or "名称" in str(col)), None)
                if code_column is not None and name_column is not None:
                    for _, row in frame.iterrows():
                        self._fund_names[str(row[code_column]).zfill(6)] = str(row[name_column])
                    self.cache.set("fund-names:v1", self._fund_names, 7 * 24 * 60 * 60)
            except Exception:
                pass

    async def resolve_query(self, query: str) -> dict[str, Any] | None:
        query = query.strip()
        curated = self.store.find_curated(query)
        if curated:
            return curated
        if query.isdigit() and len(query) == 6:
            cached = self.cache.get(f"portfolio:v2:{query}")
            if isinstance(cached, dict):
                cached["_cache_status"] = "hit"
                self._portfolio_cache[query] = cached
                return cached
            nav = await self.market.latest_nav(query)
            if not nav.ok and not nav.fund_name:
                return None
            return await self._load_public_fund(query, nav.fund_name or query)

        await self._load_fund_names()
        exact = [code for code, name in self._fund_names.items() if name == query]
        if len(exact) != 1:
            return None
        code = exact[0]
        return await self._load_public_fund(code, self._fund_names.get(code, "未知基金"))

    @staticmethod
    def _looks_like_lof_code(code: str) -> bool:
        return code.startswith("16") or code.startswith(("501", "502", "506"))

    @staticmethod
    def _holding_symbol(value: Any) -> str:
        raw = str(value or "").strip().upper().replace(" ", "")
        if raw.endswith(".0") and raw[:-2].isdigit():
            raw = raw[:-2]
        if not raw or raw in {"NAN", "NONE", "--"}:
            return ""
        if raw.endswith((".SH", ".SZ", ".HK", ".T", ".SW", ".L")):
            return normalize_symbol(raw)
        if raw.isdigit() and len(raw) == 6:
            suffix = ".SH" if raw.startswith(("5", "6", "9")) else ".SZ"
            return raw + suffix
        if raw.isdigit() and len(raw) == 5:
            return raw.zfill(5) + ".HK"
        if raw.isdigit() and len(raw) == 4:
            return raw + ".T"
        return raw

    @staticmethod
    def _parse_holdings_document(document: str) -> tuple[list[dict[str, Any]], str | None]:
        match = re.search(r'content:(".*"),arryear:', document, flags=re.S)
        if not match:
            return [], None
        try:
            html = json.loads(match.group(1))
        except json.JSONDecodeError:
            return [], None
        if not html.strip():
            return [], None
        quarter_labels = re.findall(r"(\d{4}年[一二三四1-4]季度)", html)
        try:
            frames = pd.read_html(StringIO(html))
        except (ValueError, ImportError):
            return [], None

        for index, frame in enumerate(frames):
            frame.columns = [
                " ".join(str(part) for part in column if str(part) != "nan")
                if isinstance(column, tuple)
                else str(column)
                for column in frame.columns
            ]
            code_column = next((col for col in frame.columns if "股票代码" in col), None)
            name_column = next((col for col in frame.columns if "股票名称" in col), None)
            weight_column = next((col for col in frame.columns if "占净值比例" in col), None)
            if code_column is None or weight_column is None:
                continue
            holdings: list[dict[str, Any]] = []
            for _, row in frame.iterrows():
                symbol = ValuationService._holding_symbol(row[code_column])
                weight = _number(str(row[weight_column]).replace("%", ""))
                if not symbol or weight is None or weight <= 0:
                    continue
                name_value = row[name_column] if name_column is not None else symbol
                name = str(name_value) if str(name_value).lower() != "nan" else symbol
                holdings.append(
                    {
                        "name": name,
                        "price_symbol": symbol,
                        "w": weight,
                        "type": "STOCK",
                    }
                )
            if holdings:
                period = quarter_labels[index] if index < len(quarter_labels) else None
                return holdings, period
        return [], None

    async def _load_public_fund(self, code: str, name: str) -> dict[str, Any]:
        cached = self._portfolio_cache.get(code)
        if cached:
            return cached
        persisted = self.cache.get(f"portfolio:v2:{code}")
        if isinstance(persisted, dict):
            persisted["_cache_status"] = "hit"
            self._portfolio_cache[code] = persisted
            return persisted

        document = await self.market.fund_holdings_document(code)
        holdings, report_period = await asyncio.to_thread(
            self._parse_holdings_document, document
        )
        fund = {
            "fund_code": code,
            "fund_name": name,
            "is_qdii": "QDII" in name.upper(),
            "is_lof": self._looks_like_lof_code(code),
            "report_period": report_period,
            "holdings": holdings,
            "_cache_status": "stored",
        }
        self._portfolio_cache[code] = fund
        persisted_fund = {key: value for key, value in fund.items() if key != "_cache_status"}
        self.cache.set(
            f"portfolio:v2:{code}",
            persisted_fund,
            7 * 24 * 60 * 60 if holdings else 6 * 60 * 60,
        )
        return fund
