@echo off
title Spotify Import Manager - Desinstalacao
cd /d "%~dp0"
echo.
echo ============================================================
echo   Spotify Import Manager - Desinstalacao de Dependencias
echo ============================================================
echo.
echo Este script remove apenas as bibliotecas Python instaladas
echo pelo Spotify Import Manager. O Python e o PostgreSQL nao serao afetados.
echo.
set /p CONFIRMA="Deseja continuar? (S/N): "
if /i not "%CONFIRMA%"=="S" goto :cancel

call "%~dp0find_python.bat" run
if not defined PYCMD goto :nopy

echo.
echo Removendo dependencias com: %PYCMD%
echo.
%PYCMD% -m pip uninstall -y psycopg2-binary requests librosa pyloudnorm numpy scipy sshtunnel paramiko

echo.
echo Dependencias removidas.
echo A pasta do aplicativo pode ser deletada manualmente.
echo.
pause
exit /b 0

:cancel
echo Operacao cancelada.
echo.
pause
exit /b 0

:nopy
echo Nao encontrei um Python com as dependencias instaladas (nada a remover).
echo.
pause
exit /b 0
