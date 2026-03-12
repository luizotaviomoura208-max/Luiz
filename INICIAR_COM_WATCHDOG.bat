@echo off
title Garfio — Watchdog
color 0A
cd /d "%~dp0"

echo.
echo  =====================================================
echo   Garfio — Monitor Automatico (Watchdog)
echo  =====================================================
echo.
echo  O Watchdog vai:
echo    1. Iniciar o servidor
echo    2. Aguardar ficar pronto
echo    3. Abrir o navegador automaticamente
echo    4. Monitorar e reiniciar se necessario
echo.
echo  NAO feche esta janela!
echo  =====================================================
echo.

:: Encontra Python
set PYTHON_EXE=
python --version >nul 2>&1
if %errorlevel%==0 ( set PYTHON_EXE=python & goto :ok )
py --version >nul 2>&1
if %errorlevel%==0 ( set PYTHON_EXE=py & goto :ok )
for %%V in (313 312 311 310 39 38) do (
    if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" (
        set PYTHON_EXE="%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
        goto :ok
    )
)
echo  ERRO: Python nao encontrado!
echo  Instale em: https://python.org/downloads
pause & exit /b

:ok
echo  Python: %PYTHON_EXE%
echo.
%PYTHON_EXE% "%~dp0watchdog.py"

echo.
echo  Watchdog encerrado.
pause
