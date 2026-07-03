@echo off
rem Launch with a visible console so any error is readable (unlike the Start-menu
rem shortcut, which uses pythonw and hides errors).
cd /d "%~dp0"
python app.py
echo.
echo (app closed - if it errored, the traceback is above and in crash.log)
pause
