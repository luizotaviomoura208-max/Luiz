@echo off
title Garfio — Watchdog (Monitor Automatico)
color 0A
echo.
echo  =====================================================
echo   Garfio — Watchdog
echo   Monitor automatico do servidor
echo  =====================================================
echo.
echo  Mantenha esta janela ABERTA enquanto o sistema
echo  estiver em uso. Ela reinicia o servidor sozinha
echo  caso ocorra algum problema.
echo.
echo  Logs salvos em: data\watchdog.log
echo  Pressione CTRL+C para parar.
echo.
echo  =====================================================
echo.

cd /d "%~dp0"
python watchdog.py
if %errorlevel% neq 0 (
    echo.
    echo  ERRO: Python nao encontrado ou falha ao executar.
    echo  Verifique se o Python esta instalado.
    pause
)
