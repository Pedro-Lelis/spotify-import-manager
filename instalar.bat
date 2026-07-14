@echo off
title Spotify Import Manager - Instalacao
cd /d "%~dp0"
echo.
echo ============================================================
echo   Spotify Import Manager - Instalacao de Dependencias
echo ============================================================
echo.

call "%~dp0find_python.bat" install
if not defined PYCMD goto :nopy

echo Usando o Python:
%PYCMD% --version
echo.
echo Instalando dependencias (pode levar alguns minutos)...
echo.
%PYCMD% -m pip install --upgrade pip
%PYCMD% -m pip install -r requirements.txt
if %errorlevel% neq 0 goto :falha

echo.
echo ============================================================
echo   Instalacao concluida com sucesso!
echo ============================================================
echo.
echo Proximos passos:
echo   1. Instale o PostgreSQL em https://www.postgresql.org/download/
echo   2. Crie o banco de dados com o arquivo setup_banco.sql
echo   3. Execute abrir_app.bat para iniciar o aplicativo
echo   4. Na aba Configuracoes, escolha "Conectar a um PostgreSQL existente"
echo.
pause
exit /b 0

:nopy
echo [ERRO] Nenhum Python compativel encontrado.
echo.
echo Instale o Python 3.10, 3.11, 3.12 ou 3.13 em https://python.org/downloads
echo IMPORTANTE: marque "Add Python to PATH" durante a instalacao.
echo Obs: a versao 3.14 ainda nao e suportada por algumas bibliotecas.
echo.
pause
exit /b 1

:falha
echo.
echo [ERRO] Falha ao instalar dependencias.
echo Verifique sua conexao com a internet e tente novamente.
echo.
pause
exit /b 1
