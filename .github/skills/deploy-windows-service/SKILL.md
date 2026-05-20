---
name: deploy-windows-service
description: "Build, package, and deploy the TradeWiser Bot as a Windows Service with MSI installer and handle service lifecycle operations"
---

# Deploy Windows Service Skill

This skill automates building and deploying the TradeWiser Bot as a Windows Service.

## What It Does

1. Validates environment and dependencies
2. Builds executable with PyInstaller
3. Creates Windows Service wrapper
4. Generates MSI installer package (WiX)
5. Tests installation and service startup
6. Deploys to target Windows machines
7. Manages service lifecycle (start/stop/restart)

## Prerequisites

- Windows development machine (or target)
- Python venv activated with all dependencies
- PyInstaller installed: `pip install pyinstaller`
- WiX Toolset installed (for MSI generation)
- Administrative privileges on target machine
- Alpaca API credentials configured in `.env`

## Workflow

### Step 1: Prepare for Deployment
```bash
# Verify venv is activated
cd c:\Users\Administrator\pkanderi-abio\tradewiser_bot
venv\Scripts\Activate.ps1

# Verify dependencies
pip list | grep -E "flask|alpaca|pyinstaller"

# Check configuration
cat .env | grep -E "ALPACA|LOG"

# Run test suite to ensure quality
pytest tests/ -v --tb=short
```

### Step 2: Build Executables
```bash
# Build main bot executable
pyinstaller tradewiser.spec

# Build Windows Service wrapper
pyinstaller windows_service.spec

# Verify builds
ls -la dist/
ls -la build/
```

**What Gets Built:**
- `dist/tradewiser-bot.exe` — Main application
- `dist/windows_service.exe` — Service wrapper
- Supporting DLLs and libraries in dist/

### Step 3: Create MSI Installer
```bash
# Run WiX candle and light to generate MSI
.\build_msi.bat

# Output: tradewiser-bot-[version].msi
ls -la *.msi
```

**What's Packaged:**
- Executables, libraries, config files
- Service registration scripts
- Uninstall support
- Auto-start on boot configuration

### Step 4: Test Installation
```bash
# Install MSI in test environment
msiexec /i tradewiser-bot-1.0.0.msi /passive

# Verify installation
Get-Service -Name "TradeWiser Bot" | Select Name, Status

# Check installation directory
dir "C:\Program Files (x86)\TradeWiser Bot"

# Test service start
Start-Service -Name "TradeWiser Bot"
Get-EventLog -LogName Application -Source "TradeWiser Bot" -Newest 5
```

### Step 5: Verify Service Operations
```bash
# Start service
.\status.ps1 -action start

# Check status
.\status.ps1 -action status

# Stop service
.\status.ps1 -action stop

# View logs
Get-EventLog -LogName Application | Where-Object { $_.Source -eq "TradeWiser Bot" } | Format-List

# Test service restart
Restart-Service -Name "TradeWiser Bot"
```

### Step 6: Deploy to Production
```bash
# Copy MSI to target machine
scp tradewiser-bot-1.0.0.msi user@target-machine:C:\temp\

# Or run deployment script
.\deploy.ps1 -target-host 192.168.1.100 -msi-path ./tradewiser-bot-1.0.0.msi

# Verify deployment
Get-Service -ComputerName 192.168.1.100 -Name "TradeWiser Bot"
```

### Step 7: Monitor & Maintain
```bash
# Check service health
.\status.ps1 -action health

# View recent logs
Get-EventLog -LogName Application -Source "TradeWiser Bot" -Newest 20

# Restart if needed
Restart-Service -Name "TradeWiser Bot" -Force

# Update configuration
notepad "C:\Program Files (x86)\TradeWiser Bot\.env"
Restart-Service -Name "TradeWiser Bot"
```

## File Structure for Deployment

```
tradewiser_bot/
├── tradewiser.spec         # PyInstaller spec for main bot
├── windows_service.spec    # PyInstaller spec for service wrapper
├── windows_service.py      # Windows Service implementation
├── build_msi.bat           # MSI build script
├── deploy.ps1              # Deployment script
├── status.ps1              # Service status/control script
├── tradewiser.wxs          # WiX installer source
├── dist/                   # Compiled executables (after PyInstaller)
└── app/                    # Source code
```

## Build Scripts Reference

### build_msi.bat
```batch
@echo off
setlocal

echo Building TradeWiser Bot MSI...

REM Check if WiX is installed
if not exist "C:\Program Files (x86)\WiX Toolset v3" (
    echo ERROR: WiX Toolset not found
    exit /b 1
)

set WIX=C:\Program Files (x86)\WiX Toolset v3\bin\

REM Run candle.exe to compile .wxs to .wixobj
"%WIX%candle.exe" tradewiser.wxs -o tradewiser.wixobj

REM Run light.exe to link and create MSI
"%WIX%light.exe" tradewiser.wixobj -o tradewiser-bot-1.0.0.msi

echo Build complete: tradewiser-bot-1.0.0.msi
```

### deploy.ps1
```powershell
param(
    [string]$TargetHost = "localhost",
    [string]$MsiPath = "./tradewiser-bot-1.0.0.msi"
)

Write-Host "Deploying TradeWiser Bot to $TargetHost"

# Copy MSI
Copy-Item -Path $MsiPath -Destination "\\$TargetHost\c$\temp\"

# Stop existing service
Invoke-Command -ComputerName $TargetHost -ScriptBlock {
    Stop-Service -Name "TradeWiser Bot" -Force -ErrorAction SilentlyContinue
}

# Install MSI
Invoke-Command -ComputerName $TargetHost -ScriptBlock {
    msiexec /i "C:\temp\$(Split-Path $args -Leaf)" /quiet /norestart
} -ArgumentList $MsiPath

# Start service
Invoke-Command -ComputerName $TargetHost -ScriptBlock {
    Start-Service -Name "TradeWiser Bot"
}

Write-Host "Deployment complete"
```

## Troubleshooting

| Issue | Cause | Solution |
|-------|-------|----------|
| PyInstaller build fails | Missing imports/dependencies | Run `pip install -r requirements.txt`, check spec files |
| MSI install fails | WiX not found or corrupted | Reinstall WiX Toolset, verify `tradewiser.wxs` |
| Service won't start | Config/env not found | Check install path, verify `.env` copied to Program Files |
| Service crashes on startup | Missing Alpaca credentials | Set ALPACA_API_KEY in .env before service start |
| Port already in use | Flask port conflict | Change FLASK_PORT in config, restart service |

## Verification Checklist

After deployment:
- [ ] MSI installs without errors
- [ ] Service appears in Services.msc
- [ ] Service starts automatically on boot
- [ ] Trading engine connects to Alpaca
- [ ] API endpoints respond (http://localhost:5000/health)
- [ ] Logs appear in Event Viewer
- [ ] Update mechanism works (new MSI uninstalls old, installs new)
- [ ] Uninstall removes all components cleanly

## Rollback Procedure

If deployment fails:
```bash
# Stop service
Stop-Service -Name "TradeWiser Bot" -Force

# Uninstall MSI
msiexec /x tradewiser-bot-1.0.0.msi /quiet

# Reinstall previous version
msiexec /i tradewiser-bot-0.9.9.msi /quiet

# Start service
Start-Service -Name "TradeWiser Bot"
```
