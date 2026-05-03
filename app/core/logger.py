# app/core/logger.py
import logging
import sys
import os

def setup_logger():
    """Configure the standard library logging to integrate with FastAPI logging."""
    # Remove all existing handlers to avoid duplicate logs when reloading
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    # Set up proper encoding for Windows
    if os.name == 'nt':  # Windows
        # Force UTF-8 encoding for console output
        if hasattr(sys.stdout, 'reconfigure'):
            try:
                sys.stdout.reconfigure(encoding='utf-8')
                sys.stderr.reconfigure(encoding='utf-8')
            except Exception:
                pass  # Fallback if reconfigure not available

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)

    root.setLevel(logging.INFO)
    root.addHandler(handler)

    # Log an initialization message using the module logger
    logging.getLogger(__name__).info("Logger initialized successfully!")

# Expose a module-level logger for other modules to import
logger = logging.getLogger("tradewiser")

__all__ = ["logger", "setup_logger"]
