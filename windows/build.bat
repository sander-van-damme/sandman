@echo off
REM Build Sandman as a single-file windowed Windows executable, then wrap it
REM in a Windows installer with Inno Setup.
REM
REM Requires:
REM   pip install pyinstaller
REM   pip install -r requirements.txt
REM   Inno Setup 6 (https://jrsoftware.org/isinfo.php)  -- optional, only
REM     needed if you want the installer. The raw .exe builds without it.

setlocal

pyinstaller --onefile --windowed ^
    --name=Sandman ^
    --icon=windows/assets/icon_active.ico ^
    --add-data "windows/assets;windows/assets" ^
    windows/__main__.py
if errorlevel 1 goto :error

echo.
echo PyInstaller build complete. Output: dist\Sandman.exe

set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
    echo.
    echo Inno Setup 6 not found -- skipping installer build.
    echo Install it from https://jrsoftware.org/isinfo.php to get Sandman-Setup.exe
    goto :done
)

"%ISCC%" windows/installer.iss
if errorlevel 1 goto :error

echo.
echo Installer build complete. Output: installer\Sandman-Setup-*.exe

:done
endlocal
exit /b 0

:error
echo.
echo Build FAILED.
endlocal
exit /b 1
