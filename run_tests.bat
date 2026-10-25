@echo off
rem Corre la suite del bot desde la raiz del repo. Falla si falla.
cd /d "%~dp0"
call python -m pytest bot/tests -q -p no:cacheprovider
exit /b %errorlevel%
