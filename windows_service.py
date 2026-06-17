# windows_service.py - Windows Service wrapper for TradeWiser Bot
import win32serviceutil
import win32service
import win32event
import servicemanager
import socket
import sys
import os
from pathlib import Path

# When running from source, add the project root to sys.path so `app` is importable.
# When frozen by PyInstaller, all modules are bundled — no path manipulation needed.
if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).parent))

class TradeWiserService(win32serviceutil.ServiceFramework):
    _svc_name_ = "TradeWiserBot"
    _svc_display_name_ = "TradeWiser Trading Bot"
    _svc_description_ = "Automated trading bot with RSI momentum strategy for Alpaca Markets"

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
        socket.setdefaulttimeout(60)
        self.server = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.hWaitStop)
        if self.server is not None:
            self.server.should_exit = True

    def SvcDoRun(self):
        servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE,
                              servicemanager.PYS_SERVICE_STARTED,
                              (self._svc_name_, ''))
        try:
            # LocalSystem starts every service with cwd=C:\Windows\System32, which
            # is also where any relative config path (the audit DB, env file, etc.)
            # ends up by default. Pin cwd to the install directory so SQLite,
            # .env, and any other relative I/O land alongside the service binary.
            if getattr(sys, 'frozen', False):
                install_dir = Path(sys.executable).parent
            else:
                install_dir = Path(__file__).parent
            os.chdir(install_dir)

            from app.main import app
            import uvicorn
            import asyncio

            host = os.environ.get("SERVICE_HOST", "0.0.0.0")
            port = int(os.environ.get("SERVICE_PORT", "8000"))
            # log_config=None disables uvicorn's dictConfig setup which fails on Python 3.14
            config = uvicorn.Config(app, host=host, port=port, log_level="info", log_config=None)
            self.server = uvicorn.Server(config)
            asyncio.run(self.server.serve())

        except Exception as e:
            servicemanager.LogErrorMsg(f"TradeWiserBot service failed: {str(e)}")
            raise

if __name__ == '__main__':
    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(TradeWiserService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(TradeWiserService)