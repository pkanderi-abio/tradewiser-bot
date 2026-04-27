# test_build.py - Quick test script for Windows build
# Run this before building MSI to ensure everything works

import sys
import os
from pathlib import Path

def test_imports():
    """Test that all required modules can be imported"""
    try:
        import fastapi
        import uvicorn
        import pydantic
        import alpaca  # Correct import name
        import yfinance
        print("✅ All Python imports successful")
        return True
    except ImportError as e:
        print(f"❌ Import error: {e}")
        return False

def test_app_structure():
    """Test that application structure is correct"""
    required_files = [
        'app/main.py',
        'app/core/config.py',
        'app/routes/trades.py',
        'app/services/trading_engine.py',
        'requirements.txt'
    ]

    missing = []
    for file in required_files:
        if not Path(file).exists():
            missing.append(file)

    if missing:
        print(f"❌ Missing files: {missing}")
        return False
    else:
        print("✅ Application structure OK")
        return True

def test_config():
    """Test configuration loading"""
    try:
        from app.core.config import settings
        print("✅ Configuration loading OK")
        return True
    except Exception as e:
        print(f"❌ Configuration error: {e}")
        return False

def main():
    print("🔍 Testing TradeWiser Bot build prerequisites...")
    print("=" * 50)

    tests = [
        test_app_structure,
        test_imports,
        test_config
    ]

    passed = 0
    for test in tests:
        if test():
            passed += 1
        print()

    print(f"Results: {passed}/{len(tests)} tests passed")

    if passed == len(tests):
        print("🎉 All tests passed! Ready to build MSI.")
        return 0
    else:
        print("❌ Some tests failed. Please fix issues before building.")
        return 1

if __name__ == '__main__':
    sys.exit(main())