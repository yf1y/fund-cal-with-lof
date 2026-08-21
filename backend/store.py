from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SEED_FILE = PROJECT_ROOT / "data.json"


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
    """PostgreSQL when DATABASE_URL is set; otherwise read-only JSON seed data."""

    def __init__(self) -> None:
        self.database_url = os.getenv("DATABASE_URL", "").strip()
        self._funds = self._load_seed()
        self._mappings = dict(DEFAULT_PROXY_MAPPINGS)
        self._engine = None
        self.status = StoreStatus("json", False, "未配置 DATABASE_URL，使用仓库 JSON 种子数据")
        if self.database_url:
            self._init_postgres()

    @staticmethod
    def _load_seed() -> list[dict[str, Any]]:
        with SEED_FILE.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, list):
            raise ValueError("data.json 顶层必须是基金数组")
        return payload

    def _init_postgres(self) -> None:
        try:
            from sqlalchemy import (
                JSON,
                Boolean,
                Column,
                MetaData,
                String,
                Table,
                create_engine,
                select,
            )

            metadata = MetaData()
            funds = Table(
                "funds",
                metadata,
                Column("fund_code", String(6), primary_key=True),
                Column("fund_name", String(255), nullable=False),
                Column("is_qdii", Boolean, nullable=False, default=False),
                Column("report_period", String(10)),
                Column("holdings", JSON, nullable=False),
            )
            mappings = Table(
                "symbol_mappings",
                metadata,
                Column("source_symbol", String(80), primary_key=True),
                Column("proxy_symbol", String(80), nullable=False),
                Column("reason", String(255), nullable=False),
            )
            engine = create_engine(self.database_url, pool_pre_ping=True)
            metadata.create_all(engine)
            with engine.begin() as conn:
                if conn.execute(select(funds.c.fund_code).limit(1)).first() is None:
                    conn.execute(
                        funds.insert(),
                        [
                            {
                                "fund_code": str(item["fund_code"]).zfill(6),
                                "fund_name": item.get("fund_name") or "未知基金",
                                "is_qdii": bool(item.get("is_qdii")),
                                "report_period": item.get("report_period"),
                                "holdings": item.get("holdings") or [],
                            }
                            for item in self._funds
                        ],
                    )
                if conn.execute(select(mappings.c.source_symbol).limit(1)).first() is None:
                    conn.execute(
                        mappings.insert(),
                        [
                            {"source_symbol": source, **mapping}
                            for source, mapping in self._mappings.items()
                        ],
                    )
                rows = conn.execute(select(funds)).mappings().all()
                mapping_rows = conn.execute(select(mappings)).mappings().all()
            self._funds = [dict(row) for row in rows]
            self._mappings = {
                row["source_symbol"]: {
                    "proxy_symbol": row["proxy_symbol"],
                    "reason": row["reason"],
                }
                for row in mapping_rows
            }
            self._engine = engine
            self.status = StoreStatus("postgresql", True, "已连接 Supabase/PostgreSQL")
        except Exception as exc:
            self.status = StoreStatus(
                "json-fallback",
                False,
                f"PostgreSQL 连接失败，已回退 JSON：{type(exc).__name__}",
            )

    def list_dashboard_funds(self) -> list[dict[str, Any]]:
        result = []
        for fund in self._funds:
            item = dict(fund)
            item["is_lof"] = True
            result.append(item)
        return result

    def find_curated(self, query: str) -> dict[str, Any] | None:
        needle = query.strip()
        for fund in self._funds:
            if needle in {str(fund.get("fund_code", "")).zfill(6), str(fund.get("fund_name", ""))}:
                item = dict(fund)
                item["is_lof"] = True
                return item
        return None

    def proxy_mapping(self, symbol: str) -> dict[str, str] | None:
        return self._mappings.get(symbol.upper())

    def health(self) -> dict[str, Any]:
        return {
            "backend": self.status.backend,
            "persistent": self.status.persistent,
            "message": self.status.message,
            "fund_count": len(self._funds),
            "proxy_mapping_count": len(self._mappings),
        }
