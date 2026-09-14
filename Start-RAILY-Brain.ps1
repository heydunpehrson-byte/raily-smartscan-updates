$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $project '.venv-brain\Scripts\python.exe'
$health = try { Invoke-RestMethod 'http://127.0.0.1:8765/health' -TimeoutSec 2 } catch { $null }
if ($health -and $health.service -eq 'RAILY Dispatch Brain') { Write-Host 'RAILY Brain is already running.'; exit 0 }
Set-Location $project
$env:RAILY_TESSERACT_CMD = 'C:\Program Files\Tesseract-OCR\tesseract.exe'
$logs = Join-Path $env:USERPROFILE 'Documents\RAILY-Brain\Logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$worker = Start-Process -FilePath $python -ArgumentList '-m','raily.brain.worker' -WorkingDirectory $project -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs 'worker.log') -RedirectStandardError (Join-Path $logs 'worker-error.log') -PassThru
try {
    & $python -m uvicorn raily.brain.app:app --host 127.0.0.1 --port 8765
}
finally {
    if ($worker -and -not $worker.HasExited) { Stop-Process -Id $worker.Id -ErrorAction SilentlyContinue }
}
