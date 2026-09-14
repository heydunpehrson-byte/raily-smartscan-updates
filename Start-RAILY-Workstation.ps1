$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $project '.venv-brain\Scripts\python.exe'
Set-Location $project
& $python -m raily.client.workstation_gui
