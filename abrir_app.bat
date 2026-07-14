@echo off
cd /d "%~dp0"

:: Detecta um Python que tenha as dependencias instaladas
call "%~dp0find_python.bat" run

if not defined PYCMD (
    echo.
    echo [ERRO] Nenhum Python com as dependencias foi encontrado.
    echo Rode "instalar.bat" primeiro (precisa de Python 3.10 a 3.13).
    echo.
    pause
    exit /b 1
)

echo Iniciando com: %PYCMD%
%PYCMD% app.py

if %errorlevel% neq 0 (
    echo.
    echo [ERRO] Falha ao iniciar o aplicativo.
    echo Certifique-se de ter executado "instalar.bat" primeiro.
    pause
)
