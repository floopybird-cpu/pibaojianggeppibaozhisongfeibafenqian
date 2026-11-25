@echo off
chcp 65001 >nul
setlocal
pushd "%~dp0"
if not exist "%~dp0runtime\python.exe" (
  echo Private Python runtime missing. Run setup.ps1 first.
  pause
  exit /b 1
)
start "" /D "%~dp0" "%~dp0runtime\pythonw.exe" -E -s -X utf8 "%~dp0run.py" %*
popd
endlocal
