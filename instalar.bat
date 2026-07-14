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
echo Proximos passos - escolha um caminho de banco:
echo.
echo   Opcao A (recomendada, sem instalar PostgreSQL):
echo     1. Rode baixar_banco_embutido.bat
echo     2. Rode abrir_app.bat e use "Banco embutido"
echo.
echo   Opcao B (usar um PostgreSQL que voce ja tem):
echo     1. Instale o PostgreSQL e crie o banco com setup_banco.sql
echo     2. Rode abrir_app.bat e use "Conectar a um PostgreSQL existente"
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
