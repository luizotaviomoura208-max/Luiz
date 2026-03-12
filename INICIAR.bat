@echo off
title Sistema de Comandas - Servidor
color 0A

:: Muda para a pasta onde o .bat está (isso resolve o problema)
cd /d "%~dp0"

echo.
echo  ============================================
echo    SISTEMA DE COMANDAS - INICIANDO...
echo  ============================================
echo.
echo  Pasta: %~dp0
echo.

:: Tenta encontrar o Python
set PYTHON_EXE=

:: Testa python no PATH
python --version >nul 2>&1
if %errorlevel%==0 (
    set PYTHON_EXE=python
    goto :encontrou
)

:: Testa py (launcher do Windows)
py --version >nul 2>&1
if %errorlevel%==0 (
    set PYTHON_EXE=py
    goto :encontrou
)

:: Procura manualmente nas pastas comuns
for %%V in (313 312 311 310 39 38) do (
    if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" (
        set PYTHON_EXE="%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
        goto :encontrou
    )
    if exist "%USERPROFILE%\AppData\Local\Programs\Python\Python%%V\python.exe" (
        set PYTHON_EXE="%USERPROFILE%\AppData\Local\Programs\Python\Python%%V\python.exe"
        goto :encontrou
    )
    if exist "C:\Python%%V\python.exe" (
        set PYTHON_EXE="C:\Python%%V\python.exe"
        goto :encontrou
    )
)

:: Nao encontrou
echo  ERRO: Python nao encontrado!
echo.
echo  Instale em: https://python.org/downloads
echo  IMPORTANTE: marque "Add Python to PATH" durante a instalacao!
echo.
pause
exit

:encontrou
echo  Python encontrado: %PYTHON_EXE%
echo  Iniciando servidor...
echo.
echo  Acesse: http://localhost:8080
echo  NAO feche esta janela!
echo.

start "" "http://localhost:8080"
timeout /t 2 /nobreak >nul
%PYTHON_EXE% "%~dp0servidor.py"

pause
