#Requires -RunAsAdministrator
<#
.SYNOPSIS
    TradeWiser Bot - Windows Service deployment (no MSI or WiX required).

.DESCRIPTION
    Deploys TradeWiser Bot as a Windows Service directly from source.
    Creates a self-contained venv under the install directory.

    Install path : C:\Program Files (x86)\TradeWiser\TradeWiser Bot\
    Service name : TradeWiserBot
    API port     : 8000 (firewall rule created automatically)

.PARAMETER Uninstall
    Stop, unregister the service, and delete the installation directory.

.PARAMETER Reinstall
    Uninstall then install fresh. Existing .env credentials are preserved.

.EXAMPLE
    # First-time install (then edit .env before starting)
    .\deploy.ps1

    # Pull new code and redeploy without losing credentials
    .\deploy.ps1 -Reinstall

    # Remove everything
    .\deploy.ps1 -Uninstall
#>
param(
    [switch]$Uninstall,
    [switch]$Reinstall
)

$ErrorActionPreference = "Stop"

$ServiceName   = "TradeWiserBot"
$InstallDir    = "C:\Program Files (x86)\TradeWiser\TradeWiser Bot"
$SourceDir     = $PSScriptRoot
$FirewallRule  = "TradeWiser Bot API"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-OK($msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    [!!] $msg" -ForegroundColor Yellow }

function Stop-TradeWiser {
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -eq "Running") {
        Write-Step "Stopping service"
        Stop-Service $ServiceName -Force
        Start-Sleep -Seconds 3
        Write-OK "Service stopped"
    }
}

function Unregister-TradeWiser {
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($svc) {
        Write-Step "Removing service registration"
        $python    = "$InstallDir\venv\Scripts\python.exe"
        $svcScript = "$InstallDir\windows_service.py"
        if ((Test-Path $python) -and (Test-Path $svcScript)) {
            & $python $svcScript remove 2>$null
        } else {
            sc.exe delete $ServiceName | Out-Null
        }
        Start-Sleep -Seconds 2
        Write-OK "Service unregistered"
    }
}

# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------
function Invoke-Uninstall {
    Write-Host "`nUninstalling TradeWiser Bot..." -ForegroundColor Yellow
    Stop-TradeWiser
    Unregister-TradeWiser

    if (Test-Path $InstallDir) {
        Write-Step "Removing installation directory"
        Remove-Item -Recurse -Force $InstallDir
        Write-OK "Removed $InstallDir"
    }

    # Remove firewall rule
    $fw = Get-NetFirewallRule -DisplayName $FirewallRule -ErrorAction SilentlyContinue
    if ($fw) {
        Remove-NetFirewallRule -DisplayName $FirewallRule
        Write-OK "Firewall rule removed"
    }

    Write-Host "`nUninstall complete." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------
function Invoke-Install {
    # 1. Prerequisites
    Write-Step "Checking prerequisites"
    try {
        $pyVer = & python --version 2>&1
        Write-OK "Python: $pyVer"
    } catch {
        Write-Host "    [XX] Python not found." -ForegroundColor Red
        Write-Host "         Install Python 3.9+ from https://python.org (add to PATH)" -ForegroundColor DarkGray
        exit 1
    }

    # 2. Create install directory
    Write-Step "Creating installation directory"
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    Write-OK $InstallDir

    # 3. Copy source files (skip generated/runtime artefacts)
    Write-Step "Copying source files"
    $excludeNames = @("venv", "dist", "build", ".git", ".pytest_cache",
                      "__pycache__", "*.pyc", "*.pyo", "*.msi",
                      "*.wixobj", "*.pdb", ".env", "tradewiser.log")

    Get-ChildItem -Path $SourceDir | Where-Object {
        $item = $_
        -not ($excludeNames | Where-Object { $item.Name -like $_ })
    } | ForEach-Object {
        $dest = Join-Path $InstallDir $_.Name
        if ($_.PSIsContainer) {
            Copy-Item -Recurse -Force $_.FullName $dest
        } else {
            Copy-Item -Force $_.FullName $dest
        }
    }

    # Remove any __pycache__ trees that got copied
    Get-ChildItem -Path $InstallDir -Recurse -Directory -Filter "__pycache__" |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

    Write-OK "Source files copied to $InstallDir"

    # 4. Create virtual environment
    Write-Step "Creating Python virtual environment"
    $venvDir = "$InstallDir\venv"
    if (-not (Test-Path $venvDir)) {
        & python -m venv $venvDir
        Write-OK "venv created"
    } else {
        Write-OK "venv already exists - skipping creation"
    }

    # 5. Install dependencies
    Write-Step "Installing Python dependencies"
    $pip    = "$venvDir\Scripts\pip.exe"
    $python = "$venvDir\Scripts\python.exe"
    & $pip install --upgrade pip --quiet
    & $pip install -r "$InstallDir\requirements.txt" --quiet
    & $pip install pywin32 --quiet
    Write-OK "Dependencies installed"

    # 6. Register pywin32 DLLs
    Write-Step "Registering pywin32"
    & $python "$venvDir\Scripts\pywin32_postinstall.py" -install 2>$null
    Write-OK "pywin32 registered"

    # 7. Create .env from sample if not present
    $envFile    = "$InstallDir\.env"
    $sampleFile = "$InstallDir\sample.env"

    if (-not (Test-Path $envFile)) {
        Write-Step "Creating .env configuration file"
        if (Test-Path $sampleFile) {
            Copy-Item $sampleFile $envFile
            Write-OK ".env created from sample.env at:"
            Write-OK "  $envFile"
            Write-Host ""
            Write-Host "  *** REQUIRED: edit .env before starting the service ***" -ForegroundColor Yellow
            Write-Host "  Minimum settings to change:" -ForegroundColor Yellow
            Write-Host "    ALPACA_API_KEY      - from https://alpaca.markets" -ForegroundColor Yellow
            Write-Host "    ALPACA_SECRET_KEY   - from https://alpaca.markets" -ForegroundColor Yellow
            Write-Host "    BOT_API_KEY         - any strong random string" -ForegroundColor Yellow
            Write-Host "    GROQ_API_KEY        - from https://console.groq.com (free)" -ForegroundColor Yellow
        } else {
            Write-Host "    [XX] sample.env not found - cannot create .env" -ForegroundColor Red
            exit 1
        }
    } else {
        Write-OK ".env already exists - credentials preserved"
    }

    # 8. Register Windows Service (pywin32 writes to SCM)
    Write-Step "Registering Windows Service"
    $svcScript = "$InstallDir\windows_service.py"

    # Build the command the SCM will invoke: venv python + script path
    # pywin32 registers exactly this command when "install" is called
    Push-Location $InstallDir
    try {
        & $python $svcScript install
        if ($LASTEXITCODE -ne 0) {
            Write-Host "    [XX] Service registration failed (exit $LASTEXITCODE)" -ForegroundColor Red
            exit 1
        }
    } finally {
        Pop-Location
    }
    Write-OK "Service '$ServiceName' registered"

    # 9. Harden service settings
    Write-Step "Configuring service"
    sc.exe config      $ServiceName start= auto | Out-Null
    sc.exe description $ServiceName "Automated trading bot - RSI momentum strategy for Alpaca Markets" | Out-Null
    sc.exe failure     $ServiceName reset= 86400 actions= restart/10000/restart/30000/restart/60000 | Out-Null
    Write-OK "Startup: Automatic"
    Write-OK "Failure actions: restart at 10s / 30s / 60s"

    # 10. Firewall rule for port 8000
    Write-Step "Configuring Windows Firewall"
    $fw = Get-NetFirewallRule -DisplayName $FirewallRule -ErrorAction SilentlyContinue
    if (-not $fw) {
        New-NetFirewallRule -DisplayName $FirewallRule `
            -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow | Out-Null
        Write-OK "Inbound rule added for TCP 8000"
    } else {
        Write-OK "Firewall rule already present"
    }

    # 11. Start service - only if .env looks configured
    $envContent  = Get-Content $envFile -Raw
    $needsConfig = ($envContent -match "BOT_API_KEY=change_me") -or
                   ($envContent -match "ALPACA_API_KEY=your_alpaca")

    Write-Step "Finalizing"
    if ($needsConfig) {
        Write-Warn "Service NOT started - .env still has placeholder values."
        Write-Host ""
        Write-Host "  1. Edit: $envFile" -ForegroundColor Yellow
        Write-Host "  2. Then: Start-Service $ServiceName" -ForegroundColor Yellow
        Write-Host "  3. Then: .\status.ps1 -ApiKey YOUR_BOT_API_KEY" -ForegroundColor Yellow
    } else {
        Start-Service $ServiceName
        Start-Sleep -Seconds 4
        $svc = Get-Service -Name $ServiceName
        if ($svc.Status -eq "Running") {
            Write-OK "Service is RUNNING"
        } else {
            Write-Warn "Service did not reach Running state (status: $($svc.Status))"
            Write-Host "  Check: Get-EventLog -LogName Application -Source $ServiceName -Newest 10" -ForegroundColor DarkGray
        }
    }
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "  TradeWiser Bot - Windows Deployment" -ForegroundColor Cyan
Write-Host "  ====================================" -ForegroundColor Cyan
Write-Host "  Install directory : $InstallDir" -ForegroundColor DarkGray
Write-Host "  Source directory  : $SourceDir"  -ForegroundColor DarkGray

if ($Uninstall) {
    Invoke-Uninstall
} elseif ($Reinstall) {
    # Preserve .env across reinstall
    $envFile  = "$InstallDir\.env"
    $savedEnv = $null
    if (Test-Path $envFile) {
        Write-Host "`n  Preserving existing .env credentials..." -ForegroundColor DarkGray
        $savedEnv = Get-Content $envFile -Raw
    }
    Invoke-Uninstall
    Invoke-Install
    if ($savedEnv) {
        Set-Content -Path "$InstallDir\.env" -Value $savedEnv -Encoding UTF8
        Write-OK ".env restored from previous install"
        # Restart so the restored credentials take effect
        Restart-Service $ServiceName -ErrorAction SilentlyContinue
    }
} else {
    Invoke-Install
}

Write-Host ""
Write-Host "  Done." -ForegroundColor Cyan
Write-Host "  Monitor: .\status.ps1 -ApiKey YOUR_BOT_API_KEY" -ForegroundColor DarkGray
Write-Host "  Swagger: http://127.0.0.1:8000/docs" -ForegroundColor DarkGray
Write-Host ""
