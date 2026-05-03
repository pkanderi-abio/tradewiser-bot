# windows_service.py - Windows Service wrapper for TradeWiser Bot
import win32serviceutil
import win32service
import win32event
import servicemanager
import socket
import sys
import os
from pathlib import Path

# Add the current directory to Python path
current_dir = Path(__file__).parent
sys.path.insert(0, str(current_dir))

class TradeWiserService(win32serviceutil.ServiceFramework):
    _svc_name_ = "TradeWiserBot"
    _svc_display_name_ = "TradeWiser Trading Bot"
    _svc_description_ = "Automated trading bot with momentum strategy for Alpaca"

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
        socket.setdefaulttimeout(60)
        self.server = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.hWaitStop)
        # Signal uvicorn to exit — it polls this flag on every tick
        if self.server is not None:
            self.server.should_exit = True

    def SvcDoRun(self):
        servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE,
                            servicemanager.PYS_SERVICE_STARTED,
                            (self._svc_name_, ''))

        try:
            from app.main import app
            import uvicorn
            import asyncio

            config = uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="info")
            self.server = uvicorn.Server(config)
            asyncio.run(self.server.serve())

        except Exception as e:
            servicemanager.LogErrorMsg(f"Service failed: {str(e)}")
            raise

if __name__ == '__main__':
    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(TradeWiserService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(TradeWiserService)