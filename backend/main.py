from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .cache import LocalCache
from .market_data import MarketDataClient
from .store import FundStore
from .valuation import ValuationService


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"

store = FundStore()
cache = LocalCache()
market = MarketDataClient()
valuation = ValuationService(store, market, cache)


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await market.close()


app = FastAPI(
    title="FundCal",
    description="基金实时估值与 LOF 折溢价工具",
    version="2.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "database": store.health(), "cache": cache.health()}


@app.get("/api/dashboard")
async def dashboard() -> dict:
    rows = await valuation.dashboard()
    fx_times = [row["fx_updated_at"] for row in rows if row.get("fx_updated_at")]
    return {
        "funds": rows,
        "count": len(rows),
        "database": store.health(),
        "fx_updated_at": max(fx_times) if fx_times else None,
        "disclaimer": "估值基于披露持仓、公开行情和已标注代理标的，仅供参考。",
    }


@app.get("/api/search")
async def search_fund(q: str = Query(min_length=1, max_length=100)) -> dict:
    fund = await valuation.resolve_query(q)
    if not fund:
        raise HTTPException(status_code=404, detail="未找到完全匹配的基金代码或名称")
    result = await valuation.estimate_fund(fund)
    if not fund.get("holdings"):
        result["warnings"].append("未取得最新季度持仓，实时估值可能无法计算。")
    return result


@app.get("/api/fund/{fund_code}")
async def fund_by_code(fund_code: str) -> dict:
    if not fund_code.isdigit() or len(fund_code) != 6:
        raise HTTPException(status_code=400, detail="基金代码必须为 6 位数字")
    fund = await valuation.resolve_query(fund_code)
    if not fund:
        raise HTTPException(status_code=404, detail="基金不存在")
    result = await valuation.estimate_fund(fund)
    # 保留旧前端字段，便于已有调用方平滑升级。
    result["estimate_change"] = result["estimated_change_pct"]
    result["total_weight"] = result["recorded_weight"]
    return result


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
async def read_root():
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"message": "Frontend not found"}


if __name__ == "__main__":
    import uvicorn

    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        reload=os.environ.get("RELOAD", "0") == "1",
    )
