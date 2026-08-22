from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import akshare as ak

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.market_data import MarketDataClient, exchange_symbol_for_fund
from backend.valuation import ValuationService


DATA_DIR = PROJECT_ROOT / "data"
CATALOG_FILE = DATA_DIR / "lof_catalog.json"
HOLDINGS_FILE = DATA_DIR / "lof_holdings.json"


INDUSTRY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("半导体", ("半导体", "芯片")),
    ("人工智能与科技", ("人工智能", "科技", "互联网", "软件", "计算机", "通信")),
    ("医药医疗", ("医药", "医疗", "生物", "创新药")),
    ("新能源", ("新能源", "光伏", "电池", "电力", "碳中和")),
    ("消费", ("消费", "食品", "白酒", "家电", "旅游")),
    ("金融", ("银行", "证券", "金融", "保险")),
    ("资源与商品", ("黄金", "原油", "有色", "煤炭", "商品", "能源化工")),
    ("军工", ("军工", "国防", "航空航天")),
    ("农业", ("农业", "畜牧", "养殖")),
    ("地产与基建", ("地产", "房地产", "基建", "建筑")),
    ("港股", ("香港", "港股", "恒生")),
    ("海外市场", ("美国", "纳斯达克", "标普", "德国", "法国", "日本", "印度", "海外")),
    ("红利", ("红利", "股息")),
    ("国企与央企", ("国企", "央企")),
    ("债券", ("债", "信用", "转债")),
]


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(path)


def _industry(name: str, fund_type: str) -> str:
    text = f"{name} {fund_type}"
    for label, keywords in INDUSTRY_RULES:
        if any(keyword in text for keyword in keywords):
            return label
    if "QDII" in text.upper():
        return "海外市场"
    if "指数" in text:
        return "宽基与策略指数"
    return "综合"


async def sync_catalog() -> dict[str, Any]:
    frame = await asyncio.to_thread(ak.fund_name_em)
    code_column = next(col for col in frame.columns if "代码" in str(col))
    name_column = next(col for col in frame.columns if "简称" in str(col) or "名称" in str(col))
    type_column = next((col for col in frame.columns if "类型" in str(col)), None)
    candidates: dict[str, dict[str, Any]] = {}
    for _, row in frame.iterrows():
        code = str(row[code_column]).zfill(6)
        if not code.startswith(("16", "501", "502", "506")):
            continue
        name = str(row[name_column])
        fund_type = str(row[type_column]) if type_column is not None else "未知"
        candidates[code] = {
            "fund_code": code,
            "fund_name": name,
            "market": "SH" if code.startswith(("501", "502", "506")) else "SZ",
            "fund_category": fund_type,
            "industry": _industry(name, fund_type),
            "is_qdii": "QDII" in f"{name} {fund_type}".upper(),
            "classification_method": "名称与基金类型规则",
        }

    market = MarketDataClient()
    try:
        symbols = [exchange_symbol_for_fund(code) for code in candidates]
        quotes = await market.current_prices(symbols)
    finally:
        await market.close()
    active_codes = {
        code
        for code in candidates
        if exchange_symbol_for_fund(code) in quotes
    }

    existing = _read_json(CATALOG_FILE, {}).get("funds", [])
    existing_by_code = {str(item["fund_code"]).zfill(6): item for item in existing}
    curated_codes = {
        code for code, item in existing_by_code.items() if item.get("curated")
    }
    active_codes.update(curated_codes)
    funds = []
    for code in sorted(active_codes):
        item = dict(candidates.get(code) or existing_by_code.get(code) or {})
        existing_item = existing_by_code.get(code) or {}
        if existing_item.get("curated"):
            item.update(
                {
                    "fund_name": existing_item.get("fund_name") or item.get("fund_name") or code,
                    "is_qdii": bool(existing_item.get("is_qdii")),
                }
            )
        item["fund_code"] = code
        item["market"] = "SH" if code.startswith(("501", "502", "506")) else "SZ"
        item.setdefault("fund_category", "QDII" if item.get("is_qdii") else "LOF")
        item.setdefault("industry", _industry(item["fund_name"], item["fund_category"]))
        item.setdefault("classification_method", "名称与基金类型规则")
        item["curated"] = code in curated_codes
        funds.append(item)

    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "东方财富基金目录 + 腾讯场内行情验证",
        "verification": "候选代码必须取得沪深场内行情；人工维护基金始终保留",
        "count": len(funds),
        "funds": funds,
    }
    _write_json(CATALOG_FILE, payload)
    return payload


def seed_holdings() -> dict[str, Any]:
    existing = _read_json(HOLDINGS_FILE, {})
    funds = dict(existing.get("funds") or {})
    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "基金公开披露；人工数据优先于自动抓取",
        "count": len(funds),
        "funds": funds,
    }
    _write_json(HOLDINGS_FILE, payload)
    return payload


async def sync_holdings(limit: int | None = None, include_curated: bool = False) -> dict[str, Any]:
    catalog = _read_json(CATALOG_FILE, {})
    catalog_funds = catalog.get("funds") or []
    if not catalog_funds:
        raise RuntimeError("LOF 目录为空，请先运行 --catalog")
    payload = seed_holdings()
    stored = dict(payload.get("funds") or {})
    targets = [
        item
        for item in catalog_funds
        if include_curated or not stored.get(str(item["fund_code"]).zfill(6), {}).get("curated")
    ]
    if limit is not None:
        targets = targets[:limit]

    market = MarketDataClient()
    worker_limit = asyncio.Semaphore(6)

    async def load(item: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
        code = str(item["fund_code"]).zfill(6)
        async with worker_limit:
            document = await market.fund_holdings_document(code)
        holdings, report_period = await asyncio.to_thread(
            ValuationService._parse_holdings_document, document
        )
        if not holdings:
            return code, None
        return code, {
            "report_period": report_period,
            "holdings": holdings,
            "source": "东方财富基金持仓公开披露自动同步",
            "curated": False,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }

    try:
        for offset in range(0, len(targets), 24):
            results = await asyncio.gather(*(load(item) for item in targets[offset : offset + 24]))
            for code, data in results:
                if data is not None:
                    stored[code] = data
            _write_json(
                HOLDINGS_FILE,
                {
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                    "source": "基金公开披露；人工数据优先于自动抓取",
                    "count": len(stored),
                    "funds": stored,
                },
            )
    finally:
        await market.close()
    return _read_json(HOLDINGS_FILE, {})


async def main() -> None:
    parser = argparse.ArgumentParser(description="同步 FundCal 的 LOF 目录与季度持仓")
    parser.add_argument("--catalog", action="store_true", help="更新全量 LOF 基础目录")
    parser.add_argument("--holdings", action="store_true", help="更新非人工维护 LOF 的最新持仓")
    parser.add_argument("--all", action="store_true", help="依次更新目录和持仓")
    parser.add_argument("--limit", type=int, help="仅同步前 N 只持仓，便于测试")
    parser.add_argument("--include-curated", action="store_true", help="也覆盖人工校验持仓")
    args = parser.parse_args()
    if not any((args.catalog, args.holdings, args.all)):
        args.catalog = True
    if args.catalog or args.all:
        catalog = await sync_catalog()
        print(f"LOF 目录已更新：{catalog['count']} 只")
    if args.holdings or args.all:
        holdings = await sync_holdings(args.limit, args.include_curated)
        print(f"LOF 持仓已保存：{holdings['count']} 只")


if __name__ == "__main__":
    asyncio.run(main())
