@echo off
title Importar planilhas de HM
cd /d "%~dp0"
where py >nul 2>nul && (py importar_hms.py & pause & exit /b)
where python >nul 2>nul && (python importar_hms.py & pause & exit /b)
echo Python nao encontrado. Instale em https://www.python.org/downloads/
pause
