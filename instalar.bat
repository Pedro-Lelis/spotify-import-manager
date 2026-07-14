@echo off
title Spotify Import Manager — Instalacao
echo.
echo ============================================================
echo   Spotify Import Manager — Instalacao de Dependencias
echo ============================================================
echo.

:: Verifica se Python esta instalado
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERRO] Python nao encontrado!
    echo.
    echo Instale Python 3.10, 3.11 ou 3.12 em: https://python.org/downloads
    echo.
    echo IMPORTANTE: marque "Add Python to PATH" durante a instalacao.
    pause
    exit /b 1
)

echo Python encontrado:
python --version
echo.

:: Instala dependencias
echo Instalando dependencias (pode levar alguns minutos)...
echo.
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if %errorlevel% neq 0 (
    echo.
    echo [ERRO] Falha ao instalar dependencias.
    echo Verifique sua conexao com a internet e tente novamente.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Instalacao concluida com sucesso!
echo ============================================================
echo.
echo Proximos passos:
echo   1. Instale o PostgreSQL (https://www.postgresql.org/download/)
echo   2. Crie o banco de dados com o arquivo setup_banco.sql
echo   3. Execute "abrir_app.bat" para iniciar o aplicativo
echo   4. Configure a conexao na aba "Configuracoes"
echo.
pause
