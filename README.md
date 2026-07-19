# A股板块/ETF 条件筛选器（本地原型）

基于公开数据（AKShare）的板块/ETF 条件筛选工具。**只筛选不荐股、不承诺收益、不输出买卖点。**

## ⚠️ 合规声明

本工具是**数据筛选器，非投资咨询**：

- 仅基于公开行情/资金流数据做阈值/排名过滤
- **不荐股、不承诺收益、不输出买卖点、不构成投资建议**
- 不做与推荐结果挂钩的付费/会员/收益分成
- 无证券投资咨询资质，不提供任何证券投资咨询服务

## 安装

```bash
cd D:\claude-info\stock-screener
pip install -r requirements.txt
```

## 核对 AKShare 真实字段名（首次运行必做）

AKShare 版本间字段可能微调。装好后先核对：

```bash
python -c "import akshare as ak; print(ak.__version__); print(ak.stock_board_industry_name_em().head())"
```

若列名与 `data/models.py` 的 `BOARD_ALIASES` 不一致，补一个别名映射即可，无需改 schema。

## 运行

```bash
# 1. 单测（不依赖网络）
pytest tests/test_engine.py

# 2. 启动本地服务
uvicorn api.server:app --reload --port 8000

# 3. 手动触发数据采集（浏览器或 curl）
curl -X POST http://localhost:8000/api/refresh

# 4. 打开前端验证页（直接双击 web/index.html，或）
python -m http.server 8080 --directory web
#    浏览器访问 http://localhost:8080
```

## 接口

| 接口 | 说明 |
|---|---|
| `GET /api/fields` | 字段目录+运算符，前端动态渲染条件表单 |
| `GET /api/boards?category=行业&sort=change_pct&limit=20` | 板块排名 |
| `GET /api/etfs?sort=turnover_amount&limit=30` | ETF 排名 |
| `GET /api/screen?category=行业&conditions=<json>&sort=&limit=` | 条件筛选 |
| `POST /api/refresh` | 手动全量采集刷新 |
| `GET /api/meta` | 最近更新时间 |

所有数据响应都附 `disclaimer` + `update_time`。

## 条件结构

```json
[{"field": "main_net_inflow", "op": "gt", "value": 0},
 {"field": "change_pct", "op": "between", "value": [0, 3]}]
```

op ∈ `gt | gte | lt | lte | between | topn`；`between` 用 `[lo,hi]`，`topn` 取前 N 名。

## 项目结构

```
stock-screener/
  data/        collector.py db.py models.py
  screener/    conditions.py engine.py
  api/         server.py (FastAPI)
  web/         index.html (暗色CSS验证页)
  tests/       test_engine.py (mock单测)
  config.yaml  预置条件模板
```

## Docker Desktop 部署

```bash
# 一键构建并后台启动
docker compose up --build -d

# 采集数据(首次必做)
curl -X POST http://localhost:8000/api/refresh
```

- 前端：http://localhost:8000/web/index.html
- API 文档：http://localhost:8000/docs
- SQLite 持久化在命名卷 `screener-db`（挂 `/app/var`），容器重启不丢数据

停止 / 清理：
```bash
docker compose down            # 停止(保留数据卷)
docker compose down -v         # 连同数据卷一起清除
```

## 分期（本仓库只做阶段1）

- ✅ 阶段1：采集 + SQLite + 筛选引擎 + FastAPI + web 验证页 + 单测
- ⏳ 阶段2：小程序前端（微信开发者工具连 localhost:8000，开发期关域名校验）
- ⏳ 阶段3：迁云开发/独立后端 + 备案上架

## 免责

数据来自公开接口（AKShare 聚合东方财富等），可能存在延迟或缺失。本工具不构成任何投资建议，市场有风险，投资决策请独立判断，盈亏自负。
