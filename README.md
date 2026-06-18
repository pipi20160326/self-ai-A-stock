# A 股板块趋势策略扫描与回测工具

一个“先板块、后个股”的日线趋势扫描与回测工具。默认使用 Baostock 获取 A 股日线、指数和行业成分数据，ETF 列表仍可选用 AkShare 增强；配置 `TUSHARE_TOKEN` 后可预留切换 Tushare 数据源。

> 仅用于研究和学习，不构成投资建议。

## 安装

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 使用

```powershell
streamlit run app.py
uvicorn api_server:app --host 127.0.0.1 --port 8600
python -m src.cli scan
python -m src.cli update-data
```

常用环境变量：

```powershell
$env:DATA_PROVIDER="auto"
$env:TUSHARE_TOKEN=""
$env:SCAN_TOP_SECTORS="5"
$env:SCAN_TOP_STOCKS_PER_SECTOR="3"
$env:MARKET_FILTER="true"
$env:START_DATE="20200101"
$env:INITIAL_CASH="1000000"
$env:DATA_REQUEST_TIMEOUT="15"
$env:DATA_RETRY_ATTEMPTS="2"
$env:DATA_RETRY_DELAY="1"
$env:BAOSTOCK_SECTOR_MEMBER_LIMIT="8"
$env:BAOSTOCK_SECTOR_PREFILTER="12"
$env:BAOSTOCK_SCAN_MEMBER_LIMIT="20"
$env:BAOSTOCK_REPORT_PREFILTER="12"
$env:DAILY_PREFILTER="40"
$env:DAILY_TOP_SECTORS="12"
$env:DAILY_STOCKS_PER_SECTOR="3"
$env:DAILY_MEMBER_LIMIT="20"
$env:DAILY_ETF_PREFILTER="30"
$env:DAILY_TOP_ETFS="10"
$env:DAILY_REFRESH="false"
```

也可以复制 `.env.example` 为 `.env`，定时任务和页面启动时会自动读取 `.env`。`.env` 已被 git 忽略，不会提交到仓库。

## 输出

- `data/cache/market_cache.sqlite3`：本地 SQLite 缓存。
- `reports/daily/YYYY-MM-DD_scan.csv`：每日扫描清单。
- `reports/backtest/`：历史兼容目录；当前页面已停用回测，避免长时间阻塞。

## 扫描与筛选口径

当前工作流是“先板块、后个股、ETF 做替代观察”，页面工作台和日报共用同一套核心逻辑。

### 板块扫描

1. 先按侧边栏选择的 `板块类型` 获取候选列表：
   - `industry` 行业板块：优先使用 AkShare 东方财富行业板块列表；失败时使用同花顺行业板块列表；如果行业接口都不可用，再降级到 BaoStock `query_stock_industry()` 的行业分类聚合，并生成本地 `BSIxxxxxx` 代码。
   - `concept` 概念板块：使用 AkShare 东方财富/同花顺概念板块列表；如果概念接口不可用，返回空结果并在页面提示，不用 BaoStock 伪造概念板块。
2. 如果板块列表里带 `pct_chg`，日报会先按当日涨跌幅做预筛；工作台的实时排行会取前 `BAOSTOCK_SECTOR_PREFILTER` 个候选，默认至少约为关注板块数的 3 倍。
3. 对每个预筛板块拉取板块历史 K 线并计算趋势分。行业/概念历史优先用 AkShare 板块指数接口；行业接口失败时，会用 BaoStock 成分股近似合成板块走势。
4. 板块趋势分主要由 `ret20`、`ret60`、`ma20_slope`、相对沪深 300 强弱、成交额放大、MA20/MA60 多头结构组成，最后按 `score` 降序取 Top 板块。

### 板块内个股扫描

1. 对入选板块读取成分股：优先 AkShare 板块成分；行业板块失败时可降级 BaoStock 行业成分。
2. 个股只保留主板常见代码前缀：`000`、`001`、`002`、`003`、`600`、`601`、`603`、`605`；会过滤科创板、北交所、创业板/`30` 开头以及其他暂不覆盖的标的。
3. 每个板块只扫描前 `member_limit` 只成分股；工作台默认使用 `DAILY_MEMBER_LIMIT` 或 `BAOSTOCK_SCAN_MEMBER_LIMIT` 控制数量，避免实时逐只拉取过慢。
4. 个股 K 线走 BaoStock `query_history_k_data_plus`，评分使用 `score_stock()`：核心看 MA20/MA60 多头、MA20 斜率、20/60 日收益、20 日新高突破、成交额放大、20 日回撤，并叠加所属板块趋势分。
5. 大盘过滤开启时，如果沪深 300 不满足健康条件，原本的买入信号会降级为观察；卖出信号不会进入候选。
6. 每个板块按观点强弱和分数排序，只保留 `SCAN_TOP_STOCKS_PER_SECTOR` 或日报配置指定数量。

