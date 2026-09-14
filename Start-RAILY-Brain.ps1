$project = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $project
$env:RAILY_TESSERACT_CMD = 'C:\Program Files\Tesseract-OCR\tesseract.exe'
& "$project\.venv-brain\Scripts\python.exe" -m raily.desktop.service brain
