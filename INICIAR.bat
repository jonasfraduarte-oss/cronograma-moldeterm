@echo off
title Cronograma MoldeTerm
cd /d "%~dp0"

rem ---- acha o Python
set PY=
where py >nul 2>nul && set PY=py
if "%PY%"=="" (where python >nul 2>nul && set PY=python)

if "%PY%"=="" (
  echo.
  echo  ================================================================
  echo   O Python nao esta instalado neste computador.
  echo.
  echo   1^) Baixe em https://www.python.org/downloads/
  echo   2^) Na PRIMEIRA tela do instalador, marque a caixa
  echo      "Add Python to PATH" antes de clicar em Install Now.
  echo   3^) Depois de instalar, de duplo clique neste arquivo de novo.
  echo  ================================================================
  echo.
  pause
  exit /b
)

rem ---- garante a biblioteca de leitura de Excel
%PY% -c "import openpyxl" >nul 2>nul
if errorlevel 1 (
  echo Instalando a biblioteca de leitura de Excel, so na primeira vez...
  %PY% -m pip install --quiet openpyxl
  echo.
)

%PY% cronograma.py
if errorlevel 1 pause
