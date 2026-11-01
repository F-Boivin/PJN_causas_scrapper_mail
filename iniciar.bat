@echo off
cd /d "%~dp0"
title PJN Novedades (cerrar esta ventana cierra la app)
where python >nul 2>nul
if errorlevel 1 (
  echo No encuentro Python. Corre primero instalar.bat
  pause
  exit /b 1
)
python app.py
pause
