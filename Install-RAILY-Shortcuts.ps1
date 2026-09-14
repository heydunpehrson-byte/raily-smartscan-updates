# Install per-user desktop shortcuts only. No execution-policy changes.
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonw = Join-Path $project '.venv-brain\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) { throw 'RAILY Python environment not found.' }
$desktop = [Environment]::GetFolderPath('Desktop')
$shell = New-Object -ComObject WScript.Shell
foreach ($item in @(@('RAILY','Start-RAILY.pyw','raily.ico'), @('RAILY Diagnostics','RAILY-Diagnostics.pyw','raily-diagnostics.ico'))) {
    $link = $shell.CreateShortcut((Join-Path $desktop ($item[0] + '.lnk')))
    $link.TargetPath = $pythonw
    $link.Arguments = '"' + (Join-Path $project $item[1]) + '"'
    $link.WorkingDirectory = $project
    $link.IconLocation = (Join-Path $project ('assets\' + $item[2])) + ',0'
    $link.Description = $item[0] + ' — Local Dispatch Center'
    $link.Save()
    Write-Output (Join-Path $desktop ($item[0] + '.lnk'))
}
