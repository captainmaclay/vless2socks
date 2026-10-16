#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Compatibility wrapper for add_vendor_c_gui_instance.py.
Разработано исключительно в исследовательских и образовательных целях
для развертывания изолированных тестовых сред Vendor C Desktop GUI в WSL2.
"""
import os
import sys

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from add_vendor_c_gui_instance import *

if __name__ == "__main__":
    main()
