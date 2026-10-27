@echo off
rem Finishes the plugin build in this folder.
rem
rem Close AyuGram first: AyuGram.exe is locked while it runs, so the copy
rem cannot complete otherwise. Your tdata stays here, so the account you are
rem signed in to is kept.
rem
rem The previous build shipped _ssl.pyd without libssl-3.dll and
rem libcrypto-3.dll, which is why OzonSearch failed with
rem "DLL load failed while importing _ssl". deploy.py now copies them from the
rem Python install's DLLs folder and refuses to finish while any staged module
rem still cannot resolve its libraries.

setlocal
cd /d "%~dp0"

tasklist /fi "IMAGENAME eq AyuGram.exe" | find /i "AyuGram.exe" >nul
if not errorlevel 1 (
	echo AyuGram is still running. Close it, then run this file again.
	pause
	exit /b 1
)

set "PY="
for %%p in (
	"%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
	"%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
	"%ProgramFiles%\Python314\python.exe"
) do if not defined PY if exist %%p set "PY=%%~p"

if not defined PY (
	echo Python 3.14 not found. Install it or pass --python to deploy.py.
	pause
	exit /b 1
)

echo.
echo Updating this folder to a build with working plugins...
echo.
"%PY%" "D:\TBuild\AyuGram\source\SourceFiles\ayu\plugins\standalone\deploy.py" ^
	--build-dir "D:\TBuild\tdesktop\out" ^
	--config Release ^
	--to "%~dp0" --python "%PY%"

set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" (
	echo Update did not finish. The message above says what is missing.
) else (
	echo Done. Start AyuGram from this folder.
	echo Then open Settings -^> Plugins and run the "Check plugins" button.
)
pause
exit /b %RC%
