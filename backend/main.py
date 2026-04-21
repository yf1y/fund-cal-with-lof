from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import akshare as ak
import httpx
import re
import os
import asyncio
from datetime import datetime
from pydantic import BaseModel

app = FastAPI(title="Fund Realtime Monitor")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 内存缓存
fund_cache = {}
fund_name_cache = {}


def get_fund_name(code: str) -> str:
    global fund_name_cache
    if not fund_name_cache:
        try:
            df = ak.fund_name_em()
            # 将 df转换为字典，以基金代码为主键
            for _, row in df.iterrows():
                fund_name_cache[str(row["基金代码"]).zfill(6)] = str(row["基金简称"])
        except Exception as e:
            print("Get fund name error:", e)
    return fund_name_cache.get(code, "未知基金")


class FundResponse(BaseModel):
    fund_code: str
    fund_name: str
    estimate_change: float
    total_weight: float
    unknown_weight: float
    update_time: str
    base_nav: float


async def fetch_tencent_stocks(stock_codes):
    """批量从腾讯接口获取股票实时涨跌幅"""
    if not stock_codes:
        return {}

    # 格式化代码，加上sh或sz前缀
    formatted_codes = []
    for code in stock_codes:
        if code.startswith(("6", "9")):  # 沪市
            formatted_codes.append(f"sh{code}")
        else:  # 深市或北交所等，简化处理
            formatted_codes.append(f"sz{code}")

    url = f"http://qt.gtimg.cn/q={','.join(formatted_codes)}"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, timeout=5)
            text = resp.text

            result = {}
            lines = text.split(";")
            for line in lines:
                if not line.strip():
                    continue
                match = re.search(r'v_(s[hz]\d+)="([^"]+)"', line)
                if match:
                    code_prefix = match.group(1)
                    info_str = match.group(2)
                    parts = info_str.split("~")
                    if len(parts) > 32:
                        # 腾迅接口返回：涨跌幅在第33位(下标32)，现价在第4位(下标3)等
                        stock_code = code_prefix[2:]  # 纯数字代码
                        try:
                            pct_change = float(parts[32])
                            result[stock_code] = pct_change
                        except ValueError:
                            pass
            return result
        except BaseException as e:
            print(f"Fetch stock error: {e}")
            return {}


def fetch_fund_data_blocking(fund_code: str):
    # 阻塞的 akshare 操作
    # 获取基金名称
    fund_name = get_fund_name(fund_code)

    # 获取持仓
    current_year = datetime.now().year
    fund_portfolio_df = None
    for offset in range(3):
        year_str = str(current_year - offset)
        try:
            df = ak.fund_portfolio_hold_em(symbol=fund_code, date=year_str)
            if df is not None and not df.empty:
                fund_portfolio_df = df
                break
        except Exception:
            pass

    return fund_name, fund_portfolio_df


@app.get("/api/fund/{fund_code}", response_model=FundResponse)
async def get_fund_estimate(fund_code: str):
    # 1. 如果缓存中没有，则抓取基金信息和持仓
    if fund_code not in fund_cache:
        try:
            # 将阻塞的同步调用放到线程池中执行，避免阻塞 FastAPI 事件循环
            fund_name, fund_portfolio_df = await asyncio.to_thread(
                fetch_fund_data_blocking, fund_code
            )

            if fund_portfolio_df is None or fund_portfolio_df.empty:
                raise HTTPException(
                    status_code=404, detail="Fund holding info not found"
                )

            holdings = []
            total_weight = 0.0

            if not fund_portfolio_df.empty:
                # 过滤出最新季度的持仓数据，因为 akshare 默认按年份拉取全年的几个季度数据，叠加会导致占比超过100%
                if "季度" in fund_portfolio_df.columns:
                    latest_quarter = fund_portfolio_df["季度"].max()
                    fund_portfolio_df = fund_portfolio_df[
                        fund_portfolio_df["季度"] == latest_quarter
                    ]

                # DataFrame列名：序号、股票代码、股票名称、占净值比例
                for _, row in fund_portfolio_df.iterrows():
                    # 检查是否是前十大重仓股（一般只有10条）
                    code = str(row["股票代码"]).zfill(6)
                    try:
                        weight = float(row["占净值比例"])
                        holdings.append({"code": code, "weight": weight})
                        total_weight += weight
                    except:
                        pass

            fund_cache[fund_code] = {
                "name": fund_name,
                "holdings": holdings,
                "total_weight": total_weight,
                "last_update": datetime.now(),
            }
        except Exception as e:
            print(f"Error fetching fund {fund_code}: {e}")
            raise HTTPException(status_code=500, detail="Error fetching fund data")

    fund_data = fund_cache[fund_code]
    holdings = fund_data["holdings"]
    total_weight = fund_data["total_weight"]

    # 无持仓数据时
    if not holdings or total_weight == 0:
        return {
            "fund_code": fund_code,
            "fund_name": fund_data["name"],
            "estimate_change": 0.0,
            "total_weight": 0.0,
            "unknown_weight": 100.0,
            "update_time": datetime.now().strftime("%H:%M:%S"),
            "base_nav": 1.0,
        }

    # 2. 批量获取实时行情
    stock_codes = [h["code"] for h in holdings]
    live_quotes = await fetch_tencent_stocks(stock_codes)

    # 3. 计算加权涨跌幅
    estimate_change = 0.0
    for h in holdings:
        pct = live_quotes.get(h["code"], 0.0)
        # weight 是百分比，例如 8.5 表示 8.5%
        estimate_change += pct * (h["weight"] / 100.0)

    return {
        "fund_code": fund_code,
        "fund_name": fund_data["name"],
        "estimate_change": round(estimate_change, 2),
        "total_weight": round(total_weight, 2),
        "unknown_weight": round(100.0 - total_weight, 2),
        "update_time": datetime.now().strftime("%H:%M:%S"),
        "base_nav": 1.0,  # 这里可以扩展取昨收净值计算现价估算净值
    }


# 挂载前端静态文件
frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.exists(frontend_dir):
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")


@app.get("/")
def read_root():
    index_path = os.path.join(frontend_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Frontend not found"}


if __name__ == "__main__":
    import uvicorn
    import os
    import sys

    # 确保本地运行时 uvicorn 能够正确找到 backend 模块
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    # 从环境变量中获取端口（Render 等云平台会自动设置端口），默认为 8000
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("backend.main:app", host="0.0.0.0", port=port, reload=True)
