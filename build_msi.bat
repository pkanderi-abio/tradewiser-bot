@echo off
REM TradeWiser Bot - Windows MSI Build Script
REM Supports WiX Toolset v4/v5 (uses "wix build" instead of candle/light)

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
pip install pyinstaller pywin32

REM Run pywin32 post-install to register DLLs
echo Registering pywin32...
python venv\Scripts\pywin32_postinstall.py -install 2>nul

REM Build the main executable
echo Building main executable...
pyinstaller --clean --onefile --name tradewiser-bot app\main.py

REM Build the service executable with all required win32 hidden imports
echo Building Windows service executable...
pyinstaller --clean --onefile ^
  --hidden-import=win32serviceutil ^
  --hidden-import=win32service ^
  --hidden-import=win32event ^
  --hidden-import=servicemanager ^
  --hidden-import=win32timezone ^
  --hidden-import=win32api ^
  --hidden-import=win32con ^
  --hidden-import=win32security ^
  --hidden-import=win32process ^
  --hidden-import=pywintypes ^
  --name windows_service ^
  windows_service.py

REM Verify service EXE was built
if not exist "dist\windows_service.exe" (
    echo ERROR: Failed to build windows_service.exe
    pause
    exit /b 1
)

REM Check for WiX Toolset - support both v3 (candle) and v4 (wix)
set WIX_VERSION=0
where candle >nul 2>nul
if %errorlevel% equ 0 set WIX_VERSION=3

where wix >nul 2>nul
if %errorlevel% equ 0 set WIX_VERSION=4

if %WIX_VERSION% equ 0 (
    echo ERROR: WiX Toolset not found. Please install WiX Toolset from:
    echo https://wixtoolset.org/releases/
    pause
    exit /b 1
)

REM Build MSI installer
echo Building MSI installer using WiX v%WIX_VERSION%...

if %WIX_VERSION% equ 3 (
    REM WiX v3 syntax
    candle tradewiser_simple.wxs
    if %errorlevel% neq 0 (
        echo ERROR: candle failed
        pause
        exit /b 1
    )
    light tradewiser_simple.wixobj -ext WixToolset.UI.wixext -o tradewiser.msi
    if %errorlevel% neq 0 (
        echo ERROR: light failed
        pause
        exit /b 1
    )
) else (
    REM WiX v4/v5 syntax
    wix build tradewiser_simple.wxs -ext WixToolset.UI.wixext -o tradewiser.msi
    if %errorlevel% neq 0 (
        echo ERROR: wix build failed
        pause
        exit /b 1
    )
)

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