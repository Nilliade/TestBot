@echo off
:: Alias wrapper for Setup.bat
call "%~dp0Setup.bat" %*
exit /b %ERRORLEVEL%
