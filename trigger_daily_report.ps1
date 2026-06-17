$ErrorActionPreference = "Continue"
$api = "http://127.0.0.1:8600/daily-report/run"
$body = @{
    force = $false
    notify = $true
} | ConvertTo-Json -Compress

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
