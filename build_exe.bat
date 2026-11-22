@echo off
cd /d "%~dp0"
echo Installing build requirements...
python -m pip install --upgrade pyinstaller pygame vgamepad pystray pillow
if errorlevel 1 goto fail
echo Building Pad2Xbox (onedir, to reduce AV false positives) ...
python -m PyInstaller --noconsole --clean --noconfirm --name Pad2Xbox --collect-all vgamepad --collect-all pygame Pad2Xbox.py
if errorlevel 1 goto fail
echo.
echo Done. Your app is in: %~dp0dist\Pad2Xbox\Pad2Xbox.exe
pause
exit /b 0
:fail
echo Build failed.
pause
exit /b 1