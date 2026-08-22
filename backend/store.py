from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CATALOG_FILE = PROJECT_ROOT / "data" / "lof_catalog.json"
HOLDINGS_FILE = PROJECT_ROOT / "data" / "lof_holdings.json"


DEFAULT_PROXY_MAPPINGS: dict[str, dict[str, str]] = {
    "BRENT_ETC_PROXY": {"proxy_symbol": "BNO", "reason": "无法唯一识别的 Brent ETC；使用 BNO 代理"},
    "AUUSI.SW": {"proxy_symbol": "GLD", "reason": "瑞士黄金 ETF；使用 GLD 与人民币汇率代理"},
    "ZGLD.SW": {"proxy_symbol": "GLD", "reason": "瑞士黄金 ETF；使用 GLD 与人民币汇率代理"},
    "CSGOLD.SW": {"proxy_symbol": "GLD", "reason": "瑞士黄金 ETF；使用 GLD 与人民币汇率代理"},
    "OILUSA.SW": {"proxy_symbol": "USO", "reason": "瑞士原油 ETF；使用 USO 与人民币汇率代理"},
    "2644.T": {"proxy_symbol": "SOXX", "reason": "日本半导体 ETF；使用 SOXX 与人民币汇率代理"},
    "1671.T": {"proxy_symbol": "USO", "reason": "日本 WTI ETF；使用 USO 与人民币汇率代理"},
    "1699.T": {"proxy_symbol": "USO", "reason": "日本原油 ETF；使用 USO 与人民币汇率代理"},
    "1678.T": {"proxy_symbol": "INDA", "reason": "日本印度指数 ETF；使用 INDA 与人民币汇率代理"},
    "1540.T": {"proxy_symbol": "GLD", "reason": "日本黄金 ETF；使用 GLD 与人民币汇率代理"},
    "CRUD.L": {"proxy_symbol": "USO", "reason": "伦敦 WTI ETC；使用 USO 与人民币汇率代理"},
    "BRNT.L": {"proxy_symbol": "BNO", "reason": "伦敦 Brent ETC；使用 BNO 与人民币汇率代理"},
    "NDIA.L": {"proxy_symbol": "INDA", "reason": "伦敦印度 ETF；使用 INDA 与人民币汇率代理"},
    "SGLD.L": {"proxy_symbol": "GLD", "reason": "伦敦黄金 ETC；使用 GLD 与人民币汇率代理"},
    "INDI.MI": {"proxy_symbol": "INDA", "reason": "米兰印度 ETF；使用 INDA 与人民币汇率代理"},
    "ISIN:US91282CPJ44": {"proxy_symbol": "IEF", "reason": "美国中期国债；使用 IEF 代理"},
    "ISIN:US91282CMM00": {"proxy_symbol": "IEF", "reason": "美国中期国债；使用 IEF 代理"},
    "ISIN:US91282CNC19": {"proxy_symbol": "IEF", "reason": "美国中期国债；使用 IEF 代理"},
    "ISIN:US91282CPA35": {"proxy_symbol": "IEF", "reason": "美国中期国债；使用 IEF 代理"},
    "ISIN:US91282CNT44": {"proxy_symbol": "IEF", "reason": "美国中期国债；使用 IEF 代理"},
}


@dataclass
class StoreStatus:
    backend: str
    persistent: bool
    message: str


class FundStore:
    """Version-controlled LOF metadata and quarterly holdings."""

    def __init__(self) -> None:
        self._catalog = self._load_catalog()
        self._holdings = self._load_holdings()
        self._mappings = dict(DEFAULT_PROXY_MAPPINGS)
        self.status = StoreStatus(
            "versioned-json",
            True,
            "LOF 目录与季度持仓来自仓库版本化 JSON",
        )

    @staticmethod
    def _load_catalog() -> list[dict[str, Any]]:
        with CATALOG_FILE.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        funds = payload.get("funds") if isinstance(payload, dict) else None
        if not isinstance(funds, list):
            raise ValueError("lof_catalog.json 缺少 funds 数组")
        return funds

    @staticmethod
    def _load_holdings() -> dict[str, dict[str, Any]]:
        with HOLDINGS_FILE.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        funds = payload.get("funds") if isinstance(payload, dict) else None
        if not isinstance(funds, dict):
            raise ValueError("lof_holdings.json 缺少 funds 对象")
        return funds

    def _joined_fund(self, catalog_item: dict[str, Any]) -> dict[str, Any]:
        code = str(catalog_item.get("fund_code", "")).zfill(6)
        item = dict(catalog_item)
        holding_data = self._holdings.get(code) or {}
        item.update(
            {
                "fund_code": code,
                "is_lof": True,
                "report_period": holding_data.get("report_period"),
                "holdings": holding_data.get("holdings") or [],
                "holdings_source": holding_data.get("source"),
                "holdings_available": bool(holding_data.get("holdings")),
                "holdings_curated": bool(holding_data.get("curated")),
            }
        )
        return item

    def list_lof_catalog(self) -> list[dict[str, Any]]:
        return [self._joined_fund(item) for item in self._catalog]

    def list_dashboard_funds(self) -> list[dict[str, Any]]:
        return [item for item in self.list_lof_catalog() if item["holdings_curated"]]

    def find_curated(self, query: str) -> dict[str, Any] | None:
        needle = query.strip()
        for catalog_item in self._catalog:
            code = str(catalog_item.get("fund_code", "")).zfill(6)
            if needle in {code, str(catalog_item.get("fund_name", ""))}:
                item = self._joined_fund(catalog_item)
                return item if item["holdings_available"] else None
        return None

    def find_lof(self, query: str) -> dict[str, Any] | None:
        needle = query.strip()
        for catalog_item in self._catalog:
            code = str(catalog_item.get("fund_code", "")).zfill(6)
            if needle in {code, str(catalog_item.get("fund_name", ""))}:
                return self._joined_fund(catalog_item)
        return None

    def proxy_mapping(self, symbol: str) -> dict[str, str] | None:
        return self._mappings.get(symbol.upper())

    def health(self) -> dict[str, Any]:
        return {
            "backend": self.status.backend,
            "persistent": self.status.persistent,
            "message": self.status.message,
            "catalog_count": len(self._catalog),
            "holdings_count": len(self._holdings),
            "proxy_mapping_count": len(self._mappings),
        }
