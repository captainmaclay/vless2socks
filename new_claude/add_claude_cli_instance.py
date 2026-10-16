#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Compatibility wrapper for add_vendor_c_cli_instance.py.
Разработано исключительно в исследовательских и образовательных целях
для развертывания изолированных тестовых сред Vendor C Code CLI.
"""
import os
import sys

# Ensure current directory is in sys.path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from add_vendor_c_cli_instance import *

if __name__ == "__main__":
    main()
