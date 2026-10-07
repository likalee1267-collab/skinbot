@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title Skinbot - installazione
echo.
echo  === Skinbot: installazione ===
echo.

call :find_python
if not defined PY (
  echo  Python non trovato: lo installo con winget ^(puo' volerci qualche minuto^)...
  winget install -e --id Python.Python.3.11 --scope user --accept-package-agreements --accept-source-agreements
  call :find_python
)
if not defined PY (
  echo.
  echo  Python non e' installato. Installalo da https://www.python.org/downloads/ e rilancia questo file.
  pause
  exit /b 1
)
echo  Python:  !PY!
"!PY!" -m pip install --quiet --disable-pip-version-check pillow numpy
if errorlevel 1 (
  echo  Installazione delle librerie Python fallita. Controlla la connessione e riprova.
  pause
  exit /b 1
)

call :find_blender
if not defined BLENDER (
  echo  Blender non trovato: lo installo con winget ^(serve il permesso di amministratore^)...
  winget install -e --id BlenderFoundation.Blender --accept-package-agreements --accept-source-agreements
  call :find_blender
)
if not defined BLENDER (
  echo.
  echo  Blender non e' installato. Installalo da https://www.blender.org/download/ e rilancia questo file.
  pause
  exit /b 1
)
echo  Blender: !BLENDER!

set "PYW=!PY:python.exe=pythonw.exe!"
if not exist "!PYW!" set "PYW=!PY!"
rem applica_skin.bat usera' lo stesso Python in cui sono appena state installate le librerie
> "%~dp0.python" echo !PYW!
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Skinbot.lnk'); $s.TargetPath='!PYW!'; $s.Arguments='skinbot_gui.py'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='!PY!,0'; $s.Description='Skinbot - skin, rampa e cielo abbinati'; $s.Save()"
if errorlevel 1 (
  echo  Collegamento sul desktop non creato: apri Skinbot con applica_skin.bat.
) else (
  echo  Collegamento "Skinbot" creato sul desktop.
)
echo.
echo  Installazione completata. Ricorda: UEFN aperto sulla mappa e FortnitePorting installato.
echo  Apro Skinbot...
start "" "!PYW!" "%~dp0skinbot_gui.py"
"%SystemRoot%\System32\timeout.exe" /t 3 /nobreak >nul
exit /b 0

:find_python
set "PY="
for /f "delims=" %%p in ('where python 2^>nul') do (
  if not defined PY (
    set "CAND=%%p"
    rem l'alias dello Store (WindowsApps) non e' un Python vero
    if /i "!CAND:WindowsApps=!"=="!CAND!" set "PY=!CAND!"
  )
)
if defined PY (
  "!PY!" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul || set "PY="
)
if not defined PY for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%d\python.exe" set "PY=%%d\python.exe"
if not defined PY for /d %%d in ("%ProgramFiles%\Python3*") do if exist "%%d\python.exe" set "PY=%%d\python.exe"
exit /b 0

:find_blender
set "BLENDER="
for /d %%d in ("%ProgramFiles%\Blender Foundation\Blender*") do if exist "%%d\blender.exe" set "BLENDER=%%d\blender.exe"
exit /b 0
