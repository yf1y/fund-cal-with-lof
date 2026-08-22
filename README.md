# FundCal

> 看得见计算过程的基金实时估值工具：既能查询普通公募基金，也能在一张看板里比较全量 LOF 的场内价格、估算净值与折溢价。

FundCal 面向个人投资者，不只给出一个“实时估值”数字，还把估值依据一并展示：基金最新披露持仓、每项资产使用的行情或替代标的、人民币汇率、成功取价比例和各自更新时间。这样即使免费行情覆盖不完整，也能知道误差可能来自哪里，而不是把近似值包装成官方净值。

当前目录包含 **469 只可取得沪深场内报价的 LOF**。其中 21 只经过人工校验，打开看板即可估值；其余基金保留完整目录与场内价格，点击后按需读取持仓并计算。普通公募基金也可以通过代码或完整名称查询，查过的基金会保留在列表中，最近查询排在最前。

## 功能概述

网页包含两个入口：

| 页面 | 能做什么 |
| --- | --- |
| **LOF 看板** | 浏览全量目录，按代码、名称或行业筛选；查看场内价格；对 21 只深度维护基金直接估值，对其余基金按需计算；展开查看持仓贡献与计价口径 |
| **基金查询** | 按 6 位基金代码或完整名称计算实时估值；自动保存成功结果；继续查询时保留旧结果并将最新查询置顶 |

### 估算口径

FundCal 以基金最新公布的单位净值为基准，计算已披露持仓从净值基准日至当前的人民币收益：

```text
持仓人民币收益率 = (当前价 / 基准日价格) × (当前汇率 / 基准日汇率) - 1

实时估值 = 最新单位净值 × (1 + Σ 持仓权重 × 持仓人民币收益率)

溢价率 = (实时估值 - 场内价格) / 场内价格 × 100%
```

- 溢价率为正，表示估算净值高于场内价格；为负，表示估算净值低于场内价格。
- 海外资产的标的涨跌和汇率变化都会计入，最终折算为人民币；页面单独显示汇率更新时间。
- 未披露仓位和未成功取价的持仓按 0 收益处理，不会把前十大持仓的涨跌放大到整个基金。
- “已估持仓”指成功取得行情并参与计算的持仓占基金净值的比例，不是置信度评分。
- 原证券没有稳定免费行情时，可能采用相关 ETF 或指数近似；页面会明确标为“相关资产近似”并展示替代代码。
- 不同市场交易时段、持仓披露滞后、基金调仓、替代标的和免费行情延迟都会造成误差。结果不是基金公司发布的官方 IOPV，不构成投资建议。

项目不维护庞大的历史净值库。历史行情只在计算基准涨跌时按需获取；重点维护的是两类真正影响使用速度的数据：季度持仓和查询缓存。

## 使用方式与部署方式

### 本机运行

本机不需要安装 PostgreSQL。Python 自带的 SQLite 会在首次启动时自动创建本地文件。

```powershell
git clone https://github.com/yf1y/fund-cal-with-lof.git
cd fund-cal-with-lof
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>，按 `Ctrl+C` 停止。

已经安装过依赖时：

```powershell
cd fund-cal-with-lof
.\venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

### Render + Supabase 部署

`render.yaml` 已包含 Render 的构建、启动和健康检查配置。连接 GitHub 仓库创建 Blueprint 后即可部署。

云端建议同时创建一个 Supabase 免费项目，并把 Supabase 的 PostgreSQL 连接串作为 Render 环境变量 `DATABASE_URL`。应用首次连接时会自动创建：

- `runtime_cache`：减少重复抓取持仓和基金目录的等待时间；
- `search_history`：永久保存查过的基金及顺序。

Supabase 是托管的 PostgreSQL；这里仍然使用标准 SQL 和 PostgreSQL 驱动，不需要 Supabase 专有 SDK。数据库与 Render Web Service 相互独立，因此 **重新部署、重启或休眠 Render 不会删除 Supabase 中的数据**。

如果没有配置 `DATABASE_URL`，Render 也能启动，但只能使用实例内的 SQLite。Render 的临时文件系统在重新部署、重启或服务休眠后可能丢失，此时查询历史无法保证保留。

