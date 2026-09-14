$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $project '.venv-brain\Scripts\python.exe'
$health = try { Invoke-RestMethod 'http://127.0.0.1:8765/health' -TimeoutSec 2 } catch { $null }
if ($health -and $health.service -eq 'RAILY Dispatch Brain') { Write-Host 'RAILY Brain is already running.'; exit 0 }
Set-Location $project
& $python -m uvicorn raily.brain.app:app --host 127.0.0.1 --port 8765
