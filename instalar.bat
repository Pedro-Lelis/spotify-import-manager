@echo off
title Spotify Import Manager — Instalacao
cd /d "%~dp0"
echo.
echo ============================================================
echo   Spotify Import Manager — Instalacao de Dependencias
echo ============================================================
echo.

:: Detecta um Python compativel (3.10 a 3.13; a 3.14 ainda nao e suportada
:: por algumas libs como librosa/numba)
call "%~dp0find_python.bat" install

if not defined PYCMD (
    echo [ERRO] Nenhum Python compativel encontrado.
    echo.
    echo Instale o Python 3.10, 3.11, 3.12 ou 3.13 em: https://python.org/downloads
    echo IMPORTANTE: marque "Add Python to PATH" durante a instalacao.
    echo Obs: a versao mais nova (3.14) ainda nao e suportada por algumas bibliotecas.
    echo.
    pause
    exit /b 1
)

echo Usando o Python:
%PYCMD% --version
echo.

:: Instala dependencias
echo Instalando dependencias (pode levar alguns minutos)...
echo.
%PYCMD% -m pip install --upgrade pip
%PYCMD% -m pip install -r requirements.txt

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
