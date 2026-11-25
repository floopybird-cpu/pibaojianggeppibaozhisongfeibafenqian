$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    if (-not (Test-Path -LiteralPath "$PSScriptRoot\runtime\python.exe")) {
        $pythonCommand = Get-Command python -ErrorAction Stop
        $basePath = & $pythonCommand.Source -c "import sys; assert sys.version_info[:3] == (3,13,16), 'Python 3.13.16 x64 required'; print(sys.base_prefix)"
        if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.13.16 x64 from python.org before setup.' }
        New-Item -ItemType Directory -Path "$PSScriptRoot\runtime" -Force | Out-Null
        & $pythonCommand.Source -c "import shutil,sys; from pathlib import Path; src=Path(sys.argv[1]); dst=Path(sys.argv[2]); shutil.copytree(src,dst,ignore=shutil.ignore_patterns('__pycache__','include','libs','tcl'),dirs_exist_ok=True)" $basePath "$PSScriptRoot\runtime"
    }
    & "$PSScriptRoot\runtime\python.exe" -m pip install --target "$PSScriptRoot\runtime\Lib\site-packages" --upgrade -r "$PSScriptRoot\requirements.lock"
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    & "$PSScriptRoot\tools\install_typst.ps1"
    Write-Output 'Setup ready. Run launch.cmd.'
}
finally { Pop-Location }
