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
    echo.
    echo ERROR: WiX Toolset not found!
    echo.
    echo Please install WiX Toolset from one of these sources:
    echo.
    echo OPTION 1: Download executable installer
    echo   URL: https://github.com/wixtoolset/wix/releases
    echo   Download: wix-X.X.X-x64.exe
    echo   Then run the installer
    echo.
    echo OPTION 2: Use Windows Package Manager
    echo   Command: winget install --id WiXToolset.WiXToolset
    echo.
    echo OPTION 3: Install via Visual Studio
    echo   Add "Windows development" workload to VS installer
    echo.
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