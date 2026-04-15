@echo off
REM Build Sandman as a single-file windowed Windows executable.
REM Requires: pip install pyinstaller && pip install -r requirements.txt

pyinstaller --onefile --windowed ^
    --name=Sandman ^
    --icon=sandman/assets/icon_active.ico ^
    --add-data "sandman/assets;sandman/assets" ^
    sandman/main.py

echo.
echo Build complete. Output: dist\Sandman.exe
