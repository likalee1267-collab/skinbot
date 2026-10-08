@echo off
setlocal
title Installazione Skinbot
set "URL=https://raw.githubusercontent.com/likalee1267-collab/skinbot/main/"
set "DEST=%LOCALAPPDATA%\Skinbot"
echo.
echo  Scarico Skinbot in %DEST% ...
echo.
if not exist "%DEST%" mkdir "%DEST%"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue';" ^
  "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12;" ^
  "$u = '%URL%'; $d = '%DEST%'; $n = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds();" ^
  "$m = Invoke-RestMethod ($u + 'manifest.json?nocache=' + $n);" ^
  "foreach ($f in $m.files.PSObject.Properties.Name) {" ^
  "  $p = Join-Path $d $f; New-Item -ItemType Directory -Force (Split-Path $p) | Out-Null;" ^
  "  Invoke-WebRequest ($u + $f + '?nocache=' + $n) -OutFile $p -UseBasicParsing;" ^
  "  $h = (Get-FileHash $p -Algorithm SHA256).Hash.ToLower();" ^
  "  if ($h -ne $m.files.$f.sha256) { throw ('File corrotto: ' + $f) } };" ^
  "Write-Host ('  Scaricata la versione ' + $m.version)"
if errorlevel 1 (
  echo.
  echo  Download fallito. Controlla la connessione e riprova.
  pause
  exit /b 1
)
call "%DEST%\installa.bat"
