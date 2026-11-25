$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
$runtimeDirectory = Join-Path $projectDirectory 'runtime\typst'
$executable = Join-Path $runtimeDirectory 'typst-x86_64-pc-windows-msvc\typst.exe'
if (Test-Path -LiteralPath $executable) {
    $versionText = & $executable --version
    if ($versionText -notlike 'typst 0.15.1 *') { throw 'Typst version differs from the pinned runtime.' }
    Write-Output $versionText
    return
}
New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
$archivePath = Join-Path $runtimeDirectory 'upstream-v0.15.1.zip'
Invoke-WebRequest -Uri 'https://github.com/typst/typst/releases/download/v0.15.1/typst-x86_64-pc-windows-msvc.zip' -OutFile $archivePath
$expectedDigest = '19ce3551153c2fe7ee9fa2f95208310c8f4d3209fedb699e0333faf8913f6736'
if ((Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedDigest) { throw 'Official Typst archive hash mismatch; nothing extracted.' }
Expand-Archive -LiteralPath $archivePath -DestinationPath $runtimeDirectory
& $executable --version
if ($LASTEXITCODE -ne 0) { throw 'Typst runtime verification failed.' }
