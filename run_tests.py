#!/usr/bin/env python3
"""Convenient test runner for vless2socks.

Usage:
    python run_tests.py             # Runs fast unit & integration tests
    python run_tests.py --reconnect # Runs VLESS & SOCKS5 auto-reconnect tests
    python run_tests.py --all       # Runs entire 228+ test suite
"""

import argparse
import os
import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def run():
    parser = argparse.ArgumentParser(description="Run vless2socks test suites.")
    parser.add_argument("--reconnect", action="store_true", help="Run forced reconnection tests (VLESS & SOCKS5)")
    parser.add_argument("--all", action="store_true", help="Run all 228+ tests across the entire codebase")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose test output")
    args = parser.parse_args()

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    if args.reconnect:
        print("⚡ Running forced reconnection tests (VLESS & SOCKS5)...")
        suite.addTests(loader.loadTestsFromName("tests.test_reconnect_socks5_vless"))
        suite.addTests(loader.loadTestsFromName("tests.test_force_restart"))
    elif args.all:
        print("🔬 Running entire test suite (228+ tests)...")
        discovered = loader.discover(str(ROOT_DIR / "tests"), pattern="test_*.py")
        suite.addTests(discovered)
    else:
        print("🚀 Running fast core & feature test suites...")
        modules = [
            "tests.test_reconnect_socks5_vless",
            "tests.test_force_restart",
            "tests.test_stop_and_indication",
            "tests.test_socks_support",
            "tests.test_features",
            "tests.test_config",
            "tests.test_url",
            "tests.test_protocol",
            "tests.test_killswitch",
            "tests.test_tls",
            "tests.test_xray_config",
            "tests.test_get_xray",
            "tests.test_isolation",
            "tests.test_e2e",
        ]
        for mod in modules:
            try:
                suite.addTests(loader.loadTestsFromName(mod))
            except Exception as e:
                print(f"Warning: could not load {mod}: {e}")

    runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    run()
