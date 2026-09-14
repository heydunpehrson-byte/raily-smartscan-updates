$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $project '.venv-brain\Scripts\python.exe'
Set-Location $project
& "$project\.venv-brain\Scripts\pythonw.exe" -m raily.desktop.launcher
