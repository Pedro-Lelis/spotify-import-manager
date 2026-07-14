@echo off
cd /d "%~dp0"
python app.py
if %errorlevel% neq 0 (
    echo.
    echo [ERRO] Falha ao iniciar o aplicativo.
    echo Certifique-se de ter executado "instalar.bat" primeiro.
    pause
)
