@echo off
cd /d "%~dp0"
title PJN Novedades (cerrar esta ventana cierra la app)

rem Igual que instalar.bat: python o, si no, el lanzador py. "where python"
rem no alcanza: encuentra el alias de la Microsoft Store aunque no haya Python.
set "PY="
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=python"
if not defined PY (
  py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=py -3"
)
if not defined PY (
  echo No encuentro Python 3.10 o mas nuevo. Corre primero instalar.bat
  pause
  exit /b 1
)
%PY% app.py
pause
