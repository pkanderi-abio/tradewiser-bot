# TradeWiser Bot - Windows Server Deployment Script
# Run this script on your Windows Server after installing the MSI

param(
    [Parameter(Mandatory=$true)]
    [string]$AlpacaApiKey,

    [Parameter(Mandatory=$true)]
    [string]$AlpacaSecretKey,

    [Parameter(Mandatory=$false)]
    [switch]$UseLiveTrading = $false,

    [Parameter(Mandatory=$false)]
    [int]$PollInterval = 5
)

Write-Host "=========================================" -ForegroundColor Green
Write-Host "TradeWiser Bot - Production Deployment" -ForegroundColor Green
Write-Host "=========================================" -ForegroundColor Green

# Check if running as administrator
$currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "ERROR: Please run this script as Administrator" -ForegroundColor Red
    exit 1
}

# Find installation directory
$installPath = Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*" |
    Where-Object { $_.DisplayName -eq "TradeWiser Bot" } |
    Select-Object -ExpandProperty InstallLocation

if (-not $installPath) {
    # Try alternative locations
    $possiblePaths = @(
        "C:\Program Files\TradeWiser\TradeWiser Bot",
        "C:\Program Files (x86)\TradeWiser\TradeWiser Bot"
    )

    foreach ($path in $possiblePaths) {
        if (Test-Path $path) {
            $installPath = $path
            break
        }
    }
}

if (-not $installPath) {
    Write-Host "ERROR: Could not find TradeWiser Bot installation directory" -ForegroundColor Red
    exit 1
}

Write-Host "Installation found at: $installPath" -ForegroundColor Yellow

# Create .env file
$envFile = Join-Path $installPath ".env"
$baseUrl = if ($UseLiveTrading) { "https://api.alpaca.markets" } else { "https://paper-api.alpaca.markets" }

$envContent = @"
# TradeWiser Bot - Production Configuration
ALPACA_API_KEY=$AlpacaApiKey
ALPACA_SECRET_KEY=$AlpacaSecretKey
ALPACA_BASE_URL=$baseUrl
POLL_INTERVAL=$PollInterval
WEBULL_EMAIL=
WEBULL_PASSWORD=
WEBULL_DEVICE_NAME=TradeWiserBot
"@

Set-Content -Path $envFile -Value $envContent -Force
Write-Host "Created configuration file: $envFile" -ForegroundColor Green

# Configure Windows Firewall
Write-Host "Configuring Windows Firewall..." -ForegroundColor Yellow
$firewallRule = Get-NetFirewallRule -DisplayName "TradeWiser Bot" -ErrorAction SilentlyContinue
if (-not $firewallRule) {
    New-NetFirewallRule -DisplayName "TradeWiser Bot" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
    Write-Host "Created firewall rule for port 8000" -ForegroundColor Green
}

# Start the service
Write-Host "Starting TradeWiser Bot service..." -ForegroundColor Yellow
try {
    Start-Service -Name "TradeWiserBot" -ErrorAction Stop
    Write-Host "Service started successfully" -ForegroundColor Green
} catch {
    Write-Host "ERROR: Failed to start service: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

# Verify service is running
$service = Get-Service -Name "TradeWiserBot"
if ($service.Status -eq "Running") {
    Write-Host "=========================================" -ForegroundColor Green
    Write-Host "DEPLOYMENT SUCCESSFUL!" -ForegroundColor Green
    Write-Host "=========================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "TradeWiser Bot is now running on port 8000" -ForegroundColor Cyan
    Write-Host "Service: TradeWiserBot" -ForegroundColor Cyan
    Write-Host "Status: $($service.Status)" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "API Endpoints:" -ForegroundColor Yellow
    Write-Host "  Health: http://localhost:8000/health" -ForegroundColor White
    Write-Host "  Strategy: http://localhost:8000/trades/strategy/status" -ForegroundColor White
    Write-Host "  Audit: http://localhost:8000/trades/audit" -ForegroundColor White
    Write-Host ""
    Write-Host "To check service status: Get-Service -Name 'TradeWiserBot'" -ForegroundColor Gray
    Write-Host "To restart service: Restart-Service -Name 'TradeWiserBot'" -ForegroundColor Gray
} else {
    Write-Host "WARNING: Service is not running. Status: $($service.Status)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Deployment completed!" -ForegroundColor Green