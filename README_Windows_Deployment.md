# TradeWiser Bot - Windows MSI Packaging & Deployment

This guide covers packaging the TradeWiser trading bot as an MSI installer and deploying it to a Windows Server for production use.

## 📋 Prerequisites

### Build Machine Requirements
- Windows 10/11 with Python 3.8+
- [WiX Toolset](https://wixtoolset.org/releases/) (for MSI creation)
- PyInstaller (`pip install pyinstaller`)

### Windows Server Requirements
- Windows Server 2016+ or Windows 10/11 Pro
- Administrator privileges
- PowerShell execution enabled
- Internet access for Alpaca API

## 🏗️ Building the MSI Package

### Step 1: Prepare the Build Environment
```bash
# Clone or copy the tradewiser_bot directory to a Windows machine
cd tradewiser_bot

# Ensure all files are present
dir
# Should see: app/, requirements.txt, setup.py, tradewiser.spec, etc.
```

### Step 2: Run the Build Script
```cmd
# Run the automated build script
build_msi.bat
```

This will:
- Create a Python virtual environment
- Install all dependencies
- Build two executables (main app + Windows service)
- Create the MSI installer (`tradewiser.msi`)

### Step 3: Verify Build Output
```cmd
dir dist\
# Should contain: tradewiser-bot.exe, windows_service.exe, and tradewiser.msi
```

## 🚀 Production Deployment

### Step 1: Install on Windows Server
```cmd
# Copy tradewiser.msi to your Windows Server
# Run as Administrator:
msiexec /i tradewiser.msi
```

The MSI will:
- Install files to `C:\Program Files\TradeWiser\TradeWiser Bot\`
- Create a Windows service named "TradeWiserBot"
- Add Start Menu shortcuts
- Configure firewall rules

### Step 2: Configure Production Settings
```powershell
# Run the deployment script with your Alpaca credentials
# Use live trading for production:
.\deploy.ps1 -AlpacaApiKey "YOUR_LIVE_API_KEY" -AlpacaSecretKey "YOUR_LIVE_SECRET" -UseLiveTrading

# Or use paper trading for testing:
.\deploy.ps1 -AlpacaApiKey "YOUR_PAPER_API_KEY" -AlpacaSecretKey "YOUR_PAPER_SECRET"
```

### Step 3: Verify Installation
```powershell
# Check service status
Get-Service -Name "TradeWiserBot"

# Check if port 8000 is listening
netstat -ano | findstr :8000

# Test the API
curl http://localhost:8000/health
curl http://localhost:8000/trades/strategy/status
```

## 📁 File Structure After Installation

```
C:\Program Files\TradeWiser\TradeWiser Bot\
├── tradewiser-bot.exe          # Main application
├── windows_service.exe         # Windows service wrapper
├── .env                        # Configuration file
├── app\                        # Application modules
│   ├── main.py
│   ├── core\
│   ├── routes\
│   └── services\
├── requirements.txt
└── sample.env
```

## ⚙️ Configuration

### Environment Variables (.env)
```env
# Production Alpaca credentials
ALPACA_API_KEY=your_live_api_key
ALPACA_SECRET_KEY=your_live_secret_key
ALPACA_BASE_URL=https://api.alpaca.markets  # Live trading

# Trading parameters
POLL_INTERVAL=5  # Seconds between checks

# Legacy (not used)
WEBULL_EMAIL=
WEBULL_PASSWORD=
WEBULL_DEVICE_NAME=TradeWiserBot
```

### Service Management
```powershell
# Start service
Start-Service -Name "TradeWiserBot"

# Stop service
Stop-Service -Name "TradeWiserBot"

# Restart service
Restart-Service -Name "TradeWiserBot"

# Check status
Get-Service -Name "TradeWiserBot"

# View logs
Get-EventLog -LogName "Application" -Source "TradeWiserBot" -Newest 10
```

## 🔧 Troubleshooting

### Service Won't Start
```powershell
# Check service status
Get-Service -Name "TradeWiserBot" | Format-List

# Check event logs
Get-EventLog -LogName "System" -Newest 10 | Where-Object { $_.Source -eq "Service Control Manager" }

# Manual start with error details
sc.exe start "TradeWiserBot"
```

### API Not Responding
```powershell
# Check if service is running
Get-Service -Name "TradeWiserBot"

# Check firewall
Get-NetFirewallRule -DisplayName "TradeWiser Bot"

# Test locally
curl http://localhost:8000/health
```

### Alpaca Connection Issues
```powershell
# Verify credentials in .env file
Get-Content "C:\Program Files\TradeWiser\TradeWiser Bot\.env"

# Check internet connectivity
Test-NetConnection -ComputerName "api.alpaca.markets" -Port 443
```

## 📊 Monitoring

### API Endpoints
- **Health Check:** `GET /health`
- **Strategy Status:** `GET /trades/strategy/status`
- **Trade Audit:** `GET /trades/audit`
- **Manual Trade:** `POST /trades/execute`

### Trading Activity
The bot automatically:
- Monitors 9 symbols (3 stocks + 6 options)
- Triggers buys/sells at 0.2% momentum thresholds
- Logs all activity to audit trail
- Runs as a Windows service 24/7

### Logs
- Application logs: Windows Event Viewer → Application
- Service logs: Windows Event Viewer → System
- Trading logs: Check `/trades/audit` endpoint

## 🔄 Updates

### To Update the Application
1. Build new MSI with updated code
2. Stop the service: `Stop-Service -Name "TradeWiserBot"`
3. Install new MSI (it will upgrade automatically)
4. Start service: `Start-Service -Name "TradeWiserBot"`

### Configuration Changes
1. Edit the `.env` file in the installation directory
2. Restart the service to pick up changes

## 🛡️ Security Considerations

- Store Alpaca credentials securely (consider Azure Key Vault for production)
- Run the service under a dedicated service account with minimal privileges
- Regularly update the server and dependencies
- Monitor trading activity and set appropriate risk limits
- Use live trading credentials only when ready for real money trading

## 📞 Support

For issues with:
- **Build process:** Check PyInstaller and WiX Toolset documentation
- **Windows service:** Check Windows Event Logs
- **Trading logic:** Review application logs and audit trail
- **Alpaca API:** Check Alpaca status page and API documentation

---

**Production Deployment Checklist:**
- [ ] MSI built successfully
- [ ] Installed on Windows Server as Administrator
- [ ] Production Alpaca credentials configured
- [ ] Service running and accessible on port 8000
- [ ] Firewall configured for port 8000
- [ ] API endpoints responding correctly
- [ ] Trading activity visible in Alpaca dashboard