# tradewiser.spec - PyInstaller specification for TradeWiser Bot
# Run with: pyinstaller --clean tradewiser.spec

import os
from pathlib import Path

# Get the project root
project_root = Path(__file__).parent

# Analysis configuration
a = Analysis(
    ['app/main.py'],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        # Include config files
        (str(project_root / 'app' / 'core' / 'config.py'), 'app/core/'),
        (str(project_root / 'app' / 'core' / 'logger.py'), 'app/core/'),
        # Include all route files
        (str(project_root / 'app' / 'routes'), 'app/routes/'),
        # Include all service files
        (str(project_root / 'app' / 'services'), 'app/services/'),
        # Include requirements
        (str(project_root / 'requirements.txt'), '.'),
    ],
    hiddenimports=[
        'uvicorn',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.websockets',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'fastapi',
        'pydantic',
        'pydantic_settings',
        'alpaca_py',
        'sqlalchemy',
        'yfinance',
        'asyncio',
        'app.routes.quotes',
        'app.routes.trades',
        'app.routes.health',
        'app.services.trading_engine',
        'app.services.webull_client',
        'app.services.utils',
        'app.core.config',
        'app.core.logger',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

# PYZ configuration
pyz = PYZ(a.pure, a.zipped_data, cipher=None)

# EXE configuration
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='tradewiser-bot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # Add icon if available
)