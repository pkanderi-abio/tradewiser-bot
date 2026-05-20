#!/usr/bin/env pwsh
# TradeWiser Bot - MSI Build Automation
# Fixed encoding and syntax for PowerShell 5.0 compatibility

param(
    [ValidateSet('Build', 'Test', 'Deploy', 'All')]
    [string]$Action = 'Build',
    
    [string]$TargetHost = 'localhost',
    
    [switch]$QuietMode
)

# Don't treat warnings as errors
$ErrorActionPreference = 'Continue'

# =============================================================================
# HELPERS
# =============================================================================

function Write-Status {
    param([string]$Message, [string]$Status = 'Info')
    
    $colors = @{
        'OK' = 'Green'
        'ERROR' = 'Red'
        'WARN' = 'Yellow'
        'INFO' = 'Cyan'
    }
    
    $prefix = @{
        'OK' = '[OK]'
        'ERROR' = '[XX]'
        'WARN' = '[!!]'
        'INFO' = '[->]'
    }
    
    $color = $colors[$Status]
    $mark = $prefix[$Status]
    Write-Host "$mark $Message" -ForegroundColor $color
}

# =============================================================================
# VALIDATION
# =============================================================================

function Validate-Environment {
    Write-Host ""
    Write-Host "======================================================================" -ForegroundColor Cyan
    Write-Host "PHASE 1: VALIDATE ENVIRONMENT" -ForegroundColor Cyan
    Write-Host "======================================================================" -ForegroundColor Cyan
    
    $success = $true
    
    # Check admin
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (!$isAdmin) {
        Write-Status "ERROR: Administrator privileges required" ERROR
        exit 1
    }
    Write-Status "Administrator privileges verified" OK
    
    # Check Python
    $pythonVer = python --version 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Status "Python found: $pythonVer" OK
    } else {
        Write-Status "Python not found - install Python 3.9+" ERROR
        $success = $false
    }
    
    # Check venv
    if (Test-Path ".\venv\Scripts\python.exe") {
        Write-Status "Virtual environment found" OK
    } else {
        Write-Status "Virtual environment not found" ERROR
        $success = $false
    }
    
    # Check PyInstaller (filter out warnings)
    $pipOutput = pip list 2>&1 | Select-String -Pattern "pyinstaller", "Package" | Out-String
    if ($pipOutput -match "pyinstaller") {
        Write-Status "PyInstaller installed" OK
    } else {
        Write-Status "PyInstaller not found" ERROR
        $success = $false
    }
    
    # Check WiX
    $hasWix = (where.exe wix 2>$null) -or (where.exe candle 2>$null)
    if ($hasWix) {
        if (where.exe wix 2>$null) {
            Write-Status "WiX Toolset v4/v5 found" OK
        } else {
            Write-Status "WiX Toolset v3 found" OK
        }
    } else {
        Write-Status "WiX Toolset not found - download from https://wixtoolset.org" ERROR
        $success = $false
    }
    
    # Check required files
    $required = @("app/main.py", "windows_service.py", "tradewiser.wxs", "requirements.txt")
    foreach ($f in $required) {
        if (Test-Path $f) {
            Write-Status "File found - $f" OK
        } else {
            Write-Status "File missing - $f" ERROR
            $success = $false
        }
    }
    
    if (!$success) {
        Write-Host ""
        Write-Status "Prerequisites check FAILED" ERROR
        exit 1
    }
    
    Write-Status "All prerequisites verified" OK
    Write-Host ""
    return $true
}

# =============================================================================
# BUILD
# =============================================================================