### ETF 筛选

1. ETF 候选不是按所选板块绑定，而是从全市场 ETF 列表中独立筛选，用作“个股不合适时的替代观察”。
2. 列表来源为 AkShare `fund_etf_spot_em()`；先按当日涨跌幅和成交额降序取 `DAILY_ETF_PREFILTER` / 页面 `ETF 预筛数量`。
3. 页面工作台的 ETF 分数为基础分 `0.25 + 当日涨跌幅 / 100 + 成交额相对加分`，再展示前 `DAILY_TOP_ETFS` / 页面 `ETF 展示数量`。
4. 日报会尽量再拉 ETF 历史 K 线，用和个股相同的趋势评分补充 `ret20`、`ret60`、信号和理由；如果历史 K 线失败，则保留当日涨幅和成交额排序结果，并在报告里记录数据提示。
5. 注意：ETF Top 10 不等于 10 个独立方向。很多 ETF 可能跟踪同一行业、主题或指数，比如同一个半导体/人工智能/证券方向会有多只不同基金公司产品。阅读 ETF 候选时应先按名称和跟踪方向归类，实际可能只有 2-3 个主线方向，再从同方向里比较规模、成交额、费率、折溢价和流动性。

## BaoStock 本地预热

BaoStock 的 K 线接口更稳定，但实时扫描强势板块时仍可能需要拉多只成分股日线。建议收盘数据更新后预热本地缓存，并生成当天工作台扫描结果：

```powershell
python -m src.cli update-data --warm-workspace --end 20260618
```

默认预热 Top 板块和每个板块前 `BAOSTOCK_SCAN_MEMBER_LIMIT` 只成分股。预热完成后，页面再次查询会优先命中 `data/cache/market_cache.sqlite3`，速度会明显快于实时逐只拉取。定时任务默认按 18:00 执行，先预热工作台缓存，再生成日报。

## MySQL 历史报告与定时任务

默认 MySQL 配置：

```powershell
$env:MYSQL_HOST="127.0.0.1"
$env:MYSQL_PORT="3306"
$env:MYSQL_USER="root"
$env:MYSQL_PASSWORD="<your-mysql-password>"
$env:MYSQL_DATABASE="astock_strategy"
```

补录已有 HTML：

```powershell
python -m src.daily_job --date 2026-06-16 --html 2026-06-16-report.html --no-notify
```

收盘后生成并入库：

```powershell
python -m src.daily_job
```

每天建议在 A 股收盘后执行，默认脚本按 18:00 设计。脚本会自动判断交易日，周六日和节假日跳过。

安装每天 18:00 自动任务需要管理员权限：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_daily_task.ps1
```

定时任务实际执行 `run_daily_report.bat`，它会优先调用本机接口：

```text
POST http://127.0.0.1:8600/daily-report/run
```

如果接口服务没有启动，脚本会自动降级执行 `python -m src.daily_job`，保证报告仍可生成。

日报默认使用安全模式 `DAILY_REFRESH=false`，避免公开行情接口挂起导致报告生成失败。候选股展示价格会优先使用板块成分中的最新价/涨幅；如需强制刷新历史行情，可设置 `DAILY_REFRESH=true`，但公开接口不稳定时可能明显变慢。

查看任务状态和日志：

```powershell
schtasks /Query /TN AStockTrendDailyReport /V /FO LIST
Get-Content .\logs\daily_report.log -Tail 80
```

通知配置是可选的。未配置时只入库不推送：

```powershell
$env:DINGTALK_WEBHOOK="https://oapi.dingtalk.com/robot/send?access_token=..."
$env:SMTP_HOST="smtp.example.com"
$env:SMTP_PORT="465"
$env:SMTP_USER="your@email.com"
$env:SMTP_PASSWORD="password-or-auth-code"
$env:SMTP_TO="target@email.com"
```

钉钉机器人如果开启关键词校验，请把关键词设为 `time`；每日摘要会自动包含 `time: YYYY-MM-DD HH:mm:ss`。
推送内容会整理当天强势板块、板块内“强势看涨 / 一般看涨 / 观察”候选、ETF 候选和板块变化提醒。

## 后台服务与监控

启动页面：

```powershell
streamlit run app.py
```

启动接口服务：

```powershell
uvicorn api_server:app --host 127.0.0.1 --port 8600
```

常用接口：

```text
GET  /health
GET  /score?kind=stock&code=600519
GET  /score?kind=sector&code=BK1625
GET  /score?kind=etf&code=510300
POST /backtest  # 已停用，返回 410
GET  /reports
GET  /reports/{id}/html
POST /daily-report/run
GET  /monitors
POST /monitors
POST /monitor/run
GET  /monitor/events
```

监控目标可以在 Streamlit 的“监控”页添加，也可以通过接口添加。每日任务生成报告后会自动执行监控，达到条件后写入 `monitor_events`，并在配置了邮箱或钉钉时推送。
