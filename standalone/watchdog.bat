@echo off
rem Starts AyuGram under the watchdog.
rem
rem The app is restarted if it crashes or stops responding, and left alone when
rem it is closed on purpose. Run this instead of AyuGram.exe:
rem
rem   watchdog.bat        run, and recover from crashes
rem   watchdog.bat log    the same, then show what the watchdog recorded
rem
rem The restart budget is three failures inside five minutes. After that it
rem stops and writes down why, rather than retrying forever and hiding the cause.

setlocal
cd /d "%~dp0"

set "WATCHDOG=%~dp0tools\watchdog.py"
if not exist "%WATCHDOG%" set "WATCHDOG=%~dp0standalone\watchdog.py"
if not exist "%WATCHDOG%" (
	echo watchdog.py was not found next to this file.
	echo Looked in "%~dp0tools" and "%~dp0standalone".
	pause
	exit /b 1
)

set "EXE=%~dp0AyuGram.exe"
if not exist "%EXE%" set "EXE=%~dp0app\AyuGram.exe"
if not exist "%EXE%" (
	echo AyuGram.exe was not found next to this file.
	pause
	exit /b 1
)

rem Prefer the interpreter that ships with the app, so the watchdog and the app
rem agree on the Python runtime. Fall back to whatever is on PATH.
set "PY="
if exist "%~dp0python314.dll" (
	for /f "delims=" %%i in ('where python 2^>nul') do (
		if not defined PY set "PY=%%i"
	)
) else (
	for /f "delims=" %%i in ('where python 2^>nul') do (
		if not defined PY set "PY=%%i"
	)
)
if not defined PY (
	echo Python was not found on PATH.
	echo Install Python 3.12 or newer, then run this again.
	pause
	exit /b 1
)

if /i "%~1"=="log" (
	"%PY%" "%WATCHDOG%" --exe "%EXE%"
	echo.
	echo ---------------- watchdog.log ----------------
	rem The log is written beside watchdog.py, not beside this file.
	if exist "%~dp0watchdog.log" (
		type "%~dp0watchdog.log"
	) else if exist "%~dp0tools\watchdog.log" (
		type "%~dp0tools\watchdog.log"
	) else if exist "%~dp0standalone\watchdog.log" (
		type "%~dp0standalone\watchdog.log"
	) else (
		echo no watchdog.log was found
	)
	pause
) else (
	"%PY%" "%WATCHDOG%" --exe "%EXE%"
)

endlocal
