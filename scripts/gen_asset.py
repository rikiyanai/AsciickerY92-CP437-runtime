#!/usr/bin/env python3
import sys
import os

# Enable importing from local directory
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from asset_gen.cli import main

if __name__ == "__main__":
    main()
