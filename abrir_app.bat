@echo off
cd /d "%~dp0"

call "%~dp0find_python.bat" run
if not defined PYCMD goto :nopy

echo Iniciando com: %PYCMD%
echo.
%PYCMD% app.py

echo.
echo ------------------------------------------------------------
echo O aplicativo foi encerrado.
echo Se houve algum erro acima, me mostre a mensagem.
pause
exit /b 0

:nopy
echo.
echo [ERRO] Nenhum Python com as dependencias foi encontrado.
echo Rode o instalar.bat primeiro. Precisa de Python 3.10 a 3.13.
echo.
pause
exit /b 1
