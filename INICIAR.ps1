# Sistema de Comandas - Iniciador
Set-Location $PSScriptRoot
Write-Host ""
Write-Host "  ============================================" -ForegroundColor Green
Write-Host "    SISTEMA DE COMANDAS - INICIANDO..." -ForegroundColor Green  
Write-Host "  ============================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Abrindo navegador em http://localhost:8080" -ForegroundColor Cyan
Write-Host "  NAO feche esta janela!" -ForegroundColor Yellow
Write-Host ""

# Abre navegador após 2 segundos em background
Start-Job -ScriptBlock {
    Start-Sleep 2
    Start-Process "http://localhost:8080"
} | Out-Null

# Inicia o servidor
python servidor.py
