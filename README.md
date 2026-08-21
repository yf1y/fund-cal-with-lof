# LOF 与基金实时估值中心

> **一眼看懂 LOF 是贵了还是便宜了，也看得见这个估值靠不靠谱。**

很多基金估值工具只给一个数字。本项目把 **最新披露持仓、跨市场行情、人民币汇率、场内价格和已估持仓比例** 放到同一张看板里：既计算实时估值和溢价，也明确告诉你哪些持仓按原标的计算、哪些使用相关资产近似、哪些暂时没有取到价格。

打开网页即可使用，无需券商客户端，也无需在本机安装数据库。当前先维护 21 只 LOF/QDII-LOF，并保留按基金代码或完整名称查询其他公募基金的能力。

## 功能概述

网页包含两个 Tab：

| 功能 | 你可以看到什么 |
| --- | --- |
| **LOF 看板** | 21 只基金的实时估值、场内价格、溢价率、已估持仓比例和估值说明 |
| **基金搜索** | 输入完整 6 位基金代码或完整基金名称，按最新披露持仓计算估算净值 |

看板将交易代码作为主信息，名称明确标注为“基金简称”；估值更新时间、场内行情时间和汇率更新时间分别展示，避免把不同时间点的数据混为一次更新。

### 估算口径

估值从基金最近一次披露的单位净值出发，计算各项已披露持仓从净值基准日至当前的人民币收益率：

```text
持仓人民币收益率 = (当前价 / 基准日价格) × (当前汇率 / 基准日汇率) - 1

实时估值 = 最新披露单位净值 × (1 + Σ 持仓权重 × 持仓人民币收益率)

溢价率 = (场内价格 / 实时估值 - 1) × 100%
```

- 溢价率大于 0：场内交易价格高于估算净值，即溢价。
- 溢价率小于 0：场内交易价格低于估算净值，即折价。
- 外币资产会同时计入标的涨跌与汇率变化，并统一折算为人民币。
- 未披露仓位默认收益为 0；页面会展示“已记录持仓权重”和“成功取价权重”，方便判断估值完整度。

### 这不是一个假装精确的数字

页面会把估值所用数据明确分为：

| 标记 | 含义 |
| --- | --- |
| **原标的行情** | 使用持仓本身的行情；基准价由前收或历史日线补充 |
| **相关资产近似** | 原标的缺少稳定免费行情，使用相关 ETF 或指数近似，并展示替代代码与原因 |
| **最近收盘价** | 当前只能取得最近收盘价，并非盘中价格 |
| **价格暂按不变** | 对部分低波动债券暂按价格不变处理 |
| **暂缺价格** | 该持仓不参与本次涨跌贡献，同时降低已估持仓比例 |

估值基于公开披露持仓和第三方行情，不是基金公司发布的官方 IOPV，也不构成投资建议。基金调仓、未披露仓位、代理标的偏差和行情延迟都会影响结果。

## 使用方式与部署方式

### 在本机运行

本机默认读取仓库里的 `data.json`，**不需要安装 PostgreSQL，也不需要配置任何 API Key**。

Windows PowerShell：

```powershell
cd C:\Users\21999\Desktop\stock_cal
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

浏览器打开：<http://127.0.0.1:8000>

如果虚拟环境已经创建，也可以直接运行：

```powershell
cd C:\Users\21999\Desktop\stock_cal
.\venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

按 `Ctrl+C` 停止服务。首次打开看板需要抓取跨市场行情，通常会比后续刷新慢；服务会对结果做短时缓存。

### 部署到 Render

仓库内的 `render.yaml` 已包含构建、启动和健康检查配置：

1. 在 Render 新建 **Blueprint**，连接本 GitHub 仓库。
2. Render 会读取 `render.yaml` 创建 Web Service。
3. 部署完成后打开 Render 分配的公网网址。

默认仍使用 `data.json`，无需额外数据库。Render 免费实例休眠后，首次访问需要等待实例启动和行情初始化。

### 可选：使用 Supabase / PostgreSQL

