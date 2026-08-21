from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import httpx


EASTMONEY_UT = "b2884a393a59ad64002292a3e90d46a5"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"


def as_float(value: Any) -> float | None:
    try:
        number = float(value)
        return number if number == number else None
    except (TypeError, ValueError):
        return None


def normalize_symbol(symbol: str) -> str:
    value = str(symbol or "").strip().upper()
    if value.endswith(".SS"):
        value = value[:-3] + ".SH"
    return value


def symbol_currency(symbol: str) -> str:
    symbol = normalize_symbol(symbol)
    if symbol.endswith((".SH", ".SZ")) or symbol.startswith("CNBOND:"):
        return "CNY"
    if symbol.endswith(".HK"):
        return "HKD"
    if symbol.endswith(".T"):
        return "JPY"
    if symbol.endswith(".SW"):
        return "CHF"
    if symbol.endswith((".L",)):
        return "UNKNOWN"
    if symbol and "." not in symbol and not symbol.startswith("ISIN:"):
        return "USD"
    return "UNKNOWN"


def exchange_symbol_for_fund(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith(("50", "51", "52", "56", "58")):
        return f"{code}.SH"
    return f"{code}.SZ"


@dataclass
class NavQuote:
    value: float | None
    nav_date: str | None
    source: str
    ok: bool
    message: str = ""


@dataclass
class PriceQuote:
    symbol: str
    latest: float | None
    latest_date: str | None
    base_close: float | None
    base_date: str | None
    currency: str
    source: str
    quality: str
    ok: bool
    message: str = ""


@dataclass
class FxQuote:
    currency: str
    latest: float | None
    base: float | None
    latest_date: str | None
    base_date: str | None
    source: str
    quality: str
    ok: bool
    message: str = ""


class MarketDataClient:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=12.0
        )
        self._cache: dict[str, tuple[float, Any]] = {}
        self._semaphore = asyncio.Semaphore(10)

    async def close(self) -> None:
        await self.client.aclose()

    def _cache_get(self, key: str) -> Any | None:
        item = self._cache.get(key)
        if item and item[0] > time.time():
            return item[1]
        if item:
            self._cache.pop(key, None)
        return None

    def _cache_set(self, key: str, value: Any, ttl: int) -> Any:
        self._cache[key] = (time.time() + ttl, value)
        return value

    async def latest_nav(self, fund_code: str) -> NavQuote:
        fund_code = str(fund_code).zfill(6)
        key = f"nav:{fund_code}"
        cached = self._cache_get(key)
        if cached:
            return cached
        try:
            async with self._semaphore:
                response = await self.client.get(
                    f"https://fund.eastmoney.com/pingzhongdata/{fund_code}.js",
                    params={"v": int(time.time() * 1000)},
                )
                response.raise_for_status()
            match = re.search(
                r"Data_netWorthTrend\s*=\s*(\[.*?\]);", response.text, flags=re.S
            )
            if not match:
                raise ValueError("未找到净值序列")
            values = json.loads(match.group(1))
            latest = values[-1]
            nav = as_float(latest.get("y"))
            timestamp = latest.get("x")
            nav_date = datetime.fromtimestamp(timestamp / 1000).strftime("%Y-%m-%d")
            result = NavQuote(nav, nav_date, "东方财富基金净值", nav is not None, "ok")
        except Exception as exc:
            result = NavQuote(None, None, "东方财富基金净值", False, str(exc))
        return self._cache_set(key, result, 1800 if result.ok else 60)

    @staticmethod
    def _tencent_code(symbol: str) -> str | None:
        symbol = normalize_symbol(symbol)
        if symbol.endswith(".SH"):
            return "sh" + symbol[:-3]
        if symbol.endswith(".SZ"):
            return "sz" + symbol[:-3]
        if symbol.endswith(".HK"):
            return "hk" + symbol[:-3].zfill(5)
        if symbol and "." not in symbol and symbol.replace("-", "").isalnum():
            return "us" + symbol.replace("-", ".")
        return None

    @staticmethod
    def _parse_tencent_line(line: str) -> tuple[float | None, float | None, str | None]:
        match = re.search(r'="(.*?)"', line)
        if not match:
            return None, None, None
        parts = match.group(1).split("~")
        current = as_float(parts[3]) if len(parts) > 3 else None
        previous = as_float(parts[4]) if len(parts) > 4 else None
        quote_date = None
        for item in parts:
            if re.fullmatch(r"\d{14}", item):
                quote_date = f"{item[:4]}-{item[4:6]}-{item[6:8]}"
                break
            if re.fullmatch(r"\d{4}[-/]\d{2}[-/]\d{2}.*", item):
                quote_date = item[:10].replace("/", "-")
                break
        return current, previous, quote_date

    async def current_prices(self, symbols: list[str]) -> dict[str, tuple[float, float | None, str | None]]:
        normalized = sorted({normalize_symbol(item) for item in symbols})
        result: dict[str, tuple[float, float | None, str | None]] = {}
        missing: list[tuple[str, str]] = []
        for symbol in normalized:
            cached = self._cache_get(f"spot:{symbol}")
            if cached:
                result[symbol] = cached
                continue
            code = self._tencent_code(symbol)
            if code:
                missing.append((symbol, code))
        for offset in range(0, len(missing), 50):
            chunk = missing[offset : offset + 50]
            try:
                async with self._semaphore:
                    response = await self.client.get(
                        "https://qt.gtimg.cn/q=" + ",".join(code for _, code in chunk),
                        headers={"Referer": "https://gu.qq.com/", "User-Agent": USER_AGENT},
                    )
                    response.raise_for_status()
                text = response.content.decode("gb18030", errors="ignore")
                stripped_lines = [line.strip() for line in text.split(";") if "=" in line]
                lines = {line.split("=", 1)[0]: line for line in stripped_lines}
                for symbol, code in chunk:
                    line = lines.get(f"v_{code}", "")
                    current, previous, quote_date = self._parse_tencent_line(line)
                    if current is not None:
                        value = (current, previous, quote_date)
                        result[symbol] = value
                        self._cache_set(f"spot:{symbol}", value, 300)
            except Exception:
                continue
        return result

    @staticmethod
    def _eastmoney_secid(symbol: str) -> str | None:
        symbol = normalize_symbol(symbol)
        if symbol.endswith(".SH"):
            return f"1.{symbol[:-3]}"
        if symbol.endswith(".SZ"):
            return f"0.{symbol[:-3]}"
        if symbol.endswith(".HK"):
            return f"116.{symbol[:-3].zfill(5)}"
        if symbol and "." not in symbol and symbol.replace("-", "").isalnum():
            return f"105.{symbol}"
        return None

    async def _history(self, symbol: str, base_date: str | None) -> list[dict[str, Any]]:
        symbol = normalize_symbol(symbol)
        key = f"history:{symbol}:{base_date}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        secid = self._eastmoney_secid(symbol)
        if not secid:
            return []
        try:
            base_dt = datetime.strptime(base_date, "%Y-%m-%d") if base_date else datetime.now() - timedelta(days=7)
            async with self._semaphore:
                response = await self.client.get(
                    "https://push2his.eastmoney.com/api/qt/stock/kline/get",
                    params={
                        "fields1": "f1,f2,f3,f4,f5,f6",
                        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                        "klt": "101",
                        "fqt": "1",
                        "secid": secid,
                        "beg": (base_dt - timedelta(days=14)).strftime("%Y%m%d"),
                        "end": (datetime.now() + timedelta(days=2)).strftime("%Y%m%d"),
                        "ut": EASTMONEY_UT,
                    },
                )
                response.raise_for_status()
            klines = ((response.json() or {}).get("data") or {}).get("klines") or []
            rows = []
            for line in klines:
                parts = str(line).split(",")
                close = as_float(parts[2] if len(parts) > 2 else None)
                if close is not None:
                    rows.append({"date": parts[0], "close": close})
            return self._cache_set(key, rows, 21600 if rows else 120)
        except Exception:
            return self._cache_set(key, [], 120)

    async def price_quote(self, symbol: str, base_date: str | None) -> PriceQuote:
        symbol = normalize_symbol(symbol)
        key = f"quote:{symbol}:{base_date}"
        cached = self._cache_get(key)
        if cached:
            return cached
        if symbol.startswith("CNBOND:"):
            result = PriceQuote(symbol, 1.0, date.today().isoformat(), 1.0, base_date, "CNY", "债券净价近似不变", "assumed_stable", True)
            return self._cache_set(key, result, 1800)

        spots = await self.current_prices([symbol])
        spot = spots.get(symbol)
        if spot and spot[1] not in (None, 0) and base_date and spot[2]:
            try:
                lag_days = (datetime.strptime(spot[2], "%Y-%m-%d") - datetime.strptime(base_date, "%Y-%m-%d")).days
            except ValueError:
                lag_days = 99
            if 0 < lag_days <= 7:
                result = PriceQuote(
                    symbol,
                    spot[0],
                    spot[2],
                    spot[1],
                    base_date,
                    symbol_currency(symbol),
                    "腾讯实时行情（基准价取前收）",
                    "direct_realtime",
                    True,
                    "ok",
                )
                return self._cache_set(key, result, 120)
        rows = await self._history(symbol, base_date)
        latest = spot[0] if spot else (rows[-1]["close"] if rows else None)
        latest_date = spot[2] if spot else (rows[-1]["date"] if rows else None)
        base_row = None
        if base_date:
            eligible = [row for row in rows if row["date"] <= base_date]
            base_row = eligible[-1] if eligible else None
        if base_row is None and spot and spot[1] is not None:
            base_row = {"date": base_date, "close": spot[1]}
        ok = latest is not None and base_row is not None and base_row["close"] not in (None, 0)
        quality = "direct_realtime" if spot else ("eod" if rows else "missing")
        result = PriceQuote(
            symbol,
            latest,
            latest_date,
            base_row["close"] if base_row else None,
            base_row["date"] if base_row else None,
            symbol_currency(symbol),
            "腾讯实时行情 + 东方财富历史行情" if spot else "东方财富收盘行情",
            quality,
            ok,
            "ok" if ok else "缺少当前价或净值基准日价格",
        )
        return self._cache_set(key, result, 120 if ok else 90)

    async def current_market_price(self, symbol: str) -> PriceQuote:
        symbol = normalize_symbol(symbol)
        spots = await self.current_prices([symbol])
        spot = spots.get(symbol)
        if spot:
            return PriceQuote(symbol, spot[0], spot[2], spot[1], None, symbol_currency(symbol), "腾讯实时行情", "direct_realtime", True)
        rows = await self._history(symbol, None)
        if rows:
            return PriceQuote(symbol, rows[-1]["close"], rows[-1]["date"], None, None, symbol_currency(symbol), "东方财富收盘行情", "eod", True)
        return PriceQuote(symbol, None, None, None, None, symbol_currency(symbol), "无", "missing", False, "场内价格获取失败")

    async def _current_fx_chinamoney(self, currency: str) -> tuple[float | None, str | None]:
        try:
            async with self._semaphore:
                response = await self.client.post(
                    "http://www.chinamoney.com.cn/r/cms/www/chinamoney/data/fx/rfx-sp-quot.json",
                    data={"t": str(int(time.time() * 1000))},
                    headers={"User-Agent": USER_AGENT},
                )
                response.raise_for_status()
            wanted = "100JPY/CNY" if currency == "JPY" else f"{currency}/CNY"
            for row in response.json().get("records", []):
                if row.get("ccyPair") == wanted:
                    bid, ask = as_float(row.get("bidPrc")), as_float(row.get("askPrc"))
                    if bid is not None and ask is not None:
                        rate = (bid + ask) / 2
                        return (rate / 100 if currency == "JPY" else rate), row.get("time")
        except Exception:
            pass
        return None, None

    async def _frankfurter_rate(self, currency: str, rate_date: str | None) -> tuple[float | None, str | None]:
        endpoint = rate_date or "latest"
        try:
            async with self._semaphore:
                response = await self.client.get(
                    f"https://api.frankfurter.app/{endpoint}", params={"from": currency, "to": "CNY"}
                )
                response.raise_for_status()
            payload = response.json()
            return as_float((payload.get("rates") or {}).get("CNY")), payload.get("date")
        except Exception:
            return None, None

    async def fx_quote(self, currency: str, base_date: str | None) -> FxQuote:
        currency = currency.upper()
        if currency == "CNY":
            return FxQuote("CNY", 1.0, 1.0, date.today().isoformat(), base_date, "人民币本币", "identity", True)
        key = f"fx:{currency}:{base_date}"
        cached = self._cache_get(key)
        if cached:
            return cached
        (latest, latest_time), (base, actual_base_date) = await asyncio.gather(
            self._current_fx_chinamoney(currency), self._frankfurter_rate(currency, base_date)
        )
        quality = "intraday" if latest is not None else "daily_reference"
        source = "中国外汇交易中心即期报价 + Frankfurter 历史参考汇率"
        if latest is None:
            latest, latest_time = await self._frankfurter_rate(currency, None)
            source = "Frankfurter 每日参考汇率"
        ok = latest not in (None, 0) and base not in (None, 0)
        result = FxQuote(currency, latest, base, latest_time or date.today().isoformat(), actual_base_date, source, quality, ok, "ok" if ok else "汇率获取失败")
        return self._cache_set(key, result, 60 if ok else 120)
