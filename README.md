# Pad2Xbox

A tool to make a generic DirectInput gamepad (e.g. "PC Gamepad", "Wire PS4 Controller" clones) appear as a virtual Xbox 360 (XInput) controller on Windows, using ViGEmBus.

## Features
- GUI calibration wizard
- Virtual Xbox 360 / DualShock 4 output
- HidHide integration (hide original pad from other apps)
- System tray icon
- Live test view
- Stick/trigger tuning (deadzone, curve, anti-deadzone, threshold)
- Profiles + auto-switch by foreground app
- Remap + Turbo + Combo
- Multi-controller (up to 4)
- Diagnostics export + logging

## Requirements
- Windows 10/11 x64
- ViGEmBus driver
- HidHide driver (optional, for hiding original pad)
- Python 3.10+ (if running from source)

## Install (binary)
Use `Pad2Xbox_Setup.exe` (built with Inno Setup) which installs prerequisites and creates shortcuts.

## Run from source
```
pip install -r requirements.txt
python Pad2Xbox.py
```

## Build
```
build_exe.bat
```
PY