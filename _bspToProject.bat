@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PY_SCRIPT=%~dp0Tool_bspToProject\bspToProject.py"

echo ================================================================
echo  bspToProject - sync bsp .c files to Keil / EIDE projects
echo ================================================================
echo.

if not exist "%PY_SCRIPT%" (
    echo [ERROR] Script not found:
    echo         %PY_SCRIPT%
    echo.
    pause
    exit /b 1
)

set "PY_CMD="
py -3 -c "import sys; sys.exit(0 if sys.version_info[0]==3 else 1)" >nul 2>nul
if not errorlevel 1 set "PY_CMD=py -3"
if not defined PY_CMD (
    python -c "import sys; sys.exit(0 if sys.version_info[0]==3 else 1)" >nul 2>nul
    if not errorlevel 1 set "PY_CMD=python"
)
if not defined PY_CMD (
    echo [ERROR] Python 3 was not found.
    echo         Install Python 3 and enable "Add python.exe to PATH".
    echo.
    pause
    exit /b 1
)

echo [Python] %PY_CMD%
echo [Script] %PY_SCRIPT%
echo.
%PY_CMD% "%PY_SCRIPT%" %*
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" (
    echo [EXIT] %RC%
) else (
    echo [DONE]
)
echo.
pause
exit /b %RC%