Render 免费 Web Service 每个 workspace 每个自然月共享 750 个实例小时；15 分钟无访问会休眠，下一次访问会冷启动。用量月底重置且不结转，多个免费服务会共同消耗额度。详见 [Render Free 实例说明](https://render.com/docs/free)。Supabase 免费项目长时间低活跃时也可能暂停，但数据库不会因 Render 部署而消失，可在 Supabase 控制台恢复。

### 在本机改用 PostgreSQL

只有需要验证云端数据库时才需要这样做：

```powershell
$env:DATABASE_URL = "postgresql://USER:PASSWORD@HOST:5432/postgres"
.\venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

连接串包含密码，不要写入代码或提交到 GitHub。未设置 `DATABASE_URL` 时始终使用 SQLite。

## 使用说明

### LOF 数据如何维护

数据按变化频率拆为三层，没有重复职责：

| 数据 | 文件或数据库 | 更新节奏 | 内容 |
| --- | --- | --- | --- |
| LOF 基础目录 | `data/lof_catalog.json` | 低频 | 代码、名称、交易市场、基金类别、行业分类、是否 QDII |
| LOF 持仓 | `data/lof_holdings.json` | 季度为主 | 披露期、持仓、行情代码映射、是否人工校验 |
| 用户查询 | SQLite 或 Supabase PostgreSQL | 每次成功查询 | 查询结果、持仓抓取缓存、最近查询顺序 |

手工同步命令：

```powershell
# 更新全量场内目录
.\venv\Scripts\python.exe scripts\sync_lof_data.py --catalog

# 更新公开披露持仓
.\venv\Scripts\python.exe scripts\sync_lof_data.py --holdings

# 两者依次更新
.\venv\Scripts\python.exe scripts\sync_lof_data.py --all
```

GitHub Actions 中的 `Sync LOF catalog and holdings` 每月自动运行一次，也可以在 Actions 页面手动触发。自动同步不会覆盖标记为人工校验的 21 只持仓；如确需覆盖，可加 `--include-curated`。

### 查询与缓存

- 代码查询最快，例如 `012870`、`160719`。
- 名称查询目前要求完整名称精确匹配。
- 第一次查询需要读取基金公开资料，复杂基金可能较慢；成功后持仓文档会缓存 7 天。
- 每次查询仍会更新可取得的行情和汇率，不会直接把旧估值冒充实时数据。
- 同一基金再次查询会移动到第一条，其他已查询基金继续保留在后面。

### 配置项

本项目不使用大模型，没有模型、Prompt 或模型 API Key。

| 环境变量 | 必需 | 作用 |
| --- | --- | --- |
| `DATABASE_URL` | 否 | Supabase 或标准 PostgreSQL 连接串；Render 上建议配置 |
| `PORT` | 否 | 服务端口，默认 `8000`；Render 自动提供 |
| `RELOAD` | 否 | 本地开发设为 `1` 时启用代码自动重载 |

### 运行交互式公网分享 CLI

先保持网页服务运行，再打开第二个 PowerShell：

```powershell
cd fund-cal-with-lof
.\venv\Scripts\python.exe share.py
```

`share.py` 使用 ngrok 创建临时公网网址。首次运行会交互式要求 ngrok Authtoken；按 `Ctrl+C` 关闭分享。

### API

| 接口 | 作用 |
| --- | --- |
| `GET /api/lof/catalog` | 快速返回全量 LOF 目录与场内价格 |
| `GET /api/dashboard` | 返回全量目录及 21 只深度维护基金的估值 |
| `GET /api/lof/{fund_code}/estimate` | 按需计算指定 LOF |
| `GET /api/search?q=012870` | 按完整代码或名称查询并保存结果 |
| `GET /api/search/history` | 返回最近查询在前的历史列表 |
| `GET /api/fund/160719` | 兼容旧版基金代码接口 |
| `GET /api/health` | 查看静态数据数量及 SQLite / PostgreSQL 状态 |

## 项目结构

```text
fund-cal-with-lof/
├── .github/workflows/
│   └── sync-lof-data.yml   # 每月同步目录与季度持仓
├── backend/
│   ├── cache.py            # SQLite / PostgreSQL 查询缓存与历史
│   ├── main.py             # FastAPI 路由、健康检查与网页入口
│   ├── market_data.py      # 基金净值、持仓、国内外行情与汇率
│   ├── store.py            # LOF 目录与持仓数据层
│   └── valuation.py        # 人民币估值、折溢价与覆盖率
├── data/
│   ├── lof_catalog.json    # 低频变化的全量 LOF 基础目录
│   └── lof_holdings.json   # 季度变化的 LOF 披露持仓
├── frontend/
│   └── index.html          # LOF 看板与基金查询界面
├── scripts/
│   └── sync_lof_data.py    # 目录发现与持仓更新 CLI
├── tests/
│   └── test_core.py        # 估值、行情、缓存与数据分层测试
├── render.yaml             # Render Blueprint
└── share.py                # ngrok 交互式分享工具
```

FundCal 借鉴了 [FundVal-Live](https://github.com/Ye-Yu-Mo/FundVal-Live) 对持仓穿透和覆盖率的重视，以及 [lof-fund-monitor-optimized-v2.8L](https://github.com/YeYeXuXu/lof-fund-monitor-optimized-v2.8L) 对 LOF 分类、海外代理和申赎信息的产品思路。FundCal 的取舍是更适合个人部署：一个网页、无需本机数据库服务、全量场内目录与按需估值并存，并把每一项近似和缺失显式展示。估值与缓存代码为独立实现，未复制上述项目源码。
