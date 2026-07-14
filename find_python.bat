@echo off
REM ============================================================
REM  find_python.bat  -  detecta um Python adequado e define PYCMD
REM
REM  Uso:  call find_python.bat run       (exige as dependencias instaladas)
REM        call find_python.bat install   (exige versao suportada 3.10-3.13)
REM
REM  Resultado: variavel de ambiente PYCMD (ex: "py -3.10" ou "python").
REM  Fica vazia se nada adequado for encontrado.
REM ============================================================
set "PYCMD="
set "MODE=%~1"
if "%MODE%"=="" set "MODE=run"

call :try "py -3.13"
if defined PYCMD exit /b 0
call :try "py -3.12"
if defined PYCMD exit /b 0
call :try "py -3.11"
if defined PYCMD exit /b 0
call :try "py -3.10"
if defined PYCMD exit /b 0
call :try "python"
exit /b 0

:try
set "CAND=%~1"
REM o interpretador existe? (neq 0 pega tambem os codigos negativos do py novo)
%CAND% --version >nul 2>&1
if %errorlevel% neq 0 exit /b 0

if /i "%MODE%"=="run" (
    %CAND% -c "import psycopg2, sshtunnel" >nul 2>&1
) else (
    %CAND% -c "import sys; sys.exit(0 if (3,10)<=sys.version_info[:2]<(3,14) else 1)" >nul 2>&1
)
if %errorlevel% neq 0 exit /b 0

set "PYCMD=%CAND%"
exit /b 0
