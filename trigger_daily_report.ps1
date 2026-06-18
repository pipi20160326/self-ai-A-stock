param(
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Continue"
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $project

$logDir = Join-Path $project "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logPath = Join-Path $logDir "daily_report.log"

function Resolve-Python {
    if ($PythonExe -and (Test-Path $PythonExe)) {
        return $PythonExe
    }
    if ($env:ASTOCK_PYTHON -and (Test-Path $env:ASTOCK_PYTHON)) {
        return $env:ASTOCK_PYTHON
    }
    $venvPython = Join-Path $project ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        return $venvPython
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }
    throw "Python executable not found. Set ASTOCK_PYTHON or pass -PythonExe."
}

$exitCode = 0
Start-Transcript -Path $logPath -Append | Out-Null
try {
    $api = "http://127.0.0.1:8600/daily-report/run"
    $body = @{
        force = $false
        notify = $true
    } | ConvertTo-Json -Compress

    $python = Resolve-Python
    $lookbackDays = 240
    if ($env:DAILY_LOOKBACK_DAYS) {
        $parsedLookback = 0
        if ([int]::TryParse($env:DAILY_LOOKBACK_DAYS, [ref]$parsedLookback) -and $parsedLookback -gt 90) {
            $lookbackDays = $parsedLookback
        }
    }
    $topSectors = if ($env:SCAN_TOP_SECTORS) { $env:SCAN_TOP_SECTORS } else { "5" }
    $stocksPerSector = if ($env:SCAN_TOP_STOCKS_PER_SECTOR) { $env:SCAN_TOP_STOCKS_PER_SECTOR } else { "3" }
    $memberLimit = if ($env:BAOSTOCK_SCAN_MEMBER_LIMIT) { $env:BAOSTOCK_SCAN_MEMBER_LIMIT } else { "8" }
    $today = Get-Date -Format "yyyyMMdd"
    $start = (Get-Date).AddDays(-1 * $lookbackDays).ToString("yyyyMMdd")
    Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] project: $project"
    Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] python: $python"
    Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] warm workspace cache: $start -> $today"

    & $python -m src.cli update-data --warm-workspace --start $start --end $today --top-sectors $topSectors --stocks-per-sector $stocksPerSector --member-limit $memberLimit
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Workspace warmup failed with exit code $LASTEXITCODE, continue daily report generation."
    }

    Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] trigger daily report via API: $api"
    try {
        $result = Invoke-RestMethod -Uri $api -Method Post -ContentType "application/json" -Body $body -TimeoutSec 30
        $result | ConvertTo-Json -Depth 5
        $exitCode = 0
    } catch {
        Write-Host "API trigger failed, fallback to python -m src.daily_job"
        Write-Host $_
        & $python -m src.daily_job
        $exitCode = $LASTEXITCODE
    }
} catch {
    Write-Host "Daily report task failed."
    Write-Host $_
    $exitCode = 1
} finally {
    Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] daily task finished with exit code $exitCode"
    Stop-Transcript | Out-Null
}

exit $exitCode
