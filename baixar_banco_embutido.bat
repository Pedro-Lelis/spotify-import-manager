@echo off
title Spotify Import Manager - Baixar PostgreSQL embutido
cd /d "%~dp0"
echo.
echo ============================================================
echo   Baixar o PostgreSQL embutido (~320 MB)
echo ============================================================
echo.
echo Isso permite usar o modo "Banco embutido" SEM instalar o
echo PostgreSQL no sistema. Os arquivos ficam na pasta pgsql\.
echo.

if exist "%~dp0pgsql\bin\initdb.exe" goto :jatem

set "PGURL=https://get.enterprisedb.com/postgresql/postgresql-18.0-1-windows-x64-binaries.zip"
set "ZIP=%TEMP%\pg_embedded_sim.zip"

echo Baixando (pode levar alguns minutos, dependendo da internet)...
echo.
curl -L --fail -o "%ZIP%" "%PGURL%"
if %errorlevel% neq 0 goto :falhadl
if not exist "%ZIP%" goto :falhadl

echo.
echo Extraindo (pode levar 1-2 minutos)...
tar -xf "%ZIP%" -C "%~dp0." 2>nul
if exist "%~dp0pgsql\bin\initdb.exe" goto :ok

echo tar indisponivel, tentando com PowerShell...
powershell -NoProfile -Command "Expand-Archive -Path '%ZIP%' -DestinationPath '%~dp0.' -Force"
if exist "%~dp0pgsql\bin\initdb.exe" goto :ok
goto :falhaext

:ok
del "%ZIP%" >nul 2>&1
echo.
echo ============================================================
echo   PostgreSQL embutido instalado com sucesso!
echo ============================================================
echo.
echo Agora, no aplicativo (abrir_app.bat):
echo   1. Aba Configuracoes, escolha "Banco embutido"
echo   2. Defina uma senha para o banco
echo   3. Clique em "Preparar banco"
echo.
pause
exit /b 0

:jatem
echo O PostgreSQL embutido ja esta instalado (pasta pgsql).
echo.
pause
exit /b 0

:falhadl
echo.
echo [ERRO] Falha ao baixar os binarios.
echo Verifique sua conexao com a internet e tente novamente.
echo.
pause
exit /b 1

:falhaext
echo.
echo [ERRO] Falha ao extrair os binarios.
echo Requer Windows 10 ou mais novo (tar/PowerShell).
echo.
pause
exit /b 1