只有需要在线维护基金与代理映射数据时，才建议接入数据库。Supabase 提供的是托管 PostgreSQL，本项目使用标准 SQLAlchemy/PostgreSQL 连接，不依赖 Supabase 专有 SDK。

```powershell
$env:DATABASE_URL = "postgresql+psycopg://postgres.PROJECT:PASSWORD@REGION.pooler.supabase.com:5432/postgres"
.\venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

首次连接会自动创建并填充：

- `funds`：看板基金、报告期和披露持仓；
- `symbol_mappings`：原始标的与代理行情代码的映射。

数据库不可用时，应用会回退到仓库中的 `data.json`。连接字符串包含密码，不要提交到 GitHub。

## 使用说明

### 使用 LOF 看板

打开首页后默认进入 **LOF 看板**：

1. 先看“实时估值”和“场内价格”，判断两者是否明显偏离。
2. 再看“溢价率”：正数为溢价，负数为折价。
3. 最后检查“已估持仓”和“估值说明”。使用相关资产近似或暂缺价格的持仓越多，估值的不确定性越高。
4. 展开基金详情，可以查看每项持仓的行情代码、币种、价格质量和代理原因。

### 精确搜索基金

切换到 **基金搜索**，输入以下任意一种内容：

```text
160719
嘉实黄金证券投资基金（LOF）
```

搜索只接受完整代码或完整名称。搜索结果会展示估算净值、估算涨跌、已估持仓比例、持仓明细和警告信息。

### 配置项

本项目不使用大模型，因此没有模型名称、模型 API Key 或 Prompt 需要配置。可用的运行配置只有：

| 环境变量 | 是否必需 | 作用 |
| --- | --- | --- |
| `DATABASE_URL` | 否 | 连接 Supabase 或标准 PostgreSQL；不配置则使用 `data.json` |
| `PORT` | 否 | 服务端口，默认 `8000`；Render 会自动提供 |
| `RELOAD` | 否 | 本地开发时设为 `1` 可启用自动重载 |

### 运行交互式公网分享 CLI

如果想在手机上访问，或临时把本机网页发给别人，可以使用仓库里的 `share.py`。先保持网页服务运行，再打开第二个 PowerShell：

```powershell
cd C:\Users\21999\Desktop\stock_cal
.\venv\Scripts\python.exe share.py
```

脚本会通过 ngrok 创建临时公网链接。第一次运行时，如果尚未配置 ngrok，会交互式提示粘贴你的 Authtoken；配置完成后重新运行即可。按 `Ctrl+C` 关闭分享链接。

公网链接会暴露当前服务，请只在需要时运行，不要在页面或终端里放置敏感数据。

### API

| 接口 | 作用 |
| --- | --- |
| `GET /api/dashboard` | 返回 21 只 LOF 看板数据 |
| `GET /api/search?q=160719` | 按完整代码或名称查询并估值 |
| `GET /api/fund/160719` | 兼容旧版的基金代码接口 |
| `GET /api/health` | 查看服务、存储模式和基础数据状态 |

## 项目结构

```text
stock_cal/
├── backend/
│   ├── main.py             # FastAPI 路由与网页入口
│   ├── market_data.py      # 国内外行情、历史价格与汇率
│   ├── valuation.py        # 持仓估值、溢价率与覆盖率计算
│   ├── store.py            # JSON / PostgreSQL 双模式数据层
│   └── requirements.txt    # Python 依赖
├── frontend/
│   └── index.html          # 两个 Tab 的单页前端
├── tests/
│   └── test_core.py        # 核心口径与代码映射测试
├── data.json               # 当前 21 只基金与披露持仓
├── share.py                # ngrok 交互式公网分享工具
├── render.yaml             # Render 部署配置
├── .env.example            # 可选环境变量示例
└── README.md
```

当前阶段聚焦 21 只已维护基金。后续扩展全市场时，建议优先增加基金与持仓数据的自动同步，再逐步补充更多市场的直接行情源。
