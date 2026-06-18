$ErrorActionPreference = "Continue"
$api = "http://127.0.0.1:8600/daily-report/run"
$body = @{
    force = $false
    notify = $true
} | ConvertTo-Json -Compress

$today = Get-Date -Format "yyyyMMdd"
Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] warm workspace cache: $today"
python -m src.cli update-data --warm-workspace --end $today
if ($LASTEXITCODE -ne 0) {
    Write-Host "Workspace warmup failed, continue daily report generation."
}

Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] trigger daily report via API: $api"
try {
    $result = Invoke-RestMethod -Uri $api -Method Post -ContentType "application/json" -Body $body -TimeoutSec 30
    $result | ConvertTo-Json -Depth 5
    exit 0
} catch {
    Write-Host "API trigger failed, fallback to python -m src.daily_job"
    Write-Host $_
    python -m src.daily_job
    exit $LASTEXITCODE
}
