# LOF 与基金实时估值中心

把原有的基金实时监控与 QDII/LOF 溢价看板整合为一个 FastAPI 网页。

## 功能

- **LOF 看板**：展示当前维护的 21 只基金，包括基金名称、代码、持仓实时估值、场内价格、行业口径溢价率、取价覆盖率和行情质量。
- **基金搜索**：按完整 6 位代码或完整名称搜索公募基金，根据最新披露持仓计算估算净值。
- **跨市场换算**：外币持仓按基准日与当前汇率折算成人民币。
- **代理估值标注**：日股、瑞士、伦敦等缺少稳定免费实时 API 的标的可使用相关 ETF 代理；前端逐项显示代理代码和原因。
- **可选 PostgreSQL**：配置 `DATABASE_URL` 时使用 Supabase/标准 PostgreSQL；未配置时直接读取仓库中的 `data.json`，本机无需安装 PostgreSQL。

## 估值口径

```text
持仓人民币收益率 = (当前价 / 净值基准日价格) × (当前汇率 / 基准日汇率) - 1
实时估值 = 最新披露单位净值 × (1 + Σ 持仓权重 × 持仓人民币收益率)
溢价率 = (场内价格 / 实时估值 - 1) × 100%
```

未披露仓位默认收益为 0，因此页面同时展示已记录持仓权重和成功取价权重。该结果不是基金公司发布的 IOPV，也不构成投资建议。

行情质量分为：

- `直接行情`：腾讯实时快照，历史基准价由前收或东方财富日线补充；
- `代理估值`：使用相关 ETF/指数代替无法稳定取价的原标的；
- `收盘行情`：只能取得最近收盘价；
- `稳定近似`：低波动债券暂按价格不变处理；
- `行情缺失`：该持仓不参与本次涨跌贡献，并降低覆盖率。

## 本地运行

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>。

默认不需要数据库。应用会读取根目录的 `data.json`，包含 21 只看板基金及其披露持仓。

## 可选：连接 Supabase PostgreSQL

Supabase 提供的是完整 PostgreSQL。项目仅使用标准 SQLAlchemy/PostgreSQL 连接，不依赖 Supabase 专有 SDK，未来可迁移到其他 PostgreSQL。

1. 在 Supabase 创建项目。
2. 在项目的 **Connect** 面板复制 **Session Pooler** 连接串。
3. 将驱动前缀改为 `postgresql+psycopg://`。
4. 在本机设置环境变量（不要提交密码）：

```powershell
$env:DATABASE_URL = "postgresql+psycopg://postgres.PROJECT:PASSWORD@REGION.pooler.supabase.com:5432/postgres"
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

首次连接会创建并填充：

- `funds`：基金、报告期和持仓 JSON；
- `symbol_mappings`：无法直接取价标的与代理标的的映射。

如果数据库暂时不可用，应用会回退到 `data.json`，并在 `/api/health` 返回当前存储状态。

## Render 部署

仓库根目录的 `render.yaml` 可部署单个免费 Web Service：

```text
Build: pip install -r backend/requirements.txt
Start: uvicorn backend.main:app --host 0.0.0.0 --port $PORT
Health: /api/health
```

如需 Supabase，在 Render 服务的 **Environment** 页面手动添加 `DATABASE_URL`。不要把连接串写入 `render.yaml` 或 GitHub。

Render 免费服务闲置后会休眠，第一次访问还需要等待服务启动和首次行情缓存；后续看板请求会使用短时缓存。

## API

- `GET /api/dashboard`：21 只 LOF 看板；
- `GET /api/search?q=005827`：按完整代码或名称估值；
- `GET /api/fund/005827`：兼容旧版代码接口；
- `GET /api/health`：服务与数据库状态。

## 数据维护

- 无数据库模式：编辑 `data.json` 后提交 Git；
- PostgreSQL 模式：维护 `funds` 和 `symbol_mappings` 表；
- 新增代理映射时必须填写原因，前端会原样展示，禁止把代理行情伪装为原标的实时行情。
