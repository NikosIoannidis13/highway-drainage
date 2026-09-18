@echo off
setlocal
pushd "%~dp0"
if errorlevel 1 (
    echo Could not open the application folder.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo The project virtual environment is missing.
    echo Expected: "%~dp0.venv\Scripts\python.exe"
    popd
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m highway_drainage
set "app_exit_code=%errorlevel%"
popd
if not "%app_exit_code%"=="0" (
    echo.
    echo Application exited with error code %app_exit_code%.
    pause
)
exit /b %app_exit_code%
