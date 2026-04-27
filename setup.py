# setup.py for PyInstaller
from setuptools import setup
import os

# Read requirements
with open('requirements.txt', 'r') as f:
    requirements = [line.strip() for line in f if line.strip() and not line.startswith('#')]

setup(
    name="tradewiser-bot",
    version="1.0.0",
    description="Automated Trading Bot with Momentum Strategy",
    author="TradeWiser",
    packages=[],
    install_requires=requirements,
    entry_points={
        'console_scripts': [
            'tradewiser-bot=app.main:app',
        ],
    },
)