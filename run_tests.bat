@echo off
REM TradeWiser Bot - Test Runner for Windows
REM Run from the tradewiser_bot directory: .\run_tests.bat

echo ========================================
echo  TradeWiser Bot - Test Suite
echo ========================================
echo.

REM Verify we're in the right directory
if not exist "app\main.py" (
    echo ERROR: Run this script from the tradewiser_bot directory.
    pause
    exit /b 1
)

REM Set up virtual environment if missing
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo ERROR: Failed to create virtual environment. Is Python installed?
        pause
        exit /b 1
    )
)

REM Activate virtual environment
call venv\Scripts\activate.bat

REM Install app dependencies
echo Installing application dependencies...
pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo ERROR: Failed to install requirements.txt
    pause
    exit /b 1
)

REM Install test dependencies
echo Installing test dependencies...
pip install -r requirements-test.txt --quiet
if %errorlevel% neq 0 (
    echo ERROR: Failed to install requirements-test.txt
    pause
    exit /b 1
)

echo.
echo Running tests...
echo ----------------------------------------

REM Run pytest with verbose output and colour
python -m pytest tests/ ^
    --tb=short ^
    -v ^
    --no-header ^
    -p no:cacheprovider

set EXIT_CODE=%errorlevel%

echo.
echo ----------------------------------------
if %EXIT_CODE% equ 0 (
    echo  ALL TESTS PASSED
) else (
    echo  SOME TESTS FAILED - see output above
)
echo ----------------------------------------
echo.

pause
exit /b %EXIT_CODE%
