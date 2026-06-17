# A 股板块趋势策略扫描与回测工具

一个“先板块、后个股”的日线趋势扫描与回测工具。默认使用 AkShare，配置 `TUSHARE_TOKEN` 后可预留切换 Tushare 数据源。

> 仅用于研究和学习，不构成投资建议。

## 无数据库手动版

当前分支提供无数据库入口，不连接 MySQL，不入库，不定时，只手动生成本地 HTML 报告：

```powershell
streamlit run app_manual.py --server.port 8502
uvicorn api_manual:app --host 127.0.0.1 --port 8601
```

接口：

```text
POST /daily-report/run
GET  /daily-report/html
```

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
python -m src.cli backtest --start 20240101 --end 20241231
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
$env:DAILY_PREFILTER="40"
$env:DAILY_TOP_SECTORS="12"
$env:DAILY_STOCKS_PER_SECTOR="3"
$env:DAILY_MEMBER_LIMIT="20"
$env:DAILY_ETF_PREFILTER="30"
$env:DAILY_TOP_ETFS="10"
```

也可以复制 `.env.example` 为 `.env`，定时任务和页面启动时会自动读取 `.env`。`.env` 已被 git 忽略，不会提交到仓库。

## 输出

- `data/cache/market_cache.sqlite3`：本地 SQLite 缓存。
- `reports/daily/YYYY-MM-DD_scan.csv`：每日扫描清单。
- `reports/backtest/`：回测净值、交易明细、指标。

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

每天建议在 A 股收盘后执行，默认脚本按 16:00 设计。脚本会自动判断交易日，周六日和节假日跳过。

安装每天 16:00 自动任务需要管理员权限：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_daily_task.ps1
```

定时任务实际执行 `run_daily_report.bat`，它会优先调用本机接口：

```text
POST http://127.0.0.1:8600/daily-report/run
```

如果接口服务没有启动，脚本会自动降级执行 `python -m src.daily_job`，保证报告仍可生成。

日报默认强制刷新行情数据，不复用本地缓存，避免开盘价、收盘价、当日涨幅因缓存滞后而不准。命令行静态报告如需调试缓存，可显式加 `--use-cache`。

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
POST /backtest
GET  /reports
GET  /reports/{id}/html
POST /daily-report/run
GET  /monitors
POST /monitors
POST /monitor/run
GET  /monitor/events
```

监控目标可以在 Streamlit 的“监控”页添加，也可以通过接口添加。每日任务生成报告后会自动执行监控，达到条件后写入 `monitor_events`，并在配置了邮箱或钉钉时推送。
