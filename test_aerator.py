#!/usr/bin/env python3
"""
Convenience entrypoint alias for test_aerator_valves.py
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from test_aerator_valves import main

if __name__ == "__main__":
    main()
