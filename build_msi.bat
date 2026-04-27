@echo off
REM TradeWiser Bot - Windows MSI Build Script
REM Run this script on a Windows machine with Python, PyInstaller, and WiX Toolset installed

echo ========================================
echo TradeWiser Bot - MSI Build Script
echo ========================================

REM Check if we're in the right directory
if not exist "app\main.py" (
    echo ERROR: Please run this script from the tradewiser_bot directory
    pause
    exit /b 1
)

REM Create virtual environment if it doesn't exist
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate virtual environment
call venv\Scripts\activate.bat

REM Install requirements
echo Installing Python dependencies...
pip install -r requirements.txt
pip install pyinstaller

REM Build the main executable
echo Building main executable...
pyinstaller --clean --onefile --name tradewiser-bot app\main.py

REM Build the service executable
echo Building Windows service executable...
pyinstaller --clean --onefile --name windows_service windows_service.py

REM Check if WiX Toolset is installed
where candle >nul 2>nul
if %errorlevel% neq 0 (
    echo ERROR: WiX Toolset not found. Please install WiX Toolset from:
    echo https://wixtoolset.org/releases/
    pause
    exit /b 1
)

REM Build MSI installer
echo Building MSI installer...
candle tradewiser.wxs
light tradewiser.wixobj

REM Check if MSI was created successfully
if exist "tradewiser.msi" (
    echo.
    echo ========================================
    echo SUCCESS: tradewiser.msi created!
    echo ========================================
    echo.
    echo To install on Windows Server:
    echo 1. Copy tradewiser.msi to the server
    echo 2. Run: msiexec /i tradewiser.msi
    echo 3. Configure .env file in installation directory
    echo 4. Restart the TradeWiserBot service
    echo.
) else (
    echo ERROR: Failed to create MSI installer
    pause
    exit /b 1
)

echo Build completed successfully!
pause