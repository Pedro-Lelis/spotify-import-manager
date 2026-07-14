@echo off
title Spotify Import Manager — Desinstalacao
echo.
echo ============================================================
echo   Spotify Import Manager — Desinstalacao de Dependencias
echo ============================================================
echo.
echo Este script remove apenas as bibliotecas Python instaladas
echo pelo Spotify Import Manager. O Python e o PostgreSQL
echo nao serao afetados.
echo.
set /p CONFIRMA="Deseja continuar? (S/N): "
if /i "%CONFIRMA%" neq "S" (
    echo Operacao cancelada.
    pause
    exit /b 0
)

echo.
echo Removendo dependencias...
echo.
python -m pip uninstall -y psycopg2-binary requests librosa pyloudnorm numpy scipy

if %errorlevel% neq 0 (
    echo.
    echo [AVISO] Algumas bibliotecas podem nao ter sido removidas.
    echo Verifique manualmente com: python -m pip list
) else (
    echo.
    echo Dependencias removidas com sucesso.
)

echo.
echo A pasta do aplicativo pode ser deletada manualmente.
echo.
pause
