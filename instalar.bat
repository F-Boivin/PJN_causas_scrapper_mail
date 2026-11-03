@echo off
setlocal
cd /d "%~dp0"
title PJN Novedades - instalacion
echo.
echo  ==============================================
echo    PJN Novedades - instalacion (una sola vez)
echo  ==============================================
echo.

rem --- 1. Python 3.10 o mas nuevo ---
set "PY="
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=python"
if not defined PY (
  py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=py -3"
)
if not defined PY goto :sin_python
echo [1/3] Python encontrado:
%PY% --version
echo.

rem --- 2. Librerias ---
echo [2/3] Instalando las librerias (puede tardar un par de minutos)...
%PY% -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :error
echo.

rem --- 3. Navegador del scraper ---
echo [3/3] Instalando el navegador que usa el scraper (Chromium, ~150 MB)...
%PY% -m playwright install chromium
if errorlevel 1 goto :error
echo.

rem --- Acceso directo opcional ---
set /p ATAJO="Crear un acceso directo 'PJN Novedades' en el Escritorio? (S/N): "
if /i "%ATAJO%"=="S" (
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'PJN Novedades.lnk')); $s.TargetPath='%~dp0iniciar.bat'; $s.WorkingDirectory='%~dp0'; $s.Save()"
  echo Acceso directo creado.
)

echo.
echo  Listo. Para usar la app: doble click en iniciar.bat
echo  (o en el acceso directo del Escritorio).
echo.
pause
exit /b 0

:sin_python
echo  No encontre Python 3.10 o mas nuevo en esta PC.
echo.
echo  Opcion A (recomendada): instalarlo ahora con winget.
echo  Opcion B: bajarlo de https://www.python.org/downloads/ y, al instalar,
echo            TILDAR "Add python.exe to PATH".
echo.
set /p INST="Instalar Python ahora con winget? (S/N): "
if /i "%INST%"=="S" (
  winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
  echo.
  echo  Si se instalo bien, CERRA esta ventana y volve a abrir instalar.bat
  echo  ^(Windows necesita una ventana nueva para encontrar Python^).
)
pause
exit /b 1

:error
echo.
echo  Algo fallo en la instalacion. Copia el mensaje de arriba y pedile ayuda
echo  a un asistente de IA (ver "Instalacion con ayuda de un asistente de IA"
echo  en el README) o a quien te paso la app.
pause
exit /b 1
