$ErrorActionPreference = "Stop"

python -m pip install -r requirements.txt

python -m PyInstaller `
  --noconfirm `
  --clean `
  --name AStockTrendScanner `
  --onedir `
  --collect-all streamlit `
  --collect-all altair `
  --collect-all plotly `
  --collect-all akshare `
  --collect-all pyarrow `
  --hidden-import streamlit.web.cli `
  --add-data "app.py;." `
  --add-data "src;src" `
  --add-data "data;data" `
  run_app.py

if (Test-Path "dist\AStockTrendScanner\data") {
  Remove-Item -Recurse -Force "dist\AStockTrendScanner\data"
}
Copy-Item -Recurse "data" "dist\AStockTrendScanner\data"

if (-not (Test-Path "dist\AStockTrendScanner\reports")) {
  New-Item -ItemType Directory -Force "dist\AStockTrendScanner\reports" | Out-Null
}

Write-Host "Built: dist\AStockTrendScanner\AStockTrendScanner.exe"
