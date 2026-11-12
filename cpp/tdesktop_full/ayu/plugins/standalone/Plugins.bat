@echo off
rem Manage AyuGram Desktop plugins and their settings.
rem   Plugins.bat list
rem   Plugins.bat doctor
rem   Plugins.bat settings <id>
rem   Plugins.bat set <id> <key> <value>
rem   Plugins.bat enable|disable <id>
rem   Plugins.bat install <path>
setlocal
cd /d "%~dp0"
set "AYU_PLUGINS_SHIM=%~dp0"

rem Prefer the interpreter that ships next to AyuGram, so the CLI sees the
rem same environment the app uses.
set "PY="
if exist "%~dp0python314.dll" (
	for /f "delims=" %%i in ('where python 2^>nul') do (
		if not defined PY set "PY=%%i"
	)
)
if not defined PY (
	for %%p in (
		"%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
		"%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
		"%ProgramFiles%\Python314\python.exe"
	) do if not defined PY if exist %%p set "PY=%%~p"
)
if not defined PY (
	echo Python not found. Install Python 3.11+ or add it to PATH.
	pause
	exit /b 1
)

"%PY%" "%~dp0tools\ayu-plugin-cli\manage.py" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" pause
exit /b %RC%
