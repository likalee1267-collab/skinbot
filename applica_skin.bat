@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
set "PYW="
rem 1) il Python scelto da installa.bat (dove sono installate le librerie)
if exist "%~dp0.python" (
  set /p PYW=<"%~dp0.python"
  if not exist "!PYW!" set "PYW="
)
rem 2) il pythonw nel PATH, saltando l'alias dello Store (WindowsApps)
if not defined PYW for /f "delims=" %%p in ('where pythonw 2^>nul') do (
  if not defined PYW (
    set "CAND=%%p"
    if /i "!CAND:WindowsApps=!"=="!CAND!" set "PYW=!CAND!"
  )
)
rem 3) le cartelle di installazione standard
if not defined PYW for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%d\pythonw.exe" set "PYW=%%d\pythonw.exe"
if not defined PYW for /d %%d in ("%ProgramFiles%\Python3*") do if exist "%%d\pythonw.exe" set "PYW=%%d\pythonw.exe"
if not defined PYW (
  echo Python non trovato. Lancia prima installa.bat
  pause
  exit /b 1
)
start "" "!PYW!" skinbot_gui.py