function Build-Executables {
    Write-Host "======================================================================" -ForegroundColor Cyan
    Write-Host "PHASE 2: BUILD EXECUTABLES" -ForegroundColor Cyan
    Write-Host "======================================================================" -ForegroundColor Cyan
    Write-Host ""
    
    # Activate venv
    Write-Status "Activating virtual environment" INFO
    . ".\venv\Scripts\Activate.ps1"
    
    # Install deps
    Write-Status "Installing dependencies" INFO
    pip install -q --upgrade -r requirements.txt
    
    # Build main exe
    Write-Status "Building tradewiser-bot.exe" INFO
    pyinstaller --clean --onefile --name tradewiser-bot `
        --hidden-import=asyncio --hidden-import=aiohttp `
        app/main.py 2>&1 | Where-Object { $_ -match "error|running" }
    
    if (!(Test-Path ".\dist\tradewiser-bot.exe")) {
        Write-Status "FAILED: tradewiser-bot.exe not created" ERROR
        exit 1
    }
    Write-Status "Created executable: dist/tradewiser-bot.exe" OK
    
    # Build service exe
    Write-Status "Building windows_service.exe" INFO
    pyinstaller --clean --onefile `
        --hidden-import=win32serviceutil --hidden-import=win32service `
        --hidden-import=win32event --hidden-import=servicemanager `
        --hidden-import=win32timezone --hidden-import=win32api `
        --hidden-import=win32con --hidden-import=win32security `
        --hidden-import=pywintypes --hidden-import=asyncio `
        --hidden-import=aiohttp --name windows_service `
        windows_service.py 2>&1 | Where-Object { $_ -match "error|running" }
    
    if (!(Test-Path ".\dist\windows_service.exe")) {
        Write-Status "FAILED: windows_service.exe not created" ERROR
        exit 1
    }
    Write-Status "Created executable: dist/windows_service.exe" OK
    Write-Host ""
}

# =============================================================================
# MSI BUILD
# =============================================================================

function Build-MSI {
    Write-Host "======================================================================" -ForegroundColor Cyan
    Write-Host "PHASE 3: BUILD MSI INSTALLER" -ForegroundColor Cyan
    Write-Host "======================================================================" -ForegroundColor Cyan
    Write-Host ""
    
    # Detect WiX
    $wixV5 = where.exe wix 2>$null
    $wixV3 = where.exe candle 2>$null
    
    if ($wixV5) {
        Write-Status "Building with WiX v4/v5" INFO
        wix build -o tradewiser.msi tradewiser.wxs -ext WixToolset.UI.wixext 2>&1 | Where-Object { $_ -match "error|built" }
    } elseif ($wixV3) {
        Write-Status "Building with WiX v3" INFO
        candle.exe tradewiser.wxs -o tradewiser.wixobj 2>&1 | Where-Object { $_ -match "error" }
        light.exe -out tradewiser.msi tradewiser.wixobj -ext WixUIExtension 2>&1 | Where-Object { $_ -match "error" }
    } else {
        Write-Status "WiX Toolset not found" ERROR
        exit 1
    }
    
    if (!(Test-Path "tradewiser.msi")) {
        Write-Status "FAILED: MSI not created" ERROR
        exit 1
    }
    
    $msiSize = (Get-Item "tradewiser.msi").Length / 1MB
    $sizeStr = [math]::Round($msiSize, 2)
    Write-Status "Created MSI: tradewiser.msi size $sizeStr MB" OK
    Write-Host ""
}

# =============================================================================
# TEST INSTALLATION
# =============================================================================

function Test-LocalInstall {
    Write-Host "======================================================================" -ForegroundColor Cyan
    Write-Host "PHASE 4: TEST LOCAL INSTALLATION" -ForegroundColor Cyan
    Write-Host "======================================================================" -ForegroundColor Cyan
    Write-Host ""
    
    # Check existing
    $svc = Get-Service -Name "TradeWiser Bot" -ErrorAction SilentlyContinue
    if ($svc) {
        Write-Status "Stopping existing service" WARN
        Stop-Service -Name "TradeWiser Bot" -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
        
        Write-Status "Uninstalling previous version" WARN
        msiexec /x tradewiser.msi /quiet /norestart 2>$null
        Start-Sleep -Seconds 3
    }
    
    # Install
    Write-Status "Installing MSI" INFO
    $msiPath = (Resolve-Path "tradewiser.msi").Path
    msiexec /i "$msiPath" /quiet /norestart
    Start-Sleep -Seconds 5
    
    # Verify
    $svc = Get-Service -Name "TradeWiser Bot" -ErrorAction SilentlyContinue
    if (!$svc) {
        Write-Status "Service not registered" ERROR
        exit 1
    }
    Write-Status "Service registered successfully" OK
    
    # Check install dir
    $installDir = "C:\Program Files (x86)\TradeWiser\TradeWiser Bot"
    if (!(Test-Path $installDir)) {
        Write-Status "Installation directory not found" ERROR
        exit 1
    }
    Write-Status "Installation directory verified" OK
    
    # Copy .env if missing
    if (!(Test-Path "$installDir\.env")) {
        if (Test-Path ".\.env") {
            Copy-Item ".\.env" "$installDir\.env" -ErrorAction SilentlyContinue
            Write-Status "Copied .env to installation directory" OK
        }
    }
    
    # Start service
    Write-Status "Starting service" INFO
    Start-Service -Name "TradeWiser Bot"
    Start-Sleep -Seconds 3
    
    $svc = Get-Service -Name "TradeWiser Bot"
    if ($svc.Status -eq "Running") {
        Write-Status "Service started successfully" OK
    } else {
        Write-Status "Service failed to start - check Event Viewer" WARN
    }
    
    Write-Host ""
}

# =============================================================================
# MAIN
# =============================================================================

function Main {
    Write-Host ""
    Write-Host "======================================================================" -ForegroundColor Cyan
    Write-Host "  TradeWiser Bot - Windows MSI Build and Deployment" -ForegroundColor Cyan
    Write-Host "  Action: $Action" -ForegroundColor Cyan
    Write-Host "======================================================================" -ForegroundColor Cyan
    
    if (!(Test-Path "app\main.py")) {
        Write-Status "Run from tradewiser_bot directory" ERROR
        exit 1
    }
    
    if ($Action -in "Build", "All") {
        Validate-Environment
        Build-Executables
        Build-MSI
    }
    
    if ($Action -in "Test", "All") {
        Test-LocalInstall
    }
    
    if ($Action -in "Deploy") {
        Write-Host "======================================================================" -ForegroundColor Cyan
        Write-Host "PHASE 5: DEPLOYMENT INSTRUCTIONS" -ForegroundColor Cyan
        Write-Host "======================================================================" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "Copy tradewiser.msi to target machine and run:"
        Write-Host "  msiexec /i tradewiser.msi /quiet"
        Write-Host ""
        Write-Host "Then configure and start:"
        Write-Host "  notepad C:\Program` Files` (x86)\TradeWiser\TradeWiser` Bot\.env"
        Write-Host "  Start-Service -Name 'TradeWiser Bot'"
        Write-Host ""
    }
    
    Write-Host "======================================================================" -ForegroundColor Green
    Write-Host "SUCCESS: Workflow completed" -ForegroundColor Green
    Write-Host "======================================================================" -ForegroundColor Green
    Write-Host ""
}

Main
