@echo off
REM Run this from inside the aiscore folder, in Command Prompt.
REM Produces dist\aiscore.exe — a single portable file, no Docker/Postgres needed.

echo Installing standalone build dependencies...
pip install -r requirements-standalone.txt
if errorlevel 1 goto :error

echo.
echo Building aiscore.exe (this can take a minute or two)...
python -m PyInstaller --onefile --name aiscore --add-data "data;data" run_standalone.py
if errorlevel 1 goto :error

echo.
echo Done. Your exe is at: dist\aiscore.exe
echo Copy dist\aiscore.exe anywhere (including a different PC) and double-click it to run.
goto :eof

:error
echo.
echo Build failed — see the error above.
