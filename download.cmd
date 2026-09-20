@echo off
setlocal DisableDelayedExpansion
py -3 "%~dp0download.py" %*
exit /b %errorlevel%
